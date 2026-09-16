import uuid

from django.db import models


class TimeStampedModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class TenantModel(TimeStampedModel):
    tenant_id = models.UUIDField(db_index=True)

    class Meta:
        abstract = True


class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField(db_index=True)
    actor_id = models.CharField(max_length=64)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    action = models.CharField(max_length=120, db_index=True)
    aggregate_type = models.CharField(max_length=80)
    aggregate_id = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-occurred_at"]


class OutboxEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField(db_index=True)
    topic = models.CharField(max_length=120, db_index=True)
    aggregate_id = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    available_at = models.DateTimeField(auto_now_add=True, db_index=True)
    attempt_count = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["created_at"]


class IdempotencyRecord(TenantModel):
    scope = models.CharField(max_length=120)
    key = models.CharField(max_length=128)
    request_hash = models.CharField(max_length=64)
    result_id = models.CharField(max_length=64, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant_id", "scope", "key"], name="unique_tenant_idempotency_key")]
