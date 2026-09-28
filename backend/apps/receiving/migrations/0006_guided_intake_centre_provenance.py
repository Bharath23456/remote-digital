from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("receiving", "0005_dispatch_source_institution_preparedpacket")]

    operations = [
        migrations.AddField(
            model_name="dispatch",
            name="prepared_centre_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="dispatch",
            name="received_centre_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="packet",
            name="received_centre_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="preparedpacket",
            name="prepared_centre_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
    ]
