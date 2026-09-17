from django.db import migrations


def upgrade_pending_masks(apps, schema_editor):
    Job = apps.get_model("anonymisation", "MaskingJob")
    Region = apps.get_model("anonymisation", "MaskRegion")
    for job in Job.objects.filter(profile="university-standard-v1", status__in=["detected", "reviewed"]).iterator():
        Region.objects.filter(job_id=job.id, source="automatic").delete()
        Region.objects.create(tenant_id=job.tenant_id, job_id=job.id, page_number=1, category="identity_page", x=0, y=0, width=1, height=1, source="automatic", confidence=1)
        job.profile = "identity-cover-v1"
        if job.status == "reviewed":
            job.status = "detected"
            job.reviewed_by_id = None
            job.reviewed_at = None
        job.save(update_fields=["profile", "status", "reviewed_by_id", "reviewed_at", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [("anonymisation", "0002_identity_page_category")]
    operations = [migrations.RunPython(upgrade_pending_masks, migrations.RunPython.noop)]
