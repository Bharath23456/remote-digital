import re

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone

from apps.tenancy.models import Membership, TenantAccount, TenantDomain


class AuditRequestContextMiddleware:
    """Expose the request IP to atomic domain audit writes."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from apps.core.services import reset_audit_ip, set_audit_ip

        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",", 1)[0].strip()
        token = set_audit_ip(forwarded or request.META.get("REMOTE_ADDR") or None)
        try:
            return self.get_response(request)
        finally:
            reset_audit_ip(token)


class TenantDomainMiddleware:
    """Resolve a trusted tenant from the request hostname before authorization."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        hostname = request.get_host().split(":", 1)[0].lower().rstrip(".")
        domain = TenantDomain.objects.select_related("tenant_account__root_institution").filter(
            hostname__iexact=hostname,
            status=TenantDomain.Status.ACTIVE,
        ).first()
        request.tenant_domain = domain
        request.resolved_tenant_id = domain.tenant_account.root_institution.tenant_id if domain else None
        request.is_platform_host = hostname in settings.PLATFORM_HOSTS
        if domain and request.path.startswith("/api/") and request.path != "/api/v1/enterprise/domain-context":
            account = domain.tenant_account
            if account.status != TenantAccount.Status.ACTIVE or not account.root_institution.is_active:
                return JsonResponse({"detail": "This university workspace is suspended"}, status=423)
            if request.user.is_authenticated:
                has_membership = Membership.objects.filter(
                    user=request.user,
                    institution__tenant_id=request.resolved_tenant_id,
                    institution__is_active=True,
                    is_active=True,
                ).exists()
                if not has_membership:
                    return JsonResponse({"detail": "Your account does not belong to this university"}, status=403)
                active_tenant_id = request.session.get("active_tenant_id")
                if active_tenant_id and str(request.resolved_tenant_id) != str(active_tenant_id):
                    return JsonResponse({"detail": "Sign in again to enter this university domain"}, status=409)
        elif settings.ENFORCE_TENANT_DOMAINS and not request.is_platform_host and request.path.startswith("/api/"):
            return JsonResponse({"detail": "University domain was not found"}, status=404)
        return self.get_response(request)


