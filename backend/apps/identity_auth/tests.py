import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.management import call_command
from django.test import Client, RequestFactory, TestCase, override_settings

from apps.core.models import AuditEvent, OutboxEvent
from apps.security.crypto import decrypt_secret, encrypt_secret
from apps.security.models import SecurityPolicy
from apps.tenancy.models import Institution, Membership

from .models import AccessSession, AuthenticationMethod, OidcProvider, TrustedDevice
from .services import complete_oidc_login
from .totp import code_at


class IdentityAuthenticationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap_demo", verbosity=0)

    def setUp(self):
        self.client = Client()

    def post(self, path, payload):
        return self.client.post(path, data=json.dumps(payload), content_type="application/json")

    def login(self):
        return self.post(
            "/api/v1/auth/login",
            {
                "email": "admin@admiezo.local",
                "password": "ChangeMe123!",
                "device_id": "test-device-key",
                "device_label": "Test laptop",
                "platform": "macOS",
                "browser": "Test client",
            },
        )

    def test_login_binds_device_and_creates_audited_session(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        session_id = response.json()["session"]["id"]
        session = AccessSession.objects.get(id=session_id)
        self.assertEqual(session.device.label, "Test laptop")
        self.assertTrue(TrustedDevice.objects.filter(user=session.user, device_hash=session.device.device_hash).exists())
        self.assertTrue(AuditEvent.objects.filter(action="auth.login.succeeded", aggregate_id=session_id).exists())
        self.assertTrue(OutboxEvent.objects.filter(topic="auth.login.succeeded", aggregate_id=session_id).exists())

    def test_totp_secret_is_encrypted_and_required_on_next_login(self):
        self.assertEqual(self.login().status_code, 200)
        setup = self.post("/api/v1/auth/totp/setup", {"label": "Primary phone"})
        self.assertEqual(setup.status_code, 200)
        body = setup.json()
        method = AuthenticationMethod.objects.get(id=body["method_id"])
        self.assertNotIn(body["secret"], method.secret_ciphertext)
        self.assertEqual(decrypt_secret(method.secret_ciphertext), body["secret"])
        confirm = self.post(
            "/api/v1/auth/totp/confirm",
            {"method_id": body["method_id"], "code": code_at(body["secret"])},
        )
        self.assertEqual(confirm.status_code, 200)
        self.assertEqual(self.post("/api/v1/auth/logout", {}).status_code, 200)
        challenge = self.login()
        self.assertEqual(challenge.status_code, 202)
        verified = self.post("/api/v1/auth/mfa/verify", {"code": code_at(body["secret"])})
        self.assertEqual(verified.status_code, 200)
        self.assertTrue(verified.json()["session"]["mfa_verified"])

    def test_active_authenticator_replacement_requires_step_up_and_keeps_one_active_method(self):
        self.assertEqual(self.login().status_code, 200)
        user = Membership.objects.get(user__username="admin@admiezo.local").user
        original = AuthenticationMethod.objects.create(
            user=user,
            kind=AuthenticationMethod.Kind.TOTP,
            label="Primary authenticator",
            secret_ciphertext=encrypt_secret("original-secret"),
            is_active=True,
            is_primary=True,
        )
        blocked = self.post("/api/v1/auth/totp/setup", {"label": "Primary authenticator"})
        self.assertEqual(blocked.status_code, 403)
        self.assertTrue(AuthenticationMethod.objects.get(id=original.id).is_active)
        self.assertEqual(self.post("/api/v1/auth/step-up", {"password": "ChangeMe123!"}).status_code, 200)
        setup = self.post("/api/v1/auth/totp/setup", {"label": "Primary authenticator"})
        self.assertEqual(setup.status_code, 200)
        self.assertNotEqual(setup.json()["method_id"], str(original.id))
        confirmed = self.post(
            "/api/v1/auth/totp/confirm",
            {"method_id": setup.json()["method_id"], "code": code_at(setup.json()["secret"])},
        )
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(AuthenticationMethod.objects.filter(user=user, kind="totp", is_active=True).count(), 1)
        original.refresh_from_db()
        self.assertFalse(original.is_active)

    @override_settings(DEBUG=True, DEMO_PASSWORD_ONLY_LOGIN=True)
    def test_demo_password_only_login_skips_an_enrolled_authenticator(self):
        user = Membership.objects.get(user__username="admin@admiezo.local").user
        AuthenticationMethod.objects.create(
            user=user,
            kind=AuthenticationMethod.Kind.TOTP,
            label="Demo authenticator",
            secret_ciphertext=encrypt_secret("demo-secret"),
            is_active=True,
        )

        response = self.login()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["session"]["mfa_verified"])

    @override_settings(DEBUG=True, DEMO_PASSWORD_ONLY_LOGIN=True)
    def test_required_tenant_mfa_enrolls_user_during_login(self):
        membership = Membership.objects.get(user__username="admin@admiezo.local")
        policy, _ = SecurityPolicy.objects.get_or_create(tenant_id=membership.institution.tenant_id)
        policy.require_mfa = True
        policy.save(update_fields=["require_mfa", "updated_at"])

        challenge = self.login()

        self.assertEqual(challenge.status_code, 202)
        self.assertEqual(challenge.json()["challenge"], "mfa_enrollment")
        enrollment = challenge.json()["enrollment"]
        self.assertTrue(enrollment["provisioning_uri"].startswith("otpauth://totp/"))
        self.assertEqual(
            self.post(
                "/api/v1/auth/mfa/enroll",
                {"method_id": enrollment["method_id"], "code": "000000"},
            ).status_code,
            422,
        )

        completed = self.post(
            "/api/v1/auth/mfa/enroll",
            {"method_id": enrollment["method_id"], "code": code_at(enrollment["secret"])},
        )

        self.assertEqual(completed.status_code, 200)
        self.assertTrue(completed.json()["session"]["mfa_verified"])
        self.assertTrue(
            AuthenticationMethod.objects.filter(
                id=enrollment["method_id"],
                user=membership.user,
                kind=AuthenticationMethod.Kind.TOTP,
                is_active=True,
            ).exists()
        )
        self.assertTrue(
            AuditEvent.objects.filter(
                action="auth.totp.enabled",
                aggregate_id=enrollment["method_id"],
            ).exists()
        )

        self.assertEqual(self.post("/api/v1/auth/logout", {}).status_code, 200)
        next_login = self.login()
        self.assertEqual(next_login.status_code, 202)
        self.assertEqual(next_login.json()["challenge"], "mfa")

    @override_settings(DEBUG=False, DEMO_PASSWORD_ONLY_LOGIN=True)
    def test_demo_password_only_login_cannot_bypass_production_mfa(self):
        user = Membership.objects.get(user__username="admin@admiezo.local").user
        AuthenticationMethod.objects.create(
            user=user,
            kind=AuthenticationMethod.Kind.TOTP,
            label="Production authenticator",
            secret_ciphertext=encrypt_secret("production-secret"),
            is_active=True,
        )

        self.assertEqual(self.login().status_code, 202)

    def test_revoked_session_is_rejected_by_middleware(self):
        login_response = self.login()
        session = AccessSession.objects.get(id=login_response.json()["session"]["id"])
        session.revoked_at = session.created_at
        session.revoked_reason = "test"
        session.save(update_fields=["revoked_at", "revoked_reason"])
        response = self.client.get("/api/v1/operations/overview")
        self.assertEqual(response.status_code, 401)

    @patch("apps.identity_auth.services.oidc_discovery")
    def test_oidc_start_uses_state_nonce_and_pkce(self, discovery):
        institution = Institution.objects.get(code="northbridge-university")
        provider = OidcProvider.objects.create(
            tenant_id=institution.tenant_id,
            name="University SSO",
            issuer="https://identity.example.edu",
            client_id="admiezo-client",
            client_secret_ciphertext=encrypt_secret("top-secret"),
            domain_hint="admiezo.local",
        )
        discovery.return_value = {
            "authorization_endpoint": "https://identity.example.edu/authorize",
            "token_endpoint": "https://identity.example.edu/token",
            "jwks_uri": "https://identity.example.edu/jwks",
        }
        response = self.post(
            "/api/v1/auth/sso/start",
            {"provider_id": str(provider.id), "device_id": "sso-browser"},
        )
        self.assertEqual(response.status_code, 200)
        query = parse_qs(urlparse(response.json()["authorization_url"]).query)
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["response_type"], ["code"])
        self.assertTrue(query["state"][0])
        self.assertTrue(query["nonce"][0])
        self.assertNotIn("top-secret", response.json()["authorization_url"])

    @patch("apps.identity_auth.services.jwt.PyJWKClient")
    @patch("apps.identity_auth.services._request_json")
    @patch("apps.identity_auth.services.oidc_discovery")
    def test_oidc_completion_verifies_signed_token_and_tenant_membership(self, discovery, request_json, jwks_client):
        institution = Institution.objects.get(code="northbridge-university")
        provider = OidcProvider.objects.create(
            tenant_id=institution.tenant_id,
            name="University SSO",
            issuer="https://identity.example.edu",
            client_id="admiezo-client",
            client_secret_ciphertext=encrypt_secret("top-secret"),
            domain_hint="admiezo.local",
        )
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.now(timezone.utc)
        token = jwt.encode(
            {
                "iss": provider.issuer,
                "aud": provider.client_id,
                "sub": "institutional-123",
                "iat": now,
                "exp": now + timedelta(minutes=5),
                "nonce": "expected-nonce",
                "email": "admin@admiezo.local",
                "email_verified": True,
                "amr": ["pwd", "mfa"],
            },
            private_key,
            algorithm="RS256",
            headers={"kid": "test-key"},
        )
        discovery.return_value = {
            "issuer": provider.issuer,
            "authorization_endpoint": f"{provider.issuer}/authorize",
            "token_endpoint": f"{provider.issuer}/token",
            "jwks_uri": f"{provider.issuer}/jwks",
            "id_token_signing_alg_values_supported": ["RS256"],
        }
        request_json.return_value = {"id_token": token}
        jwks_client.return_value.get_signing_key_from_jwt.return_value = SimpleNamespace(key=private_key.public_key())
        request = RequestFactory().get("/api/v1/auth/sso/callback")
        request.session = {
            "oidc_flow": {
                "provider_id": str(provider.id),
                "state": "expected-state",
                "nonce": "expected-nonce",
                "verifier": "pkce-verifier",
                "redirect_uri": "http://testserver/api/v1/auth/sso/callback",
                "expires_at": int((now + timedelta(minutes=5)).timestamp()),
                "context": {"raw_device_id": "sso-browser"},
            }
        }
        user, membership, _context, mfa_verified, claims = complete_oidc_login(
            request,
            state="expected-state",
            code="authorization-code",
        )
        self.assertEqual(user.username, "admin@admiezo.local")
        self.assertEqual(membership.institution.tenant_id, provider.tenant_id)
        self.assertTrue(mfa_verified)
        self.assertEqual(claims["sub"], "institutional-123")
        self.assertNotIn("oidc_flow", request.session)
