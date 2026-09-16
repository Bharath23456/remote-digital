import uuid

from django.db import models


class CandidateIdentity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField(db_index=True)
    identity_reference = models.UUIDField(unique=True, db_index=True)
    script_id = models.UUIDField(unique=True, db_index=True)
    session_id = models.UUIDField(null=True, blank=True, db_index=True)
    pii_ciphertext = models.TextField()
    register_number_hash = models.CharField(max_length=64, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class IdentityAccessLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField(db_index=True)
    identity_reference = models.UUIDField(db_index=True)
    action = models.CharField(max_length=40)
    authorization_id = models.CharField(max_length=64)
    actor_id = models.CharField(max_length=64)
    purpose = models.CharField(max_length=160)
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)


class ConsumedAuthorization(models.Model):
    jti = models.CharField(max_length=64, primary_key=True)
    consumed_at = models.DateTimeField(auto_now_add=True)
