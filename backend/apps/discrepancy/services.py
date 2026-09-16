from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from ninja.errors import HttpError

from apps.core.services import record_event
from apps.valuation.models import FinalMark, ValuationComparison

from .models import DiscrepancyCase, DiscrepancyResolution, ExaminerClarification


CASE_TRANSITIONS = {
    DiscrepancyCase.Status.OPEN: {DiscrepancyCase.Status.CLARIFICATION, DiscrepancyCase.Status.MODERATOR, DiscrepancyCase.Status.CHIEF_EXAMINER, DiscrepancyCase.Status.THIRD_REQUESTED, DiscrepancyCase.Status.RESOLVED},
    DiscrepancyCase.Status.CLARIFICATION: {DiscrepancyCase.Status.MODERATOR, DiscrepancyCase.Status.CHIEF_EXAMINER, DiscrepancyCase.Status.THIRD_REQUESTED, DiscrepancyCase.Status.RESOLVED},
    DiscrepancyCase.Status.MODERATOR: {DiscrepancyCase.Status.CHIEF_EXAMINER, DiscrepancyCase.Status.THIRD_REQUESTED, DiscrepancyCase.Status.RESOLVED},
    DiscrepancyCase.Status.CHIEF_EXAMINER: {DiscrepancyCase.Status.THIRD_REQUESTED, DiscrepancyCase.Status.RESOLVED},
    DiscrepancyCase.Status.THIRD_REQUESTED: {DiscrepancyCase.Status.RESOLVED},
    DiscrepancyCase.Status.RESOLVED: {DiscrepancyCase.Status.APPROVED},
}


@transaction.atomic
def create_case(*, tenant_id, actor_id, comparison):
    if comparison.status not in (ValuationComparison.Status.DISCREPANCY, ValuationComparison.Status.THIRD_REQUIRED):
        raise HttpError(409, "Only a threshold discrepancy can enter reconciliation")
    item, created = DiscrepancyCase.objects.get_or_create(
        tenant_id=tenant_id,
        comparison=comparison,
        defaults={
            "threshold": comparison.script.paper.discrepancy_threshold,
            "items": [{"key": key, **value} for key, value in comparison.question_differences.items() if Decimal(value["difference"]) > 0],
            "status": DiscrepancyCase.Status.THIRD_REQUESTED if comparison.requires_third_valuation else DiscrepancyCase.Status.OPEN,
            "assigned_role": "chief_examiner" if comparison.requires_third_valuation else "moderator",
        },
    )
    if created:
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="discrepancy.case.created", aggregate="DiscrepancyCase", aggregate_id=item.id, payload={"comparison_id": str(comparison.id), "threshold": str(item.threshold), "status": item.status})
    return item


@transaction.atomic
def route_case(*, tenant_id, actor_id, case_id, expected_version, target, role):
    item = DiscrepancyCase.objects.select_for_update().filter(id=case_id, tenant_id=tenant_id).first()
    if not item or item.version != expected_version:
        raise HttpError(409, "Discrepancy case is missing or stale")
    if target not in CASE_TRANSITIONS.get(item.status, set()) or target in (DiscrepancyCase.Status.RESOLVED, DiscrepancyCase.Status.APPROVED):
        raise HttpError(409, f"Case transition from {item.status} to {target} is not allowed here")
    previous = item.status
    item.status = target
    item.assigned_role = role[:32]
    item.version += 1
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="discrepancy.case.routed", aggregate="DiscrepancyCase", aggregate_id=item.id, payload={"from": previous, "to": target, "role": item.assigned_role})
    return item


@transaction.atomic
def request_clarification(*, tenant_id, actor_id, case_id, expected_version, valuation_round, request_text):
    item = DiscrepancyCase.objects.select_for_update().filter(id=case_id, tenant_id=tenant_id).first()
    if not item or item.version != expected_version:
        raise HttpError(409, "Discrepancy case is missing or stale")
    if DiscrepancyCase.Status.CLARIFICATION not in CASE_TRANSITIONS.get(item.status, set()) or len(request_text.strip()) < 8:
        raise HttpError(422, "A specific clarification request is required")
    clarification = ExaminerClarification.objects.create(tenant_id=tenant_id, case=item, valuation_round=valuation_round, requested_by_id=actor_id, request=request_text.strip())
    previous = item.status
    item.status = DiscrepancyCase.Status.CLARIFICATION
    item.version += 1
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="discrepancy.clarification.requested", aggregate="ExaminerClarification", aggregate_id=clarification.id, payload={"case_id": str(item.id), "round": valuation_round, "from": previous})
    return clarification, item


