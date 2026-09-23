import hashlib
import json
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from ninja.errors import HttpError

from apps.core.idempotency import begin_idempotent, complete_idempotent
from apps.core.services import record_event
from apps.custody.models import Script
from apps.custody.services import transition_script
from apps.marking.models import Evaluation
from apps.marking.services import latest_marks
from apps.security.models import SecurityPolicy
from apps.workflow.models import EvaluationWorkflow, WorkflowTransition
from apps.workflow.services import WORKFLOW_TRANSITIONS

from .models import FinalMark, RevealAuthorization, ValuationComparison, ValuationResult


FINAL_MARK_TRANSITIONS = {
    FinalMark.Status.PROPOSED: {FinalMark.Status.APPROVED},
    FinalMark.Status.APPROVED: {FinalMark.Status.LOCKED},
}


def _snapshot(evaluation):
    return {
        f"{item.question_id}:{item.sub_question}": {
            "question_id": str(item.question_id),
            "sub_question": item.sub_question,
            "marks": str(item.marks),
            "outcome": item.outcome,
            "adjustment": item.adjustment,
        }
        for item in latest_marks(evaluation)
    }


def _result_checksum(evaluation, snapshot):
    payload = {"evaluation": evaluation.checksum, "round": evaluation.assignment.valuation_round, "marks": snapshot}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _differences(results):
    keys = set().union(*(item.question_snapshot.keys() for item in results))
    differences = {}
    for key in keys:
        values = [Decimal(item.question_snapshot.get(key, {}).get("marks", "0")) for item in results]
        differences[key] = {"marks": [str(value) for value in values], "difference": str(max(values) - min(values))}
    totals = [item.total_marks for item in results]
    return differences, max(totals) - min(totals)


def required_valuation_rounds(script, first_result=None):
    ai_mode = SecurityPolicy.objects.filter(tenant_id=script.tenant_id).values_list("ai_evaluation_mode", flat=True).first()
    if ai_mode == SecurityPolicy.AIEvaluationMode.AUTONOMOUS:
        return 1
    if script.paper.valuation_rounds != 1:
        return script.paper.valuation_rounds
    threshold = script.paper.rules.get("second_valuation_mark_threshold")
    if threshold is None or threshold == "":
        return 1
    if first_result is None:
        first_result = ValuationResult.objects.filter(script=script, valuation_round=1, is_locked=True).first()
    return 2 if first_result and first_result.total_marks > Decimal(str(threshold)) else 1


def _propose_final_mark(*, tenant_id, actor_id, script, comparison, results):
    existing = FinalMark.objects.filter(script=script).first()
    if existing:
        return existing
    rule = script.paper.rules.get("final_mark_rule", FinalMark.Rule.AVERAGE)
    totals = [item.total_marks for item in results]
    if rule == FinalMark.Rule.BEST:
        mark = max(totals)
    elif rule == FinalMark.Rule.RULE_BASED:
        ordered = sorted(totals)
        mark = ordered[len(ordered) // 2]
    else:
        rule = FinalMark.Rule.AVERAGE
        mark = sum(totals, Decimal("0")) / Decimal(len(totals))
    item = FinalMark.objects.create(
        tenant_id=tenant_id,
        script=script,
        comparison=comparison,
        rule=rule,
        mark=mark.quantize(Decimal("0.01")),
        calculation={"valuation_result_ids": [str(result.id) for result in results], "totals": [str(value) for value in totals]},
        proposed_by_id=actor_id,
    )
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="valuation.final_mark.proposed", aggregate="FinalMark", aggregate_id=item.id, payload={"script_id": str(script.id), "rule": rule, "mark": str(item.mark)})
    return item


