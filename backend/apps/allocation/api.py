from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from apps.configuration.models import Paper
from apps.core.authz import membership_for, require_roles, require_secure_evaluation_session
from apps.custody.models import Script
from apps.custody.services import transition_script
from apps.evaluators.models import Evaluator
from apps.assignment.models import AssignmentLock
from apps.repository.models import ScriptAsset
from apps.repository.storage import signed_object_url
from apps.core.services import record_event
from apps.tenancy.models import Membership

from .models import AllocationPolicy, AllocationProposal, AllocationRun, Assignment
from .schemas import AssignmentActionIn, AssignmentFlagIn, PolicyIn, RedistributionIn, SimulationIn
from .services import build_plan, execute_plan, next_valuation_round, redistribute_assignment, save_policy


router = Router(tags=["Evaluator assignment and allocation"])
ADMIN_ROLES = (Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER)


def assignment_data(item):
    return {"id": str(item.id), "script_id": str(item.script_id), "script": item.script.script_code, "paper": item.script.paper.code, "page_count": item.script.page_count, "evaluator_id": str(item.evaluator_id), "evaluator": item.evaluator.display_name, "backup_evaluator_id": str(item.backup_evaluator_id) if item.backup_evaluator_id else None, "backup_evaluator": item.backup_evaluator.display_name if item.backup_evaluator else None, "valuation_round": item.valuation_round, "status": item.status, "quality_score": float(item.quality_score), "source": item.source, "priority": item.priority, "is_flagged": item.is_flagged, "flag_reason": item.flag_reason, "skipped_at": item.skipped_at.isoformat() if item.skipped_at else None, "draft_saved_at": item.draft_saved_at.isoformat() if item.draft_saved_at else None, "last_opened_at": item.last_opened_at.isoformat() if item.last_opened_at else None, "last_page": item.last_page, "progress_percent": item.progress_percent, "due_at": item.due_at.isoformat(), "version": item.version}


def evaluator_assignment_data(item):
    return {
        "id": str(item.id),
        "script": item.script.script_code,
        "paper": item.script.paper.code,
        "page_count": item.script.page_count,
        "valuation_round": item.valuation_round,
        "status": item.status,
        "priority": item.priority,
        "is_flagged": item.is_flagged,
        "draft_saved_at": item.draft_saved_at.isoformat() if item.draft_saved_at else None,
        "last_opened_at": item.last_opened_at.isoformat() if item.last_opened_at else None,
        "last_page": item.last_page,
        "progress_percent": item.progress_percent,
        "due_at": item.due_at.isoformat(),
        "version": item.version,
        "locked": bool(getattr(item, "locked_elsewhere", False)),
    }


@router.get("/catalog")
def allocation_catalog(request):
    membership = membership_for(request)
    tenant_id = membership.institution.tenant_id
    assignments = Assignment.objects.filter(tenant_id=tenant_id).select_related("script__paper", "evaluator", "backup_evaluator")
    if membership.role == Membership.Role.EVALUATOR:
        active_other_locks = AssignmentLock.objects.filter(
            assignment_id=OuterRef("pk"),
            released_at__isnull=True,
            expires_at__gt=timezone.now(),
        ).exclude(access_session_id=request.access_session.id)
        assignments = assignments.filter(evaluator__email__iexact=request.auth.email).annotate(
            locked_elsewhere=Exists(active_other_locks),
        )
        return {
            "assignments": [evaluator_assignment_data(item) for item in assignments.order_by("-priority", "due_at")[:1000]],
            "scripts": [],
            "papers": [],
            "evaluators": [],
            "policies": [],
            "runs": [],
        }
    papers = Paper.objects.filter(tenant_id=tenant_id).select_related("subject").annotate(stored_count=Count("scripts", filter=Q(scripts__state=Script.State.STORED), distinct=True)).order_by("code")
    evaluators = Evaluator.objects.filter(tenant_id=tenant_id).annotate(active_load=Count("assignments", filter=Q(assignments__status__in=[Assignment.Status.ASSIGNED, Assignment.Status.ACCEPTED, Assignment.Status.IN_PROGRESS])))
    policies = AllocationPolicy.objects.filter(tenant_id=tenant_id).select_related("paper")
    runs = AllocationRun.objects.filter(tenant_id=tenant_id).select_related("paper").order_by("-created_at")[:100]
    scripts = Script.objects.filter(tenant_id=tenant_id, state__in=[Script.State.STORED, Script.State.ASSIGNED, Script.State.SUBMITTED]).select_related("paper").prefetch_related("assignments", "valuation_results", "final_mark").order_by("script_code")[:1000]
    return {"assignments": [assignment_data(item) for item in assignments.order_by("-priority", "due_at")[:1000]], "scripts": [{"id": str(item.id), "script_code": item.script_code, "paper_id": str(item.paper_id), "paper": item.paper.code, "state": item.state, "version": item.version, "assigned_rounds": [assignment.valuation_round for assignment in item.assignments.all()], "next_round": next_valuation_round(item)} for item in scripts], "papers": [{"id": str(item.id), "code": item.code, "title": item.title, "subject_code": item.subject.code, "subject_name": item.subject.name, "valuation_rounds": item.valuation_rounds, "second_valuation_mark_threshold": item.rules.get("second_valuation_mark_threshold"), "stored_scripts": item.stored_count, "status": item.status} for item in papers], "evaluators": [{"id": str(item.id), "code": item.evaluator_code, "name": item.display_name, "status": item.status, "daily_capacity": item.daily_capacity, "active_load": item.active_load} for item in evaluators.order_by("display_name")], "policies": [{"id": str(item.id), "paper_id": str(item.paper_id), "paper": item.paper.code, "algorithm": item.algorithm, "minimum_experience_years": item.minimum_experience_years, "minimum_expertise_level": item.minimum_expertise_level, "maximum_active_assignments": item.maximum_active_assignments, "assignment_due_hours": item.assignment_due_hours, "backup_required": item.backup_required, "allow_same_institution": item.allow_same_institution, "weights": item.weights, "version": item.version} for item in policies], "runs": [{"id": str(item.id), "paper": item.paper.code, "mode": item.mode, "algorithm": item.algorithm, "status": item.status, "requested_scripts": item.requested_scripts, "planned_scripts": item.planned_scripts, "allocated_scripts": item.allocated_scripts, "unallocated_scripts": item.unallocated_scripts, "average_quality_score": float(item.average_quality_score), "forecast": item.forecast, "created_at": item.created_at.isoformat()} for item in runs]}


