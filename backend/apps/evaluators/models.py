from django.db import models
from django.conf import settings

from apps.configuration.models import Subject
from apps.core.models import TenantModel


class Evaluator(TenantModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending verification"
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        SUSPENDED = "suspended", "Suspended"
        RETIRED = "retired", "Retired"

    class Grade(models.TextChoices):
        EVALUATOR = "evaluator", "Evaluator"
        SENIOR = "senior", "Senior examiner"
        CHIEF = "chief", "Chief examiner"
        MODERATOR = "moderator", "Moderator"
        REVALUATOR = "revaluator", "Revaluator"
        SCRUTINIZER = "scrutinizer", "Scrutinizer"
        VERIFIER = "verifier", "Verifier"

    evaluator_code = models.CharField(max_length=32)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="evaluator_profiles")
    display_name = models.CharField(max_length=120)
    email = models.EmailField(blank=True)
    mobile = models.CharField(max_length=24, blank=True)
    employee_id = models.CharField(max_length=64, blank=True)
    institution_name = models.CharField(max_length=180)
    department = models.CharField(max_length=120)
    designation = models.CharField(max_length=120)
    qualification = models.CharField(max_length=180)
    employment_type = models.CharField(max_length=40, default="permanent")
    employment_details = models.JSONField(default=dict, blank=True)
    custom_fields = models.JSONField(default=dict, blank=True)
    years_experience = models.PositiveSmallIntegerField(default=0)
    grade = models.CharField(max_length=20, choices=Grade.choices, default=Grade.EVALUATOR)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    daily_capacity = models.PositiveSmallIntegerField(default=20)
    available_from = models.DateField(null=True, blank=True)
    available_to = models.DateField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant_id", "evaluator_code"], name="unique_evaluator_code")]


class Expertise(TenantModel):
    evaluator = models.ForeignKey(Evaluator, on_delete=models.CASCADE, related_name="expertise")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="evaluators")
    level = models.PositiveSmallIntegerField(default=1)
    years_experience = models.PositiveSmallIntegerField(default=0)
    verified = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["evaluator", "subject"], name="unique_evaluator_subject")]


class EvaluatorAvailability(TenantModel):
    evaluator = models.ForeignKey(Evaluator, on_delete=models.CASCADE, related_name="availability_periods")
    starts_on = models.DateField()
    ends_on = models.DateField()
    daily_capacity = models.PositiveSmallIntegerField()
    notes = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["starts_on"]


class EvaluatorHistory(TenantModel):
    evaluator = models.ForeignKey(Evaluator, on_delete=models.PROTECT, related_name="history")
    action = models.CharField(max_length=40)
    from_status = models.CharField(max_length=16, blank=True)
    to_status = models.CharField(max_length=16, blank=True)
    from_grade = models.CharField(max_length=20, blank=True)
    to_grade = models.CharField(max_length=20, blank=True)
    actor_id = models.PositiveBigIntegerField()
    snapshot = models.JSONField(default=dict)
    reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