def _compare_after_result(*, tenant_id, actor_id, result):
    results = list(ValuationResult.objects.filter(script=result.script, is_locked=True).order_by("valuation_round"))
    required_rounds = required_valuation_rounds(result.script, results[0] if results else None)
    if len(results) == 1 and required_rounds == 1:
        _propose_final_mark(tenant_id=tenant_id, actor_id=actor_id, script=result.script, comparison=None, results=results)
        return None
    if len(results) < 2:
        return None
    first, second = results[:2]
    comparison = ValuationComparison.objects.filter(script=result.script, first_result=first, second_result=second).first()
    if not comparison:
        differences, total_difference = _differences([first, second])
        threshold = result.script.paper.discrepancy_threshold
        requires_third = required_rounds == 3
        status = ValuationComparison.Status.THIRD_REQUIRED if requires_third else (ValuationComparison.Status.DISCREPANCY if total_difference > threshold else ValuationComparison.Status.WITHIN_THRESHOLD)
        comparison = ValuationComparison.objects.create(
            tenant_id=tenant_id,
            script=result.script,
            first_result=first,
            second_result=second,
            question_differences=differences,
            total_difference=total_difference,
            percentage_difference=(total_difference * Decimal("100") / max(result.script.paper.max_marks, Decimal("1"))).quantize(Decimal("0.001")),
            threshold=threshold,
            status=status,
            requires_third_valuation=requires_third,
        )
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="valuation.comparison.created", aggregate="ValuationComparison", aggregate_id=comparison.id, payload={"script_id": str(result.script_id), "difference": str(total_difference), "threshold": str(threshold), "status": status})
        if status == ValuationComparison.Status.WITHIN_THRESHOLD:
            _propose_final_mark(tenant_id=tenant_id, actor_id=actor_id, script=result.script, comparison=comparison, results=[first, second])
        elif total_difference > threshold:
            from apps.discrepancy.services import create_case

            create_case(tenant_id=tenant_id, actor_id=actor_id, comparison=comparison)
    elif len(results) >= 3 and comparison.requires_third_valuation and not comparison.third_result_id:
        third = results[2]
        differences, total_difference = _differences([first, second, third])
        comparison.third_result = third
        comparison.question_differences = differences
        comparison.total_difference = total_difference
        comparison.percentage_difference = (total_difference * Decimal("100") / max(result.script.paper.max_marks, Decimal("1"))).quantize(Decimal("0.001"))
        has_case = hasattr(comparison, "discrepancy_case")
        comparison.status = ValuationComparison.Status.RECONCILED if has_case else (ValuationComparison.Status.DISCREPANCY if total_difference > comparison.threshold else ValuationComparison.Status.WITHIN_THRESHOLD)
        comparison.requires_third_valuation = False
        comparison.save()
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="valuation.comparison.third_completed", aggregate="ValuationComparison", aggregate_id=comparison.id, payload={"third_result_id": str(third.id), "difference": str(total_difference)})
        if has_case:
            from apps.discrepancy.services import resolve_case

            case = comparison.discrepancy_case
            ordered = sorted(item.total_marks for item in (first, second, third))
            resolve_case(
                tenant_id=tenant_id,
                actor_id=actor_id,
                case_id=case.id,
                expected_version=case.version,
                method="third_valuation",
                final_mark=ordered[1],
                reason="Third valuation completed; median valuation selected by configured reconciliation rule.",
                calculation={"valuation_result_ids": [str(first.id), str(second.id), str(third.id)], "totals": [str(item.total_marks) for item in (first, second, third)]},
            )
        elif total_difference > comparison.threshold:
            from apps.discrepancy.services import create_case

            create_case(tenant_id=tenant_id, actor_id=actor_id, comparison=comparison)
        else:
            _propose_final_mark(tenant_id=tenant_id, actor_id=actor_id, script=result.script, comparison=comparison, results=[first, second, third])
    return comparison


def reconcile_single_round_results(*, apply=False):
    eligible = []
    for result in ValuationResult.objects.filter(is_locked=True, valuation_round=1, script__paper__valuation_rounds=1).select_related("script__paper").order_by("created_at"):
        if required_valuation_rounds(result.script, result) != 1:
            continue
        if not FinalMark.objects.filter(script=result.script).exists():
            eligible.append(result)
    if apply:
        for result in eligible:
            with transaction.atomic():
                script = Script.objects.select_for_update().select_related("paper").get(id=result.script_id)
                if not FinalMark.objects.filter(script=script).exists() and required_valuation_rounds(script, result) == 1:
                    _propose_final_mark(tenant_id=script.tenant_id, actor_id=result.locked_by_id, script=script, comparison=None, results=[result])
    return len(eligible)


def finalize_valuation(*, tenant_id, actor_id, evaluation_id, evaluator, idempotency_key):
    with transaction.atomic():
        evaluation = Evaluation.objects.select_for_update().select_related("assignment__script__paper", "scheme").filter(id=evaluation_id, tenant_id=tenant_id, assignment__evaluator=evaluator).first()
        if not evaluation:
            raise HttpError(404, "Evaluation not found")
        existing_result = ValuationResult.objects.filter(evaluation=evaluation).first()
        if existing_result:
            return existing_result, True
        payload = {"evaluation_id": str(evaluation.id), "checksum": evaluation.checksum}
        idempotency, existing = begin_idempotent(tenant_id=tenant_id, scope="valuation.finalize", key=idempotency_key, payload=payload)
        if existing:
            return ValuationResult.objects.get(id=existing), True
        if evaluation.status != Evaluation.Status.SUBMITTED or not evaluation.checksum:
            raise HttpError(409, "Only a submitted evaluation can become a valuation result")
        snapshot = _snapshot(evaluation)
        result = ValuationResult.objects.create(
            tenant_id=tenant_id,
            evaluation=evaluation,
            script=evaluation.assignment.script,
            valuation_round=evaluation.assignment.valuation_round,
            total_marks=evaluation.total_marks,
            question_snapshot=snapshot,
            checksum=_result_checksum(evaluation, snapshot),
            is_locked=True,
            locked_by_id=actor_id,
            locked_at=timezone.now(),
        )
        evaluation.status = Evaluation.Status.LOCKED
        evaluation.version += 1
        evaluation.save()
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="valuation.result.locked", aggregate="ValuationResult", aggregate_id=result.id, payload={"script_id": str(result.script_id), "round": result.valuation_round, "checksum": result.checksum})
        _compare_after_result(tenant_id=tenant_id, actor_id=actor_id, result=result)
        complete_idempotent(idempotency, result.id)
    return result, False


