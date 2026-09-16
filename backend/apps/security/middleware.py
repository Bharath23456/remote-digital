import json

from django.db import transaction
from django.http import JsonResponse

from apps.core.services import record_event
from apps.tenancy.models import Membership

from .models import DlpIncident, SecurityPolicy


class DlpInspectionMiddleware:
    monitored_prefixes = (
        "/api/v1/allocation/",
        "/api/v1/anonymisation/",
        "/api/v1/custody/",
        "/api/v1/repository/",
    )
    protected_keys = {
        "candidate_name",
        "student_name",
        "registration_number",
        "roll_number",
        "candidate_email",
        "candidate_mobile",
        "date_of_birth",
        "candidate_address",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        detected = self._detected_fields(request)
        if detected and request.user.is_authenticated:
            active_tenant = request.session.get("active_tenant_id")
            memberships = Membership.objects.filter(user=request.user, is_active=True, institution__is_active=True).select_related("institution")
            membership = memberships.filter(institution__tenant_id=active_tenant).first() if active_tenant else memberships.first()
            if membership and self._enabled(membership.institution.tenant_id):
                with transaction.atomic():
                    incident = DlpIncident.objects.create(
                        tenant_id=membership.institution.tenant_id,
                        actor=request.user,
                        channel="evaluation_core_api",
                        data_classification="candidate_pii",
                        rule="candidate_identity_boundary",
                        resource_reference=request.path[:160],
                        details={"fields": sorted(detected), "method": request.method},
                    )
                    record_event(
                        tenant_id=membership.institution.tenant_id,
                        actor_id=request.user.id,
                        action="security.dlp.blocked",
                        aggregate="DlpIncident",
                        aggregate_id=incident.id,
                        payload={"fields": sorted(detected), "path": request.path},
                    )
                return JsonResponse({"detail": "Candidate identity data is prohibited in evaluation-core"}, status=422)
        return self.get_response(request)

    def _enabled(self, tenant_id):
        policy = SecurityPolicy.objects.filter(tenant_id=tenant_id).only("dlp_enabled").first()
        return policy.dlp_enabled if policy else True

    def _detected_fields(self, request):
        if request.method not in {"POST", "PUT", "PATCH"} or not request.path.startswith(self.monitored_prefixes):
            return set()
        if "application/json" not in (request.content_type or ""):
            return set()
        try:
            payload = json.loads(request.body or b"{}")
        except (TypeError, ValueError):
            return set()
        return self._walk(payload)

    def _walk(self, value):
        found = set()
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key).strip().lower()
                if normalized in self.protected_keys:
                    found.add(normalized)
                found.update(self._walk(child))
        elif isinstance(value, list):
            for child in value:
                found.update(self._walk(child))
        return found
