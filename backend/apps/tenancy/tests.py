import json

from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from apps.core.models import AuditEvent, OutboxEvent
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
            data=json.dumps({"name": "Domain University", "code": "domain-u", "subdomain": "domain", "admin_email": "admin@domain.example", "admin_first_name": "Domain", "admin_last_name": "Admin", "plan": "professional", "storage_quota_gb": 25, "data_region": "in-south", "policy": {"timezone": "Asia/Kolkata"}}),
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 200)
        body = created.json()
        self.assertEqual(body["hostname"], "domain.localhost")
        self.assertTrue(body["temporary_password"])
        account = TenantAccount.objects.get(root_institution__tenant_id=body["id"])
        self.assertEqual(account.plan, TenantAccount.Plan.PROFESSIONAL)
        self.assertEqual(account.storage_quota_bytes, 25 * 1024 * 1024 * 1024)
        self.assertTrue(TenantDomain.objects.filter(tenant_account=account, hostname="domain.localhost", status="active").exists())
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
