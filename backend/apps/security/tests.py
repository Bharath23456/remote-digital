import json

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase

from apps.core.models import AuditEvent, OutboxEvent
from apps.phase4.models import CentreProfile
from apps.tenancy.models import Membership

from .models import DlpIncident, PrivilegedAccessRequest, SecurityPolicy


class SecurityGovernanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap_demo", verbosity=0)
        cls.auditor = User.objects.get(username="auditor@admiezo.local")
        cls.auditor.set_password("AuditPass123!")
        cls.auditor.save()

    def setUp(self):
        self.admin = Client()
        response = self.post(
            self.admin,
            "/api/v1/auth/login",
            {"email": "admin@admiezo.local", "password": "ChangeMe123!", "device_id": "admin-device"},
        )
        self.assertEqual(response.status_code, 200)
        membership = Membership.objects.get(user__email="admin@admiezo.local")
        self.centre = CentreProfile.objects.create(
            tenant_id=membership.institution.tenant_id,
            code="SEC-CENTRE",
            name="Security Test Centre",
            location="Block S",
            capacity=50,
            workstation_count=10,
            status=CentreProfile.Status.ACTIVE,
        )

    def post(self, client, path, payload):
        return client.post(path, data=json.dumps(payload), content_type="application/json")

    def test_policy_requires_step_up_and_uses_optimistic_locking(self):
        payload = {
            "version": 0,
            "session_timeout_minutes": 45,
            "maximum_concurrent_sessions": 2,
            "step_up_minutes": 8,
            "failed_login_limit": 5,
            "lockout_minutes": 20,
            "require_mfa": False,
            "require_trusted_device": False,
            "approved_networks": [],
            "allowed_countries": ["IN"],
            "vpn_risk_threshold": 70,
            "alert_risk_threshold": 50,
            "dlp_enabled": True,
        }
        blocked = self.admin.put("/api/v1/security/policy", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(blocked.status_code, 428)
        self.assertEqual(self.post(self.admin, "/api/v1/auth/step-up", {"password": "ChangeMe123!"}).status_code, 200)
        saved = self.admin.put("/api/v1/security/policy", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(SecurityPolicy.objects.get().session_timeout_minutes, 45)
        stale = self.admin.put("/api/v1/security/policy", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(stale.status_code, 409)
        self.assertTrue(AuditEvent.objects.filter(action="security.policy.updated").exists())
        self.assertTrue(OutboxEvent.objects.filter(topic="security.policy.updated").exists())

    def test_privileged_access_requires_two_people(self):
        auditor_client = Client()
        login = self.post(
            auditor_client,
            "/api/v1/auth/login",
            {"email": "auditor@admiezo.local", "password": "AuditPass123!", "device_id": "auditor-device"},
        )
        self.assertEqual(login.status_code, 200)
        created = self.post(
            auditor_client,
            "/api/v1/security/privileged-access",
            {"requested_role": "audit_export", "reason": "Quarterly control evidence", "duration_minutes": 60},
        )
        self.assertEqual(created.status_code, 200)
        self.assertEqual(self.post(self.admin, "/api/v1/auth/step-up", {"password": "ChangeMe123!"}).status_code, 200)
        request_id = created.json()["id"]
        decided = self.post(
            self.admin,
            f"/api/v1/security/privileged-access/{request_id}/decision",
            {"version": 1, "approve": True, "note": "Approved for scheduled review"},
        )
        self.assertEqual(decided.status_code, 200)
        access_request = PrivilegedAccessRequest.objects.get(id=request_id)
        self.assertEqual(access_request.status, PrivilegedAccessRequest.Status.APPROVED)
        self.assertNotEqual(access_request.requester_id, access_request.decided_by_id)

    def test_dlp_blocks_candidate_identity_from_evaluation_core(self):
        response = self.post(
            self.admin,
            "/api/v1/repository/uploads",
            {"candidate_name": "Protected Student", "registration_number": "REG-100", "script_id": "invalid"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "Candidate identity data is prohibited in evaluation-core")
        incident = DlpIncident.objects.get(rule="candidate_identity_boundary")
        self.assertEqual(incident.details["fields"], ["candidate_name", "registration_number"])
        self.assertTrue(AuditEvent.objects.filter(action="security.dlp.blocked", aggregate_id=incident.id).exists())
        self.assertTrue(OutboxEvent.objects.filter(topic="security.dlp.blocked", aggregate_id=incident.id).exists())

    def test_control_catalog_reports_isolated_script_storage(self):
        response = self.admin.get("/api/v1/security/catalog")
        self.assertEqual(response.status_code, 200)
        controls = response.json()["controls"]
        self.assertTrue(controls["script_store_isolated"])
        self.assertTrue(controls["identity_service_authorization"])
        self.assertIn("vulnerability_monitoring", controls)

    def test_admin_creates_user_with_explicit_module_access_and_temporary_password(self):
        self.assertEqual(self.post(self.admin, "/api/v1/auth/step-up", {"password": "ChangeMe123!"}).status_code, 200)
        response = self.post(
            self.admin,
            "/api/v1/security/memberships",
            {
                "first_name": "New",
                "last_name": "Evaluator",
                "email": "new.evaluator@example.edu",
                "role": "evaluator",
                "permissions": [],
                "enabled_modules": ["evaluation"],
                "custom_fields": {},
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["temporary_password"])
        membership = Membership.objects.get(user__username="new.evaluator@example.edu")
        self.assertEqual(membership.enabled_modules, ["evaluation"])
        self.assertTrue(membership.must_change_password)
        self.assertTrue(AuditEvent.objects.filter(action="security.membership.created", aggregate_id=str(membership.id)).exists())

    def test_intake_desk_user_requires_exactly_its_module(self):
        self.assertEqual(self.post(self.admin, "/api/v1/auth/step-up", {"password": "ChangeMe123!"}).status_code, 200)
        for role, module in (("bundle_preparer", "receiving"), ("intake_receiver", "custody"), ("scan_operator", "digitization")):
            payload = {
                "first_name": "Desk", "last_name": "Worker", "email": f"{role}@example.edu",
                "role": role, "permissions": [], "enabled_modules": [module], "operational_centre_id": str(self.centre.id), "custom_fields": {},
            }
            created = self.post(self.admin, "/api/v1/security/memberships", payload)
            self.assertEqual(created.status_code, 200, created.content)
            self.assertEqual(Membership.objects.get(user__email=payload["email"]).enabled_modules, [module])
            payload["email"] = f"extra-{role}@example.edu"
            payload["enabled_modules"] = [module, "security"]
            denied = self.post(self.admin, "/api/v1/security/memberships", payload)
            self.assertEqual(denied.status_code, 422, denied.content)

    def test_operations_supervisor_requires_only_the_three_intake_modules(self):
        self.assertEqual(self.post(self.admin, "/api/v1/auth/step-up", {"password": "ChangeMe123!"}).status_code, 200)
        payload = {
            "first_name": "Intake", "last_name": "Supervisor", "email": "intake.supervisor@example.edu",
            "role": "operations_supervisor", "permissions": [],
            "enabled_modules": ["receiving", "custody", "digitization"], "operational_centre_id": str(self.centre.id), "custom_fields": {},
        }
        created = self.post(self.admin, "/api/v1/security/memberships", payload)
        self.assertEqual(created.status_code, 200, created.content)
        self.assertEqual(set(created.json()["enabled_modules"]), {"receiving", "custody", "digitization"})
        for modules in (["receiving", "custody"], ["receiving", "custody", "digitization", "security"]):
            payload["email"] = f"denied-{len(modules)}@example.edu"
            payload["enabled_modules"] = modules
            self.assertEqual(self.post(self.admin, "/api/v1/security/memberships", payload).status_code, 422)
        payload["email"] = "permission-denied@example.edu"
        payload["enabled_modules"] = ["receiving", "custody", "digitization"]
        payload["permissions"] = ["identity.resolve"]
        self.assertEqual(self.post(self.admin, "/api/v1/security/memberships", payload).status_code, 422)

    def test_operational_user_requires_an_active_centre(self):
        self.assertEqual(self.post(self.admin, "/api/v1/auth/step-up", {"password": "ChangeMe123!"}).status_code, 200)
        payload = {
            "first_name": "No", "last_name": "Centre", "email": "no-centre@example.edu",
            "role": "bundle_preparer", "permissions": [], "enabled_modules": ["receiving"], "custom_fields": {},
        }
        missing = self.post(self.admin, "/api/v1/security/memberships", payload)
        self.assertEqual(missing.status_code, 422)
        self.assertIn("active centre", missing.json()["detail"])
        payload["operational_centre_id"] = str(self.centre.id)
        created = self.post(self.admin, "/api/v1/security/memberships", payload)
        self.assertEqual(created.status_code, 200, created.content)
        self.assertEqual(Membership.objects.get(user__email=payload["email"]).operational_centre_id, self.centre.id)

    def test_emergency_role_grant_is_time_bound_and_revocable(self):
        auditor_client = Client()
        self.assertEqual(
            self.post(
                auditor_client,
                "/api/v1/auth/login",
                {"email": "auditor@admiezo.local", "password": "AuditPass123!", "device_id": "emergency-auditor"},
            ).status_code,
            200,
        )
        denied = self.post(auditor_client, "/api/v1/configuration/centres", {"code": "DENIED", "name": "Denied"})
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.post(self.admin, "/api/v1/auth/step-up", {"password": "ChangeMe123!"}).status_code, 200)
        grant = self.post(
            self.admin,
            "/api/v1/security/emergency-access",
            {
                "user_id": self.auditor.id,
                "role": "exam_controller",
                "incident_reference": "INC-42",
                "justification": "Restore evaluation operations during incident",
                "duration_minutes": 15,
            },
        )
        self.assertEqual(grant.status_code, 200)
        allowed = self.post(auditor_client, "/api/v1/configuration/centres", {"code": "EMERGENCY", "name": "Emergency centre"})
        self.assertEqual(allowed.status_code, 200)
        revoked = self.post(self.admin, f"/api/v1/security/emergency-access/{grant.json()['id']}/revoke", {})
        self.assertEqual(revoked.status_code, 200)
        denied_again = self.post(auditor_client, "/api/v1/configuration/centres", {"code": "REVOKED", "name": "Revoked"})
        self.assertEqual(denied_again.status_code, 403)
