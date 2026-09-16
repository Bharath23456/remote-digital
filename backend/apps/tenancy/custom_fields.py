from datetime import date

from django.core.validators import validate_slug
from django.core.exceptions import ValidationError
from django.db import transaction
from ninja.errors import HttpError

from apps.core.services import record_event

from .models import CustomFieldDefinition, CustomFieldRecord


FORM_CATALOG = {
    "user_access": "User access",
    "evaluator_profile": "Evaluator profile",
    "institution": "Institution",
    "exam_session": "Exam session",
    "paper": "Paper configuration",
    "dispatch": "Script dispatch",
    "script": "Answer script",
    "allocation": "Evaluator allocation",
    "rubric": "Marking scheme",
    "notification": "Notification",
}


def serialize_definition(item):
    return {
        "id": str(item.id),
        "tenant_id": str(item.tenant_id),
        "form_key": item.form_key,
        "key": item.key,
        "label": item.label,
        "field_type": item.field_type,
        "required": item.required,
        "options": item.options,
        "placeholder": item.placeholder,
        "help_text": item.help_text,
        "sort_order": item.sort_order,
        "is_active": item.is_active,
        "version": item.version,
    }


def validate_definition(values):
    form_key = values.get("form_key", "")
    key = values.get("key", "")
    if form_key not in FORM_CATALOG:
        raise HttpError(422, "Unsupported form")
    try:
        validate_slug(key)
    except ValidationError as exc:
        raise HttpError(422, "Field key must contain only letters, numbers, underscores or hyphens") from exc
    if not key or not values.get("label", "").strip():
        raise HttpError(422, "Field key and label are required")
    if values.get("field_type") not in CustomFieldDefinition.FieldType.values:
        raise HttpError(422, "Unsupported field type")
    options = [str(value).strip() for value in values.get("options", []) if str(value).strip()]
    if values.get("field_type") == CustomFieldDefinition.FieldType.SELECT and not options:
        raise HttpError(422, "Select fields require at least one option")
    values["options"] = options
    return values


def validate_custom_values(*, tenant_id, form_key, values):
    values = values or {}
    if not isinstance(values, dict):
        raise HttpError(422, "Custom fields must be an object")
    definitions = list(CustomFieldDefinition.objects.filter(tenant_id=tenant_id, form_key=form_key, is_active=True))
    allowed = {item.key: item for item in definitions}
    unknown = set(values) - set(allowed)
    if unknown:
        raise HttpError(422, f"Unknown custom fields: {', '.join(sorted(unknown))}")
    cleaned = {}
    for key, definition in allowed.items():
        value = values.get(key)
        missing = value is None or value == ""
        if definition.required and missing:
            raise HttpError(422, f"{definition.label} is required")
        if missing:
            continue
        if definition.field_type == CustomFieldDefinition.FieldType.NUMBER:
            try:
                value = float(value)
            except (TypeError, ValueError) as exc:
                raise HttpError(422, f"{definition.label} must be a number") from exc
        elif definition.field_type == CustomFieldDefinition.FieldType.DATE:
            try:
                value = date.fromisoformat(str(value)).isoformat()
            except ValueError as exc:
                raise HttpError(422, f"{definition.label} must be a valid date") from exc
        elif definition.field_type == CustomFieldDefinition.FieldType.CHECKBOX:
            if not isinstance(value, bool):
                raise HttpError(422, f"{definition.label} must be true or false")
        elif definition.field_type == CustomFieldDefinition.FieldType.SELECT:
            value = str(value)
            if value not in definition.options:
                raise HttpError(422, f"{definition.label} has an unsupported option")
        else:
            value = str(value).strip()
        cleaned[key] = value
    return cleaned


def persist_custom_values(*, tenant_id, actor_id, form_key, record_id, values):
    cleaned = validate_custom_values(tenant_id=tenant_id, form_key=form_key, values=values)
    if not cleaned:
        return cleaned
    with transaction.atomic():
        item, created = CustomFieldRecord.objects.update_or_create(
            tenant_id=tenant_id,
            form_key=form_key,
            record_id=record_id,
            defaults={"values": cleaned},
        )
        record_event(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="tenancy.custom_field_values.created" if created else "tenancy.custom_field_values.updated",
            aggregate="CustomFieldRecord",
            aggregate_id=item.id,
            payload={"form_key": form_key, "record_id": str(record_id), "keys": sorted(cleaned)},
        )
    return cleaned
