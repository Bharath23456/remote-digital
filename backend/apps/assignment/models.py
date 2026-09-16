from django.db import models
from django.db.models import Q

from apps.allocation.models import Assignment
from apps.configuration.models import Paper
from apps.core.models import TenantModel
from apps.evaluators.models import Evaluator


class AssignmentGovernancePolicy(TenantModel):
    paper = models.OneToOneField(Paper, on_delete=models.PROTECT, related_name="assignment_governance")
    approval_required = models.BooleanField(default=True)
    assignment_expiry_hours = models.PositiveSmallIntegerField(default=120)
    lock_minutes = models.PositiveSmallIntegerField(default=30)
    require_step_up_for_reassignment = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)


class AssignmentApproval(TenantModel):
    class Decision(models.TextChoices):
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    assignment = models.ForeignKey(Assignment, on_delete=models.PROTECT, related_name="governance_approvals")
    decision = models.CharField(max_length=16, choices=Decision.choices)
    actor_id = models.PositiveBigIntegerField()
    note = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["assignment", "actor_id"], name="unique_assignment_approval_actor")]


class AssignmentLock(TenantModel):
    assignment = models.ForeignKey(Assignment, on_delete=models.PROTECT, related_name="access_locks")
    evaluator = models.ForeignKey(Evaluator, on_delete=models.PROTECT, related_name="assignment_locks")
    access_session = models.ForeignKey("identity_auth.AccessSession", null=True, blank=True, on_delete=models.SET_NULL, related_name="assignment_locks")
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField(db_index=True)
    released_at = models.DateTimeField(null=True, blank=True)
    release_reason = models.CharField(max_length=120, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["assignment"],
                condition=Q(released_at__isnull=True),
                name="one_active_assignment_lock",
            )
        ]


class SecureReassignmentRequest(TenantModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        EXECUTED = "executed", "Executed"
        EXPIRED = "expired", "Expired"

    assignment = models.ForeignKey(Assignment, on_delete=models.PROTECT, related_name="secure_reassignments")
    proposed_evaluator = models.ForeignKey(Evaluator, null=True, blank=True, on_delete=models.PROTECT, related_name="proposed_reassignments")
    reason = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.REQUESTED)
    requested_by_id = models.PositiveBigIntegerField()
    decided_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    executed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(db_index=True)
    version = models.PositiveIntegerField(default=1)
