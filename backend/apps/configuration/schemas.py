from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ninja import Field, Schema


class AcademicYearIn(Schema):
    label: str
    starts_on: date
    ends_on: date


class RegulationIn(Schema):
    code: str
    title: str
    effective_from: date
    effective_to: date | None = None


class TermIn(Schema):
    academic_year_id: str
    name: str
    sequence: int
    starts_on: date
    ends_on: date


class ExamSessionIn(Schema):
    academic_year_id: str
    name: str
    term: str
    evaluation_starts_at: datetime
    evaluation_ends_at: datetime
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class EvaluationEventIn(Schema):
    session_id: str
    name: str
    starts_at: datetime
    ends_at: datetime
    evaluation_centre_ids: list[str] = Field(default_factory=list)


class ProgrammeIn(Schema):
    code: str
    name: str
    regulation: str


class CourseIn(Schema):
    programme_id: str
    regulation_id: str
    code: str
    name: str
    duration_terms: int


class SubjectIn(Schema):
    programme_id: str
    course_id: str | None = None
    session_ids: list[str] = Field(default_factory=list)
    related_subject_ids: list[str] = Field(default_factory=list)
    code: str
    name: str
    semester: int


class EvaluationCentreIn(Schema):
    code: str
    name: str
    address: str = ""
    network_cidrs: list[str] = Field(default_factory=list)


class PaperIn(Schema):
    session_id: str
    subject_id: str
    code: str
    title: str
    max_marks: Decimal
    pass_marks: Decimal
    valuation_rounds: int = 1
    discrepancy_threshold: Decimal = Decimal("0")
    moderation_required: bool = False
    rules: dict[str, Any] = Field(default_factory=dict)
    effective_from: datetime | None = None
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class QuestionIn(Schema):
    number: str
    sub_question: str = ""
    max_marks: Decimal
    question_type: str = "descriptive"
    required: bool = True
    position: int | None = None


class QuestionUpdateIn(Schema):
    version: int
    number: str
    sub_question: str = ""
    max_marks: Decimal
    question_type: str = "descriptive"
    required: bool = True
    position: int | None = None


class QuestionDeleteIn(Schema):
    version: int


class PaperActionIn(Schema):
    version: int
    note: str = ""


class PaperUpdateIn(Schema):
    version: int
    title: str | None = None
    max_marks: Decimal | None = None
    pass_marks: Decimal | None = None
    valuation_rounds: int | None = None
    discrepancy_threshold: Decimal | None = None
    moderation_required: bool | None = None
    rules: dict[str, Any] | None = None
    effective_from: datetime | None = None


class PaperRollbackIn(Schema):
    version: int
    revision_version: int
    reason: str
    emergency: bool = False


class ConfigurationChangeIn(Schema):
    version: int
    kind: str
    reason: str
    target_revision_version: int | None = None
    changes: dict[str, Any] = Field(default_factory=dict)


class ConfigurationChangeDecisionIn(Schema):
    version: int
    decision: str
    note: str = ""
