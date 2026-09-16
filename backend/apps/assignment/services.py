import hashlib
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from ninja.errors import HttpError

from apps.allocation.models import Assignment
from apps.allocation.services import redistribute_assignment
from apps.core.services import record_event

from .models import AssignmentApproval, AssignmentGovernancePolicy, AssignmentLock, SecureReassignmentRequest


def policy_for(tenant_id, paper):
    return AssignmentGovernancePolicy.objects.filter(tenant_id=tenant_id, paper=paper).first() or AssignmentGovernancePolicy(
        tenant_id=tenant_id,
        paper=paper,
        assignment_expiry_hours=120,
    )


@transaction.atomic
def save_policy(*, tenant_id, actor_id, paper, expected_version, values):
    policy = AssignmentGovernancePolicy.objects.select_for_update().filter(tenant_id=tenant_id, paper=paper).first()
    if policy:
        if expected_version is None or policy.version != expected_version:
            raise HttpError(409, "Assignment governance policy is stale")
    else:
        if expected_version not in (None, 0):
            raise HttpError(409, "Assignment governance policy does not exist")
        policy = AssignmentGovernancePolicy(tenant_id=tenant_id, paper=paper)
    for field in ("approval_required", "assignment_expiry_hours", "lock_minutes", "require_step_up_for_reassignment"):
        if field in values:
            setattr(policy, field, values[field])
    if not 1 <= policy.lock_minutes <= 240 or not 1 <= policy.assignment_expiry_hours <= 720:
        raise HttpError(422, "Lock and expiry windows are outside the supported range")
    if policy.pk:
        policy.version += 1
    policy.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="assignment.policy.saved", aggregate="AssignmentGovernancePolicy", aggregate_id=policy.id, payload={"paper_id": str(paper.id), "version": policy.version})
    return policy


def acquire_lock(*, tenant_id, actor_id, assignment, evaluator, expected_version, access_session=None):
    now = timezone.now()
    policy = policy_for(tenant_id, assignment.script.paper)
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    with transaction.atomic():
        current = Assignment.objects.select_for_update().get(id=assignment.id, tenant_id=tenant_id)
        if current.version != expected_version:
            raise HttpError(409, "Assignment was changed by another operation")
        if current.evaluator_id != evaluator.id or current.status in (Assignment.Status.SUBMITTED, Assignment.Status.EXPIRED):
            raise HttpError(403, "This assignment cannot be locked by the current evaluator")
        AssignmentLock.objects.filter(assignment=current, released_at__isnull=True, expires_at__lte=now).update(
            released_at=now,
            release_reason="expired",
        )
        lock = AssignmentLock.objects.select_for_update().filter(assignment=current, released_at__isnull=True).first()
        resumed = bool(lock and access_session and lock.evaluator_id == evaluator.id and lock.access_session_id == access_session.id)
        if lock and not resumed:
            raise HttpError(409, "Assignment is already locked in another session")
        if resumed:
            lock.token_hash = token_hash
            lock.expires_at = now + timedelta(minutes=policy.lock_minutes)
            lock.save(update_fields=["token_hash", "expires_at", "updated_at"])
        else:
            lock = AssignmentLock.objects.create(
                tenant_id=tenant_id,
                assignment=current,
                evaluator=evaluator,
                access_session=access_session,
                token_hash=token_hash,
                expires_at=now + timedelta(minutes=policy.lock_minutes),
            )
        record_event(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="assignment.lock.resumed" if resumed else "assignment.lock.acquired",
            aggregate="AssignmentLock",
            aggregate_id=lock.id,
            payload={"assignment_id": str(current.id), "expires_at": lock.expires_at.isoformat(), "access_session_id": str(access_session.id) if access_session else None},
        )
    return lock, raw_token


def release_lock(*, tenant_id, actor_id, assignment_id, token, reason="released"):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with transaction.atomic():
        lock = AssignmentLock.objects.select_for_update().filter(
            tenant_id=tenant_id,
            assignment_id=assignment_id,
            token_hash=token_hash,
            released_at__isnull=True,
        ).first()
        if not lock:
            raise HttpError(404, "Active assignment lock not found")
        lock.released_at = timezone.now()
        lock.release_reason = reason[:120]
        lock.save(update_fields=["released_at", "release_reason", "updated_at"])
        record_event(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="assignment.lock.released",
            aggregate="AssignmentLock",
            aggregate_id=lock.id,
            payload={"assignment_id": str(assignment_id), "reason": lock.release_reason},
        )
    return lock


