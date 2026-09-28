"""Manifest-led intake. Legacy receiving endpoints remain available for old records."""

import hashlib
import hmac
import json
import re
from uuid import uuid4
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
from apps.phase4.models import CentreProfile
from apps.tenancy.models import Institution, Membership

from .models import Dispatch, Packet, PreparedPacket
from .omr import RecognitionError, read_cover_qr_if_present, recognize_cover
from .schemas import GuidedBundleIn, GuidedPacketScanIn, GuidedPreparedBundleIn, GuidedPreparedPacketIn, GuidedScanIn


router = Router(tags=["Guided script intake"])
SUPERVISORS = (Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER, Membership.Role.OPERATIONS_SUPERVISOR)
PREPARERS = SUPERVISORS + (Membership.Role.BUNDLE_PREPARER,)
RECEIVERS = SUPERVISORS + (Membership.Role.INTAKE_RECEIVER,)
SCANNERS = SUPERVISORS + (Membership.Role.SCAN_OPERATOR,)
READERS = SUPERVISORS + (Membership.Role.BUNDLE_PREPARER, Membership.Role.INTAKE_RECEIVER, Membership.Role.SCAN_OPERATOR)
BARCODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$")
MANUAL_USN = re.compile(r"^[A-Z0-9]{6,20}$")


def _enabled():
    if not settings.DEMO_MANUAL_INTAKE_ENABLED:
        raise HttpError(403, "Manual intake is disabled on this deployment")


def _code(value):
    result = value.strip().upper()
    if not BARCODE.fullmatch(result):
        raise HttpError(422, "Use a barcode of 3-64 letters, digits, dots, hyphens, underscores or colons")
    return result


def _generated_code(prefix, tenant_id):
    for _ in range(5):
        value = f"{prefix}-{timezone.now():%y%m%d}-{uuid4().hex[:10].upper()}"
        if prefix == "PKT":
            exists = Packet.objects.filter(barcode=value).exists() or PreparedPacket.objects.filter(barcode=value).exists()
        else:
            exists = Dispatch.objects.filter(tenant_id=tenant_id, reference=value).exists()
        if not exists:
            return value
    raise HttpError(503, "A unique QR label could not be generated; retry the operation")


def _assigned_operational_centre(membership):
    if not membership.operational_centre_id:
        return None
    return CentreProfile.objects.filter(
        id=membership.operational_centre_id,
        tenant_id=membership.institution.tenant_id,
        status=CentreProfile.Status.ACTIVE,
    ).first()


def _operational_centre(membership):
    centre = _assigned_operational_centre(membership)
    if not centre and not membership.operational_centre_id:
        raise HttpError(409, "Assign this user to an active centre in Access governance before continuing")
    if not centre:
        raise HttpError(409, "The assigned centre is not active; update it in Access governance before continuing")
    return centre


def _centre_row(centre):
    return {"id": str(centre.id), "code": centre.code, "name": centre.name}


def _centre_map(tenant_id, ids):
    return {
        item.id: _centre_row(item)
        for item in CentreProfile.objects.filter(tenant_id=tenant_id, id__in={value for value in ids if value})
    }


def _masked(code):
    return hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()[:12].upper()


def _bundle(tenant_id, barcode):
    item = Dispatch.objects.filter(tenant_id=tenant_id, reference=_code(barcode)).exclude(intake_mode=Dispatch.IntakeMode.LEGACY).first()
    if not item:
        raise HttpError(404, "Bundle barcode is not in this university's manifest")
    return item


def _packet_row(packet, centres=None):
    scanned = {item.primary_barcode for item in packet.scripts.all() if item.state != Script.State.REGISTERED}
    return {"id": str(packet.id), "barcode": packet.barcode, "qr_value": packet.barcode, "subject": packet.paper.code if packet.paper else "", "status": packet.status, "expected_scripts": packet.expected_scripts, "scanned_scripts": len(scanned), "missing_count": len(set(packet.script_manifest) - scanned), "missing_references": [_masked(code) for code in packet.script_manifest if code not in scanned], "received_centre": (centres or {}).get(packet.received_centre_id)}


