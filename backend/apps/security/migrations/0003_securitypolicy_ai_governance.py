from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("security", "0002_securitypolicy_evaluation_camera_required_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="securitypolicy",
            name="ai_evaluation_mode",
            field=models.CharField(
                choices=[
                    ("disabled", "No AI"),
                    ("assistive", "AI assistance"),
                    ("autonomous", "Autonomous AI"),
                ],
                default="disabled",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="securitypolicy",
            name="ai_confidence_threshold",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("85.00"),
                max_digits=5,
            ),
        ),
        migrations.AddField(
            model_name="securitypolicy",
            name="ai_model_name",
            field=models.CharField(default="gemini-2.5-flash", max_length=80),
        ),
    ]
