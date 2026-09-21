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
from apps.custody.models import Script
from apps.anonymisation.models import IdentityLink
from apps.repository.models import UploadIntent
from apps.tenancy.models import Membership

from .models import Dispatch, Packet
from .omr import RecognitionError, _read_usn_grid, USN_COLUMN_SYMBOLS


@override_settings(DEMO_MANUAL_INTAKE_ENABLED=True, IDENTITY_SERVICE_URL="http://identity-service:8100")
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
