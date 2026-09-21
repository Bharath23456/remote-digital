import hashlib
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate, logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponseRedirect
from django.middleware.csrf import get_token
from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from apps.core.authz import membership_for, require_roles
from apps.core.services import record_event
from apps.security.models import SecurityPolicy
from apps.tenancy.models import Membership, TenantAccount

from .models import AccessSession, AuthenticationHistory, AuthenticationMethod, LoginAttempt, OidcProvider, PasskeyCredential, TrustedDevice
from .schemas import (
    DeviceTrustIn,
    LoginIn,
    MfaEnrollmentConfirmIn,
    MfaVerifyIn,
    PasskeyCompleteIn,
    PasskeyLoginBeginIn,
    PasskeyLoginCompleteIn,
    PasswordChangeIn,
    PasswordUpdateIn,
    SsoStartIn,
    StepUpIn,
    TotpConfirmIn,
    TotpSetupIn,
)
from .services import (
    active_session_for_request,
    assess_login_risk,
    begin_oidc_login,
    begin_passkey_login,
    begin_passkey_registration,
    begin_totp_setup,
    client_ip,
    complete_oidc_login,
    complete_passkey_login,
    complete_passkey_registration,
    confirm_totp_setup,
    device_hash,
    finalize_login,
    identifier_hash,
    membership_for_user,
    policy_for,
    record_auth_history,
    OidcError,
    verify_totp_for_user,
)


router = Router(tags=["Authentication and session access"])


@router.get("/csrf", auth=None)
def csrf_token(request):
    return {"csrf_token": get_token(request)}


@router.get("/sso/providers", auth=None)
def sso_providers(request):
    providers = OidcProvider.objects.filter(is_active=True)
    if getattr(request, "resolved_tenant_id", None):
        providers = providers.filter(tenant_id=request.resolved_tenant_id)
    providers = providers.order_by("name")
    return [
        {"id": str(item.id), "name": item.name, "domain_hint": item.domain_hint}
        for item in providers
    ]


@router.post("/sso/start", auth=None)
def sso_start(request, payload: SsoStartIn):
    provider = OidcProvider.objects.filter(id=payload.provider_id, is_active=True).first()
    if not provider:
        raise HttpError(404, "Institutional sign-in provider not found")
    if policy_for(provider.tenant_id).require_mfa:
        raise HttpError(403, "This university requires password and authenticator app sign-in")
    context = {
        "raw_device_id": payload.device_id,
        "device_label": payload.device_label,
        "platform": payload.platform,
        "browser": payload.browser,
        "location": payload.location,
        "risk_score": 0,
        "risk_reasons": [],
    }
    try:
        return {"authorization_url": begin_oidc_login(request, provider, context)}
    except OidcError as exc:
        raise HttpError(502, str(exc)) from exc


@router.get("/sso/callback", auth=None)
def sso_callback(request, state: str = "", code: str = "", error: str = ""):
    base_url = settings.APP_BASE_URL or request.build_absolute_uri("/").rstrip("/")
    if error or not state or not code:
        return HttpResponseRedirect(f"{base_url}/?sso=failed")
    try:
        user, membership, context, mfa_verified, claims = complete_oidc_login(request, state=state, code=code)
        if policy_for(membership.institution.tenant_id).require_mfa:
            return HttpResponseRedirect(f"{base_url}/?sso=authenticator_required")
        risk_score, reasons, _device, hashed_device = assess_login_risk(request, user, context.pop("raw_device_id", ""))
        context.update({"risk_score": risk_score, "risk_reasons": reasons, "device_hash": hashed_device})
        membership, session = finalize_login(request, user, context, mfa_verified=mfa_verified)
        record_auth_history(
            user=user,
            membership=membership,
            event="oidc",
            outcome=AuthenticationHistory.Outcome.SUCCESS,
            request=request,
            hashed_device=hashed_device,
            risk_score=risk_score,
            reasons=reasons,
            metadata={"provider": str(claims.get("iss", "")), "mfa": mfa_verified},
        )
        return HttpResponseRedirect(f"{base_url}/?sso=success")
    except ValueError:
        return HttpResponseRedirect(f"{base_url}/?sso=failed")


