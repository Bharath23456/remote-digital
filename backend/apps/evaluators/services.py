import hashlib
import hmac
import json
import math
import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth.models import User
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.allocation.models import Assignment
from apps.configuration.models import Subject
from apps.core.services import record_event
from apps.evaluators.models import Evaluator, EvaluatorAvailability, EvaluatorFaceTemplate, EvaluatorHistory, EvaluatorIdentityVerification, Expertise
from apps.evaluators.face_engine import FaceEngineError, extract_embedding
from apps.security.crypto import SecretDecryptionError, decrypt_secret, encrypt_secret
from apps.security.models import SecurityPolicy
from apps.tenancy.custom_fields import validate_custom_values
from apps.tenancy.models import Institution, Membership


class EvaluatorError(Exception):
    pass


class EvaluatorConflict(EvaluatorError):
    pass


STATUS_TRANSITIONS = {
    Evaluator.Status.PENDING: {Evaluator.Status.ACTIVE, Evaluator.Status.SUSPENDED},
    Evaluator.Status.ACTIVE: {Evaluator.Status.INACTIVE, Evaluator.Status.SUSPENDED, Evaluator.Status.RETIRED},
    Evaluator.Status.INACTIVE: {Evaluator.Status.ACTIVE, Evaluator.Status.RETIRED},
    Evaluator.Status.SUSPENDED: {Evaluator.Status.ACTIVE, Evaluator.Status.RETIRED},
    Evaluator.Status.RETIRED: set(),
}
ALLOWED_ACCESS_STATUSES = {Assignment.Status.ASSIGNED, Assignment.Status.ACCEPTED, Assignment.Status.IN_PROGRESS, Assignment.Status.REASSIGNED, Assignment.Status.SUBMITTED}


def _decimal_setting(name, default):
    try:
        return Decimal(str(getattr(settings, name, default)))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(str(default))


def _face_threshold():
    value = _decimal_setting("EVALUATOR_FACE_MATCH_THRESHOLD", "0.82")
    return min(max(value, Decimal("0.0000")), Decimal("1.0000")).quantize(Decimal("0.0001"))


def _face_min_quality():
    value = _decimal_setting("EVALUATOR_FACE_MIN_QUALITY", "0.45")
    return min(max(value, Decimal("0.00")), Decimal("1.00")).quantize(Decimal("0.01"))


def _face_ttl():
    minutes = getattr(settings, "EVALUATOR_FACE_VERIFICATION_TTL_MINUTES", 10)
    try:
        minutes = max(1, int(minutes))
    except (TypeError, ValueError):
        minutes = 10
    return timedelta(minutes=minutes)


def _digest(value):
    key = (getattr(settings, "APPLICATION_ENCRYPTION_KEY", "") or settings.SECRET_KEY).encode()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


def _quality_score(quality):
    candidates = [quality.get("score"), quality.get("quality_score"), quality.get("confidence")]
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            return min(max(Decimal(str(candidate)), Decimal("0.00")), Decimal("1.00")).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError):
            continue
    return Decimal("0.00")


def _normalise_capture(capture):
    try:
        embedding = extract_embedding(capture.get("image_base64", ""))
    except FaceEngineError as exc:
        raise EvaluatorError(str(exc)) from exc

    descriptor = json.dumps(embedding, separators=(",", ":"))
    quality = capture.get("quality") or {}
    if not isinstance(quality, dict):
        quality = {}

    try:
        face_count = int(capture.get("face_count", 1))
    except (TypeError, ValueError):
        face_count = 0

    return {
        "descriptor": descriptor,
        "embedding": embedding,
        "digest": _digest(descriptor),
        "face_count": face_count,
        "liveness_passed": bool(capture.get("liveness_passed")),
        "quality": quality,
        "quality_score": _quality_score(quality),
        "model_version": str(capture.get("model_version") or "opencv-sface-v1")[:64],
        "device_fingerprint": str(capture.get("device_fingerprint") or "")[:128],
    }


def _empty_capture(capture=None):
    capture = capture or {}
    return {
        "descriptor": "",
        "embedding": [],
        "digest": "",
        "face_count": 0,
        "liveness_passed": False,
        "quality": capture.get("quality") if isinstance(capture.get("quality"), dict) else {},
        "quality_score": Decimal("0.00"),
        "model_version": str(capture.get("model_version") or "opencv-sface-v1")[:64],
        "device_fingerprint": str(capture.get("device_fingerprint") or "")[:128],
    }


