from ninja import Router
from ninja.errors import HttpError

from apps.configuration import services
from apps.configuration.schemas import (
    AcademicYearIn,
    CourseIn,
    ConfigurationChangeDecisionIn,
    ConfigurationChangeIn,
    EvaluationCentreIn,
    EvaluationEventIn,
    ExamSessionIn,
    PaperActionIn,
    PaperIn,
    PaperUpdateIn,
    ProgrammeIn,
    QuestionIn,
    RegulationIn,
    SubjectIn,
    TermIn,
)
from apps.core.authz import require_roles
from apps.tenancy.custom_fields import persist_custom_values, validate_custom_values
from apps.tenancy.models import Membership


router = Router(tags=["Evaluation configuration"])
WRITE_ROLES = (Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER)


def _context(request):
    membership = require_roles(request, *WRITE_ROLES)
    return membership.institution.tenant_id, request.auth.id


def _run(call, **kwargs):
    try:
        return call(**kwargs)
    except services.ConfigurationConflict as exc:
        raise HttpError(409, str(exc)) from exc
    except services.ConfigurationError as exc:
        raise HttpError(422, str(exc)) from exc


@router.get("/catalog")
def catalog(request):
    membership = require_roles(request, *WRITE_ROLES, Membership.Role.AUDITOR)
    return services.configuration_catalog(membership.institution.tenant_id)


@router.post("/academic-years")
def add_academic_year(request, payload: AcademicYearIn):
    tenant_id, actor_id = _context(request)
    item = _run(services.create_academic_year, tenant_id=tenant_id, actor_id=actor_id, **payload.dict())
    return {"id": str(item.id)}


@router.post("/regulations")
def add_regulation(request, payload: RegulationIn):
    tenant_id, actor_id = _context(request)
    item = _run(services.create_regulation, tenant_id=tenant_id, actor_id=actor_id, **payload.dict())
    return {"id": str(item.id)}


@router.post("/terms")
def add_term(request, payload: TermIn):
    tenant_id, actor_id = _context(request)
    item = _run(services.create_term, tenant_id=tenant_id, actor_id=actor_id, **payload.dict())
    return {"id": str(item.id)}


@router.post("/sessions")
def add_session(request, payload: ExamSessionIn):
    tenant_id, actor_id = _context(request)
    values = payload.dict()
    custom_fields = validate_custom_values(tenant_id=tenant_id, form_key="exam_session", values=values.pop("custom_fields"))
    item = _run(services.create_session, tenant_id=tenant_id, actor_id=actor_id, **values)
    persist_custom_values(tenant_id=tenant_id, actor_id=actor_id, form_key="exam_session", record_id=item.id, values=custom_fields)
    return {"id": str(item.id), "version": item.version}


@router.post("/events")
def add_event(request, payload: EvaluationEventIn):
    tenant_id, actor_id = _context(request)
    item = _run(services.create_event, tenant_id=tenant_id, actor_id=actor_id, **payload.dict())
    return {"id": str(item.id)}


@router.post("/programmes")
def add_programme(request, payload: ProgrammeIn):
    tenant_id, actor_id = _context(request)
    item = _run(services.create_programme, tenant_id=tenant_id, actor_id=actor_id, **payload.dict())
    return {"id": str(item.id)}


@router.post("/courses")
def add_course(request, payload: CourseIn):
    tenant_id, actor_id = _context(request)
    item = _run(services.create_course, tenant_id=tenant_id, actor_id=actor_id, **payload.dict())
    return {"id": str(item.id)}


@router.post("/subjects")
def add_subject(request, payload: SubjectIn):
    tenant_id, actor_id = _context(request)
    item = _run(services.create_subject, tenant_id=tenant_id, actor_id=actor_id, **payload.dict())
    return {"id": str(item.id)}


@router.post("/centres")
def add_centre(request, payload: EvaluationCentreIn):
    tenant_id, actor_id = _context(request)
    item = _run(services.create_centre, tenant_id=tenant_id, actor_id=actor_id, **payload.dict())
    return {"id": str(item.id)}


