import base64
import hashlib
import hmac
import json
import time
import uuid
from io import BytesIO

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from PIL import Image

from .models import CandidateIdentity, IdentityAccessLog


def token(action, tenant_id, identity_reference, script_id, **extra):
    claims = {"action": action, "tenant_id": str(tenant_id), "identity_reference": str(identity_reference), "script_id": str(script_id), "actor_id": "91", "purpose": "Authorized test", "request_id": "", "session_id": "", "jti": uuid.uuid4().hex, "exp": int(time.time()) + 300}
    claims.update(extra)
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

    def test_automated_omr_accepts_usn_without_inventing_candidate_name(self):
        create_token = token("identity.create", self.tenant_id, self.identity_reference, self.script_id, purpose="Automated OMR intake", institution_name="Northbridge University")
        response = self.client.post("/v1/candidates", data=json.dumps({"candidate_name": "", "register_number": "4UB22CS032", "usn": "4UB22CS032"}), content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {create_token}")
        self.assertEqual(response.status_code, 201)
        stored = CandidateIdentity.objects.get(identity_reference=self.identity_reference)
        self.assertNotIn("4UB22CS032", stored.pii_ciphertext)
        retry_token = token("identity.create", self.tenant_id, self.identity_reference, self.script_id, purpose="Automated OMR intake", institution_name="Northbridge University")
        retried = self.client.post("/v1/candidates", data=json.dumps({"candidate_name": "", "register_number": "4UB22CS032", "usn": "4UB22CS032"}), content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {retry_token}")
        self.assertEqual(retried.status_code, 200)
        self.assertIn("receipt", retried.json())
        wrong_token = token("identity.create", self.tenant_id, self.identity_reference, self.script_id, purpose="Automated OMR intake", institution_name="Northbridge University")
        wrong = self.client.post("/v1/candidates", data=json.dumps({"candidate_name": "", "register_number": "4UB22CS033", "usn": "4UB22CS033"}), content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {wrong_token}")
        self.assertEqual(wrong.status_code, 409)

    def test_optional_images_are_sanitized_encrypted_and_only_returned_on_resolution(self):
        raw = BytesIO()
        Image.new("RGB", (60, 40), "red").save(raw, format="PNG")
        create_token = token("identity.create", self.tenant_id, self.identity_reference, self.script_id, institution_name="Northbridge University", college_name="Engineering College")
        response = self.client.post("/v1/candidates", data={
            "candidate_name": "Maya Joseph", "register_number": "REG-20401", "institution": "Forged University", "college": "Forged College",
            "signature_image": SimpleUploadedFile("signature.png", raw.getvalue(), content_type="image/png"),
        }, HTTP_AUTHORIZATION=f"Bearer {create_token}")
        self.assertEqual(response.status_code, 201)
        stored = CandidateIdentity.objects.get(identity_reference=self.identity_reference)
        self.assertTrue(stored.signature_image_ciphertext)
        self.assertFalse(stored.photo_image_ciphertext)
        self.assertNotIn("Maya Joseph", stored.pii_ciphertext)
        resolved = self.client.get(f"/v1/candidates/{self.identity_reference}", HTTP_AUTHORIZATION=f"Bearer {token('identity.resolve', self.tenant_id, self.identity_reference, self.script_id)}")
        self.assertEqual(resolved.status_code, 200)
        candidate = resolved.json()["candidate"]
        self.assertEqual(candidate["institution"], "Northbridge University")
        self.assertEqual(candidate["college"], "Engineering College")
        self.assertTrue(candidate["signature_image"].startswith("data:image/webp;base64,"))
        self.assertNotIn("photo_image", candidate)

    def test_rejects_invalid_image_without_consuming_authorization(self):
        create_token = token("identity.create", self.tenant_id, self.identity_reference, self.script_id)
        response = self.client.post("/v1/candidates", data={
            "candidate_name": "Maya Joseph", "register_number": "REG-20401", "institution": "Northbridge University",
            "photo_image": SimpleUploadedFile("photo.png", b"not an image", content_type="image/png"),
        }, HTTP_AUTHORIZATION=f"Bearer {create_token}")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(CandidateIdentity.objects.filter(identity_reference=self.identity_reference).exists())

    def test_multipart_without_images_is_valid(self):
        create_token = token("identity.create", self.tenant_id, self.identity_reference, self.script_id, institution_name="Northbridge University")
        response = self.client.post("/v1/candidates", data={"candidate_name": "Maya Joseph", "register_number": "REG-20401"}, HTTP_AUTHORIZATION=f"Bearer {create_token}")
        self.assertEqual(response.status_code, 201)
        stored = CandidateIdentity.objects.get(identity_reference=self.identity_reference)
        self.assertFalse(stored.signature_image_ciphertext)
        self.assertFalse(stored.photo_image_ciphertext)
