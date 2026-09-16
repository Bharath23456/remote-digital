from django.db import models

from apps.allocation.models import Assignment
from apps.configuration.models import ExamSession, Paper
from apps.core.models import TenantModel
from apps.custody.models import Script
from apps.evaluators.models import Evaluator
from apps.valuation.models import FinalMark, ValuationResult


class ModerationPolicy(TenantModel):
    paper = models.OneToOneField(Paper, on_delete=models.PROTECT, related_name="moderation_policy")
    sample_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=10)
    sampling_modes = models.JSONField(default=list)
    high_score_threshold = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    low_score_threshold = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    mandatory = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)


class ModerationCase(TenantModel):
    class Status(models.TextChoices):
        SAMPLED = "sampled", "Sampled"
        ASSIGNED = "assigned", "Assigned"
        REVIEW = "review", "Review"
        DECIDED = "decided", "Decided"
        APPROVED = "approved", "Approved"

    script = models.ForeignKey(Script, on_delete=models.PROTECT, related_name="moderation_cases")
    source_result = models.ForeignKey(ValuationResult, on_delete=models.PROTECT, related_name="moderation_cases")
    moderator = models.ForeignKey(Evaluator, null=True, blank=True, on_delete=models.PROTECT, related_name="moderation_cases")
    sample_reasons = models.JSONField(default=list)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SAMPLED)
    fresh_mark_snapshot = models.JSONField(default=dict)
    original_mark = models.DecimalField(max_digits=8, decimal_places=2)
    adjusted_mark = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    adjustment_reason = models.TextField(blank=True)
    decided_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    approved_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["script", "source_result"], name="unique_moderation_source")]


class RevaluationRequest(TenantModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        APPROVED = "approved", "Approved"
        ASSIGNED = "assigned", "Assigned"
        EVALUATED = "evaluated", "Evaluated"
        DECIDED = "decided", "Decided"
        CLOSED = "closed", "Closed"
        REJECTED = "rejected", "Rejected"

    class Rule(models.TextChoices):
        BEST = "best", "Best mark"
        AVERAGE = "average", "Average mark"
        REGULATION = "regulation", "Regulation based"

    script = models.ForeignKey(Script, on_delete=models.PROTECT, related_name="revaluation_requests")
    identity_reference = models.CharField(max_length=128, db_index=True)
    scope = models.CharField(max_length=16, choices=[("full", "Full script"), ("questions", "Selected questions")], default="full")
    question_ids = models.JSONField(default=list)
    reason = models.TextField()
    eligibility_snapshot = models.JSONField(default=dict)
    original_final_mark = models.ForeignKey(FinalMark, on_delete=models.PROTECT, related_name="revaluation_requests")
    original_mark_snapshot = models.DecimalField(max_digits=8, decimal_places=2)
    assigned_evaluator = models.ForeignKey(Evaluator, null=True, blank=True, on_delete=models.PROTECT, related_name="revaluation_work")
    assignment = models.ForeignKey(Assignment, null=True, blank=True, on_delete=models.PROTECT, related_name="revaluation_request")
    new_result = models.ForeignKey(ValuationResult, null=True, blank=True, on_delete=models.PROTECT, related_name="revaluation_requests")
    new_mark = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    mark_difference = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    rule = models.CharField(max_length=16, choices=Rule.choices, default=Rule.BEST)
    final_mark = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.REQUESTED)
    requested_by_id = models.PositiveBigIntegerField()
    approved_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    closed_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)


