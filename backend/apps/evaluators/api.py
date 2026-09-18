from ninja import Router
from ninja.errors import HttpError

from apps.allocation.models import Assignment
from apps.core.authz import membership_for, require_roles
from apps.evaluators import services
from apps.evaluators.models import Evaluator
from apps.evaluators.schemas import AvailabilityIn, EvaluatorCreateIn, EvaluatorUpdateIn, ExpertiseIn, FaceAccessIn, FaceCaptureIn, LifecycleIn
from apps.tenancy.models import Membership


router = Router(tags=["Evaluator master"])
WRITE_ROLES = (Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER)


def _context(request):
    membership = require_roles(request, *WRITE_ROLES)
    return membership.institution.tenant_id, request.auth.id


def _run(call, **kwargs):
    try:
        return call(**kwargs)
    except services.EvaluatorConflict as exc:
        raise HttpError(409, str(exc)) from exc
    except services.EvaluatorError as exc:
        raise HttpError(422, str(exc)) from exc


def _request_ip(request):
    forwarded = request.headers.get("X-Forwarded-For", "")
    return (forwarded.split(",", 1)[0] or request.META.get("REMOTE_ADDR") or "").strip() or None


@router.get("")
def catalog(request):
    membership = require_roles(request, *WRITE_ROLES, Membership.Role.AUDITOR)
    return services.evaluator_catalog(membership.institution.tenant_id)


@router.post("")
def create(request, payload: EvaluatorCreateIn):
    tenant_id, actor_id = _context(request)
    evaluator, temporary_password = _run(services.create_evaluator, tenant_id=tenant_id, actor_id=actor_id, values=payload.dict())
    return {
        "id": str(evaluator.id),
        "evaluator_code": evaluator.evaluator_code,
        "display_name": evaluator.display_name,
        "email": evaluator.email,
        "version": evaluator.version,
        "status": evaluator.status,
        "face_enrolled": False,
        "face_status": "not_enrolled",
        "face_enrollment_required": True,
        "login_enabled": bool(evaluator.user_id),
        "username": evaluator.email if evaluator.user_id else "",
        "temporary_password": temporary_password,
        "existing_identity": bool(evaluator.user_id and not temporary_password),
    }


@router.patch("/{evaluator_id}")
def update(request, evaluator_id: str, payload: EvaluatorUpdateIn):
    tenant_id, actor_id = _context(request)
    evaluator = _run(services.update_evaluator, tenant_id=tenant_id, actor_id=actor_id, evaluator_id=evaluator_id, version=payload.version, changes=payload.dict(exclude={"version"}, exclude_unset=True))
    return {"id": str(evaluator.id), "version": evaluator.version}


@router.post("/{evaluator_id}/lifecycle")
def lifecycle(request, evaluator_id: str, payload: LifecycleIn):
    tenant_id, actor_id = _context(request)
    evaluator = _run(services.change_lifecycle, tenant_id=tenant_id, actor_id=actor_id, evaluator_id=evaluator_id, **payload.dict())
    return {"id": str(evaluator.id), "version": evaluator.version, "status": evaluator.status, "grade": evaluator.grade}


@router.post("/{evaluator_id}/expertise")
def expertise(request, evaluator_id: str, payload: ExpertiseIn):
    tenant_id, actor_id = _context(request)
    item, evaluator = _run(services.add_expertise, tenant_id=tenant_id, actor_id=actor_id, evaluator_id=evaluator_id, values=payload.dict())
    return {"id": str(item.id), "evaluator_version": evaluator.version}


@router.post("/{evaluator_id}/availability")
def availability(request, evaluator_id: str, payload: AvailabilityIn):
    tenant_id, actor_id = _context(request)
    item, evaluator = _run(services.add_availability, tenant_id=tenant_id, actor_id=actor_id, evaluator_id=evaluator_id, values=payload.dict())
    return {"id": str(item.id), "evaluator_version": evaluator.version}


@router.get("/face/status")
def self_face_status(request):
    membership = require_roles(request, Membership.Role.EVALUATOR)
    evaluator = Evaluator.objects.filter(tenant_id=membership.institution.tenant_id, email__iexact=request.auth.email).first()
    if not evaluator:
        raise HttpError(404, "Evaluator was not found")
    return _run(services.face_status, tenant_id=membership.institution.tenant_id, evaluator_id=evaluator.id)


