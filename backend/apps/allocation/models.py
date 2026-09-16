from django.db import models

from apps.core.models import TenantModel
from apps.custody.models import Script
from apps.evaluators.models import Evaluator


class AllocationPolicy(TenantModel):
    class Algorithm(models.TextChoices):
        INTELLIGENT = "intelligent", "Intelligent matching"
        RULES = "rules", "Rules based"
        RANDOM = "random", "Random"

    paper = models.OneToOneField("configuration.Paper", on_delete=models.PROTECT, related_name="allocation_policy")
    algorithm = models.CharField(max_length=20, choices=Algorithm.choices, default=Algorithm.INTELLIGENT)
    minimum_experience_years = models.PositiveSmallIntegerField(default=2)
    minimum_expertise_level = models.PositiveSmallIntegerField(default=2)
    maximum_active_assignments = models.PositiveSmallIntegerField(default=20)
    assignment_due_hours = models.PositiveSmallIntegerField(default=120)
    backup_required = models.BooleanField(default=True)
    allow_same_institution = models.BooleanField(default=False)
    weights = models.JSONField(default=dict, blank=True)
    version = models.PositiveIntegerField(default=1)


class AllocationRun(TenantModel):
    class Mode(models.TextChoices):
        SIMULATION = "simulation", "Simulation"
        EXECUTION = "execution", "Execution"

    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        COMPLETED = "completed", "Completed"
        PARTIAL = "partial", "Partially completed"
        FAILED = "failed", "Failed"

    paper = models.ForeignKey("configuration.Paper", on_delete=models.PROTECT, related_name="allocation_runs")
    mode = models.CharField(max_length=16, choices=Mode.choices)
    algorithm = models.CharField(max_length=20, choices=AllocationPolicy.Algorithm.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PLANNED)
    requested_scripts = models.PositiveIntegerField(default=0)
    planned_scripts = models.PositiveIntegerField(default=0)
    allocated_scripts = models.PositiveIntegerField(default=0)
    unallocated_scripts = models.PositiveIntegerField(default=0)
    average_quality_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    forecast = models.JSONField(default=dict)
    created_by_id = models.PositiveBigIntegerField()


class AllocationProposal(TenantModel):
    run = models.ForeignKey(AllocationRun, on_delete=models.PROTECT, related_name="proposals")
    script = models.ForeignKey(Script, on_delete=models.PROTECT, related_name="allocation_proposals")
    evaluator = models.ForeignKey(Evaluator, null=True, blank=True, on_delete=models.PROTECT, related_name="allocation_proposals")
    backup_evaluator = models.ForeignKey(Evaluator, null=True, blank=True, on_delete=models.PROTECT, related_name="backup_allocation_proposals")
    valuation_round = models.PositiveSmallIntegerField(default=1)
    quality_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    score_breakdown = models.JSONField(default=dict)
    blockers = models.JSONField(default=list)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["run", "script", "valuation_round"], name="unique_run_script_round_proposal")]


class Assignment(TenantModel):
    class Status(models.TextChoices):
        ASSIGNED = "assigned", "Assigned"
        ACCEPTED = "accepted", "Accepted"
        IN_PROGRESS = "in_progress", "In progress"
        SUBMITTED = "submitted", "Submitted"
        EXPIRED = "expired", "Expired"
        REASSIGNED = "reassigned", "Reassigned"

    script = models.ForeignKey(Script, on_delete=models.PROTECT, related_name="assignments")
    evaluator = models.ForeignKey(Evaluator, on_delete=models.PROTECT, related_name="assignments")
    backup_evaluator = models.ForeignKey(Evaluator, null=True, blank=True, on_delete=models.PROTECT, related_name="backup_assignments")
    valuation_round = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ASSIGNED)
    quality_score = models.DecimalField(max_digits=5, decimal_places=2, default=100)
    source = models.CharField(max_length=20, default="manual")
    score_breakdown = models.JSONField(default=dict, blank=True)
    priority = models.PositiveSmallIntegerField(default=3)
    is_flagged = models.BooleanField(default=False)
    flag_reason = models.CharField(max_length=240, blank=True)
    skipped_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    draft_saved_at = models.DateTimeField(null=True, blank=True)
    last_opened_at = models.DateTimeField(null=True, blank=True)
    last_page = models.PositiveSmallIntegerField(default=1)
    progress_percent = models.PositiveSmallIntegerField(default=0)
    assigned_at = models.DateTimeField(auto_now_add=True)
    due_at = models.DateTimeField()
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["script", "valuation_round"], name="unique_script_round")]


class AssignmentHistory(TenantModel):
    assignment = models.ForeignKey(Assignment, on_delete=models.PROTECT, related_name="history")
    action = models.CharField(max_length=40)
    actor_id = models.PositiveBigIntegerField()
    from_evaluator_id = models.UUIDField(null=True, blank=True)
    to_evaluator_id = models.UUIDField(null=True, blank=True)
    reason = models.TextField(blank=True)
    snapshot = models.JSONField(default=dict)

    class Meta:
        ordering = ["-created_at"]