class CompletionRecord(TenantModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        READY = "ready", "Ready"
        DECLARED = "declared", "Declared"
        SIGNED = "signed", "Signed"
        LOCKED = "locked", "Locked"
        RELEASED = "released", "Released"

    script = models.OneToOneField(Script, on_delete=models.PROTECT, related_name="completion_record")
    final_mark = models.OneToOneField(FinalMark, on_delete=models.PROTECT, related_name="completion_record")
    checks = models.JSONField(default=dict)
    examiner_declaration = models.TextField(blank=True)
    declaration_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    signature_digest = models.CharField(max_length=64, blank=True)
    signed_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    version = models.PositiveIntegerField(default=1)


class ControlledAuthorization(TenantModel):
    class Kind(models.TextChoices):
        RESULT_RELEASE = "result_release", "Result release"
        POST_LOCK_CHANGE = "post_lock_change", "Post-lock change"
        EVIDENCE_ACCESS = "evidence_access", "Evidence access"

    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CONSUMED = "consumed", "Consumed"
        EXPIRED = "expired", "Expired"

    kind = models.CharField(max_length=24, choices=Kind.choices)
    final_mark = models.ForeignKey(FinalMark, null=True, blank=True, on_delete=models.PROTECT, related_name="controlled_authorizations")
    purpose = models.TextField()
    proposed_change = models.JSONField(default=dict)
    requested_by_id = models.PositiveBigIntegerField()
    approved_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    expires_at = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.REQUESTED)
    version = models.PositiveIntegerField(default=1)


class PresenceSecurityEvent(TenantModel):
    assignment = models.ForeignKey(Assignment, on_delete=models.PROTECT, related_name="presence_events")
    access_session_id = models.UUIDField()
    category = models.CharField(max_length=40)
    severity = models.CharField(max_length=16, choices=[("low", "Low"), ("medium", "Medium"), ("high", "High"), ("critical", "Critical")])
    device_fingerprint = models.CharField(max_length=64)
    session_fingerprint = models.CharField(max_length=64)
    details = models.JSONField(default=dict)
    action = models.CharField(max_length=24, choices=[("record", "Record"), ("warn", "Warn"), ("pause", "Pause")], default="record")


class SecureEvaluationSession(TenantModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        COMPLETED = "completed", "Completed"
        ABANDONED = "abandoned", "Abandoned"

    assignment = models.ForeignKey(Assignment, on_delete=models.PROTECT, related_name="secure_sessions")
    evaluator = models.ForeignKey(Evaluator, on_delete=models.PROTECT, related_name="secure_sessions")
    access_session_id = models.UUIDField()
    session_fingerprint = models.CharField(max_length=64)
    device_fingerprint = models.CharField(max_length=64)
    policy_snapshot = models.JSONField(default=dict)
    preflight = models.JSONField(default=dict)
    device_inventory = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    pause_reason = models.CharField(max_length=80, blank=True)
    violation_count = models.PositiveSmallIntegerField(default=0)
    consented_at = models.DateTimeField()
    started_at = models.DateTimeField()
    last_heartbeat_at = models.DateTimeField()
    paused_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)


class ProctoringEvidence(TenantModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending upload"
        VERIFIED = "verified", "Verified"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "Expired and purged"

    secure_session = models.ForeignKey(SecureEvaluationSession, on_delete=models.PROTECT, related_name="evidence")
    event = models.ForeignKey(PresenceSecurityEvent, null=True, blank=True, on_delete=models.PROTECT, related_name="evidence")
    sequence = models.PositiveIntegerField()
    reason = models.CharField(max_length=80)
    storage_key = models.CharField(max_length=500, unique=True)
    mime_type = models.CharField(max_length=100)
    sha256 = models.CharField(max_length=64, blank=True)
    byte_size = models.PositiveBigIntegerField(default=0)
    captured_from = models.DateTimeField()
    captured_to = models.DateTimeField()
    retention_until = models.DateTimeField()
    uploaded_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["secure_session", "sequence"], name="unique_proctoring_evidence_sequence")]


class ProctoringReview(TenantModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        REVIEWING = "reviewing", "Reviewing"
        CLEARED = "cleared", "Cleared"
        ESCALATED = "escalated", "Escalated"

    secure_session = models.ForeignKey(SecureEvaluationSession, on_delete=models.PROTECT, related_name="reviews")
    event = models.OneToOneField(PresenceSecurityEvent, on_delete=models.PROTECT, related_name="review")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    decision_note = models.TextField(blank=True)
    reviewer_id = models.PositiveBigIntegerField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)


