import json
from uuid import uuid4
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase

from apps.configuration.models import Paper
from apps.core.models import AuditEvent, OutboxEvent
from apps.custody.models import Script
from apps.core.outbox import publish_next
from apps.tenancy.models import Institution, Membership


class EvaluationCoreApiTests(TestCase):
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

    def test_overview_is_authenticated_and_tenant_scoped(self):
        response = self.client.get("/api/v1/operations/overview")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["session"]["name"], "November 2026 End Semester")
        self.assertGreater(body["metrics"]["expected_scripts"], 0)
        self.assertEqual(len(body["papers"]), 4)

    def test_evaluator_role_is_blocked_from_administrative_apis(self):
        client = Client()
        login = client.post(
            "/api/v1/auth/login",
            data=json.dumps({"email": "evaluator1043@admiezo.local", "password": "ChangeMe123!", "device_id": "role-boundary-test"}),
            content_type="application/json",
        )
        self.assertEqual(login.status_code, 200)
        self.assertEqual(client.get("/api/v1/operations/overview").status_code, 403)
        self.assertEqual(client.get("/api/v1/config/papers").status_code, 403)
        self.assertEqual(client.get("/api/v1/evaluators").status_code, 403)
        self.assertEqual(client.get("/api/v1/phase4/catalog").status_code, 403)
        catalog = client.get("/api/v1/allocation/catalog")
        self.assertEqual(catalog.status_code, 200)
        assignment_id = catalog.json()["assignments"][0]["id"]
        self.assertEqual(client.get(f"/api/v1/marking/assignments/{assignment_id}/workspace").status_code, 423)
        self.assertEqual(client.post(f"/api/v1/valuation/evaluations/{uuid4()}/finalize").status_code, 404)

    def test_paper_update_requires_current_version_and_records_outbox(self):
        source = Paper.objects.first()
        source.session.status = source.session.Status.DRAFT
        source.session.save(update_fields=["status"])
        paper = Paper.objects.create(
            tenant_id=source.tenant_id,
            session=source.session,
            subject=source.subject,
            code="CORE-DRAFT",
            title="Draft paper",
            max_marks=100,
            pass_marks=40,
            discrepancy_threshold=15,
        )
        stale = self.client.patch(
            f"/api/v1/config/papers/{paper.id}",
            data=json.dumps({"version": paper.version + 1, "pass_marks": "45"}),
            content_type="application/json",
        )
        self.assertEqual(stale.status_code, 409)
        response = self.client.patch(
            f"/api/v1/config/papers/{paper.id}",
            data=json.dumps({"version": paper.version, "pass_marks": "45"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(AuditEvent.objects.filter(action="config.paper.updated", aggregate_id=str(paper.id)).exists())
        self.assertTrue(OutboxEvent.objects.filter(topic="config.paper.updated", aggregate_id=str(paper.id)).exists())

    def test_invalid_custody_transition_is_rejected_without_event(self):
        script = Script.objects.filter(state=Script.State.STORED).first()
        before = script.custody_events.count()
        response = self.client.post(
            f"/api/v1/custody/scripts/{script.id}/transition",
            data=json.dumps({"version": script.version, "to_state": "finalized", "location": "test"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(script.custody_events.count(), before)


class CandidatePiiBoundaryTests(TestCase):
    def test_evaluation_core_models_do_not_contain_candidate_pii_fields(self):
        forbidden = {
            "candidate_address",
            "candidate_email",
            "candidate_mobile",
            "candidate_name",
            "candidate_phone",
            "candidate_photo",
            "candidate_signature",
            "date_of_birth",
            "hall_ticket",
            "register_number",
            "registration_number",
            "roll_number",
            "student_name",
            "usn",
        }
        core_apps = {
            "allocation",
            "anonymisation",
            "configuration",
            "custody",
            "receiving",
            "repository",
            "security",
        }
        exposed = set()
        for model in apps.get_models():
            if model._meta.app_label in core_apps:
                exposed.update(field.name for field in model._meta.fields)
        self.assertFalse(forbidden.intersection(exposed))


class OperationalRoleBoundaryTests(TestCase):
    password = "RoleBoundary123!"

    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap_demo", verbosity=0)
        institution = Institution.objects.get(code="northbridge-university")
        for email, role, modules in (
            ("receiver-rbac@admiezo.local", Membership.Role.SCRIPT_RECEIVER, ["receiving"]),
            ("scanner-rbac@admiezo.local", Membership.Role.SCANNER_OPERATOR, ["digitization"]),
            ("custody-rbac@admiezo.local", Membership.Role.CUSTODY_OFFICER, ["custody"]),
        ):
            user = User.objects.create_user(username=email, email=email, password=cls.password)
            Membership.objects.create(
                user=user,
                institution=institution,
                role=role,
                enabled_modules=modules,
            )

    def client_for(self, email):
        client = Client()
        response = client.post(
            "/api/v1/auth/login",
            data=json.dumps({"email": email, "password": self.password, "device_id": f"{email}-device"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        return client, response.json()

    def test_script_receiver_is_limited_to_receiving(self):
        client, context = self.client_for("receiver-rbac@admiezo.local")
        self.assertEqual(context["enabled_modules"], ["receiving"])
        self.assertEqual(client.get("/api/v1/receiving/catalog").status_code, 200)
        self.assertEqual(client.get("/api/v1/scanning/catalog").status_code, 403)
        self.assertEqual(client.get("/api/v1/custody/catalog").status_code, 403)
        self.assertEqual(client.get("/api/v1/security/catalog").status_code, 403)

    def test_scanner_operator_is_limited_to_digitization(self):
        client, context = self.client_for("scanner-rbac@admiezo.local")
        self.assertEqual(context["enabled_modules"], ["digitization"])
        self.assertEqual(client.get("/api/v1/scanning/catalog").status_code, 200)
        self.assertEqual(client.get("/api/v1/scan-processing/catalog").status_code, 200)
        self.assertEqual(client.get("/api/v1/integrity/catalog").status_code, 200)
        self.assertEqual(client.get("/api/v1/receiving/catalog").status_code, 403)
        self.assertEqual(client.get("/api/v1/custody/catalog").status_code, 403)
        self.assertEqual(client.get("/api/v1/security/catalog").status_code, 403)

    def test_custody_officer_is_limited_to_chain_of_custody(self):
        client, context = self.client_for("custody-rbac@admiezo.local")
        self.assertEqual(context["enabled_modules"], ["custody"])
        self.assertEqual(client.get("/api/v1/custody/catalog").status_code, 200)
        self.assertEqual(client.get("/api/v1/receiving/catalog").status_code, 200)
        self.assertEqual(client.get("/api/v1/scanning/catalog").status_code, 403)
        self.assertEqual(client.get("/api/v1/security/catalog").status_code, 403)


class CsrfBoundaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap_demo", verbosity=0)

    def test_session_login_and_domain_writes_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        payload = json.dumps({"email": "admin@admiezo.local", "password": "ChangeMe123!"})
        login = client.post(
            "/api/v1/auth/login",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(login.status_code, 200)
        write_payload = json.dumps({"code": "CSRF", "name": "CSRF proof centre"})
        self.assertEqual(
            client.post("/api/v1/configuration/centres", data=write_payload, content_type="application/json").status_code,
            403,
        )
        rotated = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        self.assertEqual(
            client.post(
                "/api/v1/configuration/centres",
                data=write_payload,
                content_type="application/json",
                HTTP_X_CSRFTOKEN=rotated,
            ).status_code,
            200,
        )


class TransactionalOutboxTests(TestCase):
    def test_publisher_marks_success_and_retries_failure(self):
        event = OutboxEvent.objects.create(tenant_id="11111111-1111-1111-1111-111111111111", topic="test.created", aggregate_id="42", payload={"value": 7})
        with patch("apps.core.outbox._deliver"):
            self.assertTrue(publish_next())
        event.refresh_from_db()
        self.assertIsNotNone(event.published_at)
        self.assertEqual(event.attempt_count, 1)

        retry = OutboxEvent.objects.create(tenant_id=event.tenant_id, topic="test.failed", aggregate_id="43")
        with patch("apps.core.outbox._deliver", side_effect=OSError("destination unavailable")):
            self.assertTrue(publish_next())
        retry.refresh_from_db()
        self.assertIsNone(retry.published_at)
        self.assertEqual(retry.attempt_count, 1)
        self.assertIn("destination unavailable", retry.last_error)