@router.post("/policies")
def update_policy(request, payload: PolicyIn):
    membership = require_roles(request, *ADMIN_ROLES)
    paper = Paper.objects.filter(id=payload.paper_id, tenant_id=membership.institution.tenant_id).first()
    if not paper:
        raise HttpError(404, "Paper not found")
    policy = save_policy(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, paper=paper, payload=payload)
    return {"id": str(policy.id), "version": policy.version, "algorithm": policy.algorithm}


@router.post("/assignments")
def manual_assignment(request):
    require_roles(request, *ADMIN_ROLES)
    raise HttpError(410, "Manual allocation is disabled; simulate and commit an eligible run")


@router.get("/history")
def allocation_history(request, page: int = 1, q: str = ""):
    membership = require_roles(request, *ADMIN_ROLES)
    if page < 1 or len(q) > 100:
        raise HttpError(422, "Invalid allocation history filter")
    proposals = AllocationProposal.objects.filter(tenant_id=membership.institution.tenant_id).select_related("run__paper__subject", "script", "evaluator")
    if q.strip():
        term = q.strip()
        actor_ids = get_user_model().objects.filter(Q(username__icontains=term) | Q(first_name__icontains=term) | Q(last_name__icontains=term)).values_list("id", flat=True)
        proposals = proposals.filter(Q(script__script_code__icontains=term) | Q(run__paper__code__icontains=term) | Q(run__paper__subject__code__icontains=term) | Q(evaluator__display_name__icontains=term) | Q(run__created_by_id__in=actor_ids))
    total = proposals.count()
    page_size = 50
    proposals = list(proposals.order_by("-run__created_at", "script__script_code", "id")[(page - 1) * page_size:page * page_size])
    actors = get_user_model().objects.in_bulk({proposal.run.created_by_id for proposal in proposals})
    rows = []
    for proposal in proposals:
        run = proposal.run
        actor = actors.get(run.created_by_id)
        rows.append({
            "id": str(proposal.id),
            "run_id": str(run.id),
            "ran_by": (actor.get_full_name() or actor.get_username()) if actor else "Unknown operator",
            "ran_at": run.created_at.isoformat(),
            "script": proposal.script.script_code,
            "paper": run.paper.code,
            "subject": run.paper.subject.code,
            "round": proposal.valuation_round,
            "evaluator": proposal.evaluator.display_name if proposal.evaluator else None,
            "status": "unallocated" if not proposal.evaluator_id else run.status,
            "blockers": proposal.blockers,
        })
    return {"total": total, "page": page, "page_size": page_size, "rows": rows}


