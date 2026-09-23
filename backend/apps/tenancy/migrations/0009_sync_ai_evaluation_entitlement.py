from django.db import migrations


def sync_ai_entitlements(apps, schema_editor):
    TenantAccount = apps.get_model("tenancy", "TenantAccount")
    Membership = apps.get_model("tenancy", "Membership")
    SecurityPolicy = apps.get_model("security", "SecurityPolicy")

    enabled_tenants = set(
        SecurityPolicy.objects.exclude(ai_evaluation_mode="disabled").values_list("tenant_id", flat=True)
    )
    for account in TenantAccount.objects.select_related("root_institution").iterator():
        modules = set(account.enabled_modules or [])
        if account.root_institution.tenant_id in enabled_tenants:
            modules.add("ai_evaluation")
        else:
            modules.discard("ai_evaluation")
        account.enabled_modules = sorted(modules)
        account.save(update_fields=["enabled_modules"])

    for membership in Membership.objects.select_related("institution").iterator():
        modules = set(membership.enabled_modules or [])
        modules.discard("ai_evaluation")
        if membership.institution.tenant_id in enabled_tenants and membership.role in {"platform_admin", "university_admin"}:
            modules.add("ai_evaluation")
        membership.enabled_modules = sorted(modules)
        membership.save(update_fields=["enabled_modules"])


def remove_ai_entitlements(apps, schema_editor):
    TenantAccount = apps.get_model("tenancy", "TenantAccount")
    Membership = apps.get_model("tenancy", "Membership")
    for model in (TenantAccount, Membership):
        for item in model.objects.all().iterator():
            item.enabled_modules = sorted(set(item.enabled_modules or []) - {"ai_evaluation"})
            item.save(update_fields=["enabled_modules"])


class Migration(migrations.Migration):
    dependencies = [
        ("security", "0003_securitypolicy_ai_governance"),
        ("tenancy", "0008_enable_ai_evaluation_module"),
    ]
    operations = [migrations.RunPython(sync_ai_entitlements, remove_ai_entitlements)]
