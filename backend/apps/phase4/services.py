import hashlib
import json
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone
from ninja.errors import HttpError

from apps.allocation.models import Assignment
from apps.core.services import record_event
from apps.custody.models import Script
from apps.discrepancy.models import DiscrepancyCase
from apps.evaluators.models import Evaluator
from apps.integrity.models import IntegrityAlert
from apps.marking.models import Evaluation
from apps.repository.models import ScriptAsset
from apps.repository.storage import delete_object
from apps.security.models import SecurityAlert, SecurityPolicy
from apps.scan_processing.models import ScanQualityException
from apps.valuation.models import FinalMark, ValuationResult

from .models import (
    AttendanceRecord,
    CentreProfile,
    CentreReadiness,
    CompletionRecord,
    ControlledAuthorization,
    EvidencePackage,
    EvaluationCamp,
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
    RemunerationStatement,
    ResultHandover,
    RevaluationRequest,
    RuntimeIncident,
    SecureEvaluationSession,
    StudentScriptRequest,
    WorkloadAction,
)


SECURE_PAUSE_CATEGORIES = {
    "camera_obstructed",
    "camera_stopped",
    "external_media_device",
    "fullscreen_exited",
    "heartbeat_lost",
    "multiple_faces",
    "multiple_screens",
    "viewer_hidden",
}


def security_policy_snapshot(tenant_id):
    policy = SecurityPolicy.objects.filter(tenant_id=tenant_id).first() or SecurityPolicy()
    return {
        "camera_required": policy.evaluation_camera_required,
        "fullscreen_required": policy.evaluation_fullscreen_required,
        "single_screen_required": policy.evaluation_single_screen_required,
        "event_recording": policy.evaluation_event_recording,
        "heartbeat_seconds": policy.evaluation_heartbeat_seconds,
        "no_face_seconds": policy.evaluation_no_face_seconds,
        "retention_days": policy.evaluation_retention_days,
    }


ADMIN_TRANSITIONS = {
    ModerationCase.Status.SAMPLED: {ModerationCase.Status.ASSIGNED},
    ModerationCase.Status.ASSIGNED: {ModerationCase.Status.REVIEW},
    ModerationCase.Status.REVIEW: {ModerationCase.Status.DECIDED},
    ModerationCase.Status.DECIDED: {ModerationCase.Status.APPROVED},
}
REVALUATION_TRANSITIONS = {
    RevaluationRequest.Status.REQUESTED: {RevaluationRequest.Status.APPROVED, RevaluationRequest.Status.REJECTED},
    RevaluationRequest.Status.APPROVED: {RevaluationRequest.Status.ASSIGNED},
    RevaluationRequest.Status.ASSIGNED: {RevaluationRequest.Status.EVALUATED},
    RevaluationRequest.Status.EVALUATED: {RevaluationRequest.Status.DECIDED},
    RevaluationRequest.Status.DECIDED: {RevaluationRequest.Status.CLOSED},
}
ISSUE_TRANSITIONS = {
    OperationalIssue.Status.OPEN: {OperationalIssue.Status.ASSIGNED, OperationalIssue.Status.ESCALATED, OperationalIssue.Status.RESOLVED},
    OperationalIssue.Status.ASSIGNED: {OperationalIssue.Status.ESCALATED, OperationalIssue.Status.RESOLVED},
    OperationalIssue.Status.ESCALATED: {OperationalIssue.Status.RESOLVED},
    OperationalIssue.Status.RESOLVED: {OperationalIssue.Status.CONFIRMED, OperationalIssue.Status.REOPENED},
    OperationalIssue.Status.REOPENED: {OperationalIssue.Status.ASSIGNED, OperationalIssue.Status.ESCALATED, OperationalIssue.Status.RESOLVED},
}
RUNTIME_TRANSITIONS = {
    RuntimeIncident.Status.DETECTED: {RuntimeIncident.Status.RETRYING, RuntimeIncident.Status.DEGRADED, RuntimeIncident.Status.FAILED},
    RuntimeIncident.Status.RETRYING: {RuntimeIncident.Status.RECOVERED, RuntimeIncident.Status.DEGRADED, RuntimeIncident.Status.FAILED},
    RuntimeIncident.Status.DEGRADED: {RuntimeIncident.Status.RETRYING, RuntimeIncident.Status.RECOVERED, RuntimeIncident.Status.FAILED},
    RuntimeIncident.Status.FAILED: {RuntimeIncident.Status.RETRYING},
}


def _transition(item, target, transitions, expected_version):
    if item.version != expected_version or target not in transitions.get(item.status, set()):
        raise HttpError(409, "The record is stale or the requested transition is not allowed")
    previous = item.status
    item.status = target
    item.version += 1
    return previous


@transaction.atomic
def save_moderation_policy(*, tenant_id, actor_id, paper, values, expected_version=None):
    item = ModerationPolicy.objects.select_for_update().filter(tenant_id=tenant_id, paper=paper).first()
    if item and item.version != expected_version:
        raise HttpError(409, "Moderation policy is stale")
    item = item or ModerationPolicy(tenant_id=tenant_id, paper=paper)
    for key in ("sample_percentage", "sampling_modes", "high_score_threshold", "low_score_threshold", "mandatory"):
        if key in values:
            setattr(item, key, values[key])
    if not Decimal("0") <= item.sample_percentage <= Decimal("100"):
        raise HttpError(422, "Sampling percentage must be between 0 and 100")
    if item.pk:
        item.version += 1
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="moderation.policy.saved", aggregate="ModerationPolicy", aggregate_id=item.id, payload={"paper_id": str(paper.id), "version": item.version})
    return item


@transaction.atomic
def sample_moderation_cases(*, tenant_id, actor_id, policy):
    created = []
    results = ValuationResult.objects.filter(tenant_id=tenant_id, script__paper=policy.paper, is_locked=True).select_related("script__paper")
    for result in results:
        reasons = []
        score = int(hashlib.sha256(str(result.script_id).encode()).hexdigest()[:8], 16) % 10_000 / 100
        if policy.mandatory or score < float(policy.sample_percentage):
            reasons.append("percentage_random")
        if policy.high_score_threshold is not None and result.total_marks >= policy.high_score_threshold:
            reasons.append("high_score")
        if policy.low_score_threshold is not None and result.total_marks <= policy.low_score_threshold:
            reasons.append("low_score")
        if "failed_script" in policy.sampling_modes and result.total_marks < result.script.paper.pass_marks:
            reasons.append("failed_script")
        if not reasons:
            continue
        item, was_created = ModerationCase.objects.get_or_create(
            tenant_id=tenant_id,
            script=result.script,
            source_result=result,
            defaults={"sample_reasons": sorted(set(reasons)), "original_mark": result.total_marks},
        )
        if was_created:
            created.append(item)
            record_event(tenant_id=tenant_id, actor_id=actor_id, action="moderation.case.sampled", aggregate="ModerationCase", aggregate_id=item.id, payload={"script_id": str(item.script_id), "reasons": item.sample_reasons})
    return created


