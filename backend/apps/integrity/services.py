import base64
import hashlib
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from ninja.errors import HttpError

from apps.core.services import record_event
from apps.repository.storage import read_object_metadata

from .models import IntegrityAlert, IntegrityCheck, IntegrityEvidence, IntegrityManifest


ALERT_TRANSITIONS = {
    IntegrityAlert.Status.OPEN: {IntegrityAlert.Status.ACKNOWLEDGED},
    IntegrityAlert.Status.ACKNOWLEDGED: {IntegrityAlert.Status.RESOLVED},
}


def _json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _signing_key():
    material = (settings.APPLICATION_ENCRYPTION_KEY or settings.SECRET_KEY).encode()
    return Ed25519PrivateKey.from_private_bytes(hashlib.sha256(b"admiezo-integrity-v1:" + material).digest())


def _custody_snapshot(asset, signed_at=None):
    events = asset.script.custody_events.all().order_by("created_at")
    if signed_at:
        events = events.filter(created_at__lte=signed_at)
    return [{"from": item.from_state, "to": item.to_state, "location": item.location, "actor": item.actor_id, "at": item.created_at.isoformat(), "metadata": item.metadata} for item in events]


def _metadata_snapshot(asset):
    return {"asset_id": str(asset.id), "script_id": str(asset.script_id), "kind": asset.kind, "page_number": asset.page_number, "storage_key": asset.storage_key, "mime_type": asset.mime_type, "byte_size": asset.byte_size, "object_lock_until": asset.object_lock_until.isoformat() if asset.object_lock_until else None}


def _signed_payload(manifest):
    return f"{manifest.file_sha256}:{manifest.metadata_sha256}:{manifest.custody_sha256}:{manifest.version_sha256}".encode()


@transaction.atomic
def create_manifest(*, tenant_id, actor_id, asset):
    existing = IntegrityManifest.objects.filter(asset=asset).first()
    if existing:
        return existing, False
    metadata = read_object_metadata(asset.storage_key)
    if metadata.sha256 != asset.sha256:
        raise HttpError(409, "Repository checksum does not match storage; manifest was not created")
    now = timezone.now()
    private_key = _signing_key()
    manifest = IntegrityManifest(
        tenant_id=tenant_id,
        asset=asset,
        file_sha256=asset.sha256,
        metadata_sha256=_json_hash(_metadata_snapshot(asset)),
        custody_sha256=_json_hash(_custody_snapshot(asset, now)),
        version_sha256=_json_hash({"asset_version": asset.version, "storage_key": asset.storage_key}),
        public_key=base64.b64encode(private_key.public_key().public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)).decode(),
        signed_at=now,
    )
    manifest.signature = base64.b64encode(private_key.sign(_signed_payload(manifest))).decode()
    manifest.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="integrity.manifest.signed", aggregate="IntegrityManifest", aggregate_id=manifest.id, payload={"asset_id": str(asset.id), "file_sha256": manifest.file_sha256})
    return manifest, True


def verify_manifest(*, tenant_id, actor_id, manifest_id, checked_by="operator"):
    manifest = IntegrityManifest.objects.filter(id=manifest_id, tenant_id=tenant_id).select_related("asset__script").first()
    if not manifest:
        raise HttpError(404, "Integrity manifest not found")
    observed = {}
    status = IntegrityCheck.Status.PASSED
    kind = ""
    try:
        storage = read_object_metadata(manifest.asset.storage_key)
        observed = {
            "file_sha256": storage.sha256,
            "byte_size": storage.byte_size,
            "mime_type": storage.mime_type,
            "replication_status": storage.replication_status,
            "backup_status": storage.backup_status,
            "metadata_sha256": _json_hash(_metadata_snapshot(manifest.asset)),
            "custody_sha256": _json_hash(_custody_snapshot(manifest.asset, manifest.signed_at)),
            "version_sha256": _json_hash({"asset_version": manifest.asset.version, "storage_key": manifest.asset.storage_key}),
        }
        signature_valid = True
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(manifest.public_key)).verify(base64.b64decode(manifest.signature), _signed_payload(manifest))
        except Exception:
            signature_valid = False
        observed["signature_valid"] = signature_valid
        expected = {"file_sha256": manifest.file_sha256, "metadata_sha256": manifest.metadata_sha256, "custody_sha256": manifest.custody_sha256, "version_sha256": manifest.version_sha256}
        changed = [field for field, value in expected.items() if observed.get(field) != value]
        if not signature_valid:
            changed.append("signature")
        if changed:
            status = IntegrityCheck.Status.FAILED
            kind = "asset_modified"
            observed["changed_fields"] = changed
        elif storage.replication_status != "completed" or storage.backup_status != "completed":
            status = IntegrityCheck.Status.FAILED
            kind = "replica_or_backup_failed"
    except Exception as exc:
        status = IntegrityCheck.Status.MISSING
        kind = "asset_missing"
        observed = {"error": str(exc)[:300], "storage_key": manifest.asset.storage_key}

    with transaction.atomic():
        current = IntegrityManifest.objects.select_for_update().get(id=manifest.id)
        check = IntegrityCheck.objects.create(tenant_id=tenant_id, manifest=current, status=status, observed=observed, checked_by=checked_by[:64], checked_at=timezone.now())
        current.last_verified_at = check.checked_at
        current.verification_status = status
        current.save(update_fields=["last_verified_at", "verification_status", "updated_at"])
        alert = None
        if status != IntegrityCheck.Status.PASSED:
            alert = IntegrityAlert.objects.filter(manifest=current, kind=kind, status__in=[IntegrityAlert.Status.OPEN, IntegrityAlert.Status.ACKNOWLEDGED]).first()
            if not alert:
                alert = IntegrityAlert.objects.create(tenant_id=tenant_id, manifest=current, kind=kind, details=observed)
                evidence_snapshot = {"check_id": str(check.id), "manifest": {"file_sha256": current.file_sha256, "metadata_sha256": current.metadata_sha256, "custody_sha256": current.custody_sha256, "version_sha256": current.version_sha256, "signature": current.signature}, "observed": observed}
                IntegrityEvidence.objects.create(tenant_id=tenant_id, alert=alert, evidence_type="verification_snapshot", snapshot=evidence_snapshot, sha256=_json_hash(evidence_snapshot), preserved_at=timezone.now())
        record_event(tenant_id=tenant_id, actor_id=actor_id, action=f"integrity.check.{status}", aggregate="IntegrityCheck", aggregate_id=check.id, payload={"manifest_id": str(current.id), "alert_id": str(alert.id) if alert else None, "observed": observed})
    return check, alert


@transaction.atomic
def transition_alert(*, tenant_id, actor_id, alert_id, target):
    alert = IntegrityAlert.objects.select_for_update().filter(id=alert_id, tenant_id=tenant_id).first()
    if not alert:
        raise HttpError(404, "Integrity alert not found")
    if target not in ALERT_TRANSITIONS.get(alert.status, set()):
        raise HttpError(409, f"Alert transition from {alert.status} to {target} is not allowed")
    if target == IntegrityAlert.Status.RESOLVED:
        latest = alert.manifest.checks.order_by("-checked_at").first()
        if not latest or latest.status != IntegrityCheck.Status.PASSED or latest.checked_at <= alert.created_at:
            raise HttpError(409, "A later passing verification is required before resolving this alert")
        alert.resolved_by_id = actor_id
    else:
        alert.acknowledged_by_id = actor_id
    previous = alert.status
    alert.status = target
    alert.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="integrity.alert.transitioned", aggregate="IntegrityAlert", aggregate_id=alert.id, payload={"from": previous, "to": target})
    return alert