class AttendanceRecord(TenantModel):
    evaluator = models.ForeignKey(Evaluator, on_delete=models.PROTECT, related_name="attendance_records")
    session = models.ForeignKey(ExamSession, on_delete=models.PROTECT, related_name="attendance_records")
    role = models.CharField(max_length=24, default="evaluator")
    checked_in_at = models.DateTimeField()
    checked_out_at = models.DateTimeField(null=True, blank=True)
    active_seconds = models.PositiveIntegerField(default=0)
    idle_seconds = models.PositiveIntegerField(default=0)
    exception = models.CharField(max_length=200, blank=True)


class WorkloadAction(TenantModel):
    class Status(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        APPROVED = "approved", "Approved"
        EXECUTED = "executed", "Executed"
        REJECTED = "rejected", "Rejected"

    evaluator = models.ForeignKey(Evaluator, on_delete=models.PROTECT, related_name="workload_actions")
    action = models.CharField(max_length=24, choices=[("rebalance", "Rebalance"), ("prioritize", "Prioritize"), ("reallocate", "Reallocate")])
    reason = models.TextField()
    metrics = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PROPOSED)
    requested_by_id = models.PositiveBigIntegerField()
    decided_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)


class RuntimeIncident(TenantModel):
    class Status(models.TextChoices):
        DETECTED = "detected", "Detected"
        RETRYING = "retrying", "Retrying"
        DEGRADED = "degraded", "Degraded"
        RECOVERED = "recovered", "Recovered"
        FAILED = "failed", "Failed"

    service = models.CharField(max_length=40)
    category = models.CharField(max_length=60)
    severity = models.CharField(max_length=16)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DETECTED)
    last_confirmed_state = models.JSONField(default=dict)
    recovery_point = models.JSONField(default=dict)
    retry_count = models.PositiveIntegerField(default=0)
    details = models.JSONField(default=dict)
    detected_at = models.DateTimeField()
    recovered_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)


class OperationalIssue(TenantModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ASSIGNED = "assigned", "Assigned"
        ESCALATED = "escalated", "Escalated"
        RESOLVED = "resolved", "Resolved"
        CONFIRMED = "confirmed", "Confirmed"
        REOPENED = "reopened", "Reopened"

    issue_type = models.CharField(max_length=32)
    title = models.CharField(max_length=180)
    description = models.TextField()
    classification = models.CharField(max_length=40)
    priority = models.PositiveSmallIntegerField(default=3)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    owner_id = models.PositiveBigIntegerField(null=True, blank=True)
    paper = models.ForeignKey(Paper, null=True, blank=True, on_delete=models.PROTECT, related_name="operational_issues")
    question_reference = models.CharField(max_length=40, blank=True)
    sla_due_at = models.DateTimeField()
    duplicate_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="duplicates")
    resolution = models.TextField(blank=True)
    resolution_locked = models.BooleanField(default=False)
    created_by_id = models.PositiveBigIntegerField()
    version = models.PositiveIntegerField(default=1)


class KnowledgeArticle(TenantModel):
    title = models.CharField(max_length=180)
    body = models.TextField()
    category = models.CharField(max_length=40)
    issue = models.ForeignKey(OperationalIssue, null=True, blank=True, on_delete=models.PROTECT, related_name="knowledge_articles")
    is_global = models.BooleanField(default=False)
    published_by_id = models.PositiveBigIntegerField()
    version = models.PositiveIntegerField(default=1)


class NotificationDelivery(TenantModel):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        DELIVERED = "delivered", "Delivered"
        FAILED = "failed", "Failed"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"
        ESCALATED = "escalated", "Escalated"

    user_id = models.PositiveBigIntegerField()
    category = models.CharField(max_length=40)
    title = models.CharField(max_length=180)
    body = models.TextField()
    severity = models.CharField(max_length=16, default="normal")
    channels = models.JSONField(default=list)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    mandatory_acknowledgement = models.BooleanField(default=False)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=300, blank=True)
    escalation_at = models.DateTimeField(null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)


