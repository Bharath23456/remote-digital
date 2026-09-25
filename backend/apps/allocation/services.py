import secrets
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Avg, Count
from django.utils import timezone
from ninja.errors import HttpError

from apps.core.services import record_event
from apps.custody.models import Script
from apps.custody.services import transition_script
from apps.eligibility.models import EligibilityRecord
from apps.evaluators.models import Evaluator, EvaluatorAvailability, Expertise
from apps.phase4.services import create_notification, notification_action
from apps.tenancy.models import Membership
from apps.valuation.services import required_valuation_rounds

from .models import AllocationPolicy, AllocationProposal, AllocationRun, Assignment, AssignmentHistory


DEFAULT_WEIGHTS = {"subject": 15, "qualification": 10, "expertise": 15, "experience": 10, "availability": 10, "capacity": 10, "workload": 10, "deadline": 5, "performance": 10, "risk": 5}
ACTIVE_STATUSES = [Assignment.Status.ASSIGNED, Assignment.Status.ACCEPTED, Assignment.Status.IN_PROGRESS, Assignment.Status.REASSIGNED]


def next_valuation_round(script):
    if hasattr(script, "final_mark"):
        return None
    results = sorted((item for item in script.valuation_results.all() if item.is_locked), key=lambda item: item.valuation_round)
    if any(item.valuation_round != index for index, item in enumerate(results, 1)):
        return None
    next_round = len(results) + 1
    required_rounds = required_valuation_rounds(script, results[0]) if results else script.paper.valuation_rounds
    if next_round > required_rounds:
        return None
    if any(item.valuation_round == next_round for item in script.assignments.all()):
        return None
    return next_round


def evaluator_work_history(*, tenant_id, evaluator_id):
    if not Evaluator.objects.filter(tenant_id=tenant_id, id=evaluator_id).exists():
        raise HttpError(404, "Evaluator was not found")
    assignments = Assignment.objects.filter(tenant_id=tenant_id, evaluator_id=evaluator_id).select_related(
        "script__paper__subject"
    ).order_by("-assigned_at")
    status_counts = {status: 0 for status, _ in Assignment.Status.choices}
    rows = []
    quality_scores = []
    for assignment in assignments:
        status_counts[assignment.status] += 1
        if assignment.status == Assignment.Status.SUBMITTED:
            quality_scores.append(float(assignment.quality_score))
        rows.append({
            "id": str(assignment.id),
            "script_code": assignment.script.script_code,
            "paper_code": assignment.script.paper.code,
            "subject": assignment.script.paper.subject.name,
            "valuation_round": assignment.valuation_round,
            "status": assignment.status,
            "source": assignment.source,
            "quality_score": float(assignment.quality_score),
            "progress_percent": assignment.progress_percent,
            "assigned_at": assignment.assigned_at.isoformat(),
            "due_at": assignment.due_at.isoformat(),
            "submitted_at": assignment.submitted_at.isoformat() if assignment.submitted_at else None,
        })
    return {
        "summary": {
            "total": len(rows),
            "active": sum(status_counts[status] for status in ACTIVE_STATUSES),
            "submitted": status_counts[Assignment.Status.SUBMITTED],
            "expired": status_counts[Assignment.Status.EXPIRED],
            "average_quality": round(sum(quality_scores) / len(quality_scores), 2) if quality_scores else None,
        },
        "assignments": rows,
    }


def _policy_for(tenant_id, paper):
    policy = AllocationPolicy.objects.filter(tenant_id=tenant_id, paper=paper).first()
    if policy:
        return policy
    return AllocationPolicy(tenant_id=tenant_id, paper=paper, weights=DEFAULT_WEIGHTS.copy())


