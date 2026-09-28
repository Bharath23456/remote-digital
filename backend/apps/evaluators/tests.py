import json
from unittest.mock import patch

from django.core.management import call_command
from apps.core.testing import create_operational_fixtures
from django.test import Client, TestCase

from apps.allocation.models import Assignment
from apps.configuration.models import Subject
from apps.core.models import AuditEvent, OutboxEvent
from apps.evaluators.models import Evaluator, EvaluatorIdentityVerification, Expertise
from apps.tenancy.models import Membership


class EvaluatorManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap_demo", verbosity=0)
        create_operational_fixtures()

    def setUp(self):
        self.client = Client()
        response = self.client.post("/api/v1/auth/login", data=json.dumps({"email": "admin@admiezo.local", "password": "ChangeMe123!"}), content_type="application/json")
        self.assertEqual(response.status_code, 200)

    def post(self, path, payload):
        return self.client.post(path, data=json.dumps(payload), content_type="application/json")

    def face_capture(self):
        return {
            "image_base64": "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
            "liveness_passed": True,
            "face_count": 1,
            "quality": {"score": 0.94, "lighting": 0.86, "sharpness": 0.91},
            "model_version": "opencv-sface-v1",
            "device_fingerprint": "f" * 64,
        }

    @patch("apps.evaluators.services.extract_embedding", return_value=[1.0, 0.0, 0.0])
    def test_profile_expertise_availability_and_lifecycle(self, _extract_embedding):
        response = self.post("/api/v1/evaluator-management", {
            "evaluator_code": "EV-NEW-01", "display_name": "Dr. Test Examiner",
            "email": "examiner@example.edu", "institution_name": "Northbridge University",
            "department": "Computer Science", "designation": "Professor", "qualification": "PhD",
            "years_experience": 12, "daily_capacity": 24,
        })
        self.assertEqual(response.status_code, 200)
        evaluator = response.json()
        subject = Subject.objects.first()
        expertise = self.post(f"/api/v1/evaluator-management/{evaluator['id']}/expertise", {"subject_id": str(subject.id), "level": 5, "years_experience": 10})
        self.assertEqual(expertise.status_code, 200)
        availability = self.post(f"/api/v1/evaluator-management/{evaluator['id']}/availability", {"starts_on": "2026-09-01", "ends_on": "2026-12-31", "daily_capacity": 20, "notes": "Weekdays"})
        self.assertEqual(availability.status_code, 200)
        version = availability.json()["evaluator_version"]
        enrolled = self.post(f"/api/v1/evaluator-management/{evaluator['id']}/face/enroll", self.face_capture())
        self.assertEqual(enrolled.status_code, 200)
        lifecycle = self.post(f"/api/v1/evaluator-management/{evaluator['id']}/lifecycle", {"version": version, "status": "active", "reason": "Verification complete"})
        self.assertEqual(lifecycle.status_code, 200)
        self.assertEqual(lifecycle.json()["status"], "active")
        self.assertTrue(AuditEvent.objects.filter(action="evaluator.lifecycle.changed", aggregate_id=evaluator["id"]).exists())
        self.assertTrue(OutboxEvent.objects.filter(topic="evaluator.lifecycle.changed", aggregate_id=evaluator["id"]).exists())
        deactivated = self.post(f"/api/v1/evaluator-management/{evaluator['id']}/lifecycle", {"version": lifecycle.json()["version"], "status": "inactive", "reason": "Temporarily removed from evaluator pool"})
        self.assertEqual(deactivated.status_code, 200)
        self.assertEqual(deactivated.json()["status"], "inactive")

    def test_registration_stores_selected_subjects_as_pending_expertise(self):
        subjects = list(Subject.objects.all()[:2])
        self.assertEqual(len(subjects), 2)
        response = self.post("/api/v1/evaluator-management", {
            "evaluator_code": "EV-SUBJECT-01", "display_name": "Dr. Subject Examiner",
            "email": "subjects@example.edu", "institution_name": "Northbridge University",
            "department": "Computer Science", "designation": "Professor", "qualification": "PhD",
            "years_experience": 12, "daily_capacity": 20, "create_login": False,
            "subject_ids": [str(subject.id) for subject in subjects],
        })
        self.assertEqual(response.status_code, 200)
        expertise = Expertise.objects.filter(evaluator_id=response.json()["id"])
        self.assertEqual(set(expertise.values_list("subject_id", flat=True)), {subject.id for subject in subjects})
        self.assertFalse(expertise.filter(verified=True).exists())

    def test_profile_update_and_work_history_are_real_and_tenant_scoped(self):
        membership = Membership.objects.get(user__username="admin@admiezo.local")
        evaluator = Evaluator.objects.filter(tenant_id=membership.institution.tenant_id).first()
        response = self.client.patch(
            f"/api/v1/evaluator-management/{evaluator.id}",
            data=json.dumps({"version": evaluator.version, "mobile": "+91 9000000010", "daily_capacity": 26}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        evaluator.refresh_from_db()
        self.assertEqual(evaluator.mobile, "+91 9000000010")
        self.assertTrue(AuditEvent.objects.filter(action="evaluator.updated", aggregate_id=str(evaluator.id)).exists())

        history = self.client.get(f"/api/v1/evaluator-management/{evaluator.id}/history")
        work = self.client.get(f"/api/v1/evaluator-management/{evaluator.id}/work-history")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(work.status_code, 200)
        self.assertIn("summary", work.json())
        self.assertIn("assignments", work.json())
        self.assertNotIn("candidate_name", json.dumps(work.json()))

    @patch("apps.evaluators.services.extract_embedding", return_value=[1.0, 0.0, 0.0])
    def test_face_template_enrollment_status_and_audit_do_not_expose_template(self, _extract_embedding):
        membership = Membership.objects.get(user__username="admin@admiezo.local")
        evaluator = Evaluator.objects.filter(tenant_id=membership.institution.tenant_id, status=Evaluator.Status.ACTIVE).first()
        response = self.post(f"/api/v1/evaluator-management/{evaluator.id}/face/enroll", self.face_capture())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "active")
        self.assertNotIn("encrypted_template", body)
        self.assertNotIn("template_digest", body)

        status = self.client.get(f"/api/v1/evaluator-management/{evaluator.id}/face/status")
        self.assertEqual(status.status_code, 200)
        self.assertTrue(status.json()["enrolled"])
        self.assertTrue(AuditEvent.objects.filter(action="evaluator.face.enrolled", aggregate_id=body["id"]).exists())

    @patch("apps.evaluators.services.extract_embedding", return_value=[1.0, 0.0, 0.0])
    def test_evaluator_face_verification_gates_assignment_access(self, _extract_embedding):
        evaluator = Evaluator.objects.get(email="evaluator1043@admiezo.local")
        assignment = Assignment.objects.filter(tenant_id=evaluator.tenant_id, evaluator=evaluator).exclude(status=Assignment.Status.SUBMITTED).first()
        self.assertIsNotNone(assignment)
        enrolled = self.post(f"/api/v1/evaluator-management/{evaluator.id}/face/enroll", self.face_capture())
        self.assertEqual(enrolled.status_code, 200)

        evaluator_client = Client()
        login = evaluator_client.post("/api/v1/auth/login", data=json.dumps({"email": evaluator.email, "password": "ChangeMe123!", "device_id": "face-access-test"}), content_type="application/json")
        self.assertEqual(login.status_code, 200)
        verified = evaluator_client.post(
            "/api/v1/evaluator-management/face/verify-access",
            data=json.dumps({"assignment_id": str(assignment.id), **self.face_capture()}),
            content_type="application/json",
        )
        self.assertEqual(verified.status_code, 200)
        self.assertTrue(verified.json()["access_granted"])
        self.assertTrue(EvaluatorIdentityVerification.objects.filter(evaluator=evaluator, assignment=assignment, access_granted=True).exists())

    @patch("apps.evaluators.services.extract_embedding", side_effect=[[1.0, 0.0, 0.0], [-1.0, 0.0, 0.0]])
    def test_face_mismatch_is_denied_and_audited(self, _extract_embedding):
        evaluator = Evaluator.objects.get(email="evaluator1043@admiezo.local")
        assignment = Assignment.objects.filter(tenant_id=evaluator.tenant_id, evaluator=evaluator).exclude(status=Assignment.Status.SUBMITTED).first()
        self.assertEqual(self.post(f"/api/v1/evaluator-management/{evaluator.id}/face/enroll", self.face_capture()).status_code, 200)

        evaluator_client = Client()
        self.assertEqual(evaluator_client.post("/api/v1/auth/login", data=json.dumps({"email": evaluator.email, "password": "ChangeMe123!", "device_id": "face-deny-test"}), content_type="application/json").status_code, 200)
        denied = evaluator_client.post(
            "/api/v1/evaluator-management/face/verify-access",
            data=json.dumps({"assignment_id": str(assignment.id), **self.face_capture()}),
            content_type="application/json",
        )
        self.assertEqual(denied.status_code, 409)
        attempt = EvaluatorIdentityVerification.objects.filter(evaluator=evaluator, assignment=assignment).latest("created_at")
        self.assertFalse(attempt.access_granted)
        self.assertEqual(attempt.failure_reason, "face_mismatch")
        self.assertTrue(AuditEvent.objects.filter(action="evaluator.face.denied", aggregate_id=str(attempt.id)).exists())
