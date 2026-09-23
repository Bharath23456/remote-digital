from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("phase4", "0007_recounting_naming")]

    operations = [
        migrations.AddField(
            model_name="studentscriptrequest",
            name="release_mode",
            field=models.CharField(
                choices=[
                    ("masked", "Masked student copy"),
                    ("unmasked_identity", "Unmasked identity copy"),
                ],
                default="masked",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="studentscriptrequest",
            name="delivered_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="studentscriptrequest",
            name="delivery_reference",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AlterField(
            model_name="studentscriptrequest",
            name="status",
            field=models.CharField(
                choices=[
                    ("requested", "Requested"),
                    ("approved", "Approved"),
                    ("available", "Available"),
                    ("delivered", "Delivered to university"),
                    ("expired", "Expired"),
                    ("rejected", "Rejected"),
                ],
                default="requested",
                max_length=16,
            ),
        ),
    ]
