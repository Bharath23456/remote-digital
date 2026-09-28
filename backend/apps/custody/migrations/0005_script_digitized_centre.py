from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("custody", "0004_script_recognized_cover_sha256")]

    operations = [
        migrations.AddField(
            model_name="script",
            name="digitized_centre_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
    ]