def _bundle_row(bundle, centres=None):
    packets = [_packet_row(packet, centres) for packet in bundle.packets.all()]
    return {"id": str(bundle.id), "barcode": bundle.reference, "qr_value": bundle.reference, "source_centre": bundle.source_centre, "source_college_id": str(bundle.source_institution_id) if bundle.source_institution_id else None, "prepared_centre": (centres or {}).get(bundle.prepared_centre_id), "received_centre": (centres or {}).get(bundle.received_centre_id), "mode": bundle.intake_mode, "status": bundle.status, "expected_packets": bundle.expected_packets, "received_packets": sum(item["status"] != "registered" for item in packets), "expected_scripts": bundle.expected_scripts, "scanned_scripts": sum(item["scanned_scripts"] for item in packets), "packets": packets}


def _prepared_packet_row(packet, centres=None):
    return {
        "id": str(packet.id),
        "barcode": packet.barcode,
        "qr_value": packet.barcode,
        "paper_id": str(packet.paper_id),
        "subject": packet.paper.code,
        "paper_title": packet.paper.title,
        "source_college_id": str(packet.source_college_id),
        "source_college": packet.source_college.name,
        "prepared_centre": (centres or {}).get(packet.prepared_centre_id),
        "expected_scripts": len(packet.script_manifest),
        "status": packet.status,
        "version": packet.version,
        "created_at": packet.created_at.isoformat(),
    }


@router.get("/catalog")
def catalog(request):
    _enabled()
    membership = require_roles(request, *READERS)
    tenant_id = membership.institution.tenant_id
    centre = _operational_centre(membership)
    bundles = Dispatch.objects.filter(tenant_id=tenant_id).exclude(intake_mode=Dispatch.IntakeMode.LEGACY).prefetch_related("packets__paper", "packets__scripts").order_by("-created_at")[:100]
    centre_ids = [value for bundle in bundles for value in (bundle.prepared_centre_id, bundle.received_centre_id)]
    centre_ids.extend(packet.received_centre_id for bundle in bundles for packet in bundle.packets.all())
    centres = _centre_map(tenant_id, centre_ids)
    return {"enabled": True, "centre": _centre_row(centre), "bundles": [_bundle_row(bundle, centres) for bundle in bundles]}


@router.get("/preparation")
def preparation_catalog(request):
    _enabled()
    membership = require_roles(request, *PREPARERS)
    tenant_id = membership.institution.tenant_id
    centre = _assigned_operational_centre(membership)
    packets = (
        PreparedPacket.objects.filter(tenant_id=tenant_id, prepared_centre_id=centre.id)
        .select_related("paper", "source_college")
        .order_by("-created_at")[:200]
        if centre else []
    )
    centre_error = None
    if not centre:
        centre_error = (
            "The assigned centre is not active; update it in Access governance before continuing"
            if membership.operational_centre_id
            else "Assign this user to an active centre in Access governance before continuing"
        )
    return {
        "papers": list(Paper.objects.filter(tenant_id=tenant_id).order_by("code").values("id", "code", "title")),
        "colleges": list(
            Institution.objects.filter(
                tenant_id=tenant_id,
                kind=Institution.Kind.COLLEGE,
                is_active=True,
            ).order_by("name").values("id", "code", "name")
        ),
        "centre": _centre_row(centre) if centre else None,
        "centre_error": centre_error,
        "packets": [_prepared_packet_row(packet, {centre.id: _centre_row(centre)}) for packet in packets] if centre else [],
    }


