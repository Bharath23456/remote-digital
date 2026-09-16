from datetime import date

from ninja import Schema


class UploadIntentIn(Schema):
    script_id: str
    kind: str
    page_number: int
    asset_version: int = 1
    content_type: str
    maximum_bytes: int = 25_000_000


class ManualScanUploadIn(Schema):
    script_id: str
    page_number: int
    content_type: str
    maximum_bytes: int


class FinalizeUploadIn(Schema):
    version: int


class CompleteScanIn(Schema):
    version: int
    page_count: int
    location: str = "Secure scanning room"


class LegalHoldIn(Schema):
    enabled: bool
    retention_until: date | None = None


class DeleteAssetIn(Schema):
    reason: str
