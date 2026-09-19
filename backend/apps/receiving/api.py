from django.db import IntegrityError, transaction
from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from apps.configuration.models import Paper
from apps.core.authz import require_roles
from apps.core.services import record_event
from apps.tenancy.custom_fields import persist_custom_values, validate_custom_values
from apps.tenancy.models import Membership

from .models import Bundle, Dispatch, Packet, ReceivingAlert, ReceivingException
from .schemas import BundleCreateIn, BundleReceiveIn, ConfirmationIn, DispatchCreateIn, DispatchVerifyIn, ExceptionClearIn, ExceptionCreateIn, ExceptionReconcileIn, ExceptionReviewIn, PacketCreateIn, PacketReceiveIn, VersionIn
from .services import close_dispatch, confirm_receipt, create_dispatch, detect_transit_delays, receive_bundle, receive_packet, reconcile_dispatch, verify_dispatch


router = Router(tags=["Physical answer script receiving"])
RECEIVING_ROLES = (
    Membership.Role.PLATFORM_ADMIN,
    Membership.Role.UNIVERSITY_ADMIN,
    Membership.Role.EXAM_CONTROLLER,
    Membership.Role.RECEIVING_OFFICER,
    Membership.Role.SCRIPT_RECEIVER,
)
RECEIVING_READ_ROLES = RECEIVING_ROLES + (Membership.Role.CUSTODY_OFFICER,)


@router.get("/catalog")
def receiving_catalog(request):
    tenant_id = require_roles(request, *RECEIVING_READ_ROLES).institution.tenant_id
    dispatches = Dispatch.objects.filter(tenant_id=tenant_id).select_related("paper").order_by("-created_at")
    packets = Packet.objects.filter(tenant_id=tenant_id).select_related("dispatch").order_by("-created_at")
    bundles = Bundle.objects.filter(tenant_id=tenant_id).select_related("packet").order_by("-created_at")
    exceptions = ReceivingException.objects.filter(tenant_id=tenant_id).select_related("dispatch", "packet", "bundle").order_by("-created_at")
    alerts = ReceivingAlert.objects.filter(tenant_id=tenant_id).select_related("dispatch", "packet").order_by("-detected_at")
    return {
        "dispatches": [{"id": str(item.id), "reference": item.reference, "paper": item.paper.code, "source_centre": item.source_centre, "expected_packets": item.expected_packets, "received_packets": item.received_packets, "expected_scripts": item.expected_scripts, "received_scripts": item.received_scripts, "manifest_reference": item.manifest_reference, "carrier": item.carrier, "expected_arrival_at": item.expected_arrival_at.isoformat() if item.expected_arrival_at else None, "status": item.status, "version": item.version} for item in dispatches],
        "packets": [{"id": str(item.id), "dispatch_id": str(item.dispatch_id), "dispatch": item.dispatch.reference, "barcode": item.barcode, "seal_number": item.seal_number, "expected_scripts": item.expected_scripts, "received_scripts": item.received_scripts, "condition": item.condition, "status": item.status, "version": item.version} for item in packets],
        "bundles": [{"id": str(item.id), "packet_id": str(item.packet_id), "packet": item.packet.barcode, "barcode": item.barcode, "expected_scripts": item.expected_scripts, "received_scripts": item.received_scripts, "condition": item.condition, "status": item.status, "version": item.version} for item in bundles],
        "exceptions": [{"id": str(item.id), "dispatch": item.dispatch.reference, "packet": item.packet.barcode if item.packet else None, "bundle": item.bundle.barcode if item.bundle else None, "script_barcode": item.script_barcode, "kind": item.kind, "notes": item.notes, "status": item.status, "version": item.version, "cleared_at": item.cleared_at.isoformat() if item.cleared_at else None} for item in exceptions],
        "alerts": [{"id": str(item.id), "dispatch": item.dispatch.reference, "packet": item.packet.barcode if item.packet else None, "kind": item.kind, "message": item.message, "detected_at": item.detected_at.isoformat(), "acknowledged_at": item.acknowledged_at.isoformat() if item.acknowledged_at else None} for item in alerts],
    }


@router.post("/dispatches")
def register_dispatch(request, payload: DispatchCreateIn):
    membership = require_roles(request, *RECEIVING_ROLES)
    tenant_id = membership.institution.tenant_id
    custom_fields = validate_custom_values(tenant_id=tenant_id, form_key="dispatch", values=payload.custom_fields)
    paper = Paper.objects.filter(id=payload.paper_id, tenant_id=tenant_id).first()
    if not paper:
        raise HttpError(404, "Paper not found")
    dispatch = create_dispatch(tenant_id=tenant_id, actor=request.auth, paper=paper, payload=payload)
    persist_custom_values(tenant_id=tenant_id, actor_id=request.auth.id, form_key="dispatch", record_id=dispatch.id, values=custom_fields)
    return {"id": str(dispatch.id), "status": dispatch.status, "version": dispatch.version}


