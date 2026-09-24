from django.db import migrations, models


def rename_legacy_request_type(apps, schema_editor):
    RevaluationRequest = apps.get_model("phase4", "RevaluationRequest")
    RevaluationRequest.objects.filter(request_type="remarking").update(request_type="recounting")


class Migration(migrations.Migration):
    dependencies = [("phase4", "0006_university_api_keys_official_requests")]

    operations = [
        migrations.RenameField(
            model_name="revaluationrequest",
            old_name="remarking_notes",
            new_name="recounting_notes",
        ),
        migrations.RunPython(rename_legacy_request_type, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="revaluationrequest",
            name="request_type",
            field=models.CharField(
                choices=[("revaluation", "Revaluation"), ("recounting", "Recounting")],
                db_index=True,
                default="revaluation",
                max_length=20,
            ),
        ),
    ]
