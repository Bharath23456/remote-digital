import ipaddress
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from ninja.errors import HttpError

from apps.core.services import record_event
from apps.identity_auth.models import AccessSession
from apps.tenancy.models import Membership

from .models import EmergencyAccessGrant, PrivilegedAccessRequest, SecurityPolicy


def validate_policy(payload):
    if not 5 <= payload.session_timeout_minutes <= 720:
        raise HttpError(422, "Session timeout must be between 5 and 720 minutes")
    if not 1 <= payload.maximum_concurrent_sessions <= 10:
        raise HttpError(422, "Concurrent session limit must be between 1 and 10")
    if not 2 <= payload.step_up_minutes <= 30:
        raise HttpError(422, "Step-up duration must be between 2 and 30 minutes")
    if not 3 <= payload.failed_login_limit <= 20:
        raise HttpError(422, "Failed login limit must be between 3 and 20")
    if not 1 <= payload.lockout_minutes <= 1440:
        raise HttpError(422, "Lockout duration must be between 1 and 1440 minutes")
    if not 0 <= payload.vpn_risk_threshold <= 100 or not 0 <= payload.alert_risk_threshold <= 100:
        raise HttpError(422, "Risk thresholds must be between 0 and 100")
    if not 5 <= payload.evaluation_heartbeat_seconds <= 60:
        raise HttpError(422, "Evaluation heartbeat must be between 5 and 60 seconds")
    if not 10 <= payload.evaluation_no_face_seconds <= 300:
        raise HttpError(422, "No-face pause must be between 10 and 300 seconds")
    if not 1 <= payload.evaluation_retention_days <= 365:
        raise HttpError(422, "Evidence retention must be between 1 and 365 days")
    try:
        for network in payload.approved_networks:
            ipaddress.ip_network(network, strict=False)
    except ValueError as exc:
        raise HttpError(422, f"Invalid approved network: {exc}") from exc
    if any(len(country) != 2 or not country.isalpha() for country in payload.allowed_countries):
        raise HttpError(422, "Allowed countries must use two-letter ISO codes")


def update_policy(*, tenant_id, actor_id, payload):
    validate_policy(payload)
    with transaction.atomic():
        policy = SecurityPolicy.objects.select_for_update().filter(tenant_id=tenant_id).first()
        if not policy:
            if payload.version not in (0, 1):
                raise HttpError(409, "Security policy version is stale")
            policy = SecurityPolicy(tenant_id=tenant_id)
        elif policy.version != payload.version:
            raise HttpError(409, "Security policy was changed by another user")
        for field in (
            "session_timeout_minutes",
            "maximum_concurrent_sessions",
            "step_up_minutes",
            "failed_login_limit",
            "lockout_minutes",
            "require_mfa",
            "require_trusted_device",
            "approved_networks",
            "allowed_countries",
            "vpn_risk_threshold",
            "alert_risk_threshold",
            "dlp_enabled",
            "evaluation_camera_required",
            "evaluation_fullscreen_required",
            "evaluation_single_screen_required",
            "evaluation_event_recording",
            "evaluation_heartbeat_seconds",
            "evaluation_no_face_seconds",
            "evaluation_retention_days",
        ):
            setattr(policy, field, getattr(payload, field))
        policy.allowed_countries = [item.upper() for item in policy.allowed_countries]
        policy.version = policy.version + 1 if policy.pk else 1
        policy.save()
        record_event(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="security.policy.updated",
            aggregate="SecurityPolicy",
            aggregate_id=policy.id,
            payload={"version": policy.version},
        )
    return policy


def decide_privileged_request(*, tenant_id, actor, request_id, version, approve, note):
    with transaction.atomic():
        access_request = PrivilegedAccessRequest.objects.select_for_update().filter(id=request_id, tenant_id=tenant_id).first()
        if not access_request:
            raise HttpError(404, "Privileged access request not found")
        if access_request.version != version:
            raise HttpError(409, "Privileged access request was changed by another user")
        if access_request.status != PrivilegedAccessRequest.Status.PENDING:
            raise HttpError(409, "Privileged access request is no longer pending")
        if access_request.requester_id == actor.id:
            raise HttpError(409, "A requester cannot approve their own privileged access")
        now = timezone.now()
        access_request.status = PrivilegedAccessRequest.Status.APPROVED if approve else PrivilegedAccessRequest.Status.REJECTED
        access_request.decided_by = actor
        access_request.decided_at = now
        access_request.decision_note = note
        if approve:
            access_request.starts_at = now
        access_request.version += 1
        access_request.save()
        record_event(
            tenant_id=tenant_id,
            actor_id=actor.id,
            action="security.privileged_access.decided",
            aggregate="PrivilegedAccessRequest",
            aggregate_id=access_request.id,
            payload={"status": access_request.status, "requested_role": access_request.requested_role},
        )
    return access_request


def grant_emergency_access(*, tenant_id, actor, user, role, incident_reference, justification, duration_minutes):
    if not 5 <= duration_minutes <= 120:
        raise HttpError(422, "Emergency access must last between 5 and 120 minutes")
    if user.id == actor.id:
        raise HttpError(409, "Emergency access cannot be self-granted")
    if role not in Membership.Role.values or role == Membership.Role.PLATFORM_ADMIN:
        raise HttpError(422, "Emergency access role is not supported")
    if len(incident_reference.strip()) < 4 or len(justification.strip()) < 12:
        raise HttpError(422, "Incident reference and a specific justification are required")
    if not Membership.objects.filter(user=user, institution__tenant_id=tenant_id, is_active=True).exists():
        raise HttpError(404, "User not found in this tenant")
    with transaction.atomic():
        grant = EmergencyAccessGrant.objects.create(
            tenant_id=tenant_id,
            user=user,
            role=role,
            incident_reference=incident_reference,
            justification=justification,
            granted_by=actor,
            expires_at=timezone.now() + timedelta(minutes=duration_minutes),
        )
        AccessSession.objects.filter(user=user, tenant_id=tenant_id, revoked_at__isnull=True).update(step_up_expires_at=grant.expires_at)
        record_event(
            tenant_id=tenant_id,
            actor_id=actor.id,
            action="security.emergency_access.granted",
            aggregate="EmergencyAccessGrant",
            aggregate_id=grant.id,
            payload={"user_id": user.id, "role": role, "expires_at": grant.expires_at.isoformat()},
        )
    return grant
