import json
import uuid

from django.core.management import call_command
from django.test import Client, TestCase

from apps.configuration.models import ConfigurationChangeRequest, ConfigurationRevision, Course, EvaluationCentre, ExamSession, Paper, Programme, Regulation, Subject
from apps.core.models import AuditEvent, OutboxEvent


class ConfigurationWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap_demo", verbosity=0)

    def setUp(self):
        self.client = Client()
        response = self.client.post(
            "/api/v1/auth/login",
            data=json.dumps({"email": "admin@admiezo.local", "password": "ChangeMe123!"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

    def post(self, path, payload):
        return self.client.post(path, data=json.dumps(payload), content_type="application/json", HTTP_IDEMPOTENCY_KEY=f"test-{uuid.uuid4()}")

    def authenticated_client(self, email):
        client = Client()
        response = client.post(
            "/api/v1/auth/login",
            data=json.dumps({"email": email, "password": "ChangeMe123!"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        return client

    def test_complete_paper_configuration_lifecycle(self):
        session = ExamSession.objects.first()
        subject = Subject.objects.first()
        response = self.post(
            "/api/v1/configuration/papers",
            {
                "session_id": str(session.id),
                "subject_id": str(subject.id),
                "code": "LIFECYCLE-101",
                "title": "Lifecycle verification paper",
                "max_marks": "100.00",
                "pass_marks": "40.00",
                "valuation_rounds": 2,
                "discrepancy_threshold": "15.00",
                "moderation_required": True,
                "rules": {"revaluation": True},
            },
        )
        self.assertEqual(response.status_code, 200)
        paper = response.json()
        self.assertFalse(paper["readiness"]["ready"])

        self.post(
            f"/api/v1/configuration/papers/{paper['id']}/questions",
            {"number": "Q1", "max_marks": "50.00", "required": True, "position": 1},
        ).json()
        second = self.post(
            f"/api/v1/configuration/papers/{paper['id']}/questions",
            {"number": "Q2", "max_marks": "50.00", "required": True, "position": 2},
        ).json()
        self.assertTrue(second["readiness"]["ready"])

        submit_path = f"/api/v1/configuration/papers/{paper['id']}/submit"
        submit_payload = {"version": second["paper_version"], "note": "Configuration reviewed"}
        idempotency_key = f"submit-{uuid.uuid4()}"
        submitted = self.client.post(submit_path, data=json.dumps(submit_payload), content_type="application/json", HTTP_IDEMPOTENCY_KEY=idempotency_key)
        self.assertEqual(submitted.status_code, 200)
        self.assertEqual(submitted.json()["status"], "review")
        replay = self.client.post(submit_path, data=json.dumps(submit_payload), content_type="application/json", HTTP_IDEMPOTENCY_KEY=idempotency_key)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["version"], submitted.json()["version"])
        self.assertEqual(OutboxEvent.objects.filter(topic="config.paper.submitted", aggregate_id=paper["id"]).count(), 1)

        approved = self.post(
            f"/api/v1/configuration/papers/{paper['id']}/approve",
            {"version": submitted.json()["version"], "note": "Approved for evaluation"},
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["status"], "approved")

        frozen = self.post(
            f"/api/v1/configuration/papers/{paper['id']}/freeze",
            {"version": approved.json()["version"], "note": "Go-live configuration"},
        )
        self.assertEqual(frozen.status_code, 200)
        self.assertEqual(frozen.json()["status"], "frozen")
        self.assertEqual(ConfigurationRevision.objects.filter(aggregate_id=paper["id"]).count(), 6)
        self.assertTrue(AuditEvent.objects.filter(action="config.paper.frozen", aggregate_id=paper["id"]).exists())
        self.assertTrue(OutboxEvent.objects.filter(topic="config.paper.frozen", aggregate_id=paper["id"]).exists())

    def test_cross_tenant_related_record_is_rejected(self):
        session = ExamSession.objects.first()
        subject = Subject.objects.first()
        subject.tenant_id = "10000000-0000-0000-0000-000000000001"
        subject.save(update_fields=["tenant_id"])
        response = self.post(
            "/api/v1/configuration/papers",
            {
                "session_id": str(session.id),
                "subject_id": str(subject.id),
                "code": "CROSS-TENANT",
                "title": "Must fail",
                "max_marks": "100",
                "pass_marks": "40",
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_evaluation_event_is_tenant_scoped_and_inside_session_window(self):
        session = ExamSession.objects.first()
        centre = EvaluationCentre.objects.create(
            tenant_id=session.tenant_id,
            code="CENTRE-A",
            name="Evaluation Centre A",
        )
        response = self.post(
            "/api/v1/configuration/events",
            {
                "session_id": str(session.id),
                "name": "Primary valuation",
                "starts_at": session.evaluation_starts_at.isoformat(),
                "ends_at": session.evaluation_ends_at.isoformat(),
                "evaluation_centre_ids": [str(centre.id)],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(AuditEvent.objects.filter(action="config.evaluation_event.created", aggregate_id=response.json()["id"]).exists())
        readiness = self.client.get("/api/v1/configuration/readiness")
        self.assertEqual(readiness.status_code, 200)
        self.assertIn(readiness.json()["decision"], {"go_live", "not_ready"})

    def test_subject_can_be_linked_to_course_sessions_and_related_subjects(self):
        programme = Programme.objects.first()
        regulation = Regulation.objects.create(
            tenant_id=programme.tenant_id,
            code=f"REG-{uuid.uuid4().hex[:8]}",
            title="Linked subject regulation",
            effective_from="2026-01-01",
        )
        course = Course.objects.create(
            tenant_id=programme.tenant_id,
            programme=programme,
            regulation=regulation,
            code=f"COURSE-{uuid.uuid4().hex[:8]}",
            name="Linked subject course",
            duration_terms=8,
        )
        session = ExamSession.objects.first()
        related = Subject.objects.filter(programme=programme).first()
        response = self.post(
            "/api/v1/configuration/subjects",
            {
                "programme_id": str(programme.id),
                "course_id": str(course.id),
                "session_ids": [str(session.id)],
                "related_subject_ids": [str(related.id)],
                "code": f"SUBJECT-{uuid.uuid4().hex[:8]}",
                "name": "Linked subject configuration",
                "semester": 2,
            },
        )
        self.assertEqual(response.status_code, 200)
        subject = Subject.objects.get(id=response.json()["id"])
        self.assertEqual(subject.course_id, course.id)
        self.assertEqual(subject.session_ids, [str(session.id)])
        self.assertEqual(subject.related_subject_ids, [str(related.id)])

    def test_active_paper_change_requires_two_independent_approvers(self):
        paper = Paper.objects.filter(status=Paper.Status.FROZEN).first()
        direct = self.client.patch(
            f"/api/v1/configuration/papers/{paper.id}",
            data=json.dumps({"version": paper.version, "title": "Unsafe direct edit"}),
            content_type="application/json",
        )
        self.assertEqual(direct.status_code, 409)

        requested = self.post(
            f"/api/v1/configuration/papers/{paper.id}/changes",
            {
                "version": paper.version,
                "kind": "emergency_update",
                "reason": "Correct an approved paper title before evaluation continues",
                "changes": {"title": "Controlled emergency title"},
            },
        )
        self.assertEqual(requested.status_code, 200)
        change = requested.json()
        self.assertTrue(change["impact"]["is_active_evaluation"])

        self_approval = self.post(
            f"/api/v1/configuration/changes/{change['id']}/decision",
            {"version": change["version"], "decision": "approved", "note": "Must not pass"},
        )
        self.assertEqual(self_approval.status_code, 409)

        first = self.authenticated_client("controller@admiezo.local").post(
            f"/api/v1/configuration/changes/{change['id']}/decision",
            data=json.dumps({"version": change["version"], "decision": "approved", "note": "Impact reviewed"}),
            content_type="application/json",
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["change"]["status"], ConfigurationChangeRequest.Status.PENDING)
        second = self.authenticated_client("reviewer@admiezo.local").post(
            f"/api/v1/configuration/changes/{change['id']}/decision",
            data=json.dumps({"version": first.json()["change"]["version"], "decision": "approved", "note": "Emergency change authorized"}),
            content_type="application/json",
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["change"]["status"], ConfigurationChangeRequest.Status.APPLIED)
        paper.refresh_from_db()
        self.assertEqual(paper.title, "Controlled emergency title")
        self.assertTrue(ConfigurationRevision.objects.filter(aggregate_id=paper.id, change_type=ConfigurationRevision.ChangeType.EMERGENCY).exists())
        self.assertTrue(AuditEvent.objects.filter(action="config.change.applied", aggregate_id=change["id"]).exists())
        self.assertTrue(OutboxEvent.objects.filter(topic="config.change.applied", aggregate_id=change["id"]).exists())

    def test_questions_can_only_be_added_to_draft_papers(self):
        paper = Paper.objects.filter(status=Paper.Status.REVIEW).first()
        original_version = paper.version
        payload = {"number": "Q-LOCKED", "max_marks": "1.00", "required": True, "position": 99}

        in_review = self.post(f"/api/v1/configuration/papers/{paper.id}/questions", payload)
        self.assertEqual(in_review.status_code, 409)
        self.assertIn("only be changed while a paper is in Draft", in_review.json()["detail"])
        self.assertFalse(paper.questions.filter(number="Q-LOCKED").exists())

        paper.status = Paper.Status.APPROVED
        paper.save(update_fields=["status"])
        approved = self.post(f"/api/v1/configuration/papers/{paper.id}/questions", payload)
        self.assertEqual(approved.status_code, 409)

        paper.status = Paper.Status.FROZEN
        paper.save(update_fields=["status"])
        frozen = self.post(f"/api/v1/configuration/papers/{paper.id}/questions", payload)
        self.assertEqual(frozen.status_code, 409)
        paper.refresh_from_db()
        self.assertEqual(paper.version, original_version)
        self.assertFalse(paper.questions.filter(number="Q-LOCKED").exists())

    def test_question_detail_update_and_delete_are_versioned_and_audited(self):
        source = Paper.objects.first()
        created = self.post("/api/v1/configuration/papers", {
            "session_id": str(source.session_id), "subject_id": str(source.subject_id),
            "code": f"QUESTION-{uuid.uuid4().hex[:8]}", "title": "Question editing",
            "max_marks": "20.00", "pass_marks": "8.00",
        })
        self.assertEqual(created.status_code, 200)
        paper_id = created.json()["id"]
        added = self.post(f"/api/v1/configuration/papers/{paper_id}/questions", {
            "number": "Q1", "sub_question": "a", "max_marks": "20.00", "question_type": "descriptive",
        })
        self.assertEqual(added.status_code, 200)
        question_id = added.json()["question"]["id"]
        self.assertEqual(added.json()["question"]["position"], 1)
        detail = self.client.get(f"/api/v1/configuration/papers/{paper_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["questions"][0]["sub_question"], "a")

        update_path = f"/api/v1/configuration/papers/{paper_id}/questions/{question_id}"
        updated = self.client.patch(update_path, data=json.dumps({
            "version": added.json()["paper_version"], "number": "Q1", "sub_question": "b",
            "max_marks": "20.00", "question_type": "objective", "required": True,
        }), content_type="application/json")
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["question"]["question_type"], "objective")
        self.assertEqual(updated.json()["question"]["sub_question"], "b")
        stale = self.client.delete(update_path, data=json.dumps({"version": added.json()["paper_version"]}), content_type="application/json")
        self.assertEqual(stale.status_code, 409)
        deleted = self.client.delete(update_path, data=json.dumps({"version": updated.json()["paper_version"]}), content_type="application/json")
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(Paper.objects.get(id=paper_id).questions.exists())
        self.assertTrue(AuditEvent.objects.filter(action="config.question.updated", aggregate_id=question_id).exists())
        self.assertTrue(AuditEvent.objects.filter(action="config.question.deleted", aggregate_id=question_id).exists())

    def test_question_edit_rejects_non_draft_paper(self):
        paper = Paper.objects.filter(status=Paper.Status.REVIEW).first()
        question = paper.questions.first()
        response = self.client.patch(
            f"/api/v1/configuration/papers/{paper.id}/questions/{question.id}",
            data=json.dumps({"version": paper.version, "number": question.number, "max_marks": str(question.max_marks)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)
