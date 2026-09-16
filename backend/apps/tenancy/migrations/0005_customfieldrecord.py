import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tenancy", "0004_membership_modules_custom_fields")]

    operations = [
        migrations.CreateModel(
            name="CustomFieldRecord",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("tenant_id", models.UUIDField(db_index=True)),
                ("form_key", models.SlugField(max_length=64)),
                ("record_id", models.UUIDField()),
                ("values", models.JSONField(default=dict)),
            ],
            options={
                "constraints": [models.UniqueConstraint(fields=("tenant_id", "form_key", "record_id"), name="unique_tenant_form_field_record")],
            },
        ),
    ]
