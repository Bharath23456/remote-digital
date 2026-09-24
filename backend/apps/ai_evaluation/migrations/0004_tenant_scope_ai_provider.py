import uuid

from django.db import migrations, models


def copy_platform_key_to_tenants(apps, schema_editor):
    Configuration = apps.get_model("ai_evaluation", "AIProviderConfiguration")
    Institution = apps.get_model("tenancy", "Institution")
    source = Configuration.objects.order_by("created_at").first()
    if not source:
        return
    tenant_ids = list(
        Institution.objects.filter(parent__isnull=True)
        .order_by("created_at")
        .values_list("tenant_id", flat=True)
        .distinct()
    )
    if not tenant_ids:
        source.tenant_id = uuid.UUID(int=0)
        source.save(update_fields=["tenant_id"])
        return
    source.tenant_id = tenant_ids[0]
    source.save(update_fields=["tenant_id"])
    for tenant_id in tenant_ids[1:]:
        Configuration.objects.create(
            tenant_id=tenant_id,
            key="primary",
            api_key_ciphertext=source.api_key_ciphertext,
            is_active=source.is_active,
            verified_at=source.verified_at,
            version=source.version,
        )


def consolidate_tenant_keys(apps, schema_editor):
    Configuration = apps.get_model("ai_evaluation", "AIProviderConfiguration")
    configurations = list(Configuration.objects.order_by("created_at"))
    if not configurations:
        return
    keeper = configurations[0]
    keeper.key = "primary"
    keeper.save(update_fields=["key"])
    Configuration.objects.exclude(id=keeper.id).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("ai_evaluation", "0003_aiproviderconfiguration"),
        ("tenancy", "0009_sync_ai_evaluation_entitlement"),
    ]

    operations = [
        migrations.AlterField(
            model_name="aiproviderconfiguration",
            name="key",
            field=models.CharField(default="primary", max_length=32),
        ),
        migrations.AddField(
            model_name="aiproviderconfiguration",
            name="tenant_id",
            field=models.UUIDField(db_index=True, null=True),
        ),
        migrations.RunPython(copy_platform_key_to_tenants, consolidate_tenant_keys),
        migrations.AlterField(
            model_name="aiproviderconfiguration",
            name="tenant_id",
            field=models.UUIDField(db_index=True),
        ),
        migrations.RemoveField(
            model_name="aiproviderconfiguration",
            name="key",
        ),
        migrations.AddConstraint(
            model_name="aiproviderconfiguration",
            constraint=models.UniqueConstraint(fields=("tenant_id",), name="unique_ai_provider_per_tenant"),
        ),
    ]