@router.post("/simulate")
def simulate(request, payload: SimulationIn):
    membership = require_roles(request, *ADMIN_ROLES)
    paper = Paper.objects.filter(id=payload.paper_id, tenant_id=membership.institution.tenant_id, status=Paper.Status.FROZEN).first()
    if not paper:
        raise HttpError(409, "A frozen paper configuration is required")
    policy = AllocationPolicy.objects.filter(tenant_id=membership.institution.tenant_id, paper=paper).first()
    run = build_plan(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, paper=paper, algorithm=payload.algorithm or (policy.algorithm if policy else AllocationPolicy.Algorithm.INTELLIGENT), valuation_round=payload.valuation_round, maximum_scripts=payload.maximum_scripts, mode=AllocationRun.Mode.SIMULATION)
    return {"id": str(run.id), "planned_scripts": run.planned_scripts, "allocatable_scripts": run.planned_scripts - run.unallocated_scripts, "unallocated_scripts": run.unallocated_scripts, "average_quality_score": float(run.average_quality_score), "forecast": run.forecast}


@router.post("/runs/{run_id}/execute")
def execute(request, run_id: str):
    membership = require_roles(request, *ADMIN_ROLES)
    run = execute_plan(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, run_id=run_id)
    return {"id": str(run.id), "status": run.status, "allocated_scripts": run.allocated_scripts}


@router.get("/runs/{run_id}")
def run_detail(request, run_id: str):
    membership = require_roles(request, *ADMIN_ROLES)
    run = AllocationRun.objects.filter(id=run_id, tenant_id=membership.institution.tenant_id).select_related("paper").first()
    if not run:
        raise HttpError(404, "Allocation run not found")
    return {"id": str(run.id), "paper": run.paper.code, "status": run.status, "algorithm": run.algorithm, "average_quality_score": float(run.average_quality_score), "forecast": run.forecast, "proposals": [{"id": str(item.id), "script": item.script.script_code, "evaluator": item.evaluator.display_name if item.evaluator else None, "backup_evaluator": item.backup_evaluator.display_name if item.backup_evaluator else None, "valuation_round": item.valuation_round, "quality_score": float(item.quality_score), "score_breakdown": item.score_breakdown, "blockers": item.blockers} for item in run.proposals.select_related("script", "evaluator", "backup_evaluator").order_by("script__script_code")]}


@router.post("/assignments/{assignment_id}/redistribute")
def redistribute(request, assignment_id: str, payload: RedistributionIn):
    membership = require_roles(request, *ADMIN_ROLES)
    assignment = redistribute_assignment(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, assignment_id=assignment_id, expected_version=payload.version, reason=payload.reason)
    return {"id": str(assignment.id), "status": assignment.status, "evaluator_id": str(assignment.evaluator_id), "version": assignment.version}


def _authorized_assignment(request, assignment_id):
    membership = require_roles(request, *ADMIN_ROLES, Membership.Role.EVALUATOR)
    assignment = Assignment.objects.select_related("script__paper", "evaluator", "backup_evaluator").filter(id=assignment_id, tenant_id=membership.institution.tenant_id).first()
    if not assignment:
        raise HttpError(404, "Assignment not found")
    if membership.role == Membership.Role.EVALUATOR and assignment.evaluator.email.lower() != request.auth.email.lower():
        raise HttpError(403, "This assignment belongs to another evaluator")
    return membership, assignment


