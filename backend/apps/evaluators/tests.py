import json

from django.core.management import call_command
from django.test import Client, TestCase

from apps.configuration.models import Subject
from apps.core.models import AuditEvent, OutboxEvent
from apps.evaluators.models import Evaluator
from apps.tenancy.models import Membership


class EvaluatorManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap_demo", verbosity=0)

    def setUp(self):
        self.client = Client()
        response = self.client.post("/api/v1/auth/login", data=json.dumps({"email": "admin@admiezo.local", "password": "ChangeMe123!"}), content_type="application/json")
        self.assertEqual(response.status_code, 200)

    def post(self, path, payload):
        return self.client.post(path, data=json.dumps(payload), content_type="application/json")

    def test_profile_expertise_availability_and_lifecycle(self):
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
        lifecycle = self.post(f"/api/v1/evaluator-management/{evaluator['id']}/lifecycle", {"version": version, "status": "active", "reason": "Verification complete"})
        self.assertEqual(lifecycle.status_code, 200)
        self.assertEqual(lifecycle.json()["status"], "active")
        self.assertTrue(AuditEvent.objects.filter(action="evaluator.lifecycle.changed", aggregate_id=evaluator["id"]).exists())
        self.assertTrue(OutboxEvent.objects.filter(topic="evaluator.lifecycle.changed", aggregate_id=evaluator["id"]).exists())
        deactivated = self.post(f"/api/v1/evaluator-management/{evaluator['id']}/lifecycle", {"version": lifecycle.json()["version"], "status": "inactive", "reason": "Temporarily removed from evaluator pool"})
        self.assertEqual(deactivated.status_code, 200)
        self.assertEqual(deactivated.json()["status"], "inactive")

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
