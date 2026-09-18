import uuid

from django.contrib.auth.models import User
from django.db import models
from django.db.models.functions import Lower

from apps.core.models import TimeStampedModel


class Institution(TimeStampedModel):
    class Kind(models.TextChoices):
        UNIVERSITY = "university", "University"
        AUTHORITY = "authority", "Authority"
        CAMPUS = "campus", "Campus"
        COLLEGE = "college", "College"
        FACULTY = "faculty", "Faculty"
        DEPARTMENT = "department", "Department"

    tenant_id = models.UUIDField(default=uuid.uuid4, db_index=True)
    name = models.CharField(max_length=180)
    code = models.SlugField(max_length=40)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.UNIVERSITY)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    policy = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)

    def __str__(self):
        return self.name

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant_id", "code"], name="unique_tenant_institution_code")]


class Membership(TimeStampedModel):
    class Role(models.TextChoices):
        PLATFORM_ADMIN = "platform_admin", "Platform administrator"
        UNIVERSITY_ADMIN = "university_admin", "University administrator"
        EXAM_CONTROLLER = "exam_controller", "Examination controller"
        EVALUATOR = "evaluator", "Evaluator"
        RECEIVING_OFFICER = "receiving_officer", "Receiving officer"
        BUNDLE_PREPARER = "bundle_preparer", "Bundle preparer"
        INTAKE_RECEIVER = "intake_receiver", "Bundle and packet receiver"
        SCAN_OPERATOR = "scan_operator", "Scan operator"
        AUDITOR = "auditor", "Auditor"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="admiezo_memberships")
    institution = models.ForeignKey(Institution, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=32, choices=Role.choices)
    permissions = models.JSONField(default=list, blank=True)
    enabled_modules = models.JSONField(default=list, blank=True)
    custom_fields = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    must_change_password = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "institution"], name="unique_membership")]


class CustomFieldDefinition(TimeStampedModel):
    class FieldType(models.TextChoices):
        TEXT = "text", "Text"
        TEXTAREA = "textarea", "Long text"
        NUMBER = "number", "Number"
        DATE = "date", "Date"
        SELECT = "select", "Select"
        CHECKBOX = "checkbox", "Checkbox"

    tenant_id = models.UUIDField(db_index=True)
    form_key = models.SlugField(max_length=64)
    key = models.SlugField(max_length=64)
    label = models.CharField(max_length=120)
    field_type = models.CharField(max_length=16, choices=FieldType.choices, default=FieldType.TEXT)
    required = models.BooleanField(default=False)
    options = models.JSONField(default=list, blank=True)
    placeholder = models.CharField(max_length=160, blank=True)
    help_text = models.CharField(max_length=240, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["form_key", "sort_order", "label"]
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "form_key", "key"], name="unique_tenant_form_field_key")
        ]


class CustomFieldRecord(TimeStampedModel):
    tenant_id = models.UUIDField(db_index=True)
    form_key = models.SlugField(max_length=64)
    record_id = models.UUIDField()
    values = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant_id", "form_key", "record_id"], name="unique_tenant_form_field_record")
        ]


class TenantAccount(TimeStampedModel):
    class Status(models.TextChoices):
        PROVISIONING = "provisioning", "Provisioning"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        OFFBOARDING = "offboarding", "Offboarding"

    class Plan(models.TextChoices):
        STANDARD = "standard", "Standard"
        PROFESSIONAL = "professional", "Professional"
        ENTERPRISE = "enterprise", "Enterprise"

    root_institution = models.OneToOneField(Institution, on_delete=models.PROTECT, related_name="tenant_account")
    slug = models.SlugField(max_length=63, unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROVISIONING)
    plan = models.CharField(max_length=20, choices=Plan.choices, default=Plan.STANDARD)
    enabled_modules = models.JSONField(default=list)
    storage_quota_bytes = models.PositiveBigIntegerField(default=10 * 1024 * 1024 * 1024)
    data_region = models.CharField(max_length=40, default="in-primary")
    version = models.PositiveIntegerField(default=1)


class TenantDomain(TimeStampedModel):
    class Kind(models.TextChoices):
        MANAGED = "managed", "Managed subdomain"
        CUSTOM = "custom", "Custom domain"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending verification"
        ACTIVE = "active", "Active"
        FAILED = "failed", "Verification failed"

    tenant_account = models.ForeignKey(TenantAccount, on_delete=models.CASCADE, related_name="domains")
    hostname = models.CharField(max_length=253)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.MANAGED)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    is_primary = models.BooleanField(default=False)
    verification_token = models.CharField(max_length=64, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(Lower("hostname"), name="unique_tenant_domain_hostname_ci")]
