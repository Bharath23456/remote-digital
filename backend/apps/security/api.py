from datetime import timedelta
import secrets

from django.conf import settings
from django.contrib.auth.models import User
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from apps.core.authz import membership_for, require_roles, require_step_up
from apps.core.services import record_event
from apps.identity_auth.models import AccessSession, AuthenticationMethod, DeviceAuthorization, OidcProvider
from apps.identity_auth.services import active_session_for_request, policy_for
from apps.security.crypto import encrypt_secret
from apps.tenancy.custom_fields import validate_custom_values
from apps.tenancy.models import Membership, TenantAccount

from .models import DlpIncident, EmergencyAccessGrant, EncryptionKeyMetadata, PrivilegedAccessRequest, SecurityAlert, SecurityPolicy
from .schemas import (
    AlertStatusIn,
    EmergencyGrantIn,
    MembershipAccessIn,
    MembershipCreateIn,
    OidcProviderIn,
    PrivilegedDecisionIn,
    PrivilegedRequestIn,
    SecurityPolicyIn,
)
from .services import decide_privileged_request, grant_emergency_access, update_policy


router = Router(tags=["Data security and access governance"])


def _policy_data(policy):
    return {
        "id": str(policy.id) if policy else None,
        "version": policy.version if policy else 0,
        "session_timeout_minutes": policy.session_timeout_minutes if policy else 30,
        "maximum_concurrent_sessions": policy.maximum_concurrent_sessions if policy else 3,
        "step_up_minutes": policy.step_up_minutes if policy else 10,
        "failed_login_limit": policy.failed_login_limit if policy else 5,
        "lockout_minutes": policy.lockout_minutes if policy else 15,
        "require_mfa": policy.require_mfa if policy else False,
        "require_trusted_device": policy.require_trusted_device if policy else False,
        "approved_networks": policy.approved_networks if policy else [],
        "allowed_countries": policy.allowed_countries if policy else [],
        "vpn_risk_threshold": policy.vpn_risk_threshold if policy else 70,
        "alert_risk_threshold": policy.alert_risk_threshold if policy else 50,
        "dlp_enabled": policy.dlp_enabled if policy else True,
        "evaluation_camera_required": policy.evaluation_camera_required if policy else True,
        "evaluation_fullscreen_required": policy.evaluation_fullscreen_required if policy else True,
        "evaluation_single_screen_required": policy.evaluation_single_screen_required if policy else True,
        "evaluation_event_recording": policy.evaluation_event_recording if policy else True,
        "evaluation_heartbeat_seconds": policy.evaluation_heartbeat_seconds if policy else 15,
        "evaluation_no_face_seconds": policy.evaluation_no_face_seconds if policy else 30,
        "evaluation_retention_days": policy.evaluation_retention_days if policy else 30,
    }


