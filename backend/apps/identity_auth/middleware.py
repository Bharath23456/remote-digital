import hashlib
from datetime import timedelta

from django.contrib.auth import logout
from django.http import JsonResponse
from django.utils import timezone

from .models import AccessSession, DeviceAuthorization
from .services import client_ip, policy_for


class IdentitySessionMiddleware:
    exempt_paths = {
        "/api/v1/auth/login",
        "/api/v1/auth/mfa/enroll",
        "/api/v1/auth/mfa/verify",
        "/api/v1/auth/passkeys/login/options",
        "/api/v1/auth/passkeys/login/verify",
        "/api/v1/auth/sso/providers",
        "/api/v1/auth/sso/start",
        "/api/v1/auth/sso/callback",
        "/api/health",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/api/") and request.user.is_authenticated and request.path not in self.exempt_paths:
            session_key = request.session.session_key
            session = None
            if session_key:
                session = AccessSession.objects.select_related("device").filter(
                    session_key_hash=hashlib.sha256(session_key.encode()).hexdigest()
                ).first()
            now = timezone.now()
            if not session or session.revoked_at or session.expires_at <= now:
                logout(request)
                return JsonResponse({"detail": "Session expired or revoked"}, status=401)
            if session.device and session.device.revoked_at:
                logout(request)
                return JsonResponse({"detail": "This device has been revoked"}, status=401)
            authorization = DeviceAuthorization.objects.filter(tenant_id=session.tenant_id, device=session.device).first() if session.device else None
            if authorization and authorization.revoked_at:
                logout(request)
                return JsonResponse({"detail": "This device has been revoked"}, status=401)
            memberships = request.user.admiezo_memberships.filter(is_active=True, institution__is_active=True).select_related("institution")
            active_tenant_id = request.session.get("active_tenant_id")
            membership = memberships.filter(institution__tenant_id=active_tenant_id).first() if active_tenant_id else None
            membership = membership or memberships.first()
            if membership and session.tenant_id != membership.institution.tenant_id:
                logout(request)
                return JsonResponse({"detail": "Session is not valid for the selected university"}, status=401)
            if membership and membership.must_change_password and request.path not in {
                "/api/v1/auth/me",
                "/api/v1/auth/logout",
                "/api/v1/auth/password/complete-setup",
            }:
                return JsonResponse({"detail": "Complete password setup before continuing", "code": "password_setup_required"}, status=428)
            policy = policy_for(membership.institution.tenant_id) if membership else policy_for(None)
            if policy.require_trusted_device and (not authorization or not authorization.approved_at):
                logout(request)
                return JsonResponse({"detail": "Administrator device approval is required", "code": "device_approval_required"}, status=401)
            if policy.approved_networks:
                from .services import _ip_in_approved_network

                if not _ip_in_approved_network(client_ip(request), policy.approved_networks):
                    session.revoked_at = now
                    session.revoked_reason = "approved_network_policy"
                    session.save(update_fields=["revoked_at", "revoked_reason", "updated_at"])
                    logout(request)
                    return JsonResponse({"detail": "This network is not approved"}, status=403)
            session.expires_at = now + timedelta(minutes=policy.session_timeout_minutes)
            session.ip_address = client_ip(request)
            session.save(update_fields=["expires_at", "ip_address", "last_seen_at", "updated_at"])
            request.session.set_expiry(policy.session_timeout_minutes * 60)
            request.access_session = session
        return self.get_response(request)
