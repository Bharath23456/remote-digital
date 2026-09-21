import base64
import hashlib
import ipaddress
import json
import secrets
import socket
from datetime import timedelta
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

import jwt
from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from ninja.errors import HttpError
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import PublicKeyCredentialDescriptor, UserVerificationRequirement

from apps.core.services import record_event
from apps.security.crypto import decrypt_secret, encrypt_secret
from apps.security.models import SecurityAlert, SecurityPolicy
from apps.tenancy.models import Membership

from .models import (
    AccessSession,
    AuthenticationHistory,
    AuthenticationMethod,
    DeviceAuthorization,
    OidcProvider,
    PasskeyCredential,
    TrustedDevice,
)
from .totp import generate_secret, provisioning_uri, verify_code


def client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")) or None


def device_hash(device_id: str) -> str:
    if not device_id:
        return ""
    return hashlib.sha256(f"admiezo-device:{device_id}".encode()).hexdigest()


def identifier_hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


class OidcError(ValueError):
    pass


def _safe_oidc_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise OidcError("OIDC endpoints must use an HTTPS URL without embedded credentials")
    allowed_private = set(getattr(settings, "OIDC_ALLOWED_PRIVATE_HOSTS", []))
    if parsed.hostname.lower() not in allowed_private:
        try:
            addresses = {entry[4][0] for entry in socket.getaddrinfo(parsed.hostname, parsed.port or 443)}
        except socket.gaierror as exc:
            raise OidcError("OIDC provider hostname could not be resolved") from exc
        for value in addresses:
            address = ipaddress.ip_address(value)
            if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
                raise OidcError("OIDC provider resolves to a restricted network address")
    return value


def _request_json(url: str, *, form: dict | None = None) -> dict:
    _safe_oidc_url(url)
    body = urlencode(form).encode() if form is not None else None
    headers = {"Accept": "application/json", "User-Agent": "ADMIEZO/1.0"}
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    try:
        with urlopen(Request(url, data=body, headers=headers), timeout=8) as response:  # noqa: S310
            if response.status < 200 or response.status >= 300:
                raise OidcError("OIDC provider returned an unsuccessful response")
            data = json.loads(response.read(1_000_001))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise OidcError("OIDC provider could not be reached or returned invalid data") from exc
    if not isinstance(data, dict):
        raise OidcError("OIDC provider returned invalid data")
    return data


def oidc_discovery(provider: OidcProvider) -> dict:
    discovery = _request_json(f"{provider.issuer.rstrip('/')}/.well-known/openid-configuration")
    if discovery.get("issuer", "").rstrip("/") != provider.issuer.rstrip("/"):
        raise OidcError("OIDC discovery issuer does not match the configured issuer")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not discovery.get(key):
            raise OidcError(f"OIDC discovery is missing {key}")
        _safe_oidc_url(discovery[key])
    return discovery