@router.post("/prepared-packets")
def prepare_packet(request, payload: GuidedPreparedPacketIn):
    _enabled()
    membership = require_roles(request, *PREPARERS)
    tenant_id = membership.institution.tenant_id
    centre = _operational_centre(membership)
    packet_code = _code(payload.barcode) if payload.barcode else _generated_code("PKT", tenant_id)
    script_codes = [_code(code) for code in payload.script_barcodes]
    if not 1 <= len(script_codes) <= 500:
        raise HttpError(422, "A packet needs 1-500 booklet QR codes")
    if len(set(script_codes)) != len(script_codes):
        raise HttpError(422, "Each booklet QR can appear only once in a packet")
    paper = Paper.objects.filter(tenant_id=tenant_id, id=payload.paper_id).first()
    if not paper:
        raise HttpError(422, "Select a valid subject for the packet")
    college = Institution.objects.filter(
        tenant_id=tenant_id,
        id=payload.source_college_id,
        kind=Institution.Kind.COLLEGE,
        is_active=True,
    ).first()
    if not college:
        raise HttpError(422, "Select a college configured by the university administrator")
    if Packet.objects.filter(barcode=packet_code).exists() or PreparedPacket.objects.filter(barcode=packet_code).exists():
        raise HttpError(409, "Packet barcode is already registered")
    if Script.objects.filter(primary_barcode__in=script_codes).exists():
        raise HttpError(409, "A booklet QR is already registered")
    existing_manifests = PreparedPacket.objects.filter(tenant_id=tenant_id, status=PreparedPacket.Status.READY).values_list("script_manifest", flat=True)
    registered_manifests = Packet.objects.filter(tenant_id=tenant_id).values_list("script_manifest", flat=True)
    registered_codes = {code for manifest in list(existing_manifests) + list(registered_manifests) for code in manifest}
    if registered_codes.intersection(script_codes):
        raise HttpError(409, "A booklet QR already belongs to another packet")
    try:
        with transaction.atomic():
            prepared = PreparedPacket.objects.create(
                tenant_id=tenant_id,
                barcode=packet_code,
                paper=paper,
                source_college=college,
                prepared_centre_id=centre.id,
                script_manifest=script_codes,
                prepared_by=request.auth,
            )
            record_event(
                tenant_id=tenant_id,
                actor_id=request.auth.id,
                action="receiving.guided.packet_prepared",
                aggregate="PreparedPacket",
                aggregate_id=prepared.id,
                payload={"college_id": str(college.id), "paper_id": str(paper.id), "centre_id": str(centre.id), "scripts": len(script_codes)},
            )
    except IntegrityError as exc:
        raise HttpError(409, "Packet barcode was registered concurrently") from exc
    return _prepared_packet_row(prepared, {centre.id: _centre_row(centre)})


@router.post("/bundles/from-packets")
def create_bundle_from_packets(request, payload: GuidedPreparedBundleIn):
    _enabled()
    membership = require_roles(request, *PREPARERS)
    tenant_id = membership.institution.tenant_id
    centre = _operational_centre(membership)
    if payload.mode not in (Dispatch.IntakeMode.TRANSFER, Dispatch.IntakeMode.ON_SITE):
        raise HttpError(422, "Choose dispatch to university or on-site scanning")
    if not 1 <= len(payload.packet_ids) <= 100 or len(set(payload.packet_ids)) != len(payload.packet_ids):
        raise HttpError(422, "Select 1-100 different prepared packets")
    bundle_code = _code(payload.barcode) if payload.barcode else _generated_code("BND", tenant_id)
    college = Institution.objects.filter(
        tenant_id=tenant_id,
        id=payload.source_college_id,
        kind=Institution.Kind.COLLEGE,
        is_active=True,
    ).first()
    if not college:
        raise HttpError(422, "Select a college configured by the university administrator")
    if Dispatch.objects.filter(tenant_id=tenant_id, reference=bundle_code).exists():
        raise HttpError(409, "Bundle barcode is already registered")
    with transaction.atomic():
        selected = list(
            PreparedPacket.objects.select_for_update()
            .filter(tenant_id=tenant_id, id__in=payload.packet_ids)
            .select_related("paper", "source_college")
        )
        by_id = {str(packet.id): packet for packet in selected}
        if len(selected) != len(payload.packet_ids):
            raise HttpError(422, "One or more prepared packets were not found")
        packets = [by_id[packet_id] for packet_id in payload.packet_ids]
        if any(packet.status != PreparedPacket.Status.READY for packet in packets):
            raise HttpError(409, "One or more packets have already been bundled")
        if any(packet.source_college_id != college.id for packet in packets):
            raise HttpError(409, "Every packet in a bundle must come from the selected college")
        if any(packet.prepared_centre_id != centre.id for packet in packets):
            raise HttpError(409, "Every packet must be prepared at your assigned centre")
        if Packet.objects.filter(barcode__in=[packet.barcode for packet in packets]).exists():
            raise HttpError(409, "One or more packet barcodes are already registered")
        expected_scripts = sum(len(packet.script_manifest) for packet in packets)
        bundle = Dispatch.objects.create(
            tenant_id=tenant_id,
            reference=bundle_code,
            intake_mode=payload.mode,
            paper=packets[0].paper,
            source_centre=college.name,
            source_institution=college,
            prepared_centre_id=centre.id,
            expected_packets=len(packets),
            expected_scripts=expected_scripts,
            manifest_reference=bundle_code,
            registered_by=request.auth,
        )
        for prepared in packets:
            packet = Packet.objects.create(
                tenant_id=tenant_id,
                dispatch=bundle,
                barcode=prepared.barcode,
                paper=prepared.paper,
                expected_scripts=len(prepared.script_manifest),
                script_manifest=prepared.script_manifest,
            )
            prepared.status = PreparedPacket.Status.BUNDLED
            prepared.packet = packet
            prepared.bundled_at = timezone.now()
            prepared.version += 1
            prepared.save(update_fields=["status", "packet", "bundled_at", "version", "updated_at"])
        record_event(
            tenant_id=tenant_id,
            actor_id=request.auth.id,
            action="receiving.guided.manifest_created",
            aggregate="Dispatch",
            aggregate_id=bundle.id,
            payload={"mode": payload.mode, "college_id": str(college.id), "centre_id": str(centre.id), "packets": len(packets), "scripts": expected_scripts},
        )
    return {"id": str(bundle.id), "status": bundle.status, "barcode": bundle.reference, "qr_value": bundle.reference, "centre": _centre_row(centre)}