@transaction.atomic
def approve_assignment(*, tenant_id, actor_id, assignment, decision, note):
    if decision not in AssignmentApproval.Decision.values:
        raise HttpError(422, "Unsupported approval decision")
    if assignment.history.filter(action="assigned", actor_id=actor_id).exists():
        raise HttpError(409, "Assignment creator cannot approve their own assignment")
    approval, created = AssignmentApproval.objects.get_or_create(
        tenant_id=tenant_id,
        assignment=assignment,
        actor_id=actor_id,
        defaults={"decision": decision, "note": note.strip()},
    )
    if not created:
        raise HttpError(409, "This operator already decided this assignment")
    record_event(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=f"assignment.approval.{decision}",
        aggregate="AssignmentApproval",
        aggregate_id=approval.id,
        payload={"assignment_id": str(assignment.id)},
    )
    return approval


@transaction.atomic
def request_reassignment(*, tenant_id, actor_id, assignment, proposed_evaluator, reason):
    if len(reason.strip()) < 8:
        raise HttpError(422, "A specific reassignment reason is required")
    item = SecureReassignmentRequest.objects.create(
        tenant_id=tenant_id,
        assignment=assignment,
        proposed_evaluator=proposed_evaluator,
        reason=reason.strip(),
        requested_by_id=actor_id,
        expires_at=timezone.now() + timedelta(hours=24),
    )
    record_event(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="assignment.reassignment.requested",
        aggregate="SecureReassignmentRequest",
        aggregate_id=item.id,
        payload={"assignment_id": str(assignment.id), "reason": item.reason},
    )
    return item


def decide_reassignment(*, tenant_id, actor_id, request_id, expected_version, approve):
    expired = False
    with transaction.atomic():
        item = SecureReassignmentRequest.objects.select_for_update().select_related(
            "assignment__script__paper",
            "proposed_evaluator",
        ).filter(id=request_id, tenant_id=tenant_id).first()
        if not item or item.version != expected_version:
            raise HttpError(409, "Reassignment request is missing or stale")
        if item.status != SecureReassignmentRequest.Status.REQUESTED:
            raise HttpError(409, "Reassignment request is no longer pending")
        if item.requested_by_id == actor_id:
            raise HttpError(409, "Requester cannot approve their own reassignment")
        if item.expires_at <= timezone.now():
            item.status = SecureReassignmentRequest.Status.EXPIRED
            item.version += 1
            item.save(update_fields=["status", "version", "updated_at"])
            record_event(tenant_id=tenant_id, actor_id=actor_id, action="assignment.reassignment.expired", aggregate="SecureReassignmentRequest", aggregate_id=item.id, payload={"assignment_id": str(item.assignment_id)})
            expired = True
        else:
            item.status = SecureReassignmentRequest.Status.APPROVED if approve else SecureReassignmentRequest.Status.REJECTED
            item.decided_by_id = actor_id
            item.decided_at = timezone.now()
            item.version += 1
            item.save(update_fields=["status", "decided_by_id", "decided_at", "version", "updated_at"])
            record_event(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action=f"assignment.reassignment.{item.status}",
                aggregate="SecureReassignmentRequest",
                aggregate_id=item.id,
                payload={"assignment_id": str(item.assignment_id)},
            )
        if approve and not expired:
            updated = redistribute_assignment(
                tenant_id=tenant_id,
                actor_id=actor_id,
                assignment_id=item.assignment_id,
                expected_version=item.assignment.version,
                reason=item.reason,
                replacement_evaluator=item.proposed_evaluator,
            )
            item.status = SecureReassignmentRequest.Status.EXECUTED
            item.executed_at = timezone.now()
            item.version += 1
            item.save(update_fields=["status", "executed_at", "version", "updated_at"])
            record_event(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="assignment.reassignment.executed",
                aggregate="SecureReassignmentRequest",
                aggregate_id=item.id,
                payload={"assignment_id": str(updated.id), "evaluator_id": str(updated.evaluator_id)},
            )
    if expired:
        raise HttpError(409, "Reassignment request has expired")
    return item


def expire_assignments(*, tenant_id, actor_id="system"):
    expired = []
    for assignment in Assignment.objects.filter(
        tenant_id=tenant_id,
        due_at__lte=timezone.now(),
        status__in=[Assignment.Status.ASSIGNED, Assignment.Status.ACCEPTED, Assignment.Status.IN_PROGRESS],
    ):
        with transaction.atomic():
            current = Assignment.objects.select_for_update().get(id=assignment.id)
            if current.due_at > timezone.now() or current.status not in (
                Assignment.Status.ASSIGNED,
                Assignment.Status.ACCEPTED,
                Assignment.Status.IN_PROGRESS,
            ):
                continue
            current.status = Assignment.Status.EXPIRED
            current.version += 1
            current.save(update_fields=["status", "version", "updated_at"])
            AssignmentLock.objects.filter(assignment=current, released_at__isnull=True).update(
                released_at=timezone.now(),
                release_reason="assignment expired",
            )
            record_event(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="assignment.expired",
                aggregate="Assignment",
                aggregate_id=current.id,
                payload={"due_at": current.due_at.isoformat()},
            )
            expired.append(current.id)
    return expired