@router.post("/dispatches/{dispatch_id}/verify")
def dispatch_verify(request, dispatch_id: str, payload: DispatchVerifyIn):
    membership = require_roles(request, Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER)
    dispatch = verify_dispatch(tenant_id=membership.institution.tenant_id, actor=request.auth, dispatch_id=dispatch_id, version=payload.version, dispatched_at=payload.dispatched_at)
    return {"id": str(dispatch.id), "status": dispatch.status, "version": dispatch.version}


@router.post("/dispatches/{dispatch_id}/packets")
def register_packet(request, dispatch_id: str, payload: PacketCreateIn):
    membership = require_roles(request, *RECEIVING_ROLES)
    dispatch = Dispatch.objects.filter(id=dispatch_id, tenant_id=membership.institution.tenant_id).first()
    if not dispatch:
        raise HttpError(404, "Dispatch not found")
    if dispatch.status != Dispatch.Status.REGISTERED or dispatch.packets.count() >= dispatch.expected_packets:
        raise HttpError(409, "Dispatch is not accepting additional packets")
    try:
        with transaction.atomic():
            packet = Packet.objects.create(tenant_id=membership.institution.tenant_id, dispatch=dispatch, barcode=payload.barcode, expected_scripts=payload.expected_scripts, seal_number=payload.seal_number)
            record_event(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, action="receiving.packet.registered", aggregate="Packet", aggregate_id=packet.id, payload={"dispatch_id": str(dispatch.id), "barcode": packet.barcode})
    except IntegrityError as exc:
        raise HttpError(409, "Packet barcode already exists") from exc
    return {"id": str(packet.id), "status": packet.status, "version": packet.version}


@router.post("/packets/{packet_id}/receive")
def packet_receive(request, packet_id: str, payload: PacketReceiveIn):
    membership = require_roles(request, *RECEIVING_ROLES)
    packet = receive_packet(tenant_id=membership.institution.tenant_id, actor=request.auth, packet_id=packet_id, version=payload.version, received_scripts=payload.received_scripts, condition=payload.condition, handed_over_by=payload.handed_over_by)
    return {"id": str(packet.id), "status": packet.status, "version": packet.version}


@router.post("/packets/{packet_id}/bundles")
def register_bundle(request, packet_id: str, payload: BundleCreateIn):
    membership = require_roles(request, *RECEIVING_ROLES)
    packet = Packet.objects.filter(id=packet_id, tenant_id=membership.institution.tenant_id).first()
    if not packet:
        raise HttpError(404, "Packet not found")
    try:
        with transaction.atomic():
            bundle = Bundle.objects.create(tenant_id=membership.institution.tenant_id, packet=packet, barcode=payload.barcode, expected_scripts=payload.expected_scripts)
            record_event(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, action="receiving.bundle.registered", aggregate="Bundle", aggregate_id=bundle.id, payload={"packet_id": str(packet.id), "barcode": bundle.barcode})
    except IntegrityError as exc:
        raise HttpError(409, "Bundle barcode already exists") from exc
    return {"id": str(bundle.id), "status": bundle.status, "version": bundle.version}


@router.post("/bundles/{bundle_id}/receive")
def bundle_receive(request, bundle_id: str, payload: BundleReceiveIn):
    membership = require_roles(request, *RECEIVING_ROLES)
    bundle = receive_bundle(tenant_id=membership.institution.tenant_id, actor=request.auth, bundle_id=bundle_id, version=payload.version, received_scripts=payload.received_scripts, condition=payload.condition)
    return {"id": str(bundle.id), "status": bundle.status, "version": bundle.version}


@router.post("/dispatches/{dispatch_id}/reconcile")
def dispatch_reconcile(request, dispatch_id: str, payload: VersionIn):
    membership = require_roles(request, *RECEIVING_ROLES)
    dispatch = reconcile_dispatch(tenant_id=membership.institution.tenant_id, actor=request.auth, dispatch_id=dispatch_id, version=payload.version)
    return {"id": str(dispatch.id), "status": dispatch.status, "version": dispatch.version, "received_packets": dispatch.received_packets, "received_scripts": dispatch.received_scripts}


@router.post("/dispatches/{dispatch_id}/confirm")
def dispatch_confirm(request, dispatch_id: str, payload: ConfirmationIn):
    membership = require_roles(request, *RECEIVING_ROLES)
    dispatch = Dispatch.objects.filter(id=dispatch_id, tenant_id=membership.institution.tenant_id).first()
    if not dispatch:
        raise HttpError(404, "Dispatch not found")
    confirmation = confirm_receipt(tenant_id=membership.institution.tenant_id, actor=request.auth, dispatch=dispatch, confirmation_type=payload.confirmation_type, notes=payload.notes)
    return {"id": str(confirmation.id), "confirmation_type": confirmation.confirmation_type}


