import json
import uuid
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from apps.core.testing import create_operational_fixtures
from django.test import Client, TestCase
from django.utils import timezone
from ninja.errors import HttpError

from apps.allocation.services import create_assignment
from apps.core.models import AuditEvent
from apps.custody.models import Script
from apps.eligibility.models import EligibilityRecord, VerificationApproval, VerificationCase, VerificationDocument
from apps.evaluators.models import Evaluator
from apps.repository.storage import ObjectMetadata


class EligibilityAllocationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap_demo", verbosity=0)
        create_operational_fixtures()

    def setUp(self):
        self.client = Client()
        response = self.client.post("/api/v1/auth/login", data=json.dumps({"email": "admin@admiezo.local", "password": "ChangeMe123!"}), content_type="application/json")
        self.assertEqual(response.status_code, 200)

    def post(self, path, payload):
        return self.client.post(path, data=json.dumps(payload), content_type="application/json", HTTP_IDEMPOTENCY_KEY=f"test-{uuid.uuid4()}")

    def authenticated(self, email):
        client = Client()
        response = client.post("/api/v1/auth/login", data=json.dumps({"email": email, "password": "ChangeMe123!"}), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        return client

    def test_verified_conflict_free_eligibility_controls_allocation(self):
        evaluator = Evaluator.objects.filter(status=Evaluator.Status.ACTIVE, expertise__verified=True).first()
        expertise = evaluator.expertise.filter(verified=True).first()
        EligibilityRecord.objects.filter(evaluator=evaluator).delete()
        VerificationApproval.objects.filter(verification__evaluator=evaluator).delete()
        VerificationCase.objects.filter(evaluator=evaluator).delete()
        checks = {key: True for key in ["official_id", "university_employee", "faculty", "mobile", "email", "institutional_email", "qualification", "experience", "institution", "department", "designation", "subject_expertise", "documents", "kyc"]}
        created = self.post("/api/v1/eligibility/verifications", {"evaluator_id": str(evaluator.id), "checks": checks, "notes": "Documents checked"}).json()
        VerificationDocument.objects.create(tenant_id=evaluator.tenant_id, verification_id=created["id"], kind="official_id", storage_key=f"test/{created['id']}.pdf", sha256="a" * 64, byte_size=1024, status=VerificationDocument.Status.COMPLETED, verified_at=timezone.now(), expires_at=timezone.now() + timedelta(minutes=5))
        submitted = self.post(f"/api/v1/eligibility/verifications/{created['id']}/submit", {"version": created["version"], "notes": "Ready for review"}).json()
        controller = self.authenticated("controller@admiezo.local")
        first = controller.post(f"/api/v1/eligibility/verifications/{created['id']}/approve", data=json.dumps({"version": submitted["version"], "notes": "Level one approved", "expires_on": str(date.today() + timedelta(days=365))}), content_type="application/json")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["status"], "in_review")
        reviewer = self.authenticated("reviewer@admiezo.local")
        approved = reviewer.post(f"/api/v1/eligibility/verifications/{created['id']}/approve", data=json.dumps({"version": first.json()["version"], "notes": "Level two approved", "expires_on": str(date.today() + timedelta(days=365))}), content_type="application/json")
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["approval_count"], 2)
        assessed = self.post("/api/v1/eligibility/subject-records", {"evaluator_id": str(evaluator.id), "subject_id": str(expertise.subject_id), "expires_on": str(date.today() + timedelta(days=180))})
        self.assertEqual(assessed.status_code, 200)
        self.assertEqual(assessed.json()["status"], "eligible")
        script = Script.objects.filter(state=Script.State.STORED, paper__subject=expertise.subject).first()
        assignment = create_assignment(tenant_id=evaluator.tenant_id, actor_id=User.objects.get(username="admin@admiezo.local").id, script=script, evaluator=evaluator, backup_evaluator=None, valuation_round=1, due_at=timezone.now() + timedelta(days=5), source="intelligent", quality_score=None, score_breakdown=None)
        self.assertEqual(assignment.evaluator_id, evaluator.id)

    def test_unverified_evaluator_is_hard_blocked(self):
        evaluator = Evaluator.objects.filter(status=Evaluator.Status.ACTIVE, expertise__verified=True).first()
        expertise = evaluator.expertise.filter(verified=True).first()
        EligibilityRecord.objects.filter(evaluator=evaluator).delete()
        VerificationApproval.objects.filter(verification__evaluator=evaluator).delete()
        VerificationCase.objects.filter(evaluator=evaluator).delete()
        script = Script.objects.filter(state=Script.State.STORED, paper__subject=expertise.subject).first()
        with self.assertRaises(HttpError):
            create_assignment(tenant_id=evaluator.tenant_id, actor_id=evaluator.user_id, script=script, evaluator=evaluator, backup_evaluator=None, valuation_round=1, due_at=timezone.now() + timedelta(days=5), source="intelligent", quality_score=None, score_breakdown=None)

    @patch("apps.eligibility.services.read_object_metadata")
    def test_verification_document_is_direct_upload_and_hash_finalized(self, metadata):
        evaluator = Evaluator.objects.filter(status=Evaluator.Status.ACTIVE).first()
        VerificationApproval.objects.filter(verification__evaluator=evaluator).delete()
        VerificationCase.objects.filter(evaluator=evaluator).delete()
        created = self.post("/api/v1/eligibility/verifications", {"evaluator_id": str(evaluator.id), "checks": {}, "notes": "Evidence intake"}).json()
        intent = self.post(f"/api/v1/eligibility/verifications/{created['id']}/documents", {"kind": "official_id", "content_type": "application/pdf", "maximum_bytes": 5000})
        self.assertEqual(intent.status_code, 200)
        self.assertIn("/storage/objects/evaluator-verification/", intent.json()["upload_url"])
        metadata.return_value = ObjectMetadata(sha256="b" * 64, byte_size=2048, mime_type="application/pdf")
        finalized = self.post(f"/api/v1/eligibility/documents/{intent.json()['id']}/finalize", {"version": intent.json()["version"]})
        self.assertEqual(finalized.status_code, 200)
        self.assertEqual(finalized.json()["sha256"], "b" * 64)
        self.assertTrue(AuditEvent.objects.filter(action="eligibility.document.verified", aggregate_id=intent.json()["id"]).exists())

    def test_draft_verification_checks_can_be_completed_before_submission(self):
        evaluator = Evaluator.objects.filter(status=Evaluator.Status.ACTIVE).first()
        VerificationApproval.objects.filter(verification__evaluator=evaluator).delete()
        VerificationCase.objects.filter(evaluator=evaluator).delete()
        created = self.post(
            "/api/v1/eligibility/verifications",
            {"evaluator_id": str(evaluator.id), "checks": {"official_id": True}, "notes": "Initial check"},
        ).json()
        incomplete = self.post(
            f"/api/v1/eligibility/verifications/{created['id']}/submit",
            {"version": created["version"], "notes": "Too early"},
        )
        self.assertEqual(incomplete.status_code, 422)
        checks = {key: True for key in ["official_id", "university_employee", "faculty", "mobile", "email", "institutional_email", "qualification", "experience", "institution", "department", "designation", "subject_expertise", "documents", "kyc"]}
        updated = self.client.patch(
            f"/api/v1/eligibility/verifications/{created['id']}",
            data=json.dumps({"version": created["version"], "checks": checks, "notes": "All checks complete"}),
            content_type="application/json",
        )
        self.assertEqual(updated.status_code, 200)
        self.assertTrue(all(updated.json()["checks"].values()))
        self.assertTrue(AuditEvent.objects.filter(action="eligibility.verification.updated", aggregate_id=created["id"]).exists())
