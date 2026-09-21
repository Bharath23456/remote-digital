import secrets

from django.db import IntegrityError, transaction
from django.utils import timezone
from ninja.errors import HttpError

from apps.core.services import record_event

from .models import BarcodeRecord, BarcodeScan, CustodyEvent, CustodyTransfer, ReconciliationRun, Script


SCRIPT_TRANSITIONS = {
    Script.State.RECEIVED: {Script.State.REGISTERED, Script.State.EXCEPTION},
    Script.State.REGISTERED: {Script.State.SCANNED, Script.State.EXCEPTION},
    Script.State.SCANNED: {Script.State.VALIDATED, Script.State.EXCEPTION},
    Script.State.VALIDATED: {Script.State.MASKED, Script.State.EXCEPTION},
    Script.State.MASKED: {Script.State.STORED, Script.State.EXCEPTION},
    Script.State.STORED: {Script.State.ASSIGNED, Script.State.ARCHIVED},
    Script.State.ASSIGNED: {Script.State.OPENED, Script.State.EXCEPTION},
    Script.State.OPENED: {Script.State.EVALUATING, Script.State.EXCEPTION},
    Script.State.EVALUATING: {Script.State.SUBMITTED, Script.State.EXCEPTION},
    Script.State.SUBMITTED: {Script.State.REVIEW, Script.State.FINALIZED},
    Script.State.REVIEW: {Script.State.MODERATED, Script.State.REVALUATED, Script.State.FINALIZED},
    Script.State.MODERATED: {Script.State.FINALIZED},
    Script.State.REVALUATED: {Script.State.FINALIZED},
    Script.State.FINALIZED: {Script.State.ARCHIVED},
    Script.State.EXCEPTION: {Script.State.REGISTERED},
}


def transition_script(*, tenant_id, actor_id, script_id, expected_version, to_state, location, metadata=None):
    if to_state not in Script.State.values:
        raise HttpError(422, "Unsupported script state")
    with transaction.atomic():
        script = Script.objects.select_for_update().filter(id=script_id, tenant_id=tenant_id).first()
        if not script:
            raise HttpError(404, "Script not found")
        if script.version != expected_version:
            raise HttpError(409, "Script was changed by another user")
        if to_state not in SCRIPT_TRANSITIONS.get(script.state, set()):
            raise HttpError(409, f"Transition from {script.state} to {to_state} is not allowed")
        previous = script.state
        script.state = to_state
        script.last_location = location
        script.version += 1
        script.save(update_fields=["state", "last_location", "version", "updated_at"])
        CustodyEvent.objects.create(
            tenant_id=tenant_id,
            script=script,
            from_state=previous,
            to_state=to_state,
            location=location,
            actor_id=str(actor_id),
            metadata=metadata or {},
        )
        record_event(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="custody.script.transitioned",
            aggregate="Script",
            aggregate_id=script.id,
            payload={"from": previous, "to": to_state, "location": location},
        )
    return script


def return_script_for_remasking(*, tenant_id, actor_id, script_id, expected_version, job_id, reason, allow_stored=False):
    with transaction.atomic():
        script = Script.objects.select_for_update().filter(id=script_id, tenant_id=tenant_id).first()
        allowed_states = (Script.State.VALIDATED, Script.State.MASKED, Script.State.STORED) if allow_stored else (Script.State.VALIDATED, Script.State.MASKED)
        if not script or script.version != expected_version or script.state not in allowed_states:
            raise HttpError(409, "Script is no longer in a masking review state")
        previous = script.state
        script.state = Script.State.SCANNED
        script.last_location = "Anonymisation remasking queue"
        script.version += 1
        script.save(update_fields=["state", "last_location", "version", "updated_at"])
        CustodyEvent.objects.create(tenant_id=tenant_id, script=script, from_state=previous, to_state=script.state, location=script.last_location, actor_id=str(actor_id), metadata={"masking_job_id": str(job_id), "reason": reason})
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="custody.script.returned_for_remasking", aggregate="Script", aggregate_id=script.id, payload={"from": previous, "to": script.state, "masking_job_id": str(job_id)})
    return script


