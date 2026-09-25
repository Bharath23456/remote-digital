from django.db import migrations


def normalize_removed_options(apps, schema_editor):
    QuestionMark = apps.get_model("marking", "QuestionMark")
    QuestionMark.objects.filter(outcome__in=["not_applicable", "skipped"]).update(
        outcome="unanswered"
    )
    QuestionMark.objects.filter(adjustment__in=["negative", "bonus"]).update(
        adjustment="none"
    )


def reverse_normalization(apps, schema_editor):
    # Removed options are intentionally not restored.
    pass


class Migration(migrations.Migration):
    dependencies = [("marking", "0003_questionpageanchor")]

    operations = [
        migrations.RunPython(normalize_removed_options, reverse_normalization),
    ]
