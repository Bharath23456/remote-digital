import hashlib
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from ninja.errors import HttpError

from apps.allocation.models import Assignment
from apps.assignment.services import acquire_lock
from apps.configuration.models import Paper
from apps.core.models import AuditEvent, OutboxEvent
from apps.custody.models import Script
from apps.discrepancy.services import approve_resolution, resolve_case
from apps.evaluators.models import Evaluator
from apps.receiving.models import Dispatch, Packet
from apps.valuation.models import FinalMark, ValuationComparison, ValuationResult
from apps.valuation.services import finalize_valuation, lock_final_mark
from apps.workflow.services import start_workflow, submit_evaluation

from .models import Annotation, Evaluation, QuestionMark
from .services import add_annotation, open_evaluation, save_question_mark


class EndToEndMarkingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap_demo", verbosity=0)
        cls.paper = Paper.objects.filter(code="CS401-A").first()
        cls.evaluators = list(Evaluator.objects.filter(tenant_id=cls.paper.tenant_id, status=Evaluator.Status.ACTIVE)[:2])
        cls.users = [User.objects.get(username=item.email) for item in cls.evaluators]

    def setUp(self):
        token = hashlib.sha256(self._testMethodName.encode()).hexdigest()[:10]
        dispatch = Dispatch.objects.create(tenant_id=self.paper.tenant_id, reference=f"MARK-{token}", paper=self.paper, source_centre="Test", expected_packets=1, expected_scripts=1)
        packet = Packet.objects.create(tenant_id=self.paper.tenant_id, dispatch=dispatch, barcode=f"PKT-MARK-{token}", expected_scripts=1)
        self.script = Script.objects.create(tenant_id=self.paper.tenant_id, script_code=f"AS-MARK-{token}", primary_barcode=f"BC-MARK-{token}", packet=packet, paper=self.paper, state=Script.State.EVALUATING, page_count=3)

    def complete_round(self, round_number, evaluator, user, mark_value):
        assignment = Assignment.objects.create(tenant_id=self.paper.tenant_id, script=self.script, evaluator=evaluator, valuation_round=round_number, status=Assignment.Status.IN_PROGRESS, due_at=timezone.now() + timedelta(days=2))
        _, token = acquire_lock(tenant_id=self.paper.tenant_id, actor_id=user.id, assignment=assignment, evaluator=evaluator, expected_version=assignment.version)
        evaluation = open_evaluation(tenant_id=self.paper.tenant_id, actor_id=user.id, assignment=assignment, evaluator=evaluator)
        workflow = start_workflow(tenant_id=self.paper.tenant_id, actor_id=user.id, assignment=assignment)
        first_mark = None
        for question in self.paper.questions.all():
            first_mark, evaluation = save_question_mark(
                tenant_id=self.paper.tenant_id,
                actor_id=user.id,
                evaluation_id=evaluation.id,
                evaluator=evaluator,
                expected_version=evaluation.version,
                lock_token=token,
                question=question,
                values={"marks": mark_value, "outcome": QuestionMark.Outcome.EVALUATED, "adjustment": QuestionMark.Adjustment.NONE},
            )
        evaluation, replayed = submit_evaluation(tenant_id=self.paper.tenant_id, actor_id=user.id, evaluation_id=evaluation.id, evaluator=evaluator, evaluation_version=evaluation.version, workflow_version=workflow.version, lock_token=token, idempotency_key=f"submit-round-{round_number}-{self._testMethodName}")
        self.assertFalse(replayed)
        result, replayed = finalize_valuation(tenant_id=self.paper.tenant_id, actor_id=user.id, evaluation_id=evaluation.id, evaluator=evaluator, idempotency_key=f"finalize-round-{round_number}-{self._testMethodName}")
        self.assertFalse(replayed)
        return assignment, evaluation, result, first_mark

    def test_append_only_marks_annotations_and_idempotent_submission(self):
        evaluator, user = self.evaluators[0], self.users[0]
        assignment = Assignment.objects.create(tenant_id=self.paper.tenant_id, script=self.script, evaluator=evaluator, valuation_round=1, status=Assignment.Status.IN_PROGRESS, due_at=timezone.now() + timedelta(days=2))
        _, token = acquire_lock(tenant_id=self.paper.tenant_id, actor_id=user.id, assignment=assignment, evaluator=evaluator, expected_version=assignment.version)
        evaluation = open_evaluation(tenant_id=self.paper.tenant_id, actor_id=user.id, assignment=assignment, evaluator=evaluator)
        workflow = start_workflow(tenant_id=self.paper.tenant_id, actor_id=user.id, assignment=assignment)
        question = self.paper.questions.first()
        first, evaluation = save_question_mark(tenant_id=self.paper.tenant_id, actor_id=user.id, evaluation_id=evaluation.id, evaluator=evaluator, expected_version=evaluation.version, lock_token=token, question=question, values={"marks": 12, "outcome": "evaluated", "adjustment": "none"})
        corrected, evaluation = save_question_mark(tenant_id=self.paper.tenant_id, actor_id=user.id, evaluation_id=evaluation.id, evaluator=evaluator, expected_version=evaluation.version, lock_token=token, question=question, values={"marks": 15, "outcome": "evaluated", "adjustment": "none"})
        self.assertEqual(corrected.supersedes_id, first.id)
        self.assertEqual(QuestionMark.objects.filter(evaluation=evaluation, question=question).count(), 2)
        annotation, evaluation = add_annotation(tenant_id=self.paper.tenant_id, actor_id=user.id, evaluation_id=evaluation.id, evaluator=evaluator, expected_version=evaluation.version, lock_token=token, page_number=1, question=question, kind=Annotation.Kind.TICK, geometry={"x": 0.25, "y": 0.5}, style={"color": "red"}, symbol="")
        self.assertEqual(annotation.geometry["x"], 0.25)
        with self.assertRaises(HttpError):
            save_question_mark(tenant_id=self.paper.tenant_id, actor_id=user.id, evaluation_id=evaluation.id, evaluator=evaluator, expected_version=evaluation.version - 1, lock_token=token, question=question, values={"marks": 16})
        for remaining in self.paper.questions.exclude(id=question.id):
            _, evaluation = save_question_mark(tenant_id=self.paper.tenant_id, actor_id=user.id, evaluation_id=evaluation.id, evaluator=evaluator, expected_version=evaluation.version, lock_token=token, question=remaining, values={"marks": 15, "outcome": "evaluated", "adjustment": "none"})
        original_version = evaluation.version
        submitted, replayed = submit_evaluation(tenant_id=self.paper.tenant_id, actor_id=user.id, evaluation_id=evaluation.id, evaluator=evaluator, evaluation_version=original_version, workflow_version=workflow.version, lock_token=token, idempotency_key=f"submit-{self._testMethodName}")
        self.assertFalse(replayed)
        duplicate, replayed = submit_evaluation(tenant_id=self.paper.tenant_id, actor_id=user.id, evaluation_id=evaluation.id, evaluator=evaluator, evaluation_version=original_version, workflow_version=workflow.version, lock_token=token, idempotency_key=f"submit-{self._testMethodName}")
        self.assertTrue(replayed)
        self.assertEqual(duplicate.id, submitted.id)
        self.assertTrue(AuditEvent.objects.filter(action="workflow.evaluation.submitted", aggregate_id=str(evaluation.id)).exists())
        self.assertTrue(OutboxEvent.objects.filter(topic="workflow.evaluation.submitted", aggregate_id=str(evaluation.id)).exists())

    def test_two_blind_rounds_create_discrepancy_and_approved_final_lock(self):
        _, _, first_result, _ = self.complete_round(1, self.evaluators[0], self.users[0], 18)
        _, _, second_result, _ = self.complete_round(2, self.evaluators[1], self.users[1], 10)
        self.assertEqual(ValuationResult.objects.filter(script=self.script).count(), 2)
        comparison = ValuationComparison.objects.get(script=self.script)
        self.assertEqual(comparison.first_result_id, first_result.id)
        self.assertEqual(comparison.second_result_id, second_result.id)
        self.assertEqual(comparison.status, ValuationComparison.Status.DISCREPANCY)
        case = comparison.discrepancy_case
        resolution, case, final = resolve_case(tenant_id=self.paper.tenant_id, actor_id=self.users[0].id, case_id=case.id, expected_version=case.version, method="rule_based", final_mark=70, reason="Chief examiner selected the evidence-based reconciled mark.", calculation={"rule": "chief_examiner"})
        _, case, final = approve_resolution(tenant_id=self.paper.tenant_id, actor_id=self.users[1].id, resolution_id=resolution.id)
        self.assertEqual(final.status, FinalMark.Status.APPROVED)
        locked, replayed = lock_final_mark(tenant_id=self.paper.tenant_id, actor_id=self.users[0].id, final_mark_id=final.id, expected_version=final.version, idempotency_key=f"lock-{self._testMethodName}")
        self.assertFalse(replayed)
        self.assertEqual(locked.status, FinalMark.Status.LOCKED)
        self.script.refresh_from_db()
        self.assertEqual(self.script.state, Script.State.FINALIZED)

    def test_evaluation_core_models_contain_no_candidate_pii(self):
        forbidden = {"candidate_name", "register_number", "usn", "college", "candidate_email", "candidate_phone", "photo", "signature"}
        for model in (Evaluation, QuestionMark, Annotation, ValuationResult, ValuationComparison, FinalMark):
            self.assertFalse(forbidden.intersection(field.name for field in model._meta.fields))
