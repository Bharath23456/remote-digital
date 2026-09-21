"""Manifest-led intake. Legacy receiving endpoints remain available for old records."""

import hashlib
import hmac
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from ninja import File, Router
from ninja.errors import HttpError
from ninja.files import UploadedFile

from apps.anonymisation.services import confirm_identity_storage, prepare_identity_link
from apps.configuration.models import Paper
from apps.core.authz import require_roles
from apps.core.services import record_event
from apps.custody.models import Script
from apps.custody.services import register_script
from apps.tenancy.models import Membership

from .models import Dispatch, Packet
from .omr import RecognitionError, recognize_cover
from .schemas import GuidedBundleIn, GuidedPacketScanIn, GuidedScanIn


router = Router(tags=["Guided script intake"])
SUPERVISORS = (Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER)
PREPARERS = SUPERVISORS + (Membership.Role.BUNDLE_PREPARER,)
RECEIVERS = SUPERVISORS + (Membership.Role.INTAKE_RECEIVER,)
SCANNERS = SUPERVISORS + (Membership.Role.SCAN_OPERATOR,)
READERS = SUPERVISORS + (Membership.Role.BUNDLE_PREPARER, Membership.Role.INTAKE_RECEIVER, Membership.Role.SCAN_OPERATOR)
BARCODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$")


def _enabled():
    if not settings.DEMO_MANUAL_INTAKE_ENABLED:
        raise HttpError(403, "Manual intake is disabled on this deployment")


def _code(value):
    result = value.strip().upper()
    if not BARCODE.fullmatch(result):
        raise HttpError(422, "Use a barcode of 3-64 letters, digits, dots, hyphens, underscores or colons")
    return result


def _masked(code):
    return hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()[:12].upper()


def _bundle(tenant_id, barcode):
    item = Dispatch.objects.filter(tenant_id=tenant_id, reference=_code(barcode)).exclude(intake_mode=Dispatch.IntakeMode.LEGACY).first()
    if not item:
        raise HttpError(404, "Bundle barcode is not in this university's manifest")
    return item


def _packet_row(packet):
    scanned = {item.primary_barcode for item in packet.scripts.all() if item.state != Script.State.REGISTERED}
    return {"id": str(packet.id), "barcode": packet.barcode, "subject": packet.paper.code if packet.paper else "", "status": packet.status, "expected_scripts": packet.expected_scripts, "scanned_scripts": len(scanned), "missing_count": len(set(packet.script_manifest) - scanned), "missing_references": [_masked(code) for code in packet.script_manifest if code not in scanned]}


def _bundle_row(bundle):
    packets = [_packet_row(packet) for packet in bundle.packets.all()]
    return {"id": str(bundle.id), "barcode": bundle.reference, "source_centre": bundle.source_centre, "mode": bundle.intake_mode, "status": bundle.status, "expected_packets": bundle.expected_packets, "received_packets": sum(item["status"] != "registered" for item in packets), "expected_scripts": bundle.expected_scripts, "scanned_scripts": sum(item["scanned_scripts"] for item in packets), "packets": packets}


@router.get("/catalog")
def catalog(request):
    _enabled()
    tenant_id = require_roles(request, *READERS).institution.tenant_id
    bundles = Dispatch.objects.filter(tenant_id=tenant_id).exclude(intake_mode=Dispatch.IntakeMode.LEGACY).prefetch_related("packets__paper", "packets__scripts").order_by("-created_at")[:100]
    return {"enabled": True, "bundles": [_bundle_row(bundle) for bundle in bundles]}


@router.get("/lookup/bundles/{barcode}")
def bundle_by_barcode(request, barcode: str):
    _enabled()
    tenant_id = require_roles(request, *RECEIVERS).institution.tenant_id
    bundle = Dispatch.objects.filter(tenant_id=tenant_id, reference=_code(barcode)).exclude(intake_mode=Dispatch.IntakeMode.LEGACY).prefetch_related("packets__paper", "packets__scripts").first()
    if not bundle:
        raise HttpError(404, "Bundle barcode is not in this university's manifest")
    return _bundle_row(bundle)


@router.get("/lookup/packets/{barcode}")
def packet_by_barcode(request, barcode: str):
    _enabled()
    tenant_id = require_roles(request, *SCANNERS).institution.tenant_id
    packet = Packet.objects.filter(tenant_id=tenant_id, barcode=_code(barcode)).exclude(dispatch__intake_mode=Dispatch.IntakeMode.LEGACY).select_related("paper", "dispatch").prefetch_related("scripts").first()
    if not packet or packet.status not in ("received", "scanning", "complete"):
        raise HttpError(404, "Packet is not received or its barcode is not in this university's manifest")
    return {**_packet_row(packet), "bundle": packet.dispatch.reference}


@router.get("/papers")
def intake_papers(request):
    _enabled()
    tenant_id = require_roles(request, *PREPARERS).institution.tenant_id
    return {"papers": list(Paper.objects.filter(tenant_id=tenant_id).order_by("code").values("id", "code", "title"))}


