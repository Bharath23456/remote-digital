from django.db import migrations, models
from django.db.models import Count


def unlink_duplicate_profiles(apps, schema_editor):
    Evaluator = apps.get_model("evaluators", "Evaluator")
    duplicates = (
        Evaluator.objects.exclude(user_id=None)
        .values("tenant_id", "user_id")
        .annotate(profile_count=Count("id"))
        .filter(profile_count__gt=1)
    )
    for duplicate in duplicates.iterator():
        profiles = Evaluator.objects.filter(
            tenant_id=duplicate["tenant_id"],
            user_id=duplicate["user_id"],
        ).order_by("created_at", "id")
        keeper = profiles.first()
        profiles.exclude(id=keeper.id).update(user_id=None)


class Migration(migrations.Migration):
    dependencies = [("evaluators", "0006_evaluator_is_system_ai")]

    operations = [
        migrations.RunPython(unlink_duplicate_profiles, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="evaluator",
            constraint=models.UniqueConstraint(
                fields=("tenant_id", "user"),
                condition=models.Q(user__isnull=False),
                name="unique_evaluator_login",
            ),
        ),
    ]
