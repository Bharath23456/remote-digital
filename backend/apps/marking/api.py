from ninja import Field, Router, Schema
from ninja.errors import HttpError

from apps.allocation.models import Assignment
from apps.core.authz import require_roles, require_secure_evaluation_session
from apps.evaluators.models import Evaluator
from apps.rubrics.models import RubricCriterion
from apps.tenancy.models import Membership
from apps.workflow.services import start_workflow

from .models import Evaluation
from .services import add_annotation, add_comment, change_annotation, effective_annotations, latest_marks, open_evaluation, save_question_mark, transition_evaluation


router = Router(tags=["Digital annotation and question-wise evaluation"])


class MarkIn(Schema):
    version: int
    question_id: str
    sub_question: str = ""
    marks: float = 0
    outcome: str = "evaluated"
    adjustment: str = "none"
    step_marks: list = Field(default_factory=list)
    rubric_criterion_id: str | None = None
    marked_for_review: bool = False
    requires_attention: bool = False
    examiner_confirmed: bool = False
    answer_selection: dict = Field(default_factory=dict)


class AnnotationIn(Schema):
    version: int
    page_number: int
    question_id: str | None = None
    kind: str
    geometry: dict = Field(default_factory=dict)
    style: dict = Field(default_factory=dict)
    symbol: str = ""


class AnnotationActionIn(Schema):
    version: int
    action: str


class CommentIn(Schema):
    version: int
    question_id: str | None = None
    page_number: int | None = None
    kind: str
    body: str


class TransitionIn(Schema):
    version: int
    target: str


def _evaluator_context(request, assignment_id=None):
    membership = require_roles(request, Membership.Role.EVALUATOR)
    tenant_id = membership.institution.tenant_id
    evaluator = Evaluator.objects.filter(tenant_id=tenant_id, email__iexact=request.auth.email, status=Evaluator.Status.ACTIVE).first()
    if not evaluator:
        raise HttpError(403, "Active evaluator profile not found")
    assignment = None
    if assignment_id:
        assignment = Assignment.objects.filter(id=assignment_id, tenant_id=tenant_id, evaluator=evaluator).select_related("script__paper").first()
        if not assignment:
            raise HttpError(404, "Assignment not found")
    return tenant_id, evaluator, assignment


def _lock_token(request):
    return request.headers.get("X-Assignment-Lock", "")


def _question(assignment, question_id):
    question = assignment.script.paper.questions.filter(id=question_id).first()
    if not question:
        raise HttpError(404, "Question not found in this paper")
    return question


@router.post("/assignments/{assignment_id}/open")
def open_workspace(request, assignment_id: str):
    tenant_id, evaluator, assignment = _evaluator_context(request, assignment_id)
    require_secure_evaluation_session(request, assignment)
    evaluation = open_evaluation(tenant_id=tenant_id, actor_id=request.auth.id, assignment=assignment, evaluator=evaluator)
    workflow = start_workflow(tenant_id=tenant_id, actor_id=request.auth.id, assignment=assignment)
    return {"evaluation_id": str(evaluation.id), "evaluation_version": evaluation.version, "workflow_id": str(workflow.id), "workflow_version": workflow.version, "scheme_id": str(evaluation.scheme_id)}


@router.get("/assignments/{assignment_id}/workspace")
def workspace(request, assignment_id: str):
    tenant_id, evaluator, assignment = _evaluator_context(request, assignment_id)
    require_secure_evaluation_session(request, assignment, allow_paused=True)
    evaluation = Evaluation.objects.filter(tenant_id=tenant_id, assignment=assignment).select_related("scheme", "assignment__script__paper").first()
    if not evaluation:
        raise HttpError(404, "Open this assignment before loading the marking workspace")
    workflow = getattr(assignment, "workflow", None)
    marks = latest_marks(evaluation)
    annotations = effective_annotations(evaluation)
    return {
        "assignment": {"id": str(assignment.id), "script": assignment.script.script_code, "paper": assignment.script.paper.code, "round": assignment.valuation_round, "page_count": assignment.script.page_count, "status": assignment.status},
        "evaluation": {"id": str(evaluation.id), "status": evaluation.status, "total_marks": float(evaluation.total_marks), "version": evaluation.version, "last_question_id": str(evaluation.last_question_id) if evaluation.last_question_id else None, "last_page": evaluation.last_page},
        "workflow": {"id": str(workflow.id), "state": workflow.state, "version": workflow.version, "last_page": workflow.last_page, "latest_draft_sequence": workflow.drafts.order_by("-client_sequence").values_list("client_sequence", flat=True).first() or 0, "draft_expires_at": workflow.draft_expires_at.isoformat() if workflow.draft_expires_at else None} if workflow else None,
        "scheme": {"id": str(evaluation.scheme_id), "title": evaluation.scheme.title, "version": evaluation.scheme.version, "digest": evaluation.scheme.content_digest, "guidelines": evaluation.scheme.evaluation_guidelines, "instructions": evaluation.scheme.examiner_instructions},
        "questions": [{"id": str(question.id), "number": question.number, "max_marks": float(question.max_marks), "required": question.required, "criteria": [{"id": str(item.id), "code": item.code, "description": item.description, "max_marks": float(item.max_marks), "step_marks": item.step_marks, "mandatory": item.is_mandatory} for item in evaluation.scheme.criteria.filter(question=question)]} for question in assignment.script.paper.questions.all()],
        "marks": [{"id": str(item.id), "question_id": str(item.question_id), "sub_question": item.sub_question, "marks": float(item.marks), "outcome": item.outcome, "adjustment": item.adjustment, "step_marks": item.step_marks, "criterion_id": str(item.rubric_criterion_id) if item.rubric_criterion_id else None, "marked_for_review": item.marked_for_review, "requires_attention": item.requires_attention, "examiner_confirmed": item.examiner_confirmed, "sequence": item.sequence} for item in marks],
        "annotations": [{"id": str(item.id), "page_number": item.page_number, "question_id": str(item.question_id) if item.question_id else None, "kind": item.kind, "geometry": item.geometry, "style": item.style, "symbol": item.symbol} for item in annotations],
        "comments": [{"id": str(item.id), "question_id": str(item.question_id) if item.question_id else None, "page_number": item.page_number, "kind": item.kind, "body": item.body, "created_at": item.created_at.isoformat()} for item in evaluation.comments.all()],
    }