@transaction.atomic
def transition_moderation(*, tenant_id, actor_id, case_id, expected_version, target, moderator=None, adjusted_mark=None, reason="", snapshot=None):
    item = ModerationCase.objects.select_for_update().select_related("script__paper").filter(id=case_id, tenant_id=tenant_id).first()
    if not item:
        raise HttpError(404, "Moderation case not found")
    previous = _transition(item, target, ADMIN_TRANSITIONS, expected_version)
    if target == ModerationCase.Status.ASSIGNED:
        if not moderator:
            raise HttpError(422, "A moderator is required")
        item.moderator = moderator
    if target == ModerationCase.Status.DECIDED:
        adjusted_mark = Decimal(str(adjusted_mark)) if adjusted_mark is not None else None
        if adjusted_mark is None or adjusted_mark < 0 or adjusted_mark > item.script.paper.max_marks or len(reason.strip()) < 8:
            raise HttpError(422, "A valid adjusted mark and specific reason are required")
        item.adjusted_mark = adjusted_mark
        item.adjustment_reason = reason.strip()
        item.fresh_mark_snapshot = snapshot or {}
        item.decided_by_id = actor_id
    if target == ModerationCase.Status.APPROVED:
        if item.decided_by_id == actor_id:
            raise HttpError(409, "Moderator decision requires independent approval")
        item.approved_by_id = actor_id
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action=f"moderation.case.{target}", aggregate="ModerationCase", aggregate_id=item.id, payload={"from": previous, "script_id": str(item.script_id), "adjusted_mark": str(item.adjusted_mark) if item.adjusted_mark is not None else None})
    return item


@transaction.atomic
def create_revaluation(*, tenant_id, actor_id, script, identity_reference, scope, question_ids, reason, rule):
    final = FinalMark.objects.filter(tenant_id=tenant_id, script=script, status=FinalMark.Status.LOCKED).first()
    if not final:
        raise HttpError(409, "Only a script with a locked final mark is eligible for revaluation")
    if RevaluationRequest.objects.filter(tenant_id=tenant_id, script=script).exclude(status__in=[RevaluationRequest.Status.CLOSED, RevaluationRequest.Status.REJECTED]).exists():
        raise HttpError(409, "An active revaluation request already exists")
    item = RevaluationRequest.objects.create(
        tenant_id=tenant_id,
        script=script,
        identity_reference=identity_reference,
        scope=scope,
        question_ids=question_ids,
        reason=reason.strip(),
        rule=rule,
        eligibility_snapshot={"final_mark_status": final.status, "script_state": script.state, "checked_at": timezone.now().isoformat()},
        original_final_mark=final,
        original_mark_snapshot=final.mark,
        requested_by_id=actor_id,
    )
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="revaluation.requested", aggregate="RevaluationRequest", aggregate_id=item.id, payload={"script_id": str(script.id), "scope": scope})
    return item


@transaction.atomic
def transition_revaluation(*, tenant_id, actor_id, request_id, expected_version, target, evaluator=None, new_result=None):
    item = RevaluationRequest.objects.select_for_update().filter(id=request_id, tenant_id=tenant_id).first()
    if not item:
        raise HttpError(404, "Revaluation request not found")
    previous = _transition(item, target, REVALUATION_TRANSITIONS, expected_version)
    if target == RevaluationRequest.Status.APPROVED:
        if item.requested_by_id == actor_id:
            raise HttpError(409, "Requester cannot approve the revaluation")
        item.approved_by_id = actor_id
    elif target == RevaluationRequest.Status.ASSIGNED:
        if not evaluator:
            raise HttpError(422, "An independent evaluator is required")
        if ValuationResult.objects.filter(script=item.script, evaluation__assignment__evaluator=evaluator).exists():
            raise HttpError(409, "The revaluation evaluator must be independent")
        item.assigned_evaluator = evaluator
    elif target == RevaluationRequest.Status.EVALUATED:
        if not new_result or new_result.script_id != item.script_id:
            raise HttpError(422, "A locked revaluation result is required")
        item.new_result = new_result
        item.new_mark = new_result.total_marks
        item.mark_difference = new_result.total_marks - item.original_mark_snapshot
    elif target == RevaluationRequest.Status.DECIDED:
        if item.new_mark is None:
            raise HttpError(409, "Revaluation has no new result")
        if item.rule == RevaluationRequest.Rule.BEST:
            item.final_mark = max(item.original_mark_snapshot, item.new_mark)
        elif item.rule == RevaluationRequest.Rule.AVERAGE:
            item.final_mark = ((item.original_mark_snapshot + item.new_mark) / 2).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            threshold = item.script.paper.discrepancy_threshold
            item.final_mark = item.new_mark if abs(item.mark_difference) >= threshold else item.original_mark_snapshot
    elif target == RevaluationRequest.Status.CLOSED:
        item.closed_by_id = actor_id
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action=f"revaluation.{target}", aggregate="RevaluationRequest", aggregate_id=item.id, payload={"from": previous, "original_mark": str(item.original_mark_snapshot), "new_mark": str(item.new_mark) if item.new_mark is not None else None, "final_mark": str(item.final_mark) if item.final_mark is not None else None})
    return item


def completion_checks(*, tenant_id, final_mark):
    script = final_mark.script
    evaluations = Evaluation.objects.filter(tenant_id=tenant_id, assignment__script=script)
    submitted = evaluations.filter(status__in=[Evaluation.Status.SUBMITTED, Evaluation.Status.LOCKED])
    question_count = script.paper.questions.filter(required=True).count()
    complete_evaluations = sum(1 for item in submitted if item.question_marks.values("question_id").distinct().count() >= question_count)
    required_rounds = script.paper.valuation_rounds
    moderation_open = script.moderation_cases.exclude(status=ModerationCase.Status.APPROVED).exists() if script.paper.moderation_required else False
    discrepancy_open = DiscrepancyCase.objects.filter(tenant_id=tenant_id, comparison__script=script).exclude(status=DiscrepancyCase.Status.APPROVED).exists()
    revaluation_open = script.revaluation_requests.exclude(status__in=[RevaluationRequest.Status.CLOSED, RevaluationRequest.Status.REJECTED]).exists()
    quality_open = ScanQualityException.objects.filter(tenant_id=tenant_id, run__script=script).exclude(status__in=[ScanQualityException.Status.RESOLVED, ScanQualityException.Status.SKIPPED]).exists()
    integrity_open = IntegrityAlert.objects.filter(tenant_id=tenant_id, manifest__asset__script=script).exclude(status=IntegrityAlert.Status.RESOLVED).exists()
    checks = {
        "required_questions_complete": complete_evaluations >= required_rounds,
        "required_valuations_complete": ValuationResult.objects.filter(tenant_id=tenant_id, script=script, is_locked=True).count() >= required_rounds,
        "moderation_complete": not moderation_open,
        "discrepancy_closed": not discrepancy_open,
        "revaluation_closed": not revaluation_open,
        "exceptions_closed": not quality_open and not integrity_open,
        "final_mark_integrity": final_mark.status == FinalMark.Status.LOCKED and bool(final_mark.checksum),
    }
    checks["ready"] = all(checks.values())
    return checks


@transaction.atomic
def review_completion(*, tenant_id, actor_id, final_mark):
    current = FinalMark.objects.select_for_update().select_related("script__paper").get(id=final_mark.id, tenant_id=tenant_id)
    checks = completion_checks(tenant_id=tenant_id, final_mark=current)
    item, _ = CompletionRecord.objects.select_for_update().get_or_create(tenant_id=tenant_id, script=current.script, final_mark=current)
    item.checks = checks
    item.status = CompletionRecord.Status.READY if checks["ready"] else CompletionRecord.Status.PENDING
    item.version += 1
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="completion.reviewed", aggregate="CompletionRecord", aggregate_id=item.id, payload={"script_id": str(item.script_id), "checks": checks})
    return item


