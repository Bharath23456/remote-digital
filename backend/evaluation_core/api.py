from django.contrib.auth.models import User
from django.db.models import Count, Sum
from django.utils import timezone
from ninja import NinjaAPI, Schema
from ninja.errors import HttpError
from ninja.security import SessionAuth

from apps.allocation.models import Assignment
from apps.configuration.api import router as configuration_router
from apps.configuration import services as configuration_services
from apps.configuration.schemas import PaperUpdateIn
from apps.configuration.models import ExamSession, Paper
from apps.core.models import AuditEvent, OutboxEvent
from apps.core.authz import membership_for, require_roles
from apps.custody.api import router as custody_router
from apps.custody.models import CustodyEvent, Script
from apps.evaluators.models import Evaluator
from apps.evaluators.api import router as evaluators_router
from apps.evaluators import services as evaluator_services
from apps.eligibility.api import router as eligibility_router
from apps.identity_auth.api import router as auth_router
from apps.receiving.api import router as receiving_router
from apps.receiving.guided_api import router as guided_receiving_router
from apps.receiving.models import Dispatch, ReceivingException
from apps.repository.api import router as repository_router
from apps.repository.models import ScriptAsset
from apps.tenancy.models import Membership
from apps.tenancy.api import router as enterprise_router
from apps.security.api import router as security_router
from apps.anonymisation.api import router as anonymisation_router
from apps.allocation.api import router as allocation_router
from apps.assignment.api import router as assignment_router
from apps.scanning.api import router as scanning_router
from apps.scan_processing.api import router as scan_processing_router
from apps.rubrics.api import router as rubrics_router
from apps.marking.api import router as marking_router
from apps.workflow.api import router as workflow_router
from apps.valuation.api import router as valuation_router
from apps.discrepancy.api import router as discrepancy_router
from apps.integrity.api import router as integrity_router
from apps.phase4.api import router as phase4_router
from apps.ai_evaluation.api import router as ai_evaluation_router


api = NinjaAPI(
    title="ADMIEZO Evaluation Core",
    version="1.0.0",
    auth=SessionAuth(),
    urls_namespace="admiezo_api",
)
api.add_router("/v1/enterprise", enterprise_router)
api.add_router("/v1/configuration", configuration_router)
api.add_router("/v1/evaluator-management", evaluators_router)
api.add_router("/v1/eligibility", eligibility_router)
api.add_router("/v1/auth", auth_router)
api.add_router("/v1/security", security_router)
api.add_router("/v1/receiving", receiving_router)
api.add_router("/v1/receiving/guided", guided_receiving_router)
api.add_router("/v1/custody", custody_router)
api.add_router("/v1/repository", repository_router)
api.add_router("/v1/anonymisation", anonymisation_router)
api.add_router("/v1/allocation", allocation_router)
api.add_router("/v1/assignment-governance", assignment_router)
api.add_router("/v1/scanning", scanning_router)
api.add_router("/v1/scan-processing", scan_processing_router)
api.add_router("/v1/rubrics", rubrics_router)
api.add_router("/v1/marking", marking_router)
api.add_router("/v1/workflow", workflow_router)
api.add_router("/v1/valuation", valuation_router)
api.add_router("/v1/discrepancy", discrepancy_router)
api.add_router("/v1/integrity", integrity_router)
api.add_router("/v1/phase4", phase4_router)
api.add_router("/v1/ai-evaluation", ai_evaluation_router)


class EvaluatorIn(Schema):
    evaluator_code: str
    display_name: str
    institution_name: str
    department: str
    designation: str
    qualification: str
    years_experience: int = 0
    daily_capacity: int = 20


class StatusIn(Schema):
    status: str
    version: int


@api.get("/health", auth=None)
def health(request):
    return {"status": "ok", "service": "evaluation-core", "version": api.version}


