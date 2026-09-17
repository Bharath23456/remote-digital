import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from ninja.errors import HttpError

from apps.core.services import record_event
from apps.custody.models import Script
from apps.custody.services import return_script_for_remasking, transition_script
from apps.repository.models import ScriptAsset, UploadIntent
from apps.repository.storage import mask_object
from apps.security.models import EmergencyAccessGrant, SecurityAlert

from .models import (
    IdentityLink,
    IdentityResolutionApproval,
    IdentityResolutionRequest,
    MaskRegion,
    MaskVerification,
    MaskingJob,
)
from .tokens import issue_identity_token


def _decode_receipt(value):
    try:
        encoded, supplied = value.split(".", 1)
        expected = hmac.new(
            settings.IDENTITY_AUTHORIZATION_KEY.encode(),
            encoded.encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, supplied):
            raise ValueError
        raw = base64.urlsafe_b64decode(encoded + "=" * ((4 - len(encoded) % 4) % 4))
        claims = json.loads(raw)
        if claims.get("action") != "identity.stored" or int(claims.get("exp", 0)) < int(time.time()):
            raise ValueError
        return claims
    except (ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HttpError(401, "Identity storage receipt is invalid or expired") from exc


def prepare_identity_link(*, tenant_id, actor_id, script, purpose, session_id="", institution_name=None, college_name=None):
    with transaction.atomic():
        link, created = IdentityLink.objects.select_for_update().get_or_create(
            tenant_id=tenant_id,
            script=script,
            defaults={"identity_reference": uuid.uuid4(), "linked_by_id": actor_id},
        )
        if link.stored_at:
            raise HttpError(409, "Candidate identity has already been stored for this script")
        token, expires = issue_identity_token(
            action="identity.create",
            tenant_id=tenant_id,
            identity_reference=link.identity_reference,
            script_id=script.id,
            actor_id=actor_id,
            purpose=purpose.strip() or "Candidate identity registration",
            session_id=session_id,
            institution_name=institution_name,
            college_name=college_name,
        )
        record_event(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="anonymisation.identity.authorization_issued",
            aggregate="IdentityLink",
            aggregate_id=link.id,
            payload={"script_id": str(script.id), "created": created, "expires_at": expires},
        )
    return link, token, expires


def confirm_identity_storage(*, tenant_id, actor_id, link_id, version, receipt):
    claims = _decode_receipt(receipt)
    with transaction.atomic():
        link = IdentityLink.objects.select_for_update().filter(id=link_id, tenant_id=tenant_id).first()
        if not link:
            raise HttpError(404, "Identity link not found")
        if link.version != version:
            raise HttpError(409, "Identity link was changed by another user")
        if claims.get("tenant_id") != str(tenant_id) or claims.get("identity_reference") != str(link.identity_reference) or claims.get("script_id") != str(link.script_id):
            raise HttpError(401, "Identity storage receipt does not match this script")
        link.stored_at = link.stored_at or timezone.now()
        link.version += 1
        link.save(update_fields=["stored_at", "version", "updated_at"])
        record_event(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="anonymisation.identity.stored",
            aggregate="IdentityLink",
            aggregate_id=link.id,
            payload={"script_id": str(link.script_id)},
        )
    return link


def start_masking_job(*, tenant_id, actor_id, script, script_version, profile):
    if profile != "identity-cover-v1":
        raise HttpError(422, "Only the first-page identity cover profile is supported")
    if script.state != Script.State.SCANNED or script.version != script_version:
        raise HttpError(409, "Only the current scanned script version can enter masking")
    uploaded_pages = set(
        UploadIntent.objects.filter(
            tenant_id=tenant_id,
            script=script,
            kind=UploadIntent.Kind.RAW_SCAN,
            status=UploadIntent.Status.COMPLETED,
        ).values_list("page_number", flat=True)
    )
    if uploaded_pages != set(range(1, script.page_count + 1)):
        raise HttpError(409, "Every declared raw scan page must be uploaded before masking")
    with transaction.atomic():
        current = Script.objects.select_for_update().get(id=script.id, tenant_id=tenant_id)
        if current.version != script_version or current.state != Script.State.SCANNED:
            raise HttpError(409, "Script changed before masking could begin")
        next_version = (MaskingJob.objects.filter(script=current).order_by("-version").values_list("version", flat=True).first() or 0) + 1
        job = MaskingJob.objects.create(
            tenant_id=tenant_id,
            script=current,
            profile=profile[:80],
            detection_confidence=Decimal("0.9800"),
            created_by_id=actor_id,
            version=next_version,
        )
        MaskRegion.objects.create(tenant_id=tenant_id, job=job, page_number=1, category=MaskRegion.Category.IDENTITY_PAGE, x=Decimal("0"), y=Decimal("0"), width=Decimal("1"), height=Decimal("1"), source=MaskRegion.Source.AUTOMATIC, confidence=Decimal("1"))
        transition_script(tenant_id=tenant_id, actor_id=actor_id, script_id=current.id, expected_version=current.version, to_state=Script.State.VALIDATED, location="Anonymisation review", metadata={"masking_job_id": str(job.id), "profile": job.profile})
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="anonymisation.masking.detected", aggregate="MaskingJob", aggregate_id=job.id, payload={"script_id": str(script.id), "regions": 1, "identity_cover_page": 1})
    return job


