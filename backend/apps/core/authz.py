from ninja.errors import HttpError
from django.utils import timezone

from apps.tenancy.models import Membership


ROLE_MODULES = {
    Membership.Role.EVALUATOR: {"evaluation"},
    Membership.Role.RECEIVING_OFFICER: {"receiving"},
    Membership.Role.SCRIPT_RECEIVER: {"receiving"},
    Membership.Role.SCANNER_OPERATOR: {"digitization"},
    Membership.Role.CUSTODY_OFFICER: {"custody"},
    Membership.Role.AUDITOR: {"audit"},
}


def allowed_modules_for_role(role, available_modules):
    """Return tenant modules constrained by the member's operational role."""
    available = set(available_modules)
    role_modules = ROLE_MODULES.get(role)
    return sorted(available if role_modules is None else available.intersection(role_modules))


def membership_for(request):
    memberships = Membership.objects.select_related("institution", "user").filter(
        user=request.auth,
        is_active=True,
        institution__is_active=True,
    )
    active_tenant_id = request.session.get("active_tenant_id")
    membership = memberships.filter(institution__tenant_id=active_tenant_id).order_by("created_at").first() if active_tenant_id else None
    membership = membership or memberships.order_by("created_at").first()
    if not membership:
        raise HttpError(403, "No active ADMIEZO membership")
    return membership


def require_roles(request, *roles):
    membership = membership_for(request)
    if membership.role not in roles and membership.role != Membership.Role.PLATFORM_ADMIN:
        from apps.security.models import EmergencyAccessGrant

        emergency_access = EmergencyAccessGrant.objects.filter(
            tenant_id=membership.institution.tenant_id,
            user=request.auth,
            role__in=roles,
            expires_at__gt=timezone.now(),
            revoked_at__isnull=True,
        ).exists()
        if not emergency_access:
            raise HttpError(403, "This role cannot perform that operation")
    return membership


def require_step_up(request):
    from apps.identity_auth.services import active_session_for_request

    session = active_session_for_request(request)
    if not session or not session.is_step_up_valid:
        raise HttpError(428, "Step-up authentication is required")
    return session


def require_secure_evaluation_session(request, assignment, *, allow_paused=False):
    from apps.phase4.models import SecureEvaluationSession

    session_id = request.headers.get("X-Secure-Evaluation-Session", "")
    statuses = [SecureEvaluationSession.Status.ACTIVE]
    if allow_paused:
        statuses.append(SecureEvaluationSession.Status.PAUSED)
    secure_session = SecureEvaluationSession.objects.filter(
        id=session_id or None,
        tenant_id=assignment.tenant_id,
        assignment=assignment,
        access_session_id=request.access_session.id,
        status__in=statuses,
    ).first()
    if not secure_session:
        raise HttpError(423, "An active secure evaluation session is required")
    return secure_session