def begin_oidc_login(request, provider: OidcProvider, context: dict) -> str:
    discovery = oidc_discovery(provider)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    base_url = settings.APP_BASE_URL or request.build_absolute_uri("/").rstrip("/")
    redirect_uri = f"{base_url}/api/v1/auth/sso/callback"
    request.session["oidc_flow"] = {
        "provider_id": str(provider.id),
        "state": state,
        "nonce": nonce,
        "verifier": verifier,
        "redirect_uri": redirect_uri,
        "expires_at": int((timezone.now() + timedelta(minutes=5)).timestamp()),
        "context": context,
    }
    params = {
        "client_id": provider.client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": provider.scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if provider.domain_hint:
        params["login_hint"] = f"user@{provider.domain_hint}"
    return f"{discovery['authorization_endpoint']}?{urlencode(params)}"


def complete_oidc_login(request, *, state: str, code: str):
    flow = request.session.pop("oidc_flow", None)
    if not flow or flow.get("expires_at", 0) < int(timezone.now().timestamp()):
        raise OidcError("The institutional sign-in request expired")
    if not secrets.compare_digest(str(flow.get("state", "")), state):
        raise OidcError("The institutional sign-in state is invalid")
    provider = OidcProvider.objects.filter(id=flow["provider_id"], is_active=True).first()
    if not provider:
        raise OidcError("The institutional sign-in provider is unavailable")
    discovery = oidc_discovery(provider)
    token_data = _request_json(
        discovery["token_endpoint"],
        form={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": flow["redirect_uri"],
            "client_id": provider.client_id,
            "client_secret": decrypt_secret(provider.client_secret_ciphertext),
            "code_verifier": flow["verifier"],
        },
    )
    id_token = token_data.get("id_token")
    if not isinstance(id_token, str):
        raise OidcError("OIDC provider did not return an ID token")
    try:
        unverified_header = jwt.get_unverified_header(id_token)
        algorithm = unverified_header.get("alg")
        supported = set(discovery.get("id_token_signing_alg_values_supported", ["RS256"]))
        allowed = [item for item in ("RS256", "PS256", "ES256") if item in supported]
        if algorithm not in allowed:
            raise OidcError("OIDC token uses an unsupported signing algorithm")
        signing_key = jwt.PyJWKClient(discovery["jwks_uri"], timeout=8).get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=allowed,
            audience=provider.client_id,
            issuer=provider.issuer,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]},
        )
    except OidcError:
        raise
    except jwt.PyJWTError as exc:
        raise OidcError("OIDC token signature or claims are invalid") from exc
    if not secrets.compare_digest(str(claims.get("nonce", "")), str(flow["nonce"])):
        raise OidcError("OIDC token nonce is invalid")
    email = str(claims.get("email", "")).strip().lower()
    if not email or claims.get("email_verified") is False:
        raise OidcError("OIDC provider did not confirm a verified email address")
    if provider.domain_hint and not email.endswith(f"@{provider.domain_hint.lower()}"):
        raise OidcError("The account is outside the configured institutional domain")
    user = User.objects.filter(username__iexact=email, is_active=True).first()
    membership = membership_for_user(user, provider.tenant_id) if user else None
    if not membership:
        raise OidcError("This institutional account has no active ADMIEZO membership")
    policy = policy_for(provider.tenant_id)
    amr = {str(item).lower() for item in claims.get("amr", [])}
    mfa_verified = bool(amr.intersection({"mfa", "otp", "hwk", "fido", "webauthn"}))
    if policy.require_mfa and not mfa_verified:
        raise OidcError("The institution requires MFA but the identity provider did not assert it")
    flow["context"]["tenant_id"] = str(provider.tenant_id)
    return user, membership, flow["context"], mfa_verified, claims


def membership_for_user(user, tenant_id=None):
    memberships = Membership.objects.select_related("institution").filter(user=user, is_active=True, institution__is_active=True)
    if tenant_id:
        memberships = memberships.filter(institution__tenant_id=tenant_id)
    return memberships.order_by("created_at").first()


def policy_for(tenant_id):
    policy = SecurityPolicy.objects.filter(tenant_id=tenant_id).first()
    if policy:
        return policy
    return SimpleNamespace(
        session_timeout_minutes=30,
        maximum_concurrent_sessions=3,
        step_up_minutes=10,
        failed_login_limit=5,
        lockout_minutes=15,
        require_mfa=False,
        require_trusted_device=False,
        approved_networks=[],
        allowed_countries=[],
        vpn_risk_threshold=70,
        alert_risk_threshold=50,
        ai_evaluation_mode=SecurityPolicy.AIEvaluationMode.DISABLED,
        ai_confidence_threshold=85,
        ai_model_name="admiezo-ai-v1",
    )


def _ip_in_approved_network(ip_address: str | None, networks: list[str]) -> bool:
    if not networks:
        return True
    if not ip_address:
        return False
    try:
        address = ipaddress.ip_address(ip_address)
        return any(address in ipaddress.ip_network(network, strict=False) for network in networks)
    except ValueError:
        return False


