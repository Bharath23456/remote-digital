from django.db import migrations, models


def normalize_strict_policies(apps, schema_editor):
    SecurityPolicy = apps.get_model("security", "SecurityPolicy")
    SecurityPolicy.objects.filter(evaluation_strict_mode=True).update(
        evaluation_identity_verification_required=True,
        evaluation_camera_required=True,
        evaluation_fullscreen_required=True,
        evaluation_single_screen_required=True,
        evaluation_mobile_allowed=False,
        evaluation_event_recording=True,
        evaluation_pause_on_violation=True,
        evaluation_require_resume_step_up=True,
        evaluation_allow_clipboard=False,
        evaluation_allow_download=False,
        evaluation_allow_print=False,
    )


class Migration(migrations.Migration):
    dependencies = [("security", "0004_alter_securitypolicy_ai_model_name")]

    operations = [
        migrations.AddField("securitypolicy", "evaluation_strict_mode", models.BooleanField(default=True)),
        migrations.AddField("securitypolicy", "evaluation_identity_verification_required", models.BooleanField(default=True)),
        migrations.AddField("securitypolicy", "evaluation_mobile_allowed", models.BooleanField(default=False)),
        migrations.AddField("securitypolicy", "evaluation_pause_on_violation", models.BooleanField(default=True)),
        migrations.AddField("securitypolicy", "evaluation_require_resume_step_up", models.BooleanField(default=True)),
        migrations.AddField("securitypolicy", "evaluation_allow_clipboard", models.BooleanField(default=False)),
        migrations.AddField("securitypolicy", "evaluation_allow_download", models.BooleanField(default=False)),
        migrations.AddField("securitypolicy", "evaluation_allow_print", models.BooleanField(default=False)),
        migrations.AddField("securitypolicy", "evaluation_session_timeout_minutes", models.PositiveIntegerField(default=180)),
        migrations.RunPython(normalize_strict_policies, migrations.RunPython.noop),
    ]
