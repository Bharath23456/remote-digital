# Generated manually for question configuration on 2026-09-16

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("configuration", "0004_subject_course_subject_related_subject_ids_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="question",
            name="question_type",
            field=models.CharField(
                choices=[
                    ("descriptive", "Descriptive"),
                    ("objective", "Objective"),
                    ("practical", "Practical"),
                    ("oral", "Oral"),
                    ("other", "Other"),
                ],
                default="descriptive",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="question",
            name="sub_question",
            field=models.CharField(blank=True, default="", max_length=8),
            preserve_default=False,
        ),
        migrations.RemoveConstraint(
            model_name="question",
            name="unique_paper_question",
        ),
        migrations.AddConstraint(
            model_name="question",
            constraint=models.UniqueConstraint(fields=("paper", "number", "sub_question"), name="unique_paper_question_part"),
        ),
    ]