def add_mask_region(*, tenant_id, actor_id, job_id, expected_version, page_number, category, x, y, width, height):
    if category not in MaskRegion.Category.values:
        raise HttpError(422, "Unsupported identity region category")
    values = [Decimal(str(item)) for item in (x, y, width, height)]
    if any(item < 0 or item > 1 for item in values) or values[2] <= 0 or values[3] <= 0 or values[0] + values[2] > 1 or values[1] + values[3] > 1:
        raise HttpError(422, "Mask coordinates must describe a positive normalized region within the page")
    with transaction.atomic():
        job = MaskingJob.objects.select_for_update().select_related("script").filter(id=job_id, tenant_id=tenant_id).first()
        if not job:
            raise HttpError(404, "Masking job not found")
        if job.status != MaskingJob.Status.DETECTED or job.version != expected_version:
            raise HttpError(409, "Only the current detected job can be edited")
        if page_number < 1 or page_number > job.script.page_count:
            raise HttpError(422, "Mask page is outside the scanned page range")
        region = MaskRegion.objects.create(tenant_id=tenant_id, job=job, page_number=page_number, category=category, x=values[0], y=values[1], width=values[2], height=values[3], source=MaskRegion.Source.MANUAL)
        job.version += 1
        job.save(update_fields=["version", "updated_at"])
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="anonymisation.region.added", aggregate="MaskingJob", aggregate_id=job.id, payload={"region_id": str(region.id), "category": category, "page_number": page_number})
    return job, region


def review_masking_job(*, tenant_id, actor_id, job_id, expected_version):
    with transaction.atomic():
        job = MaskingJob.objects.select_for_update().filter(id=job_id, tenant_id=tenant_id).first()
        if not job:
            raise HttpError(404, "Masking job not found")
        if job.status != MaskingJob.Status.DETECTED or job.version != expected_version:
            raise HttpError(409, "Only the current detected job can be reviewed")
        if job.created_by_id == actor_id:
            raise HttpError(409, "The masking operator cannot approve their own detection")
        if not job.regions.filter(is_active=True).exists():
            raise HttpError(409, "At least one active mask region is required")
        if not job.regions.filter(is_active=True, page_number=1, category=MaskRegion.Category.IDENTITY_PAGE, x=0, y=0, width=1, height=1).exists():
            raise HttpError(409, "The first identity page must be fully masked")
        job.status = MaskingJob.Status.REVIEWED
        job.reviewed_by_id = actor_id
        job.reviewed_at = timezone.now()
        job.version += 1
        job.save()
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="anonymisation.masking.reviewed", aggregate="MaskingJob", aggregate_id=job.id, payload={"script_id": str(job.script_id)})
    return job