@router.post("/papers")
def add_paper(request, payload: PaperIn):
    tenant_id, actor_id = _context(request)
    values = payload.dict()
    custom_fields = validate_custom_values(tenant_id=tenant_id, form_key="paper", values=values.pop("custom_fields"))
    item = _run(services.create_paper, tenant_id=tenant_id, actor_id=actor_id, **values)
    persist_custom_values(tenant_id=tenant_id, actor_id=actor_id, form_key="paper", record_id=item.id, values=custom_fields)
    return services.paper_detail(item)


@router.patch("/papers/{paper_id}")
def edit_paper(request, paper_id: str, payload: PaperUpdateIn):
    tenant_id, actor_id = _context(request)
    paper = _run(
        services.update_paper,
        tenant_id=tenant_id,
        actor_id=actor_id,
        paper_id=paper_id,
        version=payload.version,
        changes=payload.dict(exclude={"version"}, exclude_none=True),
    )
    return services.paper_detail(paper)


@router.post("/papers/{paper_id}/questions")
def create_question(request, paper_id: str, payload: QuestionIn):
    tenant_id, actor_id = _context(request)
    question, paper = _run(services.add_question, tenant_id=tenant_id, actor_id=actor_id, paper_id=paper_id, values=payload.dict())
    return {"id": str(question.id), "paper_version": paper.version, "readiness": services.paper_readiness(paper)}


@router.post("/papers/{paper_id}/submit")
def submit(request, paper_id: str, payload: PaperActionIn):
    tenant_id, actor_id = _context(request)
    paper = _run(services.submit_paper, tenant_id=tenant_id, actor_id=actor_id, paper_id=paper_id, idempotency_key=request.headers.get("Idempotency-Key", ""), **payload.dict())
    return services.paper_detail(paper)


@router.post("/papers/{paper_id}/approve")
def approve(request, paper_id: str, payload: PaperActionIn):
    tenant_id, actor_id = _context(request)
    paper = _run(services.approve_paper, tenant_id=tenant_id, actor_id=actor_id, paper_id=paper_id, **payload.dict())
    return services.paper_detail(paper)


@router.post("/papers/{paper_id}/freeze")
def freeze(request, paper_id: str, payload: PaperActionIn):
    tenant_id, actor_id = _context(request)
    paper = _run(services.freeze_paper, tenant_id=tenant_id, actor_id=actor_id, paper_id=paper_id, **payload.dict())
    return services.paper_detail(paper)


@router.get("/papers/{paper_id}/history")
def history(request, paper_id: str):
    membership = require_roles(request, *WRITE_ROLES, Membership.Role.AUDITOR)
    return services.paper_history(membership.institution.tenant_id, paper_id)


@router.get("/papers/{paper_id}/impact")
def impact(request, paper_id: str):
    membership = require_roles(request, *WRITE_ROLES, Membership.Role.AUDITOR)
    paper = services._related(services.Paper, membership.institution.tenant_id, paper_id, "Paper")
    return services.paper_change_impact(paper)


@router.post("/papers/{paper_id}/changes")
def request_change(request, paper_id: str, payload: ConfigurationChangeIn):
    tenant_id, actor_id = _context(request)
    change = _run(
        services.request_paper_change,
        tenant_id=tenant_id,
        actor_id=actor_id,
        paper_id=paper_id,
        **payload.dict(),
    )
    return next(item for item in services.change_request_rows(tenant_id) if item["id"] == str(change.id))


@router.post("/changes/{request_id}/decision")
def decide_change(request, request_id: str, payload: ConfigurationChangeDecisionIn):
    tenant_id, actor_id = _context(request)
    change, paper = _run(
        services.decide_paper_change,
        tenant_id=tenant_id,
        actor_id=actor_id,
        request_id=request_id,
        **payload.dict(),
    )
    return {
        "change": next(item for item in services.change_request_rows(tenant_id) if item["id"] == str(change.id)),
        "paper": services.paper_detail(paper) if paper else None,
    }


@router.get("/readiness")
def readiness(request):
    membership = require_roles(request, *WRITE_ROLES, Membership.Role.AUDITOR)
    return services.configuration_readiness(membership.institution.tenant_id)
