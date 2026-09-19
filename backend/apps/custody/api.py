from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from apps.core.authz import require_roles
from apps.core.services import record_event
from apps.receiving.models import Packet
from apps.tenancy.custom_fields import persist_custom_values, validate_custom_values
from apps.tenancy.models import Membership

from .models import BarcodeScan, CustodyEvent, CustodyTransfer, ReconciliationRun, Script
from .schemas import BarcodeScanIn, ReconcilePacketIn, ScriptRegisterIn, ScriptRemovalIn, ScriptTransitionIn, TransferDecisionIn, TransferReceiveIn, TransferRequestIn
from .services import decide_transfer, reconcile_packet, register_script, scan_barcode, transition_script


router = Router(tags=["Script identification and chain of custody"])
CUSTODY_ROLES = (
    Membership.Role.PLATFORM_ADMIN,
    Membership.Role.UNIVERSITY_ADMIN,
    Membership.Role.EXAM_CONTROLLER,
    Membership.Role.CUSTODY_OFFICER,
)
CUSTODY_INTAKE_ROLES = CUSTODY_ROLES + (
    Membership.Role.RECEIVING_OFFICER,
    Membership.Role.SCRIPT_RECEIVER,
)
CUSTODY_SCAN_ROLES = CUSTODY_INTAKE_ROLES + (Membership.Role.SCANNER_OPERATOR,)


@router.get("/catalog")
def custody_catalog(request, state: str | None = None):
    tenant_id = require_roles(request, *CUSTODY_ROLES).institution.tenant_id
    scripts = Script.objects.filter(tenant_id=tenant_id, removed_at__isnull=True).select_related("paper", "packet")
    if state:
        scripts = scripts.filter(state=state)
    events = CustodyEvent.objects.filter(tenant_id=tenant_id).select_related("script")[:100]
    transfers = CustodyTransfer.objects.filter(tenant_id=tenant_id).select_related("script")[:100]
    scans = BarcodeScan.objects.filter(tenant_id=tenant_id).select_related("script")[:100]
    reconciliations = ReconciliationRun.objects.filter(tenant_id=tenant_id).select_related("packet")[:100]
    latest_events = {}
    for item in CustodyEvent.objects.filter(tenant_id=tenant_id).order_by("-created_at"):
        latest_events.setdefault(item.script_id, item)
    actor_ids = {int(item.actor_id) for item in latest_events.values() if item.actor_id.isdigit()}
    actor_ids.update(Script.objects.filter(tenant_id=tenant_id, removed_by_id__isnull=False).values_list("removed_by_id", flat=True))
    actors = {item.id: item.get_full_name() or item.email or item.username for item in User.objects.filter(id__in=actor_ids)}
    removed = Script.objects.filter(tenant_id=tenant_id, removed_at__isnull=False).select_related("paper", "packet").order_by("-removed_at")[:500]
    return {
        "scripts": [{"id": str(item.id), "script_code": item.script_code, "primary_barcode": item.primary_barcode, "paper": item.paper.code, "packet": item.packet.barcode, "page_count": item.page_count, "state": item.state, "last_location": item.last_location, "current_custodian": actors.get(int(latest_events[item.id].actor_id), f"User {latest_events[item.id].actor_id}") if item.id in latest_events and latest_events[item.id].actor_id.isdigit() else "Unassigned", "version": item.version} for item in scripts.order_by("script_code")[:500]],
        "removed": [{"id": str(item.id), "script_code": item.script_code, "primary_barcode": item.primary_barcode, "paper": item.paper.code, "packet": item.packet.barcode, "state": item.state, "last_location": item.last_location, "removed_at": item.removed_at.isoformat(), "removed_by": actors.get(item.removed_by_id, f"User {item.removed_by_id}"), "removal_reason": item.removal_reason, "version": item.version} for item in removed],
        "events": [{"id": str(item.id), "script": item.script.script_code, "from_state": item.from_state, "to_state": item.to_state, "location": item.location, "actor_id": item.actor_id, "metadata": item.metadata, "created_at": item.created_at.isoformat()} for item in events],
        "transfers": [{"id": str(item.id), "script": item.script.script_code, "from_location": item.from_location, "to_location": item.to_location, "reason": item.reason, "status": item.status, "version": item.version, "created_at": item.created_at.isoformat()} for item in transfers],
        "scans": [{"id": str(item.id), "barcode": item.barcode, "script": item.script.script_code if item.script else None, "purpose": item.purpose, "location": item.location, "result": item.result, "created_at": item.created_at.isoformat()} for item in scans],
        "reconciliations": [{"id": str(item.id), "packet": item.packet.barcode, "expected_scripts": item.expected_scripts, "observed_scripts": item.observed_scripts, "missing": item.missing_barcodes, "excess": item.excess_barcodes, "duplicates": item.duplicate_barcodes, "mismatched": item.mismatched_barcodes, "status": item.status, "created_at": item.created_at.isoformat()} for item in reconciliations],
    }


