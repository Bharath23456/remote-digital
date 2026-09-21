from django.utils import timezone
from django.http import HttpResponse, JsonResponse
from django.db import transaction
from ninja import Router
from ninja.errors import HttpError

from apps.core.authz import membership_for, require_roles, require_step_up
from apps.core.services import record_event
from apps.custody.models import Script
from apps.repository.models import ScriptAsset, UploadIntent
from apps.repository.storage import preview_mask_object, read_object, signed_object_url
from apps.tenancy.models import Institution, Membership

from .models import IdentityLink, IdentityResolutionRequest, MaskingJob
from .schemas import (
    IdentityLinkIn,
    IdentityReceiptIn,
    MaskingDecisionIn,
    MaskRegionIn,
    ResolutionDecisionIn,
    ResolutionRequestIn,
    StartMaskingIn,
)
from .services import (
    add_mask_region,
    apply_masking_job,
    auto_mask_guided_script,
    confirm_identity_storage,
    decide_identity_resolution,
    issue_resolution_authorization,
    prepare_identity_link,
    reject_masking_job,
    return_guided_mask_for_remasking,
    request_identity_resolution,
    review_masking_job,
    start_masking_job,
    verify_masking_job,
)


router = Router(tags=["Candidate anonymisation"])
OPERATIONS_ROLES = (
    Membership.Role.PLATFORM_ADMIN,
    Membership.Role.UNIVERSITY_ADMIN,
    Membership.Role.EXAM_CONTROLLER,
    Membership.Role.RECEIVING_OFFICER,
)
APPROVER_ROLES = (
    Membership.Role.PLATFORM_ADMIN,
    Membership.Role.UNIVERSITY_ADMIN,
    Membership.Role.EXAM_CONTROLLER,
    Membership.Role.AUDITOR,
)


def _job_data(item):
    return {
        "id": str(item.id),
        "script_id": str(item.script_id),
        "script": item.script.script_code,
        "intake_mode": item.script.packet.dispatch.intake_mode,
        "page_count": item.script.page_count,
        "profile": item.profile,
        "status": item.status,
        "detection_confidence": float(item.detection_confidence),
        "created_by_id": item.created_by_id,
        "reviewed_by_id": item.reviewed_by_id,
        "applied_by_id": item.applied_by_id,
        "verified_by_id": item.verified_by_id,
        "failure_reason": item.failure_reason,
        "version": item.version,
        "regions": [
            {
                "id": str(region.id),
                "page_number": region.page_number,
                "category": region.category,
                "x": float(region.x),
                "y": float(region.y),
                "width": float(region.width),
                "height": float(region.height),
                "source": region.source,
                "confidence": float(region.confidence),
            }
            for region in item.regions.filter(is_active=True).order_by("page_number", "category")
        ],
    }


def _require_legacy_masking_job(tenant_id, job_id):
    job = MaskingJob.objects.filter(id=job_id, tenant_id=tenant_id).select_related("script__packet__dispatch").first()
    if not job:
        raise HttpError(404, "Masking job not found")
    if job.script.packet.dispatch.intake_mode != "legacy":
        raise HttpError(409, "Guided scripts use automatic masking and a single return-for-remasking action")
    return job