@router.post("/face/verify-access")
def verify_access(request, payload: FaceAccessIn):
    membership = require_roles(request, Membership.Role.EVALUATOR)
    tenant_id = membership.institution.tenant_id
    evaluator = Evaluator.objects.filter(tenant_id=tenant_id, email__iexact=request.auth.email, status=Evaluator.Status.ACTIVE).first()
    assignment = Assignment.objects.filter(id=payload.assignment_id, tenant_id=tenant_id, evaluator=evaluator).first() if evaluator else None
    if not evaluator or not assignment:
        raise HttpError(404, "Evaluator assignment was not found")
    item = _run(
        services.verify_evaluator_access,
        tenant_id=tenant_id,
        actor_id=request.auth.id,
        evaluator=evaluator,
        assignment=assignment,
        access_session=request.access_session,
        capture=payload.dict(exclude={"assignment_id"}),
        ip_address=_request_ip(request),
    )
    return {
        "id": str(item.id),
        "verified": item.verified,
        "liveness_verified": item.liveness_verified,
        "authorized": item.authorized,
        "access_granted": item.access_granted,
        "similarity_score": str(item.similarity_score),
        "threshold": str(item.threshold),
        "expires_at": item.expires_at.isoformat() if item.expires_at else None,
    }


@router.get("/{evaluator_id}/face/status")
def evaluator_face_status(request, evaluator_id: str):
    membership = require_roles(request, *WRITE_ROLES, Membership.Role.AUDITOR)
    return _run(services.face_status, tenant_id=membership.institution.tenant_id, evaluator_id=evaluator_id)


@router.post("/{evaluator_id}/face/enroll")
def enroll_face(request, evaluator_id: str, payload: FaceCaptureIn):
    tenant_id, actor_id = _context(request)
    template = _run(services.enroll_face_template, tenant_id=tenant_id, actor_id=actor_id, evaluator_id=evaluator_id, capture=payload.dict())
    return {
        "id": str(template.id),
        "evaluator_id": evaluator_id,
        "status": template.status,
        "model_version": template.model_version,
        "threshold": str(template.threshold),
        "quality_score": str(template.quality_score),
        "enrolled_at": template.enrolled_at.isoformat(),
        "version": template.version,
    }


@router.post("/{evaluator_id}/face/verify")
def verify_face(request, evaluator_id: str, payload: FaceCaptureIn):
    membership = membership_for(request)
    if membership.role == Membership.Role.EVALUATOR:
        evaluator = Evaluator.objects.filter(id=evaluator_id, tenant_id=membership.institution.tenant_id, email__iexact=request.auth.email).first()
        if not evaluator:
            raise HttpError(403, "Evaluators can verify only their own identity")
    elif membership.role not in (*WRITE_ROLES, Membership.Role.AUDITOR) and membership.role != Membership.Role.PLATFORM_ADMIN:
        raise HttpError(403, "This role cannot perform that operation")
    item = _run(services.verify_face_template, tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, evaluator_id=evaluator_id, capture=payload.dict(), access_session=getattr(request, "access_session", None), ip_address=_request_ip(request))
    return {
        "id": str(item.id),
        "verified": item.verified,
        "liveness_verified": item.liveness_verified,
        "authorized": item.authorized,
        "access_granted": item.access_granted,
        "failure_reason": item.failure_reason,
        "similarity_score": str(item.similarity_score),
        "threshold": str(item.threshold),
        "expires_at": item.expires_at.isoformat() if item.expires_at else None,
    }


@router.get("/{evaluator_id}/history")
def history(request, evaluator_id: str):
    membership = require_roles(request, *WRITE_ROLES, Membership.Role.AUDITOR)
    return services.evaluator_history(membership.institution.tenant_id, evaluator_id)


@router.get("/{evaluator_id}/work-history")
def work_history(request, evaluator_id: str):
    membership = require_roles(request, *WRITE_ROLES, Membership.Role.AUDITOR)
    from apps.allocation.services import evaluator_work_history

    return evaluator_work_history(tenant_id=membership.institution.tenant_id, evaluator_id=evaluator_id)