@router.post("/bundles")
def create_bundle(request, payload: GuidedBundleIn):
    _enabled()
    membership = require_roles(request, *PREPARERS)
    tenant_id = membership.institution.tenant_id
    if payload.mode not in (Dispatch.IntakeMode.TRANSFER, Dispatch.IntakeMode.ON_SITE) or not 1 <= len(payload.packets) <= 100:
        raise HttpError(422, "Choose transfer or on-site scanning and add 1-100 packets")
    bundle_code = _code(payload.barcode)
    packet_codes = [_code(item.barcode) for item in payload.packets]
    script_codes = [_code(code) for item in payload.packets for code in item.script_barcodes]
    if len(set(packet_codes)) != len(packet_codes) or len(set(script_codes)) != len(script_codes):
        raise HttpError(422, "Packet and script QR codes must be unique in the manifest")
    if any(not 1 <= len(item.script_barcodes) <= 500 for item in payload.packets):
        raise HttpError(422, "Each packet needs 1-500 script QR codes")
    if not payload.source_centre.strip() or len(payload.source_centre) > 120:
        raise HttpError(422, "A source centre is required")
    papers = {str(item.id): item for item in Paper.objects.filter(tenant_id=tenant_id, id__in=[p.paper_id for p in payload.packets])}
    if any(item.paper_id not in papers for item in payload.packets):
        raise HttpError(422, "Select a valid paper for every packet")
    if Dispatch.objects.filter(tenant_id=tenant_id, reference=bundle_code).exists() or Packet.objects.filter(barcode__in=packet_codes).exists() or Script.objects.filter(primary_barcode__in=script_codes).exists():
        raise HttpError(409, "A bundle, packet or script QR code is already registered")
    try:
        with transaction.atomic():
            bundle = Dispatch.objects.create(tenant_id=tenant_id, reference=bundle_code, intake_mode=payload.mode, paper=papers[payload.packets[0].paper_id], source_centre=payload.source_centre.strip(), expected_packets=len(payload.packets), expected_scripts=len(script_codes), manifest_reference=bundle_code, registered_by=request.auth)
            for item, packet_code in zip(payload.packets, packet_codes):
                Packet.objects.create(tenant_id=tenant_id, dispatch=bundle, barcode=packet_code, paper=papers[item.paper_id], expected_scripts=len(item.script_barcodes), script_manifest=[_code(code) for code in item.script_barcodes])
            record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="receiving.guided.manifest_created", aggregate="Dispatch", aggregate_id=bundle.id, payload={"mode": payload.mode, "packets": len(payload.packets), "scripts": len(script_codes)})
    except IntegrityError as exc:
        raise HttpError(409, "A manifest barcode was registered concurrently") from exc
    return {"id": str(bundle.id), "status": bundle.status}


@router.post("/bundles/start")
def start_bundle(request, payload: GuidedScanIn):
    _enabled()
    membership = require_roles(request, *PREPARERS)
    tenant_id = membership.institution.tenant_id
    with transaction.atomic():
        bundle = Dispatch.objects.select_for_update().filter(tenant_id=tenant_id, reference=_code(payload.barcode)).exclude(intake_mode=Dispatch.IntakeMode.LEGACY).first()
        if not bundle:
            raise HttpError(404, "Bundle not found")
        if bundle.status != Dispatch.Status.REGISTERED:
            raise HttpError(409, "This bundle has already started")
        bundle.status = Dispatch.Status.IN_TRANSIT if bundle.intake_mode == Dispatch.IntakeMode.TRANSFER else Dispatch.Status.ON_SITE
        bundle.dispatched_at = timezone.now()
        bundle.version += 1
        bundle.save(update_fields=["status", "dispatched_at", "version", "updated_at"])
        record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="receiving.guided.bundle_started", aggregate="Dispatch", aggregate_id=bundle.id, payload={"mode": bundle.intake_mode})
    return {"status": bundle.status}


@router.post("/bundles/receive")
def receive_guided_bundle(request, payload: GuidedScanIn):
    _enabled()
    membership = require_roles(request, *RECEIVERS)
    tenant_id = membership.institution.tenant_id
    with transaction.atomic():
        bundle = Dispatch.objects.select_for_update().filter(tenant_id=tenant_id, reference=_code(payload.barcode), intake_mode=Dispatch.IntakeMode.TRANSFER).first()
        if not bundle:
            raise HttpError(404, "Transferred bundle not found")
        if bundle.status != Dispatch.Status.IN_TRANSIT:
            raise HttpError(409, "Bundle is not in transit")
        bundle.status = Dispatch.Status.RECEIVED
        bundle.received_at = timezone.now()
        bundle.version += 1
        bundle.save(update_fields=["status", "received_at", "version", "updated_at"])
        record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="receiving.guided.bundle_received", aggregate="Dispatch", aggregate_id=bundle.id, payload={})
    return {"status": bundle.status, "expected_packets": bundle.expected_packets}


