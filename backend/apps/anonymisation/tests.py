import hashlib
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.configuration.models import Paper
from apps.core.models import AuditEvent, OutboxEvent
from apps.custody.models import Script
from apps.receiving.models import Dispatch, Packet
from apps.repository.models import ScriptAsset, UploadIntent

from .models import IdentityLink, IdentityResolutionRequest, MaskingJob
from .services import (
    apply_masking_job,
    decide_identity_resolution,
    issue_resolution_authorization,
    request_identity_resolution,
    review_masking_job,
    start_masking_job,
    verify_masking_job,
)


class AnonymisationWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap_demo", verbosity=0)
        cls.actors = [User.objects.create_user(username=f"mask-{number}@example.test") for number in range(1, 6)]

    def setUp(self):
        paper = Paper.objects.first()
        case_id = hashlib.sha256(self._testMethodName.encode()).hexdigest()[:12]
        dispatch = Dispatch.objects.create(tenant_id=paper.tenant_id, reference=f"MASK-{case_id}", paper=paper, source_centre="Test", expected_packets=1, expected_scripts=1)
        packet = Packet.objects.create(tenant_id=paper.tenant_id, dispatch=dispatch, barcode=f"PKT-MASK-{case_id}", expected_scripts=1)
        self.script = Script.objects.create(tenant_id=paper.tenant_id, script_code=f"AS-MASK-{case_id}", primary_barcode=f"BC-MASK-{case_id}", packet=packet, paper=paper, state=Script.State.SCANNED, page_count=2, version=2)
        for page in (1, 2):
            UploadIntent.objects.create(tenant_id=paper.tenant_id, script=self.script, kind=UploadIntent.Kind.RAW_SCAN, page_number=page, storage_key=f"scripts-raw/{self.script.id}/page-{page}.png", content_type="image/png", maximum_bytes=1000, status=UploadIntent.Status.COMPLETED, expires_at=timezone.now() + timedelta(minutes=5), sha256="a" * 64, byte_size=100)

    @patch("apps.anonymisation.services.mask_object")
    def test_masking_requires_independent_operators_and_creates_repository_assets(self, mocked_mask):
        mocked_mask.side_effect = lambda source_key, destinations, regions: {
            "objects": [{"key": item["key"], "sha256": hashlib.sha256(item["key"].encode()).hexdigest(), "byte_size": 400, "mime_type": item["mime_type"]} for item in destinations]
        }
        job = start_masking_job(tenant_id=self.script.tenant_id, actor_id=self.actors[0].id, script=self.script, script_version=2, profile="university-standard-v1")
        self.script.refresh_from_db()
        self.assertEqual(self.script.state, Script.State.VALIDATED)
        job = review_masking_job(tenant_id=self.script.tenant_id, actor_id=self.actors[1].id, job_id=job.id, expected_version=job.version)
        job = apply_masking_job(tenant_id=self.script.tenant_id, actor_id=self.actors[2].id, job_id=job.id, expected_version=job.version)
        self.assertEqual(ScriptAsset.objects.filter(script=self.script).count(), 6)
        job = verify_masking_job(tenant_id=self.script.tenant_id, actor_id=self.actors[3].id, job_id=job.id, expected_version=job.version, passed=True, notes="All identity zones are opaque")
        self.script.refresh_from_db()
        self.assertEqual(job.status, MaskingJob.Status.VERIFIED)
        self.assertEqual(self.script.state, Script.State.STORED)
        self.assertTrue(AuditEvent.objects.filter(action="anonymisation.masking.verified", aggregate_id=str(job.id)).exists())
        self.assertTrue(OutboxEvent.objects.filter(topic="anonymisation.masking.verified", aggregate_id=str(job.id)).exists())

    def test_identity_resolution_requires_two_distinct_approvers_and_is_one_use(self):
        link = IdentityLink.objects.create(tenant_id=self.script.tenant_id, script=self.script, identity_reference="10000000-0000-0000-0000-000000000101", linked_by_id=self.actors[0].id, stored_at=timezone.now())
        item = request_identity_resolution(tenant_id=self.script.tenant_id, actor_id=self.actors[0].id, link=link, purpose="Result publication dispute investigation", emergency=False)
        item = decide_identity_resolution(tenant_id=self.script.tenant_id, actor_id=self.actors[1].id, request_id=item.id, expected_version=item.version, approved=True, note="Verified case reference")
        self.assertEqual(item.status, IdentityResolutionRequest.Status.PENDING)
        item = decide_identity_resolution(tenant_id=self.script.tenant_id, actor_id=self.actors[2].id, request_id=item.id, expected_version=item.version, approved=True, note="Independent approval")
        self.assertEqual(item.status, IdentityResolutionRequest.Status.APPROVED)
        item, token, expires = issue_resolution_authorization(tenant_id=self.script.tenant_id, actor_id=self.actors[0].id, request_id=item.id)
        self.assertEqual(item.status, IdentityResolutionRequest.Status.CONSUMED)
        self.assertEqual(len(token.split(".")), 2)
        self.assertLessEqual(expires - int(timezone.now().timestamp()), 300)
        with self.assertRaisesMessage(Exception, "Two current approvals"):
            issue_resolution_authorization(tenant_id=self.script.tenant_id, actor_id=self.actors[0].id, request_id=item.id)

    def test_core_models_do_not_contain_candidate_pii_fields(self):
        forbidden = {"candidate_name", "register_number", "usn", "college", "institution", "signature_reference", "photo_reference"}
        for model in (IdentityLink, IdentityResolutionRequest, MaskingJob):
            self.assertFalse(forbidden.intersection(field.name for field in model._meta.fields))

    def test_expired_resolution_decision_persists_terminal_state(self):
        link = IdentityLink.objects.create(tenant_id=self.script.tenant_id, script=self.script, identity_reference="10000000-0000-0000-0000-000000000102", linked_by_id=self.actors[0].id, stored_at=timezone.now())
        item = request_identity_resolution(tenant_id=self.script.tenant_id, actor_id=self.actors[0].id, link=link, purpose="Expired authorization test", emergency=False)
        item.expires_at = timezone.now() - timedelta(seconds=1)
        item.save(update_fields=["expires_at"])
        decided_identity = decide_identity_resolution(tenant_id=self.script.tenant_id, actor_id=self.actors[1].id, request_id=item.id, expected_version=item.version, approved=True, note="Must expire")
        self.assertEqual(decided_identity.status, IdentityResolutionRequest.Status.EXPIRED)
        self.assertEqual(IdentityResolutionRequest.objects.get(id=item.id).status, IdentityResolutionRequest.Status.EXPIRED)
        self.assertTrue(AuditEvent.objects.filter(action="anonymisation.identity_resolution.expired", aggregate_id=str(item.id)).exists())
