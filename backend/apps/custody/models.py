from django.db import models

from apps.configuration.models import Paper
from apps.core.models import TenantModel
from apps.receiving.models import Packet


class Script(TenantModel):
    class State(models.TextChoices):
        RECEIVED = "received", "Received"
        REGISTERED = "registered", "Registered"
        SCANNED = "scanned", "Scanned"
        VALIDATED = "validated", "Validated"
        MASKED = "masked", "Masked"
        STORED = "stored", "Stored"
        ASSIGNED = "assigned", "Assigned"
        OPENED = "opened", "Opened"
        EVALUATING = "evaluating", "Evaluation started"
        SUBMITTED = "submitted", "Evaluation submitted"
        REVIEW = "review", "Under review"
        MODERATED = "moderated", "Moderated"
        REVALUATED = "revaluated", "Revaluated"
        FINALIZED = "finalized", "Finalized"
        ARCHIVED = "archived", "Archived"
        EXCEPTION = "exception", "Exception"

    script_code = models.CharField(max_length=48, unique=True)
    primary_barcode = models.CharField(max_length=64, unique=True)
    recognized_cover_sha256 = models.CharField(max_length=64, blank=True)
    packet = models.ForeignKey(Packet, on_delete=models.PROTECT, related_name="scripts")
    paper = models.ForeignKey(Paper, on_delete=models.PROTECT, related_name="scripts")
    supplement_barcodes = models.JSONField(default=list, blank=True)
    page_count = models.PositiveSmallIntegerField(default=0)
    state = models.CharField(max_length=20, choices=State.choices, default=State.RECEIVED)
    version = models.PositiveIntegerField(default=1)
    bundle_barcode = models.CharField(max_length=64, blank=True)
    centre_barcode = models.CharField(max_length=64, blank=True)
    last_location = models.CharField(max_length=120, blank=True)
    removed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    removed_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    removal_reason = models.TextField(blank=True)


class CustodyEvent(TenantModel):
    script = models.ForeignKey(Script, on_delete=models.PROTECT, related_name="custody_events")
    from_state = models.CharField(max_length=20)
    to_state = models.CharField(max_length=20)
    location = models.CharField(max_length=120)
    actor_id = models.CharField(max_length=64)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]


class BarcodeRecord(TenantModel):
    class Kind(models.TextChoices):
        SCRIPT = "script", "Script"
        SUPPLEMENT = "supplement", "Supplement"
        BUNDLE = "bundle", "Bundle"
        PACKET = "packet", "Packet"
        CENTRE = "centre", "Centre"

    code = models.CharField(max_length=64, unique=True)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    script = models.ForeignKey(Script, null=True, blank=True, on_delete=models.PROTECT, related_name="barcodes")
    packet = models.ForeignKey(Packet, null=True, blank=True, on_delete=models.PROTECT, related_name="custody_barcodes")
    is_active = models.BooleanField(default=True)
    registered_by_id = models.PositiveBigIntegerField()


class BarcodeScan(TenantModel):
    barcode = models.CharField(max_length=64, db_index=True)
    script = models.ForeignKey(Script, null=True, blank=True, on_delete=models.PROTECT, related_name="scan_events")
    purpose = models.CharField(max_length=32)
    location = models.CharField(max_length=120)
    actor_id = models.PositiveBigIntegerField()
    result = models.CharField(max_length=24)
    details = models.JSONField(default=dict)

    class Meta:
        ordering = ["-created_at"]


class CustodyTransfer(TenantModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        AUTHORIZED = "authorized", "Authorized"
        IN_TRANSIT = "in_transit", "In transit"
        RECEIVED = "received", "Received"
        REJECTED = "rejected", "Rejected"

    script = models.ForeignKey(Script, on_delete=models.PROTECT, related_name="transfers")
    from_location = models.CharField(max_length=120)
    to_location = models.CharField(max_length=120)
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED)
    requested_by_id = models.PositiveBigIntegerField()
    authorized_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    received_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    authorized_at = models.DateTimeField(null=True, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)


class ReconciliationRun(TenantModel):
    packet = models.ForeignKey(Packet, on_delete=models.PROTECT, related_name="reconciliations")
    expected_scripts = models.PositiveIntegerField()
    observed_scripts = models.PositiveIntegerField()
    missing_barcodes = models.JSONField(default=list)
    excess_barcodes = models.JSONField(default=list)
    mismatched_barcodes = models.JSONField(default=list)
    duplicate_barcodes = models.JSONField(default=list)
    status = models.CharField(max_length=20)
    performed_by_id = models.PositiveBigIntegerField()
