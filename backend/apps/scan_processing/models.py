from django.db import models

from apps.core.models import TenantModel
from apps.custody.models import Script
from apps.scanning.models import ScanJob


class ProcessingProfile(TenantModel):
    code = models.CharField(max_length=48)
    name = models.CharField(max_length=120)
    configuration = models.JSONField(default=dict)
    version = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant_id", "code", "version"], name="unique_processing_profile_version")]


class ProcessingRun(TenantModel):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        QUALITY_REVIEW = "quality_review", "Quality review"
        PASSED = "passed", "Passed"
        FAILED = "failed", "Failed"
        RETURNED = "returned", "Returned"
        SKIPPED = "skipped", "Skipped"

    script = models.ForeignKey(Script, on_delete=models.PROTECT, related_name="processing_runs")
    scan_job = models.ForeignKey(ScanJob, on_delete=models.PROTECT, related_name="processing_runs")
    profile = models.ForeignKey(ProcessingProfile, on_delete=models.PROTECT, related_name="runs")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    current_stage = models.CharField(max_length=40, default="page_detection")
    source_digest = models.CharField(max_length=64)
    output_digest = models.CharField(max_length=64, blank=True)
    metrics = models.JSONField(default=dict)
    recognition_summary = models.JSONField(default=dict)
    skip_reason = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)


class ProcessedPage(TenantModel):
    run = models.ForeignKey(ProcessingRun, on_delete=models.PROTECT, related_name="pages")
    page_index = models.PositiveSmallIntegerField()
    recognized_page_number = models.PositiveSmallIntegerField(null=True, blank=True)
    source_key = models.CharField(max_length=320)
    output_key = models.CharField(max_length=320)
    source_sha256 = models.CharField(max_length=64)
    output_sha256 = models.CharField(max_length=64)
    recognition_artifact_key = models.CharField(max_length=320, blank=True)
    barcode = models.CharField(max_length=64, blank=True)
    qr_code = models.CharField(max_length=160, blank=True)
    is_blank = models.BooleanField(default=False)
    is_supplement = models.BooleanField(default=False)
    is_duplicate = models.BooleanField(default=False)
    is_wrong_page = models.BooleanField(default=False)
    quality_score = models.DecimalField(max_digits=5, decimal_places=2)
    resolution_dpi = models.PositiveSmallIntegerField()
    rotation_degrees = models.SmallIntegerField(default=0)
    processing_metadata = models.JSONField(default=dict)

    class Meta:
        ordering = ["page_index"]
        constraints = [models.UniqueConstraint(fields=["run", "page_index"], name="unique_processed_run_page")]


class ScanQualityException(TenantModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RETURNED_SCANNING = "returned_scanning", "Returned to scanning"
        RETURNED_VERIFICATION = "returned_verification", "Returned to verification"
        REPROCESSING = "reprocessing", "Reprocessing"
        RESOLVED = "resolved", "Resolved"
        SKIPPED = "skipped", "Skipped"

    run = models.ForeignKey(ProcessingRun, on_delete=models.PROTECT, related_name="exceptions")
    page = models.ForeignKey(ProcessedPage, null=True, blank=True, on_delete=models.PROTECT, related_name="exceptions")
    kind = models.CharField(max_length=40)
    severity = models.CharField(max_length=16, default="medium")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.OPEN)
    reason = models.TextField()
    resolution = models.TextField(blank=True)
    resolved_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
