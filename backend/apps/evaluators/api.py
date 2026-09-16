from ninja import Router
from ninja.errors import HttpError

from apps.core.authz import require_roles
from apps.evaluators import services
from apps.evaluators.schemas import AvailabilityIn, EvaluatorCreateIn, EvaluatorUpdateIn, ExpertiseIn, LifecycleIn
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
        "version": evaluator.version,
        "status": evaluator.status,
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


@router.get("/{evaluator_id}/history")
def history(request, evaluator_id: str):
    membership = require_roles(request, *WRITE_ROLES, Membership.Role.AUDITOR)
    return services.evaluator_history(membership.institution.tenant_id, evaluator_id)


@router.get("/{evaluator_id}/work-history")
def work_history(request, evaluator_id: str):
    membership = require_roles(request, *WRITE_ROLES, Membership.Role.AUDITOR)
    from apps.allocation.services import evaluator_work_history

    return evaluator_work_history(tenant_id=membership.institution.tenant_id, evaluator_id=evaluator_id)
