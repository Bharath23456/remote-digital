# Generated manually for Phase 4 official-portal integrations.

import uuid

import django.db.models.deletion
from django.db import migrations, models


REMOVED_RESULT_SERVICE_MODELS = (
    "RemunerationRule",
    "RemunerationStatement",
    "StudentScriptRequest",
)


def restore_removed_result_service_tables(apps, schema_editor):
    """Repair databases that ran the short-lived destructive Phase 4 migration."""
    existing_tables = set(schema_editor.connection.introspection.table_names())

    for model_name in REMOVED_RESULT_SERVICE_MODELS:
        model = apps.get_model("phase4", model_name)
        table_name = model._meta.db_table
        if table_name in existing_tables:
            continue
        schema_editor.create_model(model)
        existing_tables.add(table_name)


class Migration(migrations.Migration):

    dependencies = [
        ("phase4", "0005_revaluationrequest_external_application_id_and_more"),
    ]

    operations = [
        migrations.RunPython(
            restore_removed_result_service_tables,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddField(
            model_name="revaluationrequest",
            name="remarking_notes",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="revaluationrequest",
            name="request_type",
            field=models.CharField(
                choices=[
                    ("revaluation", "Revaluation"),
                    ("remarking", "Remarking"),
                ],
                db_index=True,
                default="revaluation",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="studentscriptrequest",
            name="external_application_id",
            field=models.CharField(blank=True, db_index=True, max_length=128),
        ),
        migrations.AddField(
            model_name="studentscriptrequest",
            name="external_payload",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="studentscriptrequest",
            name="integration_endpoint",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="student_script_requests",
                to="phase4.integrationendpoint",
            ),
        ),
        migrations.AddField(
            model_name="studentscriptrequest",
            name="received_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="studentscriptrequest",
            name="source_system",
            field=models.CharField(db_index=True, default="admiezo", max_length=50),
        ),
        migrations.CreateModel(
            name="UniversityApiKey",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("tenant_id", models.UUIDField(db_index=True)),
                ("name", models.CharField(max_length=120)),
                ("key_prefix", models.CharField(db_index=True, max_length=16)),
                ("key_hash", models.CharField(max_length=64, unique=True)),
                ("source_system", models.CharField(db_index=True, max_length=50)),
                ("scopes", models.JSONField(blank=True, default=list)),
                ("status", models.CharField(choices=[("active", "Active"), ("revoked", "Revoked")], default="active", max_length=16)),
                ("created_by_id", models.PositiveBigIntegerField()),
                ("last_used_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("version", models.PositiveIntegerField(default=1)),
                (
                    "integration_endpoint",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="api_keys",
                        to="phase4.integrationendpoint",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("tenant_id", "source_system", "name"), name="unique_university_api_key_name"),
                ],
            },
        ),
        migrations.AddIndex(
            model_name="studentscriptrequest",
            index=models.Index(fields=["tenant_id", "source_system", "external_application_id"], name="student_req_external_idx"),
        ),
        migrations.AddConstraint(
            model_name="studentscriptrequest",
            constraint=models.UniqueConstraint(condition=models.Q(("external_application_id", ""), _negated=True), fields=("tenant_id", "source_system", "external_application_id"), name="unique_external_student_script_request"),
        ),
    ]