@router.get("/lookup/bundles/{barcode}")
def bundle_by_barcode(request, barcode: str):
    _enabled()
    membership = require_roles(request, *RECEIVERS)
    tenant_id = membership.institution.tenant_id
    centre = _operational_centre(membership)
    bundle = Dispatch.objects.filter(tenant_id=tenant_id, reference=_code(barcode)).exclude(intake_mode=Dispatch.IntakeMode.LEGACY).prefetch_related("packets__paper", "packets__scripts").first()
    if not bundle:
        raise HttpError(404, "Bundle barcode is not in this university's manifest")
    if bundle.received_centre_id and bundle.received_centre_id != centre.id:
        raise HttpError(409, "This bundle was received at a different centre")
    centre_ids = [bundle.prepared_centre_id, bundle.received_centre_id, *(packet.received_centre_id for packet in bundle.packets.all())]
    return _bundle_row(bundle, _centre_map(tenant_id, centre_ids))


@router.get("/lookup/packets/{barcode}")
def packet_by_barcode(request, barcode: str):
    _enabled()
    membership = require_roles(request, *SCANNERS)
    tenant_id = membership.institution.tenant_id
    centre = _operational_centre(membership)
    packet = Packet.objects.filter(tenant_id=tenant_id, barcode=_code(barcode)).exclude(dispatch__intake_mode=Dispatch.IntakeMode.LEGACY).select_related("paper", "dispatch").prefetch_related("scripts").first()
    if not packet or packet.status not in ("received", "scanning", "complete"):
        raise HttpError(404, "Packet is not received or its barcode is not in this university's manifest")
    if packet.received_centre_id != centre.id:
        raise HttpError(409, "This packet belongs to a different receiving centre")
    return {**_packet_row(packet, {centre.id: _centre_row(centre)}), "bundle": packet.dispatch.reference, "manual_recognition_enabled": settings.DEMO_MANUAL_RECOGNITION_ENABLED}


@router.get("/papers")
def intake_papers(request):
    _enabled()
    membership = require_roles(request, *PREPARERS)
    centre = _operational_centre(membership)
    tenant_id = membership.institution.tenant_id
    return {"centre": _centre_row(centre), "papers": list(Paper.objects.filter(tenant_id=tenant_id).order_by("code").values("id", "code", "title"))}


@router.post("/bundles")
def create_bundle(request, payload: GuidedBundleIn):
    _enabled()
    membership = require_roles(request, *PREPARERS)
    tenant_id = membership.institution.tenant_id
    centre = _operational_centre(membership)
    if payload.mode not in (Dispatch.IntakeMode.TRANSFER, Dispatch.IntakeMode.ON_SITE) or not 1 <= len(payload.packets) <= 100:
        raise HttpError(422, "Choose transfer or on-site scanning and add 1-100 packets")
    bundle_code = _code(payload.barcode) if payload.barcode else _generated_code("BND", tenant_id)
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
            bundle = Dispatch.objects.create(tenant_id=tenant_id, reference=bundle_code, intake_mode=payload.mode, paper=papers[payload.packets[0].paper_id], source_centre=payload.source_centre.strip(), prepared_centre_id=centre.id, expected_packets=len(payload.packets), expected_scripts=len(script_codes), manifest_reference=bundle_code, registered_by=request.auth)
            for item, packet_code in zip(payload.packets, packet_codes):
                Packet.objects.create(tenant_id=tenant_id, dispatch=bundle, barcode=packet_code, paper=papers[item.paper_id], expected_scripts=len(item.script_barcodes), script_manifest=[_code(code) for code in item.script_barcodes])
            record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="receiving.guided.manifest_created", aggregate="Dispatch", aggregate_id=bundle.id, payload={"mode": payload.mode, "centre_id": str(centre.id), "packets": len(payload.packets), "scripts": len(script_codes)})
    except IntegrityError as exc:
        raise HttpError(409, "A manifest barcode was registered concurrently") from exc
    return {"id": str(bundle.id), "status": bundle.status, "barcode": bundle.reference, "qr_value": bundle.reference}


