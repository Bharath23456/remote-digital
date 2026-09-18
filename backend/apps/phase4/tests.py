from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
import json
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from ninja.errors import HttpError

from apps.allocation.models import Assignment
from apps.configuration.models import ExamSession, Paper
from apps.core.models import OutboxEvent
from apps.custody.models import Script
from apps.evaluators.models import Evaluator
from apps.evaluators.services import enroll_face_template, verify_evaluator_access
from apps.identity_auth.models import AccessSession
from apps.marking.models import Evaluation
from apps.repository.models import ScriptAsset
from apps.valuation.models import FinalMark, ValuationResult
from apps.workflow.models import EvaluationWorkflow

from .models import (
    AttendanceRecord,
    CentreProfile,
    CentreReadiness,
    CompletionRecord,
    ControlledAuthorization,
    EvidencePackage,
    IntegrationEndpoint,
    NotificationDelivery,
    ProctoringReview,
    RecoveryDrill,
    RecoveryPlan,
    RemunerationRule,
    ResultHandover,
    SecureEvaluationSession,
    StudentScriptRequest,
    WorkloadAction,
)
from .services import (
    acknowledge_handover,
    calculate_remuneration,
    consume_authorization,
    create_issue,
    create_notification,
    create_student_request,
    create_workload_action,
    decide_authorization,
    notification_action,
    queue_handover,
    request_authorization,
    seal_evidence,
    set_locale,
    transition_centre,
    transition_drill,
    transition_handover,
    transition_statement,
    transition_workload,
)


class RemainingModulesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap_demo", verbosity=0)
        cls.admin = User.objects.get(username="admin@admiezo.local")
        cls.controller = User.objects.get(username="controller@admiezo.local")
        cls.paper = Paper.objects.get(code="CS401-A")
        cls.tenant_id = cls.paper.tenant_id
        cls.session = ExamSession.objects.get(id=cls.paper.session_id)
        cls.evaluator = Evaluator.objects.filter(tenant_id=cls.tenant_id, status=Evaluator.Status.ACTIVE).first()

    def face_capture(self):
        return {
            "image_base64": "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
            "liveness_passed": True,
            "face_count": 1,
            "quality": {"score": 0.95, "lighting": 0.88},
            "model_version": "opencv-sface-v1",
            "device_fingerprint": "f" * 64,
        }

    def verify_identity_for_secure_session(self, evaluator, assignment):
        enroll_face_template(tenant_id=self.tenant_id, actor_id=self.admin.id, evaluator_id=evaluator.id, capture=self.face_capture())
        access_session = AccessSession.objects.filter(user_id=evaluator.user_id, tenant_id=self.tenant_id, revoked_at__isnull=True).latest("created_at")
        verify_evaluator_access(tenant_id=self.tenant_id, actor_id=evaluator.user_id, evaluator=evaluator, assignment=assignment, access_session=access_session, capture=self.face_capture())

    def test_demo_face_bypass_requires_explicit_setting_and_keeps_secure_session(self):
        evaluator = Evaluator.objects.get(email="evaluator1043@admiezo.local")
        assignment = Assignment.objects.filter(tenant_id=self.tenant_id, evaluator=evaluator).exclude(status=Assignment.Status.SUBMITTED).first()
        client = Client()
        login = client.post("/api/v1/auth/login", data=json.dumps({"email": evaluator.email, "password": "ChangeMe123!", "device_id": "face-bypass-test"}), content_type="application/json")
        self.assertEqual(login.status_code, 200)
        payload = {
            "assignment_id": str(assignment.id),
            "session_fingerprint": "a" * 64,
            "device_fingerprint": "b" * 64,
            "consent": True,
            "preflight": {"camera_ready": True, "fullscreen_active": True, "screen_count": 1},
            "device_inventory": {"video_inputs": 1, "digest": "c" * 64},
        }
        with override_settings(DEMO_SKIP_EVALUATOR_FACE_VERIFICATION=False):
            self.assertTrue(client.get("/api/v1/phase4/remote-security/policy").json()["identity_verification_required"])
            blocked = client.post("/api/v1/phase4/remote-security/sessions", data=json.dumps(payload), content_type="application/json")
            self.assertEqual(blocked.status_code, 428)
        with override_settings(DEMO_SKIP_EVALUATOR_FACE_VERIFICATION=True):
            self.assertFalse(client.get("/api/v1/phase4/remote-security/policy").json()["identity_verification_required"])
            started = client.post("/api/v1/phase4/remote-security/sessions", data=json.dumps(payload), content_type="application/json")
            self.assertEqual(started.status_code, 200)
            self.assertEqual(started.json()["policy"]["identity_verification_required"], False)
            self.assertEqual(SecureEvaluationSession.objects.get(id=started.json()["id"]).evaluator_id, evaluator.id)

    def test_workload_actions_require_independent_approval(self):
        item = create_workload_action(tenant_id=self.tenant_id, actor_id=self.admin.id, evaluator=self.evaluator, action="rebalance", reason="Deadline capacity requires redistribution.", metrics={"remaining": 28})
        with self.assertRaises(HttpError):
            transition_workload(tenant_id=self.tenant_id, actor_id=self.admin.id, action_id=item.id, expected_version=item.version, target=WorkloadAction.Status.APPROVED)
        item.refresh_from_db()
        item = transition_workload(tenant_id=self.tenant_id, actor_id=self.controller.id, action_id=item.id, expected_version=item.version, target=WorkloadAction.Status.APPROVED)
        item = transition_workload(tenant_id=self.tenant_id, actor_id=self.controller.id, action_id=item.id, expected_version=item.version, target=WorkloadAction.Status.EXECUTED)
        self.assertEqual(item.status, WorkloadAction.Status.EXECUTED)
        self.assertTrue(OutboxEvent.objects.filter(topic="workload.action.executed", aggregate_id=str(item.id)).exists())

    @patch("apps.evaluators.services.extract_embedding", return_value=[1.0, 0.0, 0.0])
    def test_secure_evaluation_session_pauses_and_creates_human_review(self, _extract_embedding):
        evaluator = Evaluator.objects.get(email="evaluator1043@admiezo.local")
        assignment = Assignment.objects.filter(tenant_id=self.tenant_id, evaluator=evaluator).exclude(status=Assignment.Status.SUBMITTED).first()
        client = Client()
        login = client.post(
            "/api/v1/auth/login",
            data=json.dumps({"email": evaluator.email, "password": "ChangeMe123!", "device_id": "secure-evaluation-test"}),
            content_type="application/json",
        )
        self.assertEqual(login.status_code, 200)
        self.verify_identity_for_secure_session(evaluator, assignment)
        started = client.post(
            "/api/v1/phase4/remote-security/sessions",
            data=json.dumps({
                "assignment_id": str(assignment.id),
                "session_fingerprint": "a" * 64,
                "device_fingerprint": "b" * 64,
                "consent": True,
                "preflight": {"camera_ready": True, "fullscreen_active": True, "screen_count": 1},
                "device_inventory": {"video_inputs": 1, "audio_inputs": 1, "digest": "c" * 64},
            }),
            content_type="application/json",
        )
        self.assertEqual(started.status_code, 200)
        session_id = started.json()["id"]
        event = client.post(
            "/api/v1/phase4/remote-security/events",
            data=json.dumps({"assignment_id": str(assignment.id), "secure_session_id": session_id, "category": "viewer_hidden", "severity": "critical", "device_fingerprint": "b" * 64, "session_fingerprint": "a" * 64, "details": {"visibility": "hidden"}}),
            content_type="application/json",
        )
        self.assertEqual(event.status_code, 200)
        self.assertEqual(event.json()["action"], "pause")
        session = SecureEvaluationSession.objects.get(id=session_id)
        self.assertEqual(session.status, SecureEvaluationSession.Status.PAUSED)
        self.assertEqual(session.violation_count, 1)
        self.assertTrue(ProctoringReview.objects.filter(secure_session=session, event_id=event.json()["id"]).exists())
        resumed = client.post(
            f"/api/v1/phase4/remote-security/sessions/{session_id}/resume",
            data=json.dumps({"posture": {"camera_active": True, "fullscreen_active": True, "screen_count": 1, "device_changed": False}}),
            content_type="application/json",
        )
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(resumed.json()["status"], "active")

    @patch("apps.evaluators.services.extract_embedding", return_value=[1.0, 0.0, 0.0])
    def test_submit_recovers_previous_permission_failure_and_locks_result(self, _extract_embedding):
        evaluator = Evaluator.objects.get(email="evaluator1043@admiezo.local")
        assignment = Assignment.objects.filter(tenant_id=self.tenant_id, evaluator=evaluator).exclude(status=Assignment.Status.SUBMITTED).first()
        client = Client()
        self.assertEqual(client.post("/api/v1/auth/login", data=json.dumps({"email": evaluator.email, "password": "ChangeMe123!", "device_id": "submit-recovery-test"}), content_type="application/json").status_code, 200)
        self.verify_identity_for_secure_session(evaluator, assignment)
        started = client.post(
            "/api/v1/phase4/remote-security/sessions",
            data=json.dumps({"assignment_id": str(assignment.id), "session_fingerprint": "d" * 64, "device_fingerprint": "e" * 64, "consent": True, "preflight": {"camera_ready": True, "fullscreen_active": True, "screen_count": 1}, "device_inventory": {"video_inputs": 1, "digest": "f" * 64}}),
            content_type="application/json",
        )
        self.assertEqual(started.status_code, 200)
        session_id = started.json()["id"]
        opened_assignment = client.post(
            f"/api/v1/allocation/assignments/{assignment.id}/action",
            data=json.dumps({"version": assignment.version, "action": "start"}),
            content_type="application/json",
            HTTP_X_SECURE_EVALUATION_SESSION=session_id,
        )
        self.assertEqual(opened_assignment.status_code, 200)
        opened = client.post(f"/api/v1/marking/assignments/{assignment.id}/open", HTTP_X_SECURE_EVALUATION_SESSION=session_id)
        self.assertEqual(opened.status_code, 200)
        evaluation = Evaluation.objects.get(assignment=assignment)
        workflow = EvaluationWorkflow.objects.get(assignment=assignment)
        evaluation.status = Evaluation.Status.SUBMITTED
        evaluation.submitted_at = timezone.now()
        evaluation.checksum = "1" * 64
        evaluation.save()
        workflow.state = EvaluationWorkflow.State.SUBMITTED
        workflow.submitted_at = timezone.now()
        workflow.save()
        assignment.refresh_from_db()
        assignment.status = Assignment.Status.SUBMITTED
        assignment.submitted_at = timezone.now()
        assignment.save()
        response = client.post(
            f"/api/v1/workflow/evaluations/{evaluation.id}/submit",
            data=json.dumps({"evaluation_version": evaluation.version, "workflow_version": workflow.version}),
            content_type="application/json",
            HTTP_X_SECURE_EVALUATION_SESSION=session_id,
            HTTP_IDEMPOTENCY_KEY="recover-finalization",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["valuation_result_id"])
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.status, Evaluation.Status.LOCKED)
        self.assertTrue(ValuationResult.objects.filter(evaluation=evaluation, is_locked=True).exists())

    def test_centre_requires_readiness_before_activation(self):
        centre = CentreProfile.objects.create(tenant_id=self.tenant_id, code="TEST-CENTRE", name="Test Centre", location="Block B", capacity=20, workstation_count=20)
        centre = transition_centre(tenant_id=self.tenant_id, actor_id=self.admin.id, centre_id=centre.id, expected_version=centre.version, target=CentreProfile.Status.REVIEW)
        with self.assertRaises(HttpError):
            transition_centre(tenant_id=self.tenant_id, actor_id=self.admin.id, centre_id=centre.id, expected_version=centre.version, target=CentreProfile.Status.READY)
        CentreReadiness.objects.create(tenant_id=self.tenant_id, centre=centre, scanner_ready=True, workstation_ready=True, network_ready=True, power_ready=True, secure_lan_ready=True, operators_ready=True, decision="go", checked_by_id=self.admin.id)
        centre = transition_centre(tenant_id=self.tenant_id, actor_id=self.admin.id, centre_id=centre.id, expected_version=centre.version, target=CentreProfile.Status.READY)
        centre = transition_centre(tenant_id=self.tenant_id, actor_id=self.admin.id, centre_id=centre.id, expected_version=centre.version, target=CentreProfile.Status.ACTIVE)
        self.assertEqual(centre.status, CentreProfile.Status.ACTIVE)

    def test_notifications_support_delivery_failure_retry_and_acknowledgement(self):
        item = create_notification(tenant_id=self.tenant_id, actor_id=self.admin.id, user_id=self.controller.id, category="deadline", title="Submission required", body="Complete the pending assigned scripts.", severity="high", channels=["in_app", "email"], mandatory_acknowledgement=True)
        item = notification_action(tenant_id=self.tenant_id, actor_id=self.admin.id, notification_id=item.id, expected_version=item.version, action="fail", error="Mail provider timeout")
        self.assertEqual(item.status, NotificationDelivery.Status.FAILED)
        item = notification_action(tenant_id=self.tenant_id, actor_id=self.admin.id, notification_id=item.id, expected_version=item.version, action="retry")
        item = notification_action(tenant_id=self.tenant_id, actor_id=self.admin.id, notification_id=item.id, expected_version=item.version, action="deliver")
        item = notification_action(tenant_id=self.tenant_id, actor_id=self.controller.id, notification_id=item.id, expected_version=item.version, action="acknowledge")
        self.assertEqual(item.status, NotificationDelivery.Status.ACKNOWLEDGED)

    def test_result_release_and_integration_handover_are_controlled(self):
        script = Script.objects.filter(tenant_id=self.tenant_id).exclude(final_mark__isnull=False).first()
        script.state = Script.State.FINALIZED
        script.save(update_fields=["state", "updated_at"])
        final = FinalMark.objects.create(tenant_id=self.tenant_id, script=script, rule=FinalMark.Rule.APPROVED, mark=Decimal("72"), calculation={"source": "test"}, status=FinalMark.Status.LOCKED, proposed_by_id=self.admin.id, approved_by_id=self.controller.id, locked_by_id=self.admin.id, locked_at=timezone.now(), checksum="a" * 64)
        completion = CompletionRecord.objects.create(tenant_id=self.tenant_id, script=script, final_mark=final, checks={"ready": True}, signature_digest="b" * 64, signed_by_id=self.admin.id, status=CompletionRecord.Status.SIGNED)
        authorization = request_authorization(tenant_id=self.tenant_id, actor_id=self.admin.id, kind=ControlledAuthorization.Kind.RESULT_RELEASE, final_mark=final, purpose="Release approved result to the university ERP.", proposed_change={}, expires_at=timezone.now() + timedelta(minutes=10))
        authorization = decide_authorization(tenant_id=self.tenant_id, actor_id=self.controller.id, authorization_id=authorization.id, expected_version=authorization.version, approve=True)
        authorization = consume_authorization(tenant_id=self.tenant_id, actor_id=self.admin.id, authorization_id=authorization.id, expected_version=authorization.version)
        completion.refresh_from_db()
        self.assertEqual(completion.status, CompletionRecord.Status.RELEASED)
        endpoint = IntegrationEndpoint.objects.get(tenant_id=self.tenant_id, name="University ERP Result Gateway")
        handover, replayed = queue_handover(tenant_id=self.tenant_id, actor_id=self.admin.id, endpoint=endpoint, final_mark=final, idempotency_key="release-test-1")
        duplicate, replayed = queue_handover(tenant_id=self.tenant_id, actor_id=self.admin.id, endpoint=endpoint, final_mark=final, idempotency_key="release-test-1")
        self.assertTrue(replayed)
        self.assertEqual(duplicate.id, handover.id)
        handover = transition_handover(tenant_id=self.tenant_id, actor_id=self.admin.id, handover_id=handover.id, expected_version=handover.version, target=ResultHandover.Status.SENT)
        handover = acknowledge_handover(tenant_id=self.tenant_id, actor_id=self.admin.id, handover_id=handover.id, expected_version=handover.version, status=ResultHandover.Status.ACKNOWLEDGED, reference="ERP-ACK-1", remote_snapshot={"mark": "72.00", "checksum": final.checksum})
        self.assertEqual(handover.differences, {})

    def test_remuneration_uses_completed_work_and_independent_approval(self):
        assignment = Assignment.objects.filter(tenant_id=self.tenant_id).select_related("evaluator").first()
        evaluator = assignment.evaluator
        assignment.status = Assignment.Status.SUBMITTED
        assignment.started_at = timezone.now() - timedelta(hours=1)
        assignment.submitted_at = timezone.now()
        assignment.save()
        AttendanceRecord.objects.create(tenant_id=self.tenant_id, evaluator=evaluator, session=self.session, checked_in_at=timezone.now() - timedelta(hours=2), checked_out_at=timezone.now())
        rule = RemunerationRule.objects.filter(tenant_id=self.tenant_id).first()
        statement = calculate_remuneration(tenant_id=self.tenant_id, actor_id=self.admin.id, evaluator=evaluator, session=self.session, rule=rule)
        self.assertGreater(statement.net_amount, 0)
        with self.assertRaises(HttpError):
            transition_statement(tenant_id=self.tenant_id, actor_id=self.admin.id, statement_id=statement.id, expected_version=statement.version, target="approved")
        statement.refresh_from_db()
        statement = transition_statement(tenant_id=self.tenant_id, actor_id=self.controller.id, statement_id=statement.id, expected_version=statement.version, target="approved")
        statement = transition_statement(tenant_id=self.tenant_id, actor_id=self.admin.id, statement_id=statement.id, expected_version=statement.version, target="paid", payment_reference="BANK-2026-01")
        statement = transition_statement(tenant_id=self.tenant_id, actor_id=self.controller.id, statement_id=statement.id, expected_version=statement.version, target="reconciled")
        self.assertEqual(statement.status, "reconciled")

    def test_student_access_evidence_i18n_and_recovery(self):
        script = Script.objects.filter(tenant_id=self.tenant_id).exclude(final_mark__isnull=False).first()
        script.state = Script.State.FINALIZED
        script.save(update_fields=["state", "updated_at"])
        FinalMark.objects.create(tenant_id=self.tenant_id, script=script, rule=FinalMark.Rule.APPROVED, mark=Decimal("68"), status=FinalMark.Status.LOCKED, proposed_by_id=self.admin.id, checksum="c" * 64)
        ScriptAsset.objects.get_or_create(
            tenant_id=self.tenant_id,
            script=script,
            kind=ScriptAsset.Kind.EVALUATION,
            page_number=1,
            version=1,
            defaults={"storage_key": f"tests/{script.id}/page-1.webp", "sha256": "d" * 64, "byte_size": 1200},
        )
        request = create_student_request(tenant_id=self.tenant_id, actor_id=self.admin.id, identity_reference="opaque-student-reference", script=script, purpose="copy")
        self.assertEqual(request.status, StudentScriptRequest.Status.REQUESTED)
        with self.assertRaises(HttpError):
            create_issue(tenant_id=self.tenant_id, actor_id=self.admin.id, issue_type="technical", title="x", description="blocked", paper=None, question_reference="")
        package = EvidencePackage.objects.create(tenant_id=self.tenant_id, script=script, purpose="audit", requested_by_id=self.admin.id)
        package = seal_evidence(tenant_id=self.tenant_id, actor_id=self.admin.id, package_id=package.id)
        self.assertEqual(len(package.digest), 64)
        preference = set_locale(tenant_id=self.tenant_id, actor_id=self.admin.id, locale="kn", additional_locales=[])
        self.assertEqual(preference.locale, "kn")
        plan = RecoveryPlan.objects.create(tenant_id=self.tenant_id, name="Test recovery", regions=["a", "b"], clean_environment="clean-room", status="approved")
        drill = RecoveryDrill.objects.create(tenant_id=self.tenant_id, plan=plan, drill_type="restore", requested_by_id=self.admin.id)
        drill = transition_drill(tenant_id=self.tenant_id, actor_id=self.admin.id, drill_id=drill.id, expected_version=drill.version, target="running")
        drill = transition_drill(tenant_id=self.tenant_id, actor_id=self.admin.id, drill_id=drill.id, expected_version=drill.version, target="verifying", measurements={"rto_minutes": 42})
        drill = transition_drill(tenant_id=self.tenant_id, actor_id=self.controller.id, drill_id=drill.id, expected_version=drill.version, target="passed", integrity_checks={"database": True, "objects": True}, report="Recovery objectives and integrity controls passed.")
        self.assertEqual(drill.status, "passed")

    def test_phase4_models_do_not_store_candidate_pii(self):
        forbidden = {"candidate_name", "register_number", "usn", "college", "candidate_email", "candidate_phone", "photo", "signature"}
        for model in (StudentScriptRequest, CompletionRecord, ResultHandover, EvidencePackage):
            self.assertFalse(forbidden.intersection(field.name for field in model._meta.fields))