@transaction.atomic
def declare_and_sign(*, tenant_id, actor_id, record_id, expected_version, declaration, confirm):
    item = CompletionRecord.objects.select_for_update().select_related("final_mark").filter(id=record_id, tenant_id=tenant_id).first()
    if not item or item.version != expected_version or item.status != CompletionRecord.Status.READY:
        raise HttpError(409, "Completion record is missing, stale, or not ready")
    if not confirm or len(declaration.strip()) < 12:
        raise HttpError(422, "A final confirmation and examiner declaration are required")
    item.examiner_declaration = declaration.strip()
    item.declaration_by_id = actor_id
    item.signature_digest = hashlib.sha256(f"{item.id}:{item.final_mark.checksum}:{actor_id}:{item.examiner_declaration}".encode()).hexdigest()
    item.signed_by_id = actor_id
    item.status = CompletionRecord.Status.SIGNED
    item.version += 1
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="completion.signed", aggregate="CompletionRecord", aggregate_id=item.id, payload={"script_id": str(item.script_id), "signature_digest": item.signature_digest})
    return item


@transaction.atomic
def request_authorization(*, tenant_id, actor_id, kind, final_mark, purpose, proposed_change, expires_at):
    if kind not in ControlledAuthorization.Kind.values:
        raise HttpError(422, "Unsupported authorization kind")
    if kind in {ControlledAuthorization.Kind.RESULT_RELEASE, ControlledAuthorization.Kind.POST_LOCK_CHANGE} and not final_mark:
        raise HttpError(422, "This authorization requires a final mark")
    if len(purpose.strip()) < 8 or expires_at <= timezone.now():
        raise HttpError(422, "A specific purpose and future expiry are required")
    item = ControlledAuthorization.objects.create(tenant_id=tenant_id, kind=kind, final_mark=final_mark, purpose=purpose.strip(), proposed_change=proposed_change, requested_by_id=actor_id, expires_at=expires_at)
    record_event(tenant_id=tenant_id, actor_id=actor_id, action=f"completion.authorization.{kind}.requested", aggregate="ControlledAuthorization", aggregate_id=item.id, payload={"final_mark_id": str(final_mark.id) if final_mark else None, "expires_at": expires_at.isoformat()})
    return item


@transaction.atomic
def decide_authorization(*, tenant_id, actor_id, authorization_id, expected_version, approve):
    item = ControlledAuthorization.objects.select_for_update().filter(id=authorization_id, tenant_id=tenant_id).first()
    if not item or item.version != expected_version or item.status != ControlledAuthorization.Status.REQUESTED:
        raise HttpError(409, "Authorization is missing, stale, or already decided")
    if item.expires_at <= timezone.now():
        item.status = ControlledAuthorization.Status.EXPIRED
    elif item.requested_by_id == actor_id:
        raise HttpError(409, "Requester cannot approve their own authorization")
    else:
        item.status = ControlledAuthorization.Status.APPROVED if approve else ControlledAuthorization.Status.REJECTED
        item.approved_by_id = actor_id
    item.version += 1
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action=f"completion.authorization.{item.status}", aggregate="ControlledAuthorization", aggregate_id=item.id, payload={"kind": item.kind})
    return item


@transaction.atomic
def consume_authorization(*, tenant_id, actor_id, authorization_id, expected_version):
    item = ControlledAuthorization.objects.select_for_update().filter(id=authorization_id, tenant_id=tenant_id).first()
    if not item or item.version != expected_version or item.status != ControlledAuthorization.Status.APPROVED:
        raise HttpError(409, "Authorization is missing, stale, or not approved")
    if item.expires_at <= timezone.now():
        item.status = ControlledAuthorization.Status.EXPIRED
        item.version += 1
        item.save()
        raise HttpError(409, "Authorization has expired")
    if item.kind == ControlledAuthorization.Kind.RESULT_RELEASE:
        completion = CompletionRecord.objects.select_for_update().filter(final_mark=item.final_mark).first()
        if not completion or completion.status != CompletionRecord.Status.SIGNED:
            raise HttpError(409, "A signed completion record is required before result release")
        completion.status = CompletionRecord.Status.RELEASED
        completion.version += 1
        completion.save()
    item.status = ControlledAuthorization.Status.CONSUMED
    item.version += 1
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action=f"completion.authorization.{item.kind}.consumed", aggregate="ControlledAuthorization", aggregate_id=item.id, payload={"final_mark_id": str(item.final_mark_id) if item.final_mark_id else None, "proposed_change": item.proposed_change})
    return item


def _security_action(category, severity):
    if severity == "critical" or category in SECURE_PAUSE_CATEGORIES:
        return "pause"
    return "warn" if severity in {"high", "medium"} else "record"


def _create_security_event(*, tenant_id, actor_id, assignment, access_session_id, category, severity, device_fingerprint, session_fingerprint, details, secure_session=None):
    action = _security_action(category, severity)
    item = PresenceSecurityEvent.objects.create(
        tenant_id=tenant_id,
        assignment=assignment,
        access_session_id=access_session_id,
        category=category,
        severity=severity,
        device_fingerprint=device_fingerprint,
        session_fingerprint=session_fingerprint,
        details={**details, "secure_session_id": str(secure_session.id) if secure_session else None},
        action=action,
    )
    if secure_session and action in {"warn", "pause"}:
        secure_session.violation_count += 1
        if action == "pause":
            secure_session.status = SecureEvaluationSession.Status.PAUSED
            secure_session.pause_reason = category
            secure_session.paused_at = timezone.now()
        secure_session.version += 1
        secure_session.save()
    if secure_session and action == "pause":
        ProctoringReview.objects.get_or_create(tenant_id=tenant_id, secure_session=secure_session, event=item)
        SecurityAlert.objects.create(
            tenant_id=tenant_id,
            category="secure_evaluation",
            severity=severity,
            title="Secure evaluation session paused",
            details={"assignment_id": str(assignment.id), "session_id": str(secure_session.id), "event_id": str(item.id), "reason": category},
        )
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="remote_security.event.recorded", aggregate="PresenceSecurityEvent", aggregate_id=item.id, payload={"assignment_id": str(assignment.id), "category": category, "severity": severity, "response": action, "secure_session_id": str(secure_session.id) if secure_session else None})
    return item