@router.post("/evaluations/{evaluation_id}/marks")
def record_mark(request, evaluation_id: str, payload: MarkIn):
    tenant_id, evaluator, assignment = _evaluator_context(request)
    evaluation = Evaluation.objects.filter(id=evaluation_id, tenant_id=tenant_id, assignment__evaluator=evaluator).select_related("assignment__script__paper").first()
    if not evaluation:
        raise HttpError(404, "Evaluation not found")
    assignment = evaluation.assignment
    require_secure_evaluation_session(request, assignment)
    criterion = RubricCriterion.objects.filter(id=payload.rubric_criterion_id, tenant_id=tenant_id).first() if payload.rubric_criterion_id else None
    mark, current = save_question_mark(tenant_id=tenant_id, actor_id=request.auth.id, evaluation_id=evaluation_id, evaluator=evaluator, expected_version=payload.version, lock_token=_lock_token(request), question=_question(assignment, payload.question_id), values={**payload.dict(exclude={"version", "question_id", "rubric_criterion_id"}), "rubric_criterion": criterion})
    return {"id": str(mark.id), "sequence": mark.sequence, "total_marks": float(current.total_marks), "version": current.version}


@router.post("/evaluations/{evaluation_id}/annotations")
def record_annotation(request, evaluation_id: str, payload: AnnotationIn):
    tenant_id, evaluator, _ = _evaluator_context(request)
    evaluation = Evaluation.objects.filter(id=evaluation_id, tenant_id=tenant_id, assignment__evaluator=evaluator).select_related("assignment__script__paper").first()
    if not evaluation:
        raise HttpError(404, "Evaluation not found")
    require_secure_evaluation_session(request, evaluation.assignment)
    question = _question(evaluation.assignment, payload.question_id) if payload.question_id else None
    item, current = add_annotation(tenant_id=tenant_id, actor_id=request.auth.id, evaluation_id=evaluation_id, evaluator=evaluator, expected_version=payload.version, lock_token=_lock_token(request), page_number=payload.page_number, question=question, kind=payload.kind, geometry=payload.geometry, style=payload.style, symbol=payload.symbol)
    return {"id": str(item.id), "version": current.version}


@router.post("/evaluations/{evaluation_id}/annotations/{annotation_id}/action")
def annotation_action(request, evaluation_id: str, annotation_id: str, payload: AnnotationActionIn):
    tenant_id, evaluator, _ = _evaluator_context(request)
    evaluation = Evaluation.objects.filter(id=evaluation_id, tenant_id=tenant_id, assignment__evaluator=evaluator).select_related("assignment").first()
    if not evaluation:
        raise HttpError(404, "Evaluation not found")
    require_secure_evaluation_session(request, evaluation.assignment)
    item, current = change_annotation(tenant_id=tenant_id, actor_id=request.auth.id, evaluation_id=evaluation_id, evaluator=evaluator, expected_version=payload.version, lock_token=_lock_token(request), target_id=annotation_id, action=payload.action)
    return {"id": str(item.id), "action": item.action, "version": current.version}


@router.post("/evaluations/{evaluation_id}/comments")
def record_comment(request, evaluation_id: str, payload: CommentIn):
    tenant_id, evaluator, _ = _evaluator_context(request)
    evaluation = Evaluation.objects.filter(id=evaluation_id, tenant_id=tenant_id, assignment__evaluator=evaluator).select_related("assignment__script__paper").first()
    if not evaluation:
        raise HttpError(404, "Evaluation not found")
    require_secure_evaluation_session(request, evaluation.assignment)
    question = _question(evaluation.assignment, payload.question_id) if payload.question_id else None
    item, current = add_comment(tenant_id=tenant_id, actor_id=request.auth.id, evaluation_id=evaluation_id, evaluator=evaluator, expected_version=payload.version, lock_token=_lock_token(request), question=question, page_number=payload.page_number, kind=payload.kind, body=payload.body)
    return {"id": str(item.id), "version": current.version}


@router.post("/evaluations/{evaluation_id}/transition")
def evaluation_transition(request, evaluation_id: str, payload: TransitionIn):
    tenant_id, evaluator, _ = _evaluator_context(request)
    evaluation = Evaluation.objects.filter(id=evaluation_id, tenant_id=tenant_id, assignment__evaluator=evaluator).select_related("assignment").first()
    if not evaluation:
        raise HttpError(404, "Evaluation not found")
    require_secure_evaluation_session(request, evaluation.assignment)
    item = transition_evaluation(tenant_id=tenant_id, actor_id=request.auth.id, evaluation_id=evaluation_id, evaluator=evaluator, expected_version=payload.version, lock_token=_lock_token(request), target=payload.target)
    return {"id": str(item.id), "status": item.status, "version": item.version}