@router.get("/catalog")
def security_catalog(request):
    membership = require_roles(
        request,
        Membership.Role.PLATFORM_ADMIN,
        Membership.Role.UNIVERSITY_ADMIN,
        Membership.Role.EXAM_CONTROLLER,
        Membership.Role.AUDITOR,
    )
    tenant_id = membership.institution.tenant_id
    policy = SecurityPolicy.objects.filter(tenant_id=tenant_id).first()
    alerts = SecurityAlert.objects.filter(tenant_id=tenant_id).order_by("-created_at")[:100]
    access_requests = PrivilegedAccessRequest.objects.filter(tenant_id=tenant_id).select_related("requester", "decided_by")[:100]
    grants = EmergencyAccessGrant.objects.filter(tenant_id=tenant_id).select_related("user", "granted_by")[:100]
    dlp_incidents = DlpIncident.objects.filter(tenant_id=tenant_id).order_by("-created_at")[:100]
    members = Membership.objects.filter(institution__tenant_id=tenant_id).select_related("user", "institution").order_by("user__first_name")
    authenticator_users = set(AuthenticationMethod.objects.filter(
        user_id__in=members.values("user_id"), kind=AuthenticationMethod.Kind.TOTP, is_active=True
    ).values_list("user_id", flat=True))
    shared_users = set(Membership.objects.filter(user_id__in=members.values("user_id"), is_active=True).exclude(
        institution__tenant_id=tenant_id
    ).values_list("user_id", flat=True))
    account = TenantAccount.objects.filter(root_institution__tenant_id=tenant_id).first()
    providers = OidcProvider.objects.filter(tenant_id=tenant_id).order_by("name")
    devices = DeviceAuthorization.objects.filter(tenant_id=tenant_id).select_related("device__user", "approved_by").order_by("-created_at")[:100]
    keys = EncryptionKeyMetadata.objects.order_by("purpose")
    return {
        "policy": _policy_data(policy),
        "devices": [
            {
                "id": str(item.device_id),
                "user": item.device.user.get_full_name() or item.device.user.username,
                "email": item.device.user.email,
                "label": item.device.label,
                "platform": item.device.platform,
                "last_seen_at": item.device.last_seen_at.isoformat() if item.device.last_seen_at else None,
                "status": "revoked" if item.revoked_at or item.device.revoked_at else "approved" if item.approved_at else "pending",
            }
            for item in devices
        ],
        "alerts": [
            {
                "id": str(item.id),
                "category": item.category,
                "severity": item.severity,
                "title": item.title,
                "details": item.details,
                "status": item.status,
                "version": item.version,
                "created_at": item.created_at.isoformat(),
            }
            for item in alerts
        ],
        "access_requests": [
            {
                "id": str(item.id),
                "requester": item.requester.get_full_name() or item.requester.username,
                "requested_role": item.requested_role,
                "reason": item.reason,
                "status": item.status,
                "starts_at": item.starts_at.isoformat() if item.starts_at else None,
                "expires_at": item.expires_at.isoformat() if item.expires_at else None,
                "decided_by": (item.decided_by.get_full_name() or item.decided_by.username) if item.decided_by else None,
                "version": item.version,
            }
            for item in access_requests
        ],
        "emergency_grants": [
            {
                "id": str(item.id),
                "user": item.user.get_full_name() or item.user.username,
                "role": item.role,
                "incident_reference": item.incident_reference,
                "expires_at": item.expires_at.isoformat(),
                "revoked_at": item.revoked_at.isoformat() if item.revoked_at else None,
            }
            for item in grants
        ],
        "dlp_incidents": [
            {
                "id": str(item.id),
                "channel": item.channel,
                "data_classification": item.data_classification,
                "rule": item.rule,
                "resource_reference": item.resource_reference,
                "status": item.status,
                "details": item.details,
                "created_at": item.created_at.isoformat(),
            }
            for item in dlp_incidents
        ],
        "members": [
            {
                "id": str(item.id),
                "user_id": item.user_id,
                "name": item.user.get_full_name() or item.user.username,
                "email": item.user.email,
                "authenticator_enabled": item.user_id in authenticator_users,
                "can_reset_authenticator": item.is_active and item.user_id in authenticator_users and item.user_id != request.auth.id and (
                    membership.role == Membership.Role.PLATFORM_ADMIN or (
                        item.role not in {Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN}
                        and item.user_id not in shared_users
                    )
                ),
                "institution": item.institution.name,
                "role": item.role,
                "permissions": item.permissions,
                "enabled_modules": item.enabled_modules,
                "custom_fields": item.custom_fields,
                "is_active": item.is_active,
            }
            for item in members
        ],
        "oidc_providers": [
            {
                "id": str(item.id),
                "name": item.name,
                "issuer": item.issuer,
                "client_id": item.client_id,
                "scopes": item.scopes,
                "domain_hint": item.domain_hint,
                "is_active": item.is_active,
            }
            for item in providers
        ],
        "key_inventory": [
            {
                "id": str(item.id),
                "purpose": item.purpose,
                "provider": item.provider,
                "key_reference": item.key_reference,
                "rotated_at": item.rotated_at.isoformat(),
                "next_rotation_at": item.next_rotation_at.isoformat(),
                "is_active": item.is_active,
            }
            for item in keys
        ],
        "controls": {
            "encryption_at_rest": bool(getattr(settings, "SCRIPT_STORAGE_INTERNAL_URL", "")),
            "tls_enforced": not settings.DEBUG,
            "database_protection": settings.DATABASES["default"]["ENGINE"].endswith("postgresql"),
            "file_protection": bool(getattr(settings, "SCRIPT_STORAGE_INTERNAL_URL", "")),
            "key_management": bool(getattr(settings, "KMS_PROVIDER", "")) or keys.filter(is_active=True).exists(),
            "secrets_management": bool(getattr(settings, "SECRETS_PROVIDER", "")),
            "secure_cookies": settings.SESSION_COOKIE_SECURE,
            "rbac": True,
            "least_privilege": True,
            "zero_trust_access": True,
            "identity_store_isolated": bool(getattr(settings, "IDENTITY_SERVICE_URL", "")),
            "script_store_isolated": bool(getattr(settings, "SCRIPT_STORAGE_INTERNAL_URL", "")),
            "identity_service_authorization": bool(getattr(settings, "IDENTITY_AUTHORIZATION_KEY", "")),
            "controlled_identity_resolution": bool(getattr(settings, "IDENTITY_SERVICE_URL", "")),
            "waf_configured": bool(getattr(settings, "WAF_PROVIDER", "")),
            "ddos_protection": bool(getattr(settings, "DDOS_PROVIDER", "")),
            "intrusion_detection": bool(getattr(settings, "IDS_PROVIDER", "")),
            "security_monitoring": bool(getattr(settings, "SECURITY_MONITORING_PROVIDER", "")),
            "siem_configured": bool(policy and policy.siem_webhook_ciphertext),
            "vulnerability_monitoring": bool(getattr(settings, "VULNERABILITY_SCANNER", "")),
            "security_alerts": True,
            "dlp_enabled": policy.dlp_enabled if policy else True,
        },
        "module_catalog": account.enabled_modules if account else [],
    }


