from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="outboxevent",
            name="attempt_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="outboxevent",
            name="available_at",
            field=models.DateTimeField(auto_now_add=True, db_index=True),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="outboxevent",
            name="last_error",
            field=models.CharField(blank=True, max_length=500),
        ),
    ]
