import time

from django.core.management.base import BaseCommand

from apps.core.outbox import publish_next


class Command(BaseCommand):
    help = "Publish transactional outbox events with at-least-once delivery"

    def add_arguments(self, parser):
        parser.add_argument("--forever", action="store_true")
        parser.add_argument("--poll-seconds", type=float, default=1.0)

    def handle(self, *args, **options):
        while True:
            delivered_or_deferred = publish_next()
            if not options["forever"]:
                if not delivered_or_deferred:
                    return
                continue
            if not delivered_or_deferred:
                time.sleep(max(options["poll_seconds"], 0.1))