@router.get("/catalog")
def anonymisation_catalog(request):
    tenant_id = membership_for(request).institution.tenant_id
    links = IdentityLink.objects.filter(tenant_id=tenant_id).select_related("script").order_by("-created_at")[:500]
    jobs = MaskingJob.objects.filter(tenant_id=tenant_id).select_related("script__packet__dispatch").prefetch_related("regions").order_by("-created_at")[:200]
    resolutions = IdentityResolutionRequest.objects.filter(tenant_id=tenant_id).select_related("identity_link__script").prefetch_related("approvals").order_by("-created_at")[:200]
    available_scripts = Script.objects.filter(tenant_id=tenant_id, state__in=[Script.State.REGISTERED, Script.State.SCANNED, Script.State.VALIDATED, Script.State.MASKED, Script.State.STORED]).select_related("packet__dispatch").order_by("script_code")[:500]
    return {
        "links": [{"id": str(item.id), "script_id": str(item.script_id), "script": item.script.script_code, "identity_reference": str(item.identity_reference), "stored": bool(item.stored_at), "stored_at": item.stored_at.isoformat() if item.stored_at else None, "version": item.version} for item in links],
        "jobs": [_job_data(item) for item in jobs],
        "resolutions": [{"id": str(item.id), "script": item.identity_link.script.script_code, "identity_reference": str(item.identity_link.identity_reference), "purpose": item.purpose, "emergency": item.emergency, "status": item.status, "requested_by_id": item.requested_by_id, "expires_at": item.expires_at.isoformat(), "approvals": [{"approver_id": approval.approver_id, "approved": approval.approved, "note": approval.note} for approval in item.approvals.all()], "version": item.version} for item in resolutions],
        "scripts": [{"id": str(item.id), "script_code": item.script_code, "state": item.state, "page_count": item.page_count, "version": item.version, "intake_mode": item.packet.dispatch.intake_mode} for item in available_scripts],
        "institutions": list(Institution.objects.filter(tenant_id=tenant_id, is_active=True, kind__in=[Institution.Kind.UNIVERSITY, Institution.Kind.CAMPUS, Institution.Kind.COLLEGE]).order_by("kind", "name").values("id", "name", "code", "kind", "parent_id")),
        "current_user_id": request.auth.id,
        "current_role": membership_for(request).role,
        "generated_at": timezone.now().isoformat(),
    }


@router.post("/scripts/{script_id}/identity/authorize")
def authorize_identity_storage(request, script_id: str, payload: IdentityLinkIn):
    membership = require_roles(request, *OPERATIONS_ROLES)
    require_step_up(request)
    script = Script.objects.filter(id=script_id, tenant_id=membership.institution.tenant_id).first()
    if not script:
        raise HttpError(404, "Script not found")
    if script.packet.dispatch.intake_mode != "legacy":
        raise HttpError(403, "Guided intake stores identity automatically from the scanned OMR cover")
    tenant_id = membership.institution.tenant_id
    institution = Institution.objects.filter(id=payload.institution_id, tenant_id=tenant_id, is_active=True, kind__in=[Institution.Kind.UNIVERSITY, Institution.Kind.CAMPUS]).first()
    if not institution:
        raise HttpError(422, "Select an active institution in this university")
    college = Institution.objects.filter(id=payload.college_id, tenant_id=tenant_id, is_active=True, kind=Institution.Kind.COLLEGE).first() if payload.college_id else None
    if payload.college_id and not college:
        raise HttpError(422, "Select an active college in this university")
    if college and institution and institution.kind == Institution.Kind.CAMPUS and college.parent_id != institution.id:
        raise HttpError(422, "The selected college does not belong to this campus")
    link, token, expires = prepare_identity_link(tenant_id=tenant_id, actor_id=request.auth.id, script=script, purpose=payload.purpose, session_id=script.paper.session_id, institution_name=institution.name, college_name=college.name if college else None)
    return {"link_id": str(link.id), "identity_reference": str(link.identity_reference), "token": token, "endpoint": "/identity-api/v1/candidates", "expires_at": expires, "version": link.version}


@router.get("/scripts/{script_id}/identity-page")
def identity_page(request, script_id: str):
    membership = require_roles(request, *OPERATIONS_ROLES)
    require_step_up(request)
    tenant_id = membership.institution.tenant_id
    if not Script.objects.filter(id=script_id, tenant_id=tenant_id).exists():
        raise HttpError(404, "Script not found")
    page = UploadIntent.objects.filter(tenant_id=tenant_id, script_id=script_id, kind=UploadIntent.Kind.RAW_SCAN, status=UploadIntent.Status.COMPLETED, page_number=1).order_by("-asset_version").first()
    if not page:
        raise HttpError(404, "The first scanned page is not available")
    url, expires = signed_object_url(method="GET", key=page.storage_key, ttl_seconds=120)
    with transaction.atomic():
        record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="anonymisation.identity_page.access_issued", aggregate="Script", aggregate_id=script_id, payload={"page_number": 1, "asset_version": page.asset_version})
    response = JsonResponse({"url": url, "expires_at": expires, "mime_type": page.content_type})
    response["Cache-Control"] = "private, no-store"
    return response


@router.post("/identity-links/{link_id}/confirm")
def confirm_identity(request, link_id: str, payload: IdentityReceiptIn):
    membership = require_roles(request, *OPERATIONS_ROLES)
    link = confirm_identity_storage(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, link_id=link_id, version=payload.version, receipt=payload.receipt)
    return {"id": str(link.id), "stored": True, "stored_at": link.stored_at.isoformat(), "version": link.version}


