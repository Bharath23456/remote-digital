import hashlib
import json

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase

from apps.configuration.models import Paper
from apps.core.models import AuditEvent, OutboxEvent
from apps.receiving.models import Dispatch, Packet

from .models import BarcodeRecord, CustodyEvent, Script


class ScriptCustodyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap_demo", verbosity=0)
        cls.controller = User.objects.get(username="controller@admiezo.local")
        cls.controller.set_password("ControlPass123!")
        cls.controller.save()

    def setUp(self):
        self.client = Client()
        self.assertEqual(self.post(self.client, "/api/v1/auth/login", {"email": "admin@admiezo.local", "password": "ChangeMe123!", "device_id": "custody-tests"}).status_code, 200)
        paper = Paper.objects.first()
        case_id = hashlib.sha256(self._testMethodName.encode()).hexdigest()[:12]
        self.dispatch = Dispatch.objects.create(tenant_id=paper.tenant_id, reference=f"CUST-{case_id}", paper=paper, source_centre="Test centre", expected_packets=1, expected_scripts=2)
        self.packet = Packet.objects.create(tenant_id=paper.tenant_id, dispatch=self.dispatch, barcode=f"PKT-{case_id}", expected_scripts=2)

    def post(self, client, path, payload):
        return client.post(path, data=json.dumps(payload), content_type="application/json")

    def register(self, primary="SCRIPT-PRIMARY-001"):
        return self.post(self.client, "/api/v1/custody/scripts", {"packet_id": str(self.packet.id), "primary_barcode": primary, "supplement_barcodes": [f"{primary}-SUP-1"], "bundle_barcode": "BUNDLE-A", "centre_barcode": "CENTRE-A", "location": "Receiving desk"})

    def test_registration_creates_opaque_id_barcodes_and_custody_event(self):
        response = self.register()
        self.assertEqual(response.status_code, 200)
        script = Script.objects.get(id=response.json()["id"])
        self.assertTrue(script.script_code.startswith("AS-"))
        self.assertEqual(script.state, Script.State.REGISTERED)
        self.assertEqual(BarcodeRecord.objects.filter(script=script).count(), 2)
        self.assertTrue(CustodyEvent.objects.filter(script=script, from_state="received", to_state="registered").exists())
        self.assertTrue(AuditEvent.objects.filter(action="custody.script.registered", aggregate_id=str(script.id)).exists())
        self.assertTrue(OutboxEvent.objects.filter(topic="custody.script.registered", aggregate_id=str(script.id)).exists())
        duplicate = self.register("SCRIPT-PRIMARY-001")
        self.assertEqual(duplicate.status_code, 409)

    def test_barcode_reconciliation_detects_duplicate_missing_and_excess(self):
        self.assertEqual(self.register("SCRIPT-REC-001").status_code, 200)
        response = self.post(self.client, f"/api/v1/custody/packets/{self.packet.id}/reconcile", {"manifest_barcodes": ["SCRIPT-REC-001", "SCRIPT-REC-002"], "observed_barcodes": ["SCRIPT-REC-001", "SCRIPT-REC-001", "SCRIPT-EXCESS"]})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "exception")
        self.assertEqual(body["duplicates"], ["SCRIPT-REC-001"])
        self.assertEqual(body["missing"], ["SCRIPT-REC-002"])
        self.assertEqual(body["excess"], ["SCRIPT-EXCESS"])

    def test_transfer_requester_cannot_self_authorize(self):
        script_id = self.register("SCRIPT-XFER-001").json()["id"]
        created = self.post(self.client, "/api/v1/custody/transfers", {"script_id": script_id, "to_location": "Secure scanning room", "reason": "Scanning handover"})
        self.assertEqual(created.status_code, 200)
        blocked = self.post(self.client, f"/api/v1/custody/transfers/{created.json()['id']}/decision", {"version": 1, "authorize": True})
        self.assertEqual(blocked.status_code, 409)
        controller = Client()
        self.assertEqual(self.post(controller, "/api/v1/auth/login", {"email": "controller@admiezo.local", "password": "ControlPass123!", "device_id": "controller-device"}).status_code, 200)
        approved = self.post(controller, f"/api/v1/custody/transfers/{created.json()['id']}/decision", {"version": 1, "authorize": True})
        self.assertEqual(approved.status_code, 200)
        dispatched = self.post(self.client, f"/api/v1/custody/transfers/{created.json()['id']}/dispatch", {"version": 2, "location": "Transit cage"})
        self.assertEqual(dispatched.status_code, 200)
        received = self.post(controller, f"/api/v1/custody/transfers/{created.json()['id']}/receive", {"version": 3, "location": "Secure scanning room"})
        self.assertEqual(received.status_code, 200)
        self.assertEqual(Script.objects.get(id=script_id).last_location, "Secure scanning room")

    def test_script_removal_is_soft_versioned_and_restorable(self):
        created = self.register("SCRIPT-REMOVE-001")
        self.assertEqual(created.status_code, 200)
        script_id = created.json()["id"]
        removed = self.post(
            self.client,
            f"/api/v1/custody/scripts/{script_id}/remove",
            {"version": created.json()["version"], "reason": "Duplicate physical script received"},
        )
        self.assertEqual(removed.status_code, 200)
        script = Script.objects.get(id=script_id)
        self.assertIsNotNone(script.removed_at)
        self.assertEqual(script.removal_reason, "Duplicate physical script received")
        self.assertTrue(AuditEvent.objects.filter(action="custody.script.removed", aggregate_id=script_id).exists())

        catalog = self.client.get("/api/v1/custody/catalog")
        self.assertEqual(catalog.status_code, 200)
        self.assertNotIn(script_id, {item["id"] for item in catalog.json()["scripts"]})
        self.assertIn(script_id, {item["id"] for item in catalog.json()["removed"]})

        restored = self.post(
            self.client,
            f"/api/v1/custody/scripts/{script_id}/restore",
            {"version": removed.json()["version"], "reason": "Physical record reconciled and approved"},
        )
        self.assertEqual(restored.status_code, 200)
        script.refresh_from_db()
        self.assertIsNone(script.removed_at)
        self.assertEqual(script.removal_reason, "")
        self.assertTrue(AuditEvent.objects.filter(action="custody.script.restored", aggregate_id=script_id).exists())
