import base64
import hashlib
import json
from io import BytesIO

from cryptography.fernet import Fernet
from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from PIL import Image, ImageOps, UnidentifiedImageError

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


def _candidate_payload(request):
    if request.content_type == "multipart/form-data":
        return request.POST.dict()
    return _json(request)


def _encrypted_image(upload):
    if not upload:
        return ""
    if upload.size < 1 or upload.size > 5_000_000:
        raise ValueError("Identity images must be between 1 byte and 5 MB")
    raw = upload.read(5_000_001)
    try:
        source = Image.open(BytesIO(raw))
        if source.format not in {"PNG", "JPEG", "WEBP"} or source.width > 5000 or source.height > 5000 or source.width < 8 or source.height < 8:
            raise ValueError("Use a JPEG, PNG or WebP image with valid dimensions")
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((1600, 1600))
        output = BytesIO()
        image.save(output, format="WEBP", quality=85, method=4)
        if output.tell() > 1_500_000:
            raise ValueError("Identity image is too large after processing")
        return _cipher().encrypt(output.getvalue()).decode()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Uploaded identity image is invalid") from exc


def health(request):
    return JsonResponse({"status": "ok", "service": "identity-service"})


@csrf_exempt
def create_candidate(request):
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed"}, status=405)
    try:
        claims = verify_token(_bearer(request), "identity.create")
        body = _candidate_payload(request)
        permitted = {"candidate_name", "register_number", "usn", "college", "institution", "signature_reference", "photo_reference"}
        pii = {key: body.get(key, "") for key in permitted}
        if "institution_name" in claims:
            pii["institution"] = claims["institution_name"]
            pii["college"] = claims.get("college_name", "")
        if any(not str(pii[key]).strip() for key in ("candidate_name", "register_number", "institution")):
            return JsonResponse({"detail": "Candidate name, register number and institution are required"}, status=422)
        signature_image = _encrypted_image(request.FILES.get("signature_image"))
        photo_image = _encrypted_image(request.FILES.get("photo_image"))
        with transaction.atomic():
            ConsumedAuthorization.objects.create(jti=claims["jti"])
            identity = CandidateIdentity.objects.create(
                tenant_id=claims["tenant_id"],
                identity_reference=claims["identity_reference"],
                script_id=claims["script_id"],
                session_id=claims.get("session_id") or None,
                pii_ciphertext=_cipher().encrypt(json.dumps(pii, separators=(",", ":")).encode()).decode(),
                signature_image_ciphertext=signature_image,
                photo_image_ciphertext=photo_image,
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
        if identity.signature_image_ciphertext:
            pii["signature_image"] = "data:image/webp;base64," + base64.b64encode(_cipher().decrypt(identity.signature_image_ciphertext.encode())).decode()
        if identity.photo_image_ciphertext:
            pii["photo_image"] = "data:image/webp;base64," + base64.b64encode(_cipher().decrypt(identity.photo_image_ciphertext.encode())).decode()
        response = JsonResponse({"identity_reference": str(identity.identity_reference), "candidate": pii})
        response["Cache-Control"] = "private, no-store"
        return response
    except IntegrityError:
        return JsonResponse({"detail": "Identity authorization has already been consumed"}, status=409)
    except AuthorizationError as exc:
        return JsonResponse({"detail": str(exc)}, status=401)
