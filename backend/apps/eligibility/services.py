import secrets
from datetime import date, timedelta
from pathlib import PurePosixPath

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.configuration.models import Subject
from apps.core.services import record_event
from apps.core.idempotency import begin_idempotent, complete_idempotent
from apps.core.models import AuditEvent
from apps.eligibility.models import EligibilityHistory, EligibilityRecord, VerificationApproval, VerificationCase, VerificationDocument
from apps.evaluators.models import Evaluator, Expertise
from apps.evaluators.services import change_lifecycle
from apps.repository.storage import read_object_metadata, signed_object_url


class EligibilityError(Exception):
    pass


class EligibilityConflict(EligibilityError):
    pass


REQUIRED_CHECKS = {
    "official_id", "university_employee", "faculty", "mobile", "email", "institutional_email",
    "qualification", "experience", "institution", "department", "designation", "subject_expertise", "documents", "kyc",
}
DOCUMENT_TYPES = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}


def _verification_row(item):
    approvals = list(item.approvals.all())
    approval_count = sum(approval.decision == VerificationApproval.Decision.APPROVED for approval in approvals)
    return {
        "id": str(item.id), "evaluator_id": str(item.evaluator_id), "evaluator": item.evaluator.display_name,
        "status": item.status, "checks": item.checks,
        "submitted_at": item.submitted_at.isoformat() if item.submitted_at else None,
        "reviewed_at": item.reviewed_at.isoformat() if item.reviewed_at else None,
        "expires_on": item.expires_on.isoformat() if item.expires_on else None,
        "revalidation_due_on": item.revalidation_due_on.isoformat() if item.revalidation_due_on else None,
        "revalidation_due": bool(item.revalidation_due_on and item.revalidation_due_on <= date.today()),
        "approval_count": approval_count, "required_approvals": item.required_approvals,
        "fraud_signals": item.fraud_signals,
        "documents": [{"id": str(document.id), "kind": document.kind, "status": document.status, "sha256": document.sha256, "byte_size": document.byte_size, "version": document.version} for document in item.documents.all()],
        "version": item.version, "notes": item.notes,
    }


def _eligibility_row(item):
    return {
        "id": str(item.id), "evaluator_id": str(item.evaluator_id), "evaluator": item.evaluator.display_name,
        "subject_id": str(item.subject_id), "subject": item.subject.code, "status": item.status,
        "qualification_ok": item.qualification_ok, "experience_ok": item.experience_ok,
        "institution_ok": item.institution_ok, "expertise_ok": item.expertise_ok,
        "has_conflict": item.has_conflict, "is_debarred": item.is_debarred,
        "is_blacklisted": item.is_blacklisted, "risk_reasons": item.risk_reasons,
        "valid_from": item.valid_from.isoformat() if item.valid_from else None,
        "expires_on": item.expires_on.isoformat() if item.expires_on else None, "version": item.version,
    }


def eligibility_catalog(tenant_id):
    verifications = VerificationCase.objects.filter(tenant_id=tenant_id).select_related("evaluator").prefetch_related("approvals", "documents").order_by("evaluator__display_name")
    records = EligibilityRecord.objects.filter(tenant_id=tenant_id).select_related("evaluator", "subject").order_by("evaluator__display_name", "subject__code")
    history = AuditEvent.objects.filter(tenant_id=tenant_id, action__startswith="eligibility.").values(
        "id", "actor_id", "action", "aggregate_type", "aggregate_id", "payload", "occurred_at"
    )[:250]
    return {"verifications": [_verification_row(item) for item in verifications], "eligibility": [_eligibility_row(item) for item in records], "history": list(history)}


