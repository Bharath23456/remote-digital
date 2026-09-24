"""Database-only operational fixtures, separate from the empty demo workspace."""

from datetime import timedelta

from django.utils import timezone

from apps.allocation.models import Assignment
from apps.configuration.models import Paper
from apps.custody.models import Script
from apps.evaluators.models import Evaluator
from apps.receiving.models import Dispatch, Packet


def create_operational_fixtures():
    for paper in Paper.objects.order_by("code"):
        evaluators = list(Evaluator.objects.filter(tenant_id=paper.tenant_id, status=Evaluator.Status.ACTIVE, is_system_ai=False))
        dispatch = Dispatch.objects.create(
            tenant_id=paper.tenant_id, paper=paper, reference=f"TEST-{paper.code}",
            source_centre="Test centre", expected_packets=1, received_packets=1,
            expected_scripts=len(evaluators) + 1, received_scripts=len(evaluators) + 1,
            status=Dispatch.Status.RECONCILED,
        )
        packet = Packet.objects.create(
            tenant_id=paper.tenant_id, dispatch=dispatch, barcode=f"TEST-PKT-{paper.code}",
            expected_scripts=len(evaluators) + 1, received_scripts=len(evaluators) + 1,
            status="received",
        )
        for index, evaluator in enumerate([None, *evaluators]):
            script = Script.objects.create(
                tenant_id=paper.tenant_id, paper=paper, packet=packet,
                script_code=f"TEST-{paper.code}-{index}", primary_barcode=f"TEST-BC-{paper.code}-{index}",
                state=Script.State.ASSIGNED if evaluator else Script.State.STORED, page_count=3,
            )
            if evaluator:
                Assignment.objects.create(
                    tenant_id=paper.tenant_id, script=script, evaluator=evaluator,
                    valuation_round=1, status=Assignment.Status.ASSIGNED,
                    due_at=timezone.now() + timedelta(days=3),
                )
