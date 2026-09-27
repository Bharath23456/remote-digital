from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tenancy", "0012_tenantaccount_brand_description_and_more")]

    operations = [
        migrations.AddField(
            model_name="membership",
            name="operational_centre_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
    ]
