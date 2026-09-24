import hashlib
import secrets
from datetime import datetime, timedelta
from uuid import uuid4

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from ninja import Field, Router, Schema
from ninja.errors import HttpError

from apps.allocation.models import Assignment
from apps.configuration.models import ExamSession, Paper
from apps.core.authz import membership_for, require_roles, require_step_up
from apps.core.models import AuditEvent, OutboxEvent
from apps.core.services import record_event
from apps.custody.models import CustodyEvent, Script
from apps.evaluators.models import Evaluator
from apps.marking.models import Evaluation
from apps.repository.models import ScriptAsset
from apps.repository.storage import read_object_metadata, signed_object_url
from apps.tenancy.custom_fields import persist_custom_values, validate_custom_values
from apps.tenancy.models import Membership
from apps.valuation.models import FinalMark, ValuationResult

from .models import (
    AttendanceRecord,
    CentreProfile,
    CentreReadiness,
    CompletionRecord,
    ControlledAuthorization,
    EvidencePackage,
    EvaluationCamp,
    IntegrationEndpoint,
    KnowledgeArticle,
    LocalePreference,
    ModerationCase,
    ModerationPolicy,
    NotificationDelivery,
    OperationalIssue,
    PresenceSecurityEvent,
    ProctoringEvidence,
    ProctoringReview,
    RecoveryDrill,
    RecoveryPlan,
    RemunerationRule,
    RemunerationStatement,
    ResultHandover,
    RevaluationRequest,
    RuntimeIncident,
    SecureEvaluationSession,
    StudentScriptRequest,
    UniversityApiKey,
    WorkloadAction,
)
from .services import (
    acknowledge_handover,
    assess_centre,
    calculate_remuneration,
    consume_authorization,
    create_issue,
    create_notification,
    create_proctoring_evidence,
    create_revaluation,
    create_student_request,
    create_workload_action,
    declare_and_sign,
    decide_authorization,
    decide_proctoring_review,
    finish_secure_evaluation_session,
    heartbeat_secure_evaluation_session,
    monitoring_snapshot,
    notification_action,
    productivity_snapshot,
    publish_article,
    queue_handover,
    record_presence_event,
    resume_secure_evaluation_session,
    request_authorization,
    review_completion,
    sample_moderation_cases,
    save_moderation_policy,
    seal_evidence,
    set_locale,
    security_policy_snapshot,
    start_secure_evaluation_session,
    transition_drill,
    transition_camp,
    transition_centre,
    transition_handover,
    transition_issue,
    transition_moderation,
    transition_recovery_plan,
    transition_revaluation,
    transition_runtime,
    transition_statement,
    transition_workload,
    verify_proctoring_evidence,
)


router = Router(tags=["Remaining operational modules"])
ADMIN_ROLES = (Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER)
READ_ROLES = (*ADMIN_ROLES, Membership.Role.AUDITOR)
SUPPORTED_LOCALES = [
    {"code": "en", "name": "English", "native": "English", "direction": "ltr"},
    {"code": "kn", "name": "Kannada", "native": "ಕನ್ನಡ", "direction": "ltr"},
    {"code": "hi", "name": "Hindi", "native": "हिन्दी", "direction": "ltr"},
    {"code": "ta", "name": "Tamil", "native": "தமிழ்", "direction": "ltr"},
    {"code": "te", "name": "Telugu", "native": "తెలుగు", "direction": "ltr"},
    {"code": "ml", "name": "Malayalam", "native": "മലയാളം", "direction": "ltr"},
    {"code": "mr", "name": "Marathi", "native": "मराठी", "direction": "ltr"},
    {"code": "gu", "name": "Gujarati", "native": "ગુજરાતી", "direction": "ltr"},
    {"code": "bn", "name": "Bengali", "native": "বাংলা", "direction": "ltr"},
    {"code": "ur", "name": "Urdu", "native": "اردو", "direction": "rtl"},
]


class VersionActionIn(Schema):
    version: int
    target: str
    reason: str = ""
    values: dict = Field(default_factory=dict)


class ModerationPolicyIn(Schema):
    paper_id: str
    version: int | None = None
    sample_percentage: float = 10
    sampling_modes: list[str] = Field(default_factory=lambda: ["percentage_random", "failed_script"])
    high_score_threshold: float | None = None
    low_score_threshold: float | None = None
    mandatory: bool = False


class RevaluationIn(Schema):
    script_id: str
    identity_reference: str
    scope: str = "full"
    question_ids: list[str] = Field(default_factory=list)
    reason: str
    rule: str = "best"
    request_type: str = "revaluation"
    recounting_notes: str = ""


class CompletionSignIn(Schema):
    version: int
    declaration: str
    confirm: bool


class AuthorizationIn(Schema):
    kind: str
    final_mark_id: str | None = None
    purpose: str
    proposed_change: dict = Field(default_factory=dict)
    expires_at: datetime | None = None


class DecisionIn(Schema):
    version: int
    approve: bool
    release_mode: str = "masked"


class PresenceEventIn(Schema):
    assignment_id: str
    secure_session_id: str | None = None
    category: str
    severity: str
    device_fingerprint: str
    session_fingerprint: str
    details: dict = Field(default_factory=dict)


class SecureSessionIn(Schema):
    assignment_id: str
    session_fingerprint: str
    device_fingerprint: str
    consent: bool
    preflight: dict = Field(default_factory=dict)
    device_inventory: dict = Field(default_factory=dict)


class PostureIn(Schema):
    posture: dict = Field(default_factory=dict)


class SecureSessionFinishIn(Schema):
    completed: bool = False


class EvidenceIntentIn(Schema):
    sequence: int
    reason: str
    mime_type: str
    captured_from: datetime
    captured_to: datetime
    max_bytes: int


class EvidenceCompleteIn(Schema):
    sha256: str
    byte_size: int


class ProctoringReviewIn(Schema):
    version: int
    status: str
    note: str = ""


class AttendanceIn(Schema):
    evaluator_id: str
    session_id: str
    role: str = "evaluator"
    action: str = "check_in"
    active_seconds: int = 0
    idle_seconds: int = 0
    exception: str = ""


class WorkloadIn(Schema):
    evaluator_id: str
    action: str
    reason: str
    metrics: dict = Field(default_factory=dict)


class RuntimeIncidentIn(Schema):
    service: str
    category: str
    severity: str
    last_confirmed_state: dict = Field(default_factory=dict)
    recovery_point: dict = Field(default_factory=dict)
    details: dict = Field(default_factory=dict)


class IssueIn(Schema):
    issue_type: str
    title: str
    description: str
    paper_id: str | None = None
    question_reference: str = ""


class ArticleIn(Schema):
    title: str
    body: str
    category: str
    issue_id: str | None = None
    is_global: bool = False


class NotificationIn(Schema):
    user_id: int
    category: str
    title: str
    body: str
    severity: str = "normal"
    channels: list[str] = Field(default_factory=lambda: ["in_app"])
    mandatory_acknowledgement: bool = False
    custom_fields: dict = Field(default_factory=dict)


class CentreIn(Schema):
    code: str
    name: str
    location: str
    capacity: int
    schedule: dict = Field(default_factory=dict)
    security_controls: list[str] = Field(default_factory=list)
    supervisor_id: int | None = None
    scanner_ids: list[str] = Field(default_factory=list)
    workstation_count: int = 0


class ReadinessIn(Schema):
    scanner_ready: bool = False
    workstation_ready: bool = False
    network_ready: bool = False
    power_ready: bool = False
    secure_lan_ready: bool = False
    operators_ready: bool = False
    seat_plan: dict = Field(default_factory=dict)
    notes: str = ""


class CampIn(Schema):
    centre_id: str
    session_id: str
    name: str
    starts_at: datetime
    ends_at: datetime
    evaluator_ids: list[str] = Field(default_factory=list)


class RemunerationRuleIn(Schema):
    paper_id: str | None = None
    centre_id: str | None = None
    per_script: float = 0
    per_page: float = 0
    per_question: float = 0
    moderator_rate: float = 0
    chief_examiner_rate: float = 0
    revaluation_rate: float = 0
    slabs: list[dict] = Field(default_factory=list)
    minimum_payment: float = 0
    maximum_payment: float | None = None
    tax_percentage: float = 0


class RemunerationCalculateIn(Schema):
    evaluator_id: str
    session_id: str
    rule_id: str


class StudentRequestIn(Schema):
    identity_reference: str
    script_id: str
    purpose: str = "copy"
    release_mode: str = "masked"