@transaction.atomic
def create_verification(*, tenant_id, actor_id, evaluator_id, checks, notes):
    evaluator = Evaluator.objects.filter(id=evaluator_id, tenant_id=tenant_id).first()
    if not evaluator:
        raise EligibilityError("Evaluator was not found")
    if VerificationCase.objects.filter(evaluator=evaluator).exists():
        raise EligibilityConflict("A verification case already exists for this evaluator")
    duplicate_filter = Q()
    for field in ("email", "mobile", "employee_id"):
        value = getattr(evaluator, field)
        if value:
            duplicate_filter |= Q(**{f"{field}__iexact": value})
    duplicates = Evaluator.objects.none()
    if duplicate_filter:
        duplicates = Evaluator.objects.filter(tenant_id=tenant_id).exclude(id=evaluator.id).filter(duplicate_filter)
    signals = []
    if duplicates.exists():
        signals.append("duplicate_profile_match")
    if not evaluator.employee_id:
        signals.append("official_identifier_missing")
    if not evaluator.mobile:
        signals.append("mobile_missing")
    if not evaluator.email:
        signals.append("email_missing")
    if any(marker in evaluator.display_name.lower() for marker in ("dummy", "fake", "test user")):
        signals.append("suspicious_profile_content")
    verification = VerificationCase.objects.create(tenant_id=tenant_id, evaluator=evaluator, checks=checks, notes=notes, fraud_signals=signals)
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="eligibility.verification.created", aggregate="VerificationCase", aggregate_id=verification.id, payload={"evaluator_id": str(evaluator.id), "fraud_signals": signals, "duplicate_matches": [str(item.id) for item in duplicates[:20]]})
    return verification


@transaction.atomic
def update_verification(*, tenant_id, actor_id, verification_id, version, checks, notes):
    verification = VerificationCase.objects.select_for_update().filter(
        id=verification_id,
        tenant_id=tenant_id,
    ).first()
    if not verification:
        raise EligibilityError("Verification case was not found")
    if verification.version != version:
        raise EligibilityConflict("Verification case was changed by another user")
    if verification.status != VerificationCase.Status.DRAFT:
        raise EligibilityConflict("Only draft verification checks can be changed")
    unsupported = set(checks) - REQUIRED_CHECKS
    if unsupported:
        raise EligibilityError(f"Unsupported verification checks: {', '.join(sorted(unsupported))}")
    verification.checks = {key: bool(checks.get(key, False)) for key in REQUIRED_CHECKS}
    verification.notes = notes
    verification.version += 1
    verification.save(update_fields=["checks", "notes", "version", "updated_at"])
    record_event(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="eligibility.verification.updated",
        aggregate="VerificationCase",
        aggregate_id=verification.id,
        payload={"completed_checks": sum(verification.checks.values()), "version": verification.version},
    )
    return verification


@transaction.atomic
def submit_verification(*, tenant_id, actor_id, verification_id, version, notes="", expires_on=None, idempotency_key=""):
    record, replay_id = begin_idempotent(tenant_id=tenant_id, scope="eligibility.verification.submit", key=idempotency_key, payload={"verification_id": verification_id, "version": version, "notes": notes, "expires_on": expires_on})
    if replay_id:
        return VerificationCase.objects.get(id=replay_id, tenant_id=tenant_id)
    verification = VerificationCase.objects.select_for_update().select_related("evaluator").filter(id=verification_id, tenant_id=tenant_id).first()
    if not verification:
        raise EligibilityError("Verification case was not found")
    if verification.version != version:
        raise EligibilityConflict("Verification case was changed by another user")
    if verification.status != VerificationCase.Status.DRAFT:
        raise EligibilityConflict("Only draft verification cases can be submitted")
    missing = {key for key in REQUIRED_CHECKS if not verification.checks.get(key, False)}
    if missing:
        raise EligibilityError(f"Verification checks are missing: {', '.join(sorted(missing))}")
    if verification.fraud_signals:
        raise EligibilityError("Profile risk signals must be resolved before verification can proceed")
    if not verification.documents.filter(status=VerificationDocument.Status.COMPLETED).exists():
        raise EligibilityError("At least one verified identity or qualification document is required")
    verification.status = VerificationCase.Status.SUBMITTED
    verification.submitted_at = timezone.now()
    verification.submitted_by_id = actor_id
    verification.notes = notes or verification.notes
    verification.version += 1
    verification.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="eligibility.verification.submitted", aggregate="VerificationCase", aggregate_id=verification.id, payload={"version": verification.version})
    complete_idempotent(record, verification.id)
    return verification


