from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models

from apps.core.models import TenantModel, TimeStampedModel


class SecurityPolicy(TenantModel):
    class AIEvaluationMode(models.TextChoices):
        DISABLED = "disabled", "No AI"
        ASSISTIVE = "assistive", "AI assistance"
        AUTONOMOUS = "autonomous", "Autonomous AI"

    session_timeout_minutes = models.PositiveIntegerField(default=30)
    maximum_concurrent_sessions = models.PositiveSmallIntegerField(default=3)
    step_up_minutes = models.PositiveSmallIntegerField(default=10)
    failed_login_limit = models.PositiveSmallIntegerField(default=5)
    lockout_minutes = models.PositiveSmallIntegerField(default=15)
    require_mfa = models.BooleanField(default=False)
    require_trusted_device = models.BooleanField(default=False)
    approved_networks = models.JSONField(default=list, blank=True)
    allowed_countries = models.JSONField(default=list, blank=True)
    vpn_risk_threshold = models.PositiveSmallIntegerField(default=70)
    alert_risk_threshold = models.PositiveSmallIntegerField(default=50)
    dlp_enabled = models.BooleanField(default=True)
    evaluation_strict_mode = models.BooleanField(default=True)
    evaluation_identity_verification_required = models.BooleanField(default=True)
    evaluation_camera_required = models.BooleanField(default=True)
    evaluation_fullscreen_required = models.BooleanField(default=True)
    evaluation_single_screen_required = models.BooleanField(default=True)
    evaluation_mobile_allowed = models.BooleanField(default=False)
    evaluation_event_recording = models.BooleanField(default=True)
    evaluation_pause_on_violation = models.BooleanField(default=True)
    evaluation_require_resume_step_up = models.BooleanField(default=True)
    evaluation_allow_clipboard = models.BooleanField(default=False)
    evaluation_allow_download = models.BooleanField(default=False)
    evaluation_allow_print = models.BooleanField(default=False)
    evaluation_session_timeout_minutes = models.PositiveIntegerField(default=180)
    evaluation_heartbeat_seconds = models.PositiveSmallIntegerField(default=15)
    evaluation_no_face_seconds = models.PositiveSmallIntegerField(default=30)
    evaluation_retention_days = models.PositiveSmallIntegerField(default=30)
    ai_evaluation_mode = models.CharField(max_length=16, choices=AIEvaluationMode.choices, default=AIEvaluationMode.DISABLED)
    ai_confidence_threshold = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("85.00"))
    ai_model_name = models.CharField(max_length=80, default="admiezo-ai-v1")
    siem_webhook_ciphertext = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant_id"], name="unique_tenant_security_policy")]


class SecurityAlert(TenantModel):
    class Severity(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        INVESTIGATING = "investigating", "Investigating"
        RESOLVED = "resolved", "Resolved"

    category = models.CharField(max_length=60, db_index=True)
    severity = models.CharField(max_length=16, choices=Severity.choices)
    title = models.CharField(max_length=160)
    details = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    assigned_to = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    resolved_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)


class PrivilegedAccessRequest(TenantModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"
        REVOKED = "revoked", "Revoked"

    requester = models.ForeignKey(User, on_delete=models.PROTECT, related_name="privileged_access_requests")
    requested_role = models.CharField(max_length=64)
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    starts_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="privileged_access_decisions",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)


class EmergencyAccessGrant(TenantModel):
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="emergency_access_grants")
    role = models.CharField(max_length=64)
    incident_reference = models.CharField(max_length=100)
    justification = models.TextField()
    granted_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="emergency_access_issued")
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)


class DlpIncident(TenantModel):
    class Status(models.TextChoices):
        BLOCKED = "blocked", "Blocked"
        REVIEW = "review", "Under review"
        CLEARED = "cleared", "Cleared"

    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    channel = models.CharField(max_length=40)
    data_classification = models.CharField(max_length=60)
    rule = models.CharField(max_length=100)
    resource_reference = models.CharField(max_length=160, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.BLOCKED)
    details = models.JSONField(default=dict)


class EncryptionKeyMetadata(TimeStampedModel):
    class Purpose(models.TextChoices):
        APPLICATION = "application", "Application data"
        SCRIPT = "script", "Script assets"
        IDENTITY = "identity", "Identity data"

    purpose = models.CharField(max_length=20, choices=Purpose.choices, unique=True)
    provider = models.CharField(max_length=40, default="local-fernet")
    key_reference = models.CharField(max_length=160)
    rotated_at = models.DateTimeField()
    next_rotation_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)
