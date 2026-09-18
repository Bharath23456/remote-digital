from django.db import migrations, models


def backfill_frozen_by_id(apps, schema_editor):
    Paper = apps.get_model("configuration", "Paper")
    AuditEvent = apps.get_model("core", "AuditEvent")

    for paper in Paper.objects.filter(status="frozen", frozen_by_id__isnull=True).iterator():
        event = (
            AuditEvent.objects.filter(
                tenant_id=paper.tenant_id,
                action="config.paper.frozen",
                aggregate_id=str(paper.id),
            )
            .order_by("-occurred_at")
            .first()
        )
        if not event:
            continue
        try:
            paper.frozen_by_id = int(event.actor_id)
        except (TypeError, ValueError):
            continue
        paper.save(update_fields=["frozen_by_id"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0004_auditevent_ip_address"),
        ("configuration", "0008_calendaroverlapexception"),
    ]

    operations = [
        migrations.AddField(
            model_name="paper",
            name="frozen_by_id",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_frozen_by_id, migrations.RunPython.noop),
    ]
