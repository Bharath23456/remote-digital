import base64
import hashlib
import json

from cryptography.fernet import Fernet
from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .models import CandidateIdentity, ConsumedAuthorization, IdentityAccessLog
from .tokens import AuthorizationError, issue_receipt, verify_token


def _cipher():
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.IDENTITY_ENCRYPTION_KEY.encode()).digest())
    return Fernet(key)


def _bearer(request):
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise AuthorizationError("Missing identity authorization")
    return header[7:]


def _json(request):
    try:
        return json.loads(request.body or b"{}")
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON body") from exc


def health(request):
    return JsonResponse({"status": "ok", "service": "identity-service"})


@csrf_exempt
def create_candidate(request):
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed"}, status=405)
    try:
        claims = verify_token(_bearer(request), "identity.create")
        body = _json(request)
        required = {"candidate_name", "register_number", "institution"}
        if not required.issubset(body) or any(not str(body[key]).strip() for key in required):
            return JsonResponse({"detail": "Candidate name, register number and institution are required"}, status=422)
        permitted = {"candidate_name", "register_number", "usn", "college", "institution", "signature_reference", "photo_reference"}
        pii = {key: body.get(key, "") for key in permitted}
        with transaction.atomic():
            ConsumedAuthorization.objects.create(jti=claims["jti"])
            identity = CandidateIdentity.objects.create(
                tenant_id=claims["tenant_id"],
                identity_reference=claims["identity_reference"],
                script_id=claims["script_id"],
                session_id=claims.get("session_id") or None,
                pii_ciphertext=_cipher().encrypt(json.dumps(pii, separators=(",", ":")).encode()).decode(),
                register_number_hash=hashlib.sha256(str(pii["register_number"]).strip().upper().encode()).hexdigest(),
            )
            IdentityAccessLog.objects.create(tenant_id=identity.tenant_id, identity_reference=identity.identity_reference, action="create", authorization_id=claims["jti"], actor_id=claims["actor_id"], purpose=claims.get("purpose", "script registration"))
        return JsonResponse({"identity_reference": str(identity.identity_reference), "stored": True, "receipt": issue_receipt(claims)}, status=201)
    except IntegrityError:
        return JsonResponse({"detail": "Identity authorization was already used or the script is already linked"}, status=409)
    except (AuthorizationError, ValueError) as exc:
        return JsonResponse({"detail": str(exc)}, status=401 if isinstance(exc, AuthorizationError) else 400)


@csrf_exempt
def resolve_candidate(request, identity_reference):
    if request.method != "GET":
        return JsonResponse({"detail": "Method not allowed"}, status=405)
    try:
        claims = verify_token(_bearer(request), "identity.resolve")
        if claims.get("identity_reference") != str(identity_reference):
            raise AuthorizationError("Authorization does not match this identity")
        identity = CandidateIdentity.objects.filter(identity_reference=identity_reference, tenant_id=claims["tenant_id"]).first()
        if not identity:
            return JsonResponse({"detail": "Identity not found"}, status=404)
        with transaction.atomic():
            ConsumedAuthorization.objects.create(jti=claims["jti"])
            IdentityAccessLog.objects.create(tenant_id=identity.tenant_id, identity_reference=identity.identity_reference, action="resolve", authorization_id=claims["jti"], actor_id=claims["actor_id"], purpose=claims["purpose"])
        pii = json.loads(_cipher().decrypt(identity.pii_ciphertext.encode()))
        return JsonResponse({"identity_reference": str(identity.identity_reference), "candidate": pii})
    except IntegrityError:
        return JsonResponse({"detail": "Identity authorization has already been consumed"}, status=409)
    except AuthorizationError as exc:
        return JsonResponse({"detail": str(exc)}, status=401)