@router.post("/scripts/{script_id}/remove")
def remove_script(request, script_id: str, payload: ScriptRemovalIn):
    membership = require_roles(request, *CUSTODY_ROLES)
    from apps.allocation.models import Assignment

    with transaction.atomic():
        script = Script.objects.select_for_update().filter(id=script_id, tenant_id=membership.institution.tenant_id, removed_at__isnull=True).first()
        if not script:
            raise HttpError(404, "Active script was not found")
        if script.version != payload.version:
            raise HttpError(409, "Script was changed by another user")
        if len(payload.reason.strip()) < 8:
            raise HttpError(422, "Removal reason must contain at least 8 characters")
        if Assignment.objects.filter(script=script, status__in=["assigned", "accepted", "in_progress"]).exists():
            raise HttpError(409, "A script with an active evaluation assignment cannot be removed")
        script.removed_at = timezone.now()
        script.removed_by_id = request.auth.id
        script.removal_reason = payload.reason.strip()
        script.version += 1
        script.save(update_fields=["removed_at", "removed_by_id", "removal_reason", "version", "updated_at"])
        record_event(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, action="custody.script.removed", aggregate="Script", aggregate_id=script.id, payload={"reason": script.removal_reason})
    return {"id": str(script.id), "version": script.version, "removed_at": script.removed_at.isoformat()}


@router.post("/scripts/{script_id}/restore")
def restore_script(request, script_id: str, payload: ScriptRemovalIn):
    membership = require_roles(request, *CUSTODY_ROLES)
    with transaction.atomic():
        script = Script.objects.select_for_update().filter(id=script_id, tenant_id=membership.institution.tenant_id, removed_at__isnull=False).first()
        if not script:
            raise HttpError(404, "Removed script was not found")
        if script.version != payload.version:
            raise HttpError(409, "Script was changed by another user")
        if len(payload.reason.strip()) < 8:
            raise HttpError(422, "Restoration reason must contain at least 8 characters")
        previous_reason = script.removal_reason
        script.removed_at = None
        script.removed_by_id = None
        script.removal_reason = ""
        script.version += 1
        script.save(update_fields=["removed_at", "removed_by_id", "removal_reason", "version", "updated_at"])
        record_event(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, action="custody.script.restored", aggregate="Script", aggregate_id=script.id, payload={"reason": payload.reason.strip(), "previous_removal_reason": previous_reason})
    return {"id": str(script.id), "version": script.version}


@router.post("/scripts")
def create_script(request, payload: ScriptRegisterIn):
    membership = require_roles(request, *CUSTODY_INTAKE_ROLES)
    tenant_id = membership.institution.tenant_id
    custom_fields = validate_custom_values(tenant_id=tenant_id, form_key="script", values=payload.custom_fields)
    packet = Packet.objects.filter(id=payload.packet_id, tenant_id=tenant_id).select_related("dispatch__paper").first()
    if not packet:
        raise HttpError(404, "Packet not found")
    script = register_script(tenant_id=tenant_id, actor_id=request.auth.id, packet=packet, primary_barcode=payload.primary_barcode, supplements=payload.supplement_barcodes, bundle_barcode=payload.bundle_barcode, centre_barcode=payload.centre_barcode, location=payload.location)
    persist_custom_values(tenant_id=tenant_id, actor_id=request.auth.id, form_key="script", record_id=script.id, values=custom_fields)
    return {"id": str(script.id), "script_code": script.script_code, "state": script.state, "version": script.version, "qr_value": script.script_code}


@router.post("/scripts/{script_id}/transition")
def change_script_state(request, script_id: str, payload: ScriptTransitionIn):
    membership = require_roles(request, *CUSTODY_SCAN_ROLES, Membership.Role.EVALUATOR)
    script = transition_script(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, script_id=script_id, expected_version=payload.version, to_state=payload.to_state, location=payload.location, metadata=payload.metadata)
    return {"id": str(script.id), "state": script.state, "version": script.version}


@router.post("/barcodes/scan")
def validate_barcode(request, payload: BarcodeScanIn):
    membership = require_roles(request, *CUSTODY_SCAN_ROLES)
    scan, barcode = scan_barcode(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, barcode=payload.barcode, purpose=payload.purpose, location=payload.location)
    return {"scan_id": str(scan.id), "result": scan.result, "kind": barcode.kind if barcode else None, "script_id": str(barcode.script_id) if barcode and barcode.script_id else None}