def _validate_capture(capture, *, purpose):
    if capture["face_count"] != 1:
        raise EvaluatorError("Exactly one live face must be visible")
    if not capture["liveness_passed"]:
        raise EvaluatorError("Liveness verification is required")
    if capture["quality_score"] < _face_min_quality():
        raise EvaluatorError("Face capture quality is too low")
    if purpose == "enroll" and capture["quality_score"] < max(_face_min_quality(), Decimal("0.60")):
        raise EvaluatorError("Enrollment requires a clearer live face capture")


def _template_payload(capture):
    return {
        "descriptor": capture["descriptor"],
        "embedding": capture["embedding"],
        "digest": capture["digest"],
        "model_version": capture["model_version"],
        "quality": capture["quality"],
        "quality_score": str(capture["quality_score"]),
        "enrolled_at": timezone.now().isoformat(),
    }


def _load_template_payload(template):
    try:
        return json.loads(decrypt_secret(template.encrypted_template))
    except (SecretDecryptionError, json.JSONDecodeError) as exc:
        raise EvaluatorError("Stored face template cannot be read") from exc


def _cosine_similarity(left, right):
    if not left or not right or len(left) != len(right):
        return None
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return None
    return Decimal(str((dot / (left_norm * right_norm) + 1) / 2)).quantize(Decimal("0.0001"))


def _compare_template(template, capture):
    stored = _load_template_payload(template)
    similarity = _cosine_similarity(stored.get("embedding") or [], capture["embedding"])
    if similarity is None:
        similarity = Decimal("1.0000") if hmac.compare_digest(template.template_digest, capture["digest"]) else Decimal("0.0000")
    return min(max(similarity, Decimal("0.0000")), Decimal("1.0000")).quantize(Decimal("0.0001"))


def _face_status_payload(evaluator):
    try:
        template = evaluator.face_template
    except EvaluatorFaceTemplate.DoesNotExist:
        template = None
    if not template:
        return {"enrolled": False, "status": "not_enrolled", "model_version": "", "enrolled_at": None, "last_verified_at": None, "threshold": str(_face_threshold())}
    return {
        "enrolled": template.status == EvaluatorFaceTemplate.Status.ACTIVE,
        "status": template.status,
        "model_version": template.model_version,
        "enrolled_at": template.enrolled_at.isoformat(),
        "last_verified_at": template.last_verified_at.isoformat() if template.last_verified_at else None,
        "threshold": str(template.threshold),
        "quality_score": str(template.quality_score),
        "version": template.version,
    }


def snapshot(evaluator):
    face = _face_status_payload(evaluator)
    return {
        "evaluator_code": evaluator.evaluator_code,
        "display_name": evaluator.display_name,
        "email": "System managed" if evaluator.is_system_ai else evaluator.email,
        "mobile": evaluator.mobile,
        "employee_id": evaluator.employee_id,
        "user_id": evaluator.user_id,
        "login_enabled": bool(evaluator.user_id),
        "is_system_ai": evaluator.is_system_ai,
        "institution_name": evaluator.institution_name,
        "department": evaluator.department,
        "designation": evaluator.designation,
        "qualification": evaluator.qualification,
        "employment_type": evaluator.employment_type,
        "employment_details": evaluator.employment_details,
        "custom_fields": evaluator.custom_fields,
        "years_experience": evaluator.years_experience,
        "grade": evaluator.grade,
        "status": evaluator.status,
        "daily_capacity": evaluator.daily_capacity,
        "available_from": evaluator.available_from.isoformat() if evaluator.available_from else None,
        "available_to": evaluator.available_to.isoformat() if evaluator.available_to else None,
        "face_status": face["status"],
        "face_enrolled": face["enrolled"],
        "face_enrolled_at": face["enrolled_at"],
        "face_model_version": face["model_version"],
        "last_identity_verified_at": face["last_verified_at"],
        "version": evaluator.version,
    }


