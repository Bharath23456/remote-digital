from ninja import Router
from ninja.errors import HttpError

from apps.core.authz import require_roles
from apps.eligibility import services
from apps.eligibility.schemas import (
    EligibilityAssessIn,
    EligibilityBlockIn,
    NoteIn,
    VerificationActionIn,
    VerificationCreateIn,
    VerificationDocumentFinalizeIn,
    VerificationDocumentUploadIn,
    VerificationUpdateIn,
)
from apps.tenancy.models import Membership


router = Router(tags=["Evaluator eligibility"])
WRITE_ROLES = (Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER)


def _context(request):
    membership = require_roles(request, *WRITE_ROLES)
    return membership.institution.tenant_id, request.auth.id


def _run(call, **kwargs):
    try:
        return call(**kwargs)
    except services.EligibilityConflict as exc:
        raise HttpError(409, str(exc)) from exc
    except services.EligibilityError as exc:
        raise HttpError(422, str(exc)) from exc


@router.get("")
def catalog(request):
    membership = require_roles(request, *WRITE_ROLES, Membership.Role.AUDITOR)
    return services.eligibility_catalog(membership.institution.tenant_id)


@router.post("/verifications")
def create_verification(request, payload: VerificationCreateIn):
    tenant_id, actor_id = _context(request)
    item = _run(services.create_verification, tenant_id=tenant_id, actor_id=actor_id, **payload.dict())
    return {"id": str(item.id), "version": item.version, "status": item.status}


@router.post("/verifications/{verification_id}/submit")
def submit(request, verification_id: str, payload: VerificationActionIn):
    tenant_id, actor_id = _context(request)
    item = _run(services.submit_verification, tenant_id=tenant_id, actor_id=actor_id, verification_id=verification_id, idempotency_key=request.headers.get("Idempotency-Key", ""), **payload.dict())
    return {"id": str(item.id), "version": item.version, "status": item.status}


@router.patch("/verifications/{verification_id}")
def update(request, verification_id: str, payload: VerificationUpdateIn):
    tenant_id, actor_id = _context(request)
    item = _run(
        services.update_verification,
        tenant_id=tenant_id,
        actor_id=actor_id,
        verification_id=verification_id,
        **payload.dict(),
    )
    return {"id": str(item.id), "version": item.version, "status": item.status, "checks": item.checks}


@router.post("/verifications/{verification_id}/approve")
def approve(request, verification_id: str, payload: VerificationActionIn):
    tenant_id, actor_id = _context(request)
    item = _run(services.review_verification, tenant_id=tenant_id, actor_id=actor_id, verification_id=verification_id, approve=True, **payload.dict())
    return {"id": str(item.id), "version": item.version, "status": item.status, "approval_count": item.approvals.filter(decision="approved").count(), "required_approvals": item.required_approvals}


@router.post("/verifications/{verification_id}/reject")
def reject(request, verification_id: str, payload: VerificationActionIn):
    tenant_id, actor_id = _context(request)
    item = _run(services.review_verification, tenant_id=tenant_id, actor_id=actor_id, verification_id=verification_id, approve=False, **payload.dict())
    return {"id": str(item.id), "version": item.version, "status": item.status}


@router.post("/verifications/{verification_id}/documents")
def create_document(request, verification_id: str, payload: VerificationDocumentUploadIn):
    tenant_id, actor_id = _context(request)
    document, upload_url, expires = _run(services.create_document_upload, tenant_id=tenant_id, actor_id=actor_id, verification_id=verification_id, **payload.dict())
    return {"id": str(document.id), "version": document.version, "upload_url": upload_url, "headers": {"Content-Type": document.mime_type}, "expires_at": expires}


@router.post("/documents/{document_id}/finalize")
def finalize_document(request, document_id: str, payload: VerificationDocumentFinalizeIn):
    tenant_id, actor_id = _context(request)
    document = _run(services.finalize_document_upload, tenant_id=tenant_id, actor_id=actor_id, document_id=document_id, version=payload.version, idempotency_key=request.headers.get("Idempotency-Key", ""))
    if document.status == "failed":
        raise HttpError(422, "Verification document upload expired or did not match its authorization")
    return {"id": str(document.id), "version": document.version, "status": document.status, "sha256": document.sha256, "byte_size": document.byte_size}


@router.post("/revalidate")
def revalidate(request):
    tenant_id, actor_id = _context(request)
    verifications, records = services.revalidate_expiries(tenant_id=tenant_id, actor_id=actor_id)
    return {"expired_verifications": verifications, "expired_eligibility_records": records}


@router.post("/subject-records")
def assess(request, payload: EligibilityAssessIn):
    tenant_id, actor_id = _context(request)
    item = _run(services.assess_eligibility, tenant_id=tenant_id, actor_id=actor_id, **payload.dict())
    return {"id": str(item.id), "version": item.version, "status": item.status, "risk_reasons": item.risk_reasons}


@router.post("/expertise/{expertise_id}/verify")
def verify_expertise(request, expertise_id: str, payload: NoteIn):
    tenant_id, actor_id = _context(request)
    item = _run(services.verify_expertise, tenant_id=tenant_id, actor_id=actor_id, expertise_id=expertise_id, reason=payload.reason)
    return {"id": str(item.id), "verified": item.verified}


@router.post("/subject-records/{eligibility_id}/block")
def block(request, eligibility_id: str, payload: EligibilityBlockIn):
    tenant_id, actor_id = _context(request)
    item = _run(services.block_eligibility, tenant_id=tenant_id, actor_id=actor_id, eligibility_id=eligibility_id, version=payload.version, reason=payload.reason, changes=payload.dict(exclude={"version", "reason"}))
    return {"id": str(item.id), "version": item.version, "status": item.status}
