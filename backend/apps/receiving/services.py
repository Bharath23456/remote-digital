from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from ninja.errors import HttpError

from apps.core.services import record_event

from .models import Bundle, Dispatch, Packet, ReceiptConfirmation, ReceivingAlert, ReceivingException


def create_dispatch(*, tenant_id, actor, paper, payload):
    if payload.expected_packets < 1 or payload.expected_scripts < 1:
        raise HttpError(422, "Expected packet and script counts must be positive")
    try:
        with transaction.atomic():
            dispatch = Dispatch.objects.create(
                tenant_id=tenant_id,
                reference=payload.reference,
                paper=paper,
                source_centre=payload.source_centre,
                expected_packets=payload.expected_packets,
                expected_scripts=payload.expected_scripts,
                manifest_reference=payload.manifest_reference,
                manifest_sha256=payload.manifest_sha256,
                carrier=payload.carrier,
                expected_arrival_at=payload.expected_arrival_at,
                registered_by=actor,
            )
            record_event(tenant_id=tenant_id, actor_id=actor.id, action="receiving.dispatch.registered", aggregate="Dispatch", aggregate_id=dispatch.id, payload={"reference": dispatch.reference, "expected_scripts": dispatch.expected_scripts})
    except IntegrityError as exc:
        raise HttpError(409, "Dispatch reference already exists") from exc
    return dispatch


def verify_dispatch(*, tenant_id, actor, dispatch_id, version, dispatched_at=None):
    with transaction.atomic():
        dispatch = Dispatch.objects.select_for_update().filter(id=dispatch_id, tenant_id=tenant_id).first()
        if not dispatch:
            raise HttpError(404, "Dispatch not found")
        if dispatch.version != version:
            raise HttpError(409, "Dispatch was changed by another user")
        if dispatch.status != Dispatch.Status.REGISTERED:
            raise HttpError(409, "Only a registered dispatch can be verified")
        if dispatch.packets.count() != dispatch.expected_packets:
            raise HttpError(409, "Registered packet count does not match the dispatch manifest")
        packet_scripts = dispatch.packets.aggregate(total=Sum("expected_scripts"))["total"] or 0
        if packet_scripts != dispatch.expected_scripts:
            raise HttpError(409, "Packet script totals do not match the dispatch manifest")
        dispatch.status = Dispatch.Status.IN_TRANSIT
        dispatch.verified_by = actor
        dispatch.verified_at = timezone.now()
        dispatch.dispatched_at = dispatched_at or timezone.now()
        dispatch.version += 1
        dispatch.save()
        record_event(tenant_id=tenant_id, actor_id=actor.id, action="receiving.dispatch.verified", aggregate="Dispatch", aggregate_id=dispatch.id, payload={"reference": dispatch.reference})
    return dispatch


def receive_packet(*, tenant_id, actor, packet_id, version, received_scripts, condition, handed_over_by):
    with transaction.atomic():
        packet = Packet.objects.select_for_update().select_related("dispatch").filter(id=packet_id, tenant_id=tenant_id).first()
        if not packet:
            raise HttpError(404, "Packet not found")
        if packet.version != version:
            raise HttpError(409, "Packet was changed by another user")
        if packet.status not in ("registered", "in_transit"):
            raise HttpError(409, "Packet is not awaiting receipt")
        packet.received_scripts = received_scripts
        packet.condition = condition
        packet.handed_over_by = handed_over_by
        packet.received_by = actor
        packet.received_at = timezone.now()
        packet.status = "received" if received_scripts == packet.expected_scripts and condition == "intact" else "exception"
        packet.version += 1
        packet.save()
        if packet.status == "exception":
            kind = ReceivingException.Kind.DISCREPANCY if received_scripts != packet.expected_scripts else ReceivingException.Kind.DAMAGED
            ReceivingException.objects.create(tenant_id=tenant_id, dispatch=packet.dispatch, packet=packet, kind=kind, notes=f"Received {received_scripts}/{packet.expected_scripts}; condition {condition}")
        record_event(tenant_id=tenant_id, actor_id=actor.id, action="receiving.packet.received", aggregate="Packet", aggregate_id=packet.id, payload={"status": packet.status, "received_scripts": received_scripts, "condition": condition})
    return packet


def receive_bundle(*, tenant_id, actor, bundle_id, version, received_scripts, condition):
    with transaction.atomic():
        bundle = Bundle.objects.select_for_update().select_related("packet__dispatch").filter(id=bundle_id, tenant_id=tenant_id).first()
        if not bundle:
            raise HttpError(404, "Bundle not found")
        if bundle.version != version:
            raise HttpError(409, "Bundle was changed by another user")
        if bundle.status != "registered":
            raise HttpError(409, "Bundle is not awaiting receipt")
        bundle.received_scripts = received_scripts
        bundle.condition = condition
        bundle.received_by = actor
        bundle.received_at = timezone.now()
        bundle.status = "received" if received_scripts == bundle.expected_scripts and condition == "intact" else "exception"
        bundle.version += 1
        bundle.save()
        if bundle.status == "exception":
            kind = ReceivingException.Kind.DISCREPANCY if received_scripts != bundle.expected_scripts else ReceivingException.Kind.DAMAGED
            ReceivingException.objects.create(tenant_id=tenant_id, dispatch=bundle.packet.dispatch, packet=bundle.packet, bundle=bundle, kind=kind, notes=f"Received {received_scripts}/{bundle.expected_scripts}; condition {condition}")
        record_event(tenant_id=tenant_id, actor_id=actor.id, action="receiving.bundle.received", aggregate="Bundle", aggregate_id=bundle.id, payload={"status": bundle.status, "received_scripts": received_scripts})
    return bundle