@transaction.atomic
def start_secure_evaluation_session(*, tenant_id, actor_id, assignment, evaluator, access_session, session_fingerprint, device_fingerprint, consent, preflight, device_inventory):
    policy = security_policy_snapshot(tenant_id)
    if not consent:
        raise HttpError(422, "Security monitoring consent is required")
    if policy["camera_required"] and not preflight.get("camera_ready"):
        raise HttpError(409, "A working webcam is required")
    if policy["fullscreen_required"] and not preflight.get("fullscreen_active"):
        raise HttpError(409, "Fullscreen mode is required")
    screen_count = preflight.get("screen_count")
    if policy["single_screen_required"] and isinstance(screen_count, int) and screen_count > 1:
        raise HttpError(409, "Disconnect additional displays before evaluation")
    if len(session_fingerprint) != 64 or len(device_fingerprint) != 64:
        raise HttpError(422, "Secure device fingerprints are invalid")
    now = timezone.now()
    for previous in SecureEvaluationSession.objects.select_for_update().filter(
        tenant_id=tenant_id,
        evaluator=evaluator,
        status__in=[SecureEvaluationSession.Status.ACTIVE, SecureEvaluationSession.Status.PAUSED],
    ):
        previous.status = SecureEvaluationSession.Status.ABANDONED
        previous.ended_at = now
        previous.version += 1
        previous.save()
    item = SecureEvaluationSession.objects.create(
        tenant_id=tenant_id,
        assignment=assignment,
        evaluator=evaluator,
        access_session_id=access_session.id,
        session_fingerprint=session_fingerprint,
        device_fingerprint=device_fingerprint,
        policy_snapshot=policy,
        preflight=preflight,
        device_inventory=device_inventory,
        consented_at=now,
        started_at=now,
        last_heartbeat_at=now,
    )
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="remote_security.session.started", aggregate="SecureEvaluationSession", aggregate_id=item.id, payload={"assignment_id": str(assignment.id), "policy": policy})
    return item


def _posture_violation(item, posture):
    policy = item.policy_snapshot
    if policy.get("camera_required") and not posture.get("camera_active"):
        return "camera_stopped"
    if policy.get("fullscreen_required") and not posture.get("fullscreen_active"):
        return "fullscreen_exited"
    if policy.get("single_screen_required") and isinstance(posture.get("screen_count"), int) and posture["screen_count"] > 1:
        return "multiple_screens"
    if posture.get("device_changed"):
        return "external_media_device"
    return ""


@transaction.atomic
def heartbeat_secure_evaluation_session(*, tenant_id, actor_id, session_id, access_session, posture):
    item = SecureEvaluationSession.objects.select_for_update().select_related("assignment").filter(id=session_id, tenant_id=tenant_id, access_session_id=access_session.id).first()
    if not item or item.status in {SecureEvaluationSession.Status.COMPLETED, SecureEvaluationSession.Status.ABANDONED}:
        raise HttpError(409, "Secure evaluation session is unavailable")
    item.last_heartbeat_at = timezone.now()
    item.save(update_fields=["last_heartbeat_at", "updated_at"])
    reason = _posture_violation(item, posture)
    event = None
    if reason and item.status == SecureEvaluationSession.Status.ACTIVE:
        event = _create_security_event(tenant_id=tenant_id, actor_id=actor_id, assignment=item.assignment, access_session_id=access_session.id, category=reason, severity="critical", device_fingerprint=item.device_fingerprint, session_fingerprint=item.session_fingerprint, details={"posture": posture}, secure_session=item)
    return item, event


@transaction.atomic
def resume_secure_evaluation_session(*, tenant_id, actor_id, session_id, access_session, posture):
    item = SecureEvaluationSession.objects.select_for_update().filter(id=session_id, tenant_id=tenant_id, access_session_id=access_session.id).first()
    if not item or item.status != SecureEvaluationSession.Status.PAUSED:
        raise HttpError(409, "Secure evaluation session is not paused")
    reason = _posture_violation(item, posture)
    if reason:
        raise HttpError(409, f"Resolve the security condition before continuing: {reason.replace('_', ' ')}")
    if item.violation_count >= 3 and not access_session.is_step_up_valid:
        raise HttpError(428, "Re-authentication is required after repeated security events")
    item.status = SecureEvaluationSession.Status.ACTIVE
    item.pause_reason = ""
    item.paused_at = None
    item.last_heartbeat_at = timezone.now()
    item.version += 1
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="remote_security.session.resumed", aggregate="SecureEvaluationSession", aggregate_id=item.id, payload={"assignment_id": str(item.assignment_id), "violations": item.violation_count})
    return item


@transaction.atomic
def finish_secure_evaluation_session(*, tenant_id, actor_id, session_id, access_session, completed):
    item = SecureEvaluationSession.objects.select_for_update().filter(id=session_id, tenant_id=tenant_id, access_session_id=access_session.id).first()
    if not item:
        raise HttpError(404, "Secure evaluation session not found")
    if item.status not in {SecureEvaluationSession.Status.COMPLETED, SecureEvaluationSession.Status.ABANDONED}:
        item.status = SecureEvaluationSession.Status.COMPLETED if completed else SecureEvaluationSession.Status.ABANDONED
        item.ended_at = timezone.now()
        item.version += 1
        item.save()
        record_event(tenant_id=tenant_id, actor_id=actor_id, action=f"remote_security.session.{item.status}", aggregate="SecureEvaluationSession", aggregate_id=item.id, payload={"assignment_id": str(item.assignment_id)})
    return item


@transaction.atomic
def record_presence_event(*, tenant_id, actor_id, assignment, access_session, category, severity, device_fingerprint, session_fingerprint, details, secure_session=None):
    if secure_session:
        secure_session = SecureEvaluationSession.objects.select_for_update().filter(id=secure_session.id, tenant_id=tenant_id, assignment=assignment, access_session_id=access_session.id).first()
        if not secure_session:
            raise HttpError(409, "Secure evaluation session is unavailable")
    return _create_security_event(tenant_id=tenant_id, actor_id=actor_id, assignment=assignment, access_session_id=access_session.id, category=category, severity=severity, device_fingerprint=device_fingerprint, session_fingerprint=session_fingerprint, details=details, secure_session=secure_session)


@transaction.atomic
def create_proctoring_evidence(*, tenant_id, actor_id, secure_session, sequence, reason, storage_key, mime_type, captured_from, captured_to, retention_days):
    item = ProctoringEvidence.objects.create(tenant_id=tenant_id, secure_session=secure_session, sequence=sequence, reason=reason[:80], storage_key=storage_key, mime_type=mime_type, captured_from=captured_from, captured_to=captured_to, retention_until=timezone.now() + timedelta(days=retention_days))
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="remote_security.evidence.requested", aggregate="ProctoringEvidence", aggregate_id=item.id, payload={"session_id": str(secure_session.id), "sequence": sequence, "reason": reason})
    return item


@transaction.atomic
def verify_proctoring_evidence(*, tenant_id, actor_id, evidence_id, sha256, byte_size, stored_sha256, stored_size):
    item = ProctoringEvidence.objects.select_for_update().filter(id=evidence_id, tenant_id=tenant_id).first()
    if not item or item.status != ProctoringEvidence.Status.PENDING:
        raise HttpError(409, "Evidence upload is missing or already finalized")
    if sha256 != stored_sha256 or byte_size != stored_size or byte_size <= 0:
        item.status = ProctoringEvidence.Status.FAILED
        item.save(update_fields=["status", "updated_at"])
        raise HttpError(409, "Evidence integrity verification failed")
    item.sha256 = sha256
    item.byte_size = byte_size
    item.uploaded_at = timezone.now()
    item.status = ProctoringEvidence.Status.VERIFIED
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="remote_security.evidence.verified", aggregate="ProctoringEvidence", aggregate_id=item.id, payload={"session_id": str(item.secure_session_id), "sha256": sha256, "byte_size": byte_size})
    return item