def _user_context(user, membership, session):
    memberships = Membership.objects.filter(user=user, is_active=True, institution__is_active=True).select_related("institution").order_by("institution__name")
    tenants = []
    seen = set()
    for item in memberships:
        tenant_id = str(item.institution.tenant_id)
        if tenant_id in seen:
            continue
        seen.add(tenant_id)
        root = item.institution if item.institution.parent_id is None else item.institution.__class__.objects.filter(tenant_id=item.institution.tenant_id, parent__isnull=True).order_by("created_at").first()
        tenants.append({"id": tenant_id, "name": root.name if root else item.institution.name, "role": item.role})
    account = TenantAccount.objects.filter(root_institution__tenant_id=membership.institution.tenant_id).first()
    tenant_modules = account.enabled_modules if account else []
    fixed_desk_roles = {Membership.Role.BUNDLE_PREPARER, Membership.Role.INTAKE_RECEIVER, Membership.Role.SCAN_OPERATOR, Membership.Role.OPERATIONS_SUPERVISOR}
    member_modules = membership.enabled_modules if membership.role in fixed_desk_roles else membership.enabled_modules or tenant_modules
    enabled_modules = [module for module in tenant_modules if module in member_modules]
    return {
        "user": {"id": user.id, "name": user.get_full_name() or user.username, "email": user.email},
        "tenant": {
            "id": str(membership.institution.tenant_id),
            "name": membership.institution.name,
            "code": membership.institution.code,
        },
        "role": membership.role,
        "permissions": membership.permissions,
        "must_change_password": membership.must_change_password,
        "enabled_modules": enabled_modules,
        "tenants": tenants,
        "session": {
            "id": str(session.id),
            "expires_at": session.expires_at.isoformat(),
            "timeout_minutes": policy_for(membership.institution.tenant_id).session_timeout_minutes,
            "risk_score": session.risk_score,
            "mfa_verified": bool(session.mfa_verified_at),
            "step_up_valid": session.is_step_up_valid,
        },
    }


@router.post("/login", auth=None, response={200: dict, 202: dict})
def sign_in(request, payload: LoginIn):
    email = payload.email.strip().lower()
    resolved_tenant_id = getattr(request, "resolved_tenant_id", None)
    user_hint = User.objects.filter(username=email, is_active=True).first()
    membership = membership_for_user(user_hint, resolved_tenant_id) if user_hint else None
    policy = policy_for(membership.institution.tenant_id) if membership else policy_for(None)
    recent_failures = LoginAttempt.objects.filter(
        identifier_hash=identifier_hash(email),
        succeeded=False,
        occurred_at__gte=timezone.now() - timedelta(minutes=policy.lockout_minutes),
    ).count()
    if recent_failures >= policy.failed_login_limit:
        raise HttpError(429, "Account temporarily locked after repeated attempts")
    user = authenticate(request, username=email, password=payload.password)
    if user is None or not user.is_active:
        LoginAttempt.objects.create(
            user=user_hint,
            identifier_hash=identifier_hash(email),
            ip_address=client_ip(request),
            device_hash=device_hash(payload.device_id),
            succeeded=False,
            risk_reasons=["invalid_credentials"],
        )
        record_auth_history(
            user=user_hint,
            membership=membership,
            event="login",
            outcome=AuthenticationHistory.Outcome.FAILED,
            request=request,
            hashed_device=device_hash(payload.device_id),
            reasons=["invalid_credentials"],
        )
        raise HttpError(401, "Invalid email or password")
    membership = membership_for_user(user, resolved_tenant_id)
    if not membership:
        message = "Your account does not belong to this university" if resolved_tenant_id else "No active ADMIEZO membership"
        raise HttpError(403, message)
    risk_score, reasons, _device, hashed_device = assess_login_risk(request, user, payload.device_id, resolved_tenant_id)
    methods = AuthenticationMethod.objects.filter(user=user, is_active=True)
    has_totp = methods.filter(kind=AuthenticationMethod.Kind.TOTP).exists()
    has_passkey = PasskeyCredential.objects.filter(user=user, revoked_at__isnull=True).exists()
    demo_password_only_login = settings.DEBUG and getattr(settings, "DEMO_PASSWORD_ONLY_LOGIN", False)
    requires_challenge = policy.require_mfa or (
        not demo_password_only_login and (has_totp or risk_score >= policy.alert_risk_threshold)
    )
    LoginAttempt.objects.create(
        user=user,
        identifier_hash=identifier_hash(email),
        ip_address=client_ip(request),
        device_hash=hashed_device,
        succeeded=True,
        challenged=requires_challenge,
        risk_score=risk_score,
        risk_reasons=reasons,
    )
    context = {
        "device_hash": hashed_device,
        "device_label": payload.device_label,
        "platform": payload.platform,
        "browser": payload.browser,
        "location": payload.location,
        "risk_score": risk_score,
        "risk_reasons": reasons,
        "tenant_id": str(resolved_tenant_id) if resolved_tenant_id else None,
    }
    if requires_challenge:
        if not has_totp and not policy.require_mfa and not has_passkey:
            raise HttpError(403, "Additional verification is required but no MFA method is enrolled")
        request.session["pending_auth"] = {
            "user_id": user.id,
            "expires_at": int((timezone.now() + timedelta(minutes=5)).timestamp()),
            "context": context,
            "challenge": "mfa_enrollment" if policy.require_mfa and not has_totp else "mfa",
        }
        if policy.require_mfa and not has_totp:
            with transaction.atomic():
                method, secret, uri = begin_totp_setup(user, "Primary authenticator")
                record_event(
                    tenant_id=membership.institution.tenant_id,
                    actor_id=user.id,
                    action="auth.totp.setup.started",
                    aggregate="AuthenticationMethod",
                    aggregate_id=method.id,
                    payload={"label": method.label, "source": "required_login_enrollment"},
                )
            pending = request.session["pending_auth"]
            pending["enrollment_method_id"] = str(method.id)
            request.session["pending_auth"] = pending
        record_auth_history(
            user=user,
            membership=membership,
            event="login",
            outcome=AuthenticationHistory.Outcome.CHALLENGED,
            request=request,
            hashed_device=hashed_device,
            risk_score=risk_score,
            reasons=reasons,
        )
        if policy.require_mfa and not has_totp:
            return 202, {
                "challenge": "mfa_enrollment",
                "enrollment": {
                    "method_id": str(method.id),
                    "secret": secret,
                    "provisioning_uri": uri,
                },
            }
        return 202, {"challenge": "mfa", "methods": {"totp": has_totp, "passkey": has_passkey}}
    membership, session = finalize_login(request, user, context)
    return _user_context(user, membership, session)


