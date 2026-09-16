import hashlib
import json

from django.db import transaction
from django.utils import timezone
from ninja.errors import HttpError

from apps.core.models import IdempotencyRecord


def begin_idempotent(*, tenant_id, scope, key, payload):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Idempotency records must share the domain transaction")
    if not 8 <= len(key) <= 128:
        raise HttpError(422, "A valid Idempotency-Key header is required")
    request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    record = IdempotencyRecord.objects.select_for_update().filter(tenant_id=tenant_id, scope=scope, key=key).first()
    if record:
        if record.request_hash != request_hash:
            raise HttpError(409, "This idempotency key was already used with a different request")
        if not record.completed_at:
            raise HttpError(409, "The original request is still being processed")
        return record, record.result_id
    return IdempotencyRecord.objects.create(tenant_id=tenant_id, scope=scope, key=key, request_hash=request_hash), None


def complete_idempotent(record, result_id):
    record.result_id = str(result_id)
    record.completed_at = timezone.now()
    record.save(update_fields=["result_id", "completed_at", "updated_at"])