@router.post("/bundles/start")
def start_bundle(request, payload: GuidedScanIn):
    _enabled()
    membership = require_roles(request, *PREPARERS)
    tenant_id = membership.institution.tenant_id
    centre = _operational_centre(membership)
    with transaction.atomic():
        bundle = Dispatch.objects.select_for_update().filter(tenant_id=tenant_id, reference=_code(payload.barcode)).exclude(intake_mode=Dispatch.IntakeMode.LEGACY).first()
        if not bundle:
            raise HttpError(404, "Bundle not found")
        if bundle.status != Dispatch.Status.REGISTERED:
            raise HttpError(409, "This bundle has already started")
        if bundle.prepared_centre_id != centre.id:
            raise HttpError(409, "This bundle was prepared at a different centre")
        bundle.status = Dispatch.Status.IN_TRANSIT if bundle.intake_mode == Dispatch.IntakeMode.TRANSFER else Dispatch.Status.ON_SITE
        if bundle.intake_mode == Dispatch.IntakeMode.ON_SITE:
            bundle.received_centre_id = centre.id
        bundle.dispatched_at = timezone.now()
        bundle.version += 1
        bundle.save(update_fields=["status", "received_centre_id", "dispatched_at", "version", "updated_at"])
        record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="receiving.guided.bundle_started", aggregate="Dispatch", aggregate_id=bundle.id, payload={"mode": bundle.intake_mode, "centre_id": str(centre.id)})
    return {"status": bundle.status}


@router.post("/bundles/receive")
def receive_guided_bundle(request, payload: GuidedScanIn):
    _enabled()
    membership = require_roles(request, *RECEIVERS)
    tenant_id = membership.institution.tenant_id
    centre = _operational_centre(membership)
    with transaction.atomic():
        bundle = Dispatch.objects.select_for_update().filter(tenant_id=tenant_id, reference=_code(payload.barcode), intake_mode=Dispatch.IntakeMode.TRANSFER).first()
        if not bundle:
            raise HttpError(404, "Transferred bundle not found")
        if bundle.status != Dispatch.Status.IN_TRANSIT:
            raise HttpError(409, "Bundle is not in transit")
        bundle.status = Dispatch.Status.RECEIVED
        bundle.received_centre_id = centre.id
        bundle.received_at = timezone.now()
        bundle.version += 1
        bundle.save(update_fields=["status", "received_centre_id", "received_at", "version", "updated_at"])
        record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="receiving.guided.bundle_received", aggregate="Dispatch", aggregate_id=bundle.id, payload={"centre_id": str(centre.id)})
    return {"status": bundle.status, "expected_packets": bundle.expected_packets, "centre": _centre_row(centre)}


@router.post("/packets/receive")
def receive_guided_packet(request, payload: GuidedPacketScanIn):
    _enabled()
    membership = require_roles(request, *RECEIVERS)
    tenant_id = membership.institution.tenant_id
    centre = _operational_centre(membership)
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
        if packet.dispatch.received_centre_id != centre.id:
            raise HttpError(409, "This bundle belongs to a different receiving centre")
        if packet.status != "registered":
            raise HttpError(409, "Packet has already been received")
        packet.status = "received"
        packet.received_at = timezone.now()
        packet.received_by = request.auth
        packet.received_centre_id = centre.id
        packet.version += 1
        packet.save(update_fields=["status", "received_at", "received_by", "received_centre_id", "version", "updated_at"])
        bundle = Dispatch.objects.select_for_update().get(id=packet.dispatch_id)
        bundle.received_packets += 1
        bundle.version += 1
        bundle.save(update_fields=["received_packets", "version", "updated_at"])
        record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="receiving.guided.packet_received", aggregate="Packet", aggregate_id=packet.id, payload={"bundle_id": str(bundle.id), "centre_id": str(centre.id)})
    return {"status": packet.status, "subject": packet.paper.code, "expected_scripts": packet.expected_scripts, "centre": _centre_row(centre)}