def validate_policy_values(payload):
    if payload.algorithm not in AllocationPolicy.Algorithm.values:
        raise HttpError(422, "Unsupported allocation algorithm")
    if not 0 <= payload.minimum_experience_years <= 60 or not 1 <= payload.minimum_expertise_level <= 5:
        raise HttpError(422, "Experience or expertise threshold is outside the supported range")
    if not 1 <= payload.maximum_active_assignments <= 500 or not 1 <= payload.assignment_due_hours <= 2160:
        raise HttpError(422, "Capacity or deadline is outside the supported range")
    weights = payload.weights or DEFAULT_WEIGHTS
    if set(weights) - set(DEFAULT_WEIGHTS) or any(not isinstance(value, (int, float)) or value < 0 for value in weights.values()) or sum(weights.values()) <= 0:
        raise HttpError(422, "Allocation weights must be non-negative supported criteria")


def save_policy(*, tenant_id, actor_id, paper, payload):
    validate_policy_values(payload)
    with transaction.atomic():
        policy = AllocationPolicy.objects.select_for_update().filter(tenant_id=tenant_id, paper=paper).first()
        if policy and policy.version != payload.version:
            raise HttpError(409, "Allocation policy was changed by another user")
        if not policy and payload.version not in (0, 1):
            raise HttpError(409, "Allocation policy version is stale")
        if not policy:
            policy = AllocationPolicy(tenant_id=tenant_id, paper=paper)
        for field in ("algorithm", "minimum_experience_years", "minimum_expertise_level", "maximum_active_assignments", "assignment_due_hours", "backup_required", "allow_same_institution"):
            setattr(policy, field, getattr(payload, field))
        policy.weights = {**DEFAULT_WEIGHTS, **(payload.weights or {})}
        policy.version = policy.version + 1 if policy.pk else 1
        policy.save()
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="allocation.policy.saved", aggregate="AllocationPolicy", aggregate_id=policy.id, payload={"paper_id": str(paper.id), "version": policy.version, "algorithm": policy.algorithm})
    return policy


def _availability_capacity(evaluator, today):
    if evaluator.available_from and evaluator.available_from > today:
        return 0
    if evaluator.available_to and evaluator.available_to < today:
        return 0
    periods = EvaluatorAvailability.objects.filter(evaluator=evaluator, starts_on__lte=today, ends_on__gte=today)
    if evaluator.availability_periods.exists() and not periods.exists():
        return 0
    period_capacity = periods.order_by("daily_capacity").values_list("daily_capacity", flat=True).first()
    return min(evaluator.daily_capacity, period_capacity) if period_capacity else evaluator.daily_capacity


def _hard_blockers(*, evaluator, script, eligibility, policy, round_evaluator_ids):
    blockers = []
    if evaluator.status != Evaluator.Status.ACTIVE:
        blockers.append("evaluator_not_active")
    if not eligibility or eligibility.status != EligibilityRecord.Status.ELIGIBLE or not eligibility.expires_on or eligibility.expires_on <= timezone.localdate():
        blockers.append("eligibility_not_active")
    elif eligibility.has_conflict or eligibility.is_debarred or eligibility.is_blacklisted:
        blockers.append("conflict_or_debarment")
    if evaluator.years_experience < policy.minimum_experience_years:
        blockers.append("experience_below_policy")
    expertise = Expertise.objects.filter(evaluator=evaluator, subject=script.paper.subject, verified=True).first()
    if not expertise or expertise.level < policy.minimum_expertise_level:
        blockers.append("expertise_below_policy")
    if evaluator.id in round_evaluator_ids:
        blockers.append("prior_valuation_evaluator")
    source = script.packet.dispatch.source_centre.strip().casefold()
    if not policy.allow_same_institution and source and source == evaluator.institution_name.strip().casefold():
        blockers.append("source_institution_conflict")
    return blockers, expertise