@router.post("/mfa/verify", auth=None)
def verify_mfa(request, payload: MfaVerifyIn):
    pending = request.session.get("pending_auth")
    if not pending or pending["expires_at"] < int(timezone.now().timestamp()):
        request.session.pop("pending_auth", None)
        raise HttpError(409, "Authentication challenge has expired")
    if pending.get("challenge") == "mfa_enrollment":
        raise HttpError(409, "Complete authenticator setup before signing in")
    user = User.objects.filter(id=pending["user_id"], is_active=True).first()
    if not user or not verify_totp_for_user(user, payload.code):
        raise HttpError(401, "The verification code is not valid")
    request.session.pop("pending_auth", None)
    membership, session = finalize_login(request, user, pending["context"], mfa_verified=True)
    return _user_context(user, membership, session)


@router.post("/mfa/enroll", auth=None)
def enroll_mfa(request, payload: MfaEnrollmentConfirmIn):
    pending = request.session.get("pending_auth")
    if not pending or pending["expires_at"] < int(timezone.now().timestamp()):
        request.session.pop("pending_auth", None)
        raise HttpError(409, "Authenticator setup has expired. Sign in again to restart it")
    if pending.get("challenge") != "mfa_enrollment" or pending.get("enrollment_method_id") != payload.method_id:
        raise HttpError(409, "Authenticator setup does not match this sign-in")
    user = User.objects.filter(id=pending["user_id"], is_active=True).first()
    if not user:
        request.session.pop("pending_auth", None)
        raise HttpError(409, "Authenticator setup is no longer available")
    membership = membership_for_user(user, pending["context"].get("tenant_id"))
    if not membership:
        request.session.pop("pending_auth", None)
        raise HttpError(403, "No active ADMIEZO membership")
    with transaction.atomic():
        method = confirm_totp_setup(user, payload.method_id, payload.code)
        record_event(
            tenant_id=membership.institution.tenant_id,
            actor_id=user.id,
            action="auth.totp.enabled",
            aggregate="AuthenticationMethod",
            aggregate_id=method.id,
            payload={"label": method.label, "source": "required_login_enrollment"},
        )
    request.session.pop("pending_auth", None)
    membership, session = finalize_login(request, user, pending["context"], mfa_verified=True)
    return _user_context(user, membership, session)


