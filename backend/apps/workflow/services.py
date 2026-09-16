import hashlib
import json

from django.db import transaction
from django.utils import timezone
from ninja.errors import HttpError

from apps.allocation.models import Assignment
from apps.assignment.models import AssignmentLock
from apps.core.idempotency import begin_idempotent, complete_idempotent
from apps.core.services import record_event
from apps.custody.models import Script
from apps.custody.services import transition_script
from apps.marking.models import Evaluation
from apps.marking.services import EVALUATION_TRANSITIONS, evaluation_checksum, validate_submission, verify_assignment_lock

from .models import DraftSnapshot, EvaluationExtension, EvaluationWorkflow, WorkflowTransition


WORKFLOW_TRANSITIONS = {
    EvaluationWorkflow.State.READY: {EvaluationWorkflow.State.DRAFT, EvaluationWorkflow.State.EXPIRED},
    EvaluationWorkflow.State.DRAFT: {EvaluationWorkflow.State.REVIEW, EvaluationWorkflow.State.SUBMITTED, EvaluationWorkflow.State.EXPIRED},
    EvaluationWorkflow.State.REVIEW: {EvaluationWorkflow.State.DRAFT, EvaluationWorkflow.State.SUBMITTED, EvaluationWorkflow.State.EXPIRED},
    EvaluationWorkflow.State.SUBMITTED: {EvaluationWorkflow.State.MODERATION, EvaluationWorkflow.State.REVALUATION, EvaluationWorkflow.State.FINALIZED},
    EvaluationWorkflow.State.MODERATION: {EvaluationWorkflow.State.FINALIZED},
    EvaluationWorkflow.State.REVALUATION: {EvaluationWorkflow.State.FINALIZED},
}


@transaction.atomic
def start_workflow(*, tenant_id, actor_id, assignment):
    workflow, created = EvaluationWorkflow.objects.get_or_create(tenant_id=tenant_id, assignment=assignment)
    if workflow.state == EvaluationWorkflow.State.READY:
        previous = workflow.state
        workflow.state = EvaluationWorkflow.State.DRAFT
        workflow.started_at = timezone.now()
        workflow.draft_expires_at = assignment.due_at
        workflow.version += 1
        workflow.save()
        WorkflowTransition.objects.create(tenant_id=tenant_id, workflow=workflow, from_state=previous, to_state=workflow.state, actor_id=actor_id)
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="workflow.evaluation.started", aggregate="EvaluationWorkflow", aggregate_id=workflow.id, payload={"assignment_id": str(assignment.id)})
    elif created:
        raise HttpError(409, "Workflow could not be initialized")
    return workflow


