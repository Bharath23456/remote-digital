from django.core.management.base import BaseCommand

from apps.valuation.services import reconcile_single_round_results


class Command(BaseCommand):
    help = "Propose final marks for existing one-round valuations without an escalation threshold."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Create missing proposals; otherwise report a dry run")

    def handle(self, *args, **options):
        count = reconcile_single_round_results(apply=options["apply"])
        self.stdout.write(f"{count} eligible one-round result(s) {'processed' if options['apply'] else 'would be processed'}")
