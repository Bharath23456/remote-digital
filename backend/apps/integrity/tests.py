from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from apps.repository.models import ScriptAsset
from apps.repository.storage import ObjectMetadata
from apps.custody.models import Script

from .models import IntegrityAlert, IntegrityCheck, IntegrityEvidence
from .services import create_manifest, verify_manifest


class IntegrityManifestTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap_demo", verbosity=0)
        cls.actor = User.objects.get(username="admin@admiezo.local")

    @patch("apps.integrity.services.read_object_metadata")
    def test_signed_manifest_pass_and_tamper_failure_preserve_evidence(self, metadata):
        script = Script.objects.first()
        asset = ScriptAsset.objects.create(tenant_id=script.tenant_id, script=script, kind=ScriptAsset.Kind.MASTER, page_number=99, storage_key=f"scripts-master/{script.id}/integrity-test.webp", sha256="a" * 64, byte_size=1024, mime_type="image/webp")
        metadata.return_value = ObjectMetadata(asset.sha256, asset.byte_size, asset.mime_type, "completed", "completed")
        manifest, created = create_manifest(tenant_id=asset.tenant_id, actor_id=self.actor.id, asset=asset)
        self.assertTrue(created)
        passed, alert = verify_manifest(tenant_id=asset.tenant_id, actor_id=self.actor.id, manifest_id=manifest.id)
        self.assertEqual(passed.status, IntegrityCheck.Status.PASSED)
        self.assertIsNone(alert)
        metadata.return_value = ObjectMetadata("f" * 64, asset.byte_size, asset.mime_type, "completed", "completed")
        failed, alert = verify_manifest(tenant_id=asset.tenant_id, actor_id=self.actor.id, manifest_id=manifest.id)
        self.assertEqual(failed.status, IntegrityCheck.Status.FAILED)
        self.assertEqual(alert.status, IntegrityAlert.Status.OPEN)
        self.assertEqual(IntegrityEvidence.objects.filter(alert=alert).count(), 1)
