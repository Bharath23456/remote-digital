import hashlib
import json
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from ninja.errors import HttpError

from apps.assignment.models import AssignmentLock
from apps.core.services import record_event
from apps.rubrics.models import MarkingScheme
from apps.rubrics.services import evaluator_has_required_acknowledgements

from .models import Annotation, Evaluation, EvaluationComment, QuestionMark, QuestionPageAnchor


EVALUATION_TRANSITIONS = {
    Evaluation.Status.DRAFT: {Evaluation.Status.REVIEW, Evaluation.Status.SUBMITTED},
    Evaluation.Status.REVIEW: {Evaluation.Status.DRAFT, Evaluation.Status.SUBMITTED},
    Evaluation.Status.SUBMITTED: {Evaluation.Status.LOCKED},
}


def verify_assignment_lock(*, assignment, evaluator, token):
    if not token:
        raise HttpError(423, "An active assignment lock is required")
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    lock = AssignmentLock.objects.filter(
        assignment=assignment,
        evaluator=evaluator,
        token_hash=token_hash,
        released_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).first()
    if not lock:
        raise HttpError(423, "Assignment lock is missing or expired")
    return lock


@transaction.atomic
def open_evaluation(*, tenant_id, actor_id, assignment, evaluator):
    scheme = MarkingScheme.objects.filter(tenant_id=tenant_id, paper=assignment.script.paper, status=MarkingScheme.Status.FROZEN).order_by("-version").first()
    if not scheme:
        raise HttpError(409, "A frozen marking scheme is required before evaluation")
    if not evaluator_has_required_acknowledgements(scheme, evaluator):
        raise HttpError(428, "The marking scheme and mandatory clarifications must be acknowledged")
    evaluation, created = Evaluation.objects.get_or_create(
        tenant_id=tenant_id,
        assignment=assignment,
        defaults={"scheme": scheme, "started_at": timezone.now()},
    )
    if not created and evaluation.scheme_id != scheme.id:
        raise HttpError(409, "Evaluation is bound to a different frozen scheme")
    if created:
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="marking.evaluation.opened", aggregate="Evaluation", aggregate_id=evaluation.id, payload={"assignment_id": str(assignment.id), "scheme_id": str(scheme.id)})
    return evaluation


def latest_marks(evaluation):
    result = {}
    for item in evaluation.question_marks.select_related("question", "rubric_criterion").order_by("question__position", "sub_question", "sequence"):
        result[(item.question_id, item.sub_question)] = item
    return list(result.values())


def effective_annotations(evaluation):
    state = {}
    for item in evaluation.annotations.select_related("question", "target").order_by("created_at"):
        if item.action == Annotation.Action.ADD:
            state[item.id] = item
        elif item.target_id:
            if item.action == Annotation.Action.DELETE:
                state.pop(item.target_id, None)
            elif item.action == Annotation.Action.RESTORE and item.target:
                state[item.target_id] = item.target
    return list(state.values())


def _decimal(value, label):
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise HttpError(422, f"{label} must be a valid number") from exc


def _recalculate_total(evaluation):
    total = sum((item.marks for item in latest_marks(evaluation)), Decimal("0"))
    evaluation.total_marks = total
    return total