@transaction.atomic
def decide_proctoring_review(*, tenant_id, actor_id, review_id, expected_version, status, note):
    item = ProctoringReview.objects.select_for_update().filter(id=review_id, tenant_id=tenant_id).first()
    if not item or item.version != expected_version or status not in ProctoringReview.Status.values:
        raise HttpError(409, "Security review is missing, stale, or invalid")
    if status in {ProctoringReview.Status.CLEARED, ProctoringReview.Status.ESCALATED} and len(note.strip()) < 8:
        raise HttpError(422, "A specific review decision note is required")
    item.status = status
    item.decision_note = note.strip()
    item.reviewer_id = actor_id
    item.reviewed_at = timezone.now()
    item.version += 1
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="remote_security.review.decided", aggregate="ProctoringReview", aggregate_id=item.id, payload={"status": status, "event_id": str(item.event_id)})
    return item


def pause_stale_secure_sessions():
    cutoff = timezone.now() - timedelta(seconds=45)
    stale = SecureEvaluationSession.objects.filter(status=SecureEvaluationSession.Status.ACTIVE, last_heartbeat_at__lt=cutoff).select_related("assignment")
    count = 0
    for session in stale.iterator():
        with transaction.atomic():
            current = SecureEvaluationSession.objects.select_for_update().select_related("assignment").filter(id=session.id, status=SecureEvaluationSession.Status.ACTIVE).first()
            if not current:
                continue
            _create_security_event(tenant_id=current.tenant_id, actor_id="secure-session-monitor", assignment=current.assignment, access_session_id=current.access_session_id, category="heartbeat_lost", severity="critical", device_fingerprint=current.device_fingerprint, session_fingerprint=current.session_fingerprint, details={"last_heartbeat_at": current.last_heartbeat_at.isoformat()}, secure_session=current)
            count += 1
    return count


def purge_expired_proctoring_evidence():
    expired = ProctoringEvidence.objects.filter(status=ProctoringEvidence.Status.VERIFIED, retention_until__lte=timezone.now())
    count = 0
    for evidence in expired.iterator():
        try:
            delete_object(evidence.storage_key)
        except OSError:
            continue
        with transaction.atomic():
            current = ProctoringEvidence.objects.select_for_update().filter(id=evidence.id, status=ProctoringEvidence.Status.VERIFIED).first()
            if not current:
                continue
            current.status = ProctoringEvidence.Status.EXPIRED
            current.save(update_fields=["status", "updated_at"])
            record_event(tenant_id=current.tenant_id, actor_id="secure-session-monitor", action="remote_security.evidence.purged", aggregate="ProctoringEvidence", aggregate_id=current.id, payload={"session_id": str(current.secure_session_id), "retention_until": current.retention_until.isoformat()})
            count += 1
    return count


def monitoring_snapshot(tenant_id):
    now = timezone.now()
    assignments = Assignment.objects.filter(tenant_id=tenant_id)
    evaluators = assignments.values("evaluator_id").annotate(total=Count("id"), submitted=Count("id", filter=Q(status=Assignment.Status.SUBMITTED)), active=Count("id", filter=Q(status=Assignment.Status.IN_PROGRESS)), pages=Sum("last_page"))
    rows = []
    for row in evaluators:
        evaluator = Evaluator.objects.get(id=row["evaluator_id"])
        completed = row["submitted"] or 0
        total = row["total"] or 0
        remaining = max(total - completed, 0)
        rows.append({"evaluator_id": str(evaluator.id), "code": evaluator.evaluator_code, "name": evaluator.display_name, "assigned": total, "completed": completed, "active": row["active"] or 0, "remaining": remaining, "capacity": evaluator.daily_capacity, "deadline_risk": remaining > evaluator.daily_capacity, "projected_days": round(remaining / max(evaluator.daily_capacity, 1), 2)})
    attendance = AttendanceRecord.objects.filter(tenant_id=tenant_id, checked_in_at__date=now.date())
    return {"generated_at": now.isoformat(), "active_evaluations": assignments.filter(status=Assignment.Status.IN_PROGRESS).count(), "remaining_scripts": assignments.exclude(status=Assignment.Status.SUBMITTED).count(), "online_evaluators": attendance.filter(checked_out_at__isnull=True).count(), "deadline_risk": assignments.filter(due_at__lte=now + timedelta(days=1)).exclude(status=Assignment.Status.SUBMITTED).count(), "evaluators": rows}


def productivity_snapshot(tenant_id):
    rows = []
    for evaluator in Evaluator.objects.filter(tenant_id=tenant_id):
        assignments = Assignment.objects.filter(tenant_id=tenant_id, evaluator=evaluator)
        completed = assignments.filter(status=Assignment.Status.SUBMITTED)
        seconds = sum(max(int((item.submitted_at - item.started_at).total_seconds()), 1) for item in completed if item.submitted_at and item.started_at)
        sph = round(completed.count() * 3600 / seconds, 2) if seconds else 0
        rows.append({"evaluator_id": str(evaluator.id), "code": evaluator.evaluator_code, "name": evaluator.display_name, "completed": completed.count(), "assigned": assignments.count(), "scripts_per_hour": sph, "average_minutes": round(seconds / max(completed.count(), 1) / 60, 2) if seconds else 0, "target": evaluator.daily_capacity, "efficiency": round(completed.count() * 100 / max(evaluator.daily_capacity, 1), 1), "risk": "overloaded" if assignments.exclude(status=Assignment.Status.SUBMITTED).count() > evaluator.daily_capacity else ("idle" if not assignments.filter(status=Assignment.Status.IN_PROGRESS).exists() else "normal")})
    queues = {"pending": Assignment.objects.filter(tenant_id=tenant_id, status__in=[Assignment.Status.ASSIGNED, Assignment.Status.ACCEPTED]).count(), "priority": Assignment.objects.filter(tenant_id=tenant_id, priority__gte=4).exclude(status=Assignment.Status.SUBMITTED).count(), "moderation": ModerationCase.objects.filter(tenant_id=tenant_id).exclude(status=ModerationCase.Status.APPROVED).count(), "discrepancy": DiscrepancyCase.objects.filter(tenant_id=tenant_id).exclude(status=DiscrepancyCase.Status.APPROVED).count(), "revaluation": RevaluationRequest.objects.filter(tenant_id=tenant_id).exclude(status__in=[RevaluationRequest.Status.CLOSED, RevaluationRequest.Status.REJECTED]).count(), "rescan": ScanQualityException.objects.filter(tenant_id=tenant_id).exclude(status__in=[ScanQualityException.Status.RESOLVED, ScanQualityException.Status.SKIPPED]).count(), "risk": PresenceSecurityEvent.objects.filter(tenant_id=tenant_id, severity__in=["high", "critical"]).count()}
    return {"evaluators": rows, "queues": queues}


@transaction.atomic
def create_workload_action(*, tenant_id, actor_id, evaluator, action, reason, metrics):
    if action not in {"rebalance", "prioritize", "reallocate"} or len(reason.strip()) < 8:
        raise HttpError(422, "A supported action and specific reason are required")
    item = WorkloadAction.objects.create(tenant_id=tenant_id, evaluator=evaluator, action=action, reason=reason.strip(), metrics=metrics, requested_by_id=actor_id)
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="workload.action.proposed", aggregate="WorkloadAction", aggregate_id=item.id, payload={"evaluator_id": str(evaluator.id), "action": action})
    return item


