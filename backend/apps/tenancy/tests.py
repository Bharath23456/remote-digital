import json
from unittest.mock import patch

from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from apps.core.models import AuditEvent, OutboxEvent
from apps.ai_evaluation.models import AIProviderConfiguration
from apps.ai_evaluation.provider import AdmiezoAIError
from apps.evaluators.models import Evaluator
from apps.repository.storage import ObjectMetadata
from apps.security.crypto import decrypt_secret
from apps.security.models import SecurityPolicy
from apps.tenancy.models import Institution, Membership, TenantAccount, TenantDomain


@override_settings(ALLOWED_HOSTS=["testserver", ".localhost"])
class InstitutionHierarchyTests(TestCase):
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

    def test_add_child_uses_root_tenant_and_records_event(self):
        root = Institution.objects.get(code="northbridge-university")
        response = self.client.post(
            "/api/v1/enterprise/institutions",
            data=json.dumps({"name": "City Campus", "code": "city-campus", "kind": "campus", "parent_id": str(root.id), "policy": {"timezone": "Asia/Kolkata"}}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        campus = Institution.objects.get(id=response.json()["id"])
        self.assertEqual(campus.tenant_id, root.tenant_id)
        self.assertEqual(campus.parent, root)
        self.assertTrue(AuditEvent.objects.filter(action="tenancy.institution.created", aggregate_id=str(campus.id)).exists())
        self.assertTrue(OutboxEvent.objects.filter(topic="tenancy.institution.created", aggregate_id=str(campus.id)).exists())

    def test_non_root_institution_requires_parent(self):
        response = self.client.post(
            "/api/v1/enterprise/institutions",
            data=json.dumps({"name": "Detached Faculty", "code": "detached", "kind": "faculty"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 422)

    def test_platform_admin_provisions_and_switches_isolated_university(self):
        platform = Client()
        login = platform.post(
            "/api/v1/auth/login",
            data=json.dumps({"email": "platform@admiezo.local", "password": "ChangeMe123!"}),
            content_type="application/json",
        )
        self.assertEqual(login.status_code, 200)
        created = platform.post(
            "/api/v1/enterprise/tenants",
            data=json.dumps({"name": "Southshore University", "code": "southshore", "admin_email": "dean@southshore.example", "admin_first_name": "Riya", "admin_last_name": "Sen", "policy": {"timezone": "Asia/Kolkata"}}),
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 200)
        tenant_id = created.json()["id"]
        switched = platform.post(
            "/api/v1/enterprise/tenants/switch",
            data=json.dumps({"tenant_id": tenant_id}),
            content_type="application/json",
        )
        self.assertEqual(switched.status_code, 200)
        rows = platform.get("/api/v1/enterprise/institutions").json()
        self.assertEqual([row["code"] for row in rows], ["southshore"])
        denied = self.client.post(
            "/api/v1/enterprise/tenants/switch",
            data=json.dumps({"tenant_id": tenant_id}),
            content_type="application/json",
        )
        self.assertEqual(denied.status_code, 403)
        self.assertTrue(AuditEvent.objects.filter(tenant_id=tenant_id, action="tenancy.tenant.provisioned").exists())

    def test_control_plane_provisions_domain_bound_administrator(self):
        self.assertEqual(self.client.get("/api/v1/enterprise/control-plane").status_code, 403)
        self.assertEqual(self.client.get("/api/v1/enterprise/control-plane/audit").status_code, 403)
        platform = Client()
        self.assertEqual(platform.post("/api/v1/auth/login", data=json.dumps({"email": "platform@admiezo.local", "password": "ChangeMe123!"}), content_type="application/json").status_code, 200)
        created = platform.post(
            "/api/v1/enterprise/tenants",
            data=json.dumps({"name": "Domain University", "code": "domain-u", "subdomain": "domain", "admin_email": "admin@domain.example", "admin_first_name": "Domain", "admin_last_name": "Admin", "plan": "professional", "storage_quota_gb": 25, "data_region": "in-south", "brand_name": "Domain Evaluation", "brand_description": "Secure digital assessment", "brand_theme": "ocean", "policy": {"timezone": "Asia/Kolkata"}}),
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 200)
        body = created.json()
        self.assertEqual(body["hostname"], "domain.localhost")
        self.assertTrue(body["temporary_password"])
        account = TenantAccount.objects.get(root_institution__tenant_id=body["id"])
        self.assertEqual(account.plan, TenantAccount.Plan.PROFESSIONAL)
        self.assertEqual(account.storage_quota_bytes, 25 * 1024 * 1024 * 1024)
        self.assertEqual((account.brand_name, account.brand_theme), ("Domain Evaluation", TenantAccount.Theme.OCEAN))
        self.assertTrue(TenantDomain.objects.filter(tenant_account=account, hostname="domain.localhost", status="active").exists())
        domain_context = Client().get("/api/v1/enterprise/domain-context", HTTP_HOST="domain.localhost").json()
        self.assertEqual(domain_context["university"]["branding"]["name"], "Domain Evaluation")
        self.assertEqual(domain_context["university"]["branding"]["theme"], "ocean")
        audit_rows = platform.get("/api/v1/enterprise/control-plane/audit").json()
        self.assertTrue(any(row["university"] == "Domain University" and row["action"] == "tenancy.tenant.provisioned" for row in audit_rows))

        university = Client()
        login = university.post(
            "/api/v1/auth/login",
            data=json.dumps({"email": "admin@domain.example", "password": body["temporary_password"]}),
            content_type="application/json",
            HTTP_HOST="domain.localhost",
        )
        self.assertEqual(login.status_code, 200)
        self.assertTrue(login.json()["must_change_password"])
        self.assertEqual(login.json()["branding"]["description"], "Secure digital assessment")
        self.assertEqual(university.get("/api/v1/operations/overview", HTTP_HOST="domain.localhost").status_code, 428)
        csrf = university.get("/api/v1/auth/csrf", HTTP_HOST="domain.localhost")
        self.assertEqual(csrf.status_code, 200)
        self.assertTrue(csrf.json()["csrf_token"])
        changed = university.post(
            "/api/v1/auth/password/complete-setup",
            data=json.dumps({"new_password": "Unique-Domain-Password-2046!"}),
            content_type="application/json",
            HTTP_HOST="domain.localhost",
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(university.get("/api/v1/operations/overview", HTTP_HOST="domain.localhost").status_code, 200)
        self.assertFalse(Membership.objects.get(user__username="admin@domain.example", institution=account.root_institution).must_change_password)

        wrong_university = Client()
        denied = wrong_university.post(
            "/api/v1/auth/login",
            data=json.dumps({"email": "admin@admiezo.local", "password": "ChangeMe123!"}),
            content_type="application/json",
            HTTP_HOST="domain.localhost",
        )
        self.assertEqual(denied.status_code, 403)

    def test_platform_host_blocks_university_login_and_supports_tenant_logo(self):
        self.assertEqual(self.client.get("/api/v1/auth/me", HTTP_HOST="localhost").status_code, 403)
        blocked = Client().post(
            "/api/v1/auth/login",
            data=json.dumps({"email": "admin@admiezo.local", "password": "ChangeMe123!"}),
            content_type="application/json",
            HTTP_HOST="localhost",
        )
        self.assertEqual(blocked.status_code, 403)
        self.assertIn("subdomain", blocked.json()["detail"])

        platform = Client()
        login = platform.post(
            "/api/v1/auth/login",
            data=json.dumps({"email": "platform@admiezo.local", "password": "ChangeMe123!"}),
            content_type="application/json",
            HTTP_HOST="localhost",
        )
        self.assertEqual(login.status_code, 200, login.content)
        account = Institution.objects.get(code="northbridge-university").tenant_account
        intent = platform.post(
            f"/api/v1/enterprise/tenants/{account.root_institution.tenant_id}/branding/logo-upload",
            data=json.dumps({"content_type": "image/png", "maximum_bytes": 1024}),
            content_type="application/json",
            HTTP_HOST="localhost",
        )
        self.assertEqual(intent.status_code, 200, intent.content)
        with patch("apps.repository.storage.read_object_metadata", return_value=ObjectMetadata(sha256="a" * 64, byte_size=1024, mime_type="image/png")):
            finalized = platform.post(
                f"/api/v1/enterprise/tenants/{account.root_institution.tenant_id}/branding/logo-finalize",
                data=json.dumps({"version": account.version, "storage_key": intent.json()["storage_key"], "content_type": "image/png"}),
                content_type="application/json",
                HTTP_HOST="localhost",
            )
        self.assertEqual(finalized.status_code, 200, finalized.content)
        self.assertTrue(finalized.json()["branding"]["logo_url"])

    def test_platform_ai_policy_uses_a_verified_key_per_university_and_controls_system_evaluator(self):
        platform = Client()
        self.assertEqual(platform.post("/api/v1/auth/login", data=json.dumps({"email": "platform@admiezo.local", "password": "ChangeMe123!"}), content_type="application/json").status_code, 200)
        payload = {
            "name": "AI University",
            "code": "ai-university",
            "subdomain": "ai-university",
            "admin_email": "admin@ai-university.example",
            "admin_first_name": "AI",
            "admin_last_name": "Admin",
            "policy": {"timezone": "Asia/Kolkata"},
            "ai_evaluation_mode": "autonomous",
            "ai_confidence_threshold": 88,
            "ai_model_name": "admiezo-ai-v1",
        }
        denied = platform.post("/api/v1/enterprise/tenants", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(denied.status_code, 422)
        self.assertFalse(Institution.objects.filter(code="ai-university").exists())

        payload["ai_api_key"] = "university-provider-key"
        with patch("apps.ai_evaluation.services.AdmiezoAIClient.validate_model"):
            created = platform.post("/api/v1/enterprise/tenants", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(created.status_code, 200)
        tenant_id = created.json()["id"]
        stored_provider = AIProviderConfiguration.objects.get(tenant_id=tenant_id)
        self.assertNotIn("university-provider-key", stored_provider.api_key_ciphertext)
        self.assertEqual(decrypt_secret(stored_provider.api_key_ciphertext), "university-provider-key")
        self.assertFalse(AIProviderConfiguration.objects.exclude(tenant_id=tenant_id).exists())
        policy = SecurityPolicy.objects.get(tenant_id=tenant_id)
        account = TenantAccount.objects.get(root_institution__tenant_id=tenant_id)
        admin_membership = Membership.objects.get(user__username=payload["admin_email"], institution__tenant_id=tenant_id)
        system_evaluator = Evaluator.objects.get(tenant_id=tenant_id, evaluator_code="AI-ADMIEZO")
        self.assertEqual(policy.ai_evaluation_mode, SecurityPolicy.AIEvaluationMode.AUTONOMOUS)
        self.assertEqual(float(policy.ai_confidence_threshold), 88)
        self.assertIn("ai_evaluation", account.enabled_modules)
        self.assertIn("ai_evaluation", admin_membership.enabled_modules)
        self.assertTrue(system_evaluator.is_system_ai)
        self.assertEqual(system_evaluator.status, Evaluator.Status.ACTIVE)
        university_row = next(item for item in platform.get("/api/v1/enterprise/control-plane").json()["universities"] if item["id"] == tenant_id)
        self.assertTrue(university_row["ai_provider"]["configured"])
        self.assertNotIn("api_key", university_row["ai_provider"])

        with patch("apps.ai_evaluation.provider.AdmiezoAIClient.validate_model") as validate_model:
            updated = platform.patch(
                f"/api/v1/enterprise/tenants/{tenant_id}",
                data=json.dumps({"version": account.version, "ai_model_name": "models/provider-custom"}),
                content_type="application/json",
            )
        self.assertEqual(updated.status_code, 200)
        validate_model.assert_called_with("models/provider-custom")
        policy.refresh_from_db()
        self.assertEqual(policy.ai_model_name, "models/provider-custom")
        account.refresh_from_db()
        with patch("apps.ai_evaluation.provider.AdmiezoAIClient.validate_model", side_effect=AdmiezoAIError("Unsupported AI model")):
            unsupported = platform.patch(
                f"/api/v1/enterprise/tenants/{tenant_id}",
                data=json.dumps({"version": account.version, "ai_model_name": "unsupported-model"}),
                content_type="application/json",
            )
        self.assertEqual(unsupported.status_code, 422)
        self.assertIn("Unsupported AI model", unsupported.json()["detail"])
        policy.refresh_from_db()
        self.assertEqual(policy.ai_model_name, "models/provider-custom")

        disabled = platform.patch(
            f"/api/v1/enterprise/tenants/{tenant_id}",
            data=json.dumps({"version": account.version, "ai_evaluation_mode": "disabled"}),
            content_type="application/json",
        )
        self.assertEqual(disabled.status_code, 200)
        account.refresh_from_db()
        admin_membership.refresh_from_db()
        system_evaluator.refresh_from_db()
        self.assertNotIn("ai_evaluation", account.enabled_modules)
        self.assertNotIn("ai_evaluation", admin_membership.enabled_modules)
        self.assertEqual(system_evaluator.status, Evaluator.Status.INACTIVE)

    def test_platform_controls_lifecycle_and_custom_domain_request(self):
        platform = Client()
        platform.post("/api/v1/auth/login", data=json.dumps({"email": "platform@admiezo.local", "password": "ChangeMe123!"}), content_type="application/json")
        root = Institution.objects.get(code="northbridge-university")
        account = root.tenant_account
        domain = platform.post(
            f"/api/v1/enterprise/tenants/{root.tenant_id}/domains",
            data=json.dumps({"hostname": "evaluation.northbridge.edu"}),
            content_type="application/json",
        )
        self.assertEqual(domain.status_code, 200)
        self.assertEqual(domain.json()["status"], "pending")
        self.assertTrue(domain.json()["verification_token"])
        suspended = platform.patch(
            f"/api/v1/enterprise/tenants/{root.tenant_id}",
            data=json.dumps({"version": account.version, "status": "suspended"}),
            content_type="application/json",
        )
        self.assertEqual(suspended.status_code, 200)
        self.assertFalse(Institution.objects.get(id=root.id).is_active)
        self.assertEqual(Client().get("/api/v1/auth/csrf", HTTP_HOST="northbridge.localhost").status_code, 423)

    def test_module_entitlements_are_enforced_at_navigation_api_boundary(self):
        platform = Client()
        platform.post("/api/v1/auth/login", data=json.dumps({"email": "platform@admiezo.local", "password": "ChangeMe123!"}), content_type="application/json")
        root = Institution.objects.get(code="northbridge-university")
        account = root.tenant_account
        changed = platform.patch(
            f"/api/v1/enterprise/tenants/{root.tenant_id}",
            data=json.dumps({"version": account.version, "enabled_modules": ["configuration", "enterprise"]}),
            content_type="application/json",
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/configuration/catalog").status_code, 200)
        denied = self.client.get("/api/v1/receiving/catalog")
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["code"], "module_not_enabled")