@transaction.atomic
def save_question_mark(*, tenant_id, actor_id, evaluation_id, evaluator, expected_version, lock_token, question, values):
    evaluation = Evaluation.objects.select_for_update().select_related("assignment__script__paper").filter(id=evaluation_id, tenant_id=tenant_id, assignment__evaluator=evaluator).first()
    if not evaluation or evaluation.version != expected_version:
        raise HttpError(409, "Evaluation is missing or stale")
    verify_assignment_lock(assignment=evaluation.assignment, evaluator=evaluator, token=lock_token)
    if evaluation.status not in (Evaluation.Status.DRAFT, Evaluation.Status.REVIEW):
        raise HttpError(409, "Submitted marks are immutable")
    if question.paper_id != evaluation.assignment.script.paper_id:
        raise HttpError(422, "Question does not belong to this paper")
    outcome = values.get("outcome", QuestionMark.Outcome.EVALUATED)
    adjustment = values.get("adjustment", QuestionMark.Adjustment.NONE)
    if outcome not in QuestionMark.Outcome.values or adjustment not in QuestionMark.Adjustment.values:
        raise HttpError(422, "Unsupported marking outcome or adjustment")
    marks = _decimal(values.get("marks", 0), "Marks")
    if outcome in (QuestionMark.Outcome.UNANSWERED, QuestionMark.Outcome.NOT_APPLICABLE, QuestionMark.Outcome.SKIPPED) and marks != 0:
        raise HttpError(422, "Unanswered, skipped, and not-applicable outcomes must carry zero marks")
    if adjustment != QuestionMark.Adjustment.NEGATIVE and marks < 0:
        raise HttpError(422, "Negative marks require the negative adjustment")
    criterion = values.get("rubric_criterion")
    if criterion and (criterion.scheme_id != evaluation.scheme_id or criterion.question_id != question.id):
        raise HttpError(422, "Rubric criterion does not belong to this evaluation question")
    maximum = criterion.max_marks if criterion else question.max_marks
    if marks > maximum and adjustment != QuestionMark.Adjustment.BONUS:
        raise HttpError(422, f"Marks cannot exceed {maximum}")
    steps = values.get("step_marks", [])
    if steps:
        step_total = sum((_decimal(item.get("marks", 0), "Step mark") for item in steps), Decimal("0"))
        if step_total != marks:
            raise HttpError(422, "Step marks must total the awarded marks")
    sub_question = str(values.get("sub_question", ""))[:24]
    previous = evaluation.question_marks.filter(question=question, sub_question=sub_question).order_by("-sequence").first()
    sequence = (previous.sequence + 1) if previous else 1
    mark = QuestionMark.objects.create(
        tenant_id=tenant_id,
        evaluation=evaluation,
        question=question,
        sub_question=sub_question,
        sequence=sequence,
        marks=marks,
        outcome=outcome,
        adjustment=adjustment,
        step_marks=steps,
        rubric_criterion=criterion,
        marked_for_review=bool(values.get("marked_for_review", False)),
        requires_attention=bool(values.get("requires_attention", False)),
        examiner_confirmed=bool(values.get("examiner_confirmed", False)),
        answer_selection=values.get("answer_selection", {}),
        supersedes=previous,
        actor_id=actor_id,
    )
    _recalculate_total(evaluation)
    evaluation.last_question = question
    evaluation.version += 1
    evaluation.save(update_fields=["total_marks", "last_question", "version", "updated_at"])
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="marking.question_mark.recorded", aggregate="QuestionMark", aggregate_id=mark.id, payload={"evaluation_id": str(evaluation.id), "question": question.number, "revision": sequence, "marks": str(marks)})
    return mark, evaluation


def _number(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise HttpError(422, f"{label} must be numeric") from exc
    if not 0 <= number <= 1:
        raise HttpError(422, f"{label} must be normalized between 0 and 1")
    return number


def validate_geometry(kind, geometry):
    if not isinstance(geometry, dict):
        raise HttpError(422, "Annotation geometry must be an object")
    if kind in (Annotation.Kind.TICK, Annotation.Kind.CROSS, Annotation.Kind.SYMBOL):
        for field in ("x", "y"):
            _number(geometry.get(field), field)
    elif kind in (Annotation.Kind.HIGHLIGHT, Annotation.Kind.CIRCLE, Annotation.Kind.RECTANGLE, Annotation.Kind.UNDERLINE):
        for field in ("x", "y", "width", "height"):
            _number(geometry.get(field), field)
        if float(geometry["x"]) + float(geometry["width"]) > 1 or float(geometry["y"]) + float(geometry["height"]) > 1:
            raise HttpError(422, "Annotation bounds exceed the page")
    elif kind in (Annotation.Kind.ARROW, Annotation.Kind.FREEHAND):
        points = geometry.get("points")
        if not isinstance(points, list) or len(points) < 2 or len(points) > 2000:
            raise HttpError(422, "Line annotations require between 2 and 2000 points")
        for point in points:
            _number(point.get("x"), "point.x")
            _number(point.get("y"), "point.y")
    else:
        raise HttpError(422, "Unsupported annotation kind")


@transaction.atomic
def add_annotation(*, tenant_id, actor_id, evaluation_id, evaluator, expected_version, lock_token, page_number, question, kind, geometry, style, symbol):
    evaluation = Evaluation.objects.select_for_update().select_related("assignment__script").filter(id=evaluation_id, tenant_id=tenant_id, assignment__evaluator=evaluator).first()
    if not evaluation or evaluation.version != expected_version:
        raise HttpError(409, "Evaluation is missing or stale")
    verify_assignment_lock(assignment=evaluation.assignment, evaluator=evaluator, token=lock_token)
    if evaluation.status not in (Evaluation.Status.DRAFT, Evaluation.Status.REVIEW):
        raise HttpError(409, "Submitted annotations are immutable")
    if not 1 <= page_number <= max(evaluation.assignment.script.page_count, 1):
        raise HttpError(422, "Annotation page is outside the script")
    validate_geometry(kind, geometry)
    annotation = Annotation.objects.create(tenant_id=tenant_id, evaluation=evaluation, page_number=page_number, question=question, kind=kind, geometry=geometry, style=style, symbol=symbol[:80], actor_id=actor_id)
    evaluation.last_page = page_number
    evaluation.version += 1
    evaluation.save(update_fields=["last_page", "version", "updated_at"])
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="marking.annotation.added", aggregate="Annotation", aggregate_id=annotation.id, payload={"evaluation_id": str(evaluation.id), "page": page_number, "kind": kind})
    return annotation, evaluation