class UniversityApiKeyIn(Schema):
    name: str
    source_system: str
    endpoint_id: str | None = None
    scopes: list[str] = Field(default_factory=lambda: ["photocopy:request", "revaluation:request", "recounting:request"])


class OfficialPortalPhotocopyIn(Schema):
    external_application_id: str
    identity_reference: str
    script_id: str
    purpose: str = "copy"
    release_mode: str = "masked"
    payload: dict = Field(default_factory=dict)


class DeliveryAcknowledgementIn(Schema):
    delivery_reference: str


class OfficialPortalRevaluationIn(Schema):
    external_application_id: str
    identity_reference: str
    script_id: str
    scope: str = "full"
    question_ids: list[str] = Field(default_factory=list)
    reason: str
    rule: str = "best"
    request_type: str = "revaluation"
    recounting_notes: str = ""
    payload: dict = Field(default_factory=dict)


class EvidenceIn(Schema):
    script_id: str
    purpose: str


class IntegrationIn(Schema):
    name: str
    kind: str
    base_url: str
    authentication: str
    secret_reference: str
    rate_limit_per_minute: int = 60
    webhook_events: list[str] = Field(default_factory=list)


class HandoverIn(Schema):
    endpoint_id: str
    final_mark_id: str
    idempotency_key: str


class AcknowledgeIn(Schema):
    version: int
    status: str
    acknowledgement_reference: str
    remote_snapshot: dict = Field(default_factory=dict)


class LocaleIn(Schema):
    locale: str
    additional_locales: list[str] = Field(default_factory=list)


class RecoveryPlanIn(Schema):
    name: str
    regions: list[str]
    database_replication: dict = Field(default_factory=dict)
    file_replication: dict = Field(default_factory=dict)
    rpo_minutes: int = 15
    rto_minutes: int = 60
    clean_environment: str


class DrillIn(Schema):
    plan_id: str
    drill_type: str
    recovery_point: dict = Field(default_factory=dict)


def _tenant(request, *roles):
    membership = require_roles(request, *(roles or READ_ROLES))
    return membership.institution.tenant_id


def _hash_api_key(raw_key):
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _official_portal_context(request, required_scope):
    raw_key = request.headers.get("X-ADMIEZO-API-KEY", "").strip()
    if not raw_key:
        raise HttpError(401, "Official portal API key is required")

    key_hash = _hash_api_key(raw_key)
    item = UniversityApiKey.objects.select_related("integration_endpoint").filter(
        key_hash=key_hash,
        status=UniversityApiKey.Status.ACTIVE,
    ).first()
    if not item or required_scope not in item.scopes:
        raise HttpError(403, "Official portal API key is invalid or lacks scope")

    item.last_used_at = timezone.now()
    item.save(update_fields=["last_used_at", "updated_at"])
    return item


def _secure_evaluator_context(request, assignment_id=None):
    membership = require_roles(request, Membership.Role.EVALUATOR)
    tenant_id = membership.institution.tenant_id
    evaluator = Evaluator.objects.filter(tenant_id=tenant_id, email__iexact=request.auth.email, status=Evaluator.Status.ACTIVE).first()
    assignment = None
    if assignment_id and evaluator:
        assignment = Assignment.objects.filter(id=assignment_id, tenant_id=tenant_id, evaluator=evaluator).first()
    if not evaluator or (assignment_id and not assignment):
        raise HttpError(404, "Evaluator assignment not found")
    return membership, evaluator, assignment


def _secure_session_data(item):
    return {
        "id": str(item.id),
        "assignment_id": str(item.assignment_id),
        "status": item.status,
        "pause_reason": item.pause_reason,
        "violation_count": item.violation_count,
        "policy": item.policy_snapshot,
        "last_heartbeat_at": item.last_heartbeat_at.isoformat(),
        "version": item.version,
    }


def _serialize(items, fields):
    result = []
    for item in items:
        row = {"id": str(item.id)}
        for field in fields:
            value = getattr(item, field)
            if hasattr(value, "isoformat"):
                value = value.isoformat()
            elif hasattr(value, "as_tuple"):
                value = float(value)
            elif field.endswith("_id") and value is not None:
                value = str(value)
            row[field] = value
        result.append(row)
    return result


