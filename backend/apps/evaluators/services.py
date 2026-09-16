import secrets

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction

from apps.configuration.models import Subject
from apps.core.services import record_event
from apps.evaluators.models import Evaluator, EvaluatorAvailability, EvaluatorHistory, Expertise
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


def snapshot(evaluator):
    return {
        "evaluator_code": evaluator.evaluator_code,
        "display_name": evaluator.display_name,
        "email": evaluator.email,
        "mobile": evaluator.mobile,
        "employee_id": evaluator.employee_id,
        "user_id": evaluator.user_id,
        "login_enabled": bool(evaluator.user_id),
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
        "version": evaluator.version,
    }


def evaluator_catalog(tenant_id):
    profiles = []
    for evaluator in Evaluator.objects.filter(tenant_id=tenant_id).prefetch_related("expertise__subject", "availability_periods", "history").order_by("display_name"):
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


@transaction.atomic
def create_evaluator(*, tenant_id, actor_id, values):
    create_login = values.pop("create_login", True)
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
    EvaluatorHistory.objects.create(tenant_id=tenant_id, evaluator=evaluator, action="created", to_status=evaluator.status, to_grade=evaluator.grade, actor_id=actor_id, snapshot=snapshot(evaluator))
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="evaluator.created", aggregate="Evaluator", aggregate_id=evaluator.id, payload={"code": evaluator.evaluator_code})
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
