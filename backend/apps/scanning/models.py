from django.db import models

from apps.configuration.models import Paper
from apps.core.models import TenantModel
from apps.custody.models import Script


class ScannerDevice(TenantModel):
    class Topology(models.TextChoices):
        CENTRAL = "central", "Central"
        DISTRIBUTED = "distributed", "Distributed"

    class Status(models.TextChoices):
        ONLINE = "online", "Online"
        DEGRADED = "degraded", "Degraded"
        OFFLINE = "offline", "Offline"
        MAINTENANCE = "maintenance", "Maintenance"

    code = models.CharField(max_length=40)
    name = models.CharField(max_length=120)
    location = models.CharField(max_length=160)
    topology = models.CharField(max_length=16, choices=Topology.choices, default=Topology.CENTRAL)
    capabilities = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OFFLINE)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True, db_index=True)
    pages_per_minute = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    pages_scanned_today = models.PositiveIntegerField(default=0)
    failure_rate = models.DecimalField(max_digits=6, decimal_places=3, default=0)
    firmware_version = models.CharField(max_length=80, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant_id", "code"], name="unique_tenant_scanner_code")]


class ScanBatch(TenantModel):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        ASSIGNED = "assigned", "Assigned"
        SCANNING = "scanning", "Scanning"
        PAUSED = "paused", "Paused"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    reference = models.CharField(max_length=64)
    paper = models.ForeignKey(Paper, on_delete=models.PROTECT, related_name="scan_batches")
    scanner = models.ForeignKey(ScannerDevice, null=True, blank=True, on_delete=models.PROTECT, related_name="batches")
    failover_from = models.ForeignKey(ScannerDevice, null=True, blank=True, on_delete=models.PROTECT, related_name="failed_over_batches")
    requested_topology = models.CharField(max_length=16, choices=ScannerDevice.Topology.choices)
    duplex = models.BooleanField(default=True)
    adf = models.BooleanField(default=True)
    priority = models.PositiveSmallIntegerField(default=3)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    operator_id = models.PositiveBigIntegerField(null=True, blank=True)
    expected_scripts = models.PositiveIntegerField(default=0)
    completed_scripts = models.PositiveIntegerField(default=0)
    failed_scripts = models.PositiveIntegerField(default=0)
    assigned_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant_id", "reference"], name="unique_tenant_scan_batch")]


class ScanJob(TenantModel):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SCANNING = "scanning", "Scanning"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        RESCAN = "rescan", "Re-scan required"
        CANCELLED = "cancelled", "Cancelled"

    batch = models.ForeignKey(ScanBatch, on_delete=models.PROTECT, related_name="jobs")
    script = models.ForeignKey(Script, on_delete=models.PROTECT, related_name="scan_jobs")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    expected_pages = models.PositiveSmallIntegerField(default=0)
    scanned_pages = models.PositiveSmallIntegerField(default=0)
    attempt = models.PositiveSmallIntegerField(default=1)
    source_manifest = models.JSONField(default=list)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["batch", "script"], name="unique_batch_script_scan")]


class ScannerErrorLog(TenantModel):
    scanner = models.ForeignKey(ScannerDevice, on_delete=models.PROTECT, related_name="errors")
    batch = models.ForeignKey(ScanBatch, null=True, blank=True, on_delete=models.PROTECT, related_name="errors")
    job = models.ForeignKey(ScanJob, null=True, blank=True, on_delete=models.PROTECT, related_name="errors")
    code = models.CharField(max_length=64)
    message = models.TextField()
    recoverable = models.BooleanField(default=True)
    telemetry = models.JSONField(default=dict)


class ScannerMaintenanceAlert(TenantModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"
        RESOLVED = "resolved", "Resolved"

    scanner = models.ForeignKey(ScannerDevice, on_delete=models.PROTECT, related_name="maintenance_alerts")
    severity = models.CharField(max_length=16, default="medium")
    reason = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    due_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    resolved_by_id = models.PositiveBigIntegerField(null=True, blank=True)
