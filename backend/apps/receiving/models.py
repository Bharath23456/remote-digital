from django.contrib.auth.models import User
from django.db import models

from apps.configuration.models import Paper
from apps.core.models import TenantModel
from apps.tenancy.models import Institution


class Dispatch(TenantModel):
    class IntakeMode(models.TextChoices):
        LEGACY = "legacy", "Legacy receiving"
        TRANSFER = "transfer", "Transfer to university"
        ON_SITE = "on_site", "Scan on site"

    class Status(models.TextChoices):
        REGISTERED = "registered", "Registered"
        IN_TRANSIT = "in_transit", "In transit"
        ON_SITE = "on_site", "On-site scanning"
        RECEIVED = "received", "Received"
        RECONCILED = "reconciled", "Reconciled"
        EXCEPTION = "exception", "Exception"
        CLOSED = "closed", "Closed"

    reference = models.CharField(max_length=40)
    intake_mode = models.CharField(max_length=16, choices=IntakeMode.choices, default=IntakeMode.LEGACY)
    paper = models.ForeignKey(Paper, on_delete=models.PROTECT, related_name="dispatches")
    source_centre = models.CharField(max_length=120)
    source_institution = models.ForeignKey(
        Institution,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="intake_dispatches",
    )
    prepared_centre_id = models.UUIDField(null=True, blank=True, db_index=True)
    received_centre_id = models.UUIDField(null=True, blank=True, db_index=True)
    expected_packets = models.PositiveIntegerField()
    expected_scripts = models.PositiveIntegerField()
    received_packets = models.PositiveIntegerField(default=0)
    received_scripts = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.REGISTERED)
    version = models.PositiveIntegerField(default=1)
    manifest_reference = models.CharField(max_length=80, blank=True)
    manifest_sha256 = models.CharField(max_length=64, blank=True)
    carrier = models.CharField(max_length=100, blank=True)
    registered_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="dispatches_registered")
    verified_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="dispatches_verified")
    verified_at = models.DateTimeField(null=True, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    expected_arrival_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant_id", "reference"], name="unique_dispatch_ref")]


class Packet(TenantModel):
    dispatch = models.ForeignKey(Dispatch, on_delete=models.PROTECT, related_name="packets")
    paper = models.ForeignKey(Paper, null=True, blank=True, on_delete=models.PROTECT, related_name="intake_packets")
    barcode = models.CharField(max_length=64, unique=True)
    script_manifest = models.JSONField(default=list, blank=True)
    expected_scripts = models.PositiveIntegerField()
    received_scripts = models.PositiveIntegerField(default=0)
    condition = models.CharField(max_length=20, default="intact")
    status = models.CharField(max_length=20, default="registered")
    seal_number = models.CharField(max_length=80, blank=True)
    handed_over_by = models.CharField(max_length=120, blank=True)
    received_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="packets_received")
    received_centre_id = models.UUIDField(null=True, blank=True, db_index=True)
    received_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)


class PreparedPacket(TenantModel):
    class Status(models.TextChoices):
        READY = "ready", "Ready for bundling"
        BUNDLED = "bundled", "Bundled"

    barcode = models.CharField(max_length=64, unique=True)
    paper = models.ForeignKey(Paper, on_delete=models.PROTECT, related_name="prepared_intake_packets")
    source_college = models.ForeignKey(Institution, on_delete=models.PROTECT, related_name="prepared_intake_packets")
    prepared_centre_id = models.UUIDField(null=True, blank=True, db_index=True)
    script_manifest = models.JSONField(default=list)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.READY)
    prepared_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="prepared_intake_packets")
    packet = models.OneToOneField(Packet, null=True, blank=True, on_delete=models.PROTECT, related_name="preparation")
    bundled_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)


class Bundle(TenantModel):
    packet = models.ForeignKey(Packet, on_delete=models.PROTECT, related_name="bundles")
    barcode = models.CharField(max_length=64, unique=True)
    expected_scripts = models.PositiveIntegerField()
    received_scripts = models.PositiveIntegerField(default=0)
    condition = models.CharField(max_length=20, default="intact")
    status = models.CharField(max_length=20, default="registered")
    received_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="bundles_received")
    received_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)


class ReceivingException(TenantModel):
    class Kind(models.TextChoices):
        DAMAGED = "damaged", "Damaged script"
        TORN = "torn", "Torn script"
        WET = "wet", "Wet script"
        LOOSE_PAGE = "loose_page", "Loose page"
        MISSING_SUPPLEMENT = "missing_supplement", "Missing supplement"
        UNIDENTIFIED = "unidentified", "Unidentified script"
        DISCREPANCY = "discrepancy", "Physical discrepancy"

    dispatch = models.ForeignKey(Dispatch, on_delete=models.PROTECT, related_name="exceptions")
    kind = models.CharField(max_length=28, choices=Kind.choices)
    packet = models.ForeignKey(Packet, null=True, blank=True, on_delete=models.PROTECT, related_name="exceptions")
    bundle = models.ForeignKey(Bundle, null=True, blank=True, on_delete=models.PROTECT, related_name="exceptions")
    script_barcode = models.CharField(max_length=64, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, default="open")
    supervisor_id = models.PositiveBigIntegerField(null=True, blank=True)
    supervisor_note = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reconciliation_note = models.TextField(blank=True)
    cleared_at = models.DateTimeField(null=True, blank=True)
    cleared_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)


class ReceiptConfirmation(TenantModel):
    dispatch = models.ForeignKey(Dispatch, on_delete=models.PROTECT, related_name="confirmations")
    confirmation_type = models.CharField(max_length=32)
    actor = models.ForeignKey(User, on_delete=models.PROTECT, related_name="receiving_confirmations")
    notes = models.TextField(blank=True)
    confirmed_at = models.DateTimeField(auto_now_add=True)


class ReceivingAlert(TenantModel):
    class Kind(models.TextChoices):
        TRANSIT_DELAY = "transit_delay", "Transit delay"
        UNRECEIVED_PACKET = "unreceived_packet", "Unreceived packet"
        COUNT_MISMATCH = "count_mismatch", "Count mismatch"

    dispatch = models.ForeignKey(Dispatch, on_delete=models.PROTECT, related_name="alerts")
    packet = models.ForeignKey(Packet, null=True, blank=True, on_delete=models.PROTECT, related_name="alerts")
    kind = models.CharField(max_length=28, choices=Kind.choices)
    message = models.CharField(max_length=240)
    detected_at = models.DateTimeField(auto_now_add=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["dispatch", "packet", "kind"],
                condition=models.Q(acknowledged_at__isnull=True),
                name="unique_open_receiving_alert",
            )
        ]
