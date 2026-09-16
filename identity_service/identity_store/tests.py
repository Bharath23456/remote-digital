import base64
import hashlib
import hmac
import json
import time
import uuid

from django.conf import settings
from django.test import Client, TestCase

from .models import CandidateIdentity, IdentityAccessLog


def token(action, tenant_id, identity_reference, script_id):
    claims = {"action": action, "tenant_id": str(tenant_id), "identity_reference": str(identity_reference), "script_id": str(script_id), "actor_id": "91", "purpose": "Authorized test", "request_id": "", "session_id": "", "jti": uuid.uuid4().hex, "exp": int(time.time()) + 300}
    encoded = base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode()).decode().rstrip("=")
    signature = hmac.new(settings.IDENTITY_AUTHORIZATION_KEY.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


class IdentityBoundaryTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.tenant_id = uuid.uuid4()
        self.identity_reference = uuid.uuid4()
        self.script_id = uuid.uuid4()

    def test_pii_is_encrypted_and_authorizations_are_one_use(self):
        create_token = token("identity.create", self.tenant_id, self.identity_reference, self.script_id)
        payload = {"candidate_name": "Maya Joseph", "register_number": "REG-20401", "usn": "USN-20401", "college": "Engineering College", "institution": "Northbridge University"}
        response = self.client.post("/v1/candidates", data=json.dumps(payload), content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {create_token}")
        self.assertEqual(response.status_code, 201)
        stored = CandidateIdentity.objects.get(identity_reference=self.identity_reference)
        self.assertNotIn("Maya Joseph", stored.pii_ciphertext)
        self.assertNotIn("REG-20401", stored.pii_ciphertext)
        replay = self.client.post("/v1/candidates", data=json.dumps(payload), content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {create_token}")
        self.assertEqual(replay.status_code, 409)
        resolve_token = token("identity.resolve", self.tenant_id, self.identity_reference, self.script_id)
        resolved = self.client.get(f"/v1/candidates/{self.identity_reference}", HTTP_AUTHORIZATION=f"Bearer {resolve_token}")
        self.assertEqual(resolved.status_code, 200)
        self.assertEqual(resolved.json()["candidate"]["register_number"], "REG-20401")
        replayed_resolution = self.client.get(f"/v1/candidates/{self.identity_reference}", HTTP_AUTHORIZATION=f"Bearer {resolve_token}")
        self.assertEqual(replayed_resolution.status_code, 409)
        self.assertEqual(IdentityAccessLog.objects.filter(identity_reference=self.identity_reference).count(), 2)
