import hashlib
import json

from django.db import transaction
from django.utils import timezone
from ninja.errors import HttpError

from apps.core.services import record_event

from .models import InstructionAcknowledgement, MarkingScheme, RubricCriterion, SchemeClarification, SchemeHistory


SCHEME_TRANSITIONS = {
    MarkingScheme.Status.DRAFT: {MarkingScheme.Status.REVIEW},
    MarkingScheme.Status.REVIEW: {MarkingScheme.Status.DRAFT, MarkingScheme.Status.APPROVED},
    MarkingScheme.Status.APPROVED: {MarkingScheme.Status.FROZEN},
    MarkingScheme.Status.FROZEN: {MarkingScheme.Status.SUPERSEDED},
}


def scheme_snapshot(scheme):
    return {
        "paper": str(scheme.paper_id),
        "version": scheme.version,
        "title": scheme.title,
        "evaluation_guidelines": scheme.evaluation_guidelines,
        "examiner_instructions": scheme.examiner_instructions,
        "model_answer_key": scheme.model_answer_key,
        "suggested_answer_key": scheme.suggested_answer_key,
        "subject_rules": scheme.subject_rules,
        "regulation_rules": scheme.regulation_rules,
        "partial_credit_rules": scheme.partial_credit_rules,
        "tolerance_rules": scheme.tolerance_rules,
        "special_rules": scheme.special_rules,
        "criteria": [
            {
                "question": str(item.question_id),
                "code": item.code,
                "description": item.description,
                "max_marks": str(item.max_marks),
                "step_marks": item.step_marks,
                "partial_credit_rule": item.partial_credit_rule,
                "tolerance": str(item.tolerance),
                "mandatory": item.is_mandatory,
            }
            for item in scheme.criteria.select_related("question").order_by("question__position", "position")
        ],
    }


def content_digest(scheme):
    return hashlib.sha256(json.dumps(scheme_snapshot(scheme), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@transaction.atomic
def create_scheme(*, tenant_id, actor_id, paper, values):
    latest = MarkingScheme.objects.filter(tenant_id=tenant_id, paper=paper).order_by("-version").first()
    if latest and latest.status != MarkingScheme.Status.SUPERSEDED:
        raise HttpError(409, "The current scheme must be superseded before creating a new version")
    scheme = MarkingScheme.objects.create(
        tenant_id=tenant_id,
        paper=paper,
        version=(latest.version + 1) if latest else 1,
        created_by_id=actor_id,
        **values,
    )
    SchemeHistory.objects.create(tenant_id=tenant_id, scheme=scheme, action="created", actor_id=actor_id, snapshot=scheme_snapshot(scheme))
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="rubric.scheme.created", aggregate="MarkingScheme", aggregate_id=scheme.id, payload={"paper_id": str(paper.id), "version": scheme.version})
    return scheme


@transaction.atomic
def add_criterion(*, tenant_id, actor_id, scheme, question, values):
    if scheme.status != MarkingScheme.Status.DRAFT:
        raise HttpError(409, "Rubric criteria can only change while the scheme is in draft")
    if question.paper_id != scheme.paper_id:
        raise HttpError(422, "Question does not belong to the scheme paper")
    criterion = RubricCriterion.objects.create(tenant_id=tenant_id, scheme=scheme, question=question, **values)
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="rubric.criterion.created", aggregate="RubricCriterion", aggregate_id=criterion.id, payload={"scheme_id": str(scheme.id), "question": question.number, "code": criterion.code})
    return criterion


