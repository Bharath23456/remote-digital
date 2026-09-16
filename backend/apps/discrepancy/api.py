from ninja import Field, Router, Schema
from ninja.errors import HttpError

from apps.core.authz import membership_for, require_roles, require_step_up
from apps.evaluators.models import Evaluator
from apps.tenancy.models import Membership

from .models import DiscrepancyCase, DiscrepancyResolution, ExaminerClarification
from .services import approve_resolution, request_clarification, resolve_case, respond_clarification, route_case


router = Router(tags=["Discrepancy and reconciliation"])
ADMIN_ROLES = (Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER)


class RouteIn(Schema):
    version: int
    target: str
    role: str


class ClarificationIn(Schema):
    version: int
    valuation_round: int
    request: str


class ResponseIn(Schema):
    response: str


class ResolutionIn(Schema):
    version: int
    method: str
    final_mark: float
    reason: str
    calculation: dict = Field(default_factory=dict)


@router.get("/catalog")
def catalog(request):
    membership = membership_for(request)
    tenant_id = membership.institution.tenant_id
    cases = DiscrepancyCase.objects.filter(tenant_id=tenant_id).select_related("comparison__script")
    if membership.role == Membership.Role.EVALUATOR:
        evaluator = Evaluator.objects.filter(tenant_id=tenant_id, email__iexact=request.auth.email).first()
        allowed_rounds = set(evaluator.assignments.values_list("script_id", "valuation_round")) if evaluator else set()
        clarifications = ExaminerClarification.objects.filter(tenant_id=tenant_id).select_related("case__comparison")
        own_clarifications = [item for item in clarifications if (item.case.comparison.script_id, item.valuation_round) in allowed_rounds]
        return {
            "cases": [],
            "clarifications": [{"id": str(item.id), "case_id": str(item.case_id), "round": item.valuation_round, "request": item.request, "response": item.response, "responded_at": item.responded_at.isoformat() if item.responded_at else None} for item in own_clarifications],
            "resolutions": [],
        }
    case_ids = cases.values_list("id", flat=True)
    return {
        "cases": [{"id": str(item.id), "comparison_id": str(item.comparison_id), "script": item.comparison.script.script_code, "status": item.status, "threshold": float(item.threshold), "difference": float(item.comparison.total_difference), "items": item.items, "assigned_role": item.assigned_role, "version": item.version} for item in cases],
        "clarifications": [{"id": str(item.id), "case_id": str(item.case_id), "round": item.valuation_round, "request": item.request, "response": item.response, "responded_at": item.responded_at.isoformat() if item.responded_at else None} for item in ExaminerClarification.objects.filter(tenant_id=tenant_id, case_id__in=case_ids)],
        "resolutions": [{"id": str(item.id), "case_id": str(item.case_id), "method": item.method, "final_mark": float(item.final_mark), "reason": item.reason, "approved": bool(item.approved_at)} for item in DiscrepancyResolution.objects.filter(tenant_id=tenant_id, case_id__in=case_ids)],
    }


@router.post("/cases/{case_id}/route")
def route(request, case_id: str, payload: RouteIn):
    membership = require_roles(request, *ADMIN_ROLES)
    item = route_case(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, case_id=case_id, expected_version=payload.version, target=payload.target, role=payload.role)
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/cases/{case_id}/clarifications")
def clarification(request, case_id: str, payload: ClarificationIn):
    membership = require_roles(request, *ADMIN_ROLES)
    created, item = request_clarification(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, case_id=case_id, expected_version=payload.version, valuation_round=payload.valuation_round, request_text=payload.request)
    return {"id": str(created.id), "case_status": item.status, "case_version": item.version}


@router.post("/clarifications/{clarification_id}/respond")
def clarification_response(request, clarification_id: str, payload: ResponseIn):
    membership = require_roles(request, Membership.Role.EVALUATOR)
    evaluator = Evaluator.objects.filter(tenant_id=membership.institution.tenant_id, email__iexact=request.auth.email, status=Evaluator.Status.ACTIVE).first()
    if not evaluator:
        raise HttpError(403, "Active evaluator profile not found")
    item = respond_clarification(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, clarification_id=clarification_id, evaluator=evaluator, response=payload.response)
    return {"id": str(item.id), "responded_at": item.responded_at.isoformat()}


@router.post("/cases/{case_id}/resolve")
def resolve(request, case_id: str, payload: ResolutionIn):
    membership = require_roles(request, *ADMIN_ROLES)
    if payload.method == DiscrepancyResolution.Method.OVERRIDE:
        require_step_up(request)
    resolution, item, final = resolve_case(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, case_id=case_id, expected_version=payload.version, method=payload.method, final_mark=payload.final_mark, reason=payload.reason, calculation=payload.calculation)
    return {"id": str(resolution.id), "case_status": item.status, "case_version": item.version, "final_mark_id": str(final.id), "final_mark_version": final.version}


@router.post("/resolutions/{resolution_id}/approve")
def resolution_approval(request, resolution_id: str):
    membership = require_roles(request, *ADMIN_ROLES)
    resolution, item, final = approve_resolution(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, resolution_id=resolution_id)
    return {"id": str(resolution.id), "case_status": item.status, "case_version": item.version, "final_mark_id": str(final.id), "final_mark_status": final.status, "final_mark_version": final.version}