@router.post("/passkeys/login/options", auth=None)
def passkey_login_options(request, payload: PasskeyLoginBeginIn):
    user = User.objects.filter(username=payload.email.strip().lower(), is_active=True).first()
    if not user:
        raise HttpError(401, "Authentication failed")
    resolved_tenant_id = getattr(request, "resolved_tenant_id", None)
    if resolved_tenant_id and not membership_for_user(user, resolved_tenant_id):
        raise HttpError(403, "Your account does not belong to this university")
    membership = membership_for_user(user, resolved_tenant_id)
    if membership and policy_for(membership.institution.tenant_id).require_mfa:
        raise HttpError(403, "This university requires password and authenticator app sign-in")
    risk_score, reasons, _device, hashed_device = assess_login_risk(request, user, payload.device_id, resolved_tenant_id)
    context = {
        "device_hash": hashed_device,
        "device_label": payload.device_label,
        "platform": payload.platform,
        "browser": payload.browser,
        "risk_score": risk_score,
        "risk_reasons": reasons,
        "tenant_id": str(resolved_tenant_id) if resolved_tenant_id else None,
    }
    return begin_passkey_login(request, user, context)


@router.post("/passkeys/login/verify", auth=None)
def passkey_login_verify(request, payload: PasskeyLoginCompleteIn):
    user, context = complete_passkey_login(request, payload.credential)
    membership = membership_for_user(user, context.get("tenant_id"))
    if membership and policy_for(membership.institution.tenant_id).require_mfa:
        raise HttpError(403, "This university requires password and authenticator app sign-in")
    membership, session = finalize_login(request, user, context, mfa_verified=True)
    return _user_context(user, membership, session)


@router.get("/me")
def me(request):
    membership = membership_for(request)
    session = active_session_for_request(request)
    if not session:
        raise HttpError(401, "Session is unavailable")
    return _user_context(request.auth, membership, session)


@router.post("/password/complete-setup")
def complete_password_setup(request, payload: PasswordChangeIn):
    membership = membership_for(request)
    if not membership.must_change_password:
        raise HttpError(409, "Password setup is not required")
    try:
        validate_password(payload.new_password, request.auth)
    except ValidationError as exc:
        raise HttpError(422, " ".join(exc.messages)) from exc
    with transaction.atomic():
        request.auth.set_password(payload.new_password)
        request.auth.save(update_fields=["password"])
        membership.must_change_password = False
        membership.save(update_fields=["must_change_password", "updated_at"])
        current = active_session_for_request(request)
        other_sessions = AccessSession.objects.filter(user=request.auth, revoked_at__isnull=True)
        if current:
            other_sessions = other_sessions.exclude(id=current.id)
        other_sessions.update(revoked_at=timezone.now(), revoked_reason="password_changed")
        record_event(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, action="auth.password.setup.completed", aggregate="Membership", aggregate_id=membership.id)
    update_session_auth_hash(request, request.auth)
    if current:
        current.session_key_hash = hashlib.sha256(request.session.session_key.encode()).hexdigest()
        current.save(update_fields=["session_key_hash", "updated_at"])
    return {"ok": True}


@router.post("/password/change")
def change_password(request, payload: PasswordUpdateIn):
    membership = membership_for(request)
    if not request.auth.check_password(payload.current_password):
        raise HttpError(401, "Current password is incorrect")
    if payload.current_password == payload.new_password:
        raise HttpError(422, "Choose a different password")
    try:
        validate_password(payload.new_password, request.auth)
    except ValidationError as exc:
        raise HttpError(422, " ".join(exc.messages)) from exc
    current = active_session_for_request(request)
    if not current:
        raise HttpError(401, "Session is unavailable")
    with transaction.atomic():
        request.auth.set_password(payload.new_password)
        request.auth.save(update_fields=["password"])
        AccessSession.objects.filter(user=request.auth, revoked_at__isnull=True).exclude(id=current.id).update(
            revoked_at=timezone.now(), revoked_reason="password_changed"
        )
        record_event(
            tenant_id=membership.institution.tenant_id,
            actor_id=request.auth.id,
            action="auth.password.changed",
            aggregate="AccessSession",
            aggregate_id=current.id,
        )
    update_session_auth_hash(request, request.auth)
    current.session_key_hash = hashlib.sha256(request.session.session_key.encode()).hexdigest()
    current.save(update_fields=["session_key_hash", "updated_at"])
    return {"ok": True}