def score_evaluator(*, tenant_id, evaluator, script, policy, projected_load, round_evaluator_ids):
    eligibility = EligibilityRecord.objects.filter(tenant_id=tenant_id, evaluator=evaluator, subject=script.paper.subject).first()
    blockers, expertise = _hard_blockers(evaluator=evaluator, script=script, eligibility=eligibility, policy=policy, round_evaluator_ids=round_evaluator_ids)
    capacity = min(_availability_capacity(evaluator, timezone.localdate()), policy.maximum_active_assignments)
    if capacity <= projected_load:
        blockers.append("capacity_reached")
    if blockers:
        return Decimal("0"), {}, sorted(set(blockers))
    weights = {**DEFAULT_WEIGHTS, **(policy.weights or {})}
    total_weight = Decimal(str(sum(weights.values())))
    quality_average = Assignment.objects.filter(tenant_id=tenant_id, evaluator=evaluator, status=Assignment.Status.SUBMITTED).aggregate(value=Avg("quality_score"))["value"] or Decimal("85")
    ratios = {
        "subject": Decimal("1"),
        "qualification": Decimal("1") if eligibility.qualification_ok else Decimal("0"),
        "expertise": Decimal(expertise.level) / Decimal("5"),
        "experience": min(Decimal(evaluator.years_experience) / Decimal(max(policy.minimum_experience_years * 2, 1)), Decimal("1")),
        "availability": Decimal("1"),
        "capacity": max(Decimal("0"), Decimal(capacity - projected_load) / Decimal(max(capacity, 1))),
        "workload": max(Decimal("0"), Decimal("1") - Decimal(projected_load) / Decimal(max(capacity, 1))),
        "deadline": Decimal("1") if policy.assignment_due_hours >= 24 else Decimal("0.8"),
        "performance": min(Decimal(quality_average) / Decimal("100"), Decimal("1")),
        "risk": Decimal("1") if not eligibility.risk_reasons else Decimal("0.5"),
    }
    breakdown = {key: round(float(ratios[key] * Decimal(str(weights[key])) / total_weight * 100), 2) for key in weights}
    return Decimal(str(round(sum(breakdown.values()), 2))), breakdown, []