@router.post("/packets/{packet_id}/reconcile")
def run_packet_reconciliation(request, packet_id: str, payload: ReconcilePacketIn):
    membership = require_roles(request, *CUSTODY_INTAKE_ROLES)
    packet = Packet.objects.filter(id=packet_id, tenant_id=membership.institution.tenant_id).first()
    if not packet:
        raise HttpError(404, "Packet not found")
    run = reconcile_packet(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, packet=packet, manifest_barcodes=payload.manifest_barcodes, observed_barcodes=payload.observed_barcodes)
    return {"id": str(run.id), "status": run.status, "missing": run.missing_barcodes, "excess": run.excess_barcodes, "duplicates": run.duplicate_barcodes, "mismatched": run.mismatched_barcodes}


@router.post("/transfers")
def request_transfer(request, payload: TransferRequestIn):
    membership = require_roles(request, *CUSTODY_ROLES)
    tenant_id = membership.institution.tenant_id
    script = Script.objects.filter(id=payload.script_id, tenant_id=tenant_id).first()
    if not script:
        raise HttpError(404, "Script not found")
    if not payload.reason.strip() or script.last_location == payload.to_location:
        raise HttpError(422, "A reason and a different destination are required")
    with transaction.atomic():
        transfer = CustodyTransfer.objects.create(tenant_id=tenant_id, script=script, from_location=script.last_location or "Unknown", to_location=payload.to_location, reason=payload.reason, requested_by_id=request.auth.id)
        record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="custody.transfer.requested", aggregate="CustodyTransfer", aggregate_id=transfer.id, payload={"script_id": str(script.id), "to_location": transfer.to_location})
    return {"id": str(transfer.id), "status": transfer.status, "version": transfer.version}


@router.post("/transfers/{transfer_id}/decision")
def transfer_decision(request, transfer_id: str, payload: TransferDecisionIn):
    membership = require_roles(request, *CUSTODY_ROLES)
    transfer = decide_transfer(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, transfer_id=transfer_id, version=payload.version, authorize=payload.authorize, rejection_reason=payload.rejection_reason)
    return {"id": str(transfer.id), "status": transfer.status, "version": transfer.version}


@router.post("/transfers/{transfer_id}/dispatch")
def dispatch_transfer(request, transfer_id: str, payload: TransferReceiveIn):
    membership = require_roles(request, *CUSTODY_ROLES)
    with transaction.atomic():
        transfer = CustodyTransfer.objects.select_for_update().filter(id=transfer_id, tenant_id=membership.institution.tenant_id).first()
        if not transfer or transfer.version != payload.version:
            raise HttpError(409, "Transfer is missing or stale")
        if transfer.status != CustodyTransfer.Status.AUTHORIZED:
            raise HttpError(409, "Only an authorized transfer can enter transit")
        transfer.status = CustodyTransfer.Status.IN_TRANSIT
        transfer.dispatched_at = timezone.now()
        transfer.version += 1
        transfer.save()
        record_event(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, action="custody.transfer.dispatched", aggregate="CustodyTransfer", aggregate_id=transfer.id, payload={"location": payload.location})
    return {"id": str(transfer.id), "status": transfer.status, "version": transfer.version}


@router.post("/transfers/{transfer_id}/receive")
def receive_transfer(request, transfer_id: str, payload: TransferReceiveIn):
    membership = require_roles(request, *CUSTODY_ROLES)
    with transaction.atomic():
        transfer = CustodyTransfer.objects.select_for_update().select_related("script").filter(id=transfer_id, tenant_id=membership.institution.tenant_id).first()
        if not transfer or transfer.version != payload.version:
            raise HttpError(409, "Transfer is missing or stale")
        if transfer.status != CustodyTransfer.Status.IN_TRANSIT:
            raise HttpError(409, "Only an in-transit transfer can be received")
        transfer.status = CustodyTransfer.Status.RECEIVED
        transfer.received_by_id = request.auth.id
        transfer.received_at = timezone.now()
        transfer.version += 1
        transfer.save()
        transfer.script.last_location = payload.location
        transfer.script.version += 1
        transfer.script.save(update_fields=["last_location", "version", "updated_at"])
        CustodyEvent.objects.create(tenant_id=membership.institution.tenant_id, script=transfer.script, from_state=transfer.script.state, to_state=transfer.script.state, location=payload.location, actor_id=str(request.auth.id), metadata={"transfer_id": str(transfer.id), "movement": "physical"})
        record_event(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, action="custody.transfer.received", aggregate="CustodyTransfer", aggregate_id=transfer.id, payload={"location": payload.location})
    return {"id": str(transfer.id), "status": transfer.status, "version": transfer.version}
