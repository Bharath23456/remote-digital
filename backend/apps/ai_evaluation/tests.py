import hashlib
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from ninja.errors import HttpError

from apps.allocation.models import Assignment
from apps.configuration.models import Paper, Question
from apps.core.models import AuditEvent
from apps.core.testing import create_operational_fixtures
from apps.custody.models import Script
from apps.eligibility.models import EligibilityRecord
from apps.evaluators.models import Evaluator, Expertise
from apps.marking.models import Evaluation, QuestionMark
from apps.receiving.models import Dispatch, Packet
from apps.repository.models import ScriptAsset
from apps.repository.storage import ObjectMetadata
from apps.rubrics.models import MarkingScheme
from apps.security.crypto import encrypt_secret
from apps.security.models import SecurityPolicy
from apps.tenancy.models import Membership, TenantAccount
from apps.valuation.models import FinalMark, ValuationResult

from .models import AIAnalysis, AIProviderConfiguration, AIQuestionGuide, AIReferenceAsset, AIReferencePack, AIReferenceUpload
from .api import analysis_data
from .provider import AdmiezoAIClient, AdmiezoAIError, AdmiezoAITransientError, _provider_model
from .services import (
    _analysis_input,
    assign_all_ready_scripts_to_ai,
    assign_paper_to_ai,
    finalize_reference_upload,
    pack_readiness,
    process_next_analysis,
    provider_status,
    queue_assistive_analysis,
    synchronize_ai_governance,
)


class AdmiezoAIProviderTests(SimpleTestCase):
    @override_settings(ADMIEZO_AI_PROVIDER_MODEL="gemini-3.8-flash")
    def test_assistant_alias_resolves_to_configured_provider_model(self):
        self.assertEqual(_provider_model("admiezo-ai-v1"), "gemini-3.8-flash")
        self.assertEqual(_provider_model("gemini-3.7-flash"), "gemini-3.7-flash")
        self.assertEqual(_provider_model("admiezo-unknown"), "admiezo-unknown")

    def test_model_must_support_configured_provider_generation(self):
        from io import BytesIO

        supported = BytesIO(b'{"supportedGenerationMethods":["generateContent"]}')
        supported.status = 200
        unsupported = BytesIO(b'{"supportedGenerationMethods":["embedContent"]}')
        unsupported.status = 200
        client = AdmiezoAIClient(api_key="test-key")
        with patch("apps.ai_evaluation.provider.urlopen", return_value=supported):
            client.validate_model("provider-model")
        with patch("apps.ai_evaluation.provider.urlopen", return_value=unsupported):
            with self.assertRaisesMessage(AdmiezoAIError, "does not support evaluation"):
                client.validate_model("provider-model")

    def test_provider_quota_error_is_readable(self):
        from io import BytesIO
        from urllib.error import HTTPError

        response = BytesIO(b'{"error":{"code":429,"message":"Quota exceeded. Check billing details.\\nMore information follows."}}')
        failure = HTTPError("https://provider.example", 429, "quota", {}, response)
        client = AdmiezoAIClient(api_key="test-key")
        with patch("apps.ai_evaluation.provider.urlopen", side_effect=failure):
            with self.assertRaisesMessage(AdmiezoAIError, "request failed (429): Quota exceeded. Check billing details."):
                client.extract_answer(model="provider-model", question={"number": "1", "text": "Question"}, media=[])


