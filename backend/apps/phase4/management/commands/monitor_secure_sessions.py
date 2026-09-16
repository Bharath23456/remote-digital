import time

from django.core.management.base import BaseCommand

from apps.phase4.services import pause_stale_secure_sessions, purge_expired_proctoring_evidence


class Command(BaseCommand):
    help = "Pause secure evaluation sessions whose browser heartbeat has stopped"

    def add_arguments(self, parser):
        parser.add_argument("--forever", action="store_true")
        parser.add_argument("--interval", type=int, default=10)

    def handle(self, *args, **options):
        while True:
            paused = pause_stale_secure_sessions()
            purged = purge_expired_proctoring_evidence()
            if paused:
                self.stdout.write(f"Paused {paused} stale secure evaluation session(s)")
            if purged:
                self.stdout.write(f"Purged {purged} expired proctoring evidence object(s)")
            if not options["forever"]:
                return
            time.sleep(max(options["interval"], 5))
