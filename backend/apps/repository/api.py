from datetime import date

from django.db import transaction
from django.db.models import Count, Max, Q
from ninja import Router
from ninja.errors import HttpError

from apps.allocation.models import Assignment
from apps.core.authz import membership_for, require_roles, require_step_up
from apps.core.services import record_event
from apps.custody.models import Script
from apps.custody.services import transition_script
from apps.evaluators.models import Evaluator
from apps.tenancy.models import Membership

from .models import ScriptAsset, UploadIntent
from .schemas import CompleteScanIn, DeleteAssetIn, FinalizeUploadIn, LegalHoldIn, ManualScanUploadIn, UploadIntentIn
from .services import archive_asset as archive_repository_asset
from .services import create_upload_intent, finalize_upload, secure_delete_asset, verify_asset_integrity
from .storage import signed_object_url


router = Router(tags=["Digital script repository"])
INGEST_ROLES = (
    Membership.Role.PLATFORM_ADMIN,
    Membership.Role.UNIVERSITY_ADMIN,
    Membership.Role.EXAM_CONTROLLER,
    Membership.Role.RECEIVING_OFFICER,
    Membership.Role.SCANNER_OPERATOR,
)


def _asset_data(item):
    return {"id": str(item.id), "script_id": str(item.script_id), "script": item.script.script_code, "kind": item.kind, "page_number": item.page_number, "sha256": item.sha256, "byte_size": item.byte_size, "mime_type": item.mime_type, "version": item.version, "retention_until": item.retention_until.isoformat() if item.retention_until else None, "legal_hold": item.legal_hold, "integrity_checked_at": item.integrity_checked_at.isoformat() if item.integrity_checked_at else None, "archived_at": item.archived_at.isoformat() if item.archived_at else None, "backup_status": item.backup_status, "replication_status": item.replication_status}


@router.get("/catalog")
def repository_catalog(request, q: str = "", script_id: str | None = None, asset_page: int = 1, upload_page: int = 1, page_size: int = 25):
    if asset_page < 1 or upload_page < 1 or not 1 <= page_size <= 100:
        raise HttpError(422, "Pages must be positive and page size must be between 1 and 100")
    membership = membership_for(request)
    tenant_id = membership.institution.tenant_id
    assets = ScriptAsset.objects.filter(tenant_id=tenant_id, deleted_at__isnull=True).select_related("script")
    intents = UploadIntent.objects.filter(tenant_id=tenant_id).select_related("script")
    if membership.role == Membership.Role.EVALUATOR:
        evaluator = Evaluator.objects.filter(tenant_id=tenant_id, email__iexact=request.auth.email, status=Evaluator.Status.ACTIVE).first()
        assigned_scripts = Assignment.objects.filter(tenant_id=tenant_id, evaluator=evaluator).values_list("script_id", flat=True) if evaluator else []
        assets = assets.filter(script_id__in=assigned_scripts, kind__in=[ScriptAsset.Kind.EVALUATION, ScriptAsset.Kind.THUMBNAIL])
        intents = intents.none()
    if q:
        assets = assets.filter(Q(script__script_code__icontains=q) | Q(sha256__icontains=q))
        intents = intents.filter(script__script_code__icontains=q)
    if script_id:
        assets = assets.filter(script_id=script_id)
        intents = intents.filter(script_id=script_id)
    summary = assets.aggregate(total=Count("id"), holds=Count("id", filter=Q(legal_hold=True)), verified=Count("id", filter=Q(integrity_checked_at__isnull=False)))
    upload_total = intents.count()
    asset_start = (asset_page - 1) * page_size
    upload_start = (upload_page - 1) * page_size
    assets = assets.order_by("script__script_code", "page_number", "kind", "version", "id")[asset_start:asset_start + page_size]
    intents = intents.order_by("-created_at", "-id")[upload_start:upload_start + page_size]
    return {"assets": [_asset_data(item) for item in assets], "uploads": [{"id": str(item.id), "script": item.script.script_code, "kind": item.kind, "page_number": item.page_number, "asset_version": item.asset_version, "status": item.status, "expires_at": item.expires_at.isoformat(), "sha256": item.sha256, "byte_size": item.byte_size, "version": item.version} for item in intents], "asset_pagination": {"page": asset_page, "page_size": page_size, "total": summary["total"]}, "upload_pagination": {"page": upload_page, "page_size": page_size, "total": upload_total}, "summary": summary}


