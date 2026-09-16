from datetime import date

from ninja import Field, Schema


class VerificationCreateIn(Schema):
    evaluator_id: str
    checks: dict[str, bool] = Field(default_factory=dict)
    notes: str = ""


class VerificationActionIn(Schema):
    version: int
    notes: str = ""
    expires_on: date | None = None


class VerificationUpdateIn(Schema):
    version: int
    checks: dict[str, bool] = Field(default_factory=dict)
    notes: str = ""


class EligibilityAssessIn(Schema):
    evaluator_id: str
    subject_id: str
    has_conflict: bool = False
    is_debarred: bool = False
    is_blacklisted: bool = False
    expires_on: date


class EligibilityBlockIn(Schema):
    version: int
    reason: str
    has_conflict: bool | None = None
    is_debarred: bool | None = None
    is_blacklisted: bool | None = None


class NoteIn(Schema):
    reason: str


class VerificationDocumentUploadIn(Schema):
    kind: str
    content_type: str
    maximum_bytes: int = 10_000_000


class VerificationDocumentFinalizeIn(Schema):
    version: int