def assess_login_risk(request, user, raw_device_id: str, tenant_id=None):
    membership = membership_for_user(user, tenant_id)
    policy = policy_for(membership.institution.tenant_id) if membership else policy_for(None)
    hashed_device = device_hash(raw_device_id)
    ip_address = client_ip(request)
    score = 0
    reasons = []
    known_device = None
    if hashed_device:
        known_device = TrustedDevice.objects.filter(user=user, device_hash=hashed_device, revoked_at__isnull=True).first()
        if not known_device:
            score += 25
            reasons.append("new_device")
    else:
        score += 20
        reasons.append("device_identifier_missing")
    if not AccessSession.objects.filter(user=user, ip_address=ip_address, revoked_at__isnull=True).exists():
        score += 15
        reasons.append("new_ip_address")
    if not _ip_in_approved_network(ip_address, policy.approved_networks):
        score += 50
        reasons.append("outside_approved_network")
    if getattr(settings, "TRUST_PROXY_RISK_HEADERS", False) and request.headers.get("x-vpn-risk") == "high":
        score += 70
        reasons.append("vpn_or_proxy_risk")
    country = request.headers.get("x-country-code", "").upper()
    if policy.allowed_countries and country and country not in policy.allowed_countries:
        score += 50
        reasons.append("unexpected_country")
    return min(score, 100), reasons, known_device, hashed_device


def record_auth_history(*, user, membership, event, outcome, request, hashed_device="", risk_score=0, reasons=None, metadata=None):
    AuthenticationHistory.objects.create(
        user=user,
        tenant_id=membership.institution.tenant_id if membership else None,
        event=event,
        outcome=outcome,
        ip_address=client_ip(request),
        device_hash=hashed_device,
        risk_score=risk_score,
        risk_reasons=reasons or [],
        metadata=metadata or {},
    )


def active_session_for_request(request):
    if not request.session.session_key:
        return None
    hashed_key = hashlib.sha256(request.session.session_key.encode()).hexdigest()
    return AccessSession.objects.filter(session_key_hash=hashed_key, revoked_at__isnull=True).first()


def finalize_login(request, user, context: dict, *, mfa_verified=False):
    membership = membership_for_user(user, context.get("tenant_id"))
    if not membership:
        raise HttpError(403, "No active ADMIEZO membership")
    policy = policy_for(membership.institution.tenant_id)
    risk_score = int(context.get("risk_score", 0))
    risk_reasons = list(context.get("risk_reasons", []))
    hashed_device = context.get("device_hash", "")
    device = None
    if policy.require_trusted_device and not hashed_device:
        raise HttpError(403, "This university requires an approved device")
    if hashed_device:
        device, _ = TrustedDevice.objects.get_or_create(
            user=user,
            device_hash=hashed_device,
            defaults={
                "label": context.get("device_label", "Current device")[:100],
                "platform": context.get("platform", "")[:80],
                "browser": context.get("browser", "")[:80],
            },
        )
        DeviceAuthorization.objects.get_or_create(tenant_id=membership.institution.tenant_id, device=device)
    with transaction.atomic():
        if device:
            device = TrustedDevice.objects.select_for_update().get(pk=device.pk)
            authorization = DeviceAuthorization.objects.select_for_update().get(tenant_id=membership.institution.tenant_id, device=device)
            if device.revoked_at or authorization.revoked_at:
                raise HttpError(403, "This device has been revoked. Contact your university administrator")
            if policy.require_trusted_device and not authorization.approved_at:
                raise HttpError(403, "Device approval is pending. Ask your university administrator to approve this device")
            device.last_ip = client_ip(request)
            device.last_seen_at = timezone.now()
            device.save(update_fields=["last_ip", "last_seen_at", "updated_at"])
        active = list(
            AccessSession.objects.select_for_update()
            .filter(user=user, tenant_id=membership.institution.tenant_id, revoked_at__isnull=True, expires_at__gt=timezone.now())
            .order_by("created_at")
        )
        overflow = max(0, len(active) - policy.maximum_concurrent_sessions + 1)
        for old_session in active[:overflow]:
            old_session.revoked_at = timezone.now()
            old_session.revoked_reason = "concurrent_session_limit"
            old_session.save(update_fields=["revoked_at", "revoked_reason", "updated_at"])
        login(request, user)
        request.session.cycle_key()
        request.session["active_tenant_id"] = str(membership.institution.tenant_id)
        request.session.set_expiry(policy.session_timeout_minutes * 60)
        session_hash = hashlib.sha256(request.session.session_key.encode()).hexdigest()
        access_session = AccessSession.objects.create(
            user=user,
            tenant_id=membership.institution.tenant_id,
            session_key_hash=session_hash,
            device=device,
            ip_address=client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:255],
            location=context.get("location", "")[:120],
            trusted_device=bool(device and authorization.approved_at and not authorization.revoked_at),
            risk_score=risk_score,
            risk_reasons=risk_reasons,
            mfa_verified_at=timezone.now() if mfa_verified else None,
            expires_at=timezone.now() + timedelta(minutes=policy.session_timeout_minutes),
        )
        record_auth_history(
            user=user,
            membership=membership,
            event="login",
            outcome=AuthenticationHistory.Outcome.SUCCESS,
            request=request,
            hashed_device=hashed_device,
            risk_score=risk_score,
            reasons=risk_reasons,
            metadata={"mfa": mfa_verified},
        )
        record_event(
            tenant_id=membership.institution.tenant_id,
            actor_id=user.id,
            action="auth.login.succeeded",
            aggregate="AccessSession",
            aggregate_id=access_session.id,
            payload={"risk_score": risk_score, "mfa": mfa_verified},
        )
        if risk_score >= policy.alert_risk_threshold:
            alert = SecurityAlert.objects.create(
                tenant_id=membership.institution.tenant_id,
                category="suspicious_login",
                severity=SecurityAlert.Severity.HIGH if risk_score >= 70 else SecurityAlert.Severity.MEDIUM,
                title="Risky authentication accepted after verification",
                details={"risk_score": risk_score, "reasons": risk_reasons, "user_id": user.id},
            )
            record_event(
                tenant_id=membership.institution.tenant_id,
                actor_id=user.id,
                action="security.alert.created",
                aggregate="SecurityAlert",
                aggregate_id=alert.id,
                payload={"category": alert.category, "severity": alert.severity},
            )
    return membership, access_session