def reconcile_dispatch(*, tenant_id, actor, dispatch_id, version):
    with transaction.atomic():
        dispatch = Dispatch.objects.select_for_update().filter(id=dispatch_id, tenant_id=tenant_id).first()
        if not dispatch:
            raise HttpError(404, "Dispatch not found")
        if dispatch.version != version:
            raise HttpError(409, "Dispatch was changed by another user")
        totals = dispatch.packets.aggregate(packets=Sum("received_scripts"))
        packet_count = dispatch.packets.filter(received_at__isnull=False).count()
        dispatch.received_packets = packet_count
        dispatch.received_scripts = totals["packets"] or 0
        exact = packet_count == dispatch.expected_packets and dispatch.received_scripts == dispatch.expected_scripts
        no_packet_exceptions = not dispatch.packets.filter(status="exception").exists()
        dispatch.status = Dispatch.Status.RECONCILED if exact and no_packet_exceptions else Dispatch.Status.EXCEPTION
        dispatch.received_at = timezone.now()
        dispatch.version += 1
        dispatch.save()
        if not exact:
            ReceivingAlert.objects.get_or_create(tenant_id=tenant_id, dispatch=dispatch, packet=None, kind=ReceivingAlert.Kind.COUNT_MISMATCH, acknowledged_at=None, defaults={"message": f"Received {packet_count}/{dispatch.expected_packets} packets and {dispatch.received_scripts}/{dispatch.expected_scripts} scripts"})
        record_event(tenant_id=tenant_id, actor_id=actor.id, action="receiving.dispatch.reconciled", aggregate="Dispatch", aggregate_id=dispatch.id, payload={"status": dispatch.status, "received_packets": packet_count, "received_scripts": dispatch.received_scripts})
    return dispatch


def confirm_receipt(*, tenant_id, actor, dispatch, confirmation_type, notes):
    allowed = {"handover", "receiver", "receipt"}
    if confirmation_type not in allowed:
        raise HttpError(422, "Unsupported confirmation type")
    with transaction.atomic():
        confirmation = ReceiptConfirmation.objects.create(tenant_id=tenant_id, dispatch=dispatch, confirmation_type=confirmation_type, actor=actor, notes=notes)
        record_event(tenant_id=tenant_id, actor_id=actor.id, action=f"receiving.{confirmation_type}.confirmed", aggregate="ReceiptConfirmation", aggregate_id=confirmation.id, payload={"dispatch_id": str(dispatch.id)})
    return confirmation


def close_dispatch(*, tenant_id, actor, dispatch_id, version):
    with transaction.atomic():
        dispatch = Dispatch.objects.select_for_update().filter(id=dispatch_id, tenant_id=tenant_id).first()
        if not dispatch or dispatch.version != version:
            raise HttpError(409, "Dispatch is missing or stale")
        if dispatch.status != Dispatch.Status.RECONCILED:
            raise HttpError(409, "Only a reconciled dispatch can close physical custody")
        if dispatch.exceptions.filter(cleared_at__isnull=True).exists():
            raise HttpError(409, "All receiving exceptions must be cleared before closure")
        types = set(dispatch.confirmations.values_list("confirmation_type", flat=True))
        if not {"handover", "receiver", "receipt"}.issubset(types):
            raise HttpError(409, "Handover, receiver and receipt confirmations are required")
        dispatch.status = Dispatch.Status.CLOSED
        dispatch.closed_at = timezone.now()
        dispatch.version += 1
        dispatch.save()
        record_event(tenant_id=tenant_id, actor_id=actor.id, action="receiving.physical_custody.closed", aggregate="Dispatch", aggregate_id=dispatch.id, payload={"reference": dispatch.reference})
    return dispatch


def detect_transit_delays(*, tenant_id, actor_id):
    delayed = Dispatch.objects.filter(tenant_id=tenant_id, status=Dispatch.Status.IN_TRANSIT, expected_arrival_at__lt=timezone.now())
    created = 0
    with transaction.atomic():
        for dispatch in delayed:
            _, was_created = ReceivingAlert.objects.get_or_create(tenant_id=tenant_id, dispatch=dispatch, packet=None, kind=ReceivingAlert.Kind.TRANSIT_DELAY, acknowledged_at=None, defaults={"message": f"Dispatch {dispatch.reference} passed its expected arrival time"})
            created += int(was_created)
        if created:
            record_event(tenant_id=tenant_id, actor_id=actor_id, action="receiving.transit_delays.detected", aggregate="Dispatch", aggregate_id="batch", payload={"alerts_created": created})
    return created
