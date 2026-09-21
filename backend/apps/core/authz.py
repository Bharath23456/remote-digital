from ninja.errors import HttpError
from django.utils import timezone

from apps.tenancy.models import Membership


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

    other_session = SecureEvaluationSession.objects.select_related("assignment", "assignment__script").filter(
        tenant_id=assignment.tenant_id,
        evaluator=assignment.evaluator,
        status__in=[SecureEvaluationSession.Status.ACTIVE, SecureEvaluationSession.Status.PAUSED],
    ).exclude(assignment=assignment).order_by("-started_at").first()
    if other_session:
        script_code = getattr(other_session.assignment.script, "script_code", "")
        suffix = f" Current assignment: {script_code}." if script_code else ""
        raise HttpError(409, f"Finish or exit the current evaluation before opening another paper.{suffix}")

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