@transaction.atomic
def begin_totp_setup(user, label: str):
    secret = generate_secret()
    requested_label = (label or "Authenticator").strip()[:80]
    AuthenticationMethod.objects.filter(user=user, kind=AuthenticationMethod.Kind.TOTP, is_active=False).delete()
    label_in_use = AuthenticationMethod.objects.filter(
        user=user,
        kind=AuthenticationMethod.Kind.TOTP,
        label=requested_label,
        is_active=True,
    ).exists()
    pending_label = f"{requested_label[:68]} replacement" if label_in_use else requested_label
    method = AuthenticationMethod.objects.create(
        user=user,
        kind=AuthenticationMethod.Kind.TOTP,
        label=pending_label,
        secret_ciphertext=encrypt_secret(secret),
    )
    return method, secret, provisioning_uri(secret, user.email or user.username)


def confirm_totp_setup(user, method_id, code: str):
    with transaction.atomic():
        method = (
            AuthenticationMethod.objects.select_for_update()
            .filter(id=method_id, user=user, kind=AuthenticationMethod.Kind.TOTP, is_active=False)
            .first()
        )
        if not method or not verify_code(decrypt_secret(method.secret_ciphertext), code):
            raise HttpError(422, "The authenticator code is not valid")
        replacement_suffix = " replacement"
        if method.label.endswith(replacement_suffix):
            primary_label = method.label[: -len(replacement_suffix)]
            conflicts = AuthenticationMethod.objects.select_for_update().filter(
                user=user,
                kind=AuthenticationMethod.Kind.TOTP,
                label=primary_label,
            ).exclude(id=method.id)
            for conflict in conflicts:
                conflict.label = f"Retired authenticator {str(conflict.id)[:8]}"
                conflict.is_primary = False
                conflict.is_active = False
                conflict.save(update_fields=["label", "is_primary", "is_active", "updated_at"])
            method.label = primary_label
        AuthenticationMethod.objects.filter(user=user, kind=AuthenticationMethod.Kind.TOTP).exclude(id=method.id).update(is_primary=False, is_active=False)
        method.is_active = True
        method.is_primary = True
        method.verified_at = timezone.now()
        method.save(update_fields=["label", "is_active", "is_primary", "verified_at", "updated_at"])
    return method


