from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("identity_store", "0001_initial")]
    operations = [
        migrations.AddField(model_name="candidateidentity", name="signature_image_ciphertext", field=models.TextField(blank=True)),
        migrations.AddField(model_name="candidateidentity", name="photo_image_ciphertext", field=models.TextField(blank=True)),
    ]
