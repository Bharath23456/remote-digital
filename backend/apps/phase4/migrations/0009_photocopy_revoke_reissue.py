from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("phase4", "0008_photocopy_release_delivery")]

    operations = [
        migrations.AddField("studentscriptrequest", "revoked_at", models.DateTimeField(blank=True, null=True)),
        migrations.AddField("studentscriptrequest", "reissued_from", models.ForeignKey(blank=True, null=True, on_delete=models.deletion.PROTECT, related_name="reissues", to="phase4.studentscriptrequest")),
        migrations.AlterField("studentscriptrequest", "status", models.CharField(choices=[("requested", "Requested"), ("approved", "Approved"), ("available", "Available"), ("delivered", "Delivered to university"), ("expired", "Expired"), ("rejected", "Rejected"), ("revoked", "Revoked")], default="requested", max_length=16)),
    ]
