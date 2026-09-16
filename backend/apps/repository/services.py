import secrets
from datetime import timedelta
from pathlib import PurePosixPath

from django.db import IntegrityError, transaction
from django.utils import timezone
from ninja.errors import HttpError

from apps.core.services import record_event
from apps.core.idempotency import begin_idempotent, complete_idempotent
from apps.custody.models import Script
from apps.security.models import SecurityAlert

from .models import ScriptAsset, UploadIntent
from .storage import delete_object, read_object_metadata, signed_object_url


MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}
PREFIXES = {
    UploadIntent.Kind.RAW_SCAN: "scans-raw",
    UploadIntent.Kind.MASTER: "scripts-master",
    UploadIntent.Kind.EVALUATION: "scripts-evaluation",
    UploadIntent.Kind.THUMBNAIL: "scripts-thumbnails",
}
ASSET_KINDS = {
    UploadIntent.Kind.MASTER: ScriptAsset.Kind.MASTER,
    UploadIntent.Kind.EVALUATION: ScriptAsset.Kind.EVALUATION,
    UploadIntent.Kind.THUMBNAIL: ScriptAsset.Kind.THUMBNAIL,
}


def create_upload_intent(*, tenant_id, actor_id, script, kind, page_number, asset_version, content_type, maximum_bytes):
    if kind not in UploadIntent.Kind.values or content_type not in MIME_EXTENSIONS:
        raise HttpError(422, "Unsupported upload kind or content type")
    if not 1 <= page_number <= 500 or not 1 <= asset_version <= 100:
        raise HttpError(422, "Page and asset version are outside the supported range")
    if not 1 <= maximum_bytes <= 50_000_000:
        raise HttpError(422, "Page upload limit must be between 1 byte and 50 MB")
    if kind == UploadIntent.Kind.RAW_SCAN and script.state not in (Script.State.REGISTERED, Script.State.SCANNED):
        raise HttpError(409, "Raw pages can only be attached during scanning")
    if kind != UploadIntent.Kind.RAW_SCAN and script.state not in (
        Script.State.MASKED,
        Script.State.STORED,
        Script.State.ASSIGNED,
        Script.State.OPENED,
        Script.State.EVALUATING,
        Script.State.SUBMITTED,
        Script.State.REVIEW,
        Script.State.MODERATED,
        Script.State.REVALUATED,
        Script.State.FINALIZED,
        Script.State.ARCHIVED,
    ):
        raise HttpError(409, "Repository assets require an anonymized script")
    if kind == UploadIntent.Kind.MASTER and (asset_version != 1 or ScriptAsset.objects.filter(script=script, kind=ScriptAsset.Kind.MASTER, page_number=page_number).exists()):
        raise HttpError(409, "The immutable master page is written once")
    now = timezone.now()
    UploadIntent.objects.filter(
        tenant_id=tenant_id,
        script=script,
        kind=kind,
        page_number=page_number,
        asset_version=asset_version,
        status=UploadIntent.Status.ISSUED,
        expires_at__lte=now,
    ).update(status=UploadIntent.Status.EXPIRED)
    if UploadIntent.objects.filter(tenant_id=tenant_id, script=script, kind=kind, page_number=page_number, asset_version=asset_version, status=UploadIntent.Status.ISSUED, expires_at__gt=now).exists():
        raise HttpError(409, "An active upload intent already exists for this page")
    extension = MIME_EXTENSIONS[content_type]
    key = str(PurePosixPath(PREFIXES[kind]) / str(tenant_id) / str(script.id) / f"page-{page_number:04d}-v{asset_version}-{secrets.token_hex(8)}{extension}")
    with transaction.atomic():
        intent = UploadIntent.objects.create(
            tenant_id=tenant_id,
            script=script,
            kind=kind,
            page_number=page_number,
            asset_version=asset_version,
            storage_key=key,
            content_type=content_type,
            maximum_bytes=maximum_bytes,
            expires_at=now + timedelta(minutes=5),
        )
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="repository.upload_intent.issued", aggregate="UploadIntent", aggregate_id=intent.id, payload={"script_id": str(script.id), "kind": kind, "page_number": page_number})
    url, expires = signed_object_url(method="PUT", key=key, content_type=content_type, max_bytes=maximum_bytes)
    return intent, url, expires


