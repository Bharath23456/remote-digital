from django.db import migrations


def enable_ai_evaluation(apps, schema_editor):
    TenantAccount = apps.get_model("tenancy", "TenantAccount")
    Membership = apps.get_model("tenancy", "Membership")
    for account in TenantAccount.objects.all().iterator():
        modules = list(account.enabled_modules or [])
        if "ai_evaluation" not in modules:
            modules.append("ai_evaluation")
            account.enabled_modules = modules
            account.save(update_fields=["enabled_modules"])
    for membership in Membership.objects.filter(role__in=["platform_admin", "university_admin", "exam_controller"]).iterator():
        modules = list(membership.enabled_modules or [])
        if "ai_evaluation" not in modules:
            modules.append("ai_evaluation")
            membership.enabled_modules = modules
            membership.save(update_fields=["enabled_modules"])


def disable_ai_evaluation(apps, schema_editor):
    TenantAccount = apps.get_model("tenancy", "TenantAccount")
    Membership = apps.get_model("tenancy", "Membership")
    for model in (TenantAccount, Membership):
        for item in model.objects.all().iterator():
            modules = [module for module in (item.enabled_modules or []) if module != "ai_evaluation"]
            if modules != item.enabled_modules:
                item.enabled_modules = modules
                item.save(update_fields=["enabled_modules"])


class Migration(migrations.Migration):
    dependencies = [("tenancy", "0007_alter_membership_role")]
    operations = [migrations.RunPython(enable_ai_evaluation, disable_ai_evaluation)]
