import uuid
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import models

from apps.core.models import TimeStampedModel


def default_session_expiry():
    from django.utils import timezone

    return timezone.now() + timedelta(minutes=30)


class AuthenticationMethod(TimeStampedModel):
    class Kind(models.TextChoices):
        TOTP = "totp", "Authenticator"
        EMAIL_OTP = "email_otp", "Email OTP"
        PASSKEY = "passkey", "Passkey"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="authentication_methods")
    kind = models.CharField(max_length=24, choices=Kind.choices)
    label = models.CharField(max_length=80)
    secret_ciphertext = models.TextField(blank=True)
    is_primary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "kind", "label"], name="unique_user_auth_method")]


class OtpChallenge(TimeStampedModel):
    class Purpose(models.TextChoices):
        LOGIN = "login", "Login"
        STEP_UP = "step_up", "Step-up"
        RECOVERY = "recovery", "Recovery"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="otp_challenges")
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField(db_index=True)
    attempts_remaining = models.PositiveSmallIntegerField(default=5)
    consumed_at = models.DateTimeField(null=True, blank=True)


class TrustedDevice(TimeStampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="trusted_devices")
    device_hash = models.CharField(max_length=64)
    label = models.CharField(max_length=100)
    platform = models.CharField(max_length=80, blank=True)
    browser = models.CharField(max_length=80, blank=True)
    public_key = models.TextField(blank=True)
    binding_signature = models.CharField(max_length=128, blank=True)
    trusted_until = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "device_hash"], name="unique_user_device")]


class PasskeyCredential(TimeStampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="passkey_credentials")
    credential_id = models.BinaryField(unique=True)
    public_key = models.BinaryField()
    sign_count = models.PositiveBigIntegerField(default=0)
    transports = models.JSONField(default=list)
    device_type = models.CharField(max_length=40, blank=True)
    backed_up = models.BooleanField(default=False)
    label = models.CharField(max_length=100)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)


class AccessSession(TimeStampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="access_sessions")
    tenant_id = models.UUIDField(db_index=True)
    session_key_hash = models.CharField(max_length=64, unique=True)
    device = models.ForeignKey(TrustedDevice, null=True, blank=True, on_delete=models.SET_NULL, related_name="sessions")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    location = models.CharField(max_length=120, blank=True)
    trusted_device = models.BooleanField(default=False)
    risk_score = models.PositiveSmallIntegerField(default=0)
    risk_reasons = models.JSONField(default=list)
    mfa_verified_at = models.DateTimeField(null=True, blank=True)
    step_up_expires_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(db_index=True, default=default_session_expiry)
    last_seen_at = models.DateTimeField(auto_now=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=120, blank=True)

    @property
    def is_step_up_valid(self):
        from django.utils import timezone

        return bool(self.step_up_expires_at and self.step_up_expires_at > timezone.now())


class LoginAttempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="login_attempts")
    identifier_hash = models.CharField(max_length=64, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    device_hash = models.CharField(max_length=64, blank=True)
    succeeded = models.BooleanField(default=False)
    challenged = models.BooleanField(default=False)
    risk_score = models.PositiveSmallIntegerField(default=0)
    risk_reasons = models.JSONField(default=list)
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)


class AuthenticationHistory(TimeStampedModel):
    class Outcome(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        CHALLENGED = "challenged", "Challenged"
        REVOKED = "revoked", "Revoked"

    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="auth_history")
    tenant_id = models.UUIDField(null=True, blank=True, db_index=True)
    event = models.CharField(max_length=80, db_index=True)
    outcome = models.CharField(max_length=20, choices=Outcome.choices)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    device_hash = models.CharField(max_length=64, blank=True)
    risk_score = models.PositiveSmallIntegerField(default=0)
    risk_reasons = models.JSONField(default=list)
    metadata = models.JSONField(default=dict)


class OidcProvider(TimeStampedModel):
    tenant_id = models.UUIDField(db_index=True)
    name = models.CharField(max_length=100)
    issuer = models.URLField()
    client_id = models.CharField(max_length=160)
    client_secret_ciphertext = models.TextField()
    scopes = models.CharField(max_length=255, default="openid email profile")
    domain_hint = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant_id", "name"], name="unique_tenant_oidc_provider")]