def apply_masking_job(*, tenant_id, actor_id, job_id, expected_version):
    with transaction.atomic():
        job = MaskingJob.objects.select_for_update().select_related("script").filter(id=job_id, tenant_id=tenant_id).first()
        if not job:
            raise HttpError(404, "Masking job not found")
        if job.status != MaskingJob.Status.REVIEWED or job.version != expected_version:
            raise HttpError(409, "Only the current reviewed job can be applied")
        if actor_id in (job.created_by_id, job.reviewed_by_id):
            raise HttpError(409, "Mask application requires a third independent operator")
        if not job.regions.filter(is_active=True, page_number=1, category=MaskRegion.Category.IDENTITY_PAGE, x=0, y=0, width=1, height=1).exists():
            raise HttpError(409, "The first identity page must be fully masked")
        job.status = MaskingJob.Status.PROCESSING
        job.applied_by_id = actor_id
        job.version += 1
        job.save(update_fields=["status", "applied_by_id", "version", "updated_at"])
        processing_version = job.version
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="anonymisation.masking.processing", aggregate="MaskingJob", aggregate_id=job.id, payload={"script_id": str(job.script_id)})
    try:
        objects = []
        for page in range(1, job.script.page_count + 1):
            upload = UploadIntent.objects.filter(tenant_id=tenant_id, script=job.script, kind=UploadIntent.Kind.RAW_SCAN, status=UploadIntent.Status.COMPLETED, page_number=page).order_by("-asset_version").first()
            if not upload:
                raise OSError(f"Raw page {page} is missing")
            base = f"{tenant_id}/{job.script_id}/page-{page}-v{job.version}"
            destinations = [
                {"key": f"scripts-master/{base}.webp", "mime_type": "image/webp"},
                {"key": f"scripts-evaluation/{base}.webp", "mime_type": "image/webp"},
                {"key": f"scripts-thumbnails/{base}.webp", "mime_type": "image/webp", "thumbnail": True},
            ]
            regions = list(job.regions.filter(page_number=page, is_active=True).values("x", "y", "width", "height"))
            response = mask_object(source_key=upload.storage_key, destinations=destinations, regions=[{key: float(value) for key, value in region.items()} for region in regions])
            objects.extend(response["objects"])
        with transaction.atomic():
            current = MaskingJob.objects.select_for_update().select_related("script").get(id=job.id, tenant_id=tenant_id)
            if current.status != MaskingJob.Status.PROCESSING or current.version != processing_version:
                raise HttpError(409, "Masking job changed while assets were being generated")
            retention = timezone.localdate() + timedelta(days=365 * 7)
            for item in objects:
                prefix = item["key"].split("/", 1)[0]
                kind = {"scripts-master": ScriptAsset.Kind.MASTER, "scripts-evaluation": ScriptAsset.Kind.EVALUATION, "scripts-thumbnails": ScriptAsset.Kind.THUMBNAIL}[prefix]
                page = int(item["key"].split("/page-")[1].split("-")[0].split(".")[0])
                ScriptAsset.objects.get_or_create(
                    storage_key=item["key"],
                    defaults={"tenant_id": tenant_id, "script": current.script, "kind": kind, "page_number": page, "sha256": item["sha256"], "byte_size": item["byte_size"], "mime_type": item["mime_type"], "version": current.version, "retention_until": retention, "object_lock_until": retention if kind == ScriptAsset.Kind.MASTER else None, "backup_status": "queued", "replication_status": "queued"},
                )
            current.status = MaskingJob.Status.APPLIED
            current.applied_at = timezone.now()
            current.version += 1
            current.save()
            transition_script(tenant_id=tenant_id, actor_id=actor_id, script_id=current.script_id, expected_version=current.script.version, to_state=Script.State.MASKED, location="Encrypted repository staging", metadata={"masking_job_id": str(current.id), "assets": len(objects)})
            record_event(tenant_id=tenant_id, actor_id=actor_id, action="anonymisation.masking.applied", aggregate="MaskingJob", aggregate_id=current.id, payload={"script_id": str(current.script_id), "assets": len(objects)})
        return current
    except HttpError:
        raise
    except Exception as exc:
        with transaction.atomic():
            current = MaskingJob.objects.select_for_update().filter(id=job.id, tenant_id=tenant_id).first()
            if current and current.status == MaskingJob.Status.PROCESSING:
                current.status = MaskingJob.Status.FAILED
                current.failure_reason = str(exc)[:1000]
                current.version += 1
                current.save()
                return_script_for_remasking(tenant_id=tenant_id, actor_id=actor_id, script_id=current.script_id, expected_version=current.script.version, job_id=current.id, reason=current.failure_reason)
                record_event(tenant_id=tenant_id, actor_id=actor_id, action="anonymisation.masking.failed", aggregate="MaskingJob", aggregate_id=current.id, payload={"reason": current.failure_reason})
        raise HttpError(502, "Storage transformation failed; the masking job was stopped") from exc


