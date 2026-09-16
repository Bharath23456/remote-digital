from django.db import models
from django.utils import timezone

from apps.core.models import TenantModel


class AcademicYear(TenantModel):
    label = models.CharField(max_length=20)
    starts_on = models.DateField()
    ends_on = models.DateField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant_id", "label"], name="unique_tenant_year")]


class Regulation(TenantModel):
    code = models.CharField(max_length=40)
    title = models.CharField(max_length=180)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant_id", "code"], name="unique_tenant_regulation")]


class Term(TenantModel):
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="terms")
    name = models.CharField(max_length=80)
    sequence = models.PositiveSmallIntegerField()
    starts_on = models.DateField()
    ends_on = models.DateField()

    class Meta:
        ordering = ["academic_year", "sequence"]
        constraints = [models.UniqueConstraint(fields=["academic_year", "sequence"], name="unique_year_term_sequence")]


class ExamSession(TenantModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVAL = "approval", "Awaiting approval"
        READY = "ready", "Ready"
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"

    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="sessions")
    name = models.CharField(max_length=120)
    term = models.CharField(max_length=60)
    evaluation_starts_at = models.DateTimeField()
    evaluation_ends_at = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    version = models.PositiveIntegerField(default=1)


class EvaluationEvent(TenantModel):
    session = models.ForeignKey(ExamSession, on_delete=models.PROTECT, related_name="events")
    name = models.CharField(max_length=140)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    evaluation_centre_ids = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)


class Programme(TenantModel):
    code = models.CharField(max_length=24)
    name = models.CharField(max_length=160)
    regulation = models.CharField(max_length=80)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant_id", "code"], name="unique_tenant_programme")]


class Course(TenantModel):
    programme = models.ForeignKey(Programme, on_delete=models.PROTECT, related_name="courses")
    regulation = models.ForeignKey(Regulation, on_delete=models.PROTECT, related_name="courses")
    code = models.CharField(max_length=32)
    name = models.CharField(max_length=180)
    duration_terms = models.PositiveSmallIntegerField()
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant_id", "code"], name="unique_tenant_course")]


class Subject(TenantModel):
    programme = models.ForeignKey(Programme, on_delete=models.PROTECT, related_name="subjects")
    course = models.ForeignKey(Course, null=True, blank=True, on_delete=models.PROTECT, related_name="subjects")
    code = models.CharField(max_length=24)
    name = models.CharField(max_length=180)
    semester = models.PositiveSmallIntegerField()
    session_ids = models.JSONField(default=list, blank=True)
    related_subject_ids = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant_id", "code"], name="unique_tenant_subject")]


class Paper(TenantModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        REVIEW = "review", "In review"
        APPROVED = "approved", "Approved"
        FROZEN = "frozen", "Frozen"

    session = models.ForeignKey(ExamSession, on_delete=models.PROTECT, related_name="papers")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="papers")
    code = models.CharField(max_length=32)
    title = models.CharField(max_length=180)
    max_marks = models.DecimalField(max_digits=7, decimal_places=2)
    pass_marks = models.DecimalField(max_digits=7, decimal_places=2)
    valuation_rounds = models.PositiveSmallIntegerField(default=1)
    discrepancy_threshold = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    moderation_required = models.BooleanField(default=False)
    rules = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    version = models.PositiveIntegerField(default=1)
    approved_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    frozen_at = models.DateTimeField(null=True, blank=True)
    effective_from = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant_id", "session", "code"], name="unique_session_paper")]


class Question(TenantModel):
    paper = models.ForeignKey(Paper, on_delete=models.CASCADE, related_name="questions")
    number = models.CharField(max_length=16)
    max_marks = models.DecimalField(max_digits=7, decimal_places=2)
    required = models.BooleanField(default=True)
    position = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["position"]
        constraints = [models.UniqueConstraint(fields=["paper", "number"], name="unique_paper_question")]


class EvaluationCentre(TenantModel):
    code = models.CharField(max_length=32)
    name = models.CharField(max_length=180)
    address = models.TextField(blank=True)
    network_cidrs = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant_id", "code"], name="unique_tenant_centre")]


class ConfigurationRevision(TenantModel):
    class ChangeType(models.TextChoices):
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        APPROVE = "approve", "Approve"
        FREEZE = "freeze", "Freeze"
        ROLLBACK = "rollback", "Rollback"
        EMERGENCY = "emergency", "Emergency change"

    aggregate_type = models.CharField(max_length=80)
    aggregate_id = models.UUIDField()
    version = models.PositiveIntegerField()
    change_type = models.CharField(max_length=16, choices=ChangeType.choices)
    snapshot = models.JSONField()
    reason = models.TextField(blank=True)
    actor_id = models.PositiveBigIntegerField()
    effective_from = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["tenant_id", "aggregate_type", "aggregate_id", "version"], name="unique_config_revision")]


class ConfigurationApproval(TenantModel):
    class Decision(models.TextChoices):
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    paper = models.ForeignKey(Paper, on_delete=models.PROTECT, related_name="approvals")
    stage = models.PositiveSmallIntegerField()
    decision = models.CharField(max_length=16, choices=Decision.choices)
    actor_id = models.PositiveBigIntegerField()
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [models.UniqueConstraint(fields=["paper", "stage", "actor_id"], name="unique_paper_approval_actor")]


class ConfigurationChangeRequest(TenantModel):
    class Kind(models.TextChoices):
        EMERGENCY_UPDATE = "emergency_update", "Emergency update"
        ROLLBACK = "rollback", "Rollback"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending approval"
        APPLIED = "applied", "Applied"
        REJECTED = "rejected", "Rejected"

    paper = models.ForeignKey(Paper, on_delete=models.PROTECT, related_name="change_requests")
    kind = models.CharField(max_length=24, choices=Kind.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    base_version = models.PositiveIntegerField()
    target_revision_version = models.PositiveIntegerField(null=True, blank=True)
    proposed_changes = models.JSONField(default=dict, blank=True)
    impact = models.JSONField(default=dict)
    reason = models.TextField()
    submitted_by_id = models.PositiveBigIntegerField()
    required_approvals = models.PositiveSmallIntegerField(default=2)
    version = models.PositiveIntegerField(default=1)
    applied_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class ConfigurationChangeApproval(TenantModel):
    class Decision(models.TextChoices):
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    change_request = models.ForeignKey(ConfigurationChangeRequest, on_delete=models.PROTECT, related_name="approvals")
    actor_id = models.PositiveBigIntegerField()
    decision = models.CharField(max_length=16, choices=Decision.choices)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [models.UniqueConstraint(fields=["change_request", "actor_id"], name="unique_config_change_approver")]
