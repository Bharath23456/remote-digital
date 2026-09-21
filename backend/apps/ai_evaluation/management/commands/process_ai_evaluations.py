import time

from django.core.management.base import BaseCommand

from apps.ai_evaluation.services import process_next_analysis


class Command(BaseCommand):
    help = "Process queued assistive and autonomous AI evaluations"

    def add_arguments(self, parser):
        parser.add_argument("--forever", action="store_true")
        parser.add_argument("--interval", type=float, default=2.0)

    def handle(self, *args, **options):
        while True:
            analysis = process_next_analysis()
            if analysis:
                self.stdout.write(f"{analysis.id} {analysis.status}")
            if not options["forever"]:
                break
            if not analysis:
                time.sleep(max(options["interval"], 0.25))
