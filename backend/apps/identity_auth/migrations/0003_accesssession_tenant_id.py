from django.db import migrations, models


def bind_existing_sessions(apps, schema_editor):
    AccessSession = apps.get_model("identity_auth", "AccessSession")
    Membership = apps.get_model("tenancy", "Membership")
    for session in AccessSession.objects.all().iterator():
        membership = Membership.objects.filter(user_id=session.user_id, is_active=True).select_related("institution").order_by("created_at").first()
        if membership:
            session.tenant_id = membership.institution.tenant_id
            session.save(update_fields=["tenant_id"])
        else:
            session.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("identity_auth", "0002_accesssession_expires_at_accesssession_location_and_more"),
        ("tenancy", "0002_institution_version_alter_institution_code_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="accesssession",
            name="tenant_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.RunPython(bind_existing_sessions, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="accesssession",
            name="tenant_id",
            field=models.UUIDField(db_index=True),
        ),
    ]
