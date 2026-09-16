from django.db import models

from apps.core.models import TenantModel
from apps.custody.models import Script
from apps.marking.models import Evaluation


class ValuationResult(TenantModel):
    evaluation = models.OneToOneField(Evaluation, on_delete=models.PROTECT, related_name="valuation_result")
    script = models.ForeignKey(Script, on_delete=models.PROTECT, related_name="valuation_results")
    valuation_round = models.PositiveSmallIntegerField()
    total_marks = models.DecimalField(max_digits=8, decimal_places=2)
    question_snapshot = models.JSONField(default=dict)
    checksum = models.CharField(max_length=64)
    is_locked = models.BooleanField(default=False)
    locked_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["script", "valuation_round"], name="unique_script_valuation_result")]


class RevealAuthorization(TenantModel):
    script = models.ForeignKey(Script, on_delete=models.PROTECT, related_name="reveal_authorizations")
    from_round = models.PositiveSmallIntegerField()
    to_round = models.PositiveSmallIntegerField()
    purpose = models.TextField()
    requested_by_id = models.PositiveBigIntegerField()
    approved_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)


class ValuationComparison(TenantModel):
    class Status(models.TextChoices):
        WITHIN_THRESHOLD = "within_threshold", "Within threshold"
        DISCREPANCY = "discrepancy", "Discrepancy"
        THIRD_REQUIRED = "third_required", "Third valuation required"
        RECONCILED = "reconciled", "Reconciled"

    script = models.ForeignKey(Script, on_delete=models.PROTECT, related_name="valuation_comparisons")
    first_result = models.ForeignKey(ValuationResult, on_delete=models.PROTECT, related_name="comparisons_as_first")
    second_result = models.ForeignKey(ValuationResult, on_delete=models.PROTECT, related_name="comparisons_as_second")
    third_result = models.ForeignKey(ValuationResult, null=True, blank=True, on_delete=models.PROTECT, related_name="comparisons_as_third")
    question_differences = models.JSONField(default=dict)
    total_difference = models.DecimalField(max_digits=8, decimal_places=2)
    percentage_difference = models.DecimalField(max_digits=8, decimal_places=3)
    threshold = models.DecimalField(max_digits=8, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices)
    requires_third_valuation = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["script", "first_result", "second_result"], name="unique_valuation_comparison")]


class FinalMark(TenantModel):
    class Rule(models.TextChoices):
        BEST = "best", "Best mark"
        AVERAGE = "average", "Average mark"
        RULE_BASED = "rule_based", "Rule based"
        APPROVED = "approved", "Approved final mark"

    class Status(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        APPROVED = "approved", "Approved"
        LOCKED = "locked", "Locked"

    script = models.OneToOneField(Script, on_delete=models.PROTECT, related_name="final_mark")
    comparison = models.ForeignKey(ValuationComparison, null=True, blank=True, on_delete=models.PROTECT, related_name="final_marks")
    rule = models.CharField(max_length=16, choices=Rule.choices)
    mark = models.DecimalField(max_digits=8, decimal_places=2)
    calculation = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PROPOSED)
    proposed_by_id = models.PositiveBigIntegerField()
    approved_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    locked_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    checksum = models.CharField(max_length=64, blank=True)
    version = models.PositiveIntegerField(default=1)