@transaction.atomic
def transition_workload(*, tenant_id, actor_id, action_id, expected_version, target):
    item = WorkloadAction.objects.select_for_update().filter(id=action_id, tenant_id=tenant_id).first()
    if not item or item.version != expected_version:
        raise HttpError(409, "Workload action is missing or stale")
    allowed = {
        WorkloadAction.Status.PROPOSED: {WorkloadAction.Status.APPROVED, WorkloadAction.Status.REJECTED},
        WorkloadAction.Status.APPROVED: {WorkloadAction.Status.EXECUTED},
    }
    if target not in allowed.get(item.status, set()):
        raise HttpError(409, "Workload transition is not allowed")
    if target == WorkloadAction.Status.APPROVED and item.requested_by_id == actor_id:
        raise HttpError(409, "Requester cannot approve their own workload action")
    previous = item.status
    item.status = target
    item.decided_by_id = actor_id
    item.version += 1
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action=f"workload.action.{target}", aggregate="WorkloadAction", aggregate_id=item.id, payload={"from": previous, "evaluator_id": str(item.evaluator_id), "action": item.action})
    return item


@transaction.atomic
def transition_runtime(*, tenant_id, actor_id, incident_id, expected_version, target):
    item = RuntimeIncident.objects.select_for_update().filter(id=incident_id, tenant_id=tenant_id).first()
    if not item:
        raise HttpError(404, "Runtime incident not found")
    previous = _transition(item, target, RUNTIME_TRANSITIONS, expected_version)
    if target == RuntimeIncident.Status.RETRYING:
        item.retry_count += 1
    if target == RuntimeIncident.Status.RECOVERED:
        item.recovered_at = timezone.now()
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action=f"runtime.incident.{target}", aggregate="RuntimeIncident", aggregate_id=item.id, payload={"from": previous, "service": item.service, "retry_count": item.retry_count})
    return item


@transaction.atomic
def create_issue(*, tenant_id, actor_id, issue_type, title, description, paper, question_reference):
    if len(title.strip()) < 4 or len(description.strip()) < 8:
        raise HttpError(422, "A descriptive issue title and description are required")
    normalized = " ".join(title.lower().split())
    duplicate = OperationalIssue.objects.filter(tenant_id=tenant_id, title__iexact=normalized).exclude(status=OperationalIssue.Status.CONFIRMED).first()
    priority = 1 if any(word in description.lower() for word in ("blocked", "security", "cannot submit", "data loss")) else 3
    classification = issue_type if issue_type in {"technical", "academic", "script", "scanner", "access", "support"} else "support"
    item = OperationalIssue.objects.create(tenant_id=tenant_id, issue_type=issue_type, title=normalized, description=description.strip(), classification=classification, priority=priority, paper=paper, question_reference=question_reference, sla_due_at=timezone.now() + timedelta(hours=4 if priority == 1 else 24), duplicate_of=duplicate, created_by_id=actor_id)
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="issues.issue.created", aggregate="OperationalIssue", aggregate_id=item.id, payload={"classification": classification, "priority": priority, "duplicate_of": str(duplicate.id) if duplicate else None})
    return item


@transaction.atomic
def transition_issue(*, tenant_id, actor_id, issue_id, expected_version, target, owner_id=None, resolution=""):
    item = OperationalIssue.objects.select_for_update().filter(id=issue_id, tenant_id=tenant_id).first()
    if not item:
        raise HttpError(404, "Issue not found")
    previous = _transition(item, target, ISSUE_TRANSITIONS, expected_version)
    if target == OperationalIssue.Status.ASSIGNED:
        item.owner_id = owner_id or actor_id
    if target == OperationalIssue.Status.RESOLVED:
        if len(resolution.strip()) < 8:
            raise HttpError(422, "A specific resolution is required")
        item.resolution = resolution.strip()
    if target == OperationalIssue.Status.CONFIRMED:
        item.resolution_locked = True
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action=f"issues.issue.{target}", aggregate="OperationalIssue", aggregate_id=item.id, payload={"from": previous, "resolution_locked": item.resolution_locked})
    return item


@transaction.atomic
def publish_article(*, tenant_id, actor_id, title, body, category, issue, is_global):
    item = KnowledgeArticle.objects.create(tenant_id=tenant_id, title=title.strip(), body=body.strip(), category=category, issue=issue, is_global=is_global, published_by_id=actor_id)
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="issues.knowledge.published", aggregate="KnowledgeArticle", aggregate_id=item.id, payload={"global": is_global, "issue_id": str(issue.id) if issue else None})
    return item


@transaction.atomic
def create_notification(*, tenant_id, actor_id, user_id, category, title, body, severity, channels, mandatory_acknowledgement):
    supported = {"in_app", "push", "email", "sms"}
    if not channels or not set(channels).issubset(supported):
        raise HttpError(422, "At least one supported delivery channel is required")
    item = NotificationDelivery.objects.create(tenant_id=tenant_id, user_id=user_id, category=category, title=title.strip(), body=body.strip(), severity=severity, channels=channels, mandatory_acknowledgement=mandatory_acknowledgement)
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="notifications.queued", aggregate="NotificationDelivery", aggregate_id=item.id, payload={"user_id": user_id, "channels": channels, "mandatory_acknowledgement": mandatory_acknowledgement})
    return item


@transaction.atomic
def notification_action(*, tenant_id, actor_id, notification_id, expected_version, action, error=""):
    item = NotificationDelivery.objects.select_for_update().filter(id=notification_id, tenant_id=tenant_id).first()
    if not item or item.version != expected_version:
        raise HttpError(409, "Notification is missing or stale")
    if action == "deliver":
        item.status = NotificationDelivery.Status.DELIVERED
        item.attempt_count += 1
    elif action == "fail":
        item.status = NotificationDelivery.Status.FAILED
        item.attempt_count += 1
        item.last_error = error[:300]
        item.escalation_at = timezone.now() + timedelta(minutes=15)
    elif action == "retry" and item.status == NotificationDelivery.Status.FAILED:
        item.status = NotificationDelivery.Status.QUEUED
        item.attempt_count += 1
    elif action == "acknowledge":
        item.status = NotificationDelivery.Status.ACKNOWLEDGED
        item.acknowledged_at = timezone.now()
    elif action == "escalate":
        item.status = NotificationDelivery.Status.ESCALATED
    else:
        raise HttpError(409, "Notification action is not allowed")
    item.version += 1
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action=f"notifications.{item.status}", aggregate="NotificationDelivery", aggregate_id=item.id, payload={"attempt_count": item.attempt_count})
    return item


@transaction.atomic
def assess_centre(*, tenant_id, actor_id, centre, values):
    decision = "go" if all(values.get(key, False) for key in ("scanner_ready", "workstation_ready", "network_ready", "power_ready", "secure_lan_ready", "operators_ready")) else "no_go"
    item = CentreReadiness.objects.create(tenant_id=tenant_id, centre=centre, decision=decision, checked_by_id=actor_id, **values)
    if decision == "go" and centre.status in {CentreProfile.Status.DRAFT, CentreProfile.Status.REVIEW}:
        centre.status = CentreProfile.Status.READY
        centre.version += 1
        centre.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action=f"centres.readiness.{decision}", aggregate="CentreReadiness", aggregate_id=item.id, payload={"centre_id": str(centre.id)})
    return item


