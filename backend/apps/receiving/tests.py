import json

from django.core.management import call_command
from django.test import Client, TestCase

from apps.configuration.models import Paper
from apps.core.models import AuditEvent, OutboxEvent

from .models import Dispatch, ReceivingException


class PhysicalReceivingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap_demo", verbosity=0)

    def setUp(self):
        self.client = Client()
        login = self.post("/api/v1/auth/login", {"email": "admin@admiezo.local", "password": "ChangeMe123!", "device_id": "receiving-tests"})
        self.assertEqual(login.status_code, 200)

    def post(self, path, payload):
        return self.client.post(path, data=json.dumps(payload), content_type="application/json")

    def create_dispatch(self, reference="DSP-TEST-001"):
        paper = Paper.objects.first()
        response = self.post("/api/v1/receiving/dispatches", {
            "reference": reference, "paper_id": str(paper.id), "source_centre": "City Examination Centre",
            "expected_packets": 1, "expected_scripts": 2, "manifest_reference": f"MAN-{reference}",
            "manifest_sha256": "a" * 64, "carrier": "University transport",
        })
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_dispatch_packet_receipt_reconciliation_and_closure(self):
        dispatch = self.create_dispatch()
        packet = self.post(f"/api/v1/receiving/dispatches/{dispatch['id']}/packets", {"barcode": "PKT-TEST-001", "expected_scripts": 2, "seal_number": "SEAL-1"})
        self.assertEqual(packet.status_code, 200)
        verified = self.post(f"/api/v1/receiving/dispatches/{dispatch['id']}/verify", {"version": dispatch["version"]})
        self.assertEqual(verified.status_code, 200)
        received = self.post(f"/api/v1/receiving/packets/{packet.json()['id']}/receive", {"version": 1, "received_scripts": 2, "condition": "intact", "handed_over_by": "Centre Superintendent"})
        self.assertEqual(received.status_code, 200)
        reconciled = self.post(f"/api/v1/receiving/dispatches/{dispatch['id']}/reconcile", {"version": verified.json()["version"]})
        self.assertEqual(reconciled.status_code, 200)
        self.assertEqual(reconciled.json()["status"], Dispatch.Status.RECONCILED)
        for confirmation_type in ("handover", "receiver", "receipt"):
            confirmation = self.post(f"/api/v1/receiving/dispatches/{dispatch['id']}/confirm", {"confirmation_type": confirmation_type, "notes": "Confirmed at intake desk"})
            self.assertEqual(confirmation.status_code, 200)
        closed = self.post(f"/api/v1/receiving/dispatches/{dispatch['id']}/close", {"version": reconciled.json()["version"]})
        self.assertEqual(closed.status_code, 200)
        self.assertEqual(closed.json()["status"], Dispatch.Status.CLOSED)
        self.assertTrue(AuditEvent.objects.filter(action="receiving.physical_custody.closed", aggregate_id=dispatch["id"]).exists())
        self.assertTrue(OutboxEvent.objects.filter(topic="receiving.physical_custody.closed", aggregate_id=dispatch["id"]).exists())

    def test_exception_requires_review_reconciliation_and_clearance(self):
        dispatch = self.create_dispatch("DSP-TEST-EXC")
        created = self.post("/api/v1/receiving/exceptions", {"dispatch_id": dispatch["id"], "kind": "loose_page", "script_barcode": "SCR-LOOSE", "notes": "Detached continuation page"})
        self.assertEqual(created.status_code, 200)
        exception_id = created.json()["id"]
        premature = self.post(f"/api/v1/receiving/exceptions/{exception_id}/clear", {"version": 1, "clearance_note": "Attempt"})
        self.assertEqual(premature.status_code, 409)
        reviewed = self.post(f"/api/v1/receiving/exceptions/{exception_id}/review", {"version": 1, "supervisor_note": "Page sequence checked"})
        self.assertEqual(reviewed.status_code, 200)
        reconciled = self.post(f"/api/v1/receiving/exceptions/{exception_id}/reconcile", {"version": 2, "reconciliation_note": "Page secured to script"})
        self.assertEqual(reconciled.status_code, 200)
        cleared = self.post(f"/api/v1/receiving/exceptions/{exception_id}/clear", {"version": 3, "clearance_note": "Custody label applied"})
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(ReceivingException.objects.get(id=exception_id).status, "cleared")
