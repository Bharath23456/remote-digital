from django.db import migrations, models


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
    ]
