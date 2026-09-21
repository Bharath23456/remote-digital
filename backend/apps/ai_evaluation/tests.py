import hashlib
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from ninja.errors import HttpError

from apps.allocation.models import Assignment
from apps.configuration.models import Paper, Question
from apps.custody.models import Script
from apps.eligibility.models import EligibilityRecord
from apps.evaluators.models import Evaluator, Expertise
from apps.marking.models import Evaluation, QuestionMark
from apps.receiving.models import Dispatch, Packet
from apps.repository.models import ScriptAsset
from apps.rubrics.models import MarkingScheme
from apps.security.crypto import encrypt_secret
from apps.security.models import SecurityPolicy
from apps.tenancy.models import Membership, TenantAccount
from apps.valuation.models import FinalMark, ValuationResult

from .models import AIAnalysis, AIProviderConfiguration, AIQuestionGuide, AIReferenceAsset, AIReferencePack, AIReferenceUpload
from .services import _analysis_input, assign_paper_to_ai, pack_readiness, process_next_analysis, queue_assistive_analysis, synchronize_ai_governance


class AIEvaluationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("bootstrap_demo", verbosity=0)
        cls.actor = User.objects.get(username="admin@admiezo.local")
        tenant_id = Membership.objects.filter(user=cls.actor).values_list("institution__tenant_id", flat=True).first()
        AIProviderConfiguration.objects.create(
            tenant_id=tenant_id,
            api_key_ciphertext=encrypt_secret("test-provider-key"),
            verified_at=timezone.now(),
        )

    def setUp(self):
        original = Paper.objects.first()
        case_id = hashlib.sha256(self._testMethodName.encode()).hexdigest()[:10]
        self.paper = Paper.objects.create(
            tenant_id=original.tenant_id,
            session=original.session,
            subject=original.subject,
            code=f"AI-{case_id}",
            title="AI evaluation test",
            max_marks=10,
            pass_marks=4,
            valuation_rounds=2,
            status=Paper.Status.FROZEN,
        )
        self.question = Question.objects.create(
            tenant_id=self.paper.tenant_id,
            paper=self.paper,
            number="1",
            max_marks=10,
            position=1,
        )
        MarkingScheme.objects.create(
            tenant_id=self.paper.tenant_id,
            paper=self.paper,
            title="AI test scheme",
            evaluation_guidelines="Award marks for a correct, complete answer.",
            examiner_instructions="Evaluate every question.",
            status=MarkingScheme.Status.FROZEN,
            content_digest="a" * 64,
            created_by_id=self.actor.id,
            frozen_by_id=self.actor.id,
            frozen_at=timezone.now(),
        )
        dispatch = Dispatch.objects.create(
            tenant_id=self.paper.tenant_id,
            reference=f"DSP-{case_id}",
            paper=self.paper,
            source_centre="AI Test Centre",
            expected_packets=1,
            expected_scripts=1,
        )
        packet = Packet.objects.create(
            tenant_id=self.paper.tenant_id,
            dispatch=dispatch,
            paper=self.paper,
            barcode=f"PKT-{case_id}",
            expected_scripts=1,
        )
        self.script = Script.objects.create(
            tenant_id=self.paper.tenant_id,
            script_code=f"AS-{case_id}",
            primary_barcode=f"BC-{case_id}",
            packet=packet,
            paper=self.paper,
            state=Script.State.STORED,
            page_count=1,
        )
        self.pack = AIReferencePack.objects.create(tenant_id=self.paper.tenant_id, paper=self.paper)
        for kind, slot in [
            (AIReferenceUpload.Kind.QUESTION_PAPER, 1),
            (AIReferenceUpload.Kind.REFERENCE_ANSWER, 1),
            (AIReferenceUpload.Kind.REFERENCE_ANSWER, 2),
            (AIReferenceUpload.Kind.REFERENCE_ANSWER, 3),
        ]:
            AIReferenceAsset.objects.create(
                tenant_id=self.paper.tenant_id,
                pack=self.pack,
                kind=kind,
                slot=slot,
                storage_key=f"ai-tests/{case_id}/{kind}-{slot}",
                file_name=f"{kind}-{slot}.png",
                mime_type="image/png",
                sha256=str(slot) * 64,
                byte_size=256,
                uploaded_by_id=self.actor.id,
            )
        self.guide = AIQuestionGuide.objects.create(
            tenant_id=self.paper.tenant_id,
            pack=self.pack,
            question=self.question,
            question_text="Explain the configured concept.",
            evaluation_guidance="Award up to ten marks for correctness.",
            max_marks=10,
        )
        self.human = Evaluator.objects.filter(tenant_id=self.paper.tenant_id, status=Evaluator.Status.ACTIVE, is_system_ai=False).first()
        Expertise.objects.update_or_create(
            tenant_id=self.paper.tenant_id,
            evaluator=self.human,
            subject=self.paper.subject,
            defaults={"level": 5, "verified": True, "years_experience": max(self.human.years_experience, 2)},
        )
        if self.human.years_experience < 2:
            self.human.years_experience = 2
            self.human.save(update_fields=["years_experience", "updated_at"])
        EligibilityRecord.objects.update_or_create(
            tenant_id=self.paper.tenant_id,
            evaluator=self.human,
            subject=self.paper.subject,
            defaults={
                "status": EligibilityRecord.Status.ELIGIBLE,
                "qualification_ok": True,
                "experience_ok": True,
                "institution_ok": True,
                "expertise_ok": True,
                "has_conflict": False,
                "is_debarred": False,
                "is_blacklisted": False,
                "expires_on": timezone.localdate() + timedelta(days=90),
            },
        )

    def result(self, *, marks="8", confidence="92"):
        return {
            "overall_confidence": confidence,
            "summary": "The answer addresses the required concepts.",
            "assessments": [
                {
                    "question_id": str(self.question.id),
                    "marks": marks,
                    "confidence": confidence,
                    "feedback": "Good coverage.",
                    "reasoning": "The response matches the reference criteria.",
                }
            ],
        }

    def analysis_input(self, analysis, pack):
        return [self.guide], "test prompt", []

    def set_ai_policy(self, mode, threshold="85"):
        policy, _ = SecurityPolicy.objects.get_or_create(tenant_id=self.paper.tenant_id)
        policy.ai_evaluation_mode = mode
        policy.ai_confidence_threshold = Decimal(threshold)
        policy.ai_model_name = "admiezo-ai-v1"
        policy.save()
        return policy

    def test_reference_pack_requires_exactly_three_answers_and_all_question_guides(self):
        self.assertTrue(pack_readiness(self.pack)["ready"])
        self.pack.assets.filter(kind=AIReferenceUpload.Kind.REFERENCE_ANSWER, slot=3).delete()
        readiness = pack_readiness(self.pack)
        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["reference_answers"], 2)

    def test_autonomous_governance_controls_entitlement_and_system_evaluator(self):
        tenant_id = self.paper.tenant_id
        account = TenantAccount.objects.get(root_institution__tenant_id=tenant_id)
        admin_membership = Membership.objects.get(user=self.actor, institution__tenant_id=tenant_id)
        account.enabled_modules = [item for item in account.enabled_modules if item != "ai_evaluation"]
        account.save(update_fields=["enabled_modules"])
        # A legacy bootstrap left privileged memberships empty; the first AI sync
        # then turned that into an AI-only entitlement and hid every other module.
        admin_membership.enabled_modules = []
        admin_membership.save(update_fields=["enabled_modules"])

        synchronize_ai_governance(tenant_id=tenant_id, mode=SecurityPolicy.AIEvaluationMode.AUTONOMOUS)
        account.refresh_from_db()
        admin_membership.refresh_from_db()
        system_evaluator = Evaluator.objects.get(tenant_id=tenant_id, evaluator_code="AI-ADMIEZO")
        self.assertIn("ai_evaluation", account.enabled_modules)
        self.assertIn("ai_evaluation", admin_membership.enabled_modules)
        self.assertEqual(set(admin_membership.enabled_modules), set(account.enabled_modules))
        self.assertTrue(system_evaluator.is_system_ai)
        self.assertEqual(system_evaluator.status, Evaluator.Status.ACTIVE)

        synchronize_ai_governance(tenant_id=tenant_id, mode=SecurityPolicy.AIEvaluationMode.DISABLED)
        account.refresh_from_db()
        admin_membership.refresh_from_db()
        system_evaluator.refresh_from_db()
        self.assertNotIn("ai_evaluation", account.enabled_modules)
        self.assertNotIn("ai_evaluation", admin_membership.enabled_modules)
        self.assertEqual(set(admin_membership.enabled_modules), set(account.enabled_modules))
        self.assertEqual(system_evaluator.status, Evaluator.Status.INACTIVE)

    def test_ai_requests_are_blocked_until_the_backend_key_is_configured(self):
        AIProviderConfiguration.objects.all().delete()
        self.set_ai_policy(SecurityPolicy.AIEvaluationMode.AUTONOMOUS)
        with self.assertRaisesMessage(HttpError, "ADMIEZO AI Assistant is not configured or its API key is invalid"):
            assign_paper_to_ai(tenant_id=self.paper.tenant_id, actor_id=self.actor.id, paper=self.paper, maximum_scripts=1)

    def test_assistive_analysis_never_writes_evaluation_marks(self):
        self.set_ai_policy(SecurityPolicy.AIEvaluationMode.ASSISTIVE)
        assignment = Assignment.objects.create(
            tenant_id=self.paper.tenant_id,
            script=self.script,
            evaluator=self.human,
            valuation_round=1,
            due_at=timezone.now() + timedelta(days=3),
        )
        with patch("apps.ai_evaluation.services.provider_status", return_value={"available": True}):
            analysis, created = queue_assistive_analysis(tenant_id=self.paper.tenant_id, actor_id=self.actor.id, assignment=assignment)
        self.assertTrue(created)
        with patch("apps.ai_evaluation.services._analysis_input", side_effect=self.analysis_input), patch("apps.ai_evaluation.services.AdmiezoAIClient.evaluate", return_value=self.result()):
            processed = process_next_analysis()
        self.assertEqual(processed.id, analysis.id)
        self.assertEqual(processed.status, AIAnalysis.Status.COMPLETED)
        self.assertEqual(processed.question_assessments.count(), 1)
        self.assertFalse(Evaluation.objects.filter(assignment=assignment).exists())
        self.assertFalse(QuestionMark.objects.filter(evaluation__assignment=assignment).exists())

    def test_assistant_input_uses_only_masked_script_assets_and_no_identity_values(self):
        assignment = Assignment.objects.create(
            tenant_id=self.paper.tenant_id,
            script=self.script,
            evaluator=self.human,
            valuation_round=1,
            due_at=timezone.now() + timedelta(days=3),
        )
        analysis = AIAnalysis.objects.create(
            tenant_id=self.paper.tenant_id,
            assignment=assignment,
            trigger=AIAnalysis.Trigger.ASSISTIVE,
            model_name="admiezo-ai-v1",
            requested_by_id=self.actor.id,
        )
        ScriptAsset.objects.create(
            tenant_id=self.paper.tenant_id,
            script=self.script,
            kind=ScriptAsset.Kind.MASTER,
            page_number=1,
            storage_key="identity-bearing-raw-page",
            sha256="b" * 64,
            byte_size=256,
            mime_type="image/png",
        )
        ScriptAsset.objects.create(
            tenant_id=self.paper.tenant_id,
            script=self.script,
            kind=ScriptAsset.Kind.EVALUATION,
            page_number=1,
            storage_key="masked-evaluation-page",
            sha256="c" * 64,
            byte_size=256,
            mime_type="image/png",
        )
        read_keys = []

        def read(key):
            read_keys.append(key)
            return b"image"

        with patch("apps.ai_evaluation.services.read_object", side_effect=read):
            guides, prompt, media = _analysis_input(analysis, self.pack)
        self.assertEqual(guides, [self.guide])
        self.assertEqual(len(media), 5)
        self.assertIn("masked-evaluation-page", read_keys)
        self.assertNotIn("identity-bearing-raw-page", read_keys)
        self.assertNotIn(self.script.script_code, prompt)
        self.assertNotIn(self.script.primary_barcode, prompt)

    def test_confident_autonomous_result_is_locked_and_proposes_final_mark(self):
        self.set_ai_policy(SecurityPolicy.AIEvaluationMode.AUTONOMOUS)
        with patch("apps.ai_evaluation.services.provider_status", return_value={"available": True}):
            analysis = assign_paper_to_ai(tenant_id=self.paper.tenant_id, actor_id=self.actor.id, paper=self.paper, maximum_scripts=1)[0]
        with patch("apps.ai_evaluation.services._analysis_input", side_effect=self.analysis_input), patch("apps.ai_evaluation.services.AdmiezoAIClient.evaluate", return_value=self.result(confidence="92")):
            processed = process_next_analysis()
        processed.refresh_from_db()
        assignment = processed.assignment
        assignment.refresh_from_db()
        self.script.refresh_from_db()
        self.assertEqual(processed.id, analysis.id)
        self.assertEqual(processed.status, AIAnalysis.Status.COMPLETED)
        self.assertTrue(assignment.evaluator.is_system_ai)
        self.assertEqual(assignment.status, Assignment.Status.SUBMITTED)
        self.assertEqual(self.script.state, Script.State.SUBMITTED)
        evaluation = Evaluation.objects.get(assignment=assignment)
        self.assertEqual(evaluation.status, Evaluation.Status.LOCKED)
        self.assertEqual(evaluation.total_marks, Decimal("8"))
        self.assertEqual(ValuationResult.objects.get(evaluation=evaluation).total_marks, Decimal("8"))
        self.assertEqual(FinalMark.objects.get(script=self.script).mark, Decimal("8"))

    def test_low_confidence_autonomous_result_routes_only_to_a_human(self):
        self.set_ai_policy(SecurityPolicy.AIEvaluationMode.AUTONOMOUS)
        with patch("apps.ai_evaluation.services.provider_status", return_value={"available": True}):
            analysis = assign_paper_to_ai(tenant_id=self.paper.tenant_id, actor_id=self.actor.id, paper=self.paper, maximum_scripts=1)[0]
        with patch("apps.ai_evaluation.services._analysis_input", side_effect=self.analysis_input), patch("apps.ai_evaluation.services.AdmiezoAIClient.evaluate", return_value=self.result(confidence="70")):
            processed = process_next_analysis()
        processed.refresh_from_db()
        assignment = processed.assignment
        assignment.refresh_from_db()
        self.assertEqual(processed.id, analysis.id)
        self.assertEqual(processed.status, AIAnalysis.Status.LOW_CONFIDENCE)
        self.assertFalse(assignment.evaluator.is_system_ai)
        self.assertEqual(assignment.status, Assignment.Status.REASSIGNED)
        self.assertEqual(assignment.source, "ai_low_confidence")
        self.assertTrue(assignment.is_flagged)
        self.assertFalse(Evaluation.objects.filter(assignment=assignment).exists())
        self.assertFalse(ValuationResult.objects.filter(script=self.script).exists())
