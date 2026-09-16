import base64
import hashlib
import hmac
import json
import secrets
import time

from django.conf import settings


def issue_identity_token(*, action, tenant_id, identity_reference, script_id, actor_id, purpose, request_id="", session_id="", ttl_seconds=300, institution_name=None, college_name=None):
    claims = {
        "action": action,
        "tenant_id": str(tenant_id),
        "identity_reference": str(identity_reference),
        "script_id": str(script_id),
        "actor_id": str(actor_id),
        "purpose": purpose,
        "request_id": str(request_id),
        "session_id": str(session_id),
        "jti": secrets.token_hex(24),
        "exp": int(time.time()) + min(max(ttl_seconds, 1), 300),
    }
    if institution_name is not None:
        claims["institution_name"] = institution_name
        claims["college_name"] = college_name or ""
    encoded = base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode()).decode().rstrip("=")
    signature = hmac.new(settings.IDENTITY_AUTHORIZATION_KEY.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}", claims["exp"]