@transaction.atomic
def transition_centre(*, tenant_id, actor_id, centre_id, expected_version, target):
    item = CentreProfile.objects.select_for_update().filter(id=centre_id, tenant_id=tenant_id).first()
    if not item or item.version != expected_version:
        raise HttpError(409, "Centre is missing or stale")
    allowed = {
        CentreProfile.Status.DRAFT: {CentreProfile.Status.REVIEW},
        CentreProfile.Status.REVIEW: {CentreProfile.Status.DRAFT, CentreProfile.Status.READY},
        CentreProfile.Status.READY: {CentreProfile.Status.ACTIVE, CentreProfile.Status.REVIEW},
        CentreProfile.Status.ACTIVE: {CentreProfile.Status.CLOSED},
    }
    if target not in allowed.get(item.status, set()):
        raise HttpError(409, "Centre transition is not allowed")
    if target in {CentreProfile.Status.READY, CentreProfile.Status.ACTIVE} and not item.readiness_checks.filter(decision="go").exists():
        raise HttpError(409, "A successful readiness assessment is required")
    previous = item.status
    item.status = target
    item.version += 1
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action=f"centres.centre.{target}", aggregate="CentreProfile", aggregate_id=item.id, payload={"from": previous, "code": item.code})
    return item


@transaction.atomic
def transition_camp(*, tenant_id, actor_id, camp_id, expected_version, target, incidents=None, performance=None):
    item = EvaluationCamp.objects.select_for_update().select_related("centre").filter(id=camp_id, tenant_id=tenant_id).first()
    if not item or item.version != expected_version:
        raise HttpError(409, "Camp is missing or stale")
    allowed = {EvaluationCamp.Status.PLANNED: {EvaluationCamp.Status.ACTIVE}, EvaluationCamp.Status.ACTIVE: {EvaluationCamp.Status.CLOSED}}
    if target not in allowed.get(item.status, set()):
        raise HttpError(409, "Camp transition is not allowed")
    if target == EvaluationCamp.Status.ACTIVE and item.centre.status != CentreProfile.Status.ACTIVE:
        raise HttpError(409, "The centre must be active before the camp starts")
    previous = item.status
    item.status = target
    item.incidents = incidents if incidents is not None else item.incidents
    item.performance = performance if performance is not None else item.performance
    item.version += 1
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action=f"centres.camp.{target}", aggregate="EvaluationCamp", aggregate_id=item.id, payload={"from": previous, "centre_id": str(item.centre_id), "performance": item.performance})
    return item


@transaction.atomic
def calculate_remuneration(*, tenant_id, actor_id, evaluator, session, rule):
    completed = Assignment.objects.filter(tenant_id=tenant_id, evaluator=evaluator, script__paper__session=session, status=Assignment.Status.SUBMITTED).select_related("script__paper")
    attendance = AttendanceRecord.objects.filter(tenant_id=tenant_id, evaluator=evaluator, session=session).exists()
    if not attendance or not completed.exists():
        raise HttpError(409, "Attendance and completed scripts are required before calculation")
    scripts = completed.count()
    pages = sum(item.script.page_count for item in completed)
    questions = sum(item.script.paper.questions.count() for item in completed)
    moderation = ModerationCase.objects.filter(tenant_id=tenant_id, moderator=evaluator, status=ModerationCase.Status.APPROVED).count()
    revaluations = RevaluationRequest.objects.filter(tenant_id=tenant_id, assigned_evaluator=evaluator, status=RevaluationRequest.Status.CLOSED).count()
    gross = Decimal(scripts) * rule.per_script + Decimal(pages) * rule.per_page + Decimal(questions) * rule.per_question + Decimal(moderation) * rule.moderator_rate + Decimal(revaluations) * rule.revaluation_rate
    for slab in sorted(rule.slabs, key=lambda value: value.get("minimum", 0)):
        if scripts >= int(slab.get("minimum", 0)):
            gross += Decimal(str(slab.get("bonus", 0)))
    gross = max(gross, rule.minimum_payment)
    if rule.maximum_payment is not None:
        gross = min(gross, rule.maximum_payment)
    deductions = (gross * rule.tax_percentage / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    net = gross - deductions
    item, created = RemunerationStatement.objects.get_or_create(tenant_id=tenant_id, evaluator=evaluator, session=session, rule=rule, defaults={"units": {"scripts": scripts, "pages": pages, "questions": questions, "moderation": moderation, "revaluations": revaluations}, "gross_amount": gross, "deductions": deductions, "net_amount": net, "calculation": {"attendance_valid": attendance, "tax_percentage": str(rule.tax_percentage)}, "calculated_by_id": actor_id})
    if not created:
        raise HttpError(409, "A statement already exists for this evaluator, session, and rule")
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="remuneration.calculated", aggregate="RemunerationStatement", aggregate_id=item.id, payload={"evaluator_id": str(evaluator.id), "gross": str(gross), "deductions": str(deductions), "net": str(net)})
    return item


@transaction.atomic
def transition_statement(*, tenant_id, actor_id, statement_id, expected_version, target, payment_reference=""):
    item = RemunerationStatement.objects.select_for_update().filter(id=statement_id, tenant_id=tenant_id).first()
    if not item or item.version != expected_version:
        raise HttpError(409, "Statement is missing or stale")
    allowed = {
        RemunerationStatement.Status.CALCULATED: {RemunerationStatement.Status.APPROVED},
        RemunerationStatement.Status.APPROVED: {RemunerationStatement.Status.PAID},
        RemunerationStatement.Status.PAID: {RemunerationStatement.Status.RECONCILED},
    }
    if target not in allowed.get(item.status, set()):
        raise HttpError(409, "Statement transition is not allowed")
    if target == RemunerationStatement.Status.APPROVED:
        if item.calculated_by_id == actor_id:
            raise HttpError(409, "Calculator cannot approve their own statement")
        item.approved_by_id = actor_id
    if target == RemunerationStatement.Status.PAID:
        if len(payment_reference.strip()) < 4:
            raise HttpError(422, "Payment reference is required")
        item.payment_reference = payment_reference.strip()
    previous = item.status
    item.status = target
    item.version += 1
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action=f"remuneration.statement.{target}", aggregate="RemunerationStatement", aggregate_id=item.id, payload={"from": previous, "net_amount": str(item.net_amount), "payment_reference": item.payment_reference})
    return item


@transaction.atomic
def create_student_request(*, tenant_id, actor_id, identity_reference, script, purpose):
    final = FinalMark.objects.filter(tenant_id=tenant_id, script=script, status=FinalMark.Status.LOCKED).first()
    masked = ScriptAsset.objects.filter(tenant_id=tenant_id, script=script, kind__in=[ScriptAsset.Kind.MASTER, ScriptAsset.Kind.EVALUATION], deleted_at__isnull=True).exists()
    eligible = bool(final and masked and script.state == Script.State.FINALIZED)
    if not eligible:
        raise HttpError(409, "Only finalized, masked scripts are eligible for student access")
    item = StudentScriptRequest.objects.create(tenant_id=tenant_id, identity_reference=identity_reference, script=script, purpose=purpose, eligibility={"final_mark_locked": True, "masked_asset": True, "checked_at": timezone.now().isoformat()}, requested_by_id=actor_id)
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="student.script.requested", aggregate="StudentScriptRequest", aggregate_id=item.id, payload={"script_id": str(script.id), "purpose": purpose})
    return item