@router.post("/scripts/{script_id}/masking-jobs")
def create_masking_job(request, script_id: str, payload: StartMaskingIn):
    membership = require_roles(request, *OPERATIONS_ROLES)
    script = Script.objects.filter(id=script_id, tenant_id=membership.institution.tenant_id).first()
    if not script:
        raise HttpError(404, "Script not found")
    if script.packet.dispatch.intake_mode != "legacy":
        raise HttpError(409, "Guided scripts are masked automatically after upload")
    job = start_masking_job(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, script=script, script_version=payload.script_version, profile=payload.profile)
    return _job_data(MaskingJob.objects.select_related("script").prefetch_related("regions").get(id=job.id))


@router.post("/scripts/{script_id}/auto-mask")
def auto_mask_script(request, script_id: str):
    membership = require_roles(request, *OPERATIONS_ROLES, Membership.Role.SCAN_OPERATOR, Membership.Role.OPERATIONS_SUPERVISOR)
    script = Script.objects.filter(id=script_id, tenant_id=membership.institution.tenant_id).select_related("packet__dispatch").first()
    if not script:
        raise HttpError(404, "Script not found")
    job = auto_mask_guided_script(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, script=script)
    return {"id": str(job.id), "status": job.status, "script_state": Script.State.STORED}


@router.post("/masking-jobs/{job_id}/regions")
def create_region(request, job_id: str, payload: MaskRegionIn):
    membership = require_roles(request, *OPERATIONS_ROLES)
    _require_legacy_masking_job(membership.institution.tenant_id, job_id)
    job, region = add_mask_region(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, job_id=job_id, expected_version=payload.version, page_number=payload.page_number, category=payload.category, x=payload.x, y=payload.y, width=payload.width, height=payload.height)
    return {"id": str(region.id), "job_version": job.version}


@router.get("/masking-jobs/{job_id}/preview/{page_number}")
def masking_preview(request, job_id: str, page_number: int, version: int):
    membership = require_roles(request, *set(OPERATIONS_ROLES + APPROVER_ROLES))
    tenant_id = membership.institution.tenant_id
    job = MaskingJob.objects.select_related("script").filter(id=job_id, tenant_id=tenant_id).first()
    if not job:
        raise HttpError(404, "Masking job not found")
    if version != job.version:
        raise HttpError(409, "Masking job changed; refresh the review")
    if page_number < 1 or page_number > job.script.page_count:
        raise HttpError(404, "Page not found")
    if job.status in (MaskingJob.Status.DETECTED, MaskingJob.Status.REVIEWED):
        require_roles(request, *OPERATIONS_ROLES)
        upload = UploadIntent.objects.filter(tenant_id=tenant_id, script_id=job.script_id, kind=UploadIntent.Kind.RAW_SCAN, status=UploadIntent.Status.COMPLETED, page_number=page_number).order_by("-asset_version").first()
        if not upload:
            raise HttpError(404, "Scanned page not found")
        regions = [{key: float(value) for key, value in region.items()} for region in job.regions.filter(is_active=True, page_number=page_number).values("x", "y", "width", "height")]
        source = "proposed"
        try:
            image = preview_mask_object(source_key=upload.storage_key, regions=regions)
        except OSError as exc:
            raise HttpError(502, "Masked preview is temporarily unavailable") from exc
    elif job.status in (MaskingJob.Status.APPLIED, MaskingJob.Status.VERIFIED):
        asset_version = job.version - 1 if job.status == MaskingJob.Status.APPLIED else job.version - 2
        asset = ScriptAsset.objects.filter(tenant_id=tenant_id, script_id=job.script_id, kind=ScriptAsset.Kind.EVALUATION, page_number=page_number, version=asset_version, deleted_at__isnull=True).first()
        if not asset:
            raise HttpError(404, "Masked page not found")
        source = "generated"
        try:
            image = read_object(asset.storage_key)
        except OSError as exc:
            raise HttpError(502, "Masked page is temporarily unavailable") from exc
    else:
        raise HttpError(409, "This masking job has no reviewable preview")
    if len(image) > 20_000_000:
        raise HttpError(413, "Masked page exceeds the preview size limit")
    with transaction.atomic():
        record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="anonymisation.masking.previewed", aggregate="MaskingJob", aggregate_id=job.id, payload={"page_number": page_number, "source": source})
    response = HttpResponse(image, content_type="image/webp")
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@router.post("/masking-jobs/{job_id}/review")
def review_job(request, job_id: str, payload: MaskingDecisionIn):
    membership = require_roles(request, *OPERATIONS_ROLES)
    _require_legacy_masking_job(membership.institution.tenant_id, job_id)
    job = review_masking_job(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, job_id=job_id, expected_version=payload.version)
    return {"id": str(job.id), "status": job.status, "version": job.version}