def register_script(*, tenant_id, actor_id, packet, primary_barcode, supplements, bundle_barcode, centre_barcode, location):
    normalized = [primary_barcode.strip(), *[item.strip() for item in supplements]]
    if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
        raise HttpError(422, "Script and supplement barcodes must be non-empty and unique")
    script_code = f"AS-{secrets.token_hex(8).upper()}"
    try:
        with transaction.atomic():
            script = Script.objects.create(
                tenant_id=tenant_id,
                script_code=script_code,
                primary_barcode=normalized[0],
                packet=packet,
                paper=packet.paper or packet.dispatch.paper,
                supplement_barcodes=normalized[1:],
                bundle_barcode=bundle_barcode.strip(),
                centre_barcode=centre_barcode.strip(),
                state=Script.State.REGISTERED,
                last_location=location,
            )
            BarcodeRecord.objects.create(
                tenant_id=tenant_id,
                code=normalized[0],
                kind=BarcodeRecord.Kind.SCRIPT,
                script=script,
                packet=packet,
                registered_by_id=actor_id,
            )
            for barcode in normalized[1:]:
                BarcodeRecord.objects.create(
                    tenant_id=tenant_id,
                    code=barcode,
                    kind=BarcodeRecord.Kind.SUPPLEMENT,
                    script=script,
                    packet=packet,
                    registered_by_id=actor_id,
                )
            CustodyEvent.objects.create(
                tenant_id=tenant_id,
                script=script,
                from_state=Script.State.RECEIVED,
                to_state=Script.State.REGISTERED,
                location=location,
                actor_id=str(actor_id),
                metadata={"packet_id": str(packet.id)},
            )
            record_event(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="custody.script.registered",
                aggregate="Script",
                aggregate_id=script.id,
                payload={"script_code": script.script_code, "packet_id": str(packet.id)},
            )
    except IntegrityError as exc:
        raise HttpError(409, "A script, supplement, bundle, packet or centre barcode already exists") from exc
    return script


def scan_barcode(*, tenant_id, actor_id, barcode, purpose, location):
    barcode_record = BarcodeRecord.objects.filter(tenant_id=tenant_id, code=barcode, is_active=True).select_related("script", "packet").first()
    result = "valid" if barcode_record else "unknown"
    scan = BarcodeScan.objects.create(
        tenant_id=tenant_id,
        barcode=barcode,
        script=barcode_record.script if barcode_record else None,
        purpose=purpose,
        location=location,
        actor_id=actor_id,
        result=result,
        details={"kind": barcode_record.kind if barcode_record else None},
    )
    return scan, barcode_record


def reconcile_packet(*, tenant_id, actor_id, packet, manifest_barcodes, observed_barcodes):
    duplicates = sorted({item for item in observed_barcodes if observed_barcodes.count(item) > 1})
    manifest = set(manifest_barcodes)
    observed = set(observed_barcodes)
    missing = sorted(manifest - observed)
    excess = sorted(observed - manifest)
    mismatched = sorted(
        BarcodeRecord.objects.filter(tenant_id=tenant_id, code__in=observed, packet__isnull=False)
        .exclude(packet=packet)
        .values_list("code", flat=True)
    )
    status = "reconciled" if not any((duplicates, missing, excess, mismatched)) else "exception"
    with transaction.atomic():
        run = ReconciliationRun.objects.create(
            tenant_id=tenant_id,
            packet=packet,
            expected_scripts=len(manifest_barcodes),
            observed_scripts=len(observed_barcodes),
            missing_barcodes=missing,
            excess_barcodes=excess,
            mismatched_barcodes=mismatched,
            duplicate_barcodes=duplicates,
            status=status,
            performed_by_id=actor_id,
        )
        packet.received_scripts = len(observed_barcodes)
        packet.status = status
        packet.version += 1
        packet.save(update_fields=["received_scripts", "status", "version", "updated_at"])
        record_event(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="custody.packet.reconciled",
            aggregate="ReconciliationRun",
            aggregate_id=run.id,
            payload={"packet_id": str(packet.id), "status": status, "missing": len(missing), "excess": len(excess)},
        )
    return run


def decide_transfer(*, tenant_id, actor_id, transfer_id, version, authorize, rejection_reason=""):
    with transaction.atomic():
        transfer = CustodyTransfer.objects.select_for_update().filter(id=transfer_id, tenant_id=tenant_id).first()
        if not transfer:
            raise HttpError(404, "Custody transfer not found")
        if transfer.version != version:
            raise HttpError(409, "Custody transfer was changed by another user")
        if transfer.status != CustodyTransfer.Status.REQUESTED:
            raise HttpError(409, "Custody transfer is no longer awaiting authorization")
        if transfer.requested_by_id == actor_id:
            raise HttpError(409, "A transfer requester cannot authorize their own transfer")
        transfer.status = CustodyTransfer.Status.AUTHORIZED if authorize else CustodyTransfer.Status.REJECTED
        transfer.authorized_by_id = actor_id
        transfer.authorized_at = timezone.now()
        transfer.rejection_reason = rejection_reason if not authorize else ""
        transfer.version += 1
        transfer.save()
        record_event(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="custody.transfer.authorized" if authorize else "custody.transfer.rejected",
            aggregate="CustodyTransfer",
            aggregate_id=transfer.id,
            payload={"script_id": str(transfer.script_id), "to_location": transfer.to_location},
        )
    return transfer
