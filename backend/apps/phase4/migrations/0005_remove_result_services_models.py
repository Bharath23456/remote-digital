from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("phase4", "0004_alter_proctoringevidence_status"),
    ]

    operations = [
        migrations.DeleteModel(
            name="StudentScriptRequest",
        ),
        migrations.DeleteModel(
            name="RemunerationStatement",
        ),
        migrations.DeleteModel(
            name="RemunerationRule",
        ),
    ]
