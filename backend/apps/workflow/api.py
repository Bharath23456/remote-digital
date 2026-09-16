from datetime import datetime

from django.db import transaction
from ninja import Field, Router, Schema
from ninja.errors import HttpError

from apps.allocation.models import Assignment
from apps.configuration.models import Question
from apps.core.authz import membership_for, require_roles, require_secure_evaluation_session
from apps.evaluators.models import Evaluator
from apps.marking.models import Evaluation
from apps.tenancy.models import Membership
from apps.valuation.services import finalize_valuation

from .models import EvaluationExtension, EvaluationWorkflow
from .services import decide_extension, request_extension, save_draft, submit_evaluation


router = Router(tags=["Evaluation workflow and guided operations"])
ADMIN_ROLES = (Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER)


class DraftIn(Schema):
    version: int
    client_sequence: int
    last_question_id: str | None = None
    last_page: int = 1
    ui_state: dict = Field(default_factory=dict)


class SubmitIn(Schema):
    evaluation_version: int
    workflow_version: int


class ExtensionIn(Schema):
    requested_until: datetime
    reason: str


class ExtensionDecisionIn(Schema):
    version: int
    approve: bool
    note: str = ""


def _evaluator_assignment(request, assignment_id):
    membership = require_roles(request, Membership.Role.EVALUATOR)
    tenant_id = membership.institution.tenant_id
    evaluator = Evaluator.objects.filter(tenant_id=tenant_id, email__iexact=request.auth.email, status=Evaluator.Status.ACTIVE).first()
    assignment = Assignment.objects.filter(id=assignment_id, tenant_id=tenant_id, evaluator=evaluator).select_related("script__paper").first() if evaluator else None
    if not assignment:
        raise HttpError(404, "Assignment not found")
    return tenant_id, evaluator, assignment


@router.get("/catalog")
def catalog(request):
    membership = membership_for(request)
    tenant_id = membership.institution.tenant_id
    workflows = EvaluationWorkflow.objects.filter(tenant_id=tenant_id).select_related("assignment__script")
    if membership.role == Membership.Role.EVALUATOR:
        workflows = workflows.filter(assignment__evaluator__email__iexact=request.auth.email)
    return {
        "workflows": [{"id": str(item.id), "assignment_id": str(item.assignment_id), "script": item.assignment.script.script_code, "state": item.state, "last_page": item.last_page, "expires_at": item.draft_expires_at.isoformat() if item.draft_expires_at else None, "version": item.version} for item in workflows[:1000]],
        "extensions": [{"id": str(item.id), "workflow_id": str(item.workflow_id), "status": item.status, "requested_until": item.requested_until.isoformat(), "reason": item.reason, "version": item.version} for item in EvaluationExtension.objects.filter(tenant_id=tenant_id, workflow__in=workflows)[:1000]],
    }


@router.post("/assignments/{assignment_id}/drafts")
def store_draft(request, assignment_id: str, payload: DraftIn):
    tenant_id, evaluator, assignment = _evaluator_assignment(request, assignment_id)
    require_secure_evaluation_session(request, assignment)
    workflow = EvaluationWorkflow.objects.filter(tenant_id=tenant_id, assignment=assignment).first()
    if not workflow:
        raise HttpError(404, "Evaluation workflow not found")
    question = Question.objects.filter(id=payload.last_question_id, paper=assignment.script.paper).first() if payload.last_question_id else None
    draft, current = save_draft(tenant_id=tenant_id, actor_id=request.auth.id, workflow_id=workflow.id, assignment=assignment, evaluator=evaluator, expected_version=payload.version, lock_token=request.headers.get("X-Assignment-Lock", ""), client_sequence=payload.client_sequence, last_question=question, last_page=payload.last_page, ui_state=payload.ui_state)
    return {"id": str(draft.id), "checksum": draft.checksum, "version": current.version, "assignment_version": assignment.version}


@router.post("/evaluations/{evaluation_id}/submit")
def submit(request, evaluation_id: str, payload: SubmitIn):
    membership = require_roles(request, Membership.Role.EVALUATOR)
    tenant_id = membership.institution.tenant_id
    evaluator = Evaluator.objects.filter(tenant_id=tenant_id, email__iexact=request.auth.email, status=Evaluator.Status.ACTIVE).first()
    evaluation = Evaluation.objects.filter(id=evaluation_id, tenant_id=tenant_id, assignment__evaluator=evaluator).select_related("assignment").first() if evaluator else None
    if not evaluation:
        raise HttpError(404, "Evaluation not found")
    require_secure_evaluation_session(request, evaluation.assignment)
    idempotency_key = request.headers.get("Idempotency-Key", "")
    with transaction.atomic():
        if evaluation.status in (Evaluation.Status.SUBMITTED, Evaluation.Status.LOCKED):
            item = evaluation
            replayed = True
        else:
            item, replayed = submit_evaluation(tenant_id=tenant_id, actor_id=request.auth.id, evaluation_id=evaluation_id, evaluator=evaluator, evaluation_version=payload.evaluation_version, workflow_version=payload.workflow_version, lock_token=request.headers.get("X-Assignment-Lock", ""), idempotency_key=idempotency_key)
        result, valuation_replayed = finalize_valuation(tenant_id=tenant_id, actor_id=request.auth.id, evaluation_id=evaluation_id, evaluator=evaluator, idempotency_key=f"{idempotency_key}:valuation")
    item.refresh_from_db()
    return {"id": str(item.id), "status": item.status, "total_marks": float(item.total_marks), "checksum": item.checksum, "version": item.version, "replayed": replayed, "valuation_result_id": str(result.id), "valuation_replayed": valuation_replayed}


@router.post("/workflows/{workflow_id}/extensions")
def create_extension(request, workflow_id: str, payload: ExtensionIn):
    membership = membership_for(request)
    tenant_id = membership.institution.tenant_id
    workflow = EvaluationWorkflow.objects.filter(id=workflow_id, tenant_id=tenant_id).select_related("assignment__evaluator").first()
    if not workflow:
        raise HttpError(404, "Workflow not found")
    if membership.role == Membership.Role.EVALUATOR and workflow.assignment.evaluator.email.lower() != request.auth.email.lower():
        raise HttpError(404, "Workflow not found")
    item = request_extension(tenant_id=tenant_id, actor_id=request.auth.id, workflow=workflow, requested_until=payload.requested_until, reason=payload.reason)
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/extensions/{extension_id}/decision")
def extension_decision(request, extension_id: str, payload: ExtensionDecisionIn):
    membership = require_roles(request, *ADMIN_ROLES)
    item = decide_extension(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, extension_id=extension_id, expected_version=payload.version, approve=payload.approve, note=payload.note)
    return {"id": str(item.id), "status": item.status, "version": item.version}