def evaluator_catalog(tenant_id):
    profiles = []
    evaluators = Evaluator.objects.filter(tenant_id=tenant_id)
    policy = SecurityPolicy.objects.filter(tenant_id=tenant_id).first()
    ai_available = False
    if policy and policy.ai_evaluation_mode == SecurityPolicy.AIEvaluationMode.AUTONOMOUS:
        from apps.ai_evaluation.services import provider_status

        ai_available = provider_status(tenant_id, policy.ai_model_name)["available"]
    if not ai_available:
        evaluators = evaluators.exclude(is_system_ai=True)
    for evaluator in evaluators.select_related("face_template").prefetch_related("expertise__subject", "availability_periods", "history").order_by("display_name"):
        profiles.append({
            "id": str(evaluator.id),
            **snapshot(evaluator),
            "mobile": evaluator.mobile,
            "employee_id": evaluator.employee_id,
            "employment_type": evaluator.employment_type,
            "employment_details": evaluator.employment_details,
            "available_from": evaluator.available_from.isoformat() if evaluator.available_from else None,
            "available_to": evaluator.available_to.isoformat() if evaluator.available_to else None,
            "expertise": [{"id": str(item.id), "subject_id": str(item.subject_id), "subject_code": item.subject.code, "subject_name": item.subject.name, "level": item.level, "years_experience": item.years_experience, "verified": item.verified} for item in evaluator.expertise.all()],
            "availability": [{"id": str(item.id), "starts_on": item.starts_on.isoformat(), "ends_on": item.ends_on.isoformat(), "daily_capacity": item.daily_capacity, "notes": item.notes} for item in evaluator.availability_periods.all()],
        })
    return profiles


def face_status(*, tenant_id, evaluator_id):
    evaluator = Evaluator.objects.select_related("face_template").filter(id=evaluator_id, tenant_id=tenant_id).first()
    if not evaluator:
        raise EvaluatorError("Evaluator was not found")
    return {"evaluator_id": str(evaluator.id), **_face_status_payload(evaluator)}


@transaction.atomic
def enroll_face_template(*, tenant_id, actor_id, evaluator_id, capture):
    evaluator = Evaluator.objects.select_for_update().filter(id=evaluator_id, tenant_id=tenant_id).first()
    if not evaluator:
        raise EvaluatorError("Evaluator was not found")
    normalised = _normalise_capture(capture)
    _validate_capture(normalised, purpose="enroll")
    threshold = _face_threshold()
    encrypted = encrypt_secret(json.dumps(_template_payload(normalised), sort_keys=True, separators=(",", ":")))
    template, created = EvaluatorFaceTemplate.objects.select_for_update().update_or_create(
        tenant_id=tenant_id,
        evaluator=evaluator,
        defaults={
            "encrypted_template": encrypted,
            "template_digest": normalised["digest"],
            "model_version": normalised["model_version"],
            "threshold": threshold,
            "quality_score": normalised["quality_score"],
            "liveness_reference": {"face_count": normalised["face_count"], "quality": normalised["quality"], "liveness_passed": normalised["liveness_passed"]},
            "enrolled_by_id": actor_id,
            "status": EvaluatorFaceTemplate.Status.ACTIVE,
        },
    )
    if not created:
        template.version += 1
        template.save(update_fields=["version", "updated_at"])
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="evaluator.face.enrolled", aggregate="EvaluatorFaceTemplate", aggregate_id=template.id, payload={"evaluator_id": str(evaluator.id), "model_version": template.model_version, "quality_score": str(template.quality_score), "threshold": str(template.threshold), "reenrolled": not created})
    return template