@router.post("/logout")
def sign_out(request):
    membership = membership_for(request)
    session = active_session_for_request(request)
    if session:
        with transaction.atomic():
            session.revoked_at = timezone.now()
            session.revoked_reason = "user_logout"
            session.save(update_fields=["revoked_at", "revoked_reason", "updated_at"])
            record_event(
                tenant_id=membership.institution.tenant_id,
                actor_id=request.auth.id,
                action="auth.session.revoked",
                aggregate="AccessSession",
                aggregate_id=session.id,
                payload={"reason": "user_logout"},
            )
    logout(request)
    return {"ok": True}


@router.get("/security-context")
def security_context(request):
    membership = membership_for(request)
    tenant_id = membership.institution.tenant_id
    policy = SecurityPolicy.objects.filter(tenant_id=tenant_id).first()
    session_records = AccessSession.objects.filter(user=request.auth, tenant_id=tenant_id)
    device_records = TrustedDevice.objects.filter(user=request.auth)
    history_records = AuthenticationHistory.objects.filter(user=request.auth, tenant_id=tenant_id)
    sessions = session_records.select_related("device").order_by("-last_seen_at")[:20]
    devices = device_records.order_by("-last_seen_at")[:50]
    methods = AuthenticationMethod.objects.filter(user=request.auth, is_active=True).order_by("kind", "label")
    passkeys = PasskeyCredential.objects.filter(user=request.auth).order_by("-created_at")
    history = history_records.order_by("-created_at")[:50]
    current_session = active_session_for_request(request)
    return {
        "current_session_id": str(current_session.id) if current_session else None,
        "server_time": timezone.now().isoformat(),
        "totals": {
            "sessions": session_records.count(),
            "devices": device_records.count(),
            "history": history_records.count(),
        },
        "policy": {
            "session_timeout_minutes": policy.session_timeout_minutes if policy else 30,
            "maximum_concurrent_sessions": policy.maximum_concurrent_sessions if policy else 3,
            "step_up_minutes": policy.step_up_minutes if policy else 10,
            "require_mfa": policy.require_mfa if policy else False,
            "require_trusted_device": policy.require_trusted_device if policy else False,
        },
        "sessions": [
            {
                "id": str(item.id),
                "device": item.device.label if item.device else "Unidentified device",
                "ip_address": item.ip_address,
                "risk_score": item.risk_score,
                "last_seen_at": item.last_seen_at.isoformat(),
                "expires_at": item.expires_at.isoformat(),
                "revoked_at": item.revoked_at.isoformat() if item.revoked_at else None,
                "status": "revoked" if item.revoked_at else "expired" if item.expires_at <= timezone.now() else "active",
                "step_up_valid": item.is_step_up_valid,
            }
            for item in sessions
        ],
        "devices": [
            {
                "id": str(item.id),
                "label": item.label,
                "platform": item.platform,
                "browser": item.browser,
                "trusted_until": item.trusted_until.isoformat() if item.trusted_until else None,
                "last_seen_at": item.last_seen_at.isoformat() if item.last_seen_at else None,
                "revoked_at": item.revoked_at.isoformat() if item.revoked_at else None,
            }
            for item in devices
        ],
        "methods": [
            {
                "id": str(item.id),
                "kind": item.kind,
                "label": item.label,
                "active": item.is_active,
                "verified_at": item.verified_at.isoformat() if item.verified_at else None,
            }
            for item in methods
        ]
        + [
            {
                "id": str(item.id),
                "kind": "passkey",
                "label": item.label,
                "active": item.revoked_at is None,
                "verified_at": item.created_at.isoformat(),
            }
            for item in passkeys
        ],
        "history": [
            {
                "id": str(item.id),
                "event": item.event,
                "outcome": item.outcome,
                "ip_address": item.ip_address,
                "risk_score": item.risk_score,
                "risk_reasons": item.risk_reasons,
                "created_at": item.created_at.isoformat(),
            }
            for item in history
        ],
    }


