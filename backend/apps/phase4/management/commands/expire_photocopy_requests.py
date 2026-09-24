import time

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.core.services import record_event
from apps.phase4.models import StudentScriptRequest


class Command(BaseCommand):
    help = "Expire approved photocopy requests whose release window has ended"

    def add_arguments(self, parser):
        parser.add_argument("--forever", action="store_true")
        parser.add_argument("--interval", type=int, default=60)

    def handle(self, *args, **options):
        while True:
            count = self.expire_once()
            if count:
                self.stdout.write(f"Expired {count} photocopy request(s)")
            if not options["forever"]:
                return
            time.sleep(max(options["interval"], 10))

    @staticmethod
    def expire_once():
        now = timezone.now()
        count = 0
        request_ids = StudentScriptRequest.objects.filter(
            expires_at__lte=now,
            status__in=[StudentScriptRequest.Status.APPROVED, StudentScriptRequest.Status.AVAILABLE],
        ).values_list("id", flat=True)
        for request_id in request_ids:
            with transaction.atomic():
                item = StudentScriptRequest.objects.select_for_update().filter(
                    id=request_id,
                    status__in=[StudentScriptRequest.Status.APPROVED, StudentScriptRequest.Status.AVAILABLE],
                    expires_at__lte=now,
                ).first()
                if not item:
                    continue
                item.status = StudentScriptRequest.Status.EXPIRED
                item.download_allowed = False
                item.version += 1
                item.save(update_fields=["status", "download_allowed", "version", "updated_at"])
                record_event(
                    tenant_id=item.tenant_id,
                    actor_id=0,
                    action="student.copy.expired",
                    aggregate="StudentScriptRequest",
                    aggregate_id=item.id,
                    payload={"expired_at": item.expires_at.isoformat()},
                )
                count += 1
        return count
