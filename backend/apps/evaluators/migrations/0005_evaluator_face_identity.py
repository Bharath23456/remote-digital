import decimal
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("allocation", "0002_assignment_accepted_at_assignment_backup_evaluator_and_more"),
        ("evaluators", "0004_evaluator_user_custom_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="EvaluatorFaceTemplate",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("tenant_id", models.UUIDField(db_index=True)),
                ("encrypted_template", models.TextField()),
                ("template_digest", models.CharField(db_index=True, max_length=64)),
                ("model_version", models.CharField(default="opencv-sface-v1", max_length=64)),
                ("threshold", models.DecimalField(decimal_places=4, default=decimal.Decimal("0.8200"), max_digits=5)),
                ("quality_score", models.DecimalField(decimal_places=2, default=decimal.Decimal("0.00"), max_digits=5)),
                ("liveness_reference", models.JSONField(blank=True, default=dict)),
                ("enrolled_by_id", models.PositiveBigIntegerField()),
                ("enrolled_at", models.DateTimeField(auto_now_add=True)),
                ("last_verified_at", models.DateTimeField(blank=True, null=True)),
                ("status", models.CharField(choices=[("active", "Active"), ("revoked", "Revoked")], default="active", max_length=16)),
                ("version", models.PositiveIntegerField(default=1)),
                ("evaluator", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="face_template", to="evaluators.evaluator")),
            ],
        ),
        migrations.CreateModel(
            name="EvaluatorIdentityVerification",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("tenant_id", models.UUIDField(db_index=True)),
                ("access_session_id", models.UUIDField(blank=True, db_index=True, null=True)),
                ("verified", models.BooleanField(default=False)),
                ("liveness_verified", models.BooleanField(default=False)),
                ("authorized", models.BooleanField(default=False)),
                ("access_granted", models.BooleanField(default=False)),
                ("similarity_score", models.DecimalField(decimal_places=4, default=decimal.Decimal("0.0000"), max_digits=5)),
                ("threshold", models.DecimalField(decimal_places=4, default=decimal.Decimal("0.8200"), max_digits=5)),
                ("failure_reason", models.CharField(blank=True, db_index=True, max_length=80)),
                ("model_version", models.CharField(default="opencv-sface-v1", max_length=64)),
                ("probe_digest", models.CharField(blank=True, max_length=64)),
                ("device_fingerprint", models.CharField(blank=True, max_length=128)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("evidence", models.JSONField(blank=True, default=dict)),
                ("expires_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("attempt_number", models.PositiveIntegerField(default=1)),
                ("assignment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="identity_verifications", to="allocation.assignment")),
                ("evaluator", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="identity_verifications", to="evaluators.evaluator")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="evaluatorfacetemplate",
            index=models.Index(fields=["tenant_id", "status"], name="evaluators__tenant__0dc1b4_idx"),
        ),
        migrations.AddIndex(
            model_name="evaluatoridentityverification",
            index=models.Index(fields=["tenant_id", "evaluator", "created_at"], name="evaluators__tenant__560cb4_idx"),
        ),
        migrations.AddIndex(
            model_name="evaluatoridentityverification",
            index=models.Index(fields=["tenant_id", "access_session_id", "expires_at"], name="evaluators__tenant__0d32b2_idx"),
        ),
    ]
