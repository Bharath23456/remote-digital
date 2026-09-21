from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("ai_evaluation", "0001_initial")]
    operations = [migrations.DeleteModel(name="AIEvaluationPolicy")]
