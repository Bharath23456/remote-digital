from ninja import Field, Schema


class PolicyIn(Schema):
    paper_id: str
    algorithm: str = "intelligent"
    minimum_experience_years: int = 2
    minimum_expertise_level: int = 2
    maximum_active_assignments: int = 20
    assignment_due_hours: int = 120
    backup_required: bool = True
    allow_same_institution: bool = False
    weights: dict = {}
    version: int = 0


class ManualAssignmentIn(Schema):
    script_id: str
    evaluator_id: str
    backup_evaluator_id: str | None = None
    valuation_round: int = 1
    due_in_hours: int = 120
    priority: int = 3
    custom_fields: dict = Field(default_factory=dict)


class SimulationIn(Schema):
    paper_id: str
    algorithm: str | None = None
    valuation_round: int = 1
    maximum_scripts: int = 500


class AssignmentActionIn(Schema):
    version: int
    action: str
    reason: str = ""
    page: int | None = None
    progress_percent: int | None = None
    priority: int | None = None


class AssignmentFlagIn(Schema):
    version: int
    flagged: bool
    reason: str = ""


class RedistributionIn(Schema):
    version: int
    reason: str