@router.post("/totp/setup")
def setup_totp(request, payload: TotpSetupIn):
    membership = membership_for(request)
    replacing = AuthenticationMethod.objects.filter(
        user=request.auth,
        kind=AuthenticationMethod.Kind.TOTP,
        is_active=True,
    ).exists()
    session = active_session_for_request(request)
    if replacing and (not session or not session.is_step_up_valid):
        raise HttpError(403, "Re-authenticate before replacing your active authenticator")
    with transaction.atomic():
        method, secret, uri = begin_totp_setup(request.auth, payload.label)
        record_event(
            tenant_id=membership.institution.tenant_id,
            actor_id=request.auth.id,
            action="auth.totp.setup.started",
            aggregate="AuthenticationMethod",
            aggregate_id=method.id,
            payload={"label": method.label},
        )
    return {"method_id": str(method.id), "secret": secret, "provisioning_uri": uri}


@router.post("/totp/confirm")
def confirm_totp(request, payload: TotpConfirmIn):
    membership = membership_for(request)
    with transaction.atomic():
        method = confirm_totp_setup(request.auth, payload.method_id, payload.code)
        record_event(
            tenant_id=membership.institution.tenant_id,
            actor_id=request.auth.id,
            action="auth.totp.enabled",
            aggregate="AuthenticationMethod",
            aggregate_id=method.id,
            payload={"label": method.label},
        )
    return {"id": str(method.id), "active": True}


@router.post("/passkeys/registration/options")
def passkey_registration_options(request):
    return begin_passkey_registration(request, request.auth)


@router.post("/passkeys/registration/verify")
def passkey_registration_verify(request, payload: PasskeyCompleteIn):
    membership = membership_for(request)
    with transaction.atomic():
        credential = complete_passkey_registration(request, request.auth, payload.credential, payload.label)
        record_event(
            tenant_id=membership.institution.tenant_id,
            actor_id=request.auth.id,
            action="auth.passkey.registered",
            aggregate="PasskeyCredential",
            aggregate_id=credential.id,
            payload={"label": credential.label},
        )
    return {"id": str(credential.id), "label": credential.label}


@router.post("/step-up")
def step_up(request, payload: StepUpIn):
    membership = membership_for(request)
    valid = bool(payload.password and request.auth.check_password(payload.password))
    valid = valid or bool(payload.code and verify_totp_for_user(request.auth, payload.code))
    if not valid:
        raise HttpError(401, "Re-authentication failed")
    session = active_session_for_request(request)
    if not session:
        raise HttpError(401, "Session is unavailable")
    policy = policy_for(membership.institution.tenant_id)
    with transaction.atomic():
        session.step_up_expires_at = timezone.now() + timedelta(minutes=policy.step_up_minutes)
        session.save(update_fields=["step_up_expires_at", "updated_at"])
        record_event(
            tenant_id=membership.institution.tenant_id,
            actor_id=request.auth.id,
            action="auth.step_up.succeeded",
            aggregate="AccessSession",
            aggregate_id=session.id,
            payload={"expires_at": session.step_up_expires_at.isoformat()},
        )
    return {"step_up_expires_at": session.step_up_expires_at.isoformat()}


@router.post("/devices/{device_id}/trust")
def trust_device(request, device_id: str, payload: DeviceTrustIn):
    membership = membership_for(request)
    session = active_session_for_request(request)
    if not session or not session.is_step_up_valid:
        raise HttpError(428, "Step-up authentication is required")
    if payload.trusted_days < 1 or payload.trusted_days > 90:
        raise HttpError(422, "Trusted device duration must be between 1 and 90 days")
    with transaction.atomic():
        device = TrustedDevice.objects.select_for_update().filter(id=device_id, user=request.auth, revoked_at__isnull=True).first()
        if not device:
            raise HttpError(404, "Device not found")
        device.trusted_until = timezone.now() + timedelta(days=payload.trusted_days)
        device.verified_at = timezone.now()
        device.save(update_fields=["trusted_until", "verified_at", "updated_at"])
        AccessSession.objects.filter(device=device, tenant_id=membership.institution.tenant_id, revoked_at__isnull=True).update(trusted_device=True)
        record_event(
            tenant_id=membership.institution.tenant_id,
            actor_id=request.auth.id,
            action="auth.device.trusted",
            aggregate="TrustedDevice",
            aggregate_id=device.id,
            payload={"trusted_until": device.trusted_until.isoformat()},
        )
    return {"id": str(device.id), "trusted_until": device.trusted_until.isoformat()}


