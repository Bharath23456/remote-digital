from ninja import Field, Router, Schema
from ninja.errors import HttpError

from apps.configuration.models import Paper, Question
from apps.core.authz import membership_for, require_roles
from apps.evaluators.models import Evaluator
from apps.tenancy.custom_fields import persist_custom_values, validate_custom_values
from apps.tenancy.models import Membership

from .models import MarkingScheme, RubricCriterion, SchemeClarification
from .services import acknowledge, add_criterion, create_scheme, publish_clarification, transition_scheme


router = Router(tags=["Marking schemes and rubrics"])
ADMIN_ROLES = (Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER)


class SchemeIn(Schema):
    paper_id: str
    title: str
    evaluation_guidelines: str
    examiner_instructions: str
    model_answer_key: str = ""
    suggested_answer_key: str = ""
    subject_rules: dict = Field(default_factory=dict)
    regulation_rules: dict = Field(default_factory=dict)
    partial_credit_rules: dict = Field(default_factory=dict)
    tolerance_rules: dict = Field(default_factory=dict)
    special_rules: dict = Field(default_factory=dict)
    custom_fields: dict = Field(default_factory=dict)


class CriterionIn(Schema):
    question_id: str
    code: str
    description: str
    max_marks: float
    step_marks: list = Field(default_factory=list)
    partial_credit_rule: dict = Field(default_factory=dict)
    tolerance: float = 0
    position: int = 1
    is_mandatory: bool = False


class TransitionIn(Schema):
    target: str
    reason: str = ""


class ClarificationIn(Schema):
    question_id: str | None = None
    scope: str
    title: str
    body: str
    mandatory_acknowledgement: bool = False
    supersedes_id: str | None = None


class AcknowledgeIn(Schema):
    clarification_id: str | None = None


def _scheme(tenant_id, scheme_id):
    item = MarkingScheme.objects.filter(id=scheme_id, tenant_id=tenant_id).select_related("paper").first()
    if not item:
        raise HttpError(404, "Marking scheme not found")
    return item


def _question_label(question):
    return f"{question.number}({question.sub_question})" if question.sub_question else question.number


@router.get("/catalog")
def catalog(request):
    membership = membership_for(request)
    tenant_id = membership.institution.tenant_id
    schemes = MarkingScheme.objects.filter(tenant_id=tenant_id).select_related("paper").order_by("paper__code", "-version")
    return {
        "papers": [{"id": str(item.id), "code": item.code, "title": item.title, "questions": [{"id": str(question.id), "number": question.number, "sub_question": question.sub_question, "label": _question_label(question), "max_marks": float(question.max_marks)} for question in item.questions.all()]} for item in Paper.objects.filter(tenant_id=tenant_id).prefetch_related("questions").order_by("code")],
        "schemes": [{"id": str(item.id), "paper_id": str(item.paper_id), "paper": item.paper.code, "title": item.title, "version": item.version, "status": item.status, "guidelines": item.evaluation_guidelines, "instructions": item.examiner_instructions, "content_digest": item.content_digest} for item in schemes],
        "criteria": [{"id": str(item.id), "scheme_id": str(item.scheme_id), "question_id": str(item.question_id), "question": _question_label(item.question), "code": item.code, "description": item.description, "max_marks": float(item.max_marks), "step_marks": item.step_marks, "tolerance": float(item.tolerance), "mandatory": item.is_mandatory} for item in RubricCriterion.objects.filter(tenant_id=tenant_id).select_related("question")],
        "clarifications": [{"id": str(item.id), "scheme_id": str(item.scheme_id), "question": _question_label(item.question) if item.question else None, "scope": item.scope, "title": item.title, "body": item.body, "version": item.version, "mandatory": item.mandatory_acknowledgement} for item in SchemeClarification.objects.filter(tenant_id=tenant_id).select_related("question")],
    }


