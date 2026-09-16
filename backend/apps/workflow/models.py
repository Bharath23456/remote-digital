from django.db import models

from apps.allocation.models import Assignment
from apps.configuration.models import Question
from apps.core.models import TenantModel


class EvaluationWorkflow(TenantModel):
    class State(models.TextChoices):
        READY = "ready", "Ready"
        DRAFT = "draft", "Draft"
        REVIEW = "review", "Review"
        SUBMITTED = "submitted", "Submitted"
        MODERATION = "moderation", "Moderation"
        REVALUATION = "revaluation", "Revaluation"
        FINALIZED = "finalized", "Finalized"
        EXPIRED = "expired", "Expired"

    assignment = models.OneToOneField(Assignment, on_delete=models.PROTECT, related_name="workflow")
    state = models.CharField(max_length=16, choices=State.choices, default=State.READY)
    last_question = models.ForeignKey(Question, null=True, blank=True, on_delete=models.PROTECT, related_name="workflow_positions")
    last_page = models.PositiveSmallIntegerField(default=1)
    draft_checksum = models.CharField(max_length=64, blank=True)
    draft_expires_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    finalized_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)


class WorkflowTransition(TenantModel):
    workflow = models.ForeignKey(EvaluationWorkflow, on_delete=models.PROTECT, related_name="transitions")
    from_state = models.CharField(max_length=16)
    to_state = models.CharField(max_length=16)
    actor_id = models.PositiveBigIntegerField()
    reason = models.TextField(blank=True)
    metadata = models.JSONField(default=dict)

    class Meta:
        ordering = ["created_at"]


class DraftSnapshot(TenantModel):
    workflow = models.ForeignKey(EvaluationWorkflow, on_delete=models.PROTECT, related_name="drafts")
    client_sequence = models.PositiveIntegerField()
    checksum = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    last_question = models.ForeignKey(Question, null=True, blank=True, on_delete=models.PROTECT, related_name="draft_positions")
    last_page = models.PositiveSmallIntegerField(default=1)
    actor_id = models.PositiveBigIntegerField()

    class Meta:
        ordering = ["-client_sequence"]
        constraints = [models.UniqueConstraint(fields=["workflow", "client_sequence"], name="unique_workflow_draft_sequence")]


class EvaluationExtension(TenantModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    workflow = models.ForeignKey(EvaluationWorkflow, on_delete=models.PROTECT, related_name="extensions")
    requested_until = models.DateTimeField()
    reason = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.REQUESTED)
    requested_by_id = models.PositiveBigIntegerField()
    decided_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
