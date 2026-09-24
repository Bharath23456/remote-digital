import json
import uuid

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase

from apps.configuration.models import AcademicYear, ConfigurationChangeRequest, ConfigurationRevision, Course, EvaluationCentre, ExamSession, Paper, Programme, Regulation, Subject, Term
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

    def test_one_round_score_trigger_is_validated_separately_from_difference_threshold(self):
        session = ExamSession.objects.first()
        subject = Subject.objects.first()
        payload = {
            "session_id": str(session.id), "subject_id": str(subject.id), "code": "SCORE-TRIGGER-101",
            "title": "Conditional second valuation", "max_marks": "100.00", "pass_marks": "40.00",
            "valuation_rounds": 1, "discrepancy_threshold": "10.00",
            "rules": {"second_valuation_mark_threshold": "75.00"},
        }
        created = self.post("/api/v1/configuration/papers", payload)
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["rules"]["second_valuation_mark_threshold"], "75.00")
        invalid_rounds = self.post("/api/v1/configuration/papers", {**payload, "code": "SCORE-TRIGGER-102", "valuation_rounds": 2})
        self.assertEqual(invalid_rounds.status_code, 422)
        invalid_score = self.post("/api/v1/configuration/papers", {**payload, "code": "SCORE-TRIGGER-103", "rules": {"second_valuation_mark_threshold": "100.00"}})
        self.assertEqual(invalid_score.status_code, 422)

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

        self_approval = self.post(f"/api/v1/configuration/papers/{paper['id']}/approve", {"version": submitted.json()["version"], "note": "Must be rejected"})
        self.assertEqual(self_approval.status_code, 409)
        approved = self.authenticated_client("controller@admiezo.local").post(
            f"/api/v1/configuration/papers/{paper['id']}/approve",
            data=json.dumps({"version": submitted.json()["version"], "note": "Approved for evaluation"}), content_type="application/json",
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["status"], "approved")

        self.assertEqual(self.post(f"/api/v1/configuration/papers/{paper['id']}/freeze", {"version": approved.json()["version"], "note": "Must be rejected"}).status_code, 409)
        approver_freeze = self.authenticated_client("controller@admiezo.local").post(
            f"/api/v1/configuration/papers/{paper['id']}/freeze",
            data=json.dumps({"version": approved.json()["version"], "note": "Must be rejected"}), content_type="application/json",
        )
        self.assertEqual(approver_freeze.status_code, 409)

        frozen = self.authenticated_client("reviewer@admiezo.local").post(
            f"/api/v1/configuration/papers/{paper['id']}/freeze",
            data=json.dumps({"version": approved.json()["version"], "note": "Go-live configuration"}), content_type="application/json",
        )
        self.assertEqual(frozen.status_code, 200)
        self.assertEqual(frozen.json()["status"], "frozen")
        self.assertEqual(ConfigurationRevision.objects.filter(aggregate_id=paper["id"]).count(), 6)
        stored = Paper.objects.get(id=paper["id"])
        self.assertEqual(stored.submitted_by_id, User.objects.get(username="admin@admiezo.local").id)
        self.assertEqual(stored.approved_by_id, User.objects.get(username="controller@admiezo.local").id)
        self.assertEqual(stored.frozen_by_id, User.objects.get(username="reviewer@admiezo.local").id)
        frozen_audit = AuditEvent.objects.get(action="config.paper.frozen", aggregate_id=paper["id"])
        self.assertEqual(frozen_audit.payload["submitted_by_id"], stored.submitted_by_id)
        self.assertEqual(frozen_audit.payload["approved_by_id"], stored.approved_by_id)
        self.assertEqual(frozen_audit.payload["frozen_by_id"], stored.frozen_by_id)
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

    def test_event_requires_assigned_active_centre_and_retry_is_idempotent(self):
        session = ExamSession.objects.first()
        payload = {"session_id": str(session.id), "name": "Controlled event", "starts_at": session.evaluation_starts_at.isoformat(), "ends_at": session.evaluation_ends_at.isoformat(), "evaluation_centre_ids": []}
        self.assertEqual(self.post("/api/v1/configuration/events", payload).status_code, 422)
        centre = EvaluationCentre.objects.create(tenant_id=session.tenant_id, code="RETRY-CENTRE", name="Retry centre")
        payload["evaluation_centre_ids"] = [str(centre.id)]
        key = f"event-{uuid.uuid4()}"
        first = self.client.post("/api/v1/configuration/events", data=json.dumps(payload), content_type="application/json", HTTP_IDEMPOTENCY_KEY=key)
        second = self.client.post("/api/v1/configuration/events", data=json.dumps(payload), content_type="application/json", HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.json()["id"], first.json()["id"])
        centre.is_active = False
        centre.save(update_fields=["is_active"])
        readiness = self.client.get("/api/v1/configuration/readiness").json()
        self.assertFalse(readiness["ready"])
        self.assertTrue(any("centre" in item for item in readiness["critical_alerts"]))

    def test_paper_numeric_and_subject_session_rules(self):
        session = ExamSession.objects.first()
        subject = Subject.objects.first()
        payload = {"session_id": str(session.id), "subject_id": str(subject.id), "code": "ZERO-TEST", "title": "Zero paper", "max_marks": "0", "pass_marks": "0"}
        self.assertEqual(self.post("/api/v1/configuration/papers", payload).status_code, 422)
        subject.session_ids = []
        subject.save(update_fields=["session_ids"])
        payload["max_marks"] = "10"
        self.assertEqual(self.post("/api/v1/configuration/papers", payload).status_code, 422)

    def test_linked_master_validation_and_revision(self):
        year = AcademicYear.objects.first()
        session = ExamSession.objects.first()
        term = Term.objects.filter(academic_year=year).first()
        self.assertEqual(self.post("/api/v1/configuration/sessions", {"academic_year_id": str(year.id), "term_id": str(term.id), "name": "Out of window", "evaluation_starts_at": "2030-01-01T09:00:00Z", "evaluation_ends_at": "2030-01-02T09:00:00Z"}).status_code, 422)
        programme = Programme.objects.first()
        other_regulation = Regulation.objects.create(tenant_id=programme.tenant_id, code="OTHER-REG", title="Other", effective_from="2020-01-01")
        self.assertEqual(self.post("/api/v1/configuration/courses", {"programme_id": str(programme.id), "regulation_id": str(other_regulation.id), "code": "WRONG-COURSE", "name": "Wrong", "duration_terms": 0}).status_code, 422)
        centre = EvaluationCentre.objects.create(tenant_id=year.tenant_id, code="EDIT-CENTRE", name="Edit centre")
        invalid = self.client.patch(f"/api/v1/configuration/masters/centres/{centre.id}", data=json.dumps({"version": 1, "changes": {"network_cidrs": ["not-a-cidr"]}, "reason": "Configure network"}), content_type="application/json")
        self.assertEqual(invalid.status_code, 422)
        updated = self.client.patch(f"/api/v1/configuration/masters/centres/{centre.id}", data=json.dumps({"version": 1, "changes": {"network_cidrs": ["10.0.0.0/8"]}, "reason": "Configure network"}), content_type="application/json")
        self.assertEqual(updated.status_code, 200)
        self.assertTrue(ConfigurationRevision.objects.filter(aggregate_id=centre.id, version=2).exists())
        self.assertEqual(session.term_record_id, term.id)

    def test_post_submission_question_change_reopens_paper(self):
        source = Paper.objects.first()
        created = self.post("/api/v1/configuration/papers", {"session_id": str(source.session_id), "subject_id": str(source.subject_id), "code": f"REVISE-{uuid.uuid4().hex[:8]}", "title": "Revision paper", "max_marks": "20", "pass_marks": "8"})
        self.assertEqual(created.status_code, 200)
        paper_id = created.json()["id"]
        added = self.post(f"/api/v1/configuration/papers/{paper_id}/questions", {"number": "Q1", "max_marks": "20"})
        submitted = self.post(f"/api/v1/configuration/papers/{paper_id}/submit", {"version": added.json()["paper_version"], "note": "Ready for review"})
        self.assertEqual(submitted.status_code, 200)
        request = self.post(f"/api/v1/configuration/papers/{paper_id}/changes", {"version": submitted.json()["version"], "kind": "emergency_update", "reason": "Correct the first question before evaluation", "changes": {"questions": [{"number": "Q1", "max_marks": "20", "question_type": "objective", "required": True}]}})
        self.assertEqual(request.status_code, 200)
        change = request.json()
        first = self.authenticated_client("controller@admiezo.local").post(f"/api/v1/configuration/changes/{change['id']}/decision", data=json.dumps({"version": change["version"], "decision": "approved"}), content_type="application/json")
        self.assertEqual(first.status_code, 200)
        second = self.authenticated_client("reviewer@admiezo.local").post(f"/api/v1/configuration/changes/{change['id']}/decision", data=json.dumps({"version": first.json()["change"]["version"], "decision": "approved"}), content_type="application/json")
        self.assertEqual(second.status_code, 200)
        paper = Paper.objects.get(id=paper_id)
        self.assertEqual(paper.status, Paper.Status.DRAFT)
        self.assertIsNone(paper.submitted_by_id)
        self.assertEqual(paper.questions.get().question_type, "objective")

    def test_session_readiness_requires_independent_approval_and_live_centre(self):
        session = ExamSession.objects.first()
        session.status = ExamSession.Status.DRAFT
        session.save(update_fields=["status"])
        submitted = self.post(f"/api/v1/configuration/sessions/{session.id}/transition", {"version": session.version, "target": "approval", "reason": "Request readiness review"})
        self.assertEqual(submitted.status_code, 200)
        blocked = self.post(f"/api/v1/configuration/sessions/{session.id}/transition", {"version": submitted.json()["version"], "target": "ready", "reason": "Must be independent"})
        self.assertEqual(blocked.status_code, 409)
        controller = self.authenticated_client("controller@admiezo.local")
        missing_centre = controller.post(f"/api/v1/configuration/sessions/{session.id}/transition", data=json.dumps({"version": submitted.json()["version"], "target": "ready", "reason": "Review readiness"}), content_type="application/json")
        self.assertEqual(missing_centre.status_code, 409)
        self.assertEqual(self.client.get("/api/v1/configuration/readiness").json()["decision"], "not_ready")

    def test_calendar_overlap_requires_independent_one_use_exception(self):
        payload = {"label": "2027-exception", "starts_on": "2027-01-01", "ends_on": "2027-12-31"}
        self.assertEqual(self.post("/api/v1/configuration/academic-years", payload).status_code, 409)
        requested = self.post("/api/v1/configuration/calendar-exceptions", {"entity": "academic_year", "starts_on": payload["starts_on"], "ends_on": payload["ends_on"], "reason": "University approved parallel academic calendar"})
        self.assertEqual(requested.status_code, 200)
        exception_id = requested.json()["id"]
        self.assertEqual(self.post(f"/api/v1/configuration/calendar-exceptions/{exception_id}/decision", {"approve": True}).status_code, 409)
        approved = self.authenticated_client("controller@admiezo.local").post(f"/api/v1/configuration/calendar-exceptions/{exception_id}/decision", data=json.dumps({"approve": True}), content_type="application/json")
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(self.post("/api/v1/configuration/academic-years", payload).status_code, 200)
        second = {**payload, "label": "same-window-reuse"}
        self.assertEqual(self.post("/api/v1/configuration/academic-years", second).status_code, 409)

    def test_oversized_master_labels_are_rejected_without_writes(self):
        before = AcademicYear.objects.count()
        response = self.post("/api/v1/configuration/academic-years", {
            "label": "x" * 21, "starts_on": "2035-01-01", "ends_on": "2035-12-31",
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(AcademicYear.objects.count(), before)
        year = AcademicYear.objects.first()
        original_label, original_version = year.label, year.version
        response = self.client.patch(
            f"/api/v1/configuration/masters/academic-years/{year.id}",
            data=json.dumps({"version": year.version, "changes": {"label": "x" * 21}, "reason": "Validate label length"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 422)
        year.refresh_from_db()
        self.assertEqual((year.label, year.version), (original_label, original_version))

    def test_linked_session_create_retry_is_idempotent(self):
        year = AcademicYear.objects.first()
        term = Term.objects.filter(academic_year=year).first()
        payload = {"academic_year_id": str(year.id), "term_id": str(term.id), "name": "Idempotent linked session", "evaluation_starts_at": "2026-10-01T09:00:00Z", "evaluation_ends_at": "2026-10-10T17:00:00Z"}
        key = f"session-{uuid.uuid4()}"
        first = self.client.post("/api/v1/configuration/sessions", data=json.dumps(payload), content_type="application/json", HTTP_IDEMPOTENCY_KEY=key)
        second = self.client.post("/api/v1/configuration/sessions", data=json.dumps(payload), content_type="application/json", HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(ExamSession.objects.get(id=first.json()["id"]).term_record_id, term.id)
