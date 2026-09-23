from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenancy", "0006_membership_operational_roles"),
        ("tenancy", "0009_sync_ai_evaluation_entitlement"),
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
                    ("script_receiver", "Script receiver"),
                    ("scanner_operator", "Scanner operator"),
                    ("custody_officer", "Chain custody officer"),
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