@router.post("/packets/receive")
def receive_guided_packet(request, payload: GuidedPacketScanIn):
    _enabled()
    membership = require_roles(request, *RECEIVERS)
    tenant_id = membership.institution.tenant_id
    with transaction.atomic():
        packet = Packet.objects.select_for_update().select_related("dispatch").filter(tenant_id=tenant_id, barcode=_code(payload.barcode)).exclude(dispatch__intake_mode=Dispatch.IntakeMode.LEGACY).first()
        if not packet:
            raise HttpError(404, "Packet barcode is not in a guided bundle manifest")
        if packet.dispatch.reference != _code(payload.bundle_barcode):
            raise HttpError(409, "This packet belongs to a different bundle")
        if packet.dispatch.intake_mode == Dispatch.IntakeMode.TRANSFER and packet.dispatch.status != Dispatch.Status.RECEIVED:
            raise HttpError(409, "Receive the outer bundle before opening packets")
        if packet.dispatch.intake_mode == Dispatch.IntakeMode.ON_SITE and packet.dispatch.status != Dispatch.Status.ON_SITE:
            raise HttpError(409, "Start on-site scanning before opening packets")
        if packet.status != "registered":
            raise HttpError(409, "Packet has already been received")
        packet.status = "received"
        packet.received_at = timezone.now()
        packet.received_by = request.auth
        packet.version += 1
        packet.save(update_fields=["status", "received_at", "received_by", "version", "updated_at"])
        bundle = Dispatch.objects.select_for_update().get(id=packet.dispatch_id)
        bundle.received_packets += 1
        bundle.version += 1
        bundle.save(update_fields=["received_packets", "version", "updated_at"])
        record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="receiving.guided.packet_received", aggregate="Packet", aggregate_id=packet.id, payload={"bundle_id": str(bundle.id)})
    return {"status": packet.status, "subject": packet.paper.code, "expected_scripts": packet.expected_scripts}


@router.post("/packets/{packet_id}/recognize")
def recognize_script(request, packet_id: str, cover: UploadedFile = File(...)):
    _enabled()
    membership = require_roles(request, *SCANNERS)
    tenant_id = membership.institution.tenant_id
    packet = Packet.objects.filter(id=packet_id, tenant_id=tenant_id).select_related("dispatch", "paper").first()
    if not packet or packet.dispatch.intake_mode == Dispatch.IntakeMode.LEGACY:
        raise HttpError(404, "Guided packet not found")
    if packet.status not in ("received", "scanning", "complete"):
        raise HttpError(409, "Receive the packet before scanning scripts")
    content = cover.read(12_000_001)
    cover_hash = hashlib.sha256(content).hexdigest()
    try:
        qr, usn = recognize_cover(content)
    except RecognitionError as exc:
        raise HttpError(422, str(exc)) from exc
    qr = _code(qr)
    if qr not in packet.script_manifest:
        raise HttpError(409, "Booklet QR does not belong to this packet; isolate it and check the manifest")
    existing = Script.objects.filter(primary_barcode=qr).first()
    if existing and (existing.tenant_id != tenant_id or existing.packet_id != packet.id):
        raise HttpError(409, "Booklet QR was already registered in another packet")
    if existing and existing.state != Script.State.REGISTERED:
        raise HttpError(409, "Booklet QR was already processed")
    if existing and existing.recognized_cover_sha256 and existing.recognized_cover_sha256 != cover_hash:
        raise HttpError(409, "This QR was already linked to a different front-page image")
    script = existing or register_script(tenant_id=tenant_id, actor_id=request.auth.id, packet=packet, primary_barcode=qr, supplements=[], bundle_barcode=packet.dispatch.reference, centre_barcode="", location="Guided manual scan")
    if not script.recognized_cover_sha256:
        script.recognized_cover_sha256 = cover_hash
        script.save(update_fields=["recognized_cover_sha256", "updated_at"])
    if packet.status == "received":
        packet.status = "scanning"
        packet.save(update_fields=["status", "updated_at"])
    if not getattr(script, "identity_link", None) or not script.identity_link.stored_at:
        link, token, _ = prepare_identity_link(tenant_id=tenant_id, actor_id=request.auth.id, script=script, purpose="Automated OMR intake", session_id=script.paper.session_id, institution_name=membership.institution.name)
        body = json.dumps({"candidate_name": "", "register_number": usn, "usn": usn}).encode()
        target = settings.IDENTITY_SERVICE_URL.rstrip("/") + "/v1/candidates"
        if not settings.IDENTITY_SERVICE_URL:
            raise HttpError(503, "Identity service is unavailable; the script can be retried")
        try:
            response = urlopen(Request(target, data=body, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="POST"), timeout=8)
            receipt = json.load(response)["receipt"]
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError) as exc:
            raise HttpError(503, "Identity service could not store OMR data; retry this script") from exc
        confirm_identity_storage(tenant_id=tenant_id, actor_id=request.auth.id, link_id=link.id, version=link.version, receipt=receipt)
    return {"script_id": str(script.id), "script_code": script.script_code, "version": script.version, "subject": script.paper.code, "identity_linked": True}