class TenantEntitlementMiddleware:
    """Enforce purchased module boundaries at the API edge."""

    module_prefixes = (
        ("/api/v1/configuration/", "configuration"),
        ("/api/v1/evaluator-management", "evaluators"),
        ("/api/v1/eligibility", "evaluators"),
        ("/api/v1/receiving/", "receiving"),
        ("/api/v1/custody/", "custody"),
        ("/api/v1/scanning/", "digitization"),
        ("/api/v1/scan-processing/", "digitization"),
        ("/api/v1/integrity/", "digitization"),
        ("/api/v1/anonymisation/", "anonymisation"),
        ("/api/v1/repository/", "repository"),
        ("/api/v1/allocation/", "allocation"),
        ("/api/v1/assignment-governance/", "assignment_governance"),
        ("/api/v1/rubrics/", "rubrics"),
        ("/api/v1/marking/", "evaluation"),
        ("/api/v1/workflow/", "evaluation"),
        ("/api/v1/valuation/", "valuation"),
        ("/api/v1/discrepancy/", "valuation"),
        ("/api/v1/security/", "security"),
        ("/api/v1/audit/", "audit"),
        ("/api/v1/enterprise/institutions", "enterprise"),
    )
    phase4_prefixes = (
        ("/api/v1/phase4/moderation/", "assessment"),
        ("/api/v1/phase4/revaluation/", "assessment"),
        ("/api/v1/phase4/completion/", "assessment"),
        ("/api/v1/phase4/remote-security/", "operations"),
        ("/api/v1/phase4/monitoring/", "operations"),
        ("/api/v1/phase4/workload/", "operations"),
        ("/api/v1/phase4/runtime/", "operations"),
        ("/api/v1/phase4/issues", "operations"),
        ("/api/v1/phase4/notifications", "operations"),
        ("/api/v1/phase4/centres", "operations"),
        ("/api/v1/phase4/remuneration/", "services"),
        ("/api/v1/phase4/student/", "services"),
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/api/") and request.user.is_authenticated:
            module = self._module_for(request)
            if module:
                active_tenant_id = request.session.get("active_tenant_id")
                account = TenantAccount.objects.filter(root_institution__tenant_id=active_tenant_id).only("enabled_modules").first()
                if account and module not in account.enabled_modules:
                    return JsonResponse({"detail": "This module is not enabled for the university", "code": "module_not_enabled"}, status=403)
                membership = Membership.objects.filter(
                    user=request.user,
                    institution__tenant_id=active_tenant_id,
                    is_active=True,
                ).only("enabled_modules").first()
                if membership and membership.enabled_modules and module not in membership.enabled_modules:
                    return JsonResponse({"detail": "Your administrator has not granted access to this module", "code": "module_access_denied"}, status=403)
        return self.get_response(request)

    def _module_for(self, request):
        if request.path == "/api/v1/phase4/catalog":
            return {"assessment": "assessment", "operations": "operations", "services": "services"}.get(request.GET.get("section", ""))
        if request.path == "/api/v1/receiving/guided/catalog":
            return None
        if re.fullmatch(r"/api/v1/receiving/guided/lookup/bundles/[^/]+", request.path):
            return "custody"
        if re.fullmatch(r"/api/v1/receiving/guided/lookup/packets/[^/]+", request.path):
            return "digitization"
        if request.path in ("/api/v1/receiving/guided/bundles/receive", "/api/v1/receiving/guided/packets/receive"):
            return "custody"
        if re.fullmatch(r"/api/v1/receiving/guided/packets/[^/]+/recognize", request.path):
            return "digitization"
        if request.path == "/api/v1/repository/manual-scan/uploads" or re.fullmatch(r"/api/v1/repository/(uploads/[^/]+/finalize|scripts/[^/]+/complete-scan)", request.path):
            return "digitization"
        if re.fullmatch(r"/api/v1/anonymisation/scripts/[^/]+/auto-mask", request.path):
            return "digitization"
        for prefix, module in self.module_prefixes + self.phase4_prefixes:
            if request.path.startswith(prefix):
                return module
        return None


class EvaluatorRoleBoundaryMiddleware:
    """Keep evaluator sessions inside the minimum API surface needed to mark scripts."""

    allowed_paths = {
        "/api/health",
        "/api/v1/allocation/catalog",
        "/api/v1/evaluator-management/face/status",
        "/api/v1/evaluator-management/face/verify-access",
    }
    allowed_prefixes = (
        "/api/v1/auth/",
        "/api/v1/allocation/assignments/",
        "/api/v1/assignment-governance/assignments/",
        "/api/v1/marking/",
        "/api/v1/phase4/remote-security/",
        "/api/v1/valuation/evaluations/",
        "/api/v1/workflow/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/api/") and request.user.is_authenticated:
            membership = self._membership(request)
            if membership and membership.role == Membership.Role.EVALUATOR and not self._allowed(request.path):
                if not self._has_emergency_access(request, membership):
                    return JsonResponse({"detail": "This role cannot access that operation"}, status=403)
        return self.get_response(request)

    def _membership(self, request):
        memberships = Membership.objects.filter(
            user=request.user,
            is_active=True,
            institution__is_active=True,
        ).select_related("institution")
        active_tenant_id = request.session.get("active_tenant_id")
        if active_tenant_id:
            membership = memberships.filter(institution__tenant_id=active_tenant_id).order_by("created_at").first()
            if membership:
                return membership
        return memberships.order_by("created_at").first()

    def _allowed(self, path):
        return path in self.allowed_paths or path.startswith(self.allowed_prefixes)

    def _has_emergency_access(self, request, membership):
        from apps.security.models import EmergencyAccessGrant

        return EmergencyAccessGrant.objects.filter(
            tenant_id=membership.institution.tenant_id,
            user=request.user,
            expires_at__gt=timezone.now(),
            revoked_at__isnull=True,
        ).exists()


class IntakeDeskBoundaryMiddleware:
    """Constrain intake workers to the one desk their role owns, including read routes."""

    desk_paths = {
        Membership.Role.BUNDLE_PREPARER: {
            ("GET", "/api/v1/receiving/guided/catalog"),
            ("GET", "/api/v1/receiving/guided/papers"),
            ("POST", "/api/v1/receiving/guided/bundles"),
            ("POST", "/api/v1/receiving/guided/bundles/start"),
        },
        Membership.Role.INTAKE_RECEIVER: {
            ("GET", "/api/v1/receiving/guided/catalog"),
            ("POST", "/api/v1/receiving/guided/bundles/receive"),
            ("POST", "/api/v1/receiving/guided/packets/receive"),
        },
        Membership.Role.SCAN_OPERATOR: {
            ("GET", "/api/v1/receiving/guided/catalog"),
            ("POST", "/api/v1/repository/manual-scan/uploads"),
        },
    }
    scan_patterns = (
        re.compile(r"/api/v1/receiving/guided/packets/[^/]+/recognize"),
        re.compile(r"/api/v1/repository/uploads/[^/]+/finalize"),
        re.compile(r"/api/v1/repository/scripts/[^/]+/complete-scan"),
        re.compile(r"/api/v1/anonymisation/scripts/[^/]+/auto-mask"),
    )
    receiver_lookup = re.compile(r"/api/v1/receiving/guided/lookup/bundles/[^/]+")
    scanner_lookup = re.compile(r"/api/v1/receiving/guided/lookup/packets/[^/]+")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/api/") and request.user.is_authenticated:
            active_tenant_id = request.session.get("active_tenant_id")
            membership = Membership.objects.filter(user=request.user, institution__tenant_id=active_tenant_id, is_active=True).only("role").first()
            if membership and membership.role in self.desk_paths:
                route = (request.method, request.path)
                allowed = request.path.startswith("/api/v1/auth/") or route in self.desk_paths[membership.role]
                if request.method == "GET" and membership.role == Membership.Role.INTAKE_RECEIVER:
                    allowed = allowed or bool(self.receiver_lookup.fullmatch(request.path))
                if request.method == "GET" and membership.role == Membership.Role.SCAN_OPERATOR:
                    allowed = allowed or bool(self.scanner_lookup.fullmatch(request.path))
                if membership.role == Membership.Role.SCAN_OPERATOR and request.method == "POST":
                    allowed = allowed or any(pattern.fullmatch(request.path) for pattern in self.scan_patterns)
                if not allowed:
                    return JsonResponse({"detail": "This intake desk cannot access that operation"}, status=403)
        return self.get_response(request)
