from django.db import models

from apps.configuration.models import Paper, Question
from apps.core.models import TenantModel
from apps.evaluators.models import Evaluator


class MarkingScheme(TenantModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        REVIEW = "review", "In review"
        APPROVED = "approved", "Approved"
        FROZEN = "frozen", "Frozen"
        SUPERSEDED = "superseded", "Superseded"

    paper = models.ForeignKey(Paper, on_delete=models.PROTECT, related_name="marking_schemes")
    version = models.PositiveSmallIntegerField(default=1)
    title = models.CharField(max_length=180)
    evaluation_guidelines = models.TextField()
    examiner_instructions = models.TextField()
    model_answer_key = models.CharField(max_length=320, blank=True)
    suggested_answer_key = models.CharField(max_length=320, blank=True)
    subject_rules = models.JSONField(default=dict)
    regulation_rules = models.JSONField(default=dict)
    partial_credit_rules = models.JSONField(default=dict)
    tolerance_rules = models.JSONField(default=dict)
    special_rules = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    content_digest = models.CharField(max_length=64, blank=True)
    created_by_id = models.PositiveBigIntegerField()
    approved_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    frozen_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    frozen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["paper", "version"], name="unique_paper_scheme_version")]


class RubricCriterion(TenantModel):
    scheme = models.ForeignKey(MarkingScheme, on_delete=models.PROTECT, related_name="criteria")
    question = models.ForeignKey(Question, on_delete=models.PROTECT, related_name="rubric_criteria")
    code = models.CharField(max_length=32)
    description = models.TextField()
    max_marks = models.DecimalField(max_digits=7, decimal_places=2)
    step_marks = models.JSONField(default=list)
    partial_credit_rule = models.JSONField(default=dict)
    tolerance = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    position = models.PositiveSmallIntegerField(default=1)
    is_mandatory = models.BooleanField(default=False)

    class Meta:
        ordering = ["question__position", "position"]
        constraints = [models.UniqueConstraint(fields=["scheme", "question", "code"], name="unique_scheme_question_criterion")]


class SchemeClarification(TenantModel):
    class Scope(models.TextChoices):
        QUESTION = "question", "Question"
        SUBJECT = "subject", "Subject"
        OFFICIAL = "official", "Official"

    scheme = models.ForeignKey(MarkingScheme, on_delete=models.PROTECT, related_name="clarifications")
    question = models.ForeignKey(Question, null=True, blank=True, on_delete=models.PROTECT, related_name="clarifications")
    scope = models.CharField(max_length=16, choices=Scope.choices)
    title = models.CharField(max_length=160)
    body = models.TextField()
    version = models.PositiveSmallIntegerField(default=1)
    mandatory_acknowledgement = models.BooleanField(default=False)
    published_by_id = models.PositiveBigIntegerField()
    published_at = models.DateTimeField()
    supersedes = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="revisions")


class InstructionAcknowledgement(TenantModel):
    scheme = models.ForeignKey(MarkingScheme, on_delete=models.PROTECT, related_name="acknowledgements")
    evaluator = models.ForeignKey(Evaluator, on_delete=models.PROTECT, related_name="instruction_acknowledgements")
    clarification = models.ForeignKey(SchemeClarification, null=True, blank=True, on_delete=models.PROTECT, related_name="acknowledgements")
    content_digest = models.CharField(max_length=64)
    acknowledged_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["scheme", "evaluator", "clarification"], name="unique_instruction_acknowledgement")]


class SchemeHistory(TenantModel):
    scheme = models.ForeignKey(MarkingScheme, on_delete=models.PROTECT, related_name="history")
    action = models.CharField(max_length=32)
    actor_id = models.PositiveBigIntegerField()
    reason = models.TextField(blank=True)
    snapshot = models.JSONField(default=dict)

    class Meta:
        ordering = ["-created_at"]