def build_plan(*, tenant_id, actor_id, paper, algorithm, valuation_round, maximum_scripts, mode):
    maximum_round = max(paper.valuation_rounds, 2 if paper.valuation_rounds == 1 and paper.rules.get("second_valuation_mark_threshold") is not None else 1)
    if algorithm not in AllocationPolicy.Algorithm.values or not 1 <= valuation_round <= maximum_round or not 1 <= maximum_scripts <= 5000:
        raise HttpError(422, "Allocation algorithm, valuation round or batch size is invalid")
    policy = _policy_for(tenant_id, paper)
    scripts = []
    for script in Script.objects.filter(tenant_id=tenant_id, paper=paper, state__in=[Script.State.STORED, Script.State.ASSIGNED, Script.State.SUBMITTED]).select_related("paper__subject", "packet__dispatch").prefetch_related("valuation_results", "assignments", "final_mark").order_by("created_at").iterator(chunk_size=500):
        if next_valuation_round(script) == valuation_round:
            scripts.append(script)
            if len(scripts) == maximum_scripts:
                break
    evaluators = list(Evaluator.objects.filter(tenant_id=tenant_id, status=Evaluator.Status.ACTIVE, is_system_ai=False).order_by("evaluator_code"))
    active_loads = defaultdict(int, dict(Assignment.objects.filter(tenant_id=tenant_id, status__in=ACTIVE_STATUSES).values_list("evaluator_id").annotate(count=Count("id"))))
    rows = []
    randomizer = secrets.SystemRandom()
    for script in scripts:
        prior_ids = set(Assignment.objects.filter(script=script).values_list("evaluator_id", flat=True))
        scored = []
        blocked = []
        for evaluator in evaluators:
            score, breakdown, blockers = score_evaluator(tenant_id=tenant_id, evaluator=evaluator, script=script, policy=policy, projected_load=active_loads[evaluator.id], round_evaluator_ids=prior_ids)
            if blockers:
                blocked.extend(blockers)
            else:
                scored.append((evaluator, score, breakdown))
        if algorithm == AllocationPolicy.Algorithm.RANDOM:
            randomizer.shuffle(scored)
        elif algorithm == AllocationPolicy.Algorithm.INTELLIGENT and scored:
            best_score = max(item[1] for item in scored)
            quality_band = [item for item in scored if item[1] >= best_score - Decimal("10")]
            outside_band = [item for item in scored if item[1] < best_score - Decimal("10")]
            quality_band.sort(key=lambda item: (active_loads[item[0].id], -item[1]))
            outside_band.sort(key=lambda item: item[1], reverse=True)
            scored = quality_band + outside_band
        else:
            scored.sort(key=lambda item: (item[1], -active_loads[item[0].id]), reverse=True)
        selected = scored[0] if scored else None
        backup = scored[1][0] if policy.backup_required and len(scored) > 1 else None
        if selected:
            active_loads[selected[0].id] += 1
            rows.append((script, selected[0], backup, selected[1], selected[2], []))
        else:
            rows.append((script, None, None, Decimal("0"), {}, sorted(set(blocked)) or ["no_eligible_evaluator"]))
    allocated = sum(1 for row in rows if row[1])
    average = sum((row[3] for row in rows), Decimal("0")) / max(allocated, 1)
    loads = sorted(active_loads.values())
    forecast = {"eligible_evaluators": len(evaluators), "available_scripts": len(scripts), "estimated_completion_hours": max((policy.assignment_due_hours if allocated else 0), 0), "minimum_projected_load": loads[0] if loads else 0, "maximum_projected_load": loads[-1] if loads else 0}
    with transaction.atomic():
        run = AllocationRun.objects.create(tenant_id=tenant_id, paper=paper, mode=mode, algorithm=algorithm, requested_scripts=len(scripts), planned_scripts=len(rows), allocated_scripts=0, unallocated_scripts=len(rows) - allocated, average_quality_score=average.quantize(Decimal("0.01")), forecast=forecast, created_by_id=actor_id)
        AllocationProposal.objects.bulk_create([AllocationProposal(tenant_id=tenant_id, run=run, script=row[0], evaluator=row[1], backup_evaluator=row[2], valuation_round=valuation_round, quality_score=row[3], score_breakdown=row[4], blockers=row[5]) for row in rows])
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="allocation.plan.created", aggregate="AllocationRun", aggregate_id=run.id, payload={"paper_id": str(paper.id), "mode": mode, "planned": len(rows), "allocatable": allocated})
    return run


def _assignment_snapshot(assignment):
    return {"evaluator_id": str(assignment.evaluator_id), "backup_evaluator_id": str(assignment.backup_evaluator_id) if assignment.backup_evaluator_id else None, "status": assignment.status, "quality_score": str(assignment.quality_score), "due_at": assignment.due_at.isoformat(), "priority": assignment.priority, "version": assignment.version}


def _notify_assignment(*, assignment, actor_id):
    user_id = assignment.evaluator.user_id
    if not user_id and assignment.evaluator.email:
        membership = Membership.objects.filter(
            institution__tenant_id=assignment.tenant_id,
            user__email__iexact=assignment.evaluator.email,
            role=Membership.Role.EVALUATOR,
            is_active=True,
        ).first()
        user_id = membership.user_id if membership else None
    if not user_id:
        return
    notification = create_notification(
        tenant_id=assignment.tenant_id,
        actor_id=actor_id,
        user_id=user_id,
        category="assignment",
        title="New evaluation assigned",
        body=f"Script {assignment.script.script_code} for {assignment.script.paper.code} is due {assignment.due_at.strftime('%d %b %Y, %H:%M UTC')}.",
        severity="high" if assignment.priority >= 4 else "normal",
        channels=["in_app"],
        mandatory_acknowledgement=False,
    )
    notification_action(
        tenant_id=assignment.tenant_id,
        actor_id=actor_id,
        notification_id=notification.id,
        expected_version=notification.version,
        action="deliver",
    )