@router.get("/catalog")
def catalog(request, section: str = ""):
    tenant_id = _tenant(request)
    users = User.objects.filter(admiezo_memberships__institution__tenant_id=tenant_id).distinct().order_by("first_name", "last_name")
    result = {
        "references": {
            "papers": _serialize(Paper.objects.filter(tenant_id=tenant_id).order_by("code"), ["code", "title", "moderation_required", "version"]),
            "evaluators": _serialize(Evaluator.objects.filter(tenant_id=tenant_id).order_by("evaluator_code"), ["evaluator_code", "display_name", "status", "daily_capacity", "version"]),
            "sessions": _serialize(ExamSession.objects.filter(tenant_id=tenant_id).order_by("-evaluation_starts_at"), ["name", "status", "evaluation_starts_at", "evaluation_ends_at"]),
            "scripts": _serialize(Script.objects.filter(tenant_id=tenant_id).order_by("script_code")[:300], ["script_code", "paper_id", "state", "page_count"]),
            "final_marks": _serialize(FinalMark.objects.filter(tenant_id=tenant_id).select_related("script").order_by("-created_at")[:300], ["script_id", "mark", "status", "checksum", "version"]),
            "valuation_results": _serialize(ValuationResult.objects.filter(tenant_id=tenant_id, is_locked=True).order_by("-created_at")[:300], ["script_id", "valuation_round", "total_marks", "checksum", "is_locked"]),
            "users": [{"id": str(item.id), "name": item.get_full_name() or item.username, "email": item.email} for item in users],
        },
        "monitoring": monitoring_snapshot(tenant_id),
        "productivity": productivity_snapshot(tenant_id),
        "active_evaluations": [
            {
                "id": str(item.id),
                "script": item.script.script_code,
                "paper": item.script.paper.code,
                "evaluator": item.evaluator.display_name,
                "status": item.status,
                "progress_percent": item.progress_percent,
                "started_at": item.started_at.isoformat() if item.started_at else None,
                "due_at": item.due_at.isoformat(),
            }
            for item in Assignment.objects.filter(
                tenant_id=tenant_id,
                status__in=[Assignment.Status.ACCEPTED, Assignment.Status.IN_PROGRESS],
            ).select_related("script__paper", "evaluator").order_by("due_at")[:200]
        ],
        "moderation_policies": _serialize(ModerationPolicy.objects.filter(tenant_id=tenant_id).select_related("paper"), ["paper_id", "sample_percentage", "sampling_modes", "mandatory", "version"]),
        "moderation_cases": _serialize(ModerationCase.objects.filter(tenant_id=tenant_id).select_related("script", "moderator"), ["script_id", "moderator_id", "sample_reasons", "status", "original_mark", "adjusted_mark", "version"]),
        "revaluations": _serialize(RevaluationRequest.objects.filter(tenant_id=tenant_id).select_related("script").order_by("-created_at")[:200], ["script_id", "request_type", "source_system", "external_application_id", "scope", "recounting_notes", "original_mark_snapshot", "new_mark", "mark_difference", "rule", "final_mark", "status", "received_at", "created_at", "version"]),
        "completions": _serialize(CompletionRecord.objects.filter(tenant_id=tenant_id).select_related("script", "final_mark"), ["script_id", "final_mark_id", "checks", "signature_digest", "status", "version"]),
        "authorizations": _serialize(ControlledAuthorization.objects.filter(tenant_id=tenant_id), ["kind", "final_mark_id", "purpose", "proposed_change", "expires_at", "status", "version"]),
        "presence_events": _serialize(PresenceSecurityEvent.objects.filter(tenant_id=tenant_id).order_by("-created_at")[:100], ["assignment_id", "category", "severity", "action", "created_at"]),
        "secure_sessions": _serialize(SecureEvaluationSession.objects.filter(tenant_id=tenant_id).order_by("-started_at")[:100], ["assignment_id", "evaluator_id", "status", "pause_reason", "violation_count", "started_at", "last_heartbeat_at", "ended_at", "version"]),
        "proctoring_reviews": [
            {
                "id": str(item.id),
                "session_id": str(item.secure_session_id),
                "assignment_id": str(item.secure_session.assignment_id),
                "event_id": str(item.event_id),
                "category": item.event.category,
                "severity": item.event.severity,
                "status": item.status,
                "decision_note": item.decision_note,
                "evidence_count": item.secure_session.evidence.filter(status=ProctoringEvidence.Status.VERIFIED).count(),
                "created_at": item.created_at.isoformat(),
                "version": item.version,
            }
            for item in ProctoringReview.objects.filter(tenant_id=tenant_id).select_related("secure_session", "event").order_by("-created_at")[:100]
        ],
        "attendance": _serialize(AttendanceRecord.objects.filter(tenant_id=tenant_id).order_by("-checked_in_at")[:200], ["evaluator_id", "session_id", "role", "checked_in_at", "checked_out_at", "active_seconds", "idle_seconds", "exception"]),
        "workload_actions": _serialize(WorkloadAction.objects.filter(tenant_id=tenant_id).order_by("-created_at")[:100], ["evaluator_id", "action", "reason", "metrics", "status", "version"]),
        "runtime_incidents": _serialize(RuntimeIncident.objects.filter(tenant_id=tenant_id).order_by("-detected_at")[:100], ["service", "category", "severity", "status", "retry_count", "detected_at", "recovered_at", "version"]),
        "issues": _serialize(OperationalIssue.objects.filter(tenant_id=tenant_id).order_by("priority", "sla_due_at")[:200], ["issue_type", "title", "classification", "priority", "status", "owner_id", "sla_due_at", "resolution", "resolution_locked", "version"]),
        "knowledge": _serialize(KnowledgeArticle.objects.filter(tenant_id=tenant_id).order_by("-created_at")[:100], ["title", "category", "issue_id", "is_global", "version"]),
        "notifications": _serialize(NotificationDelivery.objects.filter(tenant_id=tenant_id).order_by("-created_at")[:200], ["user_id", "category", "title", "severity", "channels", "status", "mandatory_acknowledgement", "attempt_count", "version"]),
        "centres": _serialize(CentreProfile.objects.filter(tenant_id=tenant_id), ["code", "name", "location", "capacity", "workstation_count", "status", "version"]),
        "readiness": _serialize(CentreReadiness.objects.filter(tenant_id=tenant_id).order_by("-created_at")[:100], ["centre_id", "decision", "scanner_ready", "workstation_ready", "network_ready", "power_ready", "secure_lan_ready", "operators_ready", "notes"]),
        "camps": _serialize(EvaluationCamp.objects.filter(tenant_id=tenant_id), ["centre_id", "session_id", "name", "starts_at", "ends_at", "status", "performance", "version"]),
        "remuneration_rules": _serialize(RemunerationRule.objects.filter(tenant_id=tenant_id), ["paper_id", "centre_id", "per_script", "per_page", "per_question", "minimum_payment", "maximum_payment", "tax_percentage", "version"]),
        "statements": _serialize(RemunerationStatement.objects.filter(tenant_id=tenant_id).order_by("-created_at")[:200], ["evaluator_id", "session_id", "units", "gross_amount", "deductions", "net_amount", "status", "payment_reference", "version"]),
        "student_requests": _serialize(StudentScriptRequest.objects.filter(tenant_id=tenant_id).order_by("-created_at")[:200], ["script_id", "purpose", "release_mode", "source_system", "external_application_id", "eligibility", "status", "expires_at", "download_allowed", "access_count", "delivered_at", "delivery_reference", "revoked_at", "reissued_from_id", "created_at", "received_at", "version"]),
        "evidence_packages": _serialize(EvidencePackage.objects.filter(tenant_id=tenant_id).order_by("-created_at")[:100], ["script_id", "purpose", "event_count", "digest", "status", "version"]),
        "integrations": _serialize(IntegrationEndpoint.objects.filter(tenant_id=tenant_id), ["name", "kind", "base_url", "authentication", "rate_limit_per_minute", "webhook_events", "status", "version"]),
        "university_api_keys": _serialize(UniversityApiKey.objects.filter(tenant_id=tenant_id), ["name", "key_prefix", "source_system", "integration_endpoint_id", "scopes", "status", "last_used_at", "version"]),
        "handovers": _serialize(ResultHandover.objects.filter(tenant_id=tenant_id).order_by("-created_at")[:200], ["endpoint_id", "final_mark_id", "payload_digest", "status", "attempt_count", "acknowledgement_reference", "differences", "version"]),
        "recovery_plans": _serialize(RecoveryPlan.objects.filter(tenant_id=tenant_id), ["name", "regions", "rpo_minutes", "rto_minutes", "clean_environment", "status", "version"]),
        "recovery_drills": _serialize(RecoveryDrill.objects.filter(tenant_id=tenant_id).order_by("-created_at")[:100], ["plan_id", "drill_type", "status", "measurements", "integrity_checks", "report", "corrective_actions", "version"]),
    }
    section_fields = {
        "assessment": {"moderation_policies", "moderation_cases", "revaluations", "completions", "authorizations"},
        "operations": {"monitoring", "productivity", "active_evaluations", "presence_events", "secure_sessions", "proctoring_reviews", "attendance", "workload_actions", "runtime_incidents", "issues", "knowledge", "notifications", "centres", "readiness", "camps"},
        "services": {"remuneration_rules", "statements", "student_requests"},
        "platform": {"evidence_packages", "integrations", "university_api_keys", "handovers", "recovery_plans", "recovery_drills"},
    }
    if section in section_fields:
        allowed = section_fields[section] | {"references"}
        return {key: value for key, value in result.items() if key in allowed}
    return result


