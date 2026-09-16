import hashlib
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from apps.configuration.models import Paper
from apps.core.models import AuditEvent, OutboxEvent
from apps.custody.models import Script
from apps.receiving.models import Dispatch, Packet
from apps.repository.models import ScriptAsset
from apps.repository.storage import ObjectMetadata
from apps.scanning.models import ScanBatch, ScanJob, ScannerDevice

from .models import ProcessedPage, ProcessingProfile, ProcessingRun, ScanQualityException
from .services import execute_run


class ScanProcessingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap_demo", verbosity=0)
        cls.actor = User.objects.get(username="admin@admiezo.local")

    def setUp(self):
        paper = Paper.objects.first()
        token = hashlib.sha256(self._testMethodName.encode()).hexdigest()[:10]
        dispatch = Dispatch.objects.create(tenant_id=paper.tenant_id, reference=f"PROC-{token}", paper=paper, source_centre="Test", expected_packets=1, expected_scripts=1)
        packet = Packet.objects.create(tenant_id=paper.tenant_id, dispatch=dispatch, barcode=f"PKT-PROC-{token}", expected_scripts=1)
        self.script = Script.objects.create(tenant_id=paper.tenant_id, script_code=f"AS-PROC-{token}", primary_barcode=f"BC-PROC-{token}", packet=packet, paper=paper, state=Script.State.SCANNED, page_count=2)
        scanner = ScannerDevice.objects.filter(tenant_id=paper.tenant_id).first()
        batch = ScanBatch.objects.create(tenant_id=paper.tenant_id, reference=f"BATCH-{token}", paper=paper, scanner=scanner, requested_topology="central", status=ScanBatch.Status.COMPLETED, expected_scripts=1, completed_scripts=1)
        self.job = ScanJob.objects.create(tenant_id=paper.tenant_id, batch=batch, script=self.script, status=ScanJob.Status.COMPLETED, expected_pages=2, scanned_pages=2, source_manifest=[{"storage_key": f"raw/{token}/1.webp", "sha256": "a" * 64, "page_number": 1}, {"storage_key": f"raw/{token}/2.webp", "sha256": "b" * 64, "page_number": 2}])
        self.profile = ProcessingProfile.objects.create(tenant_id=paper.tenant_id, code=f"P-{token}", name="Test", configuration={"resolution_dpi": 300, "minimum_quality_score": 65}, version=1)
        self.run = ProcessingRun.objects.create(tenant_id=paper.tenant_id, script=self.script, scan_job=self.job, profile=self.profile, source_digest="source")

    @patch("apps.scan_processing.services.process_scan_object")
    @patch("apps.scan_processing.services.read_object_metadata")
    def test_gateway_processing_creates_immutable_pages_and_quality_evidence(self, metadata, process):
        metadata.side_effect = [ObjectMetadata("a" * 64, 1000, "image/webp"), ObjectMetadata("b" * 64, 1000, "image/webp")]
        process.side_effect = lambda source_key, destination, configuration: {"object": {"key": destination["key"], "sha256": hashlib.sha256(destination["key"].encode()).hexdigest(), "byte_size": 800, "mime_type": "image/webp"}, "metrics": {"quality_score": 90, "is_blank": False, "resolution_dpi": 300, "rotation_degrees": 0, "width": 1200, "height": 1700}}
        completed = execute_run(tenant_id=self.script.tenant_id, actor_id=self.actor.id, run_id=self.run.id, expected_version=self.run.version)
        self.assertEqual(completed.status, ProcessingRun.Status.PASSED)
        self.assertEqual(ProcessedPage.objects.filter(run=self.run).count(), 2)
        self.assertEqual(ScriptAsset.objects.filter(script=self.script, kind=ScriptAsset.Kind.MASTER).count(), 2)
        self.script.refresh_from_db()
        self.assertEqual(self.script.state, Script.State.VALIDATED)
        self.assertTrue(AuditEvent.objects.filter(action="scan_processing.run.completed", aggregate_id=str(self.run.id)).exists())
        self.assertTrue(OutboxEvent.objects.filter(topic="scan_processing.run.completed", aggregate_id=str(self.run.id)).exists())

    @patch("apps.scan_processing.services.process_scan_object")
    @patch("apps.scan_processing.services.read_object_metadata")
    def test_low_quality_and_duplicate_pages_enter_review(self, metadata, process):
        self.job.source_manifest[1]["page_number"] = 1
        self.job.save(update_fields=["source_manifest"])
        metadata.side_effect = [ObjectMetadata("a" * 64, 1000, "image/webp"), ObjectMetadata("b" * 64, 1000, "image/webp")]
        process.side_effect = lambda source_key, destination, configuration: {"object": {"key": destination["key"], "sha256": "c" * 64, "byte_size": 800, "mime_type": "image/webp"}, "metrics": {"quality_score": 20, "is_blank": False, "resolution_dpi": 300, "rotation_degrees": 0}}
        completed = execute_run(tenant_id=self.script.tenant_id, actor_id=self.actor.id, run_id=self.run.id, expected_version=self.run.version)
        self.assertEqual(completed.status, ProcessingRun.Status.QUALITY_REVIEW)
        self.assertTrue(ScanQualityException.objects.filter(run=self.run, kind="low_quality").exists())
        self.assertTrue(ScanQualityException.objects.filter(run=self.run, kind="duplicate_page").exists())
        self.assertTrue(ScanQualityException.objects.filter(run=self.run, kind="missing_page").exists())
