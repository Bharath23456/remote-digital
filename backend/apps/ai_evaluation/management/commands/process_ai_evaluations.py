import logging
import time

from django.core.management.base import BaseCommand

from apps.ai_evaluation.models import AIAnalysis
from apps.ai_evaluation.services import process_next_analysis


class Command(BaseCommand):
    help = "Process queued assistive and autonomous AI evaluations"

    def add_arguments(self, parser):
        parser.add_argument("--forever", action="store_true")
        parser.add_argument("--interval", type=float, default=2.0)

    def handle(self, *args, **options):
        service_logger = logging.getLogger("apps.ai_evaluation.services")
        service_logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(self.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        service_logger.addHandler(handler)
        try:
            while True:
                analysis = process_next_analysis()
                if analysis:
                    status = "Assigned for manual evaluation" if analysis.status == AIAnalysis.Status.LOW_CONFIDENCE else analysis.status
                    self.stdout.write(f"{analysis.id} {status}")
                if not options["forever"]:
                    break
                if not analysis:
                    time.sleep(max(options["interval"], 0.25))
        finally:
            service_logger.removeHandler(handler)