@router.post("/uploads")
def issue_upload(request, payload: UploadIntentIn):
    membership = require_roles(request, *INGEST_ROLES)
    if payload.kind != UploadIntent.Kind.RAW_SCAN:
        raise HttpError(403, "Master, evaluation and thumbnail copies are generated only by approved masking")
    script = Script.objects.filter(id=payload.script_id, tenant_id=membership.institution.tenant_id).first()
    if not script:
        raise HttpError(404, "Script not found")
    intent, url, expires = create_upload_intent(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, script=script, kind=payload.kind, page_number=payload.page_number, asset_version=payload.asset_version, content_type=payload.content_type, maximum_bytes=payload.maximum_bytes)
    return {"id": str(intent.id), "upload_url": url, "expires_at": expires, "headers": {"Content-Type": intent.content_type}, "version": intent.version}


@router.post("/manual-scan/uploads")
@transaction.atomic
def issue_manual_scan_upload(request, payload: ManualScanUploadIn):
    membership = require_roles(request, *INGEST_ROLES)
    tenant_id = membership.institution.tenant_id
    script = Script.objects.select_for_update().filter(id=payload.script_id, tenant_id=tenant_id).first()
    if not script:
        raise HttpError(404, "Script not found")
    latest_version = UploadIntent.objects.filter(
        tenant_id=tenant_id,
        script=script,
        kind=UploadIntent.Kind.RAW_SCAN,
        page_number=payload.page_number,
    ).aggregate(value=Max("asset_version"))["value"] or 0
    asset_version = latest_version + 1
    if asset_version > 100:
        raise HttpError(409, "This page has reached the manual upload version limit")
    intent, url, expires = create_upload_intent(
        tenant_id=tenant_id,
        actor_id=request.auth.id,
        script=script,
        kind=UploadIntent.Kind.RAW_SCAN,
        page_number=payload.page_number,
        asset_version=asset_version,
        content_type=payload.content_type,
        maximum_bytes=payload.maximum_bytes,
    )
    return {
        "id": str(intent.id),
        "upload_url": url,
        "expires_at": expires,
        "headers": {"Content-Type": intent.content_type},
        "version": intent.version,
        "asset_version": intent.asset_version,
    }


