import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def link_users_by_email(apps, schema_editor):
    Evaluator = apps.get_model("evaluators", "Evaluator")
    User = apps.get_model("auth", "User")
    for evaluator in Evaluator.objects.exclude(email=""):
        user = User.objects.filter(email__iexact=evaluator.email).first()
        if user:
            evaluator.user_id = user.id
            evaluator.save(update_fields=["user"])


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("evaluators", "0003_alter_evaluator_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="evaluator",
            name="custom_fields",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="evaluator",
            name="user",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="evaluator_profiles", to=settings.AUTH_USER_MODEL),
        ),
        migrations.RunPython(link_users_by_email, migrations.RunPython.noop),
    ]
