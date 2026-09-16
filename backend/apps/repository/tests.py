import hashlib
import json
import uuid
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.core.management import call_command
from django.test import Client, TestCase

from apps.configuration.models import Paper
from apps.core.models import AuditEvent, OutboxEvent
from apps.custody.models import Script
from apps.receiving.models import Dispatch, Packet

from .models import ScriptAsset, UploadIntent
from .storage import ObjectMetadata, signed_object_url


class ScriptRepositoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap_demo", verbosity=0)

    def setUp(self):
        self.client = Client()
        response = self.post("/api/v1/auth/login", {"email": "admin@admiezo.local", "password": "ChangeMe123!", "device_id": "repository-tests"})
        self.assertEqual(response.status_code, 200)
        paper = Paper.objects.first()
        case_id = hashlib.sha256(self._testMethodName.encode()).hexdigest()[:12]
        dispatch = Dispatch.objects.create(tenant_id=paper.tenant_id, reference=f"REPO-{case_id}", paper=paper, source_centre="Test", expected_packets=1, expected_scripts=1)
        packet = Packet.objects.create(tenant_id=paper.tenant_id, dispatch=dispatch, barcode=f"PKT-REPO-{case_id}", expected_scripts=1)
        self.script = Script.objects.create(tenant_id=paper.tenant_id, script_code=f"AS-REPO-{case_id}", primary_barcode=f"BC-REPO-{case_id}", packet=packet, paper=paper, state=Script.State.REGISTERED)

    def post(self, path, payload):
        return self.client.post(path, data=json.dumps(payload), content_type="application/json", HTTP_IDEMPOTENCY_KEY=f"test-{uuid.uuid4()}")

    def test_raw_upload_finalization_and_scan_completion(self):
        issued = self.post("/api/v1/repository/manual-scan/uploads", {"script_id": str(self.script.id), "page_number": 1, "content_type": "image/jpeg", "maximum_bytes": 2_000_000})
        self.assertEqual(issued.status_code, 200)
        self.assertEqual(issued.json()["asset_version"], 1)
        intent_id = issued.json()["id"]
        with patch("apps.repository.services.read_object_metadata", return_value=ObjectMetadata("a" * 64, 1024, "image/jpeg")):
            finalized = self.post(f"/api/v1/repository/uploads/{intent_id}/finalize", {"version": 1})
        self.assertEqual(finalized.status_code, 200)
        self.assertIsNone(finalized.json()["asset_id"])
        completed = self.post(f"/api/v1/repository/scripts/{self.script.id}/complete-scan", {"version": 1, "page_count": 1, "location": "Scanner A"})
        self.assertEqual(completed.status_code, 200)
        self.script.refresh_from_db()
        self.assertEqual(self.script.state, Script.State.SCANNED)
        self.assertEqual(self.script.page_count, 1)
        self.assertTrue(AuditEvent.objects.filter(action="repository.upload.finalized", aggregate_id=intent_id).exists())
        self.assertTrue(OutboxEvent.objects.filter(topic="repository.upload.finalized", aggregate_id=intent_id).exists())
        replacement = self.post("/api/v1/repository/manual-scan/uploads", {"script_id": str(self.script.id), "page_number": 1, "content_type": "image/jpeg", "maximum_bytes": 2_000_000})
        self.assertEqual(replacement.status_code, 200)
        self.assertEqual(replacement.json()["asset_version"], 2)

    def test_anonymized_master_is_immutable_and_gets_five_minute_url(self):
        self.script.state = Script.State.MASKED
        self.script.save(update_fields=["state"])
        issued = self.post("/api/v1/repository/uploads", {"script_id": str(self.script.id), "kind": "master", "page_number": 1, "asset_version": 1, "content_type": "image/webp"})
        self.assertEqual(issued.status_code, 200)
        with patch("apps.repository.services.read_object_metadata", return_value=ObjectMetadata("b" * 64, 2048, "image/webp")):
            finalized = self.post(f"/api/v1/repository/uploads/{issued.json()['id']}/finalize", {"version": 1})
        self.assertEqual(finalized.status_code, 200)
        asset = ScriptAsset.objects.get(id=finalized.json()["asset_id"])
        self.assertTrue(asset.storage_key.startswith("scripts-master/"))
        duplicate = self.post("/api/v1/repository/uploads", {"script_id": str(self.script.id), "kind": "master", "page_number": 1, "asset_version": 1, "content_type": "image/webp"})
        self.assertEqual(duplicate.status_code, 409)
        url_response = self.client.get(f"/api/v1/repository/assets/{asset.id}/url")
        self.assertEqual(url_response.status_code, 200)
        self.assertEqual(url_response.json()["ttl_seconds"], 300)

    def test_signed_url_never_exceeds_five_minutes(self):
        url, expires = signed_object_url(method="GET", key="scripts-evaluation/test/page.webp", ttl_seconds=3600)
        query = parse_qs(urlparse(url).query)
        self.assertEqual(int(query["expires"][0]), expires)
        import time

        self.assertLessEqual(expires - int(time.time()), 300)

    def test_master_asset_has_no_secure_delete_path(self):
        asset = ScriptAsset.objects.create(tenant_id=self.script.tenant_id, script=self.script, kind=ScriptAsset.Kind.MASTER, page_number=1, storage_key=f"scripts-master/{self.script.id}/page.webp", sha256="c" * 64, byte_size=100, mime_type="image/webp")
        self.assertEqual(self.post("/api/v1/auth/step-up", {"password": "ChangeMe123!"}).status_code, 200)
        response = self.post(f"/api/v1/repository/assets/{asset.id}/secure-delete", {"reason": "Should be forbidden"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(UploadIntent.objects.count(), 0)

    def test_archive_status_is_verified_by_storage_not_supplied_by_client(self):
        asset = ScriptAsset.objects.create(tenant_id=self.script.tenant_id, script=self.script, kind=ScriptAsset.Kind.EVALUATION, page_number=1, storage_key=f"scripts-evaluation/{self.script.id}/page.webp", sha256="d" * 64, byte_size=100, mime_type="image/webp")
        with patch("apps.repository.services.read_object_metadata", return_value=ObjectMetadata("d" * 64, 100, "image/webp", "completed", "completed")):
            response = self.post(f"/api/v1/repository/assets/{asset.id}/archive", {"backup_status": "failed", "replication_status": "failed"})
        self.assertEqual(response.status_code, 200)
        asset.refresh_from_db()
        self.assertEqual(asset.backup_status, "completed")
        self.assertEqual(asset.replication_status, "completed")
        self.assertIsNotNone(asset.integrity_checked_at)
