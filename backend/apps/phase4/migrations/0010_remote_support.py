import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("allocation", "0002_assignment_accepted_at_assignment_backup_evaluator_and_more"),
        ("evaluators", "0006_evaluator_is_system_ai"),
        ("phase4", "0009_photocopy_revoke_reissue"),
    ]

    operations = [
        migrations.CreateModel(
            name="RemoteSupportSession",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("tenant_id", models.UUIDField(db_index=True)),
                ("requested_by_id", models.PositiveBigIntegerField()),
                ("reason", models.TextField()),
                ("status", models.CharField(choices=[("requested", "Requested"), ("active", "Active"), ("rejected", "Rejected"), ("ended", "Ended"), ("revoked", "Revoked"), ("expired", "Expired")], default="requested", max_length=16)),
                ("responded_at", models.DateTimeField(blank=True, null=True)),
                ("expires_at", models.DateTimeField()),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("version", models.PositiveIntegerField(default=1)),
                ("assignment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="remote_support_sessions", to="allocation.assignment")),
                ("evaluator", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="remote_support_sessions", to="evaluators.evaluator")),
                ("notification", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="remote_support_session", to="phase4.notificationdelivery")),
                ("secure_session", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="remote_support_sessions", to="phase4.secureevaluationsession")),
            ],
        ),
        migrations.CreateModel(
            name="RemoteSupportCommand",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("tenant_id", models.UUIDField(db_index=True)),
                ("sequence", models.PositiveIntegerField()),
                ("kind", models.CharField(choices=[("previous_page", "Previous page"), ("next_page", "Next page"), ("refresh_viewer", "Refresh viewer")], max_length=24)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("requested_by_id", models.PositiveBigIntegerField()),
                ("status", models.CharField(choices=[("queued", "Queued"), ("applied", "Applied"), ("failed", "Failed")], default="queued", max_length=16)),
                ("result", models.CharField(blank=True, max_length=240)),
                ("applied_at", models.DateTimeField(blank=True, null=True)),
                ("support_session", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="commands", to="phase4.remotesupportsession")),
            ],
            options={"ordering": ["sequence"]},
        ),
        migrations.AddIndex(model_name="remotesupportsession", index=models.Index(fields=["tenant_id", "evaluator", "status"], name="phase4_support_evaluator_idx")),
        migrations.AddIndex(model_name="remotesupportsession", index=models.Index(fields=["tenant_id", "assignment", "status"], name="phase4_support_assignment_idx")),
        migrations.AddConstraint(model_name="remotesupportcommand", constraint=models.UniqueConstraint(fields=("support_session", "sequence"), name="unique_remote_support_command_sequence")),
    ]