@router.post("/assignments/{assignment_id}/action")
def assignment_action(request, assignment_id: str, payload: AssignmentActionIn):
    membership, assignment = _authorized_assignment(request, assignment_id)
    if membership.role == Membership.Role.EVALUATOR:
        require_secure_evaluation_session(request, assignment)
    tenant_id = membership.institution.tenant_id
    actions = {"accept", "start", "progress", "skip", "flag", "unflag", "priority"}
    if payload.action not in actions:
        raise HttpError(422, "Unsupported assignment action")
    if payload.action == "priority" and membership.role not in ADMIN_ROLES:
        raise HttpError(403, "Only allocation administrators can change priority")
    with transaction.atomic():
        assignment = Assignment.objects.select_for_update().select_related("script").get(id=assignment.id, tenant_id=tenant_id)
        if assignment.version != payload.version:
            raise HttpError(409, "Assignment was changed by another user")
        now = timezone.now()
        if payload.action == "accept":
            if assignment.status not in [Assignment.Status.ASSIGNED, Assignment.Status.REASSIGNED]:
                raise HttpError(409, "Only a pending assignment can be accepted")
            assignment.status = Assignment.Status.ACCEPTED
            assignment.accepted_at = now
        elif payload.action == "start":
            if assignment.status not in [Assignment.Status.ASSIGNED, Assignment.Status.ACCEPTED, Assignment.Status.REASSIGNED, Assignment.Status.IN_PROGRESS]:
                raise HttpError(409, "This assignment cannot be opened")
            assignment.status = Assignment.Status.IN_PROGRESS
            assignment.started_at = assignment.started_at or now
            assignment.last_opened_at = now
            if assignment.script.state == Script.State.ASSIGNED:
                opened = transition_script(tenant_id=tenant_id, actor_id=request.auth.id, script_id=assignment.script_id, expected_version=assignment.script.version, to_state=Script.State.OPENED, location="Secure evaluation viewer", metadata={"assignment_id": str(assignment.id)})
                transition_script(tenant_id=tenant_id, actor_id=request.auth.id, script_id=assignment.script_id, expected_version=opened.version, to_state=Script.State.EVALUATING, location="Secure evaluation viewer", metadata={"assignment_id": str(assignment.id)})
        elif payload.action == "progress":
            if assignment.status != Assignment.Status.IN_PROGRESS or payload.page is None or payload.progress_percent is None:
                raise HttpError(409, "An in-progress assignment, page and progress are required")
            if not 1 <= payload.page <= max(assignment.script.page_count, 1) or not 0 <= payload.progress_percent <= 100:
                raise HttpError(422, "Viewer progress is outside the script range")
            assignment.last_page = payload.page
            assignment.progress_percent = payload.progress_percent
            assignment.draft_saved_at = now
            assignment.last_opened_at = now
        elif payload.action == "skip":
            assignment.skipped_at = now
        elif payload.action in ["flag", "unflag"]:
            assignment.is_flagged = payload.action == "flag"
            assignment.flag_reason = payload.reason[:240] if assignment.is_flagged else ""
        elif payload.action == "priority":
            if payload.priority is None or not 1 <= payload.priority <= 5:
                raise HttpError(422, "Priority must be between 1 and 5")
            assignment.priority = payload.priority
        assignment.version += 1
        assignment.save()
        record_event(tenant_id=tenant_id, actor_id=request.auth.id, action=f"allocation.assignment.{payload.action}", aggregate="Assignment", aggregate_id=assignment.id, payload={"script_id": str(assignment.script_id), "page": payload.page, "progress_percent": payload.progress_percent, "reason": payload.reason, "priority": payload.priority})
    return assignment_data(assignment)


@router.post("/assignments/{assignment_id}/flag")
def assignment_flag(request, assignment_id: str, payload: AssignmentFlagIn):
    """Toggle the review flag without requiring an active secure viewer session."""
    membership, assignment = _authorized_assignment(request, assignment_id)
    tenant_id = membership.institution.tenant_id
    with transaction.atomic():
        assignment = Assignment.objects.select_for_update().get(
            id=assignment.id,
            tenant_id=tenant_id,
        )
        if assignment.version != payload.version:
            raise HttpError(409, "Assignment was changed by another user")
        assignment.is_flagged = payload.flagged
        assignment.flag_reason = payload.reason[:240] if payload.flagged else ""
        assignment.version += 1
        assignment.save(update_fields=["is_flagged", "flag_reason", "version", "updated_at"])
        record_event(
            tenant_id=tenant_id,
            actor_id=request.auth.id,
            action="allocation.assignment.flag" if payload.flagged else "allocation.assignment.unflag",
            aggregate="Assignment",
            aggregate_id=assignment.id,
            payload={"script_id": str(assignment.script_id), "reason": payload.reason},
        )
    return assignment_data(assignment)


@router.get("/assignments/{assignment_id}/viewer")
def viewer_manifest(request, assignment_id: str, low_bandwidth: bool = False):
    membership, assignment = _authorized_assignment(request, assignment_id)
    if membership.role == Membership.Role.EVALUATOR:
        require_secure_evaluation_session(request, assignment)
    kind = ScriptAsset.Kind.THUMBNAIL if low_bandwidth else ScriptAsset.Kind.EVALUATION
    assets = ScriptAsset.objects.filter(tenant_id=assignment.tenant_id, script=assignment.script, kind=kind, deleted_at__isnull=True).order_by("page_number", "-version")
    latest = {}
    for asset in assets:
        latest.setdefault(asset.page_number, asset)
    pages = []
    for page_number, asset in sorted(latest.items()):
        url, expires = signed_object_url(method="GET", key=asset.storage_key, ttl_seconds=300)
        pages.append({"page_number": page_number, "asset_id": str(asset.id), "url": url, "expires_at": expires, "sha256": asset.sha256, "byte_size": asset.byte_size, "mime_type": asset.mime_type})
    return {"assignment": assignment_data(assignment), "pages": pages, "page_count": assignment.script.page_count, "mode": "low_bandwidth" if low_bandwidth else "high_resolution", "ttl_seconds": 300}