def transition_scheme(*, tenant_id, actor_id, scheme_id, target, reason):
    with transaction.atomic():
        scheme = MarkingScheme.objects.select_for_update().select_related("paper").filter(id=scheme_id, tenant_id=tenant_id).first()
        if not scheme:
            raise HttpError(404, "Marking scheme not found")
        if target not in SCHEME_TRANSITIONS.get(scheme.status, set()):
            raise HttpError(409, f"Scheme transition from {scheme.status} to {target} is not allowed")
        if target == MarkingScheme.Status.REVIEW:
            if not scheme.criteria.exists():
                raise HttpError(409, "At least one rubric criterion is required")
            for question in scheme.paper.questions.all():
                allocated = sum(item.max_marks for item in scheme.criteria.filter(question=question))
                if allocated != question.max_marks:
                    raise HttpError(422, f"Rubric criteria for question {question.number} must total {question.max_marks}")
        if target == MarkingScheme.Status.APPROVED and scheme.created_by_id == actor_id:
            raise HttpError(409, "Scheme creator cannot approve the same version")
        previous = scheme.status
        scheme.status = target
        if target == MarkingScheme.Status.APPROVED:
            scheme.approved_by_id = actor_id
            scheme.approved_at = timezone.now()
        if target == MarkingScheme.Status.FROZEN:
            scheme.content_digest = content_digest(scheme)
            scheme.frozen_by_id = actor_id
            scheme.frozen_at = timezone.now()
        scheme.save()
        SchemeHistory.objects.create(tenant_id=tenant_id, scheme=scheme, action=target, actor_id=actor_id, reason=reason.strip(), snapshot=scheme_snapshot(scheme))
        record_event(tenant_id=tenant_id, actor_id=actor_id, action=f"rubric.scheme.{target}", aggregate="MarkingScheme", aggregate_id=scheme.id, payload={"from": previous, "reason": reason.strip()})
    return scheme


@transaction.atomic
def publish_clarification(*, tenant_id, actor_id, scheme, question, scope, title, body, mandatory_acknowledgement, supersedes=None):
    if scheme.status not in (MarkingScheme.Status.APPROVED, MarkingScheme.Status.FROZEN):
        raise HttpError(409, "Clarifications require an approved scheme")
    if scope not in SchemeClarification.Scope.values or (question and question.paper_id != scheme.paper_id):
        raise HttpError(422, "Invalid clarification scope or question")
    if supersedes and supersedes.scheme_id != scheme.id:
        raise HttpError(422, "A clarification can only supersede an item in the same scheme")
    item = SchemeClarification.objects.create(
        tenant_id=tenant_id,
        scheme=scheme,
        question=question,
        scope=scope,
        title=title.strip(),
        body=body.strip(),
        version=(supersedes.version + 1) if supersedes else 1,
        mandatory_acknowledgement=mandatory_acknowledgement,
        published_by_id=actor_id,
        published_at=timezone.now(),
        supersedes=supersedes,
    )
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="rubric.clarification.published", aggregate="SchemeClarification", aggregate_id=item.id, payload={"scheme_id": str(scheme.id), "scope": scope, "version": item.version})
    return item


@transaction.atomic
def acknowledge(*, tenant_id, actor_id, scheme, evaluator, clarification=None):
    if clarification and clarification.scheme_id != scheme.id:
        raise HttpError(422, "Clarification does not belong to this scheme")
    digest = hashlib.sha256(
        (scheme.content_digest + (f"{clarification.id}:{clarification.version}:{clarification.body}" if clarification else "base")).encode()
    ).hexdigest()
    item, created = InstructionAcknowledgement.objects.get_or_create(
        tenant_id=tenant_id,
        scheme=scheme,
        evaluator=evaluator,
        clarification=clarification,
        defaults={"content_digest": digest, "acknowledged_at": timezone.now()},
    )
    if not created:
        raise HttpError(409, "These instructions were already acknowledged")
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="rubric.instructions.acknowledged", aggregate="InstructionAcknowledgement", aggregate_id=item.id, payload={"scheme_id": str(scheme.id), "clarification_id": str(clarification.id) if clarification else None})
    return item


def evaluator_has_required_acknowledgements(scheme, evaluator):
    if not InstructionAcknowledgement.objects.filter(scheme=scheme, evaluator=evaluator, clarification__isnull=True).exists():
        return False
    required = scheme.clarifications.filter(mandatory_acknowledgement=True).values_list("id", flat=True)
    acknowledged = set(InstructionAcknowledgement.objects.filter(scheme=scheme, evaluator=evaluator, clarification_id__in=required).values_list("clarification_id", flat=True))
    return all(item in acknowledged for item in required)