@transaction.atomic
def seal_evidence(*, tenant_id, actor_id, package_id):
    item = EvidencePackage.objects.select_for_update().select_related("script").filter(id=package_id, tenant_id=tenant_id).first()
    if not item or item.status not in {EvidencePackage.Status.REQUESTED, EvidencePackage.Status.BUILDING}:
        raise HttpError(409, "Evidence package cannot be sealed")
    from apps.core.models import AuditEvent

    events = list(AuditEvent.objects.filter(tenant_id=tenant_id, aggregate_id=str(item.script_id)).order_by("occurred_at").values("id", "actor_id", "action", "aggregate_type", "aggregate_id", "payload", "occurred_at"))
    serializable = [{**event, "id": str(event["id"]), "occurred_at": event["occurred_at"].isoformat()} for event in events]
    manifest = {"script_id": str(item.script_id), "purpose": item.purpose, "events": serializable, "sealed_at": timezone.now().isoformat()}
    item.event_count = len(events)
    item.manifest = manifest
    item.digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    item.status = EvidencePackage.Status.SEALED
    item.sealed_by_id = actor_id
    item.version += 1
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="audit.evidence.sealed", aggregate="EvidencePackage", aggregate_id=item.id, payload={"script_id": str(item.script_id), "digest": item.digest, "event_count": item.event_count})
    return item


@transaction.atomic
def queue_handover(*, tenant_id, actor_id, endpoint, final_mark, idempotency_key):
    if final_mark.status != FinalMark.Status.LOCKED:
        raise HttpError(409, "Only locked final marks can be handed over")
    payload = {"script_reference": str(final_mark.script_id), "mark": str(final_mark.mark), "checksum": final_mark.checksum}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    item, created = ResultHandover.objects.get_or_create(tenant_id=tenant_id, endpoint=endpoint, idempotency_key=idempotency_key, defaults={"final_mark": final_mark, "payload_digest": digest})
    if not created and (item.final_mark_id != final_mark.id or item.payload_digest != digest):
        raise HttpError(409, "Idempotency key was already used with another result")
    if created:
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="integration.handover.queued", aggregate="ResultHandover", aggregate_id=item.id, payload={"endpoint_id": str(endpoint.id), "final_mark_id": str(final_mark.id), "payload_digest": digest})
    return item, not created


@transaction.atomic
def acknowledge_handover(*, tenant_id, actor_id, handover_id, expected_version, status, reference, remote_snapshot):
    item = ResultHandover.objects.select_for_update().filter(id=handover_id, tenant_id=tenant_id).first()
    if not item or item.version != expected_version:
        raise HttpError(409, "Handover is missing or stale")
    if status not in {ResultHandover.Status.ACKNOWLEDGED, ResultHandover.Status.PARTIAL, ResultHandover.Status.REJECTED, ResultHandover.Status.FAILED}:
        raise HttpError(422, "Unsupported ERP acknowledgement")
    item.status = status
    item.acknowledgement_reference = reference
    item.remote_snapshot = remote_snapshot
    expected = {"mark": str(item.final_mark.mark), "checksum": item.final_mark.checksum}
    item.differences = {key: {"expected": value, "received": remote_snapshot.get(key)} for key, value in expected.items() if remote_snapshot.get(key) != value}
    item.attempt_count += 1
    item.version += 1
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action=f"integration.handover.{status}", aggregate="ResultHandover", aggregate_id=item.id, payload={"acknowledgement_reference": reference, "differences": item.differences})
    return item


@transaction.atomic
def transition_handover(*, tenant_id, actor_id, handover_id, expected_version, target):
    item = ResultHandover.objects.select_for_update().filter(id=handover_id, tenant_id=tenant_id).first()
    if not item or item.version != expected_version:
        raise HttpError(409, "Handover is missing or stale")
    allowed = {
        ResultHandover.Status.QUEUED: {ResultHandover.Status.SENT, ResultHandover.Status.FAILED},
        ResultHandover.Status.FAILED: {ResultHandover.Status.QUEUED},
        ResultHandover.Status.ACKNOWLEDGED: {ResultHandover.Status.RECONCILED},
        ResultHandover.Status.PARTIAL: {ResultHandover.Status.RECONCILED},
        ResultHandover.Status.RECONCILED: {ResultHandover.Status.CONFIRMED},
    }
    if target not in allowed.get(item.status, set()):
        raise HttpError(409, "Handover transition is not allowed")
    if target == ResultHandover.Status.CONFIRMED and item.differences:
        raise HttpError(409, "Differences must be cleared before confirmation")
    previous = item.status
    item.status = target
    item.version += 1
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action=f"integration.handover.{target}", aggregate="ResultHandover", aggregate_id=item.id, payload={"from": previous, "differences": item.differences})
    return item


@transaction.atomic
def transition_recovery_plan(*, tenant_id, actor_id, plan_id, expected_version, target):
    item = RecoveryPlan.objects.select_for_update().filter(id=plan_id, tenant_id=tenant_id).first()
    if not item or item.version != expected_version:
        raise HttpError(409, "Recovery plan is missing or stale")
    allowed = {"draft": {"approved"}, "approved": {"active"}, "active": {"approved"}}
    if target not in allowed.get(item.status, set()):
        raise HttpError(409, "Recovery-plan transition is not allowed")
    if target == "active" and not item.drills.filter(status=RecoveryDrill.Status.PASSED).exists():
        raise HttpError(409, "A passed recovery drill is required before activation")
    previous = item.status
    item.status = target
    item.version += 1
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action=f"continuity.plan.{target}", aggregate="RecoveryPlan", aggregate_id=item.id, payload={"from": previous, "rpo_minutes": item.rpo_minutes, "rto_minutes": item.rto_minutes})
    return item


@transaction.atomic
def set_locale(*, tenant_id, actor_id, locale, additional_locales):
    supported = {"en", "kn", "hi", "ta", "te", "ml", "mr", "gu", "bn", "ur"}
    if locale not in supported and locale not in additional_locales:
        raise HttpError(422, "Unsupported interface locale")
    item, _ = LocalePreference.objects.update_or_create(tenant_id=tenant_id, user_id=actor_id, defaults={"locale": locale, "additional_locales": additional_locales})
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="i18n.locale.changed", aggregate="LocalePreference", aggregate_id=item.id, payload={"locale": locale})
    return item


@transaction.atomic
def transition_drill(*, tenant_id, actor_id, drill_id, expected_version, target, measurements=None, integrity_checks=None, report="", corrective_actions=None):
    transitions = {RecoveryDrill.Status.PLANNED: {RecoveryDrill.Status.RUNNING}, RecoveryDrill.Status.RUNNING: {RecoveryDrill.Status.VERIFYING}, RecoveryDrill.Status.VERIFYING: {RecoveryDrill.Status.PASSED, RecoveryDrill.Status.FAILED}}
    item = RecoveryDrill.objects.select_for_update().filter(id=drill_id, tenant_id=tenant_id).first()
    if not item:
        raise HttpError(404, "Recovery drill not found")
    previous = _transition(item, target, transitions, expected_version)
    item.measurements = measurements or item.measurements
    item.integrity_checks = integrity_checks or item.integrity_checks
    item.report = report or item.report
    item.corrective_actions = corrective_actions or item.corrective_actions
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action=f"continuity.drill.{target}", aggregate="RecoveryDrill", aggregate_id=item.id, payload={"from": previous, "plan_id": str(item.plan_id), "measurements": item.measurements})
    return item
