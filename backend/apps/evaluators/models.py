from django.conf import settings
from django.db import models
from decimal import Decimal

from apps.configuration.models import Subject
from apps.core.models import TenantModel


class Evaluator(TenantModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending verification"
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        SUSPENDED = "suspended", "Suspended"
        RETIRED = "retired", "Retired"

    class Grade(models.TextChoices):
        EVALUATOR = "evaluator", "Evaluator"
        SENIOR = "senior", "Senior examiner"
        CHIEF = "chief", "Chief examiner"
        MODERATOR = "moderator", "Moderator"
        REVALUATOR = "revaluator", "Revaluator"
        SCRUTINIZER = "scrutinizer", "Scrutinizer"
        VERIFIER = "verifier", "Verifier"

    evaluator_code = models.CharField(max_length=32)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="evaluator_profiles")
    display_name = models.CharField(max_length=120)
    email = models.EmailField(blank=True)
    mobile = models.CharField(max_length=24, blank=True)
    employee_id = models.CharField(max_length=64, blank=True)
    institution_name = models.CharField(max_length=180)
    department = models.CharField(max_length=120)
    designation = models.CharField(max_length=120)
    qualification = models.CharField(max_length=180)
    employment_type = models.CharField(max_length=40, default="permanent")
    employment_details = models.JSONField(default=dict, blank=True)
    custom_fields = models.JSONField(default=dict, blank=True)
    years_experience = models.PositiveSmallIntegerField(default=0)
    grade = models.CharField(max_length=20, choices=Grade.choices, default=Grade.EVALUATOR)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    daily_capacity = models.PositiveSmallIntegerField(default=20)
    is_system_ai = models.BooleanField(default=False)
    available_from = models.DateField(null=True, blank=True)
    available_to = models.DateField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "evaluator_code"], name="unique_evaluator_code"),
            models.UniqueConstraint(fields=["tenant_id", "user"], condition=models.Q(user__isnull=False), name="unique_evaluator_login"),
        ]


class EvaluatorFaceTemplate(TenantModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        REVOKED = "revoked", "Revoked"

    evaluator = models.OneToOneField(Evaluator, on_delete=models.CASCADE, related_name="face_template")
    encrypted_template = models.TextField()
    template_digest = models.CharField(max_length=64, db_index=True)
    model_version = models.CharField(max_length=64, default="opencv-sface-v1")
    threshold = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.8200"))
    quality_score = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    liveness_reference = models.JSONField(default=dict, blank=True)
    enrolled_by_id = models.PositiveBigIntegerField()
    enrolled_at = models.DateTimeField(auto_now_add=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        indexes = [models.Index(fields=["tenant_id", "status"], name="evaluators__tenant__0dc1b4_idx")]


class EvaluatorIdentityVerification(TenantModel):
    evaluator = models.ForeignKey(Evaluator, on_delete=models.PROTECT, related_name="identity_verifications")
    assignment = models.ForeignKey("allocation.Assignment", null=True, blank=True, on_delete=models.PROTECT, related_name="identity_verifications")
    access_session_id = models.UUIDField(null=True, blank=True, db_index=True)
    verified = models.BooleanField(default=False)
    liveness_verified = models.BooleanField(default=False)
    authorized = models.BooleanField(default=False)
    access_granted = models.BooleanField(default=False)
    similarity_score = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.0000"))
    threshold = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.8200"))
    failure_reason = models.CharField(max_length=80, blank=True, db_index=True)
    model_version = models.CharField(max_length=64, default="opencv-sface-v1")
    probe_digest = models.CharField(max_length=64, blank=True)
    device_fingerprint = models.CharField(max_length=128, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    evidence = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    attempt_number = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant_id", "evaluator", "created_at"], name="evaluators__tenant__560cb4_idx"),
            models.Index(fields=["tenant_id", "access_session_id", "expires_at"], name="evaluators__tenant__0d32b2_idx"),
        ]


class Expertise(TenantModel):
    evaluator = models.ForeignKey(Evaluator, on_delete=models.CASCADE, related_name="expertise")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="evaluators")
    level = models.PositiveSmallIntegerField(default=1)
    years_experience = models.PositiveSmallIntegerField(default=0)
    verified = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["evaluator", "subject"], name="unique_evaluator_subject")]


class EvaluatorAvailability(TenantModel):
    evaluator = models.ForeignKey(Evaluator, on_delete=models.CASCADE, related_name="availability_periods")
    starts_on = models.DateField()
    ends_on = models.DateField()
    daily_capacity = models.PositiveSmallIntegerField()
    notes = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["starts_on"]


class EvaluatorHistory(TenantModel):
    evaluator = models.ForeignKey(Evaluator, on_delete=models.PROTECT, related_name="history")
    action = models.CharField(max_length=40)
    from_status = models.CharField(max_length=16, blank=True)
    to_status = models.CharField(max_length=16, blank=True)
    from_grade = models.CharField(max_length=20, blank=True)
    to_grade = models.CharField(max_length=20, blank=True)
    actor_id = models.PositiveBigIntegerField()
    snapshot = models.JSONField(default=dict)
    reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
