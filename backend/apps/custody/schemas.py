from ninja import Field, Schema


class ScriptRegisterIn(Schema):
    packet_id: str
    primary_barcode: str
    supplement_barcodes: list[str] = []
    bundle_barcode: str = ""
    centre_barcode: str = ""
    location: str
    custom_fields: dict = Field(default_factory=dict)


class ScriptTransitionIn(Schema):
    version: int
    to_state: str
    location: str
    metadata: dict = {}


class BarcodeScanIn(Schema):
    barcode: str
    purpose: str
    location: str


class ReconcilePacketIn(Schema):
    manifest_barcodes: list[str]
    observed_barcodes: list[str]


class TransferRequestIn(Schema):
    script_id: str
    to_location: str
    reason: str


class TransferDecisionIn(Schema):
    version: int
    authorize: bool
    rejection_reason: str = ""


class TransferReceiveIn(Schema):
    version: int
    location: str


class ScriptRemovalIn(Schema):
    version: int
    reason: str
