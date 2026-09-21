from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tenancy", "0005_customfieldrecord")]

    operations = [
        migrations.AlterField(
            model_name="membership",
            name="role",
            field=models.CharField(
                choices=[
                    ("platform_admin", "Platform administrator"),
                    ("university_admin", "University administrator"),
                    ("exam_controller", "Examination controller"),
                    ("evaluator", "Evaluator"),
                    ("receiving_officer", "Receiving officer"),
                    ("script_receiver", "Script receiver"),
                    ("scanner_operator", "Scanner operator"),
                    ("custody_officer", "Chain custody officer"),
                    ("auditor", "Auditor"),
                ],
                max_length=32,
            ),
        ),
    ]