def _validate_membership_access(actor_membership, role, enabled_modules):
    if role not in Membership.Role.values:
        raise HttpError(422, "Unsupported role")
    if role == Membership.Role.PLATFORM_ADMIN and actor_membership.role != Membership.Role.PLATFORM_ADMIN:
        raise HttpError(403, "Only a platform administrator can grant platform access")
    account = TenantAccount.objects.filter(root_institution__tenant_id=actor_membership.institution.tenant_id).first()
    available = set(account.enabled_modules if account else [])
    requested = set(enabled_modules)
    if not requested:
        raise HttpError(422, "Select at least one module")
    if not requested.issubset(available):
        raise HttpError(422, "One or more selected modules are not enabled for this university")
    if role == Membership.Role.EVALUATOR and requested != {"evaluation"}:
        raise HttpError(422, "Evaluator accounts can access only the Evaluation module")
    desk_modules = {
        Membership.Role.BUNDLE_PREPARER: "receiving",
        Membership.Role.INTAKE_RECEIVER: "custody",
        Membership.Role.SCAN_OPERATOR: "digitization",
    }
    if role in desk_modules and requested != {desk_modules[role]}:
        raise HttpError(422, "Select only the assigned intake desk module for this role")
    if role == Membership.Role.OPERATIONS_SUPERVISOR and requested != {"receiving", "custody", "digitization"}:
        raise HttpError(422, "Operations supervisors require exactly the receiving, custody and digitization modules")
    return sorted(requested)


@router.post("/memberships")
def create_membership(request, payload: MembershipCreateIn):
    actor_membership = require_roles(request, Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN)
    require_step_up(request)
    email = payload.email.strip().lower()
    try:
        validate_email(email)
    except ValidationError as exc:
        raise HttpError(422, "Enter a valid email address") from exc
    modules = _validate_membership_access(actor_membership, payload.role, payload.enabled_modules)
    if payload.role == Membership.Role.OPERATIONS_SUPERVISOR and payload.permissions:
        raise HttpError(422, "Operations supervisors cannot receive additional permissions")
    custom_fields = validate_custom_values(
        tenant_id=actor_membership.institution.tenant_id,
        form_key="user_access",
        values=payload.custom_fields,
    )
    with transaction.atomic():
        user = User.objects.select_for_update().filter(username__iexact=email).first()
        created = user is None
        temporary_password = ""
        if created:
            user = User(username=email, email=email, first_name=payload.first_name.strip(), last_name=payload.last_name.strip())
        if created or not user.has_usable_password():
            temporary_password = secrets.token_urlsafe(15)
            user.set_password(temporary_password)
            user.save()
        if Membership.objects.filter(user=user, institution__tenant_id=actor_membership.institution.tenant_id).exists():
            raise HttpError(409, "This user already belongs to the university")
        membership = Membership.objects.create(
            user=user,
            institution=actor_membership.institution,
            role=payload.role,
            permissions=sorted(set(payload.permissions)),
            enabled_modules=modules,
            custom_fields=custom_fields,
            must_change_password=bool(temporary_password),
        )
        record_event(
            tenant_id=actor_membership.institution.tenant_id,
            actor_id=request.auth.id,
            action="security.membership.created",
            aggregate="Membership",
            aggregate_id=membership.id,
            payload={"user_id": user.id, "role": membership.role, "enabled_modules": modules, "existing_identity": not created},
        )
    return {
        "id": str(membership.id),
        "user_id": user.id,
        "email": email,
        "temporary_password": temporary_password,
        "existing_identity": not created,
        "role": membership.role,
        "enabled_modules": membership.enabled_modules,
    }


