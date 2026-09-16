from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("anonymisation", "0001_initial")]

    operations = [
        migrations.AlterField(model_name="maskingjob", name="profile", field=models.CharField(default="identity-cover-v1", max_length=80)),
        migrations.AlterField(
            model_name="maskregion",
            name="category",
            field=models.CharField(choices=[("identity_page", "Identity cover page"), ("candidate_name", "Candidate name"), ("register_number", "Register number"), ("usn", "USN"), ("college", "College"), ("institution", "Institution"), ("signature", "Signature"), ("photograph", "Photograph")], max_length=24),
        ),
    ]