@transaction.atomic
def verify_face_template(*, tenant_id, actor_id, evaluator_id, capture, access_session=None, assignment=None, ip_address=None):
    evaluator = Evaluator.objects.select_for_update().filter(id=evaluator_id, tenant_id=tenant_id).first()
    if not evaluator:
        raise EvaluatorError("Evaluator was not found")
    normalised = _empty_capture(capture)
    failure = ""
    similarity = Decimal("0.0000")
    verified = False
    liveness = False
    authorized = evaluator.status == Evaluator.Status.ACTIVE
    template = EvaluatorFaceTemplate.objects.select_for_update().filter(tenant_id=tenant_id, evaluator=evaluator).first()
    threshold = _face_threshold()
    try:
        normalised = _normalise_capture(capture)
        liveness = normalised["liveness_passed"] and normalised["face_count"] == 1
        _validate_capture(normalised, purpose="verify")
        if not template or template.status != EvaluatorFaceTemplate.Status.ACTIVE:
            failure = "face_not_enrolled"
        elif not authorized:
            failure = "evaluator_not_active"
        else:
            threshold = template.threshold
            similarity = _compare_template(template, normalised)
            verified = similarity >= threshold
            if not verified:
                failure = "face_mismatch"
    except EvaluatorError as exc:
        failure = str(exc)
    now = timezone.now()
    attempts = EvaluatorIdentityVerification.objects.filter(tenant_id=tenant_id, evaluator=evaluator, created_at__date=now.date()).count() + 1
    item = EvaluatorIdentityVerification.objects.create(
        tenant_id=tenant_id,
        evaluator=evaluator,
        assignment=assignment,
        access_session_id=getattr(access_session, "id", None),
        verified=verified,
        liveness_verified=liveness,
        authorized=authorized,
        access_granted=verified and authorized,
        similarity_score=similarity,
        threshold=threshold,
        failure_reason=failure[:80],
        model_version=normalised["model_version"],
        probe_digest=normalised["digest"],
        device_fingerprint=normalised["device_fingerprint"],
        ip_address=ip_address,
        evidence={"quality": normalised["quality"], "quality_score": str(normalised["quality_score"]), "face_count": normalised["face_count"], "template_model_version": template.model_version if template else ""},
        expires_at=now + _face_ttl() if verified and authorized else None,
        attempt_number=attempts,
    )
    if verified and template:
        template.last_verified_at = now
        template.save(update_fields=["last_verified_at", "updated_at"])
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="evaluator.face.verified" if item.access_granted else "evaluator.face.denied", aggregate="EvaluatorIdentityVerification", aggregate_id=item.id, payload={"evaluator_id": str(evaluator.id), "assignment_id": str(assignment.id) if assignment else None, "verified": item.verified, "liveness_verified": item.liveness_verified, "authorized": item.authorized, "access_granted": item.access_granted, "failure_reason": item.failure_reason, "similarity_score": str(item.similarity_score), "threshold": str(item.threshold), "expires_at": item.expires_at.isoformat() if item.expires_at else None})
    return item


def verify_evaluator_access(*, tenant_id, actor_id, evaluator, assignment, access_session, capture, ip_address=None):
    if not assignment or assignment.tenant_id != tenant_id or assignment.evaluator_id != evaluator.id:
        raise EvaluatorError("Evaluator assignment was not found")
    if assignment.status not in ALLOWED_ACCESS_STATUSES:
        raise EvaluatorConflict("This assignment is not open for evaluator access")
    item = verify_face_template(tenant_id=tenant_id, actor_id=actor_id, evaluator_id=evaluator.id, capture=capture, access_session=access_session, assignment=assignment, ip_address=ip_address)
    if not item.access_granted:
        raise EvaluatorConflict(item.failure_reason or "Identity verification failed")
    return item


def require_recent_identity_verification(*, tenant_id, evaluator, assignment, access_session):
    now = timezone.now()
    item = EvaluatorIdentityVerification.objects.filter(
        tenant_id=tenant_id,
        evaluator=evaluator,
        assignment=assignment,
        access_session_id=getattr(access_session, "id", None),
        access_granted=True,
        expires_at__gt=now,
    ).order_by("-created_at").first()
    if not item:
        raise EvaluatorError("Identity verification is required before entering evaluation")
    return item


