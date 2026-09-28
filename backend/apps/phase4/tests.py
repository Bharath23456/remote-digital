from datetime import timedelta
from decimal import Decimal
import hashlib
import importlib

from django.apps import apps as django_apps
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import connection
import json
from unittest.mock import patch

from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from ninja.errors import HttpError

from apps.allocation.models import AllocationPolicy, Assignment
from apps.configuration.models import ExamSession, Paper
from apps.core.models import AuditEvent, OutboxEvent
from apps.core.testing import create_operational_fixtures
from apps.custody.models import Script
from apps.eligibility.models import EligibilityRecord
from apps.evaluators.models import Evaluator, Expertise
from apps.evaluators.services import enroll_face_template, verify_evaluator_access
from apps.identity_auth.models import AccessSession
from apps.marking.models import Evaluation
from apps.repository.models import ScriptAsset
from apps.rubrics.models import MarkingScheme
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
    ModerationCase,
    ModerationPolicy,
    NotificationDelivery,
    PresenceSecurityEvent,
    ProctoringReview,
    RecoveryDrill,
    RecoveryPlan,
    RemoteSupportCommand,
    RemoteSupportSession,
    RemunerationRule,
    ResultHandover,
    SecureEvaluationSession,
    StudentScriptRequest,
    UniversityApiKey,
    WorkloadAction,
)
from .services import (
    acknowledge_handover,
    calculate_remuneration,
    consume_authorization,
    create_issue,
    create_notification,
    create_remote_support_command,
    create_revaluation,
    create_student_request,
    create_workload_action,
    decide_authorization,
    decide_remote_support,
    end_remote_support,
    expire_remote_support_sessions,
    issue_secure_preflight_token,
    notification_action,
    acknowledge_remote_support_command,
    queue_handover,
    request_authorization,
    request_remote_support,
    sample_moderation_cases,
    seal_evidence,
    set_locale,
    transition_centre,
    transition_drill,
    transition_moderation,
    transition_handover,
    transition_statement,
    transition_revaluation,
    transition_workload,
)


class ResultServiceMigrationRepairTests(TransactionTestCase):
    def test_recreates_removed_tables_and_is_idempotent(self):
        migration = importlib.import_module(
            "apps.phase4.migrations.0006_university_api_keys_official_requests"
        )
        models = [
            django_apps.get_model("phase4", model_name)
            for model_name in migration.REMOVED_RESULT_SERVICE_MODELS
        ]

        with connection.schema_editor() as schema_editor:
            for model in reversed(models):
                schema_editor.delete_model(model)
            migration.restore_removed_result_service_tables(django_apps, schema_editor)
            migration.restore_removed_result_service_tables(django_apps, schema_editor)

        table_names = set(connection.introspection.table_names())
        self.assertTrue(all(model._meta.db_table in table_names for model in models))


class RemainingModulesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap_demo", verbosity=0)
        create_operational_fixtures()
        cls.admin = User.objects.get(username="admin@admiezo.local")
        cls.controller = User.objects.get(username="controller@admiezo.local")
        cls.paper = Paper.objects.get(code="CS401-A")
        cls.tenant_id = cls.paper.tenant_id
        cls.session = ExamSession.objects.get(id=cls.paper.session_id)
        cls.evaluator = Evaluator.objects.filter(tenant_id=cls.tenant_id, status=Evaluator.Status.ACTIVE).first()
        cls.phase4_script = Script.objects.create(
            tenant_id=cls.tenant_id,
            script_code="AS-PHASE4-FLOW",
            primary_barcode="NB-PHASE4-FLOW",
            packet=Script.objects.filter(tenant_id=cls.tenant_id).first().packet,
            paper=cls.paper,
            page_count=1,
            state=Script.State.FINALIZED,
        )
        ScriptAsset.objects.create(
            tenant_id=cls.tenant_id,
            script=cls.phase4_script,
            kind=ScriptAsset.Kind.MASTER,
            page_number=1,
            storage_key="scripts-master/phase4-flow/page-1.webp",
            sha256="1" * 64,
            byte_size=100,
        )
        ScriptAsset.objects.create(
            tenant_id=cls.tenant_id,
            script=cls.phase4_script,
            kind=ScriptAsset.Kind.EVALUATION,
            page_number=1,
            storage_key="scripts-evaluation/phase4-flow/page-1.webp",
            sha256="2" * 64,
            byte_size=100,
        )
        previous_assignment = Assignment.objects.create(
            tenant_id=cls.tenant_id,
            script=cls.phase4_script,
            evaluator=cls.evaluator,
            valuation_round=1,
            status=Assignment.Status.SUBMITTED,
            due_at=timezone.now() + timedelta(days=3),
        )
        scheme = MarkingScheme.objects.filter(tenant_id=cls.tenant_id, paper=cls.paper).first()
        previous_evaluation = Evaluation.objects.create(
            tenant_id=cls.tenant_id,
            assignment=previous_assignment,
            scheme=scheme,
            status=Evaluation.Status.LOCKED,
            total_marks=Decimal("72"),
            checksum="3" * 64,
        )
        ValuationResult.objects.create(
            tenant_id=cls.tenant_id,
            evaluation=previous_evaluation,
            script=cls.phase4_script,
            valuation_round=1,
            total_marks=Decimal("72"),
            checksum="5" * 64,
            is_locked=True,
            locked_by_id=cls.controller.id,
            locked_at=timezone.now(),
        )
        FinalMark.objects.create(
            tenant_id=cls.tenant_id,
            script=cls.phase4_script,
            rule=FinalMark.Rule.APPROVED,
            mark=Decimal("72"),
            calculation={"source": "phase4_test_fixture"},
            status=FinalMark.Status.LOCKED,
            proposed_by_id=cls.admin.id,
            approved_by_id=cls.controller.id,
            locked_by_id=cls.admin.id,
            locked_at=timezone.now(),
            checksum="4" * 64,
        )

    def face_capture(self):
        return {
            "image_base64": "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
            "liveness_passed": True,
            "face_count": 1,
            "quality": {"score": 0.95, "lighting": 0.88},
            "model_version": "opencv-sface-v1",
            "device_fingerprint": "f" * 64,
        }

    def remote_support_assignment(self, evaluator, suffix):
        script = Script.objects.create(
            tenant_id=self.tenant_id,
            script_code=f"AS-SUPPORT-{suffix}",
            primary_barcode=f"NB-SUPPORT-{suffix}",
            packet=self.phase4_script.packet,
            paper=self.paper,
            page_count=2,
            state=Script.State.FINALIZED,
        )
        return Assignment.objects.create(
            tenant_id=self.tenant_id,
            script=script,
            evaluator=evaluator,
            valuation_round=1,
            status=Assignment.Status.ACCEPTED,
            due_at=timezone.now() + timedelta(days=1),
        )

    def verify_identity_for_secure_session(self, evaluator, assignment):
        enroll_face_template(tenant_id=self.tenant_id, actor_id=self.admin.id, evaluator_id=evaluator.id, capture=self.face_capture())
        access_session = AccessSession.objects.filter(user_id=evaluator.user_id, tenant_id=self.tenant_id, revoked_at__isnull=True).latest("created_at")
        verify_evaluator_access(tenant_id=self.tenant_id, actor_id=evaluator.user_id, evaluator=evaluator, assignment=assignment, access_session=access_session, capture=self.face_capture())

    def secure_preflight_token(self, evaluator, assignment, *, camera_ready=True, face_ready=True):
        access_session = AccessSession.objects.filter(
            user_id=evaluator.user_id,
            tenant_id=self.tenant_id,
            revoked_at__isnull=True,
        ).latest("created_at")
        return issue_secure_preflight_token(
            tenant_id=self.tenant_id,
            evaluator_id=evaluator.id,
            assignment_id=assignment.id,
            access_session_id=access_session.id,
            camera_ready=camera_ready,
            face_ready=face_ready,
        )

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
            "preflight_token": self.secure_preflight_token(evaluator, assignment),
            "preflight": {"camera_ready": True, "face_ready": True, "fullscreen_active": True, "screen_count": 1},
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

    def test_secure_evaluation_rejects_unavailable_camera(self):
        evaluator = Evaluator.objects.get(email="evaluator1043@admiezo.local")
        assignment = Assignment.objects.filter(tenant_id=self.tenant_id, evaluator=evaluator).exclude(status=Assignment.Status.SUBMITTED).first()
        client = Client()
        self.assertEqual(client.post("/api/v1/auth/login", data=json.dumps({"email": evaluator.email, "password": "ChangeMe123!", "device_id": "camera-gate-test"}), content_type="application/json").status_code, 200)
        base = {
            "assignment_id": str(assignment.id),
            "session_fingerprint": "a" * 64,
            "device_fingerprint": "b" * 64,
            "consent": True,
            "preflight_token": self.secure_preflight_token(evaluator, assignment),
            "preflight": {"camera_ready": True, "fullscreen_active": True, "screen_count": 1},
            "device_inventory": {"video_inputs": 1, "digest": "c" * 64},
        }
        with override_settings(DEMO_SKIP_EVALUATOR_FACE_VERIFICATION=True):
            unavailable = {
                **base,
                "preflight_token": self.secure_preflight_token(evaluator, assignment, camera_ready=False),
                "preflight": {**base["preflight"], "camera_ready": False},
            }
            self.assertEqual(client.post("/api/v1/phase4/remote-security/sessions", data=json.dumps(unavailable), content_type="application/json").status_code, 409)
            no_camera = {**base, "device_inventory": {"video_inputs": 0, "digest": "c" * 64}}
            self.assertEqual(client.post("/api/v1/phase4/remote-security/sessions", data=json.dumps(no_camera), content_type="application/json").status_code, 409)

    def test_workload_actions_require_independent_approval(self):
        item = create_workload_action(tenant_id=self.tenant_id, actor_id=self.admin.id, evaluator=self.evaluator, action="rebalance", reason="Deadline capacity requires redistribution.", metrics={"remaining": 28})
        with self.assertRaises(HttpError):
            transition_workload(tenant_id=self.tenant_id, actor_id=self.admin.id, action_id=item.id, expected_version=item.version, target=WorkloadAction.Status.APPROVED)
        item.refresh_from_db()
        item = transition_workload(tenant_id=self.tenant_id, actor_id=self.controller.id, action_id=item.id, expected_version=item.version, target=WorkloadAction.Status.APPROVED)
        item = transition_workload(tenant_id=self.tenant_id, actor_id=self.controller.id, action_id=item.id, expected_version=item.version, target=WorkloadAction.Status.EXECUTED)
        self.assertEqual(item.status, WorkloadAction.Status.EXECUTED)
        self.assertTrue(OutboxEvent.objects.filter(topic="workload.action.executed", aggregate_id=str(item.id)).exists())

    @patch("apps.evaluators.services.analyze_face_posture", return_value={"face_count": 1, "face_aligned": True, "phone_detected": False, "details": {}})
    @patch("apps.evaluators.services.extract_embedding", return_value=[1.0, 0.0, 0.0])
    def test_secure_evaluation_session_pauses_and_creates_human_review(self, _extract_embedding, _enrollment_posture):
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
                "preflight_token": self.secure_preflight_token(evaluator, assignment),
                "preflight": {"camera_ready": True, "face_ready": True, "fullscreen_active": True, "screen_count": 1},
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
        missing_preflight = client.post(
            f"/api/v1/phase4/remote-security/sessions/{session_id}/resume",
            data=json.dumps({"posture": {"camera_active": True, "fullscreen_active": True, "screen_count": 1, "device_changed": False}}),
            content_type="application/json",
        )
        self.assertEqual(missing_preflight.status_code, 409)
        resumed = client.post(
            f"/api/v1/phase4/remote-security/sessions/{session_id}/resume",
            data=json.dumps({"posture": {"camera_active": True, "fullscreen_active": True, "screen_count": 1, "device_changed": False}, "preflight_token": self.secure_preflight_token(evaluator, assignment)}),
            content_type="application/json",
        )
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(resumed.json()["status"], "active")

    @patch("apps.phase4.api.analyze_face_posture", return_value={"face_count": 0, "face_aligned": False, "phone_detected": False, "details": {}})
    def test_closed_camera_shutter_blocks_paper_preflight(self, _analyze_face_posture):
        evaluator = Evaluator.objects.get(email="evaluator1043@admiezo.local")
        assignment = Assignment.objects.filter(tenant_id=self.tenant_id, evaluator=evaluator).exclude(status=Assignment.Status.SUBMITTED).first()
        client = Client()
        self.assertEqual(client.post("/api/v1/auth/login", data=json.dumps({"email": evaluator.email, "password": "ChangeMe123!", "device_id": "closed-shutter-test"}), content_type="application/json").status_code, 200)
        preflight = client.post(
            "/api/v1/phase4/remote-security/camera-preflight",
            data=json.dumps({"assignment_id": str(assignment.id), "image_base64": "data:image/jpeg;base64,frame"}),
            content_type="application/json",
        )
        self.assertEqual(preflight.status_code, 409)
        self.assertIn("shutter", preflight.json()["detail"].lower())
        with override_settings(DEMO_SKIP_EVALUATOR_FACE_VERIFICATION=True):
            blocked = client.post(
                "/api/v1/phase4/remote-security/sessions",
                data=json.dumps({"assignment_id": str(assignment.id), "session_fingerprint": "4" * 64, "device_fingerprint": "5" * 64, "consent": True, "preflight": {"camera_ready": True, "face_ready": False, "fullscreen_active": True, "screen_count": 1}, "device_inventory": {"video_inputs": 1, "digest": "6" * 64}}),
                content_type="application/json",
            )
        self.assertEqual(blocked.status_code, 409)
        self.assertFalse(SecureEvaluationSession.objects.filter(assignment=assignment).exists())

    def test_headphones_block_secure_session_start(self):
        evaluator = Evaluator.objects.get(email="evaluator1043@admiezo.local")
        assignment = Assignment.objects.filter(tenant_id=self.tenant_id, evaluator=evaluator).exclude(status=Assignment.Status.SUBMITTED).first()
        client = Client()
        self.assertEqual(client.post("/api/v1/auth/login", data=json.dumps({"email": evaluator.email, "password": "ChangeMe123!", "device_id": "headphones-preflight"}), content_type="application/json").status_code, 200)
        with override_settings(DEMO_SKIP_EVALUATOR_FACE_VERIFICATION=True):
            blocked = client.post(
                "/api/v1/phase4/remote-security/sessions",
                data=json.dumps({"assignment_id": str(assignment.id), "session_fingerprint": "a" * 64, "device_fingerprint": "b" * 64, "consent": True, "preflight_token": self.secure_preflight_token(evaluator, assignment), "preflight": {"camera_ready": True, "face_ready": True, "fullscreen_active": True, "screen_count": 1}, "device_inventory": {"video_inputs": 1, "headphones_detected": True, "headphone_devices": 1, "digest": "c" * 64}}),
                content_type="application/json",
            )
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("headphones", blocked.json()["detail"].lower())

    def test_missing_server_face_presence_pauses_active_session(self):
        evaluator = Evaluator.objects.get(email="evaluator1043@admiezo.local")
        assignment = Assignment.objects.filter(tenant_id=self.tenant_id, evaluator=evaluator).exclude(status=Assignment.Status.SUBMITTED).first()
        client = Client()
        self.assertEqual(client.post(
            "/api/v1/auth/login",
            data=json.dumps({"email": evaluator.email, "password": "ChangeMe123!", "device_id": "presence-timeout"}),
            content_type="application/json",
        ).status_code, 200)
        with override_settings(DEMO_SKIP_EVALUATOR_FACE_VERIFICATION=True):
            started = client.post(
                "/api/v1/phase4/remote-security/sessions",
                data=json.dumps({"assignment_id": str(assignment.id), "session_fingerprint": "1" * 64, "device_fingerprint": "2" * 64, "consent": True, "preflight_token": self.secure_preflight_token(evaluator, assignment), "preflight": {"camera_ready": True, "face_ready": True, "fullscreen_active": True, "screen_count": 1}, "device_inventory": {"video_inputs": 1, "digest": "3" * 64}}),
                content_type="application/json",
            )
        self.assertEqual(started.status_code, 200)
        secure_session = SecureEvaluationSession.objects.get(id=started.json()["id"])
        inventory = dict(secure_session.device_inventory)
        inventory["face_monitor"] = {"last_presence_at": (timezone.now() - timedelta(minutes=1)).isoformat()}
        secure_session.device_inventory = inventory
        secure_session.save(update_fields=["device_inventory", "updated_at"])
        heartbeat = client.post(
            f"/api/v1/phase4/remote-security/sessions/{secure_session.id}/heartbeat",
            data=json.dumps({"posture": {"camera_active": True, "fullscreen_active": True, "screen_count": 1, "device_changed": False}}),
            content_type="application/json",
        )
        self.assertEqual(heartbeat.status_code, 200)
        self.assertEqual(heartbeat.json()["action"], "pause")
        secure_session.refresh_from_db()
        self.assertEqual(secure_session.status, SecureEvaluationSession.Status.PAUSED)
        self.assertEqual(secure_session.pause_reason, "camera_stopped")

    @patch("apps.phase4.services.analyze_face_posture", return_value={"face_count": 2, "face_aligned": False, "phone_detected": False, "details": {}})
    @patch("apps.evaluators.services.analyze_face_posture", return_value={"face_count": 1, "face_aligned": True, "phone_detected": False, "details": {}})
    @patch("apps.evaluators.services.extract_embedding", return_value=[1.0, 0.0, 0.0])
    def test_three_multi_face_frames_terminate_session_and_revoke_login(self, _extract_embedding, _enrollment_posture, _presence_posture):
        evaluator = Evaluator.objects.get(email="evaluator1043@admiezo.local")
        assignment = Assignment.objects.filter(tenant_id=self.tenant_id, evaluator=evaluator).exclude(status=Assignment.Status.SUBMITTED).first()
        client = Client()
        self.assertEqual(client.post("/api/v1/auth/login", data=json.dumps({"email": evaluator.email, "password": "ChangeMe123!", "device_id": "multi-face-test"}), content_type="application/json").status_code, 200)
        self.verify_identity_for_secure_session(evaluator, assignment)
        started = client.post(
            "/api/v1/phase4/remote-security/sessions",
            data=json.dumps({"assignment_id": str(assignment.id), "session_fingerprint": "7" * 64, "device_fingerprint": "8" * 64, "consent": True, "preflight_token": self.secure_preflight_token(evaluator, assignment), "preflight": {"camera_ready": True, "face_ready": True, "fullscreen_active": True, "screen_count": 1}, "device_inventory": {"video_inputs": 1, "digest": "9" * 64}}),
            content_type="application/json",
        )
        self.assertEqual(started.status_code, 200)
        session_id = started.json()["id"]
        access_session = AccessSession.objects.filter(user=evaluator.user, tenant_id=self.tenant_id, revoked_at__isnull=True).latest("created_at")

        responses = [
            client.post(
                f"/api/v1/phase4/remote-security/sessions/{session_id}/face-presence",
                data=json.dumps({"image_base64": "data:image/jpeg;base64,frame"}),
                content_type="application/json",
            )
            for _ in range(3)
        ]
        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertFalse(responses[1].json()["terminated"])
        self.assertTrue(responses[2].json()["terminated"])
        session = SecureEvaluationSession.objects.get(id=session_id)
        self.assertEqual(session.status, SecureEvaluationSession.Status.ABANDONED)
        self.assertEqual(session.pause_reason, "multiple_faces")
        access_session.refresh_from_db()
        self.assertEqual(access_session.revoked_reason, "multiple_faces_detected")
        self.assertIsNotNone(access_session.revoked_at)
        self.assertTrue(PresenceSecurityEvent.objects.filter(details__secure_session_id=str(session_id), category="multiple_faces", severity="critical").exists())

    @patch("apps.phase4.services.analyze_face_posture", return_value={"face_count": 1, "face_aligned": True, "phone_detected": True, "phone_call_suspected": True, "details": {"phone_confidence": 0.86}})
    @patch("apps.evaluators.services.analyze_face_posture", return_value={"face_count": 1, "face_aligned": True, "phone_detected": False, "details": {}})
    @patch("apps.evaluators.services.extract_embedding", return_value=[1.0, 0.0, 0.0])
    def test_two_phone_frames_terminate_session(self, _extract_embedding, _enrollment_posture, _presence_posture):
        evaluator = Evaluator.objects.get(email="evaluator1043@admiezo.local")
        assignment = Assignment.objects.filter(tenant_id=self.tenant_id, evaluator=evaluator).exclude(status=Assignment.Status.SUBMITTED).first()
        client = Client()
        self.assertEqual(client.post("/api/v1/auth/login", data=json.dumps({"email": evaluator.email, "password": "ChangeMe123!", "device_id": "phone-test"}), content_type="application/json").status_code, 200)
        self.verify_identity_for_secure_session(evaluator, assignment)
        started = client.post(
            "/api/v1/phase4/remote-security/sessions",
            data=json.dumps({"assignment_id": str(assignment.id), "session_fingerprint": "1" * 64, "device_fingerprint": "2" * 64, "consent": True, "preflight_token": self.secure_preflight_token(evaluator, assignment), "preflight": {"camera_ready": True, "face_ready": True, "fullscreen_active": True, "screen_count": 1}, "device_inventory": {"video_inputs": 1, "digest": "3" * 64}}),
            content_type="application/json",
        )
        self.assertEqual(started.status_code, 200)
        session_id = started.json()["id"]
        responses = [
            client.post(
                f"/api/v1/phase4/remote-security/sessions/{session_id}/face-presence",
                data=json.dumps({"image_base64": "data:image/jpeg;base64,frame"}),
                content_type="application/json",
            )
            for _ in range(2)
        ]
        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertIsNone(responses[0].json()["event_id"])
        self.assertIsNotNone(responses[1].json()["event_id"])
        self.assertTrue(responses[1].json()["terminated"])
        self.assertEqual(responses[1].json()["termination_reason"], "phone_detected")
        session = SecureEvaluationSession.objects.get(id=session_id)
        self.assertEqual(session.status, SecureEvaluationSession.Status.ABANDONED)
        self.assertEqual(session.pause_reason, "phone_detected")
        access_session = AccessSession.objects.filter(user=evaluator.user, tenant_id=self.tenant_id).latest("created_at")
        self.assertEqual(access_session.revoked_reason, "phone_detected")
        self.assertIsNotNone(access_session.revoked_at)
        self.assertTrue(PresenceSecurityEvent.objects.filter(details__secure_session_id=str(session_id), category="phone_detected", severity="critical").exists())

    @patch("apps.evaluators.services.analyze_face_posture", return_value={"face_count": 1, "face_aligned": True, "phone_detected": False, "details": {}})
    @patch("apps.evaluators.services.extract_embedding", return_value=[1.0, 0.0, 0.0])
    def test_headphones_connected_during_session_terminate_login(self, _extract_embedding, _enrollment_posture):
        evaluator = Evaluator.objects.get(email="evaluator1043@admiezo.local")
        assignment = Assignment.objects.filter(tenant_id=self.tenant_id, evaluator=evaluator).exclude(status=Assignment.Status.SUBMITTED).first()
        client = Client()
        self.assertEqual(client.post("/api/v1/auth/login", data=json.dumps({"email": evaluator.email, "password": "ChangeMe123!", "device_id": "headphones-live"}), content_type="application/json").status_code, 200)
        self.verify_identity_for_secure_session(evaluator, assignment)
        started = client.post(
            "/api/v1/phase4/remote-security/sessions",
            data=json.dumps({"assignment_id": str(assignment.id), "session_fingerprint": "4" * 64, "device_fingerprint": "5" * 64, "consent": True, "preflight_token": self.secure_preflight_token(evaluator, assignment), "preflight": {"camera_ready": True, "face_ready": True, "fullscreen_active": True, "screen_count": 1}, "device_inventory": {"video_inputs": 1, "headphones_detected": False, "digest": "6" * 64}}),
            content_type="application/json",
        )
        self.assertEqual(started.status_code, 200)
        session_id = started.json()["id"]
        event = client.post(
            "/api/v1/phase4/remote-security/events",
            data=json.dumps({"assignment_id": str(assignment.id), "secure_session_id": session_id, "category": "headphones_detected", "severity": "critical", "device_fingerprint": "5" * 64, "session_fingerprint": "4" * 64, "details": {"headphone_devices": 1}}),
            content_type="application/json",
        )
        self.assertEqual(event.status_code, 200)
        self.assertEqual(event.json()["session"]["status"], "abandoned")
        access_session = AccessSession.objects.filter(user=evaluator.user, tenant_id=self.tenant_id).latest("created_at")
        self.assertEqual(access_session.revoked_reason, "headphones_detected")
        self.assertIsNotNone(access_session.revoked_at)

    @patch("apps.evaluators.services.analyze_face_posture", return_value={"face_count": 1, "face_aligned": True, "phone_detected": False, "details": {}})
    @patch("apps.evaluators.services.extract_embedding", return_value=[1.0, 0.0, 0.0])
    def test_two_confirmed_identity_mismatches_terminate_login(self, _extract_embedding, _enrollment_posture):
        evaluator = Evaluator.objects.get(email="evaluator1043@admiezo.local")
        assignment = Assignment.objects.filter(tenant_id=self.tenant_id, evaluator=evaluator).exclude(status=Assignment.Status.SUBMITTED).first()
        client = Client()
        self.assertEqual(client.post("/api/v1/auth/login", data=json.dumps({"email": evaluator.email, "password": "ChangeMe123!", "device_id": "identity-mismatch-live"}), content_type="application/json").status_code, 200)
        self.verify_identity_for_secure_session(evaluator, assignment)
        started = client.post(
            "/api/v1/phase4/remote-security/sessions",
            data=json.dumps({"assignment_id": str(assignment.id), "session_fingerprint": "a" * 64, "device_fingerprint": "b" * 64, "consent": True, "preflight_token": self.secure_preflight_token(evaluator, assignment), "preflight": {"camera_ready": True, "face_ready": True, "fullscreen_active": True, "screen_count": 1}, "device_inventory": {"video_inputs": 1, "headphones_detected": False, "digest": "c" * 64}}),
            content_type="application/json",
        )
        self.assertEqual(started.status_code, 200)
        session_id = started.json()["id"]
        event = client.post(
            "/api/v1/phase4/remote-security/events",
            data=json.dumps({"assignment_id": str(assignment.id), "secure_session_id": session_id, "category": "identity_mismatch", "severity": "critical", "device_fingerprint": "b" * 64, "session_fingerprint": "a" * 64, "details": {"consecutive_checks": 2}}),
            content_type="application/json",
        )
        self.assertEqual(event.status_code, 200)
        self.assertEqual(event.json()["session"]["status"], "abandoned")
        self.assertEqual(event.json()["session"]["pause_reason"], "identity_mismatch")
        access_session = AccessSession.objects.filter(user=evaluator.user, tenant_id=self.tenant_id).latest("created_at")
        self.assertEqual(access_session.revoked_reason, "identity_mismatch")
        self.assertIsNotNone(access_session.revoked_at)

    @patch("apps.evaluators.services.analyze_face_posture", return_value={"face_count": 1, "face_aligned": True, "phone_detected": False, "details": {}})
    @patch("apps.evaluators.services.extract_embedding", return_value=[1.0, 0.0, 0.0])
    def test_external_media_device_change_terminates_login(self, _extract_embedding, _enrollment_posture):
        evaluator = Evaluator.objects.get(email="evaluator1043@admiezo.local")
        assignment = Assignment.objects.filter(tenant_id=self.tenant_id, evaluator=evaluator).exclude(status=Assignment.Status.SUBMITTED).first()
        client = Client()
        self.assertEqual(client.post("/api/v1/auth/login", data=json.dumps({"email": evaluator.email, "password": "ChangeMe123!", "device_id": "external-device-live"}), content_type="application/json").status_code, 200)
        self.verify_identity_for_secure_session(evaluator, assignment)
        started = client.post(
            "/api/v1/phase4/remote-security/sessions",
            data=json.dumps({"assignment_id": str(assignment.id), "session_fingerprint": "d" * 64, "device_fingerprint": "e" * 64, "consent": True, "preflight_token": self.secure_preflight_token(evaluator, assignment), "preflight": {"camera_ready": True, "face_ready": True, "fullscreen_active": True, "screen_count": 1}, "device_inventory": {"video_inputs": 1, "headphones_detected": False, "digest": "f" * 64}}),
            content_type="application/json",
        )
        self.assertEqual(started.status_code, 200)
        event = client.post(
            "/api/v1/phase4/remote-security/events",
            data=json.dumps({"assignment_id": str(assignment.id), "secure_session_id": started.json()["id"], "category": "external_media_device", "severity": "critical", "device_fingerprint": "e" * 64, "session_fingerprint": "d" * 64, "details": {"connected_or_changed": True}}),
            content_type="application/json",
        )
        self.assertEqual(event.status_code, 200)
        self.assertEqual(event.json()["session"]["status"], "abandoned")
        access_session = AccessSession.objects.filter(user=evaluator.user, tenant_id=self.tenant_id).latest("created_at")
        self.assertEqual(access_session.revoked_reason, "external_media_device")
        self.assertIsNotNone(access_session.revoked_at)

    @patch("apps.evaluators.services.analyze_face_posture", return_value={"face_count": 1, "face_aligned": True, "phone_detected": False, "details": {}})
    @patch("apps.evaluators.services.extract_embedding", return_value=[1.0, 0.0, 0.0])
    def test_submit_recovers_previous_permission_failure_and_locks_result(self, _extract_embedding, _enrollment_posture):
        evaluator = Evaluator.objects.get(email="evaluator1043@admiezo.local")
        assignment = Assignment.objects.filter(tenant_id=self.tenant_id, evaluator=evaluator).exclude(status=Assignment.Status.SUBMITTED).first()
        client = Client()
        self.assertEqual(client.post("/api/v1/auth/login", data=json.dumps({"email": evaluator.email, "password": "ChangeMe123!", "device_id": "submit-recovery-test"}), content_type="application/json").status_code, 200)
        self.verify_identity_for_secure_session(evaluator, assignment)
        started = client.post(
            "/api/v1/phase4/remote-security/sessions",
            data=json.dumps({"assignment_id": str(assignment.id), "session_fingerprint": "d" * 64, "device_fingerprint": "e" * 64, "consent": True, "preflight_token": self.secure_preflight_token(evaluator, assignment), "preflight": {"camera_ready": True, "face_ready": True, "fullscreen_active": True, "screen_count": 1}, "device_inventory": {"video_inputs": 1, "digest": "f" * 64}}),
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

    def test_evaluator_sos_notifies_live_operations_with_the_query(self):
        evaluator = Evaluator.objects.get(email="evaluator1043@admiezo.local")
        assignment = self.remote_support_assignment(evaluator, "SOS")
        client = Client()
        self.assertEqual(client.post(
            "/api/v1/auth/login",
            data=json.dumps({"email": evaluator.email, "password": "ChangeMe123!", "device_id": "evaluator-sos"}),
            content_type="application/json",
        ).status_code, 200)

        response = client.post(
            "/api/v1/phase4/remote-support/help-requests",
            data=json.dumps({"assignment_id": str(assignment.id), "query": "The answer script image is too blurred to evaluate question three."}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "sent")
        self.assertGreater(response.json()["recipient_count"], 0)
        notification = NotificationDelivery.objects.filter(
            tenant_id=self.tenant_id,
            user_id=self.controller.id,
            category="evaluator_help",
        ).latest("created_at")
        self.assertEqual(notification.severity, "high")
        self.assertTrue(notification.mandatory_acknowledgement)
        self.assertIn("too blurred", notification.body)
        self.assertIn(assignment.script.script_code, notification.title)
        operations_client = Client()
        self.assertEqual(operations_client.post(
            "/api/v1/auth/login",
            data=json.dumps({"email": self.controller.email, "password": "ChangeMe123!", "device_id": "operations-sos"}),
            content_type="application/json",
        ).status_code, 200)
        inbox = operations_client.get("/api/v1/auth/notifications")
        self.assertEqual(inbox.status_code, 200)
        self.assertTrue(any(item["id"] == str(notification.id) and "too blurred" in item["body"] for item in inbox.json()["items"]))
        catalog = operations_client.get("/api/v1/phase4/catalog?section=operations")
        self.assertEqual(catalog.status_code, 200)
        self.assertTrue(any(item["id"] == str(notification.id) and "too blurred" in item["body"] for item in catalog.json()["notifications"]))
        self.assertTrue(AuditEvent.objects.filter(
            tenant_id=self.tenant_id,
            aggregate_type="Assignment",
            aggregate_id=assignment.id,
            action="evaluator_help.requested",
        ).exists())

    def test_remote_support_requires_consent_limits_commands_and_is_revocable(self):
        evaluator = Evaluator.objects.get(email="evaluator1043@admiezo.local")
        assignment = self.remote_support_assignment(evaluator, "CONSENT")

        support = request_remote_support(
            tenant_id=self.tenant_id,
            actor_id=self.admin.id,
            actor_name="University Admin",
            assignment_id=assignment.id,
            reason="The evaluator requested help recovering the script viewer.",
        )
        self.assertEqual(support.status, RemoteSupportSession.Status.REQUESTED)
        self.assertTrue(NotificationDelivery.objects.filter(
            id=support.notification_id,
            user_id=evaluator.user_id,
            category="remote_support",
            mandatory_acknowledgement=True,
        ).exists())
        with self.assertRaises(HttpError):
            create_remote_support_command(
                tenant_id=self.tenant_id,
                actor_id=self.admin.id,
                support_id=support.id,
                kind=RemoteSupportCommand.Kind.NEXT_PAGE,
            )

        support = decide_remote_support(
            tenant_id=self.tenant_id,
            actor_id=evaluator.user_id,
            evaluator=evaluator,
            support_id=support.id,
            expected_version=support.version,
            approve=True,
        )
        self.assertEqual(support.status, RemoteSupportSession.Status.ACTIVE)
        command = create_remote_support_command(
            tenant_id=self.tenant_id,
            actor_id=self.admin.id,
            support_id=support.id,
            kind=RemoteSupportCommand.Kind.NEXT_PAGE,
        )
        command = acknowledge_remote_support_command(
            tenant_id=self.tenant_id,
            actor_id=evaluator.user_id,
            evaluator=evaluator,
            command_id=command.id,
            applied=True,
            result="Moved to page 2.",
        )
        self.assertEqual(command.status, RemoteSupportCommand.Status.APPLIED)

        support = end_remote_support(
            tenant_id=self.tenant_id,
            actor_id=evaluator.user_id,
            support_id=support.id,
            evaluator=evaluator,
        )
        self.assertEqual(support.status, RemoteSupportSession.Status.REVOKED)
        with self.assertRaises(HttpError):
            create_remote_support_command(
                tenant_id=self.tenant_id,
                actor_id=self.admin.id,
                support_id=support.id,
                kind=RemoteSupportCommand.Kind.REFRESH_VIEWER,
            )
        self.assertTrue(AuditEvent.objects.filter(
            tenant_id=self.tenant_id,
            aggregate_type="RemoteSupportSession",
            action="remote_support.revoked",
        ).exists())

    def test_remote_support_request_expires_without_evaluator_approval(self):
        evaluator = Evaluator.objects.get(email="evaluator1043@admiezo.local")
        assignment = self.remote_support_assignment(evaluator, "EXPIRE")
        support = request_remote_support(
            tenant_id=self.tenant_id,
            actor_id=self.admin.id,
            actor_name="University Admin",
            assignment_id=assignment.id,
            reason="The evaluator requested temporary viewer navigation assistance.",
        )
        RemoteSupportSession.objects.filter(id=support.id).update(expires_at=timezone.now() - timedelta(seconds=1))
        expire_remote_support_sessions(tenant_id=self.tenant_id)
        support.refresh_from_db()
        self.assertEqual(support.status, RemoteSupportSession.Status.EXPIRED)

    def test_remote_support_api_is_available_only_through_evaluator_consent(self):
        evaluator = Evaluator.objects.get(email="evaluator1043@admiezo.local")
        assignment = self.remote_support_assignment(evaluator, "API")
        admin_client = Client()
        self.assertEqual(admin_client.post(
            "/api/v1/auth/login",
            data=json.dumps({"email": self.admin.email, "password": "ChangeMe123!", "device_id": "support-admin"}),
            content_type="application/json",
        ).status_code, 200)
        created = admin_client.post(
            "/api/v1/phase4/remote-support/requests",
            data=json.dumps({"assignment_id": str(assignment.id), "reason": "The evaluator asked for help restoring the script viewer."}),
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 200)
        support = created.json()
        self.assertEqual(support["status"], "requested")

        evaluator_client = Client()
        self.assertEqual(evaluator_client.post(
            "/api/v1/auth/login",
            data=json.dumps({"email": evaluator.email, "password": "ChangeMe123!", "device_id": "support-evaluator"}),
            content_type="application/json",
        ).status_code, 200)
        inbox = evaluator_client.get("/api/v1/phase4/remote-support/inbox")
        self.assertEqual(inbox.status_code, 200)
        self.assertEqual(inbox.json()["session"]["id"], support["id"])
        approved = evaluator_client.post(
            f"/api/v1/phase4/remote-support/{support['id']}/decision",
            data=json.dumps({"version": support["version"], "approve": True}),
            content_type="application/json",
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["status"], "active")
        command = admin_client.post(
            f"/api/v1/phase4/remote-support/{support['id']}/commands",
            data=json.dumps({"kind": "refresh_viewer"}),
            content_type="application/json",
        )
        self.assertEqual(command.status_code, 200)

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

    def test_revaluation_requires_masked_finalized_script_and_completes_independently(self):
        script = None
        previous_assignment = None
        for candidate in Script.objects.filter(tenant_id=self.tenant_id, state=Script.State.FINALIZED, final_mark__status=FinalMark.Status.LOCKED):
            assignment = Assignment.objects.filter(script=candidate).select_related("evaluator").first()
            masked_pages = ScriptAsset.objects.filter(tenant_id=self.tenant_id, script=candidate, kind=ScriptAsset.Kind.EVALUATION, deleted_at__isnull=True).values("page_number").distinct().count()
            if assignment and masked_pages == candidate.page_count:
                script = candidate
                previous_assignment = assignment
                break
        self.assertIsNotNone(script)
        self.assertIsNotNone(previous_assignment)
        script = Script.objects.get(id=script.id)
        previous_assignment = Assignment.objects.get(id=previous_assignment.id)
        evaluator = Evaluator.objects.filter(tenant_id=self.tenant_id, status=Evaluator.Status.ACTIVE).exclude(id=previous_assignment.evaluator_id).first()
        self.assertIsNotNone(evaluator)
        policy, _ = AllocationPolicy.objects.get_or_create(tenant_id=self.tenant_id, paper=script.paper)
        Expertise.objects.update_or_create(
            tenant_id=self.tenant_id,
            evaluator=evaluator,
            subject=script.paper.subject,
            defaults={"level": policy.minimum_expertise_level, "verified": True, "years_experience": evaluator.years_experience},
        )
        eligibility, _ = EligibilityRecord.objects.update_or_create(
            tenant_id=self.tenant_id,
            evaluator=evaluator,
            subject=script.paper.subject,
            defaults={
                "status": EligibilityRecord.Status.ELIGIBLE,
                "qualification_ok": True,
                "experience_ok": True,
                "institution_ok": True,
                "expertise_ok": True,
                "has_conflict": False,
                "is_debarred": False,
                "is_blacklisted": False,
                "expires_on": timezone.localdate() + timedelta(days=90),
            },
        )

        script.state = Script.State.MASKED
        script.save(update_fields=["state", "updated_at"])
        with self.assertRaises(HttpError):
            create_revaluation(tenant_id=self.tenant_id, actor_id=self.admin.id, script=script, identity_reference="opaque-reval", scope="full", question_ids=[], reason="Student requested revaluation", rule="best")
        script.state = Script.State.FINALIZED
        script.save(update_fields=["state", "updated_at"])

        missing_page = ScriptAsset.objects.filter(tenant_id=self.tenant_id, script=script, kind=ScriptAsset.Kind.EVALUATION, deleted_at__isnull=True).order_by("page_number").first()
        self.assertIsNotNone(missing_page)
        missing_page.deleted_at = timezone.now()
        missing_page.save(update_fields=["deleted_at", "updated_at"])
        with self.assertRaises(HttpError):
            create_revaluation(tenant_id=self.tenant_id, actor_id=self.admin.id, script=script, identity_reference="opaque-reval", scope="full", question_ids=[], reason="Student requested revaluation", rule="best")
        missing_page.deleted_at = None
        missing_page.save(update_fields=["deleted_at", "updated_at"])

        item = create_revaluation(tenant_id=self.tenant_id, actor_id=self.admin.id, script=script, identity_reference="opaque-reval", scope="full", question_ids=[], reason="Student requested revaluation", rule="best")
        self.assertEqual(item.status, item.Status.REQUESTED)
        item = transition_revaluation(tenant_id=self.tenant_id, actor_id=self.controller.id, request_id=item.id, expected_version=item.version, target=item.Status.APPROVED)
        with self.assertRaises(HttpError):
            transition_revaluation(tenant_id=self.tenant_id, actor_id=self.controller.id, request_id=item.id, expected_version=item.version, target=item.Status.ASSIGNED, evaluator=previous_assignment.evaluator)
        eligibility.status = EligibilityRecord.Status.INELIGIBLE
        eligibility.save(update_fields=["status", "updated_at"])
        with self.assertRaises(HttpError):
            transition_revaluation(tenant_id=self.tenant_id, actor_id=self.controller.id, request_id=item.id, expected_version=item.version, target=item.Status.ASSIGNED, evaluator=evaluator)
        eligibility.status = EligibilityRecord.Status.ELIGIBLE
        eligibility.save(update_fields=["status", "updated_at"])
        item = transition_revaluation(tenant_id=self.tenant_id, actor_id=self.controller.id, request_id=item.id, expected_version=item.version, target=item.Status.ASSIGNED, evaluator=evaluator)
        self.assertEqual(item.assignment.source, "revaluation")
        self.assertEqual(item.assignment.evaluator_id, evaluator.id)

        scheme = Evaluation.objects.get(assignment=previous_assignment).scheme
        evaluation = Evaluation.objects.create(tenant_id=self.tenant_id, assignment=item.assignment, scheme=scheme, status=Evaluation.Status.LOCKED, total_marks=Decimal("78"), checksum="e" * 64)
        result = ValuationResult.objects.create(tenant_id=self.tenant_id, evaluation=evaluation, script=script, valuation_round=item.assignment.valuation_round, total_marks=Decimal("78"), checksum="f" * 64, is_locked=True, locked_by_id=self.controller.id, locked_at=timezone.now())
        item = transition_revaluation(tenant_id=self.tenant_id, actor_id=self.controller.id, request_id=item.id, expected_version=item.version, target=item.Status.EVALUATED, new_result=result)
        self.assertEqual(item.mark_difference, Decimal("6.00"))
        item = transition_revaluation(tenant_id=self.tenant_id, actor_id=self.controller.id, request_id=item.id, expected_version=item.version, target=item.Status.DECIDED)
        self.assertEqual(item.final_mark, Decimal("78.00"))
        authoritative = FinalMark.objects.get(id=item.original_final_mark_id)
        original_checksum = authoritative.checksum
        original_version = authoritative.version
        completion = CompletionRecord.objects.create(
            tenant_id=self.tenant_id,
            script=script,
            final_mark=authoritative,
            checks={"ready": True},
            examiner_declaration="Original result declaration",
            declaration_by_id=self.admin.id,
            signature_digest="9" * 64,
            signed_by_id=self.admin.id,
            status=CompletionRecord.Status.SIGNED,
        )
        item = transition_revaluation(tenant_id=self.tenant_id, actor_id=self.controller.id, request_id=item.id, expected_version=item.version, target=item.Status.CLOSED)
        self.assertEqual(item.status, item.Status.CLOSED)
        authoritative.refresh_from_db()
        completion.refresh_from_db()
        self.assertEqual(authoritative.mark, Decimal("78.00"))
        self.assertEqual(authoritative.version, original_version + 1)
        self.assertNotEqual(authoritative.checksum, original_checksum)
        self.assertEqual(authoritative.calculation["revaluations"][-1]["request_id"], str(item.id))
        self.assertEqual(completion.status, CompletionRecord.Status.PENDING)
        self.assertEqual(completion.signature_digest, "")
        self.assertTrue(completion.checks["revaluation_resign_required"])
        self.assertTrue(AuditEvent.objects.filter(aggregate_id=str(item.id), action="revaluation.closed").exists())
        self.assertTrue(AuditEvent.objects.filter(aggregate_id=str(authoritative.id), action="valuation.final_mark.revalued").exists())

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
        for page in range(1, max(script.page_count, 1) + 1):
            ScriptAsset.objects.get_or_create(
                tenant_id=self.tenant_id,
                script=script,
                kind=ScriptAsset.Kind.EVALUATION,
                page_number=page,
                version=1,
                defaults={"storage_key": f"tests/{script.id}/page-{page}.webp", "sha256": f"{page:064x}", "byte_size": 1200},
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

    def test_official_portal_photocopy_http_flow_reaches_delivery(self):
        raw_key = "admz_test_phase4_photocopy_key"
        UniversityApiKey.objects.create(
            tenant_id=self.tenant_id,
            name="Phase 4 automated university portal",
            key_prefix=raw_key[:16],
            key_hash=hashlib.sha256(raw_key.encode("utf-8")).hexdigest(),
            source_system="university_portal",
            scopes=["photocopy:request", "revaluation:request", "recounting:request"],
            created_by_id=self.admin.id,
        )
        university_client = Client()
        intake = university_client.post(
            "/api/v1/phase4/official-portal/photocopy-requests",
            data=json.dumps({
                "external_application_id": "UNI-PHASE4-COPY-001",
                "identity_reference": "STUDENT-PHASE4-001",
                "script_id": str(self.phase4_script.id),
                "purpose": "copy",
                "release_mode": "masked",
            }),
            content_type="application/json",
            HTTP_X_ADMIEZO_API_KEY=raw_key,
        )
        self.assertEqual(intake.status_code, 200)
        request_id = intake.json()["id"]
        self.assertEqual(intake.json()["status"], StudentScriptRequest.Status.REQUESTED)

        admin_client = Client()
        login = admin_client.post(
            "/api/v1/auth/login",
            data=json.dumps({"email": self.admin.email, "password": "ChangeMe123!", "device_id": "phase4-http-test"}),
            content_type="application/json",
        )
        self.assertEqual(login.status_code, 200)
        approve = admin_client.post(
            f"/api/v1/phase4/student/requests/{request_id}/decision",
            data=json.dumps({"version": intake.json()["version"], "approve": True, "release_mode": "masked"}),
            content_type="application/json",
        )
        self.assertEqual(approve.status_code, 200)
        self.assertEqual(approve.json()["status"], StudentScriptRequest.Status.APPROVED)

        download = university_client.get(
            f"/api/v1/phase4/official-portal/photocopy-requests/{request_id}/download",
            HTTP_X_ADMIEZO_API_KEY=raw_key,
        )
        self.assertEqual(download.status_code, 200)
        self.assertFalse(download.json()["evaluator_marks_included"])
        self.assertEqual(download.json()["release_mode"], StudentScriptRequest.ReleaseMode.MASKED)
        self.assertEqual(len(download.json()["pages"]), 1)

        acknowledge = university_client.post(
            f"/api/v1/phase4/official-portal/photocopy-requests/{request_id}/acknowledge",
            data=json.dumps({"delivery_reference": "UNIVERSITY-HANDOVER-PHASE4-001"}),
            content_type="application/json",
            HTTP_X_ADMIEZO_API_KEY=raw_key,
        )
        self.assertEqual(acknowledge.status_code, 200)
        self.assertEqual(acknowledge.json()["status"], StudentScriptRequest.Status.DELIVERED)
        self.assertTrue(AuditEvent.objects.filter(aggregate_id=request_id, action="student.copy.delivered").exists())

    def test_photocopy_expiry_worker_closes_release_window(self):
        item = create_student_request(
            tenant_id=self.tenant_id,
            actor_id=self.admin.id,
            identity_reference="STUDENT-EXPIRY-001",
            script=self.phase4_script,
            purpose="copy",
        )
        item.status = StudentScriptRequest.Status.APPROVED
        item.expires_at = timezone.now() - timedelta(minutes=1)
        item.save(update_fields=["status", "expires_at"])

        call_command("expire_photocopy_requests")

        item.refresh_from_db()
        self.assertEqual(item.status, StudentScriptRequest.Status.EXPIRED)
        self.assertFalse(item.download_allowed)
        self.assertTrue(AuditEvent.objects.filter(aggregate_id=str(item.id), action="student.copy.expired").exists())

    def test_moderation_end_to_end_applies_approved_mark(self):
        moderator = Evaluator.objects.filter(tenant_id=self.tenant_id, status=Evaluator.Status.ACTIVE).exclude(id=self.evaluator.id).first()
        self.assertIsNotNone(moderator)
        policy, _ = ModerationPolicy.objects.update_or_create(tenant_id=self.tenant_id, paper=self.paper, defaults={"sample_percentage": Decimal("100"), "sampling_modes": ["percentage_random"], "mandatory": True})
        case = sample_moderation_cases(tenant_id=self.tenant_id, actor_id=self.admin.id, policy=policy)[0]
        case = transition_moderation(tenant_id=self.tenant_id, actor_id=self.admin.id, case_id=case.id, expected_version=case.version, target=ModerationCase.Status.ASSIGNED, moderator=moderator)
        case = transition_moderation(tenant_id=self.tenant_id, actor_id=self.admin.id, case_id=case.id, expected_version=case.version, target=ModerationCase.Status.REVIEW)
        case = transition_moderation(tenant_id=self.tenant_id, actor_id=self.admin.id, case_id=case.id, expected_version=case.version, target=ModerationCase.Status.DECIDED, adjusted_mark=Decimal("74"), reason="Question evidence supports the revised total mark.", snapshot={"source": "moderator_review"})
        case = transition_moderation(tenant_id=self.tenant_id, actor_id=self.controller.id, case_id=case.id, expected_version=case.version, target=ModerationCase.Status.APPROVED)
        final_mark = FinalMark.objects.get(script=self.phase4_script)
        self.assertEqual(case.status, ModerationCase.Status.APPROVED)
        self.assertEqual(final_mark.mark, Decimal("74"))
        self.assertTrue(AuditEvent.objects.filter(aggregate_id=str(final_mark.id), action="valuation.final_mark.moderated").exists())

    def test_moderation_samples_multi_round_final_mark_once(self):
        second_evaluator = Evaluator.objects.filter(
            tenant_id=self.tenant_id,
            status=Evaluator.Status.ACTIVE,
        ).exclude(id=self.evaluator.id).first()
        self.assertIsNotNone(second_evaluator)
        second_assignment = Assignment.objects.create(
            tenant_id=self.tenant_id,
            script=self.phase4_script,
            evaluator=second_evaluator,
            valuation_round=2,
            status=Assignment.Status.SUBMITTED,
            due_at=timezone.now() + timedelta(days=3),
        )
        second_evaluation = Evaluation.objects.create(
            tenant_id=self.tenant_id,
            assignment=second_assignment,
            scheme=MarkingScheme.objects.filter(tenant_id=self.tenant_id, paper=self.paper).first(),
            status=Evaluation.Status.LOCKED,
            total_marks=Decimal("80"),
            checksum="6" * 64,
        )
        ValuationResult.objects.create(
            tenant_id=self.tenant_id,
            evaluation=second_evaluation,
            script=self.phase4_script,
            valuation_round=2,
            total_marks=Decimal("80"),
            checksum="7" * 64,
            is_locked=True,
            locked_by_id=self.controller.id,
            locked_at=timezone.now(),
        )
        final_mark = FinalMark.objects.get(script=self.phase4_script)
        final_mark.mark = Decimal("76")
        final_mark.calculation = {"totals": ["72", "80"], "rule": "average"}
        final_mark.save(update_fields=["mark", "calculation", "updated_at"])
        policy, _ = ModerationPolicy.objects.update_or_create(
            tenant_id=self.tenant_id,
            paper=self.paper,
            defaults={"sample_percentage": Decimal("100"), "sampling_modes": ["percentage_random"], "mandatory": True},
        )

        cases = sample_moderation_cases(tenant_id=self.tenant_id, actor_id=self.admin.id, policy=policy)

        self.assertEqual(len(cases), 1)
        self.assertEqual(ModerationCase.objects.filter(script=self.phase4_script).count(), 1)
        self.assertEqual(cases[0].original_mark, Decimal("76"))