@transaction.atomic
def finalize_upload(*, tenant_id, actor_id, intent_id, expected_version, idempotency_key):
    record, replay_id = begin_idempotent(tenant_id=tenant_id, scope="repository.upload.finalize", key=idempotency_key, payload={"intent_id": intent_id, "version": expected_version})
    if replay_id:
        replay = UploadIntent.objects.select_related("script").get(id=replay_id, tenant_id=tenant_id)
        return replay, ScriptAsset.objects.filter(storage_key=replay.storage_key).first()
    intent = UploadIntent.objects.filter(id=intent_id, tenant_id=tenant_id).select_related("script").first()
    if not intent:
        raise HttpError(404, "Upload intent not found")
    if intent.status == UploadIntent.Status.COMPLETED:
        complete_idempotent(record, intent.id)
        return intent, ScriptAsset.objects.filter(storage_key=intent.storage_key).first()
    if intent.expires_at <= timezone.now():
        UploadIntent.objects.filter(id=intent.id, status=UploadIntent.Status.ISSUED).update(status=UploadIntent.Status.EXPIRED)
        raise HttpError(409, "Upload intent has expired")
    try:
        metadata = read_object_metadata(intent.storage_key)
    except Exception as exc:
        raise HttpError(409, "Uploaded object is unavailable or failed integrity verification") from exc
    if metadata.mime_type.split(";", 1)[0] != intent.content_type or metadata.byte_size > intent.maximum_bytes:
        raise HttpError(409, "Uploaded object does not match its signed intent")
    with transaction.atomic():
        intent = UploadIntent.objects.select_for_update().select_related("script").get(id=intent.id, tenant_id=tenant_id)
        if intent.version != expected_version:
            raise HttpError(409, "Upload intent was changed by another user")
        if intent.status != UploadIntent.Status.ISSUED:
            if intent.status == UploadIntent.Status.COMPLETED:
                return intent, ScriptAsset.objects.filter(storage_key=intent.storage_key).first()
            raise HttpError(409, "Upload intent is no longer active")
        asset = None
        if intent.kind in ASSET_KINDS:
            try:
                asset = ScriptAsset.objects.create(
                    tenant_id=tenant_id,
                    script=intent.script,
                    kind=ASSET_KINDS[intent.kind],
                    page_number=intent.page_number,
                    storage_key=intent.storage_key,
                    sha256=metadata.sha256,
                    byte_size=metadata.byte_size,
                    mime_type=metadata.mime_type,
                    version=intent.asset_version,
                    retention_until=timezone.localdate() + timedelta(days=365 * 7),
                    object_lock_until=timezone.localdate() + timedelta(days=365 * 7) if intent.kind == UploadIntent.Kind.MASTER else None,
                    backup_status=metadata.backup_status,
                    replication_status=metadata.replication_status,
                )
            except IntegrityError as exc:
                raise HttpError(409, "This repository asset version already exists") from exc
        intent.status = UploadIntent.Status.COMPLETED
        intent.sha256 = metadata.sha256
        intent.byte_size = metadata.byte_size
        intent.finalized_at = timezone.now()
        intent.version += 1
        intent.save()
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="repository.upload.finalized", aggregate="UploadIntent", aggregate_id=intent.id, payload={"script_id": str(intent.script_id), "kind": intent.kind, "sha256": metadata.sha256, "byte_size": metadata.byte_size})
        complete_idempotent(record, intent.id)
    return intent, asset


def verify_asset_integrity(*, tenant_id, actor_id, asset_id):
    asset = ScriptAsset.objects.filter(id=asset_id, tenant_id=tenant_id, deleted_at__isnull=True).first()
    if not asset:
        raise HttpError(404, "Repository asset not found")
    try:
        metadata = read_object_metadata(asset.storage_key)
        valid = metadata.sha256 == asset.sha256 and metadata.byte_size == asset.byte_size
    except Exception:
        valid = False
    with transaction.atomic():
        asset = ScriptAsset.objects.select_for_update().get(id=asset.id)
        asset.integrity_checked_at = timezone.now()
        asset.save(update_fields=["integrity_checked_at", "updated_at"])
        if not valid:
            alert = SecurityAlert.objects.create(tenant_id=tenant_id, category="repository_tamper", severity=SecurityAlert.Severity.CRITICAL, title="Script repository integrity verification failed", details={"asset_id": str(asset.id), "script_id": str(asset.script_id)})
            record_event(tenant_id=tenant_id, actor_id=actor_id, action="repository.integrity.failed", aggregate="ScriptAsset", aggregate_id=asset.id, payload={"alert_id": str(alert.id)})
        else:
            record_event(tenant_id=tenant_id, actor_id=actor_id, action="repository.integrity.verified", aggregate="ScriptAsset", aggregate_id=asset.id, payload={"sha256": asset.sha256})
    return asset, valid


def secure_delete_asset(*, tenant_id, actor_id, asset_id, reason):
    if not reason.strip():
        raise HttpError(422, "A deletion reason is required")
    asset = ScriptAsset.objects.filter(id=asset_id, tenant_id=tenant_id, deleted_at__isnull=True).first()
    if not asset:
        raise HttpError(404, "Repository asset not found")
    if asset.kind == ScriptAsset.Kind.MASTER:
        raise HttpError(409, "Immutable master assets have no delete path")
    if asset.legal_hold or not asset.retention_until or asset.retention_until > timezone.localdate():
        raise HttpError(409, "Retention or legal hold prevents secure deletion")
    try:
        delete_object(asset.storage_key)
    except Exception as exc:
        raise HttpError(503, "Storage gateway could not confirm secure deletion") from exc
    with transaction.atomic():
        asset = ScriptAsset.objects.select_for_update().get(id=asset.id)
        asset.deleted_at = timezone.now()
        asset.deletion_reason = reason
        asset.save(update_fields=["deleted_at", "deletion_reason", "updated_at"])
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="repository.asset.secure_deleted", aggregate="ScriptAsset", aggregate_id=asset.id, payload={"reason": reason})
    return asset


def archive_asset(*, tenant_id, actor_id, asset_id):
    asset = ScriptAsset.objects.filter(id=asset_id, tenant_id=tenant_id, deleted_at__isnull=True).first()
    if not asset:
        raise HttpError(404, "Repository asset not found")
    try:
        metadata = read_object_metadata(asset.storage_key)
    except Exception as exc:
        raise HttpError(503, "Storage copies could not be verified") from exc
    if metadata.sha256 != asset.sha256 or metadata.backup_status != "completed" or metadata.replication_status != "completed":
        raise HttpError(409, "Archive requires verified primary, backup and replica copies")
    with transaction.atomic():
        asset = ScriptAsset.objects.select_for_update().get(id=asset.id, tenant_id=tenant_id)
        asset.archived_at = timezone.now()
        asset.integrity_checked_at = timezone.now()
        asset.backup_status = metadata.backup_status
        asset.replication_status = metadata.replication_status
        asset.save(update_fields=["archived_at", "integrity_checked_at", "backup_status", "replication_status", "updated_at"])
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="repository.asset.archived", aggregate="ScriptAsset", aggregate_id=asset.id, payload={"backup_status": asset.backup_status, "replication_status": asset.replication_status})
    return asset