class CentreProfile(TenantModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        REVIEW = "review", "Review"
        READY = "ready", "Ready"
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"

    code = models.CharField(max_length=32)
    name = models.CharField(max_length=180)
    location = models.TextField()
    capacity = models.PositiveIntegerField()
    schedule = models.JSONField(default=dict)
    security_controls = models.JSONField(default=list)
    supervisor_id = models.PositiveBigIntegerField(null=True, blank=True)
    scanner_ids = models.JSONField(default=list)
    workstation_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant_id", "code"], name="unique_phase4_centre_code")]


class CentreReadiness(TenantModel):
    centre = models.ForeignKey(CentreProfile, on_delete=models.PROTECT, related_name="readiness_checks")
    scanner_ready = models.BooleanField(default=False)
    workstation_ready = models.BooleanField(default=False)
    network_ready = models.BooleanField(default=False)
    power_ready = models.BooleanField(default=False)
    secure_lan_ready = models.BooleanField(default=False)
    operators_ready = models.BooleanField(default=False)
    seat_plan = models.JSONField(default=dict)
    decision = models.CharField(max_length=16, choices=[("go", "Go"), ("no_go", "No-go")])
    notes = models.TextField(blank=True)
    checked_by_id = models.PositiveBigIntegerField()


class EvaluationCamp(TenantModel):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"

    centre = models.ForeignKey(CentreProfile, on_delete=models.PROTECT, related_name="camps")
    session = models.ForeignKey(ExamSession, on_delete=models.PROTECT, related_name="camps")
    name = models.CharField(max_length=180)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    evaluator_ids = models.JSONField(default=list)
    incidents = models.JSONField(default=list)
    performance = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PLANNED)
    version = models.PositiveIntegerField(default=1)


class RemunerationRule(TenantModel):
    paper = models.ForeignKey(Paper, null=True, blank=True, on_delete=models.PROTECT, related_name="remuneration_rules")
    centre = models.ForeignKey(CentreProfile, null=True, blank=True, on_delete=models.PROTECT, related_name="remuneration_rules")
    per_script = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    per_page = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    per_question = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    moderator_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    chief_examiner_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    revaluation_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    slabs = models.JSONField(default=list)
    minimum_payment = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    maximum_payment = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    tax_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    version = models.PositiveIntegerField(default=1)


class RemunerationStatement(TenantModel):
    class Status(models.TextChoices):
        CALCULATED = "calculated", "Calculated"
        APPROVED = "approved", "Approved"
        PAID = "paid", "Paid"
        RECONCILED = "reconciled", "Reconciled"

    evaluator = models.ForeignKey(Evaluator, on_delete=models.PROTECT, related_name="remuneration_statements")
    session = models.ForeignKey(ExamSession, on_delete=models.PROTECT, related_name="remuneration_statements")
    rule = models.ForeignKey(RemunerationRule, on_delete=models.PROTECT, related_name="statements")
    units = models.JSONField(default=dict)
    gross_amount = models.DecimalField(max_digits=12, decimal_places=2)
    deductions = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    net_amount = models.DecimalField(max_digits=12, decimal_places=2)
    calculation = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.CALCULATED)
    calculated_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    approved_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    payment_reference = models.CharField(max_length=100, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["evaluator", "session", "rule"], name="unique_remuneration_statement")]


class StudentScriptRequest(TenantModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        APPROVED = "approved", "Approved"
        AVAILABLE = "available", "Available"
        EXPIRED = "expired", "Expired"
        REJECTED = "rejected", "Rejected"

    identity_reference = models.CharField(max_length=128, db_index=True)
    script = models.ForeignKey(Script, on_delete=models.PROTECT, related_name="student_access_requests")
    purpose = models.CharField(max_length=24, choices=[("copy", "Script copy"), ("revaluation", "Revaluation")])
    eligibility = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.REQUESTED)
    expires_at = models.DateTimeField(null=True, blank=True)
    download_allowed = models.BooleanField(default=False)
    access_count = models.PositiveIntegerField(default=0)
    requested_by_id = models.PositiveBigIntegerField()
    approved_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)