@transaction.atomic
def review_verification(*, tenant_id, actor_id, verification_id, version, approve, notes, expires_on):
    verification = VerificationCase.objects.select_for_update().select_related("evaluator").filter(id=verification_id, tenant_id=tenant_id).first()
    if not verification:
        raise EligibilityError("Verification case was not found")
    if verification.version != version:
        raise EligibilityConflict("Verification case was changed by another user")
    if verification.status not in {VerificationCase.Status.SUBMITTED, VerificationCase.Status.IN_REVIEW}:
        raise EligibilityConflict("Verification case is not awaiting review")
    if verification.submitted_by_id == actor_id:
        raise EligibilityConflict("The submitter cannot approve their own verification case")
    if verification.approvals.filter(approver_id=actor_id).exists():
        raise EligibilityConflict("This reviewer has already decided this verification case")
    if approve and not all(verification.checks.get(key, False) for key in REQUIRED_CHECKS):
        raise EligibilityError("All verification checks must pass before approval")
    proposed_expiry = expires_on or verification.expires_on
    if approve and (not proposed_expiry or proposed_expiry <= date.today()):
        raise EligibilityError("Approved verification requires a future expiry date")
    level = verification.approvals.count() + 1
    VerificationApproval.objects.create(tenant_id=tenant_id, verification=verification, level=level, approver_id=actor_id, decision=VerificationApproval.Decision.APPROVED if approve else VerificationApproval.Decision.REJECTED, notes=notes)
    approved_count = verification.approvals.filter(decision=VerificationApproval.Decision.APPROVED).count()
    is_final = approve and approved_count >= verification.required_approvals
    verification.status = VerificationCase.Status.APPROVED if is_final else VerificationCase.Status.IN_REVIEW if approve else VerificationCase.Status.REJECTED
    verification.reviewed_at = timezone.now() if is_final or not approve else None
    verification.reviewer_id = actor_id if is_final or not approve else None
    verification.expires_on = proposed_expiry if approve else None
    verification.revalidation_due_on = proposed_expiry - timedelta(days=30) if is_final else None
    verification.notes = notes
    verification.version += 1
    verification.save()
    if is_final and verification.evaluator.status == Evaluator.Status.PENDING:
        change_lifecycle(tenant_id=tenant_id, actor_id=actor_id, evaluator_id=verification.evaluator_id, version=verification.evaluator.version, status=Evaluator.Status.ACTIVE, reason="Verification approved")
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="eligibility.verification.approval_recorded", aggregate="VerificationCase", aggregate_id=verification.id, payload={"evaluator_id": str(verification.evaluator_id), "decision": "approved" if approve else "rejected", "level": level, "status": verification.status, "expires_on": str(verification.expires_on)})
    return verification


@transaction.atomic
def create_document_upload(*, tenant_id, actor_id, verification_id, kind, content_type, maximum_bytes):
    verification = VerificationCase.objects.select_for_update().filter(id=verification_id, tenant_id=tenant_id, status=VerificationCase.Status.DRAFT).first()
    if not verification:
        raise EligibilityConflict("Documents can only be added to a draft verification case")
    if content_type not in DOCUMENT_TYPES or not 1 <= maximum_bytes <= 20_000_000:
        raise EligibilityError("Document type or size limit is not supported")
    filename = f"{secrets.token_urlsafe(18)}{DOCUMENT_TYPES[content_type]}"
    key = str(PurePosixPath("evaluator-verification") / str(tenant_id) / str(verification.id) / filename)
    document = VerificationDocument.objects.create(tenant_id=tenant_id, verification=verification, kind=kind[:40], storage_key=key, mime_type=content_type, maximum_bytes=maximum_bytes, expires_at=timezone.now() + timedelta(minutes=5))
    url, expires = signed_object_url(method="PUT", key=key, content_type=content_type, max_bytes=maximum_bytes)
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="eligibility.document.upload_authorized", aggregate="VerificationDocument", aggregate_id=document.id, payload={"verification_id": str(verification.id), "kind": document.kind})
    return document, url, expires