def verify_totp_for_user(user, code: str):
    methods = AuthenticationMethod.objects.filter(user=user, kind=AuthenticationMethod.Kind.TOTP, is_active=True)
    for method in methods:
        if verify_code(decrypt_secret(method.secret_ciphertext), code):
            method.last_used_at = timezone.now()
            method.save(update_fields=["last_used_at", "updated_at"])
            return True
    return False


def webauthn_context(request):
    rp_id = request.get_host().split(":")[0]
    origin = request.headers.get("origin") or f"{'https' if request.is_secure() else 'http'}://{request.get_host()}"
    return rp_id, origin


def begin_passkey_registration(request, user):
    rp_id, origin = webauthn_context(request)
    excluded = [
        PublicKeyCredentialDescriptor(id=bytes(item.credential_id))
        for item in PasskeyCredential.objects.filter(user=user, revoked_at__isnull=True)
    ]
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name="ADMIEZO",
        user_id=str(user.id).encode(),
        user_name=user.email or user.username,
        user_display_name=user.get_full_name() or user.username,
        exclude_credentials=excluded,
    )
    request.session["passkey_registration"] = {
        "challenge": base64.urlsafe_b64encode(options.challenge).decode(),
        "rp_id": rp_id,
        "origin": origin,
    }
    return json.loads(options_to_json(options))


def complete_passkey_registration(request, user, credential: dict, label: str):
    pending = request.session.pop("passkey_registration", None)
    if not pending:
        raise HttpError(409, "Passkey registration has expired")
    try:
        verification = verify_registration_response(
            credential=credential,
            expected_challenge=base64.urlsafe_b64decode(pending["challenge"]),
            expected_rp_id=pending["rp_id"],
            expected_origin=pending["origin"],
            require_user_verification=True,
        )
    except Exception as exc:
        raise HttpError(422, "Passkey registration could not be verified") from exc
    return PasskeyCredential.objects.create(
        user=user,
        credential_id=verification.credential_id,
        public_key=verification.credential_public_key,
        sign_count=verification.sign_count,
        device_type=verification.credential_device_type.value,
        backed_up=verification.credential_backed_up,
        label=label[:100],
        transports=credential.get("response", {}).get("transports", []),
    )


def begin_passkey_login(request, user, context: dict):
    rp_id, origin = webauthn_context(request)
    credentials = list(PasskeyCredential.objects.filter(user=user, revoked_at__isnull=True))
    if not credentials:
        raise HttpError(409, "No active passkey is registered for this account")
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[PublicKeyCredentialDescriptor(id=bytes(item.credential_id), transports=item.transports) for item in credentials],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    request.session["passkey_login"] = {
        "challenge": base64.urlsafe_b64encode(options.challenge).decode(),
        "rp_id": rp_id,
        "origin": origin,
        "user_id": user.id,
        "context": context,
    }
    return json.loads(options_to_json(options))


def _decode_base64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * ((4 - len(value) % 4) % 4))


def complete_passkey_login(request, credential: dict):
    pending = request.session.pop("passkey_login", None)
    if not pending:
        raise HttpError(409, "Passkey authentication has expired")
    from django.contrib.auth.models import User

    user = User.objects.filter(id=pending["user_id"], is_active=True).first()
    if not user:
        raise HttpError(401, "Authentication failed")
    credential_id = _decode_base64url(credential.get("rawId") or credential.get("id", ""))
    passkey = PasskeyCredential.objects.filter(user=user, credential_id=credential_id, revoked_at__isnull=True).first()
    if not passkey:
        raise HttpError(401, "Authentication failed")
    try:
        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=base64.urlsafe_b64decode(pending["challenge"]),
            expected_rp_id=pending["rp_id"],
            expected_origin=pending["origin"],
            credential_public_key=bytes(passkey.public_key),
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except Exception as exc:
        raise HttpError(401, "Authentication failed") from exc
    passkey.sign_count = verification.new_sign_count
    passkey.last_used_at = timezone.now()
    passkey.backed_up = verification.credential_backed_up
    passkey.save(update_fields=["sign_count", "last_used_at", "backed_up", "updated_at"])
    return user, pending["context"]
