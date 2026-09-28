from ninja import Router, Schema
from ninja.errors import HttpError

from apps.allocation.models import Assignment
from apps.configuration.models import Paper
from apps.core.authz import membership_for, require_roles, require_secure_evaluation_session, require_step_up
from apps.evaluators.models import Evaluator
from apps.tenancy.models import Membership

from .models import AssignmentApproval, AssignmentGovernancePolicy, AssignmentLock, SecureReassignmentRequest
from .services import acquire_lock, approve_assignment, decide_reassignment, release_lock, request_reassignment, save_policy


router = Router(tags=["Assignment governance and security"])
ADMIN_ROLES = (Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER)


class VersionIn(Schema):
    version: int


class ApprovalIn(Schema):
    decision: str
    note: str = ""


class ReleaseIn(Schema):
    token: str
    reason: str = "released"


class ReassignmentIn(Schema):
    proposed_evaluator_id: str | None = None
    reason: str


class DecisionIn(Schema):
    version: int
    approve: bool


class PolicyIn(Schema):
    version: int | None = None
    approval_required: bool = True
    assignment_expiry_hours: int = 120
    lock_minutes: int = 30
    require_step_up_for_reassignment: bool = True


def _assignment_for_request(request, assignment_id):
    membership = membership_for(request)
    queryset = Assignment.objects.filter(
        id=assignment_id,
        tenant_id=membership.institution.tenant_id,
    ).select_related("script__paper__session", "evaluator")
    if membership.role == Membership.Role.EVALUATOR:
        queryset = queryset.filter(evaluator__user_id=request.auth.id)
    assignment = queryset.first()
    if not assignment:
        raise HttpError(404, "Assignment not found")
    return membership, assignment


@router.get("/catalog")
def catalog(request):
    membership = membership_for(request)
    tenant_id = membership.institution.tenant_id
    assignments = Assignment.objects.filter(tenant_id=tenant_id).select_related("script__paper", "evaluator")
    if membership.role == Membership.Role.EVALUATOR:
        assignments = assignments.filter(evaluator__user_id=request.auth.id)
    assignment_ids = assignments.values_list("id", flat=True)
    return {
        "evaluators": [{"id": str(item.id), "code": item.evaluator_code, "name": item.display_name, "status": item.status} for item in Evaluator.objects.filter(tenant_id=tenant_id).order_by("display_name")],
        "policies": [
            {"id": str(item.id), "paper_id": str(item.paper_id), "paper": item.paper.code, "approval_required": item.approval_required, "assignment_expiry_hours": item.assignment_expiry_hours, "lock_minutes": item.lock_minutes, "require_step_up_for_reassignment": item.require_step_up_for_reassignment, "version": item.version}
            for item in AssignmentGovernancePolicy.objects.filter(tenant_id=tenant_id).select_related("paper")
        ],
        "assignments": [
            {
                "id": str(item.id),
                "script": item.script.script_code,
                "paper": item.script.paper.code,
                "round": item.valuation_round,
                "status": item.status,
                "due_at": item.due_at.isoformat(),
                "version": item.version,
                "approved": AssignmentApproval.objects.filter(assignment=item, decision=AssignmentApproval.Decision.APPROVED).exists(),
                "locked": AssignmentLock.objects.filter(assignment=item, released_at__isnull=True).exists(),
            }
            for item in assignments.order_by("due_at")[:1000]
        ],
        "reassignments": [
            {
                "id": str(item.id),
                "assignment_id": str(item.assignment_id),
                "script": item.assignment.script.script_code,
                "status": item.status,
                "reason": item.reason,
                "expires_at": item.expires_at.isoformat(),
                "version": item.version,
            }
            for item in SecureReassignmentRequest.objects.filter(tenant_id=tenant_id, assignment_id__in=assignment_ids).select_related("assignment__script")[:500]
        ],
    }


@router.put("/papers/{paper_id}/policy")
def update_policy(request, paper_id: str, payload: PolicyIn):
    membership = require_roles(request, *ADMIN_ROLES)
    paper = Paper.objects.filter(id=paper_id, tenant_id=membership.institution.tenant_id).first()
    if not paper:
        raise HttpError(404, "Paper not found")
    item = save_policy(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, paper=paper, expected_version=payload.version, values=payload.dict(exclude={"version"}))
    return {"id": str(item.id), "version": item.version}


@router.post("/assignments/{assignment_id}/lock")
def lock_assignment(request, assignment_id: str, payload: VersionIn):
    membership, assignment = _assignment_for_request(request, assignment_id)
    if membership.role != Membership.Role.EVALUATOR:
        raise HttpError(403, "Only the assigned evaluator can acquire this lock")
    require_secure_evaluation_session(request, assignment)
    lock, token = acquire_lock(
        tenant_id=membership.institution.tenant_id,
        actor_id=request.auth.id,
        assignment=assignment,
        evaluator=assignment.evaluator,
        expected_version=payload.version,
        access_session=request.access_session,
    )
    return {"id": str(lock.id), "token": token, "expires_at": lock.expires_at.isoformat()}


@router.post("/assignments/{assignment_id}/unlock")
def unlock_assignment(request, assignment_id: str, payload: ReleaseIn):
    membership, assignment = _assignment_for_request(request, assignment_id)
    if membership.role == Membership.Role.EVALUATOR:
        require_secure_evaluation_session(request, assignment, allow_paused=True)
    lock = release_lock(
        tenant_id=membership.institution.tenant_id,
        actor_id=request.auth.id,
        assignment_id=assignment_id,
        token=payload.token,
        reason=payload.reason,
    )
    return {"id": str(lock.id), "released_at": lock.released_at.isoformat()}


@router.post("/assignments/{assignment_id}/approval")
def assignment_approval(request, assignment_id: str, payload: ApprovalIn):
    membership = require_roles(request, *ADMIN_ROLES)
    _, assignment = _assignment_for_request(request, assignment_id)
    approval = approve_assignment(
        tenant_id=membership.institution.tenant_id,
        actor_id=request.auth.id,
        assignment=assignment,
        decision=payload.decision,
        note=payload.note,
    )
    return {"id": str(approval.id), "decision": approval.decision}


@router.post("/assignments/{assignment_id}/reassignments")
def create_reassignment(request, assignment_id: str, payload: ReassignmentIn):
    membership = require_roles(request, *ADMIN_ROLES)
    require_step_up(request)
    _, assignment = _assignment_for_request(request, assignment_id)
    evaluator = None
    if payload.proposed_evaluator_id:
        evaluator = Evaluator.objects.filter(
            id=payload.proposed_evaluator_id,
            tenant_id=membership.institution.tenant_id,
        ).first()
        if not evaluator:
            raise HttpError(404, "Proposed evaluator not found")
    item = request_reassignment(
        tenant_id=membership.institution.tenant_id,
        actor_id=request.auth.id,
        assignment=assignment,
        proposed_evaluator=evaluator,
        reason=payload.reason,
    )
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/reassignments/{request_id}/decision")
def reassignment_decision(request, request_id: str, payload: DecisionIn):
    membership = require_roles(request, *ADMIN_ROLES)
    require_step_up(request)
    item = decide_reassignment(
        tenant_id=membership.institution.tenant_id,
        actor_id=request.auth.id,
        request_id=request_id,
        expected_version=payload.version,
        approve=payload.approve,
    )
    return {"id": str(item.id), "status": item.status, "version": item.version}
