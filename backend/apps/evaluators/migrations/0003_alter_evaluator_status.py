from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("evaluators", "0002_evaluator_email_evaluator_employee_id_and_more")]

    operations = [
        migrations.AlterField(
            model_name="evaluator",
            name="status",
            field=models.CharField(choices=[("pending", "Pending verification"), ("active", "Active"), ("inactive", "Inactive"), ("suspended", "Suspended"), ("retired", "Retired")], default="pending", max_length=16),
        ),
    ]