@transaction.atomic
def change_annotation(*, tenant_id, actor_id, evaluation_id, evaluator, expected_version, lock_token, target_id, action):
    evaluation = Evaluation.objects.select_for_update().select_related("assignment").filter(id=evaluation_id, tenant_id=tenant_id, assignment__evaluator=evaluator).first()
    if not evaluation or evaluation.version != expected_version:
        raise HttpError(409, "Evaluation is missing or stale")
    verify_assignment_lock(assignment=evaluation.assignment, evaluator=evaluator, token=lock_token)
    if evaluation.status not in (Evaluation.Status.DRAFT, Evaluation.Status.REVIEW) or action not in (Annotation.Action.DELETE, Annotation.Action.RESTORE):
        raise HttpError(409, "Annotation action is not allowed")
    target = Annotation.objects.filter(id=target_id, evaluation=evaluation, action=Annotation.Action.ADD).first()
    if not target:
        raise HttpError(404, "Annotation not found")
    active_ids = {item.id for item in effective_annotations(evaluation)}
    if action == Annotation.Action.DELETE and target.id not in active_ids:
        raise HttpError(409, "Annotation is already deleted")
    if action == Annotation.Action.RESTORE and target.id in active_ids:
        raise HttpError(409, "Annotation is already active")
    event = Annotation.objects.create(tenant_id=tenant_id, evaluation=evaluation, page_number=target.page_number, question=target.question, kind=target.kind, action=action, geometry=target.geometry, style=target.style, symbol=target.symbol, target=target, actor_id=actor_id)
    evaluation.version += 1
    evaluation.save(update_fields=["version", "updated_at"])
    record_event(tenant_id=tenant_id, actor_id=actor_id, action=f"marking.annotation.{action}", aggregate="Annotation", aggregate_id=event.id, payload={"evaluation_id": str(evaluation.id), "target_id": str(target.id)})
    return event, evaluation


@transaction.atomic
def add_comment(*, tenant_id, actor_id, evaluation_id, evaluator, expected_version, lock_token, question, page_number, kind, body):
    evaluation = Evaluation.objects.select_for_update().select_related("assignment").filter(id=evaluation_id, tenant_id=tenant_id, assignment__evaluator=evaluator).first()
    if not evaluation or evaluation.version != expected_version:
        raise HttpError(409, "Evaluation is missing or stale")
    verify_assignment_lock(assignment=evaluation.assignment, evaluator=evaluator, token=lock_token)
    if evaluation.status not in (Evaluation.Status.DRAFT, Evaluation.Status.REVIEW) or kind not in EvaluationComment.Kind.values or not body.strip():
        raise HttpError(422, "Comment is invalid or evaluation is immutable")
    item = EvaluationComment.objects.create(tenant_id=tenant_id, evaluation=evaluation, question=question, page_number=page_number, kind=kind, body=body.strip(), actor_id=actor_id)
    evaluation.version += 1
    evaluation.save(update_fields=["version", "updated_at"])
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="marking.comment.added", aggregate="EvaluationComment", aggregate_id=item.id, payload={"evaluation_id": str(evaluation.id), "kind": kind})
    return item, evaluation


