from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenancy", "0006_alter_membership_role"),
    ]

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
                    ("bundle_preparer", "Bundle preparer"),
                    ("intake_receiver", "Bundle and packet receiver"),
                    ("scan_operator", "Scan operator"),
                    ("operations_supervisor", "Operations supervisor"),
                    ("auditor", "Auditor"),
                ],
                max_length=32,
            ),
        ),
    ]