@router.put("/moderation/policies")
def moderation_policy(request, payload: ModerationPolicyIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    paper = Paper.objects.filter(id=payload.paper_id, tenant_id=tenant_id).first()
    if not paper:
        raise HttpError(404, "Paper not found")
    item = save_moderation_policy(tenant_id=tenant_id, actor_id=request.auth.id, paper=paper, expected_version=payload.version, values=payload.dict(exclude={"paper_id", "version"}))
    return {"id": str(item.id), "version": item.version}


@router.post("/moderation/policies/{policy_id}/sample")
def moderation_sample(request, policy_id: str):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    policy = ModerationPolicy.objects.filter(id=policy_id, tenant_id=tenant_id).first()
    if not policy:
        raise HttpError(404, "Moderation policy not found")
    items = sample_moderation_cases(tenant_id=tenant_id, actor_id=request.auth.id, policy=policy)
    return {"created": len(items), "ids": [str(item.id) for item in items]}


@router.post("/moderation/cases/{case_id}/action")
def moderation_action(request, case_id: str, payload: VersionActionIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    evaluator = Evaluator.objects.filter(id=payload.values.get("moderator_id"), tenant_id=tenant_id).first() if payload.values.get("moderator_id") else None
    item = transition_moderation(tenant_id=tenant_id, actor_id=request.auth.id, case_id=case_id, expected_version=payload.version, target=payload.target, moderator=evaluator, adjusted_mark=payload.values.get("adjusted_mark"), reason=payload.reason, snapshot=payload.values.get("mark_snapshot"))
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/revaluation/requests")
def revaluation_create(request, payload: RevaluationIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    script = Script.objects.filter(id=payload.script_id, tenant_id=tenant_id).first()
    if not script:
        raise HttpError(404, "Script not found")
    item = create_revaluation(tenant_id=tenant_id, actor_id=request.auth.id, script=script, identity_reference=payload.identity_reference, scope=payload.scope, question_ids=payload.question_ids, reason=payload.reason, rule=payload.rule, request_type=payload.request_type, recounting_notes=payload.recounting_notes)
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/revaluation/requests/{request_id}/action")
def revaluation_action(request, request_id: str, payload: VersionActionIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    evaluator = Evaluator.objects.filter(id=payload.values.get("evaluator_id"), tenant_id=tenant_id).first() if payload.values.get("evaluator_id") else None
    result = ValuationResult.objects.filter(id=payload.values.get("result_id"), tenant_id=tenant_id, is_locked=True).first() if payload.values.get("result_id") else None
    item = transition_revaluation(tenant_id=tenant_id, actor_id=request.auth.id, request_id=request_id, expected_version=payload.version, target=payload.target, evaluator=evaluator, new_result=result)
    return {"id": str(item.id), "status": item.status, "final_mark": float(item.final_mark) if item.final_mark is not None else None, "version": item.version}


@router.post("/completion/final-marks/{final_mark_id}/review")
def completion_review(request, final_mark_id: str):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    final = FinalMark.objects.filter(id=final_mark_id, tenant_id=tenant_id).select_related("script__paper").first()
    if not final:
        raise HttpError(404, "Final mark not found")
    item = review_completion(tenant_id=tenant_id, actor_id=request.auth.id, final_mark=final)
    return {"id": str(item.id), "status": item.status, "checks": item.checks, "version": item.version}


@router.post("/completion/records/{record_id}/sign")
def completion_sign(request, record_id: str, payload: CompletionSignIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    item = declare_and_sign(tenant_id=tenant_id, actor_id=request.auth.id, record_id=record_id, expected_version=payload.version, declaration=payload.declaration, confirm=payload.confirm)
    return {"id": str(item.id), "status": item.status, "signature_digest": item.signature_digest, "version": item.version}


@router.post("/completion/authorizations")
def authorization_create(request, payload: AuthorizationIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    if payload.kind == ControlledAuthorization.Kind.POST_LOCK_CHANGE:
        require_step_up(request)
    final = FinalMark.objects.filter(id=payload.final_mark_id, tenant_id=tenant_id).first() if payload.final_mark_id else None
    if payload.final_mark_id and not final:
        raise HttpError(404, "Final mark not found")
    item = request_authorization(tenant_id=tenant_id, actor_id=request.auth.id, kind=payload.kind, final_mark=final, purpose=payload.purpose, proposed_change=payload.proposed_change, expires_at=payload.expires_at or timezone.now() + timedelta(minutes=10))
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/completion/authorizations/{authorization_id}/decision")
def authorization_decision(request, authorization_id: str, payload: DecisionIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    require_step_up(request)
    item = decide_authorization(tenant_id=tenant_id, actor_id=request.auth.id, authorization_id=authorization_id, expected_version=payload.version, approve=payload.approve)
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/completion/authorizations/{authorization_id}/consume")
def authorization_consume(request, authorization_id: str, payload: VersionActionIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    require_step_up(request)
    item = consume_authorization(tenant_id=tenant_id, actor_id=request.auth.id, authorization_id=authorization_id, expected_version=payload.version)
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.get("/remote-security/policy")
def secure_evaluation_policy(request):
    membership, _, _ = _secure_evaluator_context(request)
    return security_policy_snapshot(membership.institution.tenant_id)


@router.post("/remote-security/sessions")
def secure_evaluation_start(request, payload: SecureSessionIn):
    membership, evaluator, assignment = _secure_evaluator_context(request, payload.assignment_id)
    existing = SecureEvaluationSession.objects.select_related("assignment", "assignment__script").filter(
        tenant_id=membership.institution.tenant_id,
        evaluator=evaluator,
        status__in=[SecureEvaluationSession.Status.ACTIVE, SecureEvaluationSession.Status.PAUSED],
    ).order_by("-started_at").first()
    if existing and existing.assignment_id != assignment.id:
        script_code = getattr(existing.assignment.script, "script_code", "")
        suffix = f" Current assignment: {script_code}." if script_code else ""
        raise HttpError(409, f"Finish or exit the current evaluation before opening another paper.{suffix}")
    if existing and existing.access_session_id == request.access_session.id:
        return _secure_session_data(existing)
    item = start_secure_evaluation_session(
        tenant_id=membership.institution.tenant_id,
        actor_id=request.auth.id,
        assignment=assignment,
        evaluator=evaluator,
        access_session=request.access_session,
        session_fingerprint=payload.session_fingerprint,
        device_fingerprint=payload.device_fingerprint,
        consent=payload.consent,
        preflight=payload.preflight,
        device_inventory=payload.device_inventory,
    )
    return _secure_session_data(item)


@router.post("/remote-security/events")
def presence_event(request, payload: PresenceEventIn):
    membership, evaluator, assignment = _secure_evaluator_context(request, payload.assignment_id)
    tenant_id = membership.institution.tenant_id
    if payload.severity not in {"low", "medium", "high", "critical"}:
        raise HttpError(422, "Unsupported event severity")
    secure_session = None
    if payload.secure_session_id:
        secure_session = SecureEvaluationSession.objects.filter(id=payload.secure_session_id, tenant_id=tenant_id, assignment=assignment, evaluator=evaluator, access_session_id=request.access_session.id).first()
        if not secure_session:
            raise HttpError(404, "Secure evaluation session not found")
    item = record_presence_event(tenant_id=tenant_id, actor_id=request.auth.id, assignment=assignment, access_session=request.access_session, category=payload.category, severity=payload.severity, device_fingerprint=payload.device_fingerprint, session_fingerprint=payload.session_fingerprint, details=payload.details, secure_session=secure_session)
    if secure_session:
        secure_session.refresh_from_db()
    return {"id": str(item.id), "action": item.action, "session": _secure_session_data(secure_session) if secure_session else None}


@router.post("/remote-security/sessions/{session_id}/heartbeat")
def secure_evaluation_heartbeat(request, session_id: str, payload: PostureIn):
    membership, _, _ = _secure_evaluator_context(request)
    item, event = heartbeat_secure_evaluation_session(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, session_id=session_id, access_session=request.access_session, posture=payload.posture)
    item.refresh_from_db()
    return {"session": _secure_session_data(item), "action": event.action if event else ("pause" if item.status == SecureEvaluationSession.Status.PAUSED else "record")}


@router.post("/remote-security/sessions/{session_id}/resume")
def secure_evaluation_resume(request, session_id: str, payload: PostureIn):
    membership, _, _ = _secure_evaluator_context(request)
    item = resume_secure_evaluation_session(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, session_id=session_id, access_session=request.access_session, posture=payload.posture)
    return _secure_session_data(item)


@router.post("/remote-security/sessions/{session_id}/finish")
def secure_evaluation_finish(request, session_id: str, payload: SecureSessionFinishIn):
    membership, _, _ = _secure_evaluator_context(request)
    item = finish_secure_evaluation_session(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, session_id=session_id, access_session=request.access_session, completed=payload.completed)
    return _secure_session_data(item)


@router.post("/remote-security/sessions/{session_id}/evidence")
def proctoring_evidence_intent(request, session_id: str, payload: EvidenceIntentIn):
    membership, evaluator, _ = _secure_evaluator_context(request)
    tenant_id = membership.institution.tenant_id
    secure_session = SecureEvaluationSession.objects.filter(id=session_id, tenant_id=tenant_id, evaluator=evaluator, access_session_id=request.access_session.id).first()
    if not secure_session:
        raise HttpError(404, "Secure evaluation session not found")
    if payload.mime_type not in {"video/webm", "video/webm;codecs=vp8", "video/webm;codecs=vp9", "video/mp4"}:
        raise HttpError(422, "Unsupported evidence media type")
    if not 1 <= payload.max_bytes <= 20_000_000 or payload.captured_to <= payload.captured_from:
        raise HttpError(422, "Evidence size or capture interval is invalid")
    extension = "mp4" if payload.mime_type == "video/mp4" else "webm"
    storage_key = f"proctoring/{tenant_id}/{secure_session.id}/{payload.sequence}-{uuid4()}.{extension}"
    item = create_proctoring_evidence(tenant_id=tenant_id, actor_id=request.auth.id, secure_session=secure_session, sequence=payload.sequence, reason=payload.reason, storage_key=storage_key, mime_type=payload.mime_type, captured_from=payload.captured_from, captured_to=payload.captured_to, retention_days=int(secure_session.policy_snapshot.get("retention_days", 30)))
    upload_url, expires_at = signed_object_url(method="PUT", key=storage_key, content_type=payload.mime_type, max_bytes=payload.max_bytes)
    return {"id": str(item.id), "upload_url": upload_url, "expires_at": expires_at, "max_bytes": payload.max_bytes}


@router.post("/remote-security/evidence/{evidence_id}/complete")
def proctoring_evidence_complete(request, evidence_id: str, payload: EvidenceCompleteIn):
    membership, evaluator, _ = _secure_evaluator_context(request)
    evidence = ProctoringEvidence.objects.filter(id=evidence_id, tenant_id=membership.institution.tenant_id, secure_session__evaluator=evaluator, secure_session__access_session_id=request.access_session.id).first()
    if not evidence:
        raise HttpError(404, "Evidence upload not found")
    try:
        metadata = read_object_metadata(evidence.storage_key)
    except OSError as exc:
        raise HttpError(503, "Evidence storage verification is unavailable") from exc
    item = verify_proctoring_evidence(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, evidence_id=evidence.id, sha256=payload.sha256, byte_size=payload.byte_size, stored_sha256=metadata.sha256, stored_size=metadata.byte_size)
    return {"id": str(item.id), "status": item.status, "sha256": item.sha256}


@router.get("/remote-security/reviews/{review_id}/evidence")
def proctoring_review_evidence(request, review_id: str):
    tenant_id = _tenant(request)
    review = ProctoringReview.objects.filter(id=review_id, tenant_id=tenant_id).select_related("secure_session").first()
    if not review:
        raise HttpError(404, "Security review not found")
    now = timezone.now()
    return {
        "items": [
            {"id": str(item.id), "reason": item.reason, "url": signed_object_url(method="GET", key=item.storage_key)[0], "mime_type": item.mime_type, "captured_from": item.captured_from.isoformat(), "captured_to": item.captured_to.isoformat(), "sha256": item.sha256}
            for item in review.secure_session.evidence.filter(status=ProctoringEvidence.Status.VERIFIED, retention_until__gt=now).order_by("sequence")
        ]
    }


@router.post("/remote-security/reviews/{review_id}/action")
def proctoring_review_action(request, review_id: str, payload: ProctoringReviewIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    item = decide_proctoring_review(tenant_id=tenant_id, actor_id=request.auth.id, review_id=review_id, expected_version=payload.version, status=payload.status, note=payload.note)
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/monitoring/attendance")
def attendance(request, payload: AttendanceIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    evaluator = Evaluator.objects.filter(id=payload.evaluator_id, tenant_id=tenant_id).first()
    session = ExamSession.objects.filter(id=payload.session_id, tenant_id=tenant_id).first()
    if not evaluator or not session:
        raise HttpError(404, "Evaluator or session not found")
    with transaction.atomic():
        if payload.action == "check_in":
            item = AttendanceRecord.objects.create(tenant_id=tenant_id, evaluator=evaluator, session=session, role=payload.role, checked_in_at=timezone.now(), exception=payload.exception)
        else:
            item = AttendanceRecord.objects.select_for_update().filter(tenant_id=tenant_id, evaluator=evaluator, session=session, checked_out_at__isnull=True).order_by("-checked_in_at").first()
            if not item:
                raise HttpError(409, "No active attendance record")
            item.checked_out_at = timezone.now()
            item.active_seconds = payload.active_seconds
            item.idle_seconds = payload.idle_seconds
            item.exception = payload.exception
            item.save()
        record_event(tenant_id=tenant_id, actor_id=request.auth.id, action=f"monitoring.attendance.{payload.action}", aggregate="AttendanceRecord", aggregate_id=item.id, payload={"evaluator_id": str(evaluator.id), "session_id": str(session.id)})
    return {"id": str(item.id), "checked_out_at": item.checked_out_at.isoformat() if item.checked_out_at else None}


@router.post("/workload/actions")
def workload_create(request, payload: WorkloadIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    evaluator = Evaluator.objects.filter(id=payload.evaluator_id, tenant_id=tenant_id).first()
    if not evaluator:
        raise HttpError(404, "Evaluator not found")
    item = create_workload_action(tenant_id=tenant_id, actor_id=request.auth.id, evaluator=evaluator, action=payload.action, reason=payload.reason, metrics=payload.metrics)
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/workload/actions/{action_id}/decision")
def workload_decision(request, action_id: str, payload: VersionActionIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    item = transition_workload(tenant_id=tenant_id, actor_id=request.auth.id, action_id=action_id, expected_version=payload.version, target=payload.target)
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/runtime/incidents")
def runtime_create(request, payload: RuntimeIncidentIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    with transaction.atomic():
        item = RuntimeIncident.objects.create(tenant_id=tenant_id, service=payload.service, category=payload.category, severity=payload.severity, last_confirmed_state=payload.last_confirmed_state, recovery_point=payload.recovery_point, details=payload.details, detected_at=timezone.now())
        record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="runtime.incident.detected", aggregate="RuntimeIncident", aggregate_id=item.id, payload={"service": item.service, "category": item.category, "severity": item.severity})
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/runtime/incidents/{incident_id}/action")
def runtime_action(request, incident_id: str, payload: VersionActionIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    item = transition_runtime(tenant_id=tenant_id, actor_id=request.auth.id, incident_id=incident_id, expected_version=payload.version, target=payload.target)
    return {"id": str(item.id), "status": item.status, "retry_count": item.retry_count, "version": item.version}


@router.post("/issues")
def issue_create(request, payload: IssueIn):
    membership = membership_for(request)
    tenant_id = membership.institution.tenant_id
    paper = Paper.objects.filter(id=payload.paper_id, tenant_id=tenant_id).first() if payload.paper_id else None
    item = create_issue(tenant_id=tenant_id, actor_id=request.auth.id, issue_type=payload.issue_type, title=payload.title, description=payload.description, paper=paper, question_reference=payload.question_reference)
    return {"id": str(item.id), "status": item.status, "priority": item.priority, "duplicate_of": str(item.duplicate_of_id) if item.duplicate_of_id else None, "version": item.version}


@router.post("/issues/{issue_id}/action")
def issue_action(request, issue_id: str, payload: VersionActionIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    item = transition_issue(tenant_id=tenant_id, actor_id=request.auth.id, issue_id=issue_id, expected_version=payload.version, target=payload.target, owner_id=payload.values.get("owner_id"), resolution=payload.reason)
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/issues/knowledge")
def article_create(request, payload: ArticleIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    issue = OperationalIssue.objects.filter(id=payload.issue_id, tenant_id=tenant_id).first() if payload.issue_id else None
    item = publish_article(tenant_id=tenant_id, actor_id=request.auth.id, title=payload.title, body=payload.body, category=payload.category, issue=issue, is_global=payload.is_global)
    return {"id": str(item.id), "version": item.version}


@router.post("/notifications")
def notification_create(request, payload: NotificationIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    custom_fields = validate_custom_values(tenant_id=tenant_id, form_key="notification", values=payload.custom_fields)
    if not User.objects.filter(id=payload.user_id, admiezo_memberships__institution__tenant_id=tenant_id).exists():
        raise HttpError(404, "Recipient not found")
    item = create_notification(tenant_id=tenant_id, actor_id=request.auth.id, **payload.dict(exclude={"custom_fields"}))
    persist_custom_values(tenant_id=tenant_id, actor_id=request.auth.id, form_key="notification", record_id=item.id, values=custom_fields)
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/notifications/{notification_id}/action")
def delivery_action(request, notification_id: str, payload: VersionActionIn):
    membership = membership_for(request)
    tenant_id = membership.institution.tenant_id
    item = NotificationDelivery.objects.filter(id=notification_id, tenant_id=tenant_id).first()
    if not item or (membership.role not in ADMIN_ROLES and item.user_id != request.auth.id):
        raise HttpError(404, "Notification not found")
    item = notification_action(tenant_id=tenant_id, actor_id=request.auth.id, notification_id=notification_id, expected_version=payload.version, action=payload.target, error=payload.reason)
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/centres")
def centre_create(request, payload: CentreIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    with transaction.atomic():
        item = CentreProfile.objects.create(tenant_id=tenant_id, **payload.dict())
        record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="centres.created", aggregate="CentreProfile", aggregate_id=item.id, payload={"code": item.code, "capacity": item.capacity})
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/centres/{centre_id}/readiness")
def centre_readiness(request, centre_id: str, payload: ReadinessIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    centre = CentreProfile.objects.filter(id=centre_id, tenant_id=tenant_id).first()
    if not centre:
        raise HttpError(404, "Centre not found")
    item = assess_centre(tenant_id=tenant_id, actor_id=request.auth.id, centre=centre, values=payload.dict())
    return {"id": str(item.id), "decision": item.decision, "centre_status": centre.status}


@router.post("/centres/{centre_id}/action")
def centre_action(request, centre_id: str, payload: VersionActionIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    item = transition_centre(tenant_id=tenant_id, actor_id=request.auth.id, centre_id=centre_id, expected_version=payload.version, target=payload.target)
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/centres/camps")
def camp_create(request, payload: CampIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    centre = CentreProfile.objects.filter(id=payload.centre_id, tenant_id=tenant_id).first()
    session = ExamSession.objects.filter(id=payload.session_id, tenant_id=tenant_id).first()
    if not centre or not session or payload.ends_at <= payload.starts_at:
        raise HttpError(422, "Valid centre, session, and schedule are required")
    with transaction.atomic():
        item = EvaluationCamp.objects.create(tenant_id=tenant_id, centre=centre, session=session, name=payload.name, starts_at=payload.starts_at, ends_at=payload.ends_at, evaluator_ids=payload.evaluator_ids)
        record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="centres.camp.created", aggregate="EvaluationCamp", aggregate_id=item.id, payload={"centre_id": str(centre.id), "session_id": str(session.id)})
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/centres/camps/{camp_id}/action")
def camp_action(request, camp_id: str, payload: VersionActionIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    item = transition_camp(tenant_id=tenant_id, actor_id=request.auth.id, camp_id=camp_id, expected_version=payload.version, target=payload.target, incidents=payload.values.get("incidents"), performance=payload.values.get("performance"))
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/remuneration/rules")
def remuneration_rule(request, payload: RemunerationRuleIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    paper = Paper.objects.filter(id=payload.paper_id, tenant_id=tenant_id).first() if payload.paper_id else None
    centre = CentreProfile.objects.filter(id=payload.centre_id, tenant_id=tenant_id).first() if payload.centre_id else None
    with transaction.atomic():
        item = RemunerationRule.objects.create(tenant_id=tenant_id, paper=paper, centre=centre, **payload.dict(exclude={"paper_id", "centre_id"}))
        record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="remuneration.rule.created", aggregate="RemunerationRule", aggregate_id=item.id, payload={"paper_id": str(paper.id) if paper else None})
    return {"id": str(item.id), "version": item.version}


@router.post("/remuneration/calculate")
def remuneration_calculate(request, payload: RemunerationCalculateIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    evaluator = Evaluator.objects.filter(id=payload.evaluator_id, tenant_id=tenant_id).first()
    session = ExamSession.objects.filter(id=payload.session_id, tenant_id=tenant_id).first()
    rule = RemunerationRule.objects.filter(id=payload.rule_id, tenant_id=tenant_id).first()
    if not evaluator or not session or not rule:
        raise HttpError(404, "Evaluator, session, or rule not found")
    item = calculate_remuneration(tenant_id=tenant_id, actor_id=request.auth.id, evaluator=evaluator, session=session, rule=rule)
    return {"id": str(item.id), "gross": float(item.gross_amount), "deductions": float(item.deductions), "net": float(item.net_amount), "status": item.status, "version": item.version}


@router.post("/remuneration/statements/{statement_id}/action")
def remuneration_action(request, statement_id: str, payload: VersionActionIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    item = transition_statement(tenant_id=tenant_id, actor_id=request.auth.id, statement_id=statement_id, expected_version=payload.version, target=payload.target, payment_reference=payload.values.get("payment_reference", ""))
    return {"id": str(item.id), "status": item.status, "payment_reference": item.payment_reference, "version": item.version}


@router.post("/student/requests")
def student_request(request, payload: StudentRequestIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    script = Script.objects.filter(id=payload.script_id, tenant_id=tenant_id).first()
    if not script:
        raise HttpError(404, "Script not found")
    item = create_student_request(tenant_id=tenant_id, actor_id=request.auth.id, identity_reference=payload.identity_reference, script=script, purpose=payload.purpose, release_mode=payload.release_mode)
    return {"id": str(item.id), "status": item.status, "eligibility": item.eligibility, "version": item.version}


@router.post("/student/requests/{request_id}/decision")
def student_decision(request, request_id: str, payload: DecisionIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    with transaction.atomic():
        item = StudentScriptRequest.objects.select_for_update().filter(id=request_id, tenant_id=tenant_id, status=StudentScriptRequest.Status.REQUESTED).first()
        if not item or item.version != payload.version:
            raise HttpError(409, "Student request is missing, stale, or already decided")
        if payload.approve and payload.release_mode == StudentScriptRequest.ReleaseMode.UNMASKED_IDENTITY:
            raise HttpError(409, "Unmasked identity delivery is blocked until a verified evaluator-redacted release asset exists")
        item.release_mode = payload.release_mode
        item.status = StudentScriptRequest.Status.APPROVED if payload.approve else StudentScriptRequest.Status.REJECTED
        item.approved_by_id = request.auth.id if payload.approve else None
        item.expires_at = timezone.now() + timedelta(days=7) if payload.approve else None
        item.version += 1
        item.save()
        record_event(tenant_id=tenant_id, actor_id=request.auth.id, action=f"student.request.{item.status}", aggregate="StudentScriptRequest", aggregate_id=item.id, payload={"script_id": str(item.script_id)})
    return {"id": str(item.id), "status": item.status, "expires_at": item.expires_at.isoformat() if item.expires_at else None, "version": item.version}


@router.post("/student/requests/{request_id}/action")
def student_request_action(request, request_id: str, payload: VersionActionIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    with transaction.atomic():
        item = StudentScriptRequest.objects.select_for_update().filter(id=request_id, tenant_id=tenant_id).first()
        if not item or item.version != payload.version:
            raise HttpError(409, "Photocopy request is missing or stale")
        if payload.target == "revoke":
            if item.status not in {StudentScriptRequest.Status.APPROVED, StudentScriptRequest.Status.AVAILABLE}:
                raise HttpError(409, "Only an approved or available photocopy can be revoked")
            item.status = StudentScriptRequest.Status.REVOKED
            item.revoked_at = timezone.now()
            item.download_allowed = False
            item.version += 1
            item.save(update_fields=["status", "revoked_at", "download_allowed", "version", "updated_at"])
            record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="student.copy.revoked", aggregate="StudentScriptRequest", aggregate_id=item.id, payload={"reason": payload.reason})
            return {"id": str(item.id), "status": item.status, "version": item.version}
        if payload.target == "reissue":
            if item.status not in {StudentScriptRequest.Status.EXPIRED, StudentScriptRequest.Status.REVOKED}:
                raise HttpError(409, "Only an expired or revoked photocopy can be reissued")
            replacement = create_student_request(tenant_id=tenant_id, actor_id=request.auth.id, identity_reference=item.identity_reference, script=item.script, purpose=item.purpose, source_system=item.source_system, external_payload={**item.external_payload, "reissued_from": str(item.id)}, integration_endpoint=item.integration_endpoint, release_mode=item.release_mode)
            replacement.reissued_from = item
            replacement.save(update_fields=["reissued_from", "updated_at"])
            record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="student.copy.reissued", aggregate="StudentScriptRequest", aggregate_id=replacement.id, payload={"reissued_from": str(item.id), "reason": payload.reason})
            return {"id": str(replacement.id), "reissued_from": str(item.id), "status": replacement.status, "version": replacement.version}
        raise HttpError(422, "Unsupported photocopy action")


@router.get("/student/requests/{request_id}/viewer")
def student_viewer(request, request_id: str):
    tenant_id = _tenant(request, *READ_ROLES)
    item = StudentScriptRequest.objects.filter(id=request_id, tenant_id=tenant_id, status__in=[StudentScriptRequest.Status.APPROVED, StudentScriptRequest.Status.AVAILABLE]).select_related("script").first()
    if not item:
        raise HttpError(404, "Student access is unavailable or expired")
    if not item.expires_at:
        raise HttpError(404, "Student access is unavailable or expired")
    if item.expires_at <= timezone.now():
        StudentScriptRequest.objects.filter(id=item.id, status__in=[StudentScriptRequest.Status.APPROVED, StudentScriptRequest.Status.AVAILABLE]).update(status=StudentScriptRequest.Status.EXPIRED, download_allowed=False, version=item.version + 1, updated_at=timezone.now())
        raise HttpError(404, "Student access is unavailable or expired")
    assets = ScriptAsset.objects.filter(tenant_id=tenant_id, script=item.script, kind=ScriptAsset.Kind.EVALUATION, deleted_at__isnull=True).order_by("page_number")
    pages = []
    for asset in assets:
        url, expires = signed_object_url(method="GET", key=asset.storage_key, ttl_seconds=300)
        pages.append({"page_number": asset.page_number, "url": url, "expires_at": expires, "sha256": asset.sha256})
    with transaction.atomic():
        locked = StudentScriptRequest.objects.select_for_update().get(id=item.id)
        locked.access_count += 1
        locked.status = StudentScriptRequest.Status.AVAILABLE
        locked.save()
        record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="student.script.viewed", aggregate="StudentScriptRequest", aggregate_id=locked.id, payload={"script_id": str(locked.script_id), "page_count": len(pages)})
    return {"request_id": str(item.id), "pages": pages, "watermark": f"ADMIEZO · {item.identity_reference[-8:]} · {timezone.now().isoformat()}", "download_allowed": item.download_allowed, "ttl_seconds": 300}


@router.get("/official-portal/photocopy-requests/{request_id}/download", auth=None)
def official_portal_photocopy_download(request, request_id: str):
    api_key = _official_portal_context(request, "photocopy:request")
    item = StudentScriptRequest.objects.filter(
        id=request_id,
        tenant_id=api_key.tenant_id,
        source_system=api_key.source_system,
        status__in=[StudentScriptRequest.Status.APPROVED, StudentScriptRequest.Status.AVAILABLE, StudentScriptRequest.Status.DELIVERED],
    ).select_related("script").first()
    if not item or not item.expires_at:
        raise HttpError(404, "Approved photocopy delivery is unavailable or expired")
    if item.expires_at <= timezone.now():
        StudentScriptRequest.objects.filter(id=item.id, status__in=[StudentScriptRequest.Status.APPROVED, StudentScriptRequest.Status.AVAILABLE]).update(status=StudentScriptRequest.Status.EXPIRED, download_allowed=False, version=item.version + 1, updated_at=timezone.now())
        raise HttpError(404, "Approved photocopy delivery is unavailable or expired")
    if item.release_mode == StudentScriptRequest.ReleaseMode.UNMASKED_IDENTITY:
        raise HttpError(409, "Unmasked identity delivery is blocked until a verified evaluator-redacted release asset exists")
    assets = ScriptAsset.objects.filter(tenant_id=api_key.tenant_id, script=item.script, kind=ScriptAsset.Kind.EVALUATION, deleted_at__isnull=True).order_by("page_number")
    pages = []
    for asset in assets:
        url, expires = signed_object_url(method="GET", key=asset.storage_key, ttl_seconds=300)
        pages.append({"page_number": asset.page_number, "url": url, "expires_at": expires, "sha256": asset.sha256})
    with transaction.atomic():
        locked = StudentScriptRequest.objects.select_for_update().get(id=item.id)
        locked.access_count += 1
        locked.status = StudentScriptRequest.Status.AVAILABLE
        locked.save(update_fields=["access_count", "status", "updated_at"])
    return {"request_id": str(item.id), "external_application_id": item.external_application_id, "release_mode": item.release_mode, "evaluator_marks_included": False, "watermark": f"ADMIEZO-{item.external_application_id or item.id}-{timezone.now().isoformat()}", "pages": pages, "ttl_seconds": 300}


@router.post("/official-portal/photocopy-requests/{request_id}/acknowledge", auth=None)
def official_portal_photocopy_acknowledge(request, request_id: str, payload: DeliveryAcknowledgementIn):
    api_key = _official_portal_context(request, "photocopy:request")
    with transaction.atomic():
        item = StudentScriptRequest.objects.select_for_update().filter(id=request_id, tenant_id=api_key.tenant_id, source_system=api_key.source_system, status__in=[StudentScriptRequest.Status.APPROVED, StudentScriptRequest.Status.AVAILABLE]).first()
        if not item:
            raise HttpError(404, "Photocopy request is not ready for acknowledgement")
        if not item.expires_at or item.expires_at <= timezone.now():
            item.status = StudentScriptRequest.Status.EXPIRED
            item.download_allowed = False
            item.version += 1
            item.save(update_fields=["status", "download_allowed", "version", "updated_at"])
            record_event(tenant_id=item.tenant_id, actor_id=0, action="student.copy.expired", aggregate="StudentScriptRequest", aggregate_id=item.id, payload={"expired_at": item.expires_at.isoformat() if item.expires_at else None})
            raise HttpError(404, "Photocopy request is expired")
        reference = payload.delivery_reference.strip()
        if len(reference) < 3:
            raise HttpError(422, "A delivery reference is required")
        item.status = StudentScriptRequest.Status.DELIVERED
        item.delivered_at = timezone.now()
        item.delivery_reference = reference[:160]
        item.version += 1
        item.save(update_fields=["status", "delivered_at", "delivery_reference", "version", "updated_at"])
        record_event(tenant_id=item.tenant_id, actor_id=0, action="student.copy.delivered", aggregate="StudentScriptRequest", aggregate_id=item.id, payload={"external_application_id": item.external_application_id, "delivery_reference": item.delivery_reference})
    return {"id": str(item.id), "status": item.status, "delivery_reference": item.delivery_reference, "version": item.version}


@router.post("/audit/evidence")
def evidence_create(request, payload: EvidenceIn):
    tenant_id = _tenant(request, *READ_ROLES)
    require_step_up(request)
    script = Script.objects.filter(id=payload.script_id, tenant_id=tenant_id).first()
    if not script:
        raise HttpError(404, "Script not found")
    with transaction.atomic():
        item = EvidencePackage.objects.create(tenant_id=tenant_id, script=script, purpose=payload.purpose, requested_by_id=request.auth.id)
        record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="audit.evidence.requested", aggregate="EvidencePackage", aggregate_id=item.id, payload={"script_id": str(script.id), "purpose": payload.purpose})
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/audit/evidence/{package_id}/seal")
def evidence_seal(request, package_id: str):
    tenant_id = _tenant(request, *READ_ROLES)
    require_step_up(request)
    item = seal_evidence(tenant_id=tenant_id, actor_id=request.auth.id, package_id=package_id)
    return {"id": str(item.id), "status": item.status, "digest": item.digest, "event_count": item.event_count, "version": item.version}


@router.get("/audit/scripts/{script_id}/timeline")
def script_timeline(request, script_id: str):
    tenant_id = _tenant(request, *READ_ROLES)
    script = Script.objects.filter(id=script_id, tenant_id=tenant_id).first()
    if not script:
        raise HttpError(404, "Script not found")
    aggregate_ids = {str(script.id)}
    aggregate_ids.update(str(value) for value in Assignment.objects.filter(script=script).values_list("id", flat=True))
    aggregate_ids.update(str(value) for value in Evaluation.objects.filter(assignment__script=script).values_list("id", flat=True))
    events = AuditEvent.objects.filter(tenant_id=tenant_id, aggregate_id__in=aggregate_ids).order_by("occurred_at")
    custody = CustodyEvent.objects.filter(tenant_id=tenant_id, script=script).order_by("created_at")
    return {"script": script.script_code, "events": [{"id": str(item.id), "at": item.occurred_at.isoformat(), "actor": item.actor_id, "action": item.action, "aggregate": item.aggregate_type, "payload": item.payload} for item in events], "custody": [{"id": str(item.id), "at": item.created_at.isoformat(), "from": item.from_state, "to": item.to_state, "location": item.location, "actor": item.actor_id} for item in custody]}


@router.post("/integrations")
def integration_create(request, payload: IntegrationIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    with transaction.atomic():
        item = IntegrationEndpoint.objects.create(tenant_id=tenant_id, **payload.dict())
        record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="integration.endpoint.created", aggregate="IntegrationEndpoint", aggregate_id=item.id, payload={"kind": item.kind, "base_url": item.base_url})
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.get("/integrations/api-keys")
def university_api_keys(request):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    return _serialize(
        UniversityApiKey.objects.filter(tenant_id=tenant_id).order_by("name"),
        ["name", "key_prefix", "source_system", "integration_endpoint_id", "scopes", "status", "last_used_at", "version"],
    )


@router.post("/integrations/api-keys")
def university_api_key_create(request, payload: UniversityApiKeyIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    endpoint = IntegrationEndpoint.objects.filter(id=payload.endpoint_id, tenant_id=tenant_id).first() if payload.endpoint_id else None
    raw_key = f"admz_live_{secrets.token_urlsafe(32)}"
    source_system = payload.source_system.strip().lower()
    with transaction.atomic():
        item = UniversityApiKey.objects.create(
            tenant_id=tenant_id,
            name=payload.name.strip(),
            key_prefix=raw_key[:16],
            key_hash=_hash_api_key(raw_key),
            source_system=source_system,
            integration_endpoint=endpoint,
            scopes=payload.scopes,
            created_by_id=request.auth.id,
        )
        record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="integration.api_key.created", aggregate="UniversityApiKey", aggregate_id=item.id, payload={"name": item.name, "source_system": item.source_system, "scopes": item.scopes})
    return {"id": str(item.id), "api_key": raw_key, "key_prefix": item.key_prefix, "source_system": item.source_system, "scopes": item.scopes, "status": item.status, "version": item.version}


@router.post("/integrations/api-keys/{key_id}/revoke")
def university_api_key_revoke(request, key_id: str, payload: VersionActionIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    with transaction.atomic():
        item = UniversityApiKey.objects.select_for_update().filter(id=key_id, tenant_id=tenant_id).first()
        if not item or item.version != payload.version:
            raise HttpError(409, "API key is missing or stale")
        item.status = UniversityApiKey.Status.REVOKED
        item.revoked_at = timezone.now()
        item.version += 1
        item.save(update_fields=["status", "revoked_at", "version", "updated_at"])
        record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="integration.api_key.revoked", aggregate="UniversityApiKey", aggregate_id=item.id, payload={"reason": payload.reason})
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/official-portal/photocopy-requests", auth=None)
def official_portal_photocopy_request(request, payload: OfficialPortalPhotocopyIn):
    api_key = _official_portal_context(request, "photocopy:request")
    script = Script.objects.filter(id=payload.script_id, tenant_id=api_key.tenant_id).first()
    if not script:
        raise HttpError(404, "Script not found")
    item = create_student_request(
        tenant_id=api_key.tenant_id,
        actor_id=0,
        identity_reference=payload.identity_reference,
        script=script,
        purpose=payload.purpose,
        source_system=api_key.source_system,
        external_application_id=payload.external_application_id,
        external_payload=payload.payload,
        integration_endpoint=api_key.integration_endpoint,
        release_mode=payload.release_mode,
    )
    return {"id": str(item.id), "status": item.status, "external_application_id": item.external_application_id, "version": item.version}


@router.post("/official-portal/revaluation-requests", auth=None)
def official_portal_revaluation_request(request, payload: OfficialPortalRevaluationIn):
    api_key = _official_portal_context(request, "revaluation:request")
    if payload.request_type != RevaluationRequest.RequestType.REVALUATION:
        raise HttpError(422, "Use the recounting endpoint for recounting requests")
    script = Script.objects.filter(id=payload.script_id, tenant_id=api_key.tenant_id).first()
    if not script:
        raise HttpError(404, "Script not found")
    item = create_revaluation(
        tenant_id=api_key.tenant_id,
        actor_id=0,
        script=script,
        identity_reference=payload.identity_reference,
        scope=payload.scope,
        question_ids=payload.question_ids,
        reason=payload.reason,
        rule=payload.rule,
        request_type=payload.request_type,
        recounting_notes=payload.recounting_notes,
        source_system=api_key.source_system,
        external_application_id=payload.external_application_id,
        external_payload=payload.payload,
        integration_endpoint=api_key.integration_endpoint,
    )
    return {"id": str(item.id), "status": item.status, "request_type": item.request_type, "external_application_id": item.external_application_id, "version": item.version}


@router.post("/official-portal/recounting-requests", auth=None)
def official_portal_recounting_request(request, payload: OfficialPortalRevaluationIn):
    api_key = _official_portal_context(request, "recounting:request")
    if payload.request_type != RevaluationRequest.RequestType.RECOUNTING:
        raise HttpError(422, "Recounting endpoint requires request_type=recounting")
    script = Script.objects.filter(id=payload.script_id, tenant_id=api_key.tenant_id).first()
    if not script:
        raise HttpError(404, "Script not found")
    item = create_revaluation(
        tenant_id=api_key.tenant_id,
        actor_id=0,
        script=script,
        identity_reference=payload.identity_reference,
        scope=payload.scope,
        question_ids=payload.question_ids,
        reason=payload.reason,
        rule=payload.rule,
        request_type=payload.request_type,
        recounting_notes=payload.recounting_notes,
        source_system=api_key.source_system,
        external_application_id=payload.external_application_id,
        external_payload=payload.payload,
        integration_endpoint=api_key.integration_endpoint,
    )
    return {"id": str(item.id), "status": item.status, "request_type": item.request_type, "external_application_id": item.external_application_id, "version": item.version}


@router.post("/integrations/handovers")
def handover_create(request, payload: HandoverIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    endpoint = IntegrationEndpoint.objects.filter(id=payload.endpoint_id, tenant_id=tenant_id, status=IntegrationEndpoint.Status.ACTIVE).first()
    final = FinalMark.objects.filter(id=payload.final_mark_id, tenant_id=tenant_id).first()
    if not endpoint or not final:
        raise HttpError(404, "Integration or final mark not found")
    item, replayed = queue_handover(tenant_id=tenant_id, actor_id=request.auth.id, endpoint=endpoint, final_mark=final, idempotency_key=payload.idempotency_key)
    return {"id": str(item.id), "status": item.status, "payload_digest": item.payload_digest, "version": item.version, "replayed": replayed}


@router.post("/integrations/handovers/{handover_id}/acknowledge")
def handover_acknowledge(request, handover_id: str, payload: AcknowledgeIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    item = acknowledge_handover(tenant_id=tenant_id, actor_id=request.auth.id, handover_id=handover_id, expected_version=payload.version, status=payload.status, reference=payload.acknowledgement_reference, remote_snapshot=payload.remote_snapshot)
    return {"id": str(item.id), "status": item.status, "differences": item.differences, "version": item.version}


@router.post("/integrations/handovers/{handover_id}/action")
def handover_action(request, handover_id: str, payload: VersionActionIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    item = transition_handover(tenant_id=tenant_id, actor_id=request.auth.id, handover_id=handover_id, expected_version=payload.version, target=payload.target)
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.get("/i18n")
def i18n_catalog(request):
    membership = membership_for(request)
    preference = LocalePreference.objects.filter(tenant_id=membership.institution.tenant_id, user_id=request.auth.id).first()
    return {"locales": SUPPORTED_LOCALES, "selected": preference.locale if preference else "en", "additional": preference.additional_locales if preference else []}


@router.put("/i18n")
def i18n_update(request, payload: LocaleIn):
    membership = membership_for(request)
    item = set_locale(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, locale=payload.locale, additional_locales=payload.additional_locales)
    return {"id": str(item.id), "locale": item.locale}


@router.post("/continuity/plans")
def recovery_plan(request, payload: RecoveryPlanIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    with transaction.atomic():
        item = RecoveryPlan.objects.create(tenant_id=tenant_id, **payload.dict())
        record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="continuity.plan.created", aggregate="RecoveryPlan", aggregate_id=item.id, payload={"regions": item.regions, "rpo_minutes": item.rpo_minutes, "rto_minutes": item.rto_minutes})
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/continuity/plans/{plan_id}/action")
def recovery_plan_action(request, plan_id: str, payload: VersionActionIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    require_step_up(request)
    item = transition_recovery_plan(tenant_id=tenant_id, actor_id=request.auth.id, plan_id=plan_id, expected_version=payload.version, target=payload.target)
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/continuity/drills")
def recovery_drill(request, payload: DrillIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    require_step_up(request)
    plan = RecoveryPlan.objects.filter(id=payload.plan_id, tenant_id=tenant_id).first()
    if not plan:
        raise HttpError(404, "Recovery plan not found")
    with transaction.atomic():
        item = RecoveryDrill.objects.create(tenant_id=tenant_id, plan=plan, drill_type=payload.drill_type, recovery_point=payload.recovery_point, requested_by_id=request.auth.id)
        record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="continuity.drill.planned", aggregate="RecoveryDrill", aggregate_id=item.id, payload={"plan_id": str(plan.id), "drill_type": item.drill_type})
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/continuity/drills/{drill_id}/action")
def recovery_drill_action(request, drill_id: str, payload: VersionActionIn):
    tenant_id = _tenant(request, *ADMIN_ROLES)
    require_step_up(request)
    item = transition_drill(tenant_id=tenant_id, actor_id=request.auth.id, drill_id=drill_id, expected_version=payload.version, target=payload.target, measurements=payload.values.get("measurements"), integrity_checks=payload.values.get("integrity_checks"), report=payload.reason, corrective_actions=payload.values.get("corrective_actions"))
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.get("/runtime/health")
def runtime_health(request):
    tenant_id = _tenant(request, *READ_ROLES)
    unpublished = OutboxEvent.objects.filter(tenant_id=tenant_id, published_at__isnull=True).count()
    return {"status": "degraded" if unpublished > 100 else "healthy", "database": "healthy", "storage_assets": ScriptAsset.objects.filter(tenant_id=tenant_id, deleted_at__isnull=True).count(), "queue_backlog": unpublished, "active_sessions": request.auth.access_sessions.filter(tenant_id=tenant_id, revoked_at__isnull=True, expires_at__gt=timezone.now()).count(), "open_incidents": RuntimeIncident.objects.filter(tenant_id=tenant_id).exclude(status=RuntimeIncident.Status.RECOVERED).count(), "checked_at": timezone.now().isoformat()}