@router.post("/schemes")
def add_scheme(request, payload: SchemeIn):
    membership = require_roles(request, *ADMIN_ROLES)
    tenant_id = membership.institution.tenant_id
    custom_fields = validate_custom_values(tenant_id=tenant_id, form_key="rubric", values=payload.custom_fields)
    paper = Paper.objects.filter(id=payload.paper_id, tenant_id=tenant_id).first()
    if not paper:
        raise HttpError(404, "Paper not found")
    values = payload.dict(exclude={"paper_id", "custom_fields"})
    item = create_scheme(tenant_id=tenant_id, actor_id=request.auth.id, paper=paper, values=values)
    persist_custom_values(tenant_id=tenant_id, actor_id=request.auth.id, form_key="rubric", record_id=item.id, values=custom_fields)
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/schemes/{scheme_id}/criteria")
def create_criterion(request, scheme_id: str, payload: CriterionIn):
    membership = require_roles(request, *ADMIN_ROLES)
    tenant_id = membership.institution.tenant_id
    scheme = _scheme(tenant_id, scheme_id)
    question = Question.objects.filter(id=payload.question_id, tenant_id=tenant_id).first()
    if not question:
        raise HttpError(404, "Question not found")
    item = add_criterion(tenant_id=tenant_id, actor_id=request.auth.id, scheme=scheme, question=question, values=payload.dict(exclude={"question_id"}))
    return {"id": str(item.id)}


@router.delete("/schemes/{scheme_id}/criteria/{criterion_id}")
def delete_criterion(request, scheme_id: str, criterion_id: str):
    membership = require_roles(request, *ADMIN_ROLES)
    tenant_id = membership.institution.tenant_id
    scheme = _scheme(tenant_id, scheme_id)
    if scheme.status != MarkingScheme.Status.DRAFT:
        raise HttpError(409, "Rubric criteria can only change while the scheme is in draft")
    item = RubricCriterion.objects.filter(id=criterion_id, scheme=scheme, tenant_id=tenant_id).first()
    if not item:
        raise HttpError(404, "Rubric criterion not found")
    item.delete()
    return {"deleted": True}


@router.post("/schemes/{scheme_id}/transition")
def scheme_transition(request, scheme_id: str, payload: TransitionIn):
    membership = require_roles(request, *ADMIN_ROLES)
    item = transition_scheme(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, scheme_id=scheme_id, target=payload.target, reason=payload.reason)
    return {"id": str(item.id), "status": item.status, "content_digest": item.content_digest}


@router.post("/schemes/{scheme_id}/clarifications")
def create_clarification(request, scheme_id: str, payload: ClarificationIn):
    membership = require_roles(request, *ADMIN_ROLES)
    tenant_id = membership.institution.tenant_id
    scheme = _scheme(tenant_id, scheme_id)
    question = Question.objects.filter(id=payload.question_id, tenant_id=tenant_id).first() if payload.question_id else None
    supersedes = SchemeClarification.objects.filter(id=payload.supersedes_id, tenant_id=tenant_id).first() if payload.supersedes_id else None
    item = publish_clarification(tenant_id=tenant_id, actor_id=request.auth.id, scheme=scheme, question=question, supersedes=supersedes, **payload.dict(exclude={"question_id", "supersedes_id"}))
    return {"id": str(item.id), "version": item.version}


@router.post("/schemes/{scheme_id}/acknowledge")
def acknowledge_instructions(request, scheme_id: str, payload: AcknowledgeIn):
    membership = require_roles(request, Membership.Role.EVALUATOR)
    tenant_id = membership.institution.tenant_id
    scheme = _scheme(tenant_id, scheme_id)
    evaluator = Evaluator.objects.filter(tenant_id=tenant_id, email__iexact=request.auth.email).first()
    clarification = SchemeClarification.objects.filter(id=payload.clarification_id, tenant_id=tenant_id).first() if payload.clarification_id else None
    if not evaluator:
        raise HttpError(403, "Evaluator profile not found")
    item = acknowledge(tenant_id=tenant_id, actor_id=request.auth.id, scheme=scheme, evaluator=evaluator, clarification=clarification)
    return {"id": str(item.id), "acknowledged_at": item.acknowledged_at.isoformat()}