@api.get("/v1/operations/overview")
def operations_overview(request):
    membership = require_roles(
        request,
        Membership.Role.UNIVERSITY_ADMIN,
        Membership.Role.EXAM_CONTROLLER,
        Membership.Role.RECEIVING_OFFICER,
        Membership.Role.AUDITOR,
    )
    tenant_id = membership.institution.tenant_id
    now = timezone.now()
    session = ExamSession.objects.filter(tenant_id=tenant_id).order_by("-evaluation_starts_at").first()
    scripts = Script.objects.filter(tenant_id=tenant_id)
    assignments = Assignment.objects.filter(tenant_id=tenant_id)
    dispatches = Dispatch.objects.filter(tenant_id=tenant_id)
    state_counts = {row["state"]: row["count"] for row in scripts.values("state").annotate(count=Count("id"))}
    expected = dispatches.aggregate(total=Sum("expected_scripts"))["total"] or 0
    received = dispatches.aggregate(total=Sum("received_scripts"))["total"] or 0
    pending_exceptions = ReceivingException.objects.filter(tenant_id=tenant_id, cleared_at__isnull=True).count()
    pending_evaluators = Evaluator.objects.filter(tenant_id=tenant_id, status=Evaluator.Status.PENDING).count()
    unreconciled = dispatches.exclude(status__in=[Dispatch.Status.RECONCILED, Dispatch.Status.CLOSED]).count()
    paper_rows = []
    for paper in Paper.objects.filter(tenant_id=tenant_id).select_related("subject", "session").annotate(
        script_total=Count("scripts", distinct=True),
        assigned_total=Count("scripts__assignments", distinct=True),
    ).order_by("code")[:8]:
        issues = []
        question_total = sum(question.max_marks for question in paper.questions.all())
        if question_total != paper.max_marks:
            issues.append("Question total does not match maximum marks")
        if paper.pass_marks > paper.max_marks:
            issues.append("Pass marks exceed maximum marks")
        if paper.status != Paper.Status.FROZEN:
            issues.append("Configuration is not frozen")
        paper_rows.append({
            "id": str(paper.id),
            "code": paper.code,
            "title": paper.title,
            "programme": paper.subject.programme.name,
            "scripts": paper.script_total,
            "assigned": paper.assigned_total,
            "progress": round((paper.assigned_total / paper.script_total) * 100) if paper.script_total else 0,
            "status": paper.status,
            "readiness": "ready" if not issues else "attention",
            "issues": issues,
        })
    recent_events = [{
        "id": str(event.id),
        "script": event.script.script_code,
        "transition": f"{event.from_state} to {event.to_state}",
        "location": event.location,
        "at": event.created_at.isoformat(),
    } for event in CustodyEvent.objects.filter(tenant_id=tenant_id).select_related("script")[:6]]
    module_counts = {
        "configuration": Paper.objects.filter(tenant_id=tenant_id).count(),
        "evaluators": Evaluator.objects.filter(tenant_id=tenant_id).count(),
        "receiving": dispatches.count(),
        "custody": scripts.count(),
        "repository": ScriptAsset.objects.filter(tenant_id=tenant_id).count(),
        "allocation": assignments.count(),
    }
    return {
        "generated_at": now.isoformat(),
        "session": {"name": session.name, "term": session.term, "status": session.status} if session else None,
        "metrics": {
            "expected_scripts": expected,
            "received_scripts": received,
            "registered_scripts": scripts.count(),
            "stored_scripts": state_counts.get(Script.State.STORED, 0) + state_counts.get(Script.State.ASSIGNED, 0),
            "assigned_scripts": assignments.count(),
            "evaluation_progress": round(assignments.filter(status=Assignment.Status.SUBMITTED).count() * 100 / max(assignments.count(), 1)),
        },
        "pipeline": [
            {"key": "receiving", "label": "Receiving", "count": received, "state": "complete" if expected and received >= expected else "active"},
            {"key": "identification", "label": "Identification", "count": scripts.count(), "state": "active"},
            {"key": "repository", "label": "Repository", "count": module_counts["repository"], "state": "active"},
            {"key": "allocation", "label": "Allocation", "count": module_counts["allocation"], "state": "active"},
            {"key": "evaluation", "label": "Evaluation", "count": assignments.filter(status__in=[Assignment.Status.IN_PROGRESS, Assignment.Status.SUBMITTED]).count(), "state": "pending"},
            {"key": "finalisation", "label": "Finalisation", "count": state_counts.get(Script.State.FINALIZED, 0), "state": "pending"},
        ],
        "attention": [
            {"label": "Receiving exceptions", "count": pending_exceptions, "severity": "high" if pending_exceptions else "clear"},
            {"label": "Dispatches to reconcile", "count": unreconciled, "severity": "medium" if unreconciled else "clear"},
            {"label": "Evaluators awaiting verification", "count": pending_evaluators, "severity": "medium" if pending_evaluators else "clear"},
            {"label": "Unpublished audit events", "count": OutboxEvent.objects.filter(tenant_id=tenant_id, published_at__isnull=True).count(), "severity": "low"},
        ],
        "module_counts": module_counts,
        "papers": paper_rows,
        "recent_events": recent_events,
    }


