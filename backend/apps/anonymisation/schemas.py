from ninja import Schema


class IdentityLinkIn(Schema):
    purpose: str = "Candidate identity registration"


class IdentityReceiptIn(Schema):
    receipt: str
    version: int


class StartMaskingIn(Schema):
    script_version: int
    profile: str = "university-standard-v1"


class MaskRegionIn(Schema):
    version: int
    page_number: int
    category: str
    x: float
    y: float
    width: float
    height: float


class MaskingDecisionIn(Schema):
    version: int
    notes: str = ""


class ResolutionRequestIn(Schema):
    purpose: str
    emergency: bool = False


class ResolutionDecisionIn(Schema):
    version: int
    approved: bool
    note: str = ""