class EvidencePackage(TenantModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        BUILDING = "building", "Building"
        SEALED = "sealed", "Sealed"
        RELEASED = "released", "Released"

    script = models.ForeignKey(Script, on_delete=models.PROTECT, related_name="evidence_packages")
    purpose = models.CharField(max_length=24, choices=[("dispute", "Dispute"), ("grievance", "Student grievance"), ("rti", "RTI"), ("legal", "Legal"), ("audit", "Audit")])
    event_count = models.PositiveIntegerField(default=0)
    manifest = models.JSONField(default=dict)
    digest = models.CharField(max_length=64, blank=True)
    requested_by_id = models.PositiveBigIntegerField()
    sealed_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.REQUESTED)
    version = models.PositiveIntegerField(default=1)


class IntegrationEndpoint(TenantModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DEGRADED = "degraded", "Degraded"
        DISABLED = "disabled", "Disabled"

    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=32)
    base_url = models.URLField()
    authentication = models.CharField(max_length=24)
    secret_reference = models.CharField(max_length=160)
    rate_limit_per_minute = models.PositiveIntegerField(default=60)
    webhook_events = models.JSONField(default=list)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    version = models.PositiveIntegerField(default=1)


class ResultHandover(TenantModel):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"
        PARTIAL = "partial", "Partial"
        REJECTED = "rejected", "Rejected"
        FAILED = "failed", "Failed"
        RECONCILED = "reconciled", "Reconciled"
        CONFIRMED = "confirmed", "Confirmed"

    endpoint = models.ForeignKey(IntegrationEndpoint, on_delete=models.PROTECT, related_name="handovers")
    final_mark = models.ForeignKey(FinalMark, on_delete=models.PROTECT, related_name="handovers")
    idempotency_key = models.CharField(max_length=128)
    payload_digest = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    acknowledgement_reference = models.CharField(max_length=160, blank=True)
    remote_snapshot = models.JSONField(default=dict)
    differences = models.JSONField(default=dict)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant_id", "endpoint", "idempotency_key"], name="unique_result_handover_key")]


class LocalePreference(TenantModel):
    user_id = models.PositiveBigIntegerField()
    locale = models.CharField(max_length=12)
    additional_locales = models.JSONField(default=list)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant_id", "user_id"], name="unique_user_locale_preference")]


class RecoveryPlan(TenantModel):
    name = models.CharField(max_length=160)
    regions = models.JSONField(default=list)
    database_replication = models.JSONField(default=dict)
    file_replication = models.JSONField(default=dict)
    rpo_minutes = models.PositiveIntegerField(default=15)
    rto_minutes = models.PositiveIntegerField(default=60)
    clean_environment = models.CharField(max_length=160)
    status = models.CharField(max_length=16, choices=[("draft", "Draft"), ("approved", "Approved"), ("active", "Active")], default="draft")
    version = models.PositiveIntegerField(default=1)


class RecoveryDrill(TenantModel):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        RUNNING = "running", "Running"
        VERIFYING = "verifying", "Verifying"
        PASSED = "passed", "Passed"
        FAILED = "failed", "Failed"

    plan = models.ForeignKey(RecoveryPlan, on_delete=models.PROTECT, related_name="drills")
    drill_type = models.CharField(max_length=32)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PLANNED)
    recovery_point = models.JSONField(default=dict)
    measurements = models.JSONField(default=dict)
    integrity_checks = models.JSONField(default=dict)
    report = models.TextField(blank=True)
    corrective_actions = models.JSONField(default=list)
    requested_by_id = models.PositiveBigIntegerField()
    approved_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