@router.post("/devices/{device_id}/revoke")
def revoke_device(request, device_id: str):
    membership = membership_for(request)
    session = active_session_for_request(request)
    if not session or not session.is_step_up_valid:
        raise HttpError(428, "Step-up authentication is required")
    with transaction.atomic():
        device = TrustedDevice.objects.select_for_update().filter(id=device_id, user=request.auth, revoked_at__isnull=True).first()
        if not device:
            raise HttpError(404, "Device not found")
        device.revoked_at = timezone.now()
        device.save(update_fields=["revoked_at", "updated_at"])
        AccessSession.objects.filter(device=device, tenant_id=membership.institution.tenant_id, revoked_at__isnull=True).update(
            revoked_at=timezone.now(), revoked_reason="device_revoked"
        )
        record_event(
            tenant_id=membership.institution.tenant_id,
            actor_id=request.auth.id,
            action="auth.device.revoked",
            aggregate="TrustedDevice",
            aggregate_id=device.id,
        )
    return {"id": str(device.id), "revoked": True}


@router.post("/sessions/{session_id}/revoke")
def revoke_session(request, session_id: str):
    membership = membership_for(request)
    current = active_session_for_request(request)
    if not current or not current.is_step_up_valid:
        raise HttpError(428, "Step-up authentication is required")
    with transaction.atomic():
        target = AccessSession.objects.select_for_update().filter(id=session_id, user=request.auth, tenant_id=membership.institution.tenant_id, revoked_at__isnull=True).first()
        if not target:
            raise HttpError(404, "Session not found")
        target.revoked_at = timezone.now()
        target.revoked_reason = "user_revoked"
        target.save(update_fields=["revoked_at", "revoked_reason", "updated_at"])
        record_event(
            tenant_id=membership.institution.tenant_id,
            actor_id=request.auth.id,
            action="auth.session.revoked",
            aggregate="AccessSession",
            aggregate_id=target.id,
            payload={"reason": "user_revoked"},
        )
    return {"id": str(target.id), "revoked": True}


@router.post("/users/{user_id}/emergency-revoke")
def emergency_revoke(request, user_id: int):
    membership = require_roles(request, Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN)
    current = active_session_for_request(request)
    if not current or not current.is_step_up_valid:
        raise HttpError(428, "Step-up authentication is required")
    target_membership = Membership.objects.filter(
        user_id=user_id, institution__tenant_id=membership.institution.tenant_id, is_active=True
    ).first()
    if not target_membership:
        raise HttpError(404, "User not found in this tenant")
    with transaction.atomic():
        count = AccessSession.objects.filter(user_id=user_id, tenant_id=membership.institution.tenant_id, revoked_at__isnull=True).update(
            revoked_at=timezone.now(), revoked_reason="emergency_revocation"
        )
        record_event(
            tenant_id=membership.institution.tenant_id,
            actor_id=request.auth.id,
            action="auth.emergency_revocation",
            aggregate="User",
            aggregate_id=user_id,
            payload={"sessions_revoked": count},
        )
    return {"user_id": user_id, "sessions_revoked": count}


@router.get("/notifications")
def notification_inbox(request, limit: int = 30):
    from apps.phase4.models import NotificationDelivery

    membership = membership_for(request)
    rows = NotificationDelivery.objects.filter(
        tenant_id=membership.institution.tenant_id,
        user_id=request.auth.id,
    ).order_by("-created_at")[: max(1, min(limit, 100))]
    items = [
        {
            "id": str(item.id),
            "category": item.category,
            "title": item.title,
            "body": item.body,
            "severity": item.severity,
            "status": item.status,
            "created_at": item.created_at.isoformat(),
            "acknowledged_at": item.acknowledged_at.isoformat() if item.acknowledged_at else None,
            "version": item.version,
        }
        for item in rows
    ]
    return {"unread_count": sum(item["status"] != NotificationDelivery.Status.ACKNOWLEDGED for item in items), "items": items}


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(request, notification_id: str):
    from apps.phase4.models import NotificationDelivery
    from apps.phase4.services import notification_action

    membership = membership_for(request)
    item = NotificationDelivery.objects.filter(
        id=notification_id,
        tenant_id=membership.institution.tenant_id,
        user_id=request.auth.id,
    ).first()
    if not item:
        raise HttpError(404, "Notification not found")
    if item.status == NotificationDelivery.Status.ACKNOWLEDGED:
        return {"id": str(item.id), "status": item.status, "version": item.version}
    item = notification_action(
        tenant_id=membership.institution.tenant_id,
        actor_id=request.auth.id,
        notification_id=item.id,
        expected_version=item.version,
        action="acknowledge",
    )
    return {"id": str(item.id), "status": item.status, "version": item.version}