class AIWorkerTransactionTests(TransactionTestCase):
    def test_failed_analysis_records_event_inside_transaction(self):
        call_command("bootstrap_demo", verbosity=0)
        create_operational_fixtures()
        assignment = Assignment.objects.first()
        analysis = AIAnalysis.objects.create(
            tenant_id=assignment.tenant_id,
            assignment=assignment,
            trigger=AIAnalysis.Trigger.ASSISTIVE,
            model_name="admiezo-ai-v1",
            requested_by_id=User.objects.get(username="admin@admiezo.local").id,
        )

        with patch("apps.ai_evaluation.services.process_analysis", side_effect=AdmiezoAIError("provider unavailable")):
            processed = process_next_analysis()

        self.assertEqual(processed.status, AIAnalysis.Status.FAILED)
        self.assertEqual(processed.error_message, "provider unavailable")
        self.assertTrue(AuditEvent.objects.filter(action="ai_evaluation.analysis.failed", aggregate_id=str(analysis.id)).exists())


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
        self.backup = Evaluator.objects.filter(tenant_id=self.paper.tenant_id, status=Evaluator.Status.ACTIVE, is_system_ai=False).exclude(id=self.human.id).first()
        Expertise.objects.update_or_create(tenant_id=self.paper.tenant_id, evaluator=self.backup, subject=self.paper.subject, defaults={"level": 5, "verified": True, "years_experience": self.backup.years_experience})
        EligibilityRecord.objects.update_or_create(
            tenant_id=self.paper.tenant_id,
            evaluator=self.backup,
            subject=self.paper.subject,
            defaults={"status": EligibilityRecord.Status.ELIGIBLE, "qualification_ok": True, "experience_ok": True, "institution_ok": True, "expertise_ok": True, "has_conflict": False, "is_debarred": False, "is_blacklisted": False, "expires_on": timezone.localdate() + timedelta(days=90)},
        )

    def test_transient_provider_model_check_keeps_verified_configuration_available(self):
        with patch("apps.ai_evaluation.services.cache.get", return_value=None), patch("apps.ai_evaluation.services.cache.set"), patch("apps.ai_evaluation.services.AdmiezoAIClient.validate_model", side_effect=AdmiezoAITransientError("Provider model check temporarily failed (503)")):
            status = provider_status(self.paper.tenant_id, "admiezo-ai-v1")
        self.assertTrue(status["available"])
        self.assertTrue(status["valid"])
        self.assertIn("temporarily failed (503)", status["message"])

    def test_rejected_provider_model_remains_unavailable(self):
        with patch("apps.ai_evaluation.services.cache.get", return_value=None), patch("apps.ai_evaluation.services.cache.set"), patch("apps.ai_evaluation.services.AdmiezoAIClient.validate_model", side_effect=AdmiezoAIError("Model unsupported")):
            status = provider_status(self.paper.tenant_id, "unsupported-model")
        self.assertFalse(status["available"])
        self.assertEqual(status["message"], "Model unsupported")

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

    def analysis_input(self, analysis, pack, deadline=None):
        return [self.guide], "test prompt", []

    def set_ai_policy(self, mode, threshold="85"):
        policy, _ = SecurityPolicy.objects.get_or_create(tenant_id=self.paper.tenant_id)
        policy.ai_evaluation_mode = mode
        policy.ai_confidence_threshold = Decimal(threshold)
        policy.ai_model_name = "admiezo-ai-v1"
        policy.save()
        return policy

    def test_reference_pack_accepts_saved_question_guidance_without_uploads(self):
        self.pack.assets.all().delete()
        readiness = pack_readiness(self.pack)
        self.assertTrue(readiness["ready"])
        self.assertTrue(readiness["guide_only_ready"])
        self.guide.evaluation_guidance = ""
        self.guide.save(update_fields=["evaluation_guidance"])
        readiness = pack_readiness(self.pack)
        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["missing_evaluation_guidance"], 1)

    def test_reference_assets_remain_valid_when_guidance_is_blank(self):
        self.guide.evaluation_guidance = ""
        self.guide.save(update_fields=["evaluation_guidance"])
        self.assertTrue(pack_readiness(self.pack)["ready"])
        self.pack.assets.filter(kind=AIReferenceUpload.Kind.REFERENCE_ANSWER, slot=3).delete()
        readiness = pack_readiness(self.pack)
        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["reference_answers"], 2)

    @patch("apps.ai_evaluation.services.delete_object")
    @patch("apps.ai_evaluation.services.read_object_metadata")
    def test_reference_upload_finalization_uses_storage_mime_type(self, metadata, delete_object):
        self.pack.assets.filter(kind=AIReferenceUpload.Kind.REFERENCE_ANSWER, slot=3).delete()
        uploads = [
            AIReferenceUpload.objects.create(
                tenant_id=self.paper.tenant_id,
                pack=self.pack,
                kind=AIReferenceUpload.Kind.REFERENCE_ANSWER,
                slot=3,
                storage_key="ai-tests/new-reference-answer.png",
                file_name="new-reference-answer.png",
                content_type="image/png",
                maximum_bytes=1024,
                expires_at=timezone.now() + timedelta(minutes=5),
                created_by_id=self.actor.id,
            ),
            AIReferenceUpload.objects.create(
                tenant_id=self.paper.tenant_id,
                pack=self.pack,
                kind=AIReferenceUpload.Kind.QUESTION_PAPER,
                slot=1,
                storage_key="ai-tests/replacement-question.pdf",
                file_name="replacement-question.pdf",
                content_type="application/pdf",
                maximum_bytes=2048,
                expires_at=timezone.now() + timedelta(minutes=5),
                created_by_id=self.actor.id,
            ),
        ]
        metadata.side_effect = [
            ObjectMetadata("b" * 64, 512, "image/png"),
            ObjectMetadata("c" * 64, 1024, "application/pdf"),
        ]

        for upload in uploads:
            with self.subTest(kind=upload.kind, slot=upload.slot):
                asset = finalize_reference_upload(
                    tenant_id=self.paper.tenant_id,
                    actor_id=self.actor.id,
                    upload_id=upload.id,
                    expected_version=upload.version,
                )
                upload.refresh_from_db()
                self.assertEqual(asset.mime_type, upload.content_type)
                self.assertEqual(upload.status, AIReferenceUpload.Status.COMPLETED)
                self.assertEqual(upload.byte_size, asset.byte_size)

        delete_object.assert_called_once()

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
        with patch("apps.ai_evaluation.services._analysis_input", side_effect=self.analysis_input), patch("apps.ai_evaluation.services.AdmiezoAIClient.validate_model"), patch("apps.ai_evaluation.services.AdmiezoAIClient.extract_answer", return_value={"answer_text": "test answer"}), patch("apps.ai_evaluation.services.AdmiezoAIClient.evaluate", return_value=self.result()):
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
        with patch("apps.ai_evaluation.services._analysis_input", side_effect=self.analysis_input), patch("apps.ai_evaluation.services.AdmiezoAIClient.validate_model"), patch("apps.ai_evaluation.services.AdmiezoAIClient.extract_answer", return_value={"answer_text": "test answer"}), patch("apps.ai_evaluation.services.AdmiezoAIClient.evaluate", return_value=self.result(confidence="92")):
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

    def test_bulk_autonomous_assignment_queues_every_ready_script(self):
        self.pack.assets.all().delete()
        self.set_ai_policy(SecurityPolicy.AIEvaluationMode.AUTONOMOUS)
        with patch("apps.ai_evaluation.services.provider_status", return_value={"available": True}):
            analyses = assign_all_ready_scripts_to_ai(
                tenant_id=self.paper.tenant_id,
                actor_id=self.actor.id,
                maximum_scripts=5000,
            )

        self.assertEqual(len(analyses), 1)
        assignment = analyses[0].assignment
        self.assertEqual(assignment.script_id, self.script.id)
        self.assertTrue(assignment.evaluator.is_system_ai)
        self.assertEqual(assignment.source, "ai_autonomous")

    def test_low_confidence_autonomous_result_routes_only_to_a_human(self):
        self.set_ai_policy(SecurityPolicy.AIEvaluationMode.AUTONOMOUS)
        with patch("apps.ai_evaluation.services.provider_status", return_value={"available": True}):
            analysis = assign_paper_to_ai(tenant_id=self.paper.tenant_id, actor_id=self.actor.id, paper=self.paper, maximum_scripts=1)[0]
        with patch("apps.ai_evaluation.services._analysis_input", side_effect=self.analysis_input), patch("apps.ai_evaluation.services.AdmiezoAIClient.validate_model"), patch("apps.ai_evaluation.services.AdmiezoAIClient.extract_answer", return_value={"answer_text": "test answer"}), patch("apps.ai_evaluation.services.AdmiezoAIClient.evaluate", return_value=self.result(confidence="70")):
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
        self.assertEqual(analysis_data(processed)["assignment_status"], Assignment.Status.REASSIGNED)
        self.assertEqual(analysis_data(processed)["backup_evaluator"], assignment.backup_evaluator.display_name)
        self.assertFalse(Evaluation.objects.filter(assignment=assignment).exists())
        self.assertFalse(ValuationResult.objects.filter(script=self.script).exists())

    def test_questions_are_extracted_evaluated_and_stored_in_order(self):
        self.set_ai_policy(SecurityPolicy.AIEvaluationMode.ASSISTIVE)
        second = Question.objects.create(tenant_id=self.paper.tenant_id, paper=self.paper, number="2", max_marks=10, position=2)
        second_guide = AIQuestionGuide.objects.create(tenant_id=self.paper.tenant_id, pack=self.pack, question=second, question_text="Explain question two.", evaluation_guidance="Award up to ten marks.", max_marks=10)
        assignment = Assignment.objects.create(tenant_id=self.paper.tenant_id, script=self.script, evaluator=self.human, valuation_round=1, due_at=timezone.now() + timedelta(days=3))
        with patch("apps.ai_evaluation.services.provider_status", return_value={"available": True}):
            analysis, _ = queue_assistive_analysis(tenant_id=self.paper.tenant_id, actor_id=self.actor.id, assignment=assignment)
        steps = []
        reference_media = [{"mime_type": "application/pdf", "data": b"reference"}] * 4
        script_media = [{"mime_type": "image/png", "data": b"masked-script"}]

        def extract(*, question, media, **kwargs):
            steps.append(f"extract:{question['number']}")
            self.assertEqual(media, script_media)
            if question["number"] == "2":
                self.assertTrue(analysis.question_assessments.filter(question=self.question).exists())
            return {"answer_text": f"answer {question['number']}"}

        def evaluate(*, prompt, media, **kwargs):
            number = "1" if "answer 1" in prompt else "2"
            steps.append(f"evaluate:{number}")
            self.assertEqual(media, reference_media)
            self.assertIn(f"Extracted answer: answer {number}", prompt)
            result = self.result(marks="8" if number == "1" else "7")
            result["assessments"][0]["question_id"] = str(self.question.id if number == "1" else second.id)
            return result

        with patch("apps.ai_evaluation.services._analysis_input", return_value=([self.guide, second_guide], "", reference_media + script_media)), patch("apps.ai_evaluation.services.AdmiezoAIClient.validate_model"), patch("apps.ai_evaluation.services.AdmiezoAIClient.extract_answer", side_effect=extract), patch("apps.ai_evaluation.services.AdmiezoAIClient.evaluate", side_effect=evaluate), self.assertLogs("apps.ai_evaluation.services", level="INFO") as logs:
            processed = process_next_analysis()
        self.assertEqual(processed.status, AIAnalysis.Status.COMPLETED)
        self.assertEqual(steps, ["extract:1", "evaluate:1", "extract:2", "evaluate:2"])
        self.assertTrue(any("Question 1 attempt 1/3 extracting" in line for line in logs.output))
        self.assertTrue(any("Extracted Q1: answer 1" in line for line in logs.output))
        self.assertTrue(any("Q1 marks: 8/10" in line for line in logs.output))
        self.assertEqual(list(analysis.question_assessments.order_by("question__position").values_list("marks", flat=True)), [Decimal("8"), Decimal("7")])

    def test_exhausted_question_retries_keep_prior_marks_and_route_to_human(self):
        self.set_ai_policy(SecurityPolicy.AIEvaluationMode.AUTONOMOUS)
        second = Question.objects.create(tenant_id=self.paper.tenant_id, paper=self.paper, number="2", max_marks=10, position=2)
        second_guide = AIQuestionGuide.objects.create(tenant_id=self.paper.tenant_id, pack=self.pack, question=second, question_text="Explain question two.", evaluation_guidance="Award up to ten marks.", max_marks=10)
        with patch("apps.ai_evaluation.services.provider_status", return_value={"available": True}):
            analysis = assign_paper_to_ai(tenant_id=self.paper.tenant_id, actor_id=self.actor.id, paper=self.paper, maximum_scripts=1)[0]
        with patch("apps.ai_evaluation.services._analysis_input", return_value=([self.guide, second_guide], "", [])), patch("apps.ai_evaluation.services.AdmiezoAIClient.validate_model"), patch("apps.ai_evaluation.services.AdmiezoAIClient.extract_answer", side_effect=[{"answer_text": "answer one"}, AdmiezoAIError("missing"), AdmiezoAIError("missing"), AdmiezoAIError("missing")]) as extract, patch("apps.ai_evaluation.services.AdmiezoAIClient.evaluate", return_value=self.result()), patch("apps.ai_evaluation.services.time.sleep") as sleep:
            processed = process_next_analysis()
        self.assertEqual(extract.call_count, 4)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(processed.status, AIAnalysis.Status.LOW_CONFIDENCE)
        self.assertEqual(processed.question_assessments.count(), 1)
        self.assertEqual(processed.question_assessments.first().marks, Decimal("8"))
        self.assertFalse(processed.assignment.evaluator.is_system_ai)
        self.assertIsNotNone(processed.assignment.backup_evaluator_id)
        self.assertNotEqual(processed.assignment.evaluator_id, processed.assignment.backup_evaluator_id)
        self.assertFalse(Evaluation.objects.filter(assignment=processed.assignment).exists())

    def test_paper_deadline_routes_to_manual_before_extraction(self):
        self.set_ai_policy(SecurityPolicy.AIEvaluationMode.AUTONOMOUS)
        with patch("apps.ai_evaluation.services.provider_status", return_value={"available": True}):
            analysis = assign_paper_to_ai(tenant_id=self.paper.tenant_id, actor_id=self.actor.id, paper=self.paper, maximum_scripts=1)[0]
        with patch("apps.ai_evaluation.services.PAPER_DEADLINE_SECONDS", 0), patch("apps.ai_evaluation.services._analysis_input", side_effect=self.analysis_input), patch("apps.ai_evaluation.services.AdmiezoAIClient.validate_model"), patch("apps.ai_evaluation.services.AdmiezoAIClient.extract_answer") as extract:
            processed = process_next_analysis()
        self.assertEqual(processed.id, analysis.id)
        self.assertEqual(processed.status, AIAnalysis.Status.LOW_CONFIDENCE)
        extract.assert_not_called()
        self.assertFalse(processed.assignment.evaluator.is_system_ai)
