from django.db import models

from apps.allocation.models import Assignment
from apps.configuration.models import Question
from apps.core.models import TenantModel
from apps.rubrics.models import MarkingScheme, RubricCriterion


class Evaluation(TenantModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        REVIEW = "review", "Review"
        SUBMITTED = "submitted", "Submitted"
        LOCKED = "locked", "Locked"

    assignment = models.OneToOneField(Assignment, on_delete=models.PROTECT, related_name="evaluation")
    scheme = models.ForeignKey(MarkingScheme, on_delete=models.PROTECT, related_name="evaluations")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    total_marks = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    last_question = models.ForeignKey(Question, null=True, blank=True, on_delete=models.PROTECT, related_name="last_opened_evaluations")
    last_page = models.PositiveSmallIntegerField(default=1)
    started_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    checksum = models.CharField(max_length=64, blank=True)
    version = models.PositiveIntegerField(default=1)


class QuestionMark(TenantModel):
    class Outcome(models.TextChoices):
        EVALUATED = "evaluated", "Evaluated"
        UNANSWERED = "unanswered", "Unanswered"
        NOT_APPLICABLE = "not_applicable", "Not applicable"
        SKIPPED = "skipped", "Skipped"

    class Adjustment(models.TextChoices):
        NONE = "none", "None"
        GRACE = "grace", "Grace"
        NEGATIVE = "negative", "Negative"
        BONUS = "bonus", "Bonus"

    evaluation = models.ForeignKey(Evaluation, on_delete=models.PROTECT, related_name="question_marks")
    question = models.ForeignKey(Question, on_delete=models.PROTECT, related_name="marks")
    sub_question = models.CharField(max_length=24, blank=True)
    sequence = models.PositiveIntegerField()
    marks = models.DecimalField(max_digits=8, decimal_places=2)
    outcome = models.CharField(max_length=20, choices=Outcome.choices, default=Outcome.EVALUATED)
    adjustment = models.CharField(max_length=16, choices=Adjustment.choices, default=Adjustment.NONE)
    step_marks = models.JSONField(default=list)
    rubric_criterion = models.ForeignKey(RubricCriterion, null=True, blank=True, on_delete=models.PROTECT, related_name="marks")
    marked_for_review = models.BooleanField(default=False)
    requires_attention = models.BooleanField(default=False)
    examiner_confirmed = models.BooleanField(default=False)
    answer_selection = models.JSONField(default=dict)
    supersedes = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="corrections")
    actor_id = models.PositiveBigIntegerField()

    class Meta:
        ordering = ["question__position", "sub_question", "sequence"]
        constraints = [models.UniqueConstraint(fields=["evaluation", "question", "sub_question", "sequence"], name="unique_question_mark_revision")]


class Annotation(TenantModel):
    class Action(models.TextChoices):
        ADD = "add", "Add"
        DELETE = "delete", "Delete"
        RESTORE = "restore", "Restore"

    class Kind(models.TextChoices):
        TICK = "tick", "Tick"
        CROSS = "cross", "Cross"
        UNDERLINE = "underline", "Underline"
        HIGHLIGHT = "highlight", "Highlight"
        CIRCLE = "circle", "Circle"
        RECTANGLE = "rectangle", "Rectangle"
        ARROW = "arrow", "Arrow"
        FREEHAND = "freehand", "Freehand"
        SYMBOL = "symbol", "Symbol"

    evaluation = models.ForeignKey(Evaluation, on_delete=models.PROTECT, related_name="annotations")
    page_number = models.PositiveSmallIntegerField()
    question = models.ForeignKey(Question, null=True, blank=True, on_delete=models.PROTECT, related_name="annotations")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    action = models.CharField(max_length=16, choices=Action.choices, default=Action.ADD)
    geometry = models.JSONField(default=dict)
    style = models.JSONField(default=dict)
    symbol = models.CharField(max_length=80, blank=True)
    target = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="history_events")
    actor_id = models.PositiveBigIntegerField()

    class Meta:
        ordering = ["created_at"]


class EvaluationComment(TenantModel):
    class Kind(models.TextChoices):
        EXAMINER = "examiner", "Examiner"
        QUESTION = "question", "Question"
        PAGE = "page", "Page"
        REMARK = "remark", "Evaluation remark"

    evaluation = models.ForeignKey(Evaluation, on_delete=models.PROTECT, related_name="comments")
    question = models.ForeignKey(Question, null=True, blank=True, on_delete=models.PROTECT, related_name="evaluation_comments")
    page_number = models.PositiveSmallIntegerField(null=True, blank=True)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    body = models.TextField()
    actor_id = models.PositiveBigIntegerField()

    class Meta:
        ordering = ["created_at"]


class QuestionPageAnchor(TenantModel):
    evaluation = models.ForeignKey(Evaluation, on_delete=models.CASCADE, related_name="page_anchors")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="page_anchors")
    page_number = models.PositiveSmallIntegerField()
    actor_id = models.PositiveBigIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["evaluation", "question"], name="unique_question_page_anchor")]
