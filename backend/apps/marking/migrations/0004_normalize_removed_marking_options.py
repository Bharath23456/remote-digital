from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("marking", "0003_questionpageanchor")]

    # Historical outcomes and adjustments are audit evidence. Change only the
    # choices offered for new marks; existing rows retain their original values.
    operations = [
        migrations.AlterField(
            model_name="questionmark",
            name="outcome",
            field=models.CharField(
                choices=[("evaluated", "Evaluated"), ("unanswered", "Unanswered")],
                default="evaluated",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="questionmark",
            name="adjustment",
            field=models.CharField(
                choices=[("none", "None"), ("grace", "Grace")],
                default="none",
                max_length=16,
            ),
        ),
    ]