@transaction.atomic
def create_evaluator(*, tenant_id, actor_id, values):
    create_login = values.pop("create_login", True)
    subject_ids = list(dict.fromkeys(values.pop("subject_ids", [])))
    if len(subject_ids) > 100:
        raise EvaluatorError("Select no more than 100 subjects")
    subjects = list(Subject.objects.filter(tenant_id=tenant_id, id__in=subject_ids))
    if len(subjects) != len(subject_ids):
        raise EvaluatorError("Selected subjects must belong to this university")
    values["email"] = values.get("email", "").strip().lower()
    values["custom_fields"] = validate_custom_values(tenant_id=tenant_id, form_key="evaluator_profile", values=values.get("custom_fields"))
    temporary_password = ""
    user = None
    if create_login:
        if not values["email"]:
            raise EvaluatorError("An institutional email is required to create evaluator login access")
        institution = Institution.objects.filter(tenant_id=tenant_id, parent__isnull=True, is_active=True).first()
        if not institution:
            raise EvaluatorError("University root institution was not found")
        user = User.objects.filter(username__iexact=values["email"]).first()
        created = user is None
        if created:
            name_parts = values["display_name"].strip().split(maxsplit=1)
            user = User(username=values["email"], email=values["email"], first_name=name_parts[0], last_name=name_parts[1] if len(name_parts) > 1 else "")
        if created or not user.has_usable_password():
            temporary_password = secrets.token_urlsafe(15)
            user.set_password(temporary_password)
            user.save()
        membership = Membership.objects.filter(user=user, institution__tenant_id=tenant_id).first()
        if membership and membership.role != Membership.Role.EVALUATOR:
            raise EvaluatorConflict("This email already belongs to a non-evaluator university account")
        if not membership:
            Membership.objects.create(
                user=user,
                institution=institution,
                role=Membership.Role.EVALUATOR,
                permissions=[],
                enabled_modules=["evaluation"],
                must_change_password=bool(temporary_password),
            )
        elif not membership.is_active or membership.enabled_modules != ["evaluation"] or (temporary_password and not membership.must_change_password):
            membership.is_active = True
            membership.enabled_modules = ["evaluation"]
            membership.must_change_password = membership.must_change_password or bool(temporary_password)
            membership.save(update_fields=["is_active", "enabled_modules", "must_change_password", "updated_at"])
        values["user"] = user
    if values["daily_capacity"] <= 0:
        raise EvaluatorError("Daily capacity must be greater than zero")
    if values.get("available_from") and values.get("available_to") and values["available_from"] > values["available_to"]:
        raise EvaluatorError("Availability end date must not precede its start date")
    if values.get("years_experience", 0) < 0:
        raise EvaluatorError("Experience must not be negative")
    if values.get("grade") not in {value for value, _ in Evaluator.Grade.choices}:
        raise EvaluatorError("Unsupported evaluator role")
    try:
        evaluator = Evaluator.objects.create(tenant_id=tenant_id, **values)
    except IntegrityError as exc:
        raise EvaluatorConflict("Evaluator code already exists") from exc
    Expertise.objects.bulk_create([
        Expertise(tenant_id=tenant_id, evaluator=evaluator, subject=subject, level=3, years_experience=evaluator.years_experience)
        for subject in subjects
    ])
    EvaluatorHistory.objects.create(tenant_id=tenant_id, evaluator=evaluator, action="created", to_status=evaluator.status, to_grade=evaluator.grade, actor_id=actor_id, snapshot=snapshot(evaluator))
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="evaluator.created", aggregate="Evaluator", aggregate_id=evaluator.id, payload={"code": evaluator.evaluator_code, "subject_ids": [str(subject.id) for subject in subjects]})
    return evaluator, temporary_password


@transaction.atomic
def update_evaluator(*, tenant_id, actor_id, evaluator_id, version, changes):
    evaluator = Evaluator.objects.select_for_update().filter(id=evaluator_id, tenant_id=tenant_id).first()
    if not evaluator:
        raise EvaluatorError("Evaluator was not found")
    if evaluator.version != version:
        raise EvaluatorConflict("Evaluator was changed by another user")
    if changes.get("daily_capacity") is not None and changes["daily_capacity"] <= 0:
        raise EvaluatorError("Daily capacity must be greater than zero")
    if changes.get("years_experience") is not None and changes["years_experience"] < 0:
        raise EvaluatorError("Experience must not be negative")
    if "custom_fields" in changes:
        changes["custom_fields"] = validate_custom_values(tenant_id=tenant_id, form_key="evaluator_profile", values=changes["custom_fields"])
    if changes.get("email") and evaluator.user_id:
        email = changes["email"].strip().lower()
        if User.objects.filter(username__iexact=email).exclude(id=evaluator.user_id).exists():
            raise EvaluatorConflict("Another login already uses this email address")
        evaluator.user.username = email
        evaluator.user.email = email
        evaluator.user.save(update_fields=["username", "email"])
        changes["email"] = email
    starts_on = changes.get("available_from", evaluator.available_from)
    ends_on = changes.get("available_to", evaluator.available_to)
    if starts_on and ends_on and starts_on > ends_on:
        raise EvaluatorError("Availability end date must not precede its start date")
    for field, value in changes.items():
        setattr(evaluator, field, value)
    evaluator.version += 1
    evaluator.save()
    EvaluatorHistory.objects.create(tenant_id=tenant_id, evaluator=evaluator, action="updated", actor_id=actor_id, snapshot=snapshot(evaluator))
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="evaluator.updated", aggregate="Evaluator", aggregate_id=evaluator.id, payload={"fields": sorted(changes), "version": evaluator.version})
    return evaluator