@router.put("/policy")
def save_security_policy(request, payload: SecurityPolicyIn):
    membership = require_roles(request, Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN)
    require_step_up(request)
    with transaction.atomic():
        if payload.require_trusted_device:
            current = active_session_for_request(request)
            if not current or not current.device or current.device.revoked_at:
                raise HttpError(422, "Sign in from a recognized device before requiring device approval")
            authorization, _ = DeviceAuthorization.objects.get_or_create(tenant_id=membership.institution.tenant_id, device=current.device)
            if authorization.revoked_at:
                raise HttpError(403, "Your current device has been revoked")
            if not authorization.approved_at:
                authorization.approved_at = timezone.now()
                authorization.approved_by = request.auth
                authorization.save(update_fields=["approved_at", "approved_by", "updated_at"])
        policy = update_policy(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, payload=payload)
    return _policy_data(policy)


@router.post("/devices/{device_id}/approve")
def approve_device(request, device_id: str):
    membership = require_roles(request, Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN)
    require_step_up(request)
    tenant_id = membership.institution.tenant_id
    with transaction.atomic():
        authorization = DeviceAuthorization.objects.select_for_update().select_related("device").filter(tenant_id=tenant_id, device_id=device_id).first()
        if not authorization or authorization.revoked_at or authorization.device.revoked_at:
            raise HttpError(404, "Pending device not found")
        if not Membership.objects.filter(user=authorization.device.user, institution__tenant_id=tenant_id, is_active=True).exists():
            raise HttpError(403, "Device owner is not an active university member")
        authorization.approved_at = timezone.now()
        authorization.approved_by = request.auth
        authorization.save(update_fields=["approved_at", "approved_by", "updated_at"])
        AccessSession.objects.filter(tenant_id=tenant_id, device_id=device_id, revoked_at__isnull=True).update(trusted_device=True)
        record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="auth.device.approved", aggregate="DeviceAuthorization", aggregate_id=authorization.id)
    return {"id": device_id, "status": "approved"}


@router.post("/devices/{device_id}/revoke")
def revoke_tenant_device(request, device_id: str):
    membership = require_roles(request, Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN)
    require_step_up(request)
    tenant_id = membership.institution.tenant_id
    with transaction.atomic():
        authorization = DeviceAuthorization.objects.select_for_update().filter(tenant_id=tenant_id, device_id=device_id, revoked_at__isnull=True).first()
        if not authorization:
            raise HttpError(404, "Device not found")
        authorization.revoked_at = timezone.now()
        authorization.save(update_fields=["revoked_at", "updated_at"])
        AccessSession.objects.filter(tenant_id=tenant_id, device_id=device_id, revoked_at__isnull=True).update(revoked_at=timezone.now(), revoked_reason="admin_device_revoked")
        record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="auth.device.revoked_by_admin", aggregate="DeviceAuthorization", aggregate_id=authorization.id)
    return {"id": device_id, "status": "revoked"}