def create_assignment(*, tenant_id, actor_id, script, evaluator, backup_evaluator, valuation_round, due_at, source, quality_score, score_breakdown, priority=3):
    if script.tenant_id != tenant_id or evaluator.tenant_id != tenant_id or (backup_evaluator and backup_evaluator.tenant_id != tenant_id):
        raise HttpError(404, "Allocation inputs were not found in this tenant")
    policy = _policy_for(tenant_id, script.paper)
    prior_ids = set(Assignment.objects.filter(script=script).values_list("evaluator_id", flat=True))
    score, breakdown, blockers = score_evaluator(tenant_id=tenant_id, evaluator=evaluator, script=script, policy=policy, projected_load=Assignment.objects.filter(tenant_id=tenant_id, evaluator=evaluator, status__in=ACTIVE_STATUSES).count(), round_evaluator_ids=prior_ids)
    if blockers:
        raise HttpError(409, f"Evaluator is blocked: {', '.join(blockers)}")
    if backup_evaluator:
        _, _, backup_blockers = score_evaluator(tenant_id=tenant_id, evaluator=backup_evaluator, script=script, policy=policy, projected_load=Assignment.objects.filter(tenant_id=tenant_id, evaluator=backup_evaluator, status__in=ACTIVE_STATUSES).count(), round_evaluator_ids=prior_ids | {evaluator.id})
        if backup_blockers:
            raise HttpError(409, f"Backup evaluator is blocked: {', '.join(backup_blockers)}")
    try:
        with transaction.atomic():
            locked = Script.objects.select_for_update().select_related("paper").get(id=script.id, tenant_id=tenant_id)
            if locked.state not in [Script.State.STORED, Script.State.ASSIGNED, Script.State.SUBMITTED]:
                raise HttpError(409, "Only stored or submitted scripts can be allocated")
            if next_valuation_round(locked) != valuation_round:
                raise HttpError(409, "This valuation round is not required or its prior round is incomplete")
            assignment = Assignment.objects.create(tenant_id=tenant_id, script=locked, evaluator=evaluator, backup_evaluator=backup_evaluator, valuation_round=valuation_round, due_at=due_at, source=source, quality_score=quality_score or score, score_breakdown=score_breakdown or breakdown, priority=priority)
            AssignmentHistory.objects.create(tenant_id=tenant_id, assignment=assignment, action="assigned", actor_id=actor_id, to_evaluator_id=evaluator.id, snapshot=_assignment_snapshot(assignment))
            if locked.state == Script.State.STORED:
                transition_script(tenant_id=tenant_id, actor_id=actor_id, script_id=locked.id, expected_version=locked.version, to_state=Script.State.ASSIGNED, location="Allocation engine", metadata={"assignment_id": str(assignment.id), "source": source})
            record_event(tenant_id=tenant_id, actor_id=actor_id, action="allocation.assignment.created", aggregate="Assignment", aggregate_id=assignment.id, payload={"script_id": str(script.id), "evaluator_id": str(evaluator.id), "backup_evaluator_id": str(backup_evaluator.id) if backup_evaluator else None, "round": valuation_round, "source": source, "quality_score": str(assignment.quality_score)})
            _notify_assignment(assignment=assignment, actor_id=actor_id)
    except IntegrityError as exc:
        raise HttpError(409, "This script and valuation round are already assigned") from exc
    return assignment