@transaction.atomic
def finalize_document_upload(*, tenant_id, actor_id, document_id, version, idempotency_key=""):
    record, replay_id = begin_idempotent(tenant_id=tenant_id, scope="eligibility.document.finalize", key=idempotency_key, payload={"document_id": document_id, "version": version})
    if replay_id:
        return VerificationDocument.objects.get(id=replay_id, tenant_id=tenant_id)
    document = VerificationDocument.objects.select_for_update().filter(id=document_id, tenant_id=tenant_id).first()
    if not document:
        raise EligibilityError("Verification document was not found")
    if document.version != version:
        raise EligibilityConflict("Verification document was changed by another user")
    if document.status == VerificationDocument.Status.COMPLETED:
        complete_idempotent(record, document.id)
        return document
    if document.expires_at <= timezone.now():
        document.status = VerificationDocument.Status.FAILED
        document.version += 1
        document.save(update_fields=["status", "version", "updated_at"])
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="eligibility.document.failed", aggregate="VerificationDocument", aggregate_id=document.id, payload={"reason": "upload_expired"})
        complete_idempotent(record, document.id)
        return document
    try:
        metadata = read_object_metadata(document.storage_key)
    except Exception as exc:
        raise EligibilityConflict("Uploaded document is not available in secure storage") from exc
    if metadata.mime_type != document.mime_type or metadata.byte_size > document.maximum_bytes:
        document.status = VerificationDocument.Status.FAILED
        document.version += 1
        document.save(update_fields=["status", "version", "updated_at"])
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="eligibility.document.failed", aggregate="VerificationDocument", aggregate_id=document.id, payload={"reason": "authorization_mismatch"})
        complete_idempotent(record, document.id)
        return document
    document.sha256, document.byte_size = metadata.sha256, metadata.byte_size
    document.status = VerificationDocument.Status.COMPLETED
    document.verified_at, document.verified_by_id = timezone.now(), actor_id
    document.version += 1
    document.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="eligibility.document.verified", aggregate="VerificationDocument", aggregate_id=document.id, payload={"sha256": document.sha256, "byte_size": document.byte_size})
    complete_idempotent(record, document.id)
    return document


@transaction.atomic
def revalidate_expiries(*, tenant_id, actor_id):
    today = date.today()
    expired_verifications = list(VerificationCase.objects.select_for_update().filter(tenant_id=tenant_id, status=VerificationCase.Status.APPROVED, expires_on__lte=today))
    expired_eligibility = list(EligibilityRecord.objects.select_for_update().filter(tenant_id=tenant_id, status=EligibilityRecord.Status.ELIGIBLE, expires_on__lte=today))
    for verification in expired_verifications:
        verification.status = VerificationCase.Status.EXPIRED
        verification.version += 1
        verification.save(update_fields=["status", "version", "updated_at"])
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="eligibility.verification.expired", aggregate="VerificationCase", aggregate_id=verification.id, payload={"expires_on": str(verification.expires_on)})
    for eligibility in expired_eligibility:
        eligibility.status = EligibilityRecord.Status.EXPIRED
        eligibility.risk_reasons = sorted(set(eligibility.risk_reasons + ["eligibility_expired"]))
        eligibility.version += 1
        eligibility.save(update_fields=["status", "risk_reasons", "version", "updated_at"])
        EligibilityHistory.objects.create(tenant_id=tenant_id, eligibility=eligibility, action="expired", actor_id=actor_id, snapshot=_eligibility_row(eligibility), reason="Eligibility validity period elapsed")
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="eligibility.subject.expired", aggregate="EligibilityRecord", aggregate_id=eligibility.id, payload={"expires_on": str(eligibility.expires_on)})
    return len(expired_verifications), len(expired_eligibility)


