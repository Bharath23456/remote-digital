from datetime import timedelta

from django.db import models
from django.utils import timezone

from apps.configuration.models import Subject
from apps.core.models import TenantModel
from apps.evaluators.models import Evaluator


def default_document_expiry():
    return timezone.now() + timedelta(minutes=5)


class VerificationCase(TenantModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        IN_REVIEW = "in_review", "In review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"

    evaluator = models.OneToOneField(Evaluator, on_delete=models.PROTECT, related_name="verification")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    checks = models.JSONField(default=dict)
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewer_id = models.PositiveBigIntegerField(null=True, blank=True)
    submitted_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    revalidation_due_on = models.DateField(null=True, blank=True)
    required_approvals = models.PositiveSmallIntegerField(default=2)
    fraud_signals = models.JSONField(default=list, blank=True)
    version = models.PositiveIntegerField(default=1)
    notes = models.TextField(blank=True)


class VerificationDocument(TenantModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending upload"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    verification = models.ForeignKey(VerificationCase, on_delete=models.PROTECT, related_name="documents")
    kind = models.CharField(max_length=40)
    storage_key = models.CharField(max_length=320, unique=True)
    sha256 = models.CharField(max_length=64, blank=True)
    mime_type = models.CharField(max_length=100, default="application/pdf")
    byte_size = models.PositiveIntegerField(default=0)
    maximum_bytes = models.PositiveIntegerField(default=10_000_000)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    expires_at = models.DateTimeField(default=default_document_expiry)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)


class VerificationApproval(TenantModel):
    class Decision(models.TextChoices):
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    verification = models.ForeignKey(VerificationCase, on_delete=models.PROTECT, related_name="approvals")
    level = models.PositiveSmallIntegerField()
    approver_id = models.PositiveBigIntegerField()
    decision = models.CharField(max_length=16, choices=Decision.choices)
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["verification", "approver_id"], name="unique_verification_approver"),
            models.UniqueConstraint(fields=["verification", "level"], name="unique_verification_approval_level"),
        ]


class EligibilityRecord(TenantModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ELIGIBLE = "eligible", "Eligible"
        INELIGIBLE = "ineligible", "Ineligible"
        EXPIRED = "expired", "Expired"

    evaluator = models.ForeignKey(Evaluator, on_delete=models.PROTECT, related_name="eligibility_records")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="eligibility_records")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    qualification_ok = models.BooleanField(default=False)
    experience_ok = models.BooleanField(default=False)
    institution_ok = models.BooleanField(default=False)
    expertise_ok = models.BooleanField(default=False)
    has_conflict = models.BooleanField(default=False)
    is_debarred = models.BooleanField(default=False)
    is_blacklisted = models.BooleanField(default=False)
    risk_reasons = models.JSONField(default=list)
    valid_from = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    approved_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["evaluator", "subject"], name="unique_evaluator_subject_eligibility")]


class EligibilityHistory(TenantModel):
    eligibility = models.ForeignKey(EligibilityRecord, on_delete=models.PROTECT, related_name="history")
    action = models.CharField(max_length=40)
    actor_id = models.PositiveBigIntegerField()
    snapshot = models.JSONField(default=dict)
    reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