@transaction.atomic
def save_draft(*, tenant_id, actor_id, workflow_id, assignment, evaluator, expected_version, lock_token, client_sequence, last_question, last_page, ui_state):
    workflow = EvaluationWorkflow.objects.select_for_update().filter(id=workflow_id, tenant_id=tenant_id, assignment=assignment).first()
    if not workflow or workflow.version != expected_version:
        raise HttpError(409, "Evaluation workflow is missing or stale")
    current_assignment = Assignment.objects.select_for_update().get(id=assignment.id, tenant_id=tenant_id)
    verify_assignment_lock(assignment=current_assignment, evaluator=evaluator, token=lock_token)
    if workflow.state not in (EvaluationWorkflow.State.DRAFT, EvaluationWorkflow.State.REVIEW):
        raise HttpError(409, "This workflow no longer accepts drafts")
    if workflow.draft_expires_at and workflow.draft_expires_at <= timezone.now():
        raise HttpError(409, "Evaluation window has expired")
    allowed = {"zoom", "rotation", "active_tool", "sidebar", "fit", "scroll"}
    payload = {key: value for key, value in ui_state.items() if key in allowed}
    checksum = hashlib.sha256(json.dumps({"sequence": client_sequence, "question": str(last_question.id) if last_question else None, "page": last_page, "ui": payload}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if workflow.drafts.filter(client_sequence__gte=client_sequence).exists():
        raise HttpError(409, "Draft sequence must increase monotonically")
    draft = DraftSnapshot.objects.create(tenant_id=tenant_id, workflow=workflow, client_sequence=client_sequence, checksum=checksum, payload=payload, last_question=last_question, last_page=last_page, actor_id=actor_id)
    workflow.last_question = last_question
    workflow.last_page = last_page
    workflow.draft_checksum = checksum
    workflow.version += 1
    workflow.save()
    current_assignment.draft_saved_at = timezone.now()
    current_assignment.last_page = last_page
    current_assignment.version += 1
    current_assignment.save(update_fields=["draft_saved_at", "last_page", "version", "updated_at"])
    assignment.version = current_assignment.version
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="workflow.draft.saved", aggregate="DraftSnapshot", aggregate_id=draft.id, payload={"workflow_id": str(workflow.id), "sequence": client_sequence, "checksum": checksum})
    return draft, workflow


def submit_evaluation(*, tenant_id, actor_id, evaluation_id, evaluator, evaluation_version, workflow_version, lock_token, idempotency_key):
    with transaction.atomic():
        evaluation = Evaluation.objects.select_for_update().select_related("assignment__script__paper", "scheme").filter(id=evaluation_id, tenant_id=tenant_id, assignment__evaluator=evaluator).first()
        if not evaluation:
            raise HttpError(404, "Evaluation not found")
        payload = {"evaluation_id": str(evaluation.id), "evaluation_version": evaluation_version, "workflow_version": workflow_version}
        idempotency, existing = begin_idempotent(tenant_id=tenant_id, scope="evaluation.submit", key=idempotency_key, payload=payload)
        if existing:
            return Evaluation.objects.get(id=existing), True
        workflow = EvaluationWorkflow.objects.select_for_update().filter(assignment=evaluation.assignment, tenant_id=tenant_id).first()
        if evaluation.version != evaluation_version or not workflow or workflow.version != workflow_version:
            raise HttpError(409, "Evaluation or workflow is stale")
        verify_assignment_lock(assignment=evaluation.assignment, evaluator=evaluator, token=lock_token)
        if Evaluation.Status.SUBMITTED not in EVALUATION_TRANSITIONS.get(evaluation.status, set()) or EvaluationWorkflow.State.SUBMITTED not in WORKFLOW_TRANSITIONS.get(workflow.state, set()):
            raise HttpError(409, "Evaluation cannot be submitted from its current state")
        validate_submission(evaluation)
        checksum = evaluation_checksum(evaluation)
        now = timezone.now()
        evaluation.status = Evaluation.Status.SUBMITTED
        evaluation.submitted_at = now
        evaluation.checksum = checksum
        evaluation.version += 1
        evaluation.save()
        previous = workflow.state
        workflow.state = EvaluationWorkflow.State.SUBMITTED
        workflow.submitted_at = now
        workflow.version += 1
        workflow.save()
        WorkflowTransition.objects.create(tenant_id=tenant_id, workflow=workflow, from_state=previous, to_state=workflow.state, actor_id=actor_id, metadata={"evaluation_checksum": checksum})
        assignment = evaluation.assignment
        if assignment.status != Assignment.Status.IN_PROGRESS:
            raise HttpError(409, "Only an in-progress assignment can be submitted")
        assignment.status = Assignment.Status.SUBMITTED
        assignment.progress_percent = 100
        assignment.submitted_at = now
        assignment.version += 1
        assignment.save()
        script = assignment.script
        if script.state == Script.State.EVALUATING:
            transition_script(tenant_id=tenant_id, actor_id=actor_id, script_id=script.id, expected_version=script.version, to_state=Script.State.SUBMITTED, location="evaluation-submission", metadata={"evaluation_id": str(evaluation.id), "checksum": checksum})
        AssignmentLock.objects.filter(assignment=assignment, released_at__isnull=True).update(released_at=now, release_reason="evaluation submitted")
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="workflow.evaluation.submitted", aggregate="Evaluation", aggregate_id=evaluation.id, payload={"assignment_id": str(assignment.id), "checksum": checksum, "total_marks": str(evaluation.total_marks)})
        complete_idempotent(idempotency, evaluation.id)
    return evaluation, False


@transaction.atomic
def request_extension(*, tenant_id, actor_id, workflow, requested_until, reason):
    if requested_until <= workflow.draft_expires_at or len(reason.strip()) < 8:
        raise HttpError(422, "Extension must move the deadline forward and include a reason")
    item = EvaluationExtension.objects.create(tenant_id=tenant_id, workflow=workflow, requested_until=requested_until, reason=reason.strip(), requested_by_id=actor_id)
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="workflow.extension.requested", aggregate="EvaluationExtension", aggregate_id=item.id, payload={"workflow_id": str(workflow.id), "requested_until": requested_until.isoformat()})
    return item


@transaction.atomic
def decide_extension(*, tenant_id, actor_id, extension_id, expected_version, approve, note):
    item = EvaluationExtension.objects.select_for_update().select_related("workflow").filter(id=extension_id, tenant_id=tenant_id).first()
    if not item or item.version != expected_version or item.status != EvaluationExtension.Status.REQUESTED:
        raise HttpError(409, "Extension request is missing, stale, or already decided")
    if item.requested_by_id == actor_id:
        raise HttpError(409, "Requester cannot decide their own extension")
    item.status = EvaluationExtension.Status.APPROVED if approve else EvaluationExtension.Status.REJECTED
    item.decided_by_id = actor_id
    item.decision_note = note.strip()
    item.decided_at = timezone.now()
    item.version += 1
    item.save()
    if approve:
        workflow = item.workflow
        workflow.draft_expires_at = item.requested_until
        workflow.version += 1
        workflow.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action=f"workflow.extension.{item.status}", aggregate="EvaluationExtension", aggregate_id=item.id, payload={"workflow_id": str(item.workflow_id), "requested_until": item.requested_until.isoformat()})
    return item
