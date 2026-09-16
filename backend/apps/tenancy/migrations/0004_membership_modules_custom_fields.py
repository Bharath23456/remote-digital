import uuid

from django.db import migrations, models


def grant_existing_memberships(apps, schema_editor):
    Membership = apps.get_model("tenancy", "Membership")
    TenantAccount = apps.get_model("tenancy", "TenantAccount")
    for membership in Membership.objects.select_related("institution").all():
        account = TenantAccount.objects.filter(root_institution__tenant_id=membership.institution.tenant_id).first()
        if account:
            membership.enabled_modules = list(account.enabled_modules)
            membership.save(update_fields=["enabled_modules"])


class Migration(migrations.Migration):
    dependencies = [("tenancy", "0003_membership_must_change_password_tenantaccount_and_more")]

    operations = [
        migrations.AddField(
            model_name="membership",
            name="enabled_modules",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="membership",
            name="custom_fields",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.CreateModel(
            name="CustomFieldDefinition",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("tenant_id", models.UUIDField(db_index=True)),
                ("form_key", models.SlugField(max_length=64)),
                ("key", models.SlugField(max_length=64)),
                ("label", models.CharField(max_length=120)),
                ("field_type", models.CharField(choices=[("text", "Text"), ("textarea", "Long text"), ("number", "Number"), ("date", "Date"), ("select", "Select"), ("checkbox", "Checkbox")], default="text", max_length=16)),
                ("required", models.BooleanField(default=False)),
                ("options", models.JSONField(blank=True, default=list)),
                ("placeholder", models.CharField(blank=True, max_length=160)),
                ("help_text", models.CharField(blank=True, max_length=240)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("version", models.PositiveIntegerField(default=1)),
            ],
            options={
                "ordering": ["form_key", "sort_order", "label"],
                "constraints": [models.UniqueConstraint(fields=("tenant_id", "form_key", "key"), name="unique_tenant_form_field_key")],
            },
        ),
        migrations.RunPython(grant_existing_memberships, migrations.RunPython.noop),
    ]
