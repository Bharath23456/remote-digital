from datetime import date
from typing import Any

from ninja import Field, Schema


class EvaluatorCreateIn(Schema):
    evaluator_code: str
    display_name: str
    email: str
    mobile: str = ""
    employee_id: str = ""
    institution_name: str
    department: str
    designation: str
    qualification: str
    employment_type: str = "permanent"
    employment_details: dict[str, Any] = Field(default_factory=dict)
    years_experience: int = 0
    grade: str = "evaluator"
    daily_capacity: int = 20
    available_from: date | None = None
    available_to: date | None = None
    create_login: bool = True
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class EvaluatorUpdateIn(Schema):
    version: int
    display_name: str | None = None
    email: str | None = None
    mobile: str | None = None
    employee_id: str | None = None
    institution_name: str | None = None
    department: str | None = None
    designation: str | None = None
    qualification: str | None = None
    employment_type: str | None = None
    employment_details: dict[str, Any] | None = None
    years_experience: int | None = None
    daily_capacity: int | None = None
    available_from: date | None = None
    available_to: date | None = None
    custom_fields: dict[str, Any] | None = None


class LifecycleIn(Schema):
    version: int
    status: str | None = None
    grade: str | None = None
    reason: str


class ExpertiseIn(Schema):
    subject_id: str
    level: int
    years_experience: int


class AvailabilityIn(Schema):
    starts_on: date
    ends_on: date
    daily_capacity: int
    notes: str = ""


class FaceCaptureIn(Schema):
    image_base64: str
    liveness_passed: bool = False
    face_count: int = 1
    quality: dict[str, Any] = Field(default_factory=dict)
    model_version: str = "opencv-sface-v1"
    device_fingerprint: str = ""


class FaceAccessIn(FaceCaptureIn):
    assignment_id: str