def execute_plan(*, tenant_id, actor_id, run_id):
    run = AllocationRun.objects.filter(id=run_id, tenant_id=tenant_id).select_related("paper").first()
    if not run or run.status != AllocationRun.Status.PLANNED:
        raise HttpError(409, "Allocation run is missing or no longer executable")
    completed = 0
    for proposal in run.proposals.filter(evaluator__isnull=False).select_related("script__paper", "evaluator", "backup_evaluator"):
        try:
            create_assignment(tenant_id=tenant_id, actor_id=actor_id, script=proposal.script, evaluator=proposal.evaluator, backup_evaluator=proposal.backup_evaluator, valuation_round=proposal.valuation_round, due_at=timezone.now() + timedelta(hours=_policy_for(tenant_id, run.paper).assignment_due_hours), source=run.algorithm, quality_score=proposal.quality_score, score_breakdown=proposal.score_breakdown)
            completed += 1
        except HttpError:
            continue
    with transaction.atomic():
        current = AllocationRun.objects.select_for_update().get(id=run.id, tenant_id=tenant_id)
        current.mode = AllocationRun.Mode.EXECUTION
        current.allocated_scripts = completed
        current.status = AllocationRun.Status.COMPLETED if completed == current.planned_scripts - current.unallocated_scripts else AllocationRun.Status.PARTIAL
        current.save(update_fields=["mode", "allocated_scripts", "status", "updated_at"])
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="allocation.plan.executed", aggregate="AllocationRun", aggregate_id=current.id, payload={"allocated": completed, "status": current.status})
    return current


def redistribute_assignment(*, tenant_id, actor_id, assignment_id, expected_version, reason, replacement_evaluator=None):
    if len(reason.strip()) < 8:
        raise HttpError(422, "A specific redistribution reason is required")
    with transaction.atomic():
        assignment = Assignment.objects.select_for_update(of=("self",)).select_related("script__paper", "evaluator", "backup_evaluator").filter(id=assignment_id, tenant_id=tenant_id).first()
        if not assignment or assignment.version != expected_version:
            raise HttpError(409, "Assignment is missing or stale")
        if assignment.status == Assignment.Status.SUBMITTED:
            raise HttpError(409, "Submitted assignments cannot be redistributed")
        previous = assignment.evaluator
        replacement = replacement_evaluator or assignment.backup_evaluator
        if replacement:
            _, _, blockers = score_evaluator(tenant_id=tenant_id, evaluator=replacement, script=assignment.script, policy=_policy_for(tenant_id, assignment.script.paper), projected_load=Assignment.objects.filter(tenant_id=tenant_id, evaluator=replacement, status__in=ACTIVE_STATUSES).count(), round_evaluator_ids={previous.id})
            if blockers:
                replacement = None
        if not replacement:
            candidates = []
            for evaluator in Evaluator.objects.filter(tenant_id=tenant_id, status=Evaluator.Status.ACTIVE, is_system_ai=False).exclude(id=previous.id):
                score, breakdown, blockers = score_evaluator(tenant_id=tenant_id, evaluator=evaluator, script=assignment.script, policy=_policy_for(tenant_id, assignment.script.paper), projected_load=Assignment.objects.filter(tenant_id=tenant_id, evaluator=evaluator, status__in=ACTIVE_STATUSES).count(), round_evaluator_ids={previous.id})
                if not blockers:
                    candidates.append((score, evaluator, breakdown))
            candidates.sort(key=lambda item: item[0], reverse=True)
            if not candidates:
                raise HttpError(409, "No conflict-free replacement evaluator is available")
            score, replacement, breakdown = candidates[0]
            assignment.quality_score = score
            assignment.score_breakdown = breakdown
        assignment.evaluator = replacement
        assignment.backup_evaluator = None
        assignment.status = Assignment.Status.REASSIGNED
        assignment.version += 1
        assignment.save()
        AssignmentHistory.objects.create(tenant_id=tenant_id, assignment=assignment, action="redistributed", actor_id=actor_id, from_evaluator_id=previous.id, to_evaluator_id=replacement.id, reason=reason.strip(), snapshot=_assignment_snapshot(assignment))
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="allocation.assignment.redistributed", aggregate="Assignment", aggregate_id=assignment.id, payload={"from_evaluator_id": str(previous.id), "to_evaluator_id": str(replacement.id), "reason": reason.strip()})
        _notify_assignment(assignment=assignment, actor_id=actor_id)
    return assignment
