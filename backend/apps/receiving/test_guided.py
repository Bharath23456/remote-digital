import io
import json
from datetime import timedelta
from unittest.mock import patch
from urllib.error import URLError

import cv2
import numpy as np

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from apps.configuration.models import Paper
from apps.core.models import AuditEvent
from apps.custody.models import Script
from apps.anonymisation.models import IdentityLink
from apps.repository.models import UploadIntent
from apps.tenancy.models import Membership

from .models import Dispatch, Packet
from .omr import RecognitionError, _read_usn_grid, USN_COLUMN_SYMBOLS


@override_settings(DEMO_MANUAL_INTAKE_ENABLED=True, DEMO_MANUAL_RECOGNITION_ENABLED=True, IDENTITY_SERVICE_URL="http://identity-service:8100")
class GuidedIntakeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap_demo", verbosity=0)

    def setUp(self):
        self.client = Client()
        response = self.post("/api/v1/auth/login", {"email": "admin@admiezo.local", "password": "ChangeMe123!", "device_id": "guided-intake-tests"})
        self.assertEqual(response.status_code, 200)
        self.paper = Paper.objects.first()

    def post(self, path, body):
        return self.client.post(path, data=json.dumps(body), content_type="application/json")

    def create_manifest(self, mode="transfer"):
        packets = [
            {"barcode": f"PKT-GUIDED-{number}", "paper_id": str(self.paper.id), "script_barcodes": [f"QR-GUIDED-{number}"]}
            for number in range(1, 5)
        ]
        response = self.post("/api/v1/receiving/guided/bundles", {"barcode": "BND-GUIDED-001", "source_centre": "Test College", "mode": mode, "packets": packets})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def ready_packet(self):
        self.create_manifest("on_site")
        self.post("/api/v1/receiving/guided/bundles/start", {"barcode": "BND-GUIDED-001"})
        self.post("/api/v1/receiving/guided/packets/receive", {"barcode": "PKT-GUIDED-1", "bundle_barcode": "BND-GUIDED-001"})
        return Packet.objects.get(barcode="PKT-GUIDED-1")

    def cover_image(self, dimension=800):
        _, image = cv2.imencode(".png", np.full((dimension, dimension), 255, dtype=np.uint8))
        return bytes(image)

    def test_manual_recognition_fallback_links_identity_and_audits_without_exposing_usn(self):
        packet = self.ready_packet()
        path = f"/api/v1/receiving/guided/packets/{packet.id}/recognize"
        cover = self.cover_image()
        unreadable = self.client.post(path, {"cover": SimpleUploadedFile("front.png", cover, content_type="image/png")})
        self.assertEqual(unreadable.status_code, 422)
        self.assertFalse(Script.objects.filter(primary_barcode="QR-GUIDED-1").exists())

        with patch("apps.receiving.guided_api.urlopen", return_value=io.BytesIO(b'{"receipt":"test"}')) as identity_service, patch("apps.receiving.guided_api.confirm_identity_storage"):
            response = self.client.post(path, {
                "cover": SimpleUploadedFile("front.png", cover, content_type="image/png"),
                "manual_qr": "QR-GUIDED-1", "manual_usn": "4UB22CS032",
            })
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Script.objects.get(id=response.json()["script_id"]).paper_id, packet.paper_id)
        self.assertNotIn("4UB22CS032", response.content.decode())
        self.assertNotIn("QR-GUIDED-1", response.content.decode())
        self.assertEqual(json.loads(identity_service.call_args.args[0].data)["usn"], "4UB22CS032")
        event = AuditEvent.objects.get(action="receiving.guided.manual_recognition", aggregate_id=response.json()["script_id"])
        self.assertNotIn("4UB22CS032", json.dumps(event.payload))
        self.assertNotIn("QR-GUIDED-1", json.dumps(event.payload))

    def test_manual_recognition_requires_demo_flag_and_valid_cover(self):
        packet = self.ready_packet()
        path = f"/api/v1/receiving/guided/packets/{packet.id}/recognize"
        data = {"manual_qr": "QR-GUIDED-1", "manual_usn": "4UB22CS032"}
        with override_settings(DEMO_MANUAL_RECOGNITION_ENABLED=False):
            disabled = self.client.post(path, {**data, "cover": SimpleUploadedFile("front.png", self.cover_image(), content_type="image/png")})
        self.assertEqual(disabled.status_code, 403)
        invalid = self.client.post(path, {**data, "cover": SimpleUploadedFile("front.png", b"not-an-image", content_type="image/png")})
        self.assertEqual(invalid.status_code, 422)
        self.assertFalse(Script.objects.filter(primary_barcode="QR-GUIDED-1").exists())

    def test_manual_recognition_accepts_small_demo_cover_after_auto_reader_rejects_it(self):
        packet = self.ready_packet()
        path = f"/api/v1/receiving/guided/packets/{packet.id}/recognize"
        cover = self.cover_image(dimension=400)
        self.assertEqual(self.client.post(path, {"cover": SimpleUploadedFile("front.png", cover, content_type="image/png")}).status_code, 422)
        with patch("apps.receiving.guided_api.urlopen", return_value=io.BytesIO(b'{"receipt":"test"}')), patch("apps.receiving.guided_api.confirm_identity_storage"):
            response = self.client.post(path, {"cover": SimpleUploadedFile("front.png", cover, content_type="image/png"), "manual_qr": "QR-GUIDED-1", "manual_usn": "4UB22CS032"})
        self.assertEqual(response.status_code, 200, response.content)

    def test_manual_recognition_cannot_override_qr_or_packet_manifest(self):
        packet = self.ready_packet()
        path = f"/api/v1/receiving/guided/packets/{packet.id}/recognize"
        data = {"manual_qr": "QR-GUIDED-1", "manual_usn": "4UB22CS032"}
        with patch("apps.receiving.guided_api.recognize_cover", side_effect=RecognitionError("USN bubbles were not found")), patch("apps.receiving.guided_api.read_cover_qr_if_present", return_value="QR-GUIDED-2"):
            conflict = self.client.post(path, {**data, "cover": SimpleUploadedFile("front.png", self.cover_image(), content_type="image/png")})
        self.assertEqual(conflict.status_code, 409)
        wrong_packet = self.client.post(path, {**data, "manual_qr": "QR-GUIDED-2", "cover": SimpleUploadedFile("front.png", self.cover_image(), content_type="image/png")})
        self.assertEqual(wrong_packet.status_code, 409)
        with patch("apps.receiving.guided_api.recognize_cover", return_value=("QR-GUIDED-1", "4UB22CS032")):
            override = self.client.post(path, {**data, "manual_usn": "4UB22CS033", "cover": SimpleUploadedFile("front.png", self.cover_image(), content_type="image/png")})
        self.assertEqual(override.status_code, 409)
        self.assertFalse(Script.objects.filter(primary_barcode__in=["QR-GUIDED-1", "QR-GUIDED-2"]).exists())

    def test_transfer_requires_bundle_before_packet_and_tracks_missing(self):
        self.create_manifest()
        packet = self.post("/api/v1/receiving/guided/packets/receive", {"barcode": "PKT-GUIDED-1", "bundle_barcode": "BND-GUIDED-001"})
        self.assertEqual(packet.status_code, 409)
        self.assertEqual(self.post("/api/v1/receiving/guided/bundles/start", {"barcode": "BND-GUIDED-001"}).status_code, 200)
        self.assertEqual(self.post("/api/v1/receiving/guided/bundles/receive", {"barcode": "BND-GUIDED-001"}).status_code, 200)
        for number in (1, 2):
            self.assertEqual(self.post("/api/v1/receiving/guided/packets/receive", {"barcode": f"PKT-GUIDED-{number}", "bundle_barcode": "BND-GUIDED-001"}).status_code, 200)
        catalog = self.client.get("/api/v1/receiving/guided/catalog").json()["bundles"][0]
        self.assertEqual((catalog["received_packets"], catalog["expected_packets"]), (2, 4))
        self.assertEqual(catalog["expected_scripts"] - catalog["scanned_scripts"], 4)
        self.assertEqual(len(catalog["packets"][0]["missing_references"]), 1)
        self.assertNotIn("QR-GUIDED-1", json.dumps(catalog))

    def test_on_site_skips_transport_but_wrong_packet_is_rejected(self):
        self.create_manifest("on_site")
        self.assertEqual(self.post("/api/v1/receiving/guided/bundles/start", {"barcode": "BND-GUIDED-001"}).json()["status"], Dispatch.Status.ON_SITE)
        self.assertEqual(self.post("/api/v1/receiving/guided/packets/receive", {"barcode": "PKT-GUIDED-1", "bundle_barcode": "BND-OTHER-001"}).status_code, 409)
        self.assertEqual(self.post("/api/v1/receiving/guided/packets/receive", {"barcode": "PKT-GUIDED-1", "bundle_barcode": "BND-GUIDED-001"}).status_code, 200)
        packet = Packet.objects.get(barcode="PKT-GUIDED-1")
        cover = SimpleUploadedFile("front.png", b"fake-cover", content_type="image/png")
        with patch("apps.receiving.guided_api.recognize_cover", return_value=("QR-GUIDED-2", "AB12345678")):
            response = self.client.post(f"/api/v1/receiving/guided/packets/{packet.id}/recognize", {"cover": cover})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(Script.objects.filter(primary_barcode="QR-GUIDED-2").exists())

    def test_recognized_script_uses_packet_subject_and_is_idempotent_until_upload(self):
        self.create_manifest("on_site")
        self.post("/api/v1/receiving/guided/bundles/start", {"barcode": "BND-GUIDED-001"})
        self.post("/api/v1/receiving/guided/packets/receive", {"barcode": "PKT-GUIDED-1", "bundle_barcode": "BND-GUIDED-001"})
        packet = Packet.objects.get(barcode="PKT-GUIDED-1")
        with patch("apps.receiving.guided_api.recognize_cover", return_value=("QR-GUIDED-1", "AB12345678")), patch("apps.receiving.guided_api.urlopen", return_value=io.BytesIO(b'{"receipt":"test"}')), patch("apps.receiving.guided_api.confirm_identity_storage"):
            response = self.client.post(f"/api/v1/receiving/guided/packets/{packet.id}/recognize", {"cover": SimpleUploadedFile("front.png", b"cover", content_type="image/png")})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Script.objects.get(id=response.json()["script_id"]).paper_id, self.paper.id)
        self.assertNotIn("AB12345678", response.content.decode())
        link = IdentityLink.objects.get(script_id=response.json()["script_id"])
        link.stored_at = timezone.now()
        link.save(update_fields=["stored_at"])
        with patch("apps.receiving.guided_api.recognize_cover", return_value=("QR-GUIDED-1", "AB12345678")):
            retry = self.client.post(f"/api/v1/receiving/guided/packets/{packet.id}/recognize", {"cover": SimpleUploadedFile("front.png", b"cover", content_type="image/png")})
            changed = self.client.post(f"/api/v1/receiving/guided/packets/{packet.id}/recognize", {"cover": SimpleUploadedFile("front.png", b"different-cover", content_type="image/png")})
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(changed.status_code, 409)

    def test_duplicate_manifest_qr_is_rejected(self):
        packets = [{"barcode": f"PKT-DUP-{i}", "paper_id": str(self.paper.id), "script_barcodes": ["QR-DUP-001"]} for i in (1, 2)]
        response = self.post("/api/v1/receiving/guided/bundles", {"barcode": "BND-DUP-001", "source_centre": "Test", "mode": "transfer", "packets": packets})
        self.assertEqual(response.status_code, 422)

    def test_legacy_receiving_cannot_change_guided_bundle(self):
        created = self.create_manifest()
        legacy = self.client.get("/api/v1/receiving/catalog").json()
        self.assertNotIn("BND-GUIDED-001", json.dumps(legacy))
        self.assertEqual(self.post(f"/api/v1/receiving/dispatches/{created['id']}/verify", {"version": 1}).status_code, 404)

    def test_page_upload_completion_advances_packet_and_bundle_progress(self):
        self.create_manifest("on_site")
        self.post("/api/v1/receiving/guided/bundles/start", {"barcode": "BND-GUIDED-001"})
        self.post("/api/v1/receiving/guided/packets/receive", {"barcode": "PKT-GUIDED-1", "bundle_barcode": "BND-GUIDED-001"})
        packet = Packet.objects.get(barcode="PKT-GUIDED-1")
        script = Script.objects.create(tenant_id=packet.tenant_id, packet=packet, paper=packet.paper, script_code="AS-GUIDED-1", primary_barcode="QR-GUIDED-1", state=Script.State.REGISTERED)
        UploadIntent.objects.create(tenant_id=packet.tenant_id, script=script, kind=UploadIntent.Kind.RAW_SCAN, page_number=1, asset_version=1, storage_key="guided-test-cover", content_type="image/png", maximum_bytes=5000, status=UploadIntent.Status.COMPLETED, expires_at=timezone.now() + timedelta(minutes=5))
        response = self.post(f"/api/v1/repository/scripts/{script.id}/complete-scan", {"version": 1, "page_count": 1, "location": "Guided test"})
        self.assertEqual(response.status_code, 200, response.content)
        packet.refresh_from_db()
        packet.dispatch.refresh_from_db()
        self.assertEqual((packet.status, packet.received_scripts), ("complete", 1))
        self.assertEqual(packet.dispatch.received_scripts, 1)

    def test_identity_service_failure_keeps_cover_fingerprint_for_retry(self):
        self.create_manifest("on_site")
        self.post("/api/v1/receiving/guided/bundles/start", {"barcode": "BND-GUIDED-001"})
        self.post("/api/v1/receiving/guided/packets/receive", {"barcode": "PKT-GUIDED-1", "bundle_barcode": "BND-GUIDED-001"})
        packet = Packet.objects.get(barcode="PKT-GUIDED-1")
        with patch("apps.receiving.guided_api.recognize_cover", return_value=("QR-GUIDED-1", "AB12345678")), patch("apps.receiving.guided_api.urlopen", side_effect=URLError("offline")):
            failed = self.client.post(f"/api/v1/receiving/guided/packets/{packet.id}/recognize", {"cover": SimpleUploadedFile("front.png", b"cover", content_type="image/png")})
        self.assertEqual(failed.status_code, 503)
        script = Script.objects.get(primary_barcode="QR-GUIDED-1")
        self.assertEqual(len(script.recognized_cover_sha256), 64)
        with patch("apps.receiving.guided_api.recognize_cover", return_value=("QR-GUIDED-1", "AB12345678")):
            changed = self.client.post(f"/api/v1/receiving/guided/packets/{packet.id}/recognize", {"cover": SimpleUploadedFile("front.png", b"different-cover", content_type="image/png")})
        self.assertEqual(changed.status_code, 409)

    def test_intake_desk_roles_are_confined_to_their_stage(self):
        self.create_manifest("on_site")
        self.post("/api/v1/receiving/guided/bundles/start", {"barcode": "BND-GUIDED-001"})
        institution = Membership.objects.get(user__email="admin@admiezo.local").institution

        def worker(role, module):
            user = User.objects.create_user(username=f"{role}@example.test", email=f"{role}@example.test", password="ChangeMe123!")
            Membership.objects.create(user=user, institution=institution, role=role, enabled_modules=[module])
            client = Client()
            response = client.post("/api/v1/auth/login", data=json.dumps({"email": user.email, "password": "ChangeMe123!", "device_id": f"desk-{role}"}), content_type="application/json")
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(client.get("/api/v1/auth/me").json()["enabled_modules"], [module])
            return client

        preparer = worker(Membership.Role.BUNDLE_PREPARER, "receiving")
        receiver = worker(Membership.Role.INTAKE_RECEIVER, "custody")
        scanner = worker(Membership.Role.SCAN_OPERATOR, "digitization")
        for client in (preparer, receiver, scanner):
            self.assertEqual(client.get("/api/v1/receiving/guided/catalog").status_code, 200)
            self.assertEqual(client.get("/api/v1/operations/overview").status_code, 403)
            self.assertEqual(client.get("/api/v1/repository/catalog").status_code, 403)
        self.assertEqual(preparer.get("/api/v1/receiving/guided/papers").status_code, 200)
        self.assertEqual(receiver.get("/api/v1/receiving/guided/papers").status_code, 403)
        self.assertEqual(scanner.get("/api/v1/receiving/guided/papers").status_code, 403)
        self.assertEqual(receiver.get("/api/v1/receiving/guided/lookup/bundles/BND-GUIDED-001").status_code, 200)
        self.assertEqual(preparer.get("/api/v1/receiving/guided/lookup/bundles/BND-GUIDED-001").status_code, 403)
        self.assertEqual(scanner.get("/api/v1/receiving/guided/lookup/bundles/BND-GUIDED-001").status_code, 403)
        self.assertEqual(preparer.post("/api/v1/receiving/guided/packets/receive", data=json.dumps({"barcode": "PKT-GUIDED-1", "bundle_barcode": "BND-GUIDED-001"}), content_type="application/json").status_code, 403)
        self.assertEqual(receiver.post("/api/v1/receiving/guided/packets/receive", data=json.dumps({"barcode": "PKT-GUIDED-1", "bundle_barcode": "BND-GUIDED-001"}), content_type="application/json").status_code, 200)
        self.assertEqual(scanner.get("/api/v1/receiving/guided/lookup/packets/PKT-GUIDED-1").status_code, 200)
        self.assertEqual(receiver.get("/api/v1/receiving/guided/lookup/packets/PKT-GUIDED-1").status_code, 403)
        self.assertEqual(preparer.get("/api/v1/receiving/guided/lookup/packets/PKT-GUIDED-1").status_code, 403)
        self.assertEqual(receiver.post("/api/v1/receiving/guided/bundles", data=json.dumps({}), content_type="application/json").status_code, 403)
        packet = Packet.objects.get(barcode="PKT-GUIDED-1")
        self.assertEqual(preparer.post(f"/api/v1/receiving/guided/packets/{packet.id}/recognize", {"cover": SimpleUploadedFile("front.png", b"cover", content_type="image/png")}).status_code, 403)
        self.assertEqual(receiver.post(f"/api/v1/receiving/guided/packets/{packet.id}/recognize", {"cover": SimpleUploadedFile("front.png", b"cover", content_type="image/png")}).status_code, 403)
        with patch("apps.receiving.guided_api.recognize_cover", return_value=("QR-GUIDED-1", "AB12345678")), patch("apps.receiving.guided_api.urlopen", return_value=io.BytesIO(b'{"receipt":"test"}')), patch("apps.receiving.guided_api.confirm_identity_storage"):
            self.assertEqual(scanner.post(f"/api/v1/receiving/guided/packets/{packet.id}/recognize", {"cover": SimpleUploadedFile("front.png", b"cover", content_type="image/png")}).status_code, 200)
        self.assertEqual(scanner.post("/api/v1/receiving/guided/packets/receive", data=json.dumps({"barcode": "PKT-GUIDED-2", "bundle_barcode": "BND-GUIDED-001"}), content_type="application/json").status_code, 403)

    def test_operations_supervisor_can_work_across_intake_but_not_other_modules(self):
        institution = Membership.objects.get(user__email="admin@admiezo.local").institution
        user = User.objects.create_user(username="intake.supervisor@example.test", email="intake.supervisor@example.test", password="ChangeMe123!")
        Membership.objects.create(user=user, institution=institution, role=Membership.Role.OPERATIONS_SUPERVISOR, enabled_modules=["receiving", "custody", "digitization"])
        supervisor = Client()
        login = supervisor.post("/api/v1/auth/login", data=json.dumps({"email": user.email, "password": "ChangeMe123!", "device_id": "supervisor-test"}), content_type="application/json")
        self.assertEqual(login.status_code, 200, login.content)
        self.assertEqual(set(supervisor.get("/api/v1/auth/me").json()["enabled_modules"]), {"receiving", "custody", "digitization"})

        def submit(path, payload):
            return supervisor.post(path, data=json.dumps(payload), content_type="application/json")

        self.assertEqual(supervisor.get("/api/v1/receiving/guided/catalog").status_code, 200)
        self.assertEqual(supervisor.get("/api/v1/receiving/guided/papers").status_code, 200)
        created = submit("/api/v1/receiving/guided/bundles", {"barcode": "BND-SUP-001", "source_centre": "Test College", "mode": "transfer", "packets": [{"barcode": "PKT-SUP-001", "paper_id": str(self.paper.id), "script_barcodes": ["QR-SUP-001"]}]})
        self.assertEqual(created.status_code, 200, created.content)
        self.assertEqual(submit("/api/v1/receiving/guided/bundles/start", {"barcode": "BND-SUP-001"}).status_code, 200)
        self.assertEqual(submit("/api/v1/receiving/guided/bundles/receive", {"barcode": "BND-SUP-001"}).status_code, 200)
        self.assertEqual(supervisor.get("/api/v1/receiving/guided/lookup/bundles/BND-SUP-001").status_code, 200)
        self.assertEqual(submit("/api/v1/receiving/guided/packets/receive", {"barcode": "PKT-SUP-001", "bundle_barcode": "BND-SUP-001"}).status_code, 200)
        self.assertEqual(supervisor.get("/api/v1/receiving/guided/lookup/packets/PKT-SUP-001").status_code, 200)
        packet = Packet.objects.get(barcode="PKT-SUP-001")
        with patch("apps.receiving.guided_api.recognize_cover", return_value=("QR-SUP-001", "AB12345678")), patch("apps.receiving.guided_api.urlopen", return_value=io.BytesIO(b'{"receipt":"test"}')), patch("apps.receiving.guided_api.confirm_identity_storage"):
            recognized = supervisor.post(f"/api/v1/receiving/guided/packets/{packet.id}/recognize", {"cover": SimpleUploadedFile("front.png", b"cover", content_type="image/png")})
        self.assertEqual(recognized.status_code, 200, recognized.content)
        uploaded = submit("/api/v1/repository/manual-scan/uploads", {"script_id": recognized.json()["script_id"], "page_number": 1, "content_type": "image/png", "maximum_bytes": 5000})
        self.assertEqual(uploaded.status_code, 200, uploaded.content)
        for path in ("/api/v1/operations/overview", "/api/v1/receiving/catalog", "/api/v1/security/catalog", "/api/v1/phase4/catalog?section=operations"):
            self.assertEqual(supervisor.get(path).status_code, 403, path)


