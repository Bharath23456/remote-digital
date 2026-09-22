from datetime import datetime

from ninja import Router, Schema
from ninja.errors import HttpError

from apps.core.authz import require_roles, require_secure_evaluation_session, require_step_up
from apps.custody.models import Script
from apps.evaluators.models import Evaluator
from apps.marking.models import Evaluation
from apps.tenancy.models import Membership

from .models import FinalMark, RevealAuthorization, ValuationComparison, ValuationResult
from .services import approve_final_mark, approve_reveal, finalize_valuation, lock_final_mark, request_reveal


router = Router(tags=["Multi-valuation engine and final mark locking"])
ADMIN_ROLES = (Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER)


class RevealIn(Schema):
    script_id: str
    from_round: int
    to_round: int
    purpose: str
    expires_at: datetime


class VersionIn(Schema):
    version: int


@router.get("/catalog")
def catalog(request):
    membership = require_roles(request, *ADMIN_ROLES)
    tenant_id = membership.institution.tenant_id
    return {
        "results": [{"id": str(item.id), "script": item.script.script_code, "round": item.valuation_round, "total_marks": float(item.total_marks), "checksum": item.checksum, "locked": item.is_locked, "locked_at": item.locked_at.isoformat() if item.locked_at else None} for item in ValuationResult.objects.filter(tenant_id=tenant_id).select_related("script").order_by("script__script_code", "valuation_round")],
        "comparisons": [{"id": str(item.id), "script": item.script.script_code, "first": float(item.first_result.total_marks), "second": float(item.second_result.total_marks), "third": float(item.third_result.total_marks) if item.third_result else None, "difference": float(item.total_difference), "percentage_difference": float(item.percentage_difference), "threshold": float(item.threshold), "status": item.status, "requires_third": item.requires_third_valuation, "question_differences": item.question_differences} for item in ValuationComparison.objects.filter(tenant_id=tenant_id).select_related("script", "first_result", "second_result", "third_result")],
        "final_marks": [{"id": str(item.id), "script": item.script.script_code, "mark": float(item.mark), "rule": item.rule, "status": item.status, "checksum": item.checksum, "version": item.version} for item in FinalMark.objects.filter(tenant_id=tenant_id).select_related("script")],
        "reveals": [{"id": str(item.id), "script": item.script.script_code, "from_round": item.from_round, "to_round": item.to_round, "purpose": item.purpose, "approved": bool(item.approved_at), "expires_at": item.expires_at.isoformat()} for item in RevealAuthorization.objects.filter(tenant_id=tenant_id).select_related("script")],
    }


@router.post("/evaluations/{evaluation_id}/finalize")
def finalize(request, evaluation_id: str):
    membership = require_roles(request, Membership.Role.EVALUATOR)
    tenant_id = membership.institution.tenant_id
    evaluator = Evaluator.objects.filter(tenant_id=tenant_id, email__iexact=request.auth.email, status=Evaluator.Status.ACTIVE).first()
    evaluation = Evaluation.objects.filter(id=evaluation_id, tenant_id=tenant_id, assignment__evaluator=evaluator).select_related("assignment").first() if evaluator else None
    if not evaluation:
        raise HttpError(404, "Evaluation not found")
    require_secure_evaluation_session(request, evaluation.assignment)
    item, replayed = finalize_valuation(tenant_id=tenant_id, actor_id=request.auth.id, evaluation_id=evaluation_id, evaluator=evaluator, idempotency_key=request.headers.get("Idempotency-Key", ""))
    return {"id": str(item.id), "round": item.valuation_round, "total_marks": float(item.total_marks), "checksum": item.checksum, "locked": item.is_locked, "replayed": replayed}


@router.post("/reveals")
def create_reveal(request, payload: RevealIn):
    membership = require_roles(request, *ADMIN_ROLES)
    require_step_up(request)
    script = Script.objects.filter(id=payload.script_id, tenant_id=membership.institution.tenant_id).first()
    if not script:
        raise HttpError(404, "Script not found")
    item = request_reveal(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, script=script, from_round=payload.from_round, to_round=payload.to_round, purpose=payload.purpose, expires_at=payload.expires_at)
    return {"id": str(item.id)}


@router.post("/reveals/{reveal_id}/approve")
def reveal_approval(request, reveal_id: str):
    membership = require_roles(request, *ADMIN_ROLES)
    require_step_up(request)
    item = approve_reveal(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, reveal_id=reveal_id)
    return {"id": str(item.id), "approved_at": item.approved_at.isoformat()}


@router.post("/final-marks/{final_mark_id}/approve")
def final_mark_approval(request, final_mark_id: str, payload: VersionIn):
    membership = require_roles(request, *ADMIN_ROLES)
    item = approve_final_mark(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, final_mark_id=final_mark_id, expected_version=payload.version)
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/final-marks/{final_mark_id}/lock")
def final_mark_lock(request, final_mark_id: str, payload: VersionIn):
    membership = require_roles(request, *ADMIN_ROLES)
    item, replayed = lock_final_mark(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, final_mark_id=final_mark_id, expected_version=payload.version, idempotency_key=request.headers.get("Idempotency-Key", ""))
    return {"id": str(item.id), "status": item.status, "checksum": item.checksum, "version": item.version, "replayed": replayed}
