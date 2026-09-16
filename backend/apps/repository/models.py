from django.db import models

from apps.core.models import TenantModel
from apps.custody.models import Script


class ScriptAsset(TenantModel):
    class Kind(models.TextChoices):
        MASTER = "master", "Immutable master"
        EVALUATION = "evaluation", "Evaluation copy"
        THUMBNAIL = "thumbnail", "Thumbnail"

    script = models.ForeignKey(Script, on_delete=models.PROTECT, related_name="assets")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    page_number = models.PositiveSmallIntegerField()
    storage_key = models.CharField(max_length=320, unique=True)
    sha256 = models.CharField(max_length=64)
    byte_size = models.PositiveBigIntegerField()
    mime_type = models.CharField(max_length=64, default="image/webp")
    version = models.PositiveSmallIntegerField(default=1)
    retention_until = models.DateField(null=True, blank=True)
    legal_hold = models.BooleanField(default=False)
    integrity_checked_at = models.DateTimeField(null=True, blank=True)
    object_lock_until = models.DateField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deletion_reason = models.TextField(blank=True)
    backup_status = models.CharField(max_length=20, default="pending")
    replication_status = models.CharField(max_length=20, default="pending")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["script", "kind", "page_number", "version"], name="unique_asset_version")]


class UploadIntent(TenantModel):
    class Kind(models.TextChoices):
        RAW_SCAN = "raw_scan", "Raw scan"
        MASTER = "master", "Immutable master"
        EVALUATION = "evaluation", "Evaluation copy"
        THUMBNAIL = "thumbnail", "Thumbnail"

    class Status(models.TextChoices):
        ISSUED = "issued", "Issued"
        COMPLETED = "completed", "Completed"
        EXPIRED = "expired", "Expired"

    script = models.ForeignKey(Script, on_delete=models.PROTECT, related_name="upload_intents")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    page_number = models.PositiveSmallIntegerField()
    asset_version = models.PositiveSmallIntegerField(default=1)
    storage_key = models.CharField(max_length=320, unique=True)
    content_type = models.CharField(max_length=64)
    maximum_bytes = models.PositiveBigIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ISSUED)
    expires_at = models.DateTimeField(db_index=True)
    sha256 = models.CharField(max_length=64, blank=True)
    byte_size = models.PositiveBigIntegerField(default=0)
    finalized_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["script", "kind", "page_number", "asset_version"],
                condition=models.Q(status="completed"),
                name="one_completed_upload_per_script_kind_page",
            )
        ]