class OMRReaderTests(TestCase):
    def test_complete_bubble_grid_and_cut_off_grid(self):
        image = np.full((2650, 2000), 255, dtype=np.uint8)
        expected = "4UB22CS032"
        for column, marked in enumerate(expected):
            x = 1225 + column * 64
            cv2.circle(image, (x, 400), 20, 0, 2)
            for row, symbol in enumerate(USN_COLUMN_SYMBOLS[column]):
                x, y = 1225 + column * 64, 500 + row * 52
                cv2.circle(image, (x, y), 20, 0, 2)
                if symbol == marked:
                    cv2.circle(image, (x, y), 17, 0, -1)
        qr_points = np.array([[1700, 100], [1850, 100], [1850, 200], [1700, 200]], dtype=np.float32)
        self.assertEqual(_read_usn_grid(image, qr_points), expected)
        with self.assertRaises(RecognitionError):
            _read_usn_grid(image[:1200], qr_points)

    def test_unfilled_usn_bubble_is_rejected(self):
        image = np.full((2300, 2000), 255, dtype=np.uint8)
        for column, symbols in enumerate(USN_COLUMN_SYMBOLS):
            for row, symbol in enumerate(symbols):
                x, y = 1225 + column * 64, 500 + row * 52
                cv2.circle(image, (x, y), 20, 0, 2)
                if column != 1 and symbol == symbols[0]:
                    cv2.circle(image, (x, y), 17, 0, -1)
        qr_points = np.array([[1700, 100], [1850, 100], [1850, 200], [1700, 200]], dtype=np.float32)
        with self.assertRaisesRegex(RecognitionError, "unfilled or ambiguous"):
            _read_usn_grid(image, qr_points)