def reject_masking_job(*, tenant_id, actor_id, job_id, expected_version, notes):
    reason = notes.strip()
    if len(reason) < 10:
        raise HttpError(422, "Give a specific reason for remasking (at least 10 characters)")
    with transaction.atomic():
        job = MaskingJob.objects.select_for_update().select_related("script").filter(id=job_id, tenant_id=tenant_id).first()
        if not job:
            raise HttpError(404, "Masking job not found")
        if job.version != expected_version or job.status not in (MaskingJob.Status.DETECTED, MaskingJob.Status.REVIEWED, MaskingJob.Status.APPLIED):
            raise HttpError(409, "Masking job is no longer awaiting a decision")
        excluded = (job.created_by_id,) if job.status == MaskingJob.Status.DETECTED else (job.created_by_id, job.reviewed_by_id) if job.status == MaskingJob.Status.REVIEWED else (job.created_by_id, job.reviewed_by_id, job.applied_by_id)
        if actor_id in excluded:
            raise HttpError(409, "An independent operator must reject this masking stage")
        stage = job.status
        if stage == MaskingJob.Status.APPLIED:
            MaskVerification.objects.create(tenant_id=tenant_id, job=job, verifier_id=actor_id, passed=False, notes=reason)
            ScriptAsset.objects.filter(tenant_id=tenant_id, script_id=job.script_id, version=job.version - 1, deleted_at__isnull=True).update(deleted_at=timezone.now(), deletion_reason=f"Rejected masking job {job.id}: {reason[:500]}")
        job.status = MaskingJob.Status.FAILED
        job.failure_reason = reason[:1000]
        job.version += 1
        job.save(update_fields=["status", "failure_reason", "version", "updated_at"])
        return_script_for_remasking(tenant_id=tenant_id, actor_id=actor_id, script_id=job.script_id, expected_version=job.script.version, job_id=job.id, reason=reason)
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="anonymisation.masking.rejected", aggregate="MaskingJob", aggregate_id=job.id, payload={"script_id": str(job.script_id), "stage": stage, "notes": reason})
    return job


def verify_masking_job(*, tenant_id, actor_id, job_id, expected_version, passed, notes):
    if not passed:
        return reject_masking_job(tenant_id=tenant_id, actor_id=actor_id, job_id=job_id, expected_version=expected_version, notes=notes)
    with transaction.atomic():
        job = MaskingJob.objects.select_for_update().select_related("script").filter(id=job_id, tenant_id=tenant_id).first()
        if not job:
            raise HttpError(404, "Masking job not found")
        if job.status != MaskingJob.Status.APPLIED or job.version != expected_version:
            raise HttpError(409, "Only the current applied job can be verified")
        if actor_id in (job.created_by_id, job.reviewed_by_id, job.applied_by_id):
            raise HttpError(409, "Final mask verification requires an independent operator")
        MaskVerification.objects.create(tenant_id=tenant_id, job=job, verifier_id=actor_id, passed=True, notes=notes)
        job.status = MaskingJob.Status.VERIFIED
        job.verified_by_id = actor_id
        job.verified_at = timezone.now()
        job.version += 1
        job.save()
        transition_script(tenant_id=tenant_id, actor_id=actor_id, script_id=job.script_id, expected_version=job.script.version, to_state=Script.State.STORED, location="Encrypted digital repository", metadata={"masking_job_id": str(job.id), "verified": True})
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="anonymisation.masking.verified", aggregate="MaskingJob", aggregate_id=job.id, payload={"script_id": str(job.script_id), "notes": notes})
    return job


