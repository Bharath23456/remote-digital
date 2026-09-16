import base64
import hashlib
import hmac
import json
import time

from django.conf import settings


class AuthorizationError(ValueError):
    pass


def _decode(value):
    return base64.urlsafe_b64decode(value + "=" * ((4 - len(value) % 4) % 4))


def verify_token(value, expected_action):
    try:
        payload_part, signature = value.split(".", 1)
        expected = hmac.new(settings.IDENTITY_AUTHORIZATION_KEY.encode(), payload_part.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise AuthorizationError
        claims = json.loads(_decode(payload_part))
        if claims.get("action") != expected_action or int(claims.get("exp", 0)) < int(time.time()):
            raise AuthorizationError
        return claims
    except (ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AuthorizationError("Invalid or expired identity authorization") from exc


def issue_receipt(claims):
    receipt = {
        "action": "identity.stored",
        "tenant_id": claims["tenant_id"],
        "identity_reference": claims["identity_reference"],
        "script_id": claims["script_id"],
        "jti": claims["jti"],
        "exp": int(time.time()) + 300,
    }
    encoded = base64.urlsafe_b64encode(json.dumps(receipt, separators=(",", ":"), sort_keys=True).encode()).decode().rstrip("=")
    signature = hmac.new(settings.IDENTITY_AUTHORIZATION_KEY.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"
