from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("marking", "0004_normalize_removed_marking_options")]

    operations = [
        migrations.AlterField(
            model_name="questionmark",
            name="marks",
            field=models.DecimalField(decimal_places=1, max_digits=5),
        ),
    ]