@router.post("/memberships/{membership_id}/reset-authenticator")
def reset_member_authenticator(request, membership_id: str):
    actor = require_roles(request, Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN)
    session = require_step_up(request)
    tenant_id = actor.institution.tenant_id
    if policy_for(tenant_id).require_mfa and not session.mfa_verified_at:
        raise HttpError(403, "Sign in with your authenticator before resetting another user's MFA")
    with transaction.atomic():
        target = Membership.objects.select_for_update().select_related("user").filter(
            id=membership_id, institution__tenant_id=tenant_id, is_active=True
        ).first()
        if not target:
            raise HttpError(404, "Active university user not found")
        if target.user_id == request.auth.id:
            raise HttpError(403, "Use your own authenticator replacement flow")
        if actor.role != Membership.Role.PLATFORM_ADMIN:
            if target.role in {Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN}:
                raise HttpError(403, "Only a platform administrator can reset an administrator's authenticator")
            if Membership.objects.filter(user=target.user, is_active=True).exclude(institution__tenant_id=tenant_id).exists():
                raise HttpError(403, "A shared account requires platform administrator recovery")
        methods = AuthenticationMethod.objects.select_for_update().filter(
            user=target.user, kind=AuthenticationMethod.Kind.TOTP, is_active=True
        )
        if not methods.exists():
            raise HttpError(409, "This user has no active authenticator to reset")
        methods.update(is_active=False, is_primary=False, secret_ciphertext="")
        AccessSession.objects.filter(user=target.user, revoked_at__isnull=True).update(
            revoked_at=timezone.now(), revoked_reason="authenticator_reset"
        )
        record_event(
            tenant_id=tenant_id,
            actor_id=request.auth.id,
            action="auth.totp.reset_by_admin",
            aggregate="Membership",
            aggregate_id=target.id,
            payload={"user_id": target.user_id},
        )
    return {"ok": True, "email": target.user.email}


@router.post("/alerts/{alert_id}/status")
def update_alert_status(request, alert_id: str, payload: AlertStatusIn):
    membership = require_roles(request, Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN, Membership.Role.AUDITOR)
    if payload.status not in SecurityAlert.Status.values:
        raise HttpError(422, "Unsupported alert status")
    with transaction.atomic():
        alert = SecurityAlert.objects.select_for_update().filter(id=alert_id, tenant_id=membership.institution.tenant_id).first()
        if not alert:
            raise HttpError(404, "Security alert not found")
        if alert.version != payload.version:
            raise HttpError(409, "Security alert was changed by another user")
        alert.status = payload.status
        alert.resolved_at = timezone.now() if payload.status == SecurityAlert.Status.RESOLVED else None
        alert.assigned_to = request.auth
        alert.version += 1
        alert.save()
        record_event(
            tenant_id=membership.institution.tenant_id,
            actor_id=request.auth.id,
            action="security.alert.status_changed",
            aggregate="SecurityAlert",
            aggregate_id=alert.id,
            payload={"status": alert.status},
        )
    return {"id": str(alert.id), "status": alert.status, "version": alert.version}


@router.post("/privileged-access")
def request_privileged_access(request, payload: PrivilegedRequestIn):
    membership = membership_for(request)
    if not 15 <= payload.duration_minutes <= 480:
        raise HttpError(422, "Privileged access duration must be between 15 and 480 minutes")
    if not payload.reason.strip():
        raise HttpError(422, "A reason is required")
    with transaction.atomic():
        access_request = PrivilegedAccessRequest.objects.create(
            tenant_id=membership.institution.tenant_id,
            requester=request.auth,
            requested_role=payload.requested_role,
            reason=payload.reason,
            expires_at=timezone.now() + timedelta(minutes=payload.duration_minutes),
        )
        record_event(
            tenant_id=membership.institution.tenant_id,
            actor_id=request.auth.id,
            action="security.privileged_access.requested",
            aggregate="PrivilegedAccessRequest",
            aggregate_id=access_request.id,
            payload={"requested_role": access_request.requested_role},
        )
    return {"id": str(access_request.id), "status": access_request.status, "version": access_request.version}


@router.post("/privileged-access/{request_id}/decision")
def privileged_access_decision(request, request_id: str, payload: PrivilegedDecisionIn):
    membership = require_roles(request, Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN)
    require_step_up(request)
    access_request = decide_privileged_request(
        tenant_id=membership.institution.tenant_id,
        actor=request.auth,
        request_id=request_id,
        version=payload.version,
        approve=payload.approve,
        note=payload.note,
    )
    return {"id": str(access_request.id), "status": access_request.status, "version": access_request.version}