@transaction.atomic
def respond_clarification(*, tenant_id, actor_id, clarification_id, evaluator, response):
    item = ExaminerClarification.objects.select_for_update().select_related("case__comparison__script").filter(id=clarification_id, tenant_id=tenant_id).first()
    if not item or item.responded_at:
        raise HttpError(404, "Open clarification not found")
    if len(response.strip()) < 4:
        raise HttpError(422, "Clarification response is required")
    owns_round = evaluator.assignments.filter(script=item.case.comparison.script, valuation_round=item.valuation_round).exists()
    if not owns_round:
        raise HttpError(404, "Open clarification not found")
    item.response = response.strip()
    item.responded_by_id = actor_id
    item.responded_at = timezone.now()
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="discrepancy.clarification.responded", aggregate="ExaminerClarification", aggregate_id=item.id, payload={"case_id": str(item.case_id), "round": item.valuation_round})
    return item


@transaction.atomic
def resolve_case(*, tenant_id, actor_id, case_id, expected_version, method, final_mark, reason, calculation):
    item = DiscrepancyCase.objects.select_for_update().select_related("comparison__script").filter(id=case_id, tenant_id=tenant_id).first()
    if not item or item.version != expected_version:
        raise HttpError(409, "Discrepancy case is missing or stale")
    if DiscrepancyCase.Status.RESOLVED not in CASE_TRANSITIONS.get(item.status, set()) or method not in DiscrepancyResolution.Method.values:
        raise HttpError(409, "Case cannot be resolved from its current state")
    mark = Decimal(str(final_mark))
    if mark < 0 or mark > item.comparison.script.paper.max_marks or len(reason.strip()) < 8:
        raise HttpError(422, "Resolution mark or rationale is invalid")
    resolution = DiscrepancyResolution.objects.create(tenant_id=tenant_id, case=item, method=method, final_mark=mark, reason=reason.strip(), decided_by_id=actor_id, calculation=calculation)
    item.status = DiscrepancyCase.Status.RESOLVED
    item.version += 1
    item.save()
    final, created = FinalMark.objects.get_or_create(
        tenant_id=tenant_id,
        script=item.comparison.script,
        defaults={"comparison": item.comparison, "rule": FinalMark.Rule.APPROVED, "mark": mark, "calculation": {"resolution_id": str(resolution.id), **calculation}, "proposed_by_id": actor_id},
    )
    if not created and final.status != FinalMark.Status.PROPOSED:
        raise HttpError(409, "A final mark already progressed beyond proposal")
    if not created:
        final.rule = FinalMark.Rule.APPROVED
        final.mark = mark
        final.calculation = {"resolution_id": str(resolution.id), **calculation}
        final.proposed_by_id = actor_id
        final.version += 1
        final.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="discrepancy.case.resolved", aggregate="DiscrepancyResolution", aggregate_id=resolution.id, payload={"case_id": str(item.id), "method": method, "final_mark": str(mark)})
    return resolution, item, final


@transaction.atomic
def approve_resolution(*, tenant_id, actor_id, resolution_id):
    resolution = DiscrepancyResolution.objects.select_for_update().select_related("case__comparison__script").filter(id=resolution_id, tenant_id=tenant_id).first()
    if not resolution or resolution.approved_at:
        raise HttpError(409, "Resolution is missing or already approved")
    if resolution.decided_by_id == actor_id:
        raise HttpError(409, "Resolution author cannot approve it")
    case = resolution.case
    if DiscrepancyCase.Status.APPROVED not in CASE_TRANSITIONS.get(case.status, set()):
        raise HttpError(409, "Case is not ready for approval")
    resolution.approved_by_id = actor_id
    resolution.approved_at = timezone.now()
    resolution.save()
    case.status = DiscrepancyCase.Status.APPROVED
    case.version += 1
    case.save()
    final = FinalMark.objects.select_for_update().get(script=case.comparison.script)
    from apps.valuation.services import approve_final_mark

    final = approve_final_mark(tenant_id=tenant_id, actor_id=actor_id, final_mark_id=final.id, expected_version=final.version)
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="discrepancy.resolution.approved", aggregate="DiscrepancyResolution", aggregate_id=resolution.id, payload={"case_id": str(case.id), "final_mark_id": str(final.id)})
    return resolution, case, final
