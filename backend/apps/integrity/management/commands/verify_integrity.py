import time

from django.core.management.base import BaseCommand

from apps.integrity.models import IntegrityManifest
from apps.integrity.services import verify_manifest


class Command(BaseCommand):
    help = "Verify signed script manifests and preserve evidence for any mismatch"

    def add_arguments(self, parser):
        parser.add_argument("--forever", action="store_true")
        parser.add_argument("--interval", type=int, default=300)

    def handle(self, *args, **options):
        while True:
            passed = failed = 0
            for manifest in IntegrityManifest.objects.all().iterator():
                check, _ = verify_manifest(tenant_id=manifest.tenant_id, actor_id="integrity-verifier", manifest_id=manifest.id, checked_by="scheduled-verifier")
                if check.status == "passed":
                    passed += 1
                else:
                    failed += 1
            self.stdout.write(f"Integrity verification completed: {passed} passed, {failed} failed")
            if not options["forever"]:
                break
            time.sleep(max(options["interval"], 30))