@router.post("/emergency-access")
def emergency_access(request, payload: EmergencyGrantIn):
    membership = require_roles(request, Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN)
    require_step_up(request)
    user = User.objects.filter(id=payload.user_id, is_active=True).first()
    if not user:
        raise HttpError(404, "User not found")
    grant = grant_emergency_access(
        tenant_id=membership.institution.tenant_id,
        actor=request.auth,
        user=user,
        role=payload.role,
        incident_reference=payload.incident_reference,
        justification=payload.justification,
        duration_minutes=payload.duration_minutes,
    )
    return {"id": str(grant.id), "expires_at": grant.expires_at.isoformat()}


@router.post("/emergency-access/{grant_id}/revoke")
def revoke_emergency_access(request, grant_id: str):
    membership = require_roles(request, Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN)
    require_step_up(request)
    with transaction.atomic():
        grant = EmergencyAccessGrant.objects.select_for_update().filter(
            id=grant_id, tenant_id=membership.institution.tenant_id, revoked_at__isnull=True
        ).first()
        if not grant:
            raise HttpError(404, "Active emergency grant not found")
        grant.revoked_at = timezone.now()
        grant.save(update_fields=["revoked_at", "updated_at"])
        record_event(
            tenant_id=membership.institution.tenant_id,
            actor_id=request.auth.id,
            action="security.emergency_access.revoked",
            aggregate="EmergencyAccessGrant",
            aggregate_id=grant.id,
        )
    return {"id": str(grant.id), "revoked": True}


@router.put("/memberships/{membership_id}")
def update_membership_access(request, membership_id: str, payload: MembershipAccessIn):
    actor_membership = require_roles(request, Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN)
    require_step_up(request)
    modules = _validate_membership_access(actor_membership, payload.role, payload.enabled_modules)
    if payload.role == Membership.Role.OPERATIONS_SUPERVISOR and payload.permissions:
        raise HttpError(422, "Operations supervisors cannot receive additional permissions")
    with transaction.atomic():
        target = Membership.objects.select_for_update().filter(
            id=membership_id, institution__tenant_id=actor_membership.institution.tenant_id
        ).first()
        if not target:
            raise HttpError(404, "Membership not found")
        if target.user_id == request.auth.id and not payload.is_active:
            raise HttpError(409, "You cannot deactivate your own membership")
        previous = {"role": target.role, "permissions": target.permissions, "enabled_modules": target.enabled_modules, "is_active": target.is_active}
        target.role = payload.role
        target.permissions = sorted(set(payload.permissions))
        target.enabled_modules = modules
        target.is_active = payload.is_active
        target.save(update_fields=["role", "permissions", "enabled_modules", "is_active", "updated_at"])
        record_event(
            tenant_id=actor_membership.institution.tenant_id,
            actor_id=request.auth.id,
            action="security.membership.updated",
            aggregate="Membership",
            aggregate_id=target.id,
            payload={"before": previous, "role": target.role, "permissions": target.permissions, "enabled_modules": target.enabled_modules, "is_active": target.is_active},
        )
    return {"id": str(target.id), "role": target.role, "permissions": target.permissions, "enabled_modules": target.enabled_modules, "is_active": target.is_active}


@router.post("/oidc-providers")
def create_oidc_provider(request, payload: OidcProviderIn):
    membership = require_roles(request, Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN)
    require_step_up(request)
    if not payload.issuer.startswith("https://"):
        raise HttpError(422, "OIDC issuer must use HTTPS")
    with transaction.atomic():
        provider = OidcProvider.objects.create(
            tenant_id=membership.institution.tenant_id,
            name=payload.name,
            issuer=payload.issuer.rstrip("/"),
            client_id=payload.client_id,
            client_secret_ciphertext=encrypt_secret(payload.client_secret),
            scopes=payload.scopes,
            domain_hint=payload.domain_hint.lower(),
        )
        record_event(
            tenant_id=membership.institution.tenant_id,
            actor_id=request.auth.id,
            action="security.oidc_provider.created",
            aggregate="OidcProvider",
            aggregate_id=provider.id,
            payload={"name": provider.name, "issuer": provider.issuer},
        )
    return {"id": str(provider.id), "name": provider.name, "is_active": provider.is_active}


@router.get("/dlp-incidents")
def list_dlp_incidents(request):
    membership = require_roles(request, Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN, Membership.Role.AUDITOR)
    return list(
        DlpIncident.objects.filter(tenant_id=membership.institution.tenant_id).values(
            "id", "channel", "data_classification", "rule", "resource_reference", "status", "details", "created_at"
        )[:200]
    )
