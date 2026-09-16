from django.db import migrations, models
import uuid


class Migration(migrations.Migration):
    dependencies = [("core", "0002_outbox_delivery_state")]

    operations = [
        migrations.CreateModel(
            name="IdempotencyRecord",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("tenant_id", models.UUIDField(db_index=True)),
                ("scope", models.CharField(max_length=120)),
                ("key", models.CharField(max_length=128)),
                ("request_hash", models.CharField(max_length=64)),
                ("result_id", models.CharField(blank=True, max_length=64)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("tenant_id", "scope", "key"), name="unique_tenant_idempotency_key")]},
        ),
    ]