@router.post("/packets/{packet_id}/recognize")
def recognize_script(request, packet_id: str, cover: UploadedFile = File(...)):
    _enabled()
    membership = require_roles(request, *SCANNERS)
    centre = _operational_centre(membership)
    manual_qr = request.POST.get("manual_qr", "").strip()
    manual_usn = request.POST.get("manual_usn", "").strip().upper()
    if bool(manual_qr) != bool(manual_usn):
        raise HttpError(422, "Enter both the booklet QR and USN for manual recognition")
    if manual_qr and not settings.DEMO_MANUAL_RECOGNITION_ENABLED:
        raise HttpError(403, "Manual recognition is disabled on this deployment")
    tenant_id = membership.institution.tenant_id
    packet = Packet.objects.filter(id=packet_id, tenant_id=tenant_id).select_related("dispatch", "paper").first()
    if not packet or packet.dispatch.intake_mode == Dispatch.IntakeMode.LEGACY:
        raise HttpError(404, "Guided packet not found")
    if packet.status not in ("received", "scanning", "complete"):
        raise HttpError(409, "Receive the packet before scanning scripts")
    if packet.received_centre_id != centre.id:
        raise HttpError(409, "This packet belongs to a different receiving centre")
    content = cover.read(12_000_001)
    cover_hash = hashlib.sha256(content).hexdigest()
    manual_entry = False
    try:
        qr, usn = recognize_cover(content)
    except RecognitionError as exc:
        if not manual_qr:
            raise HttpError(422, str(exc)) from exc
        try:
            detected_qr = read_cover_qr_if_present(content)
        except RecognitionError as image_error:
            raise HttpError(422, str(image_error)) from image_error
        qr = _code(manual_qr)
        if detected_qr and _code(detected_qr) != qr:
            raise HttpError(409, "Entered booklet QR does not match the QR visible on the front page")
        if not MANUAL_USN.fullmatch(manual_usn):
            raise HttpError(422, "Enter a 6-20 character alphanumeric USN")
        usn = manual_usn
        manual_entry = True
    qr = _code(qr)
    if manual_qr and not manual_entry and (_code(manual_qr) != qr or manual_usn != usn):
        raise HttpError(409, "Entered details do not match the recognized front page")
    if qr not in packet.script_manifest:
        raise HttpError(409, "Booklet QR does not belong to this packet; isolate it and check the manifest")
    existing = Script.objects.filter(primary_barcode=qr).first()
    if existing and (existing.tenant_id != tenant_id or existing.packet_id != packet.id):
        raise HttpError(409, "Booklet QR was already registered in another packet")
    if existing and existing.state != Script.State.REGISTERED:
        raise HttpError(409, "Booklet QR was already processed")
    if existing and existing.recognized_cover_sha256 and existing.recognized_cover_sha256 != cover_hash:
        raise HttpError(409, "This QR was already linked to a different front-page image")
    if existing and existing.digitized_centre_id and existing.digitized_centre_id != centre.id:
        raise HttpError(409, "This script was already opened at a different centre")
    script = existing or register_script(tenant_id=tenant_id, actor_id=request.auth.id, packet=packet, primary_barcode=qr, supplements=[], bundle_barcode=packet.dispatch.reference, centre_barcode=centre.code, location=centre.name)
    update_fields = []
    if not script.digitized_centre_id:
        script.digitized_centre_id = centre.id
        update_fields.append("digitized_centre_id")
    if not script.recognized_cover_sha256:
        script.recognized_cover_sha256 = cover_hash
        update_fields.append("recognized_cover_sha256")
    if update_fields:
        script.save(update_fields=[*update_fields, "updated_at"])
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
        if manual_entry:
            with transaction.atomic():
                record_event(tenant_id=tenant_id, actor_id=request.auth.id, action="receiving.guided.manual_recognition", aggregate="Script", aggregate_id=script.id, payload={"packet_id": str(packet.id), "centre_id": str(centre.id), "cover_sha256": cover_hash, "qr_reference": _masked(qr)})
    return {"script_id": str(script.id), "script_code": script.script_code, "version": script.version, "subject": script.paper.code, "centre": _centre_row(centre), "identity_linked": True}