@router.get("/manual-scan/catalog")
def manual_scan_catalog(request, page: int = 1, page_size: int = 20, q: str = "", state: str = "", date_from: date | None = None, date_to: date | None = None):
    membership = require_roles(request, *INGEST_ROLES)
    if page < 1 or not 1 <= page_size <= 100:
        raise HttpError(422, "Page must be positive and page size must be between 1 and 100")
    uploads = UploadIntent.objects.filter(tenant_id=membership.institution.tenant_id, kind=UploadIntent.Kind.RAW_SCAN, status=UploadIntent.Status.COMPLETED)
    if q.strip():
        uploads = uploads.filter(Q(script__script_code__icontains=q.strip()) | Q(script__primary_barcode__icontains=q.strip()) | Q(script__paper__code__icontains=q.strip()))
    if state:
        uploads = uploads.filter(script__state=state)
    if date_from:
        uploads = uploads.filter(updated_at__date__gte=date_from)
    if date_to:
        uploads = uploads.filter(updated_at__date__lte=date_to)
    grouped = uploads.values("script_id", "script__script_code", "script__paper__code", "script__state", "script__page_count").annotate(page_count=Count("page_number", distinct=True), uploaded_at=Max("updated_at")).order_by("-uploaded_at", "-script_id")
    total = grouped.count()
    records = list(grouped[(page - 1) * page_size:page * page_size])
    page_numbers = {}
    for script_id, number in uploads.filter(script_id__in=[item["script_id"] for item in records]).values_list("script_id", "page_number").distinct():
        page_numbers.setdefault(script_id, []).append(number)
    return {
        "items": [{"script_id": str(item["script_id"]), "script": item["script__script_code"], "paper": item["script__paper__code"], "state": item["script__state"], "page_count": item["page_count"], "declared_pages": item["script__page_count"], "pages": sorted(page_numbers.get(item["script_id"], [])), "uploaded_at": item["uploaded_at"].isoformat()} for item in records],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


@router.post("/uploads/{intent_id}/finalize")
def complete_upload(request, intent_id: str, payload: FinalizeUploadIn):
    membership = require_roles(request, *INGEST_ROLES)
    if UploadIntent.objects.filter(id=intent_id, tenant_id=membership.institution.tenant_id).exclude(kind=UploadIntent.Kind.RAW_SCAN).exists():
        raise HttpError(403, "Derived copies cannot be finalized through a manual upload")
    intent, asset = finalize_upload(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, intent_id=intent_id, expected_version=payload.version, idempotency_key=request.headers.get("Idempotency-Key", ""))
    return {"id": str(intent.id), "status": intent.status, "version": intent.version, "asset_id": str(asset.id) if asset else None, "sha256": intent.sha256, "byte_size": intent.byte_size}


@router.post("/scripts/{script_id}/complete-scan")
def complete_scan(request, script_id: str, payload: CompleteScanIn):
    membership = require_roles(request, *INGEST_ROLES)
    tenant_id = membership.institution.tenant_id
    script = Script.objects.filter(id=script_id, tenant_id=tenant_id).first()
    if not script:
        raise HttpError(404, "Script not found")
    pages = set(UploadIntent.objects.filter(tenant_id=tenant_id, script=script, kind=UploadIntent.Kind.RAW_SCAN, status=UploadIntent.Status.COMPLETED).values_list("page_number", flat=True))
    if pages != set(range(1, payload.page_count + 1)):
        raise HttpError(409, "Every page from 1 through the declared page count must be uploaded")
    with transaction.atomic():
        script = transition_script(tenant_id=tenant_id, actor_id=request.auth.id, script_id=script.id, expected_version=payload.version, to_state=Script.State.SCANNED, location=payload.location, metadata={"page_count": payload.page_count})
        script.page_count = payload.page_count
        script.save(update_fields=["page_count", "updated_at"])
    return {"id": str(script.id), "state": script.state, "page_count": script.page_count, "version": script.version}


def _can_read_asset(request, membership, asset):
    if membership.role in (Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER, Membership.Role.AUDITOR):
        return True
    if membership.role == Membership.Role.EVALUATOR:
        if asset.kind not in (ScriptAsset.Kind.EVALUATION, ScriptAsset.Kind.THUMBNAIL):
            return False
        evaluator = Evaluator.objects.filter(tenant_id=membership.institution.tenant_id, email__iexact=request.auth.email, status=Evaluator.Status.ACTIVE).first()
        return bool(evaluator and Assignment.objects.filter(tenant_id=membership.institution.tenant_id, evaluator=evaluator, script=asset.script).exists())
    return False


@router.get("/assets/{asset_id}/url")
def asset_url(request, asset_id: str):
    membership = membership_for(request)
    asset = ScriptAsset.objects.filter(id=asset_id, tenant_id=membership.institution.tenant_id, deleted_at__isnull=True).select_related("script").first()
    if not asset:
        raise HttpError(404, "Repository asset not found")
    if not _can_read_asset(request, membership, asset):
        raise HttpError(403, "This assignment cannot access the requested page")
    url, expires = signed_object_url(method="GET", key=asset.storage_key, ttl_seconds=300)
    return {"url": url, "expires_at": expires, "ttl_seconds": 300, "sha256": asset.sha256}


@router.post("/assets/{asset_id}/verify")
def verify_integrity(request, asset_id: str):
    membership = require_roles(request, Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER, Membership.Role.AUDITOR)
    asset, valid = verify_asset_integrity(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, asset_id=asset_id)
    return {"id": str(asset.id), "valid": valid, "checked_at": asset.integrity_checked_at.isoformat()}


@router.post("/assets/{asset_id}/legal-hold")
def set_legal_hold(request, asset_id: str, payload: LegalHoldIn):
    membership = require_roles(request, Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN, Membership.Role.AUDITOR)
    require_step_up(request)
    with transaction.atomic():
        asset = ScriptAsset.objects.select_for_update().filter(id=asset_id, tenant_id=membership.institution.tenant_id, deleted_at__isnull=True).first()
        if not asset:
            raise HttpError(404, "Repository asset not found")
        asset.legal_hold = payload.enabled
        if payload.retention_until:
            asset.retention_until = payload.retention_until
        asset.save(update_fields=["legal_hold", "retention_until", "updated_at"])
        record_event(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, action="repository.legal_hold.changed", aggregate="ScriptAsset", aggregate_id=asset.id, payload={"enabled": asset.legal_hold, "retention_until": asset.retention_until.isoformat() if asset.retention_until else None})
    return {"id": str(asset.id), "legal_hold": asset.legal_hold}


@router.post("/assets/{asset_id}/archive")
def archive_asset(request, asset_id: str):
    membership = require_roles(request, Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER)
    asset = archive_repository_asset(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, asset_id=asset_id)
    return {"id": str(asset.id), "archived_at": asset.archived_at.isoformat()}


@router.post("/assets/{asset_id}/secure-delete")
def delete_asset(request, asset_id: str, payload: DeleteAssetIn):
    membership = require_roles(request, Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN)
    require_step_up(request)
    asset = secure_delete_asset(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, asset_id=asset_id, reason=payload.reason)
    return {"id": str(asset.id), "deleted_at": asset.deleted_at.isoformat()}