@transaction.atomic
def pin_question_page(*, tenant_id, actor_id, evaluation_id, evaluator, expected_version, lock_token, question, page_number):
    evaluation = Evaluation.objects.select_for_update().select_related("assignment__script").filter(id=evaluation_id, tenant_id=tenant_id, assignment__evaluator=evaluator).first()
    if not evaluation or evaluation.version != expected_version:
        raise HttpError(409, "Evaluation is missing or stale")
    verify_assignment_lock(assignment=evaluation.assignment, evaluator=evaluator, token=lock_token)
    if evaluation.status not in (Evaluation.Status.DRAFT, Evaluation.Status.REVIEW):
        raise HttpError(409, "Submitted page anchors are immutable")
    if not 1 <= page_number <= max(evaluation.assignment.script.page_count, 1):
        raise HttpError(422, "Question page is outside the script")
    anchor, _ = QuestionPageAnchor.objects.update_or_create(
        tenant_id=tenant_id,
        evaluation=evaluation,
        question=question,
        defaults={"page_number": page_number, "actor_id": actor_id},
    )
    evaluation.version += 1
    evaluation.save(update_fields=["version", "updated_at"])
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="marking.question_page_anchor.pinned", aggregate="QuestionPageAnchor", aggregate_id=anchor.id, payload={"evaluation_id": str(evaluation.id), "question": question.number, "page": page_number})
    return anchor, evaluation

def evaluation_checksum(evaluation):
    payload = {
        "scheme": evaluation.scheme.content_digest,
        "marks": [{"question": str(item.question_id), "sub": item.sub_question, "marks": str(item.marks), "outcome": item.outcome, "adjustment": item.adjustment, "sequence": item.sequence} for item in latest_marks(evaluation)],
        "annotations": [{"id": str(item.id), "page": item.page_number, "kind": item.kind, "geometry": item.geometry, "style": item.style} for item in effective_annotations(evaluation)],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_submission(evaluation):
    marks = latest_marks(evaluation)
    latest_questions = {item.question_id for item in marks if item.outcome != QuestionMark.Outcome.SKIPPED}
    missing = [question.number for question in evaluation.assignment.script.paper.questions.filter(required=True) if question.id not in latest_questions]
    if missing:
        raise HttpError(422, f"Mandatory questions are not evaluated: {', '.join(missing)}")
    if any(item.requires_attention and not item.examiner_confirmed for item in marks):
        raise HttpError(422, "Marks requiring attention must be explicitly confirmed")
    if evaluation.total_marks > evaluation.assignment.script.paper.max_marks and not any(item.adjustment == QuestionMark.Adjustment.BONUS for item in marks):
        raise HttpError(422, "Total marks exceed the paper maximum")
    return marks


@transaction.atomic
def transition_evaluation(*, tenant_id, actor_id, evaluation_id, evaluator, expected_version, lock_token, target):
    evaluation = Evaluation.objects.select_for_update().select_related("assignment").filter(id=evaluation_id, tenant_id=tenant_id, assignment__evaluator=evaluator).first()
    if not evaluation or evaluation.version != expected_version:
        raise HttpError(409, "Evaluation is missing or stale")
    verify_assignment_lock(assignment=evaluation.assignment, evaluator=evaluator, token=lock_token)
    if target not in EVALUATION_TRANSITIONS.get(evaluation.status, set()) or target == Evaluation.Status.SUBMITTED:
        raise HttpError(409, f"Evaluation transition from {evaluation.status} to {target} is not allowed here")
    previous = evaluation.status
    evaluation.status = target
    evaluation.version += 1
    evaluation.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="marking.evaluation.transitioned", aggregate="Evaluation", aggregate_id=evaluation.id, payload={"from": previous, "to": target})
    return evaluation
