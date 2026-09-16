import json
import logging
from datetime import timedelta
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from apps.core.models import OutboxEvent


logger = logging.getLogger("admiezo.outbox")


def _deliver(event):
    body = json.dumps({
        "id": str(event.id),
        "tenant_id": str(event.tenant_id),
        "topic": event.topic,
        "aggregate_id": event.aggregate_id,
        "payload": event.payload,
        "created_at": event.created_at.isoformat(),
    }, separators=(",", ":")).encode()
    if settings.OUTBOX_MODE == "log":
        logger.info("outbox_event=%s", body.decode())
        return
    request = Request(
        settings.OUTBOX_WEBHOOK_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": str(event.id),
            **({"Authorization": f"Bearer {settings.OUTBOX_AUTH_TOKEN}"} if settings.OUTBOX_AUTH_TOKEN else {}),
        },
    )
    with urlopen(request, timeout=10) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"Outbox destination returned HTTP {response.status}")


def publish_next():
    now = timezone.now()
    with transaction.atomic():
        queryset = OutboxEvent.objects.filter(published_at__isnull=True, available_at__lte=now).order_by("created_at")
        if connection.features.has_select_for_update_skip_locked:
            queryset = queryset.select_for_update(skip_locked=True)
        else:
            queryset = queryset.select_for_update()
        event = queryset.first()
        if not event:
            return False
        try:
            _deliver(event)
        except Exception as exc:
            event.attempt_count += 1
            delay = min(300, 2 ** min(event.attempt_count, 8))
            event.available_at = now + timedelta(seconds=delay)
            event.last_error = str(exc)[:500]
            event.save(update_fields=["attempt_count", "available_at", "last_error"])
            logger.exception("Outbox delivery failed for %s", event.id)
            return True
        event.published_at = now
        event.attempt_count += 1
        event.last_error = ""
        event.save(update_fields=["published_at", "attempt_count", "last_error"])
        return True