@api.get("/v1/config/papers")
def list_papers(request):
    tenant_id = membership_for(request).institution.tenant_id
    return [{
        "id": str(paper.id), "code": paper.code, "title": paper.title,
        "subject": paper.subject.name, "max_marks": paper.max_marks,
        "pass_marks": paper.pass_marks, "valuation_rounds": paper.valuation_rounds,
        "status": paper.status, "version": paper.version,
    } for paper in Paper.objects.filter(tenant_id=tenant_id).select_related("subject").order_by("code")]


@api.patch("/v1/config/papers/{paper_id}")
def update_paper(request, paper_id: str, payload: PaperUpdateIn):
    membership = require_roles(request, Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER)
    tenant_id = membership.institution.tenant_id
    try:
        paper = configuration_services.update_paper(
            tenant_id=tenant_id,
            actor_id=request.auth.id,
            paper_id=paper_id,
            version=payload.version,
            changes=payload.dict(exclude_none=True, exclude={"version"}),
        )
    except configuration_services.ConfigurationConflict as exc:
        raise HttpError(409, str(exc)) from exc
    except configuration_services.ConfigurationError as exc:
        raise HttpError(422, str(exc)) from exc
    return {"id": str(paper.id), "version": paper.version}


@api.get("/v1/evaluators")
def list_evaluators(request):
    tenant_id = membership_for(request).institution.tenant_id
    return list(Evaluator.objects.filter(tenant_id=tenant_id).values("id", "evaluator_code", "display_name", "institution_name", "department", "designation", "grade", "status", "daily_capacity", "version").order_by("display_name"))


@api.post("/v1/evaluators")
def create_evaluator(request, payload: EvaluatorIn):
    membership = require_roles(request, Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER)
    tenant_id = membership.institution.tenant_id
    try:
        evaluator, _temporary_password = evaluator_services.create_evaluator(
            tenant_id=tenant_id,
            actor_id=request.auth.id,
            values={**payload.dict(), "create_login": False, "custom_fields": {}},
        )
    except evaluator_services.EvaluatorConflict as exc:
        raise HttpError(409, str(exc)) from exc
    except evaluator_services.EvaluatorError as exc:
        raise HttpError(422, str(exc)) from exc
    return {"id": str(evaluator.id), "status": evaluator.status, "version": evaluator.version}


@api.post("/v1/evaluators/{evaluator_id}/status")
def change_evaluator_status(request, evaluator_id: str, payload: StatusIn):
    membership = require_roles(request, Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER)
    tenant_id = membership.institution.tenant_id
    try:
        evaluator = evaluator_services.change_lifecycle(
            tenant_id=tenant_id,
            actor_id=request.auth.id,
            evaluator_id=evaluator_id,
            version=payload.version,
            status=payload.status,
            reason="Legacy API lifecycle request",
        )
    except evaluator_services.EvaluatorConflict as exc:
        raise HttpError(409, str(exc)) from exc
    except evaluator_services.EvaluatorError as exc:
        raise HttpError(422, str(exc)) from exc
    return {"id": str(evaluator.id), "status": evaluator.status, "version": evaluator.version}


@api.get("/v1/audit/events")
def audit_events(request):
    membership = require_roles(request, Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER, Membership.Role.AUDITOR)
    events = list(AuditEvent.objects.filter(tenant_id=membership.institution.tenant_id)[:100])
    actor_ids = [int(item.actor_id) for item in events if item.actor_id.isdigit()]
    users = {str(item.id): item.get_full_name() or item.email or item.username for item in User.objects.filter(id__in=actor_ids)}
    return [
        {
            "id": str(item.id),
            "user_name": users.get(item.actor_id, "System"),
            "action_performed": item.action.replace(".", " ").title(),
            "date": item.occurred_at.date().isoformat(),
            "time": item.occurred_at.time().replace(microsecond=0).isoformat(),
            "ip_address": item.ip_address or "Not recorded",
            "script_id": item.aggregate_id if item.aggregate_type == "Script" else item.payload.get("script_id", "—"),
            "details": item.payload or {"record": item.aggregate_type, "record_id": item.aggregate_id},
            "occurred_at": item.occurred_at,
        }
        for item in events
    ]