@router.post("/dispatches/{dispatch_id}/close")
def dispatch_close(request, dispatch_id: str, payload: VersionIn):
    membership = require_roles(request, Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER)
    dispatch = close_dispatch(tenant_id=membership.institution.tenant_id, actor=request.auth, dispatch_id=dispatch_id, version=payload.version)
    return {"id": str(dispatch.id), "status": dispatch.status, "version": dispatch.version}


@router.post("/exceptions")
def create_exception(request, payload: ExceptionCreateIn):
    membership = require_roles(request, *RECEIVING_ROLES)
    tenant_id = membership.institution.tenant_id
    dispatch = Dispatch.objects.filter(id=payload.dispatch_id, tenant_id=tenant_id).first()
    packet = Packet.objects.filter(id=payload.packet_id, tenant_id=tenant_id).first() if payload.packet_id else None
    bundle = Bundle.objects.filter(id=payload.bundle_id, tenant_id=tenant_id).first() if payload.bundle_id else None
    if not dispatch or payload.kind not in ReceivingException.Kind.values:
        raise HttpError(422, "A valid dispatch and exception type are required")
    if packet and packet.dispatch_id != dispatch.id or bundle and bundle.packet.dispatch_id != dispatch.id:
        raise HttpError(422, "Exception records must belong to the selected dispatch")
    with transaction.atomic():
        exception = ReceivingException.objects.create(tenant_id=tenant_id, dispatch=dispatch, packet=packet, bundle=bundle, kind=payload.kind, script_barcode=payload.script_barcode, notes=payload.notes)
        record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="receiving.exception.created", aggregate="ReceivingException", aggregate_id=exception.id, payload={"kind": exception.kind, "dispatch_id": str(dispatch.id)})
    return {"id": str(exception.id), "status": exception.status, "version": exception.version}


@router.post("/exceptions/{exception_id}/review")
def review_exception(request, exception_id: str, payload: ExceptionReviewIn):
    membership = require_roles(request, Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER)
    with transaction.atomic():
        item = ReceivingException.objects.select_for_update().filter(id=exception_id, tenant_id=membership.institution.tenant_id).first()
        if not item or item.version != payload.version:
            raise HttpError(409, "Receiving exception is missing or stale")
        if item.status != "open":
            raise HttpError(409, "Only an open exception can enter supervisor review")
        item.status = "reviewed"
        item.supervisor_id = request.auth.id
        item.supervisor_note = payload.supervisor_note
        item.reviewed_at = timezone.now()
        item.version += 1
        item.save()
        record_event(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, action="receiving.exception.reviewed", aggregate="ReceivingException", aggregate_id=item.id, payload={"kind": item.kind})
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/exceptions/{exception_id}/reconcile")
def reconcile_exception(request, exception_id: str, payload: ExceptionReconcileIn):
    membership = require_roles(request, *RECEIVING_ROLES)
    with transaction.atomic():
        item = ReceivingException.objects.select_for_update().filter(id=exception_id, tenant_id=membership.institution.tenant_id).first()
        if not item or item.version != payload.version:
            raise HttpError(409, "Receiving exception is missing or stale")
        if item.status != "reviewed":
            raise HttpError(409, "Supervisor review is required before reconciliation")
        item.status = "reconciled"
        item.reconciliation_note = payload.reconciliation_note
        item.version += 1
        item.save()
        record_event(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, action="receiving.exception.reconciled", aggregate="ReceivingException", aggregate_id=item.id, payload={"kind": item.kind})
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/exceptions/{exception_id}/clear")
def clear_exception(request, exception_id: str, payload: ExceptionClearIn):
    membership = require_roles(request, Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER)
    with transaction.atomic():
        item = ReceivingException.objects.select_for_update().filter(id=exception_id, tenant_id=membership.institution.tenant_id).first()
        if not item or item.version != payload.version:
            raise HttpError(409, "Receiving exception is missing or stale")
        if item.status != "reconciled":
            raise HttpError(409, "Manual reconciliation is required before clearance")
        item.status = "cleared"
        item.cleared_at = timezone.now()
        item.cleared_by_id = request.auth.id
        item.reconciliation_note = f"{item.reconciliation_note}\nClearance: {payload.clearance_note}".strip()
        item.version += 1
        item.save()
        record_event(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, action="receiving.exception.cleared", aggregate="ReceivingException", aggregate_id=item.id, payload={"kind": item.kind})
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/alerts/detect-delays")
def delay_detection(request):
    membership = require_roles(request, *RECEIVING_ROLES)
    return {"alerts_created": detect_transit_delays(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id)}