@transaction.atomic
def request_reveal(*, tenant_id, actor_id, script, from_round, to_round, purpose, expires_at):
    if from_round == to_round or len(purpose.strip()) < 8 or expires_at <= timezone.now():
        raise HttpError(422, "Reveal purpose, distinct rounds, and future expiry are required")
    item = RevealAuthorization.objects.create(tenant_id=tenant_id, script=script, from_round=from_round, to_round=to_round, purpose=purpose.strip(), requested_by_id=actor_id, expires_at=expires_at)
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="valuation.reveal.requested", aggregate="RevealAuthorization", aggregate_id=item.id, payload={"script_id": str(script.id), "from_round": from_round, "to_round": to_round})
    return item


@transaction.atomic
def approve_reveal(*, tenant_id, actor_id, reveal_id):
    item = RevealAuthorization.objects.select_for_update().filter(id=reveal_id, tenant_id=tenant_id).first()
    if not item or item.approved_at or item.expires_at <= timezone.now():
        raise HttpError(409, "Reveal request is missing, expired, or already approved")
    if item.requested_by_id == actor_id:
        raise HttpError(409, "Reveal requester cannot approve the same request")
    item.approved_by_id = actor_id
    item.approved_at = timezone.now()
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="valuation.reveal.approved", aggregate="RevealAuthorization", aggregate_id=item.id, payload={"script_id": str(item.script_id)})
    return item


@transaction.atomic
def approve_final_mark(*, tenant_id, actor_id, final_mark_id, expected_version):
    item = FinalMark.objects.select_for_update().filter(id=final_mark_id, tenant_id=tenant_id).first()
    if not item or item.version != expected_version:
        raise HttpError(409, "Final mark is missing or stale")
    if FinalMark.Status.APPROVED not in FINAL_MARK_TRANSITIONS.get(item.status, set()):
        raise HttpError(409, "Final mark cannot be approved from its current state")
    if item.proposed_by_id == actor_id:
        raise HttpError(409, "Final mark proposer cannot approve it")
    item.status = FinalMark.Status.APPROVED
    item.approved_by_id = actor_id
    item.approved_at = timezone.now()
    item.version += 1
    item.save()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="valuation.final_mark.approved", aggregate="FinalMark", aggregate_id=item.id, payload={"script_id": str(item.script_id), "mark": str(item.mark)})
    return item


def lock_final_mark(*, tenant_id, actor_id, final_mark_id, expected_version, idempotency_key):
    with transaction.atomic():
        item = FinalMark.objects.select_for_update().select_related("script").filter(id=final_mark_id, tenant_id=tenant_id).first()
        if not item:
            raise HttpError(404, "Final mark not found")
        payload = {"final_mark_id": str(item.id), "version": expected_version}
        idempotency, existing = begin_idempotent(tenant_id=tenant_id, scope="final_mark.lock", key=idempotency_key, payload=payload)
        if existing:
            return FinalMark.objects.get(id=existing), True
        if item.version != expected_version or FinalMark.Status.LOCKED not in FINAL_MARK_TRANSITIONS.get(item.status, set()):
            raise HttpError(409, "Final mark is stale or not approved")
        checksum = hashlib.sha256(f"{item.script_id}:{item.mark}:{item.rule}:{item.approved_by_id}".encode()).hexdigest()
        item.status = FinalMark.Status.LOCKED
        item.locked_by_id = actor_id
        item.locked_at = timezone.now()
        item.checksum = checksum
        item.version += 1
        item.save()
        script = item.script
        if script.state in (Script.State.SUBMITTED, Script.State.REVIEW, Script.State.MODERATED, Script.State.REVALUATED):
            transition_script(tenant_id=tenant_id, actor_id=actor_id, script_id=script.id, expected_version=script.version, to_state=Script.State.FINALIZED, location="final-mark-lock", metadata={"final_mark_id": str(item.id), "checksum": checksum})
        for workflow in EvaluationWorkflow.objects.select_for_update().filter(assignment__script=script).exclude(state=EvaluationWorkflow.State.FINALIZED):
            if EvaluationWorkflow.State.FINALIZED in WORKFLOW_TRANSITIONS.get(workflow.state, set()):
                previous = workflow.state
                workflow.state = EvaluationWorkflow.State.FINALIZED
                workflow.finalized_at = timezone.now()
                workflow.version += 1
                workflow.save()
                WorkflowTransition.objects.create(tenant_id=tenant_id, workflow=workflow, from_state=previous, to_state=workflow.state, actor_id=actor_id, metadata={"final_mark_id": str(item.id)})
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="valuation.final_mark.locked", aggregate="FinalMark", aggregate_id=item.id, payload={"script_id": str(item.script_id), "checksum": checksum})
        complete_idempotent(idempotency, item.id)
    return item, False