@router.post("/masking-jobs/{job_id}/return-for-remasking")
def return_guided_job(request, job_id: str, payload: MaskingDecisionIn):
    membership = require_roles(request, *OPERATIONS_ROLES)
    job = return_guided_mask_for_remasking(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, job_id=job_id, expected_version=payload.version, notes=payload.notes)
    return {"id": str(job.id), "status": job.status, "version": job.version}


@router.post("/masking-jobs/{job_id}/apply")
def apply_job(request, job_id: str, payload: MaskingDecisionIn):
    membership = require_roles(request, *OPERATIONS_ROLES)
    _require_legacy_masking_job(membership.institution.tenant_id, job_id)
    job = apply_masking_job(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, job_id=job_id, expected_version=payload.version)
    return {"id": str(job.id), "status": job.status, "version": job.version}


@router.post("/masking-jobs/{job_id}/verify")
def verify_job(request, job_id: str, payload: MaskingDecisionIn):
    membership = require_roles(request, *APPROVER_ROLES)
    _require_legacy_masking_job(membership.institution.tenant_id, job_id)
    job = verify_masking_job(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, job_id=job_id, expected_version=payload.version, passed=payload.passed, notes=payload.notes)
    return {"id": str(job.id), "status": job.status, "version": job.version}


@router.post("/masking-jobs/{job_id}/reject")
def reject_job(request, job_id: str, payload: MaskingDecisionIn):
    membership = require_roles(request, *set(OPERATIONS_ROLES + APPROVER_ROLES))
    job = _require_legacy_masking_job(membership.institution.tenant_id, job_id)
    require_roles(request, *(APPROVER_ROLES if job.status == MaskingJob.Status.APPLIED else OPERATIONS_ROLES))
    job = reject_masking_job(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, job_id=job_id, expected_version=payload.version, notes=payload.notes)
    return {"id": str(job.id), "status": job.status, "version": job.version}


@router.post("/identity-links/{link_id}/resolution-requests")
def create_resolution_request(request, link_id: str, payload: ResolutionRequestIn):
    membership = require_roles(request, *APPROVER_ROLES)
    require_step_up(request)
    link = IdentityLink.objects.filter(id=link_id, tenant_id=membership.institution.tenant_id).first()
    if not link:
        raise HttpError(404, "Identity link not found")
    item = request_identity_resolution(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, link=link, purpose=payload.purpose, emergency=payload.emergency)
    return {"id": str(item.id), "status": item.status, "expires_at": item.expires_at.isoformat(), "version": item.version}


@router.post("/resolution-requests/{request_id}/decision")
def decide_resolution(request, request_id: str, payload: ResolutionDecisionIn):
    membership = require_roles(request, *APPROVER_ROLES)
    require_step_up(request)
    item = decide_identity_resolution(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, request_id=request_id, expected_version=payload.version, approved=payload.approved, note=payload.note)
    if item.status == IdentityResolutionRequest.Status.EXPIRED:
        raise HttpError(409, "Identity resolution request has expired")
    return {"id": str(item.id), "status": item.status, "approvals": item.approvals.filter(approved=True).count(), "version": item.version}


@router.post("/resolution-requests/{request_id}/authorize")
def authorize_resolution(request, request_id: str):
    membership = require_roles(request, *APPROVER_ROLES)
    require_step_up(request)
    item, token, expires = issue_resolution_authorization(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, request_id=request_id)
    return {"id": str(item.id), "identity_reference": str(item.identity_link.identity_reference), "token": token, "endpoint": f"/identity-api/v1/candidates/{item.identity_link.identity_reference}", "expires_at": expires, "version": item.version}
