from datetime import datetime

from ninja import Field, Schema


class DispatchCreateIn(Schema):
    reference: str
    paper_id: str
    source_centre: str
    expected_packets: int
    expected_scripts: int
    manifest_reference: str
    manifest_sha256: str = ""
    carrier: str = ""
    expected_arrival_at: datetime | None = None
    custom_fields: dict = Field(default_factory=dict)


class GuidedPacketIn(Schema):
    barcode: str
    paper_id: str
    script_barcodes: list[str]


class GuidedBundleIn(Schema):
    barcode: str
    source_centre: str
    mode: str
    packets: list[GuidedPacketIn]


class GuidedScanIn(Schema):
    barcode: str


class GuidedPacketScanIn(Schema):
    barcode: str
    bundle_barcode: str


class DispatchVerifyIn(Schema):
    version: int
    dispatched_at: datetime | None = None


class PacketCreateIn(Schema):
    barcode: str
    expected_scripts: int
    seal_number: str = ""


class PacketReceiveIn(Schema):
    version: int
    received_scripts: int
    condition: str = "intact"
    handed_over_by: str


class BundleCreateIn(Schema):
    barcode: str
    expected_scripts: int


class BundleReceiveIn(Schema):
    version: int
    received_scripts: int
    condition: str = "intact"


class VersionIn(Schema):
    version: int


class ConfirmationIn(Schema):
    confirmation_type: str
    notes: str = ""


class ExceptionCreateIn(Schema):
    dispatch_id: str
    kind: str
    packet_id: str | None = None
    bundle_id: str | None = None
    script_barcode: str = ""
    notes: str


class ExceptionReviewIn(Schema):
    version: int
    supervisor_note: str


class ExceptionReconcileIn(Schema):
    version: int
    reconciliation_note: str


class ExceptionClearIn(Schema):
    version: int
    clearance_note: str
