from django.db import migrations


def restore_privileged_modules(apps, schema_editor):
    Membership = apps.get_model("tenancy", "Membership")
    TenantAccount = apps.get_model("tenancy", "TenantAccount")

    modules_by_tenant = {
        account.root_institution.tenant_id: list(account.enabled_modules or [])
        for account in TenantAccount.objects.select_related("root_institution").iterator()
    }
    memberships = Membership.objects.select_related("institution").filter(
        role__in=["platform_admin", "university_admin"]
    )
    for membership in memberships.iterator():
        modules = modules_by_tenant.get(membership.institution.tenant_id, [])
        if membership.enabled_modules != modules:
            membership.enabled_modules = modules
            membership.save(update_fields=["enabled_modules"])


class Migration(migrations.Migration):
    dependencies = [("tenancy", "0010_merge_operational_roles_ai")]
    operations = [migrations.RunPython(restore_privileged_modules, migrations.RunPython.noop)]