@transaction.atomic
def verify_expertise(*, tenant_id, actor_id, expertise_id, reason):
    expertise = Expertise.objects.select_for_update().select_related("evaluator", "subject").filter(id=expertise_id, tenant_id=tenant_id).first()
    if not expertise:
        raise EligibilityError("Expertise record was not found")
    if expertise.verified:
        raise EligibilityConflict("Expertise is already verified")
    expertise.verified = True
    expertise.save(update_fields=["verified", "updated_at"])
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="eligibility.expertise.verified", aggregate="Expertise", aggregate_id=expertise.id, payload={"evaluator_id": str(expertise.evaluator_id), "subject_id": str(expertise.subject_id), "reason": reason})
    return expertise


@transaction.atomic
def assess_eligibility(*, tenant_id, actor_id, evaluator_id, subject_id, has_conflict, is_debarred, is_blacklisted, expires_on):
    evaluator = Evaluator.objects.filter(id=evaluator_id, tenant_id=tenant_id).first()
    subject = Subject.objects.filter(id=subject_id, tenant_id=tenant_id).first()
    if not evaluator or not subject:
        raise EligibilityError("Evaluator or subject was not found")
    verification = VerificationCase.objects.filter(evaluator=evaluator, status=VerificationCase.Status.APPROVED, expires_on__gt=date.today()).first()
    if not verification:
        raise EligibilityError("Evaluator must have an active approved verification")
    expertise = Expertise.objects.filter(evaluator=evaluator, subject=subject, verified=True).first()
    risk_reasons = []
    if not expertise:
        risk_reasons.append("subject_expertise_not_verified")
    if has_conflict:
        risk_reasons.append("conflict_of_interest")
    if is_debarred:
        risk_reasons.append("debarred")
    if is_blacklisted:
        risk_reasons.append("blacklisted")
    status = EligibilityRecord.Status.ELIGIBLE if not risk_reasons and expires_on > date.today() else EligibilityRecord.Status.INELIGIBLE
    try:
        eligibility = EligibilityRecord.objects.create(tenant_id=tenant_id, evaluator=evaluator, subject=subject, status=status, qualification_ok=True, experience_ok=True, institution_ok=True, expertise_ok=bool(expertise), has_conflict=has_conflict, is_debarred=is_debarred, is_blacklisted=is_blacklisted, risk_reasons=risk_reasons, valid_from=date.today(), expires_on=expires_on, approved_by_id=actor_id)
    except IntegrityError as exc:
        raise EligibilityConflict("Eligibility already exists for this evaluator and subject") from exc
    EligibilityHistory.objects.create(tenant_id=tenant_id, eligibility=eligibility, action="assessed", actor_id=actor_id, snapshot=_eligibility_row(eligibility), reason=", ".join(risk_reasons))
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="eligibility.subject.assessed", aggregate="EligibilityRecord", aggregate_id=eligibility.id, payload={"evaluator_id": str(evaluator.id), "subject_id": str(subject.id), "status": status, "risk_reasons": risk_reasons})
    return eligibility


@transaction.atomic
def block_eligibility(*, tenant_id, actor_id, eligibility_id, version, reason, changes):
    eligibility = EligibilityRecord.objects.select_for_update().select_related("evaluator", "subject").filter(id=eligibility_id, tenant_id=tenant_id).first()
    if not eligibility:
        raise EligibilityError("Eligibility record was not found")
    if eligibility.version != version:
        raise EligibilityConflict("Eligibility record was changed by another user")
    for field, value in changes.items():
        if value is not None:
            setattr(eligibility, field, value)
    eligibility.status = EligibilityRecord.Status.INELIGIBLE
    eligibility.risk_reasons = sorted(set(eligibility.risk_reasons + [reason]))
    eligibility.version += 1
    eligibility.save()
    EligibilityHistory.objects.create(tenant_id=tenant_id, eligibility=eligibility, action="blocked", actor_id=actor_id, snapshot=_eligibility_row(eligibility), reason=reason)
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="eligibility.subject.blocked", aggregate="EligibilityRecord", aggregate_id=eligibility.id, payload={"reason": reason})
    return eligibility
