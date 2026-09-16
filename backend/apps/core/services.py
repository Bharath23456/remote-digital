from contextvars import ContextVar

from django.db import transaction

from apps.core.models import AuditEvent, OutboxEvent


_audit_ip = ContextVar("audit_ip", default=None)


def set_audit_ip(value):
    return _audit_ip.set(value)


def reset_audit_ip(token):
    _audit_ip.reset(token)


def record_event(*, tenant_id, actor_id, action, aggregate, aggregate_id, payload=None):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Domain events must be recorded inside transaction.atomic()")
    body = payload or {}
    AuditEvent.objects.create(
        tenant_id=tenant_id,
        actor_id=str(actor_id),
        ip_address=_audit_ip.get(),
        action=action,
        aggregate_type=aggregate,
        aggregate_id=str(aggregate_id),
        payload=body,
    )
    OutboxEvent.objects.create(
        tenant_id=tenant_id,
        topic=action,
        aggregate_id=str(aggregate_id),
        payload={"aggregate": aggregate, **body},
    )
