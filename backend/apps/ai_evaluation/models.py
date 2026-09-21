from django.db import models

from apps.allocation.models import Assignment
from apps.configuration.models import Paper, Question
from apps.core.models import TenantModel, TimeStampedModel


class AIProviderConfiguration(TenantModel):
    api_key_ciphertext = models.TextField()
    is_active = models.BooleanField(default=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant_id"], name="unique_ai_provider_per_tenant"),
        ]


class AIReferencePack(TenantModel):
    paper = models.OneToOneField(Paper, on_delete=models.PROTECT, related_name="ai_reference_pack")
    version = models.PositiveIntegerField(default=1)


class AIReferenceUpload(TenantModel):
    class Kind(models.TextChoices):
        QUESTION_PAPER = "question_paper", "Question paper"
        REFERENCE_ANSWER = "reference_answer", "Reference answer"

    class Status(models.TextChoices):
        ISSUED = "issued", "Issued"
        COMPLETED = "completed", "Completed"
        EXPIRED = "expired", "Expired"

    pack = models.ForeignKey(AIReferencePack, on_delete=models.CASCADE, related_name="uploads")
    kind = models.CharField(max_length=24, choices=Kind.choices)
    slot = models.PositiveSmallIntegerField(default=1)
    storage_key = models.CharField(max_length=320, unique=True)
    file_name = models.CharField(max_length=180)
    content_type = models.CharField(max_length=64)
    maximum_bytes = models.PositiveBigIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ISSUED)
    expires_at = models.DateTimeField(db_index=True)
    sha256 = models.CharField(max_length=64, blank=True)
    byte_size = models.PositiveBigIntegerField(default=0)
    finalized_at = models.DateTimeField(null=True, blank=True)
    created_by_id = models.PositiveBigIntegerField()
    version = models.PositiveIntegerField(default=1)


class AIReferenceAsset(TenantModel):
    pack = models.ForeignKey(AIReferencePack, on_delete=models.CASCADE, related_name="assets")
    kind = models.CharField(max_length=24, choices=AIReferenceUpload.Kind.choices)
    slot = models.PositiveSmallIntegerField(default=1)
    storage_key = models.CharField(max_length=320, unique=True)
    file_name = models.CharField(max_length=180)
    mime_type = models.CharField(max_length=64)
    sha256 = models.CharField(max_length=64)
    byte_size = models.PositiveBigIntegerField()
    asset_version = models.PositiveIntegerField(default=1)
    uploaded_by_id = models.PositiveBigIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["pack", "kind", "slot"], name="unique_ai_reference_slot")]


class AIQuestionGuide(TenantModel):
    pack = models.ForeignKey(AIReferencePack, on_delete=models.CASCADE, related_name="question_guides")
    question = models.ForeignKey(Question, on_delete=models.PROTECT, related_name="ai_guides")
    question_text = models.TextField()
    evaluation_guidance = models.TextField(blank=True)
    max_marks = models.DecimalField(max_digits=7, decimal_places=2)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["question__position"]
        constraints = [models.UniqueConstraint(fields=["pack", "question"], name="unique_ai_question_guide")]


class AIAnalysis(TenantModel):
    class Trigger(models.TextChoices):
        ASSISTIVE = "assistive", "Evaluator assistance"
        AUTONOMOUS = "autonomous", "Autonomous evaluation"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        LOW_CONFIDENCE = "low_confidence", "Routed to human"
        FAILED = "failed", "Failed"

    assignment = models.ForeignKey(Assignment, on_delete=models.PROTECT, related_name="ai_analyses")
    trigger = models.CharField(max_length=16, choices=Trigger.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True)
    model_name = models.CharField(max_length=80)
    effective_confidence = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    raw_response = models.JSONField(default=dict, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    requested_by_id = models.PositiveBigIntegerField()
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]


class AIQuestionAssessment(TenantModel):
    analysis = models.ForeignKey(AIAnalysis, on_delete=models.CASCADE, related_name="question_assessments")
    question = models.ForeignKey(Question, on_delete=models.PROTECT, related_name="ai_assessments")
    marks = models.DecimalField(max_digits=7, decimal_places=2)
    confidence = models.DecimalField(max_digits=5, decimal_places=2)
    feedback = models.TextField(blank=True)
    reasoning = models.TextField(blank=True)

    class Meta:
        ordering = ["question__position"]
        constraints = [models.UniqueConstraint(fields=["analysis", "question"], name="unique_ai_analysis_question")]