@transaction.atomic
def change_lifecycle(*, tenant_id, actor_id, evaluator_id, version, status=None, grade=None, reason=""):
    evaluator = Evaluator.objects.select_for_update().filter(id=evaluator_id, tenant_id=tenant_id).first()
    if not evaluator:
        raise EvaluatorError("Evaluator was not found")
    if evaluator.version != version:
        raise EvaluatorConflict("Evaluator was changed by another user")
    previous_status, previous_grade = evaluator.status, evaluator.grade
    if status:
        if status not in STATUS_TRANSITIONS.get(evaluator.status, set()):
            raise EvaluatorConflict(f"Transition from {evaluator.status} to {status} is not allowed")
        evaluator.status = status
    if grade:
        if grade not in {value for value, _ in Evaluator.Grade.choices}:
            raise EvaluatorError("Unsupported evaluator role")
        evaluator.grade = grade
    if not status and not grade:
        raise EvaluatorError("A status or role change is required")
    evaluator.version += 1
    evaluator.save(update_fields=["status", "grade", "version", "updated_at"])
    EvaluatorHistory.objects.create(tenant_id=tenant_id, evaluator=evaluator, action="lifecycle_changed", from_status=previous_status, to_status=evaluator.status, from_grade=previous_grade, to_grade=evaluator.grade, actor_id=actor_id, snapshot=snapshot(evaluator), reason=reason)
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="evaluator.lifecycle.changed", aggregate="Evaluator", aggregate_id=evaluator.id, payload={"from_status": previous_status, "to_status": evaluator.status, "from_grade": previous_grade, "to_grade": evaluator.grade, "reason": reason})
    return evaluator


@transaction.atomic
def add_expertise(*, tenant_id, actor_id, evaluator_id, values):
    evaluator = Evaluator.objects.select_for_update().filter(id=evaluator_id, tenant_id=tenant_id).first()
    subject = Subject.objects.filter(id=values.pop("subject_id"), tenant_id=tenant_id).first()
    if not evaluator or not subject:
        raise EvaluatorError("Evaluator or subject was not found")
    if not 1 <= values["level"] <= 5:
        raise EvaluatorError("Expertise level must be between one and five")
    try:
        expertise = Expertise.objects.create(tenant_id=tenant_id, evaluator=evaluator, subject=subject, **values)
    except IntegrityError as exc:
        raise EvaluatorConflict("Expertise already exists for this subject") from exc
    evaluator.version += 1
    evaluator.save(update_fields=["version", "updated_at"])
    EvaluatorHistory.objects.create(tenant_id=tenant_id, evaluator=evaluator, action="expertise_added", actor_id=actor_id, snapshot=snapshot(evaluator), reason=subject.code)
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="evaluator.expertise.added", aggregate="Expertise", aggregate_id=expertise.id, payload={"evaluator_id": str(evaluator.id), "subject_id": str(subject.id)})
    return expertise, evaluator


@transaction.atomic
def add_availability(*, tenant_id, actor_id, evaluator_id, values):
    evaluator = Evaluator.objects.select_for_update().filter(id=evaluator_id, tenant_id=tenant_id).first()
    if not evaluator:
        raise EvaluatorError("Evaluator was not found")
    if values["starts_on"] > values["ends_on"] or values["daily_capacity"] <= 0:
        raise EvaluatorError("Availability dates or capacity are invalid")
    availability = EvaluatorAvailability.objects.create(tenant_id=tenant_id, evaluator=evaluator, **values)
    evaluator.version += 1
    evaluator.save(update_fields=["version", "updated_at"])
    EvaluatorHistory.objects.create(tenant_id=tenant_id, evaluator=evaluator, action="availability_added", actor_id=actor_id, snapshot=snapshot(evaluator))
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="evaluator.availability.added", aggregate="EvaluatorAvailability", aggregate_id=availability.id, payload={"evaluator_id": str(evaluator.id)})
    return availability, evaluator


def evaluator_history(tenant_id, evaluator_id):
    return list(EvaluatorHistory.objects.filter(tenant_id=tenant_id, evaluator_id=evaluator_id).values("id", "action", "from_status", "to_status", "from_grade", "to_grade", "actor_id", "snapshot", "reason", "created_at"))
