from django.db import models

from apps.core.models import TenantModel
from apps.repository.models import ScriptAsset


class IntegrityManifest(TenantModel):
    asset = models.OneToOneField(ScriptAsset, on_delete=models.PROTECT, related_name="integrity_manifest")
    file_sha256 = models.CharField(max_length=64)
    metadata_sha256 = models.CharField(max_length=64)
    custody_sha256 = models.CharField(max_length=64)
    version_sha256 = models.CharField(max_length=64)
    signature = models.TextField()
    public_key = models.TextField()
    signed_at = models.DateTimeField()
    last_verified_at = models.DateTimeField(null=True, blank=True)
    verification_status = models.CharField(max_length=16, default="pending")


class IntegrityCheck(TenantModel):
    class Status(models.TextChoices):
        PASSED = "passed", "Passed"
        FAILED = "failed", "Failed"
        MISSING = "missing", "Missing"

    manifest = models.ForeignKey(IntegrityManifest, on_delete=models.PROTECT, related_name="checks")
    status = models.CharField(max_length=16, choices=Status.choices)
    observed = models.JSONField(default=dict)
    checked_by = models.CharField(max_length=64, default="scheduled-verifier")
    checked_at = models.DateTimeField()

    class Meta:
        ordering = ["-checked_at"]


class IntegrityAlert(TenantModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"
        RESOLVED = "resolved", "Resolved"

    manifest = models.ForeignKey(IntegrityManifest, on_delete=models.PROTECT, related_name="alerts")
    kind = models.CharField(max_length=40)
    severity = models.CharField(max_length=16, default="critical")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    details = models.JSONField(default=dict)
    acknowledged_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    resolved_by_id = models.PositiveBigIntegerField(null=True, blank=True)


class IntegrityEvidence(TenantModel):
    alert = models.ForeignKey(IntegrityAlert, on_delete=models.PROTECT, related_name="evidence")
    evidence_type = models.CharField(max_length=40)
    storage_key = models.CharField(max_length=320, blank=True)
    snapshot = models.JSONField(default=dict)
    sha256 = models.CharField(max_length=64)
    preserved_at = models.DateTimeField()
