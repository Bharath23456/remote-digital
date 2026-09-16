from django.db import models

from apps.core.models import TenantModel
from apps.valuation.models import ValuationComparison


class DiscrepancyCase(TenantModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLARIFICATION = "clarification", "Clarification requested"
        MODERATOR = "moderator", "Moderator review"
        CHIEF_EXAMINER = "chief_examiner", "Chief examiner review"
        THIRD_REQUESTED = "third_requested", "Third valuation requested"
        RESOLVED = "resolved", "Resolved"
        APPROVED = "approved", "Approved"

    comparison = models.OneToOneField(ValuationComparison, on_delete=models.PROTECT, related_name="discrepancy_case")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.OPEN)
    threshold = models.DecimalField(max_digits=8, decimal_places=2)
    items = models.JSONField(default=list)
    assigned_role = models.CharField(max_length=32, default="moderator")
    version = models.PositiveIntegerField(default=1)


class ExaminerClarification(TenantModel):
    case = models.ForeignKey(DiscrepancyCase, on_delete=models.PROTECT, related_name="clarifications")
    valuation_round = models.PositiveSmallIntegerField()
    requested_by_id = models.PositiveBigIntegerField()
    request = models.TextField()
    response = models.TextField(blank=True)
    responded_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)


class DiscrepancyResolution(TenantModel):
    class Method(models.TextChoices):
        AUTOMATIC = "automatic", "Automatic"
        MANUAL = "manual", "Manual"
        RULE_BASED = "rule_based", "Rule based"
        THIRD_VALUATION = "third_valuation", "Third valuation"
        OVERRIDE = "override", "Override"

    case = models.ForeignKey(DiscrepancyCase, on_delete=models.PROTECT, related_name="resolutions")
    method = models.CharField(max_length=20, choices=Method.choices)
    final_mark = models.DecimalField(max_digits=8, decimal_places=2)
    reason = models.TextField()
    decided_by_id = models.PositiveBigIntegerField()
    approved_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    calculation = models.JSONField(default=dict)

    class Meta:
        ordering = ["created_at"]