def request_identity_resolution(*, tenant_id, actor_id, link, purpose, emergency):
    if not link.stored_at:
        raise HttpError(409, "Candidate identity has not been confirmed in the identity service")
    if len(purpose.strip()) < 12:
        raise HttpError(422, "A specific identity resolution purpose is required")
    with transaction.atomic():
        item = IdentityResolutionRequest.objects.create(tenant_id=tenant_id, identity_link=link, requested_by_id=actor_id, purpose=purpose.strip()[:160], emergency=emergency, expires_at=timezone.now() + timedelta(minutes=30))
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="anonymisation.identity_resolution.requested", aggregate="IdentityResolutionRequest", aggregate_id=item.id, payload={"script_id": str(link.script_id), "emergency": emergency, "expires_at": item.expires_at.isoformat()})
    return item


def decide_identity_resolution(*, tenant_id, actor_id, request_id, expected_version, approved, note):
    with transaction.atomic():
        item = IdentityResolutionRequest.objects.select_for_update().filter(id=request_id, tenant_id=tenant_id).first()
        if not item:
            raise HttpError(404, "Identity resolution request not found")
        if item.version != expected_version or item.status != IdentityResolutionRequest.Status.PENDING:
            raise HttpError(409, "Identity resolution request is no longer pending or is stale")
        if item.expires_at <= timezone.now():
            item.status = IdentityResolutionRequest.Status.EXPIRED
            item.version += 1
            item.save()
            record_event(tenant_id=tenant_id, actor_id=actor_id, action="anonymisation.identity_resolution.expired", aggregate="IdentityResolutionRequest", aggregate_id=item.id, payload={"expired_at": item.expires_at.isoformat()})
        elif item.requested_by_id == actor_id:
            raise HttpError(409, "The requester cannot approve identity resolution")
        else:
            try:
                IdentityResolutionApproval.objects.create(tenant_id=tenant_id, request=item, approver_id=actor_id, approved=approved, note=note)
            except IntegrityError as exc:
                raise HttpError(409, "This approver has already decided this request") from exc
            if not approved:
                item.status = IdentityResolutionRequest.Status.REJECTED
            elif item.approvals.filter(approved=True).values("approver_id").distinct().count() >= 2:
                item.status = IdentityResolutionRequest.Status.APPROVED
            item.version += 1
            item.save()
            record_event(tenant_id=tenant_id, actor_id=actor_id, action="anonymisation.identity_resolution.decided", aggregate="IdentityResolutionRequest", aggregate_id=item.id, payload={"approved": approved, "status": item.status, "approvals": item.approvals.filter(approved=True).count()})
    return item


def issue_resolution_authorization(*, tenant_id, actor_id, request_id):
    with transaction.atomic():
        item = IdentityResolutionRequest.objects.select_for_update().select_related("identity_link").filter(id=request_id, tenant_id=tenant_id).first()
        if not item:
            raise HttpError(404, "Identity resolution request not found")
        if item.status != IdentityResolutionRequest.Status.APPROVED or item.expires_at <= timezone.now():
            raise HttpError(409, "Two current approvals are required before identity resolution")
        if item.requested_by_id != actor_id:
            raise HttpError(403, "Only the original requester can consume this authorization")
        if item.emergency and not EmergencyAccessGrant.objects.filter(tenant_id=tenant_id, user_id=actor_id, expires_at__gt=timezone.now(), revoked_at__isnull=True).exists():
            raise HttpError(403, "An active emergency access grant is required")
        token, expires = issue_identity_token(action="identity.resolve", tenant_id=tenant_id, identity_reference=item.identity_link.identity_reference, script_id=item.identity_link.script_id, actor_id=actor_id, purpose=item.purpose, request_id=item.id, ttl_seconds=300)
        item.status = IdentityResolutionRequest.Status.CONSUMED
        item.consumed_at = timezone.now()
        item.version += 1
        item.save()
        if item.emergency:
            SecurityAlert.objects.create(tenant_id=tenant_id, category="emergency_identity_resolution", severity=SecurityAlert.Severity.CRITICAL, title="Emergency candidate identity resolution", details={"request_id": str(item.id), "actor_id": actor_id})
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="anonymisation.identity_resolution.authorized", aggregate="IdentityResolutionRequest", aggregate_id=item.id, payload={"emergency": item.emergency, "expires_at": expires})
    return item, token, expires
