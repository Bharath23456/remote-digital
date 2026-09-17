from django.db import models

from apps.core.models import TenantModel
from apps.custody.models import Script


class IdentityLink(TenantModel):
    script = models.OneToOneField(Script, on_delete=models.PROTECT, related_name="identity_link")
    identity_reference = models.UUIDField(unique=True, db_index=True)
    linked_by_id = models.PositiveBigIntegerField()
    stored_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)


class MaskingJob(TenantModel):
    class Status(models.TextChoices):
        DETECTED = "detected", "Detected"
        REVIEWED = "reviewed", "Reviewed"
        PROCESSING = "processing", "Processing"
        APPLIED = "applied", "Applied"
        VERIFIED = "verified", "Verified"
        FAILED = "failed", "Failed"

    script = models.ForeignKey(Script, on_delete=models.PROTECT, related_name="masking_jobs")
    profile = models.CharField(max_length=80, default="identity-cover-v1")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DETECTED)
    detection_confidence = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    created_by_id = models.PositiveBigIntegerField()
    reviewed_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    applied_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    verified_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["script", "version"], name="unique_script_masking_job_version")]


class MaskRegion(TenantModel):
    class Category(models.TextChoices):
        IDENTITY_PAGE = "identity_page", "Identity cover page"
        CANDIDATE_NAME = "candidate_name", "Candidate name"
        REGISTER_NUMBER = "register_number", "Register number"
        USN = "usn", "USN"
        COLLEGE = "college", "College"
        INSTITUTION = "institution", "Institution"
        SIGNATURE = "signature", "Signature"
        PHOTOGRAPH = "photograph", "Photograph"

    class Source(models.TextChoices):
        AUTOMATIC = "automatic", "Automatic"
        MANUAL = "manual", "Manual"

    job = models.ForeignKey(MaskingJob, on_delete=models.CASCADE, related_name="regions")
    page_number = models.PositiveSmallIntegerField()
    category = models.CharField(max_length=24, choices=Category.choices)
    x = models.DecimalField(max_digits=7, decimal_places=6)
    y = models.DecimalField(max_digits=7, decimal_places=6)
    width = models.DecimalField(max_digits=7, decimal_places=6)
    height = models.DecimalField(max_digits=7, decimal_places=6)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.MANUAL)
    confidence = models.DecimalField(max_digits=5, decimal_places=4, default=1)
    is_active = models.BooleanField(default=True)


class MaskVerification(TenantModel):
    job = models.ForeignKey(MaskingJob, on_delete=models.PROTECT, related_name="verifications")
    verifier_id = models.PositiveBigIntegerField()
    passed = models.BooleanField()
    notes = models.TextField(blank=True)


class IdentityResolutionRequest(TenantModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CONSUMED = "consumed", "Consumed"
        EXPIRED = "expired", "Expired"

    identity_link = models.ForeignKey(IdentityLink, on_delete=models.PROTECT, related_name="resolution_requests")
    requested_by_id = models.PositiveBigIntegerField()
    purpose = models.CharField(max_length=160)
    emergency = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)


class IdentityResolutionApproval(TenantModel):
    request = models.ForeignKey(IdentityResolutionRequest, on_delete=models.PROTECT, related_name="approvals")
    approver_id = models.PositiveBigIntegerField()
    approved = models.BooleanField()
    note = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["request", "approver_id"], name="one_identity_resolution_decision_per_approver")]
