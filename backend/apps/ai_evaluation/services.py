import json
import re
import uuid
import hashlib
import logging
import time
from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import transaction
from django.core.cache import cache
from django.utils import timezone
from ninja.errors import HttpError

from apps.allocation.models import AllocationPolicy, Assignment, AssignmentHistory
from apps.allocation.services import next_valuation_round, redistribute_assignment
from apps.configuration.models import Paper
from apps.core.services import record_event
from apps.custody.models import Script
from apps.custody.services import transition_script
from apps.evaluators.models import Evaluator
from apps.marking.models import Evaluation, QuestionMark
from apps.marking.services import evaluation_checksum
from apps.repository.models import ScriptAsset
from apps.repository.storage import delete_object, read_object, read_object_metadata, signed_object_url
from apps.security.crypto import encrypt_secret
from apps.security.models import SecurityPolicy
from apps.tenancy.models import Membership, TenantAccount
from apps.rubrics.models import MarkingScheme
from apps.valuation.services import finalize_valuation

from .models import (
    AIAnalysis,
    AIProviderConfiguration,
    AIQuestionAssessment,
    AIQuestionGuide,
    AIReferenceAsset,
    AIReferencePack,
    AIReferenceUpload,
)
from .provider import AdmiezoAIClient, AdmiezoAIError, AdmiezoAITransientError


ALLOWED_REFERENCE_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp"}
MAX_REFERENCE_BYTES = 20 * 1024 * 1024
PAPER_DEADLINE_SECONDS = 300
QUESTION_ATTEMPTS = 3
QUESTION_RETRY_SECONDS = 5
logger = logging.getLogger(__name__)


def provider_status(tenant_id, model_name="admiezo-ai-v1"):
    client = AdmiezoAIClient(tenant_id=tenant_id)
    if not client.configured:
        return {"provider": "admiezo_ai", "configured": False, "valid": False, "available": False, "message": "University API key not configured"}
    fingerprint = hashlib.sha256(client.api_key.encode()).hexdigest()[:16]
    cache_key = f"admiezo-ai-provider:{fingerprint}:{model_name}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        client.validate_model(model_name)
        result = {"provider": "admiezo_ai", "configured": True, "valid": True, "available": True, "message": "Model access verified; generation quota is checked per request"}
        cache.set(cache_key, result, 300)
    except AdmiezoAITransientError as exc:
        previously_verified = AIProviderConfiguration.objects.filter(tenant_id=tenant_id, is_active=True, verified_at__isnull=False).exists()
        result = {"provider": "admiezo_ai", "configured": True, "valid": previously_verified, "available": previously_verified, "message": f"{exc}; using the last verified university configuration" if previously_verified else str(exc)}
        cache.set(cache_key, result, 15)
    except AdmiezoAIError as exc:
        result = {"provider": "admiezo_ai", "configured": True, "valid": False, "available": False, "message": str(exc)}
        cache.set(cache_key, result, 60)
    return result


def provider_configuration_status(tenant_id):
    configuration = AIProviderConfiguration.objects.filter(tenant_id=tenant_id).first()
    configured = bool(configuration and configuration.api_key_ciphertext)
    available = bool(configured and configuration.is_active and configuration.verified_at)
    return {
        "provider": "admiezo_ai",
        "configured": configured,
        "valid": available,
        "available": available,
        "message": "ADMIEZO AI Assistant connection verified" if available else "University API key not configured",
        "version": configuration.version if configuration else 0,
        "updated_at": configuration.updated_at.isoformat() if configuration else None,
    }


def configure_provider(*, tenant_id, actor_id, api_key, version, model_name="admiezo-ai-v1"):
    secret = api_key.strip()
    candidate = AdmiezoAIClient(api_key=secret)
    try:
        candidate.validate_model(model_name)
    except AdmiezoAIError as exc:
        raise HttpError(422, str(exc)) from exc
    with transaction.atomic():
        configuration = AIProviderConfiguration.objects.select_for_update().filter(tenant_id=tenant_id).first()
        created = configuration is None
        if configuration and configuration.version != version:
            raise HttpError(409, "ADMIEZO AI Assistant configuration was changed by another administrator")
        if not configuration:
            if version not in (0, 1):
                raise HttpError(409, "ADMIEZO AI Assistant configuration is stale")
            configuration = AIProviderConfiguration(tenant_id=tenant_id)
        configuration.api_key_ciphertext = encrypt_secret(secret)
        configuration.is_active = True
        configuration.verified_at = timezone.now()
        configuration.version = 1 if created else configuration.version + 1
        configuration.save()
        record_event(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="ai_evaluation.provider.configured",
            aggregate="AIProviderConfiguration",
            aggregate_id=configuration.id,
            payload={"version": configuration.version},
        )
    cache.clear()
    return provider_configuration_status(tenant_id)


def tenant_ai_policy(tenant_id):
    return SecurityPolicy.objects.filter(tenant_id=tenant_id).first() or SecurityPolicy(tenant_id=tenant_id)


def reference_pack_for(*, tenant_id, paper):
    pack, _ = AIReferencePack.objects.get_or_create(tenant_id=tenant_id, paper=paper)
    return pack


def pack_readiness(pack):
    question_paper = pack.assets.filter(kind=AIReferenceUpload.Kind.QUESTION_PAPER, slot=1).exists()
    answer_slots = set(pack.assets.filter(kind=AIReferenceUpload.Kind.REFERENCE_ANSWER).values_list("slot", flat=True))
    question_ids = set(pack.paper.questions.values_list("id", flat=True))
    guided_ids = set(pack.question_guides.exclude(question_text="").values_list("question_id", flat=True))
    rubric_ids = set(pack.question_guides.exclude(question_text="").exclude(evaluation_guidance="").values_list("question_id", flat=True))
    missing_guides = len(question_ids - guided_ids)
    missing_guidance = len(question_ids - rubric_ids)
    guide_only_ready = bool(question_ids) and missing_guidance == 0
    return {
        "ready": bool(question_ids) and missing_guides == 0 and guide_only_ready,
        "guide_only_ready": guide_only_ready,
        "missing_evaluation_guidance": missing_guidance,
        "question_paper": question_paper,
        "reference_answers": len(answer_slots & {1, 2, 3}),
        "configured_questions": len(guided_ids & question_ids),
        "total_questions": len(question_ids),
        "missing_question_guides": missing_guides,
    }


@transaction.atomic
def create_reference_upload(*, tenant_id, actor_id, paper, kind, slot, file_name, content_type, maximum_bytes):
    if kind not in AIReferenceUpload.Kind.values or content_type not in ALLOWED_REFERENCE_TYPES:
        raise HttpError(422, "Reference file type is not supported")
    if kind == AIReferenceUpload.Kind.QUESTION_PAPER and slot != 1:
        raise HttpError(422, "The question paper must use slot 1")
    if kind == AIReferenceUpload.Kind.REFERENCE_ANSWER and slot not in (1, 2, 3):
        raise HttpError(422, "Reference answers must use slots 1, 2, or 3")
    if not 1 <= maximum_bytes <= MAX_REFERENCE_BYTES:
        raise HttpError(422, "Reference file size is outside the supported range")
    pack = reference_pack_for(tenant_id=tenant_id, paper=paper)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", file_name.strip())[:120] or "reference"
    key = f"ai-reference/{tenant_id}/{pack.id}/{uuid.uuid4().hex}-{safe_name}"
    upload = AIReferenceUpload.objects.create(
        tenant_id=tenant_id,
        pack=pack,
        kind=kind,
        slot=slot,
        storage_key=key,
        file_name=safe_name,
        content_type=content_type,
        maximum_bytes=maximum_bytes,
        expires_at=timezone.now() + timedelta(minutes=5),
        created_by_id=actor_id,
    )
    url, expires = signed_object_url(method="PUT", key=key, content_type=content_type, max_bytes=maximum_bytes)
    return upload, url, expires


@transaction.atomic
def finalize_reference_upload(*, tenant_id, actor_id, upload_id, expected_version):
    upload = AIReferenceUpload.objects.select_for_update().select_related("pack").filter(id=upload_id, tenant_id=tenant_id).first()
    if not upload or upload.version != expected_version or upload.status != AIReferenceUpload.Status.ISSUED:
        raise HttpError(409, "Reference upload is missing, stale, or already finalized")
    if upload.expires_at <= timezone.now():
        upload.status = AIReferenceUpload.Status.EXPIRED
        upload.save(update_fields=["status", "updated_at"])
        raise HttpError(409, "Reference upload expired")
    metadata = read_object_metadata(upload.storage_key)
    if metadata.mime_type != upload.content_type or metadata.byte_size > upload.maximum_bytes:
        raise HttpError(409, "Uploaded reference does not match its signed intent")
    previous = AIReferenceAsset.objects.filter(pack=upload.pack, kind=upload.kind, slot=upload.slot).first()
    previous_key = previous.storage_key if previous else ""
    asset_version = (previous.asset_version + 1) if previous else 1
    if previous:
        previous.storage_key = upload.storage_key
        previous.file_name = upload.file_name
        previous.mime_type = metadata.mime_type
        previous.sha256 = metadata.sha256
        previous.byte_size = metadata.byte_size
        previous.asset_version = asset_version
        previous.uploaded_by_id = actor_id
        previous.save()
        asset = previous
    else:
        asset = AIReferenceAsset.objects.create(
            tenant_id=tenant_id,
            pack=upload.pack,
            kind=upload.kind,
            slot=upload.slot,
            storage_key=upload.storage_key,
            file_name=upload.file_name,
            mime_type=metadata.mime_type,
            sha256=metadata.sha256,
            byte_size=metadata.byte_size,
            uploaded_by_id=actor_id,
        )
    upload.status = AIReferenceUpload.Status.COMPLETED
    upload.sha256 = metadata.sha256
    upload.byte_size = metadata.byte_size
    upload.finalized_at = timezone.now()
    upload.version += 1
    upload.save()
    upload.pack.version += 1
    upload.pack.save(update_fields=["version", "updated_at"])
    record_event(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="ai_evaluation.reference.uploaded",
        aggregate="AIReferenceAsset",
        aggregate_id=asset.id,
        payload={"paper_id": str(upload.pack.paper_id), "kind": upload.kind, "slot": upload.slot, "version": asset_version},
    )
    if previous_key and previous_key != upload.storage_key:
        try:
            delete_object(previous_key)
        except Exception:
            pass
    return asset


@transaction.atomic
def save_question_guide(*, tenant_id, actor_id, pack, question, question_text, evaluation_guidance, max_marks, version):
    if question.paper_id != pack.paper_id or not question_text.strip():
        raise HttpError(422, "Question text is required and must belong to this paper")
    try:
        maximum = Decimal(str(max_marks))
    except (InvalidOperation, ValueError) as exc:
        raise HttpError(422, "Maximum marks must be a number") from exc
    if maximum != question.max_marks:
        raise HttpError(422, "AI maximum marks must match the frozen paper configuration")
    guide = AIQuestionGuide.objects.select_for_update().filter(pack=pack, question=question).first()
    if guide and guide.version != version:
        raise HttpError(409, "Question guidance was changed by another user")
    if not guide and version not in (0, 1):
        raise HttpError(409, "Question guidance version is stale")
    if not guide:
        guide = AIQuestionGuide(tenant_id=tenant_id, pack=pack, question=question, max_marks=maximum)
    guide.question_text = question_text.strip()
    guide.evaluation_guidance = evaluation_guidance.strip()
    guide.max_marks = maximum
    guide.version = guide.version + 1 if guide.pk else 1
    guide.save()
    pack.version += 1
    pack.save(update_fields=["version", "updated_at"])
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="ai_evaluation.question_guide.saved", aggregate="AIQuestionGuide", aggregate_id=guide.id, payload={"question_id": str(question.id), "version": guide.version})
    return guide


def extract_question_paper(*, tenant_id, actor_id, pack, model_name):
    asset = pack.assets.filter(kind=AIReferenceUpload.Kind.QUESTION_PAPER, slot=1).first()
    if not asset:
        raise HttpError(409, "Upload the question paper before extracting questions")
    client = AdmiezoAIClient(tenant_id=tenant_id)
    if not client.configured:
        raise HttpError(409, "ADMIEZO AI Assistant is not configured")
    try:
        result = client.extract_questions(model=model_name, media=[{"mime_type": asset.mime_type, "data": read_object(asset.storage_key)}])
    except AdmiezoAIError as exc:
        raise HttpError(502, str(exc)) from exc
    matched = 0
    unmatched = []
    configured = {(item.number.strip().casefold(), item.sub_question.strip().casefold()): item for item in pack.paper.questions.all()}
    with transaction.atomic():
        for item in result.get("questions", []):
            key = (str(item.get("number", "")).strip().casefold(), str(item.get("sub_question", "")).strip().casefold())
            question = configured.get(key)
            text = str(item.get("text", "")).strip()
            if not question or not text:
                unmatched.append({"number": item.get("number", ""), "sub_question": item.get("sub_question", "")})
                continue
            guide, created = AIQuestionGuide.objects.get_or_create(
                tenant_id=tenant_id,
                pack=pack,
                question=question,
                defaults={"question_text": text, "max_marks": question.max_marks},
            )
            if not created:
                guide.question_text = text
                guide.version += 1
                guide.save(update_fields=["question_text", "version", "updated_at"])
            matched += 1
        pack.version += 1
        pack.save(update_fields=["version", "updated_at"])
        record_event(tenant_id=tenant_id, actor_id=actor_id, action="ai_evaluation.question_paper.extracted", aggregate="AIReferencePack", aggregate_id=pack.id, payload={"matched": matched, "unmatched": unmatched})
    return {"matched": matched, "unmatched": unmatched}


def system_ai_evaluator(*, tenant_id):
    evaluator, _ = Evaluator.objects.update_or_create(
        tenant_id=tenant_id,
        evaluator_code="AI-ADMIEZO",
        defaults={
            "display_name": "ADMIEZO AI Assistant",
            "institution_name": "ADMIEZO AI",
            "department": "Autonomous evaluation",
            "designation": "System evaluator",
            "qualification": "ADMIEZO managed evaluation model",
            "employment_type": "system",
            "years_experience": 0,
            "status": Evaluator.Status.ACTIVE,
            "daily_capacity": 5000,
            "is_system_ai": True,
        },
    )
    return evaluator


@transaction.atomic
def synchronize_ai_governance(*, tenant_id, mode):
    enabled = mode != SecurityPolicy.AIEvaluationMode.DISABLED
    account = TenantAccount.objects.select_for_update().filter(root_institution__tenant_id=tenant_id).first()
    tenant_modules = set()
    if account:
        modules = set(account.enabled_modules)
        before = set(modules)
        if enabled:
            modules.add("ai_evaluation")
        else:
            modules.discard("ai_evaluation")
        if modules != before:
            account.enabled_modules = sorted(modules)
            account.version += 1
            account.save(update_fields=["enabled_modules", "version", "updated_at"])
        tenant_modules = modules
    memberships = Membership.objects.select_for_update().filter(institution__tenant_id=tenant_id)
    for membership in memberships:
        before = set(membership.enabled_modules)
        if membership.role in {Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN}:
            modules = set(tenant_modules)
        else:
            modules = set(before)
        if enabled and membership.role in {Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN}:
            modules.add("ai_evaluation")
        elif not enabled:
            modules.discard("ai_evaluation")
        if modules != before:
            membership.enabled_modules = sorted(modules)
            membership.save(update_fields=["enabled_modules", "updated_at"])
    if mode == SecurityPolicy.AIEvaluationMode.AUTONOMOUS:
        system_ai_evaluator(tenant_id=tenant_id)
    else:
        Evaluator.objects.filter(tenant_id=tenant_id, evaluator_code="AI-ADMIEZO", is_system_ai=True).update(
            status=Evaluator.Status.INACTIVE,
            updated_at=timezone.now(),
        )


@transaction.atomic
def queue_assistive_analysis(*, tenant_id, actor_id, assignment):
    policy = tenant_ai_policy(tenant_id)
    if policy.ai_evaluation_mode != SecurityPolicy.AIEvaluationMode.ASSISTIVE:
        raise HttpError(409, "AI assistance is not enabled for this university")
    pack = reference_pack_for(tenant_id=tenant_id, paper=assignment.script.paper)
    if not pack_readiness(pack)["ready"]:
        raise HttpError(409, "Complete the AI reference pack before requesting analysis")
    if not provider_status(tenant_id, policy.ai_model_name)["available"]:
        raise HttpError(409, "ADMIEZO AI Assistant is not configured or its API key is invalid")
    existing = assignment.ai_analyses.filter(trigger=AIAnalysis.Trigger.ASSISTIVE, status__in=[AIAnalysis.Status.QUEUED, AIAnalysis.Status.RUNNING]).first()
    if existing:
        return existing, False
    analysis = AIAnalysis.objects.create(
        tenant_id=tenant_id,
        assignment=assignment,
        trigger=AIAnalysis.Trigger.ASSISTIVE,
        model_name=policy.ai_model_name,
        requested_by_id=actor_id,
    )
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="ai_evaluation.assistance.queued", aggregate="AIAnalysis", aggregate_id=analysis.id, payload={"assignment_id": str(assignment.id)})
    return analysis, True


def assign_paper_to_ai(*, tenant_id, actor_id, paper, maximum_scripts):
    if not 1 <= maximum_scripts <= 5000:
        raise HttpError(422, "AI assignment batch size must be between 1 and 5000")
    policy = tenant_ai_policy(tenant_id)
    if policy.ai_evaluation_mode != SecurityPolicy.AIEvaluationMode.AUTONOMOUS:
        raise HttpError(409, "Autonomous AI must be enabled before assigning scripts to AI")
    pack = reference_pack_for(tenant_id=tenant_id, paper=paper)
    if not pack_readiness(pack)["ready"]:
        raise HttpError(409, "Complete the AI reference pack before assigning scripts to AI")
    if not provider_status(tenant_id, policy.ai_model_name)["available"]:
        raise HttpError(409, "ADMIEZO AI Assistant is not configured or its API key is invalid")
    evaluator = system_ai_evaluator(tenant_id=tenant_id)
    due_hours = AllocationPolicy.objects.filter(tenant_id=tenant_id, paper=paper).values_list("assignment_due_hours", flat=True).first() or 120
    scripts = Script.objects.filter(tenant_id=tenant_id, paper=paper, state=Script.State.STORED).select_related("paper").prefetch_related("valuation_results", "assignments", "final_mark").order_by("created_at")
    created = []
    for script in scripts:
        if len(created) >= maximum_scripts:
            break
        if next_valuation_round(script) != 1:
            continue
        with transaction.atomic():
            locked = Script.objects.select_for_update().get(id=script.id, tenant_id=tenant_id)
            if locked.state != Script.State.STORED or Assignment.objects.filter(script=locked, valuation_round=1).exists():
                continue
            assignment = Assignment.objects.create(
                tenant_id=tenant_id,
                script=locked,
                evaluator=evaluator,
                valuation_round=1,
                status=Assignment.Status.ASSIGNED,
                quality_score=Decimal("100"),
                source="ai_autonomous",
                score_breakdown={"mode": "autonomous", "model": policy.ai_model_name},
                due_at=timezone.now() + timedelta(hours=due_hours),
            )
            AssignmentHistory.objects.create(tenant_id=tenant_id, assignment=assignment, action="assigned_to_ai", actor_id=actor_id, to_evaluator_id=evaluator.id, snapshot={"mode": policy.ai_evaluation_mode, "model": policy.ai_model_name})
            transition_script(tenant_id=tenant_id, actor_id=actor_id, script_id=locked.id, expected_version=locked.version, to_state=Script.State.ASSIGNED, location="Autonomous AI evaluation queue", metadata={"assignment_id": str(assignment.id)})
            analysis = AIAnalysis.objects.create(tenant_id=tenant_id, assignment=assignment, trigger=AIAnalysis.Trigger.AUTONOMOUS, model_name=policy.ai_model_name, requested_by_id=actor_id)
            record_event(tenant_id=tenant_id, actor_id=actor_id, action="ai_evaluation.autonomous.queued", aggregate="AIAnalysis", aggregate_id=analysis.id, payload={"assignment_id": str(assignment.id), "script_id": str(script.id)})
            created.append(analysis)
    return created


def assign_all_ready_scripts_to_ai(*, tenant_id, actor_id, maximum_scripts):
    if not 1 <= maximum_scripts <= 5000:
        raise HttpError(422, "AI assignment batch size must be between 1 and 5000")
    policy = tenant_ai_policy(tenant_id)
    if policy.ai_evaluation_mode != SecurityPolicy.AIEvaluationMode.AUTONOMOUS:
        raise HttpError(409, "Autonomous AI must be enabled before assigning scripts to AI")
    if not provider_status(tenant_id, policy.ai_model_name)["available"]:
        raise HttpError(409, "ADMIEZO AI Assistant is not configured or its API key is invalid")

    created = []
    packs = AIReferencePack.objects.filter(
        tenant_id=tenant_id,
        paper__status=Paper.Status.FROZEN,
    ).select_related("paper").prefetch_related("assets", "question_guides", "paper__questions").order_by("paper__code")
    for pack in packs:
        if len(created) >= maximum_scripts:
            break
        if not pack_readiness(pack)["ready"]:
            continue
        created.extend(assign_paper_to_ai(
            tenant_id=tenant_id,
            actor_id=actor_id,
            paper=pack.paper,
            maximum_scripts=maximum_scripts - len(created),
        ))
    return created


def _latest_assets(script):
    assets = ScriptAsset.objects.filter(script=script, kind=ScriptAsset.Kind.EVALUATION, deleted_at__isnull=True).order_by("page_number", "-version")
    latest = {}
    for asset in assets:
        latest.setdefault(asset.page_number, asset)
    if len(latest) != script.page_count:
        raise AdmiezoAIError("The masked evaluation copy is incomplete")
    return [latest[key] for key in sorted(latest)]


def _analysis_input(analysis, pack, deadline=None):
    guides = list(pack.question_guides.select_related("question").order_by("question__position"))
    script_assets = _latest_assets(analysis.assignment.script)
    questions = [
        {
            "question_id": str(item.question_id),
            "number": item.question.number,
            "sub_question": item.question.sub_question,
            "max_marks": str(item.max_marks),
            "question_text": item.question_text,
            "evaluation_guidance": item.evaluation_guidance,
        }
        for item in guides
    ]
    prompt = (
        "Evaluate the masked answer script against the configured question text and marking guidance. "
        "Return exactly one assessment for every question_id. Marks must be between zero and max_marks. "
        "Media contains only masked script pages in page order.\n"
        f"Question configuration: {json.dumps(questions, separators=(',', ':'))}"
    )
    def read_asset(item):
        if deadline is None:
            data = read_object(item.storage_key)
        else:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AdmiezoAIError("AI paper deadline expired after 300 seconds")
            data = read_object(item.storage_key, timeout=min(remaining, 15))
        return {"mime_type": item.mime_type, "data": data}

    media = [read_asset(item) for item in script_assets]
    return guides, prompt, media


def _validated_assessments(guides, result):
    by_id = {str(item.question_id): item for item in guides}
    output = {}
    for item in result.get("assessments", []):
        question_id = str(item.get("question_id", ""))
        guide = by_id.get(question_id)
        if not guide or question_id in output:
            raise AdmiezoAIError("ADMIEZO AI Assistant returned unknown or duplicate question assessments")
        try:
            marks = Decimal(str(item.get("marks"))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            confidence = Decimal(str(item.get("confidence"))).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError) as exc:
            raise AdmiezoAIError("ADMIEZO AI Assistant returned invalid marks or confidence") from exc
        if not Decimal("0") <= marks <= min(guide.max_marks, guide.question.max_marks):
            raise AdmiezoAIError(f"ADMIEZO AI Assistant marks are outside the configured maximum for question {guide.question.number}")
        if not Decimal("0") <= confidence <= Decimal("100"):
            raise AdmiezoAIError("ADMIEZO AI Assistant confidence is outside the supported range")
        output[question_id] = {
            "guide": guide,
            "marks": marks,
            "confidence": confidence,
            "feedback": str(item.get("feedback", ""))[:4000],
            "reasoning": str(item.get("reasoning", ""))[:8000],
        }
    if set(output) != set(by_id):
        raise AdmiezoAIError("ADMIEZO AI Assistant did not assess every configured question")
    try:
        overall = Decimal(str(result.get("overall_confidence"))).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError) as exc:
        raise AdmiezoAIError("ADMIEZO AI Assistant returned invalid overall confidence") from exc
    if not Decimal("0") <= overall <= Decimal("100"):
        raise AdmiezoAIError("ADMIEZO AI Assistant overall confidence is outside the supported range")
    effective = min([overall, *(item["confidence"] for item in output.values())])
    return output, effective


@transaction.atomic
def _store_assessment(analysis, result, item, confidence):
    AIQuestionAssessment.objects.update_or_create(
        analysis=analysis,
        question=item["guide"].question,
        defaults={
            "tenant_id": analysis.tenant_id,
            "marks": item["marks"],
            "confidence": item["confidence"],
            "feedback": item["feedback"],
            "reasoning": item["reasoning"],
        },
    )
    analysis.effective_confidence = min(analysis.effective_confidence, confidence) if analysis.effective_confidence is not None else confidence
    summaries = analysis.raw_response.get("question_summaries", {})
    summaries[str(item["guide"].question_id)] = str(result.get("summary", ""))[:4000]
    analysis.raw_response = {"question_summaries": summaries}
    analysis.save(update_fields=["effective_confidence", "raw_response", "updated_at"])


def _route_to_human(analysis, policy, reason=None):
    assignment = analysis.assignment
    reason = reason or f"AI confidence {analysis.effective_confidence}% was below the configured {policy.ai_confidence_threshold}% threshold"
    try:
        # The allocation engine scores active, subject-eligible human evaluators.
        with transaction.atomic():
            reassigned = redistribute_assignment(
                tenant_id=analysis.tenant_id,
                actor_id=analysis.requested_by_id,
                assignment_id=assignment.id,
                expected_version=assignment.version,
                reason=reason,
            )
            reassigned.source = "ai_low_confidence"
            reassigned.is_flagged = True
            reassigned.flag_reason = reason[:240]
            reassigned.save(update_fields=["source", "is_flagged", "flag_reason", "updated_at"])
            analysis.status = AIAnalysis.Status.LOW_CONFIDENCE
            analysis.error_message = reason[:500]
            analysis.completed_at = timezone.now()
            analysis.save(update_fields=["status", "error_message", "completed_at", "updated_at"])
            record_event(tenant_id=analysis.tenant_id, actor_id=analysis.requested_by_id, action="ai_evaluation.routed_to_human", aggregate="AIAnalysis", aggregate_id=analysis.id, payload={"assignment_id": str(assignment.id), "evaluator_id": str(reassigned.evaluator_id), "reason": reason})
    except HttpError as exc:
        with transaction.atomic():
            assignment.refresh_from_db()
            assignment.is_flagged = True
            assignment.flag_reason = f"Human fallback required: {reason}"[:240]
            assignment.save(update_fields=["is_flagged", "flag_reason", "updated_at"])
            analysis.error_message = f"Manual fallback requires an eligible registered evaluator for this subject: {exc}"[:500]
            analysis.status = AIAnalysis.Status.FAILED
            analysis.completed_at = timezone.now()
            analysis.save(update_fields=["status", "error_message", "completed_at", "updated_at"])
        logger.warning("Manual evaluation pending for script %s: %s", assignment.script_id, exc)
        return
    logger.info("Assigned for manual evaluation: script %s, evaluator %s", assignment.script_id, reassigned.evaluator_id)

@transaction.atomic
def _commit_autonomous_result(analysis):
    assignment = Assignment.objects.select_for_update().select_related("script__paper", "evaluator").get(id=analysis.assignment_id)
    scheme = MarkingScheme.objects.filter(tenant_id=analysis.tenant_id, paper=assignment.script.paper, status=MarkingScheme.Status.FROZEN).order_by("-version").first()
    if not scheme:
        raise AdmiezoAIError("A frozen marking scheme is required before autonomous evaluation")
    if hasattr(assignment, "evaluation"):
        raise AdmiezoAIError("This assignment already has an evaluation")
    assessments = list(analysis.question_assessments.select_related("question").order_by("question__position"))
    evaluation = Evaluation.objects.create(
        tenant_id=analysis.tenant_id,
        assignment=assignment,
        scheme=scheme,
        status=Evaluation.Status.SUBMITTED,
        total_marks=sum((item.marks for item in assessments), Decimal("0")),
        started_at=analysis.started_at,
        submitted_at=timezone.now(),
    )
    QuestionMark.objects.bulk_create([
        QuestionMark(
            tenant_id=analysis.tenant_id,
            evaluation=evaluation,
            question=item.question,
            sequence=1,
            marks=item.marks,
            outcome=QuestionMark.Outcome.EVALUATED,
            marked_for_review=False,
            requires_attention=False,
            examiner_confirmed=True,
            answer_selection={"source": "admiezo_ai", "confidence": str(item.confidence), "analysis_id": str(analysis.id)},
            actor_id=analysis.requested_by_id,
        )
        for item in assessments
    ])
    evaluation.checksum = evaluation_checksum(evaluation)
    evaluation.save(update_fields=["checksum", "updated_at"])
    assignment.status = Assignment.Status.SUBMITTED
    assignment.progress_percent = 100
    assignment.started_at = assignment.started_at or analysis.started_at
    assignment.submitted_at = timezone.now()
    assignment.version += 1
    assignment.save()
    script = assignment.script
    if script.state == Script.State.ASSIGNED:
        script = transition_script(tenant_id=analysis.tenant_id, actor_id=analysis.requested_by_id, script_id=script.id, expected_version=script.version, to_state=Script.State.OPENED, location="Autonomous AI evaluator", metadata={"analysis_id": str(analysis.id)})
        script = transition_script(tenant_id=analysis.tenant_id, actor_id=analysis.requested_by_id, script_id=script.id, expected_version=script.version, to_state=Script.State.EVALUATING, location="Autonomous AI evaluator", metadata={"analysis_id": str(analysis.id)})
        transition_script(tenant_id=analysis.tenant_id, actor_id=analysis.requested_by_id, script_id=script.id, expected_version=script.version, to_state=Script.State.SUBMITTED, location="Autonomous AI evaluator", metadata={"analysis_id": str(analysis.id)})
    finalize_valuation(tenant_id=analysis.tenant_id, actor_id=analysis.requested_by_id, evaluation_id=evaluation.id, evaluator=assignment.evaluator, idempotency_key=f"ai-{analysis.id}")
    analysis.status = AIAnalysis.Status.COMPLETED
    analysis.completed_at = timezone.now()
    analysis.save(update_fields=["status", "completed_at", "updated_at"])
    record_event(tenant_id=analysis.tenant_id, actor_id=analysis.requested_by_id, action="ai_evaluation.autonomous.completed", aggregate="AIAnalysis", aggregate_id=analysis.id, payload={"assignment_id": str(assignment.id), "evaluation_id": str(evaluation.id), "confidence": str(analysis.effective_confidence), "total_marks": str(evaluation.total_marks)})


def process_analysis(analysis_id):
    deadline = time.monotonic() + PAPER_DEADLINE_SECONDS
    analysis = AIAnalysis.objects.select_related("assignment__script__paper").get(id=analysis_id)
    AdmiezoAIClient(tenant_id=analysis.tenant_id).validate_model(analysis.model_name)
    policy = tenant_ai_policy(analysis.tenant_id)
    pack = reference_pack_for(tenant_id=analysis.tenant_id, paper=analysis.assignment.script.paper)
    if not pack_readiness(pack)["ready"]:
        raise AdmiezoAIError("The AI reference pack is incomplete")
    guides, _, media = _analysis_input(analysis, pack, deadline=deadline)
    reference_media = media[:pack.assets.count()]
    script_media = media[pack.assets.count():]
    for guide in guides:
        if analysis.question_assessments.filter(question=guide.question).exists():
            continue
        number = re.sub(r"^[Qq]", "", str(guide.question.number))
        question = {
            "number": number,
            "text": guide.question_text,
            "max_marks": str(guide.max_marks),
            "evaluation_guidance": guide.evaluation_guidance,
        }
        for attempt in range(1, QUESTION_ATTEMPTS + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if analysis.trigger == AIAnalysis.Trigger.AUTONOMOUS:
                    _route_to_human(analysis, policy, "AI paper deadline expired after 300 seconds")
                    return
                raise AdmiezoAIError("AI paper deadline expired after 300 seconds")
            try:
                logger.info("Question %s attempt %s/3 extracting", number, attempt)
                client = AdmiezoAIClient(tenant_id=analysis.tenant_id, timeout=min(remaining, 120))
                extracted = client.extract_answer(model=analysis.model_name, question=question, media=script_media)
                answer = str(extracted.get("answer_text", "")).strip() if isinstance(extracted, dict) else ""
                if not answer:
                    raise AdmiezoAIError(f"No answer extracted for Q{number}")
                logger.info("Extracted Q%s answer text (%s characters)", number, len(answer))
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AdmiezoAIError("AI paper deadline expired after 300 seconds")
                prompt = (
                    "Evaluate only this extracted answer against the question and supplied references. "
                    "Return exactly one assessment for this question_id. "
                    f"Question: {json.dumps({'question_id': str(guide.question_id), **question}, separators=(',', ':'))}. "
                    f"Extracted answer: {answer}"
                )
                client = AdmiezoAIClient(tenant_id=analysis.tenant_id, timeout=min(remaining, 120))
                result = client.evaluate(model=analysis.model_name, prompt=prompt, media=reference_media)
                assessments, confidence = _validated_assessments([guide], result)
            except (AdmiezoAIError, TimeoutError, ValueError, TypeError) as exc:
                if time.monotonic() >= deadline or attempt == QUESTION_ATTEMPTS:
                    reason = "AI paper deadline expired after 300 seconds" if time.monotonic() >= deadline else f"Q{number} failed after 3 attempts: {exc}"
                    if analysis.trigger == AIAnalysis.Trigger.AUTONOMOUS:
                        _route_to_human(analysis, policy, reason)
                        return
                    raise AdmiezoAIError(reason) from exc
                if deadline - time.monotonic() <= QUESTION_RETRY_SECONDS:
                    if analysis.trigger == AIAnalysis.Trigger.AUTONOMOUS:
                        _route_to_human(analysis, policy, "AI paper deadline expired after 300 seconds")
                        return
                    raise AdmiezoAIError("AI paper deadline expired after 300 seconds") from exc
                logger.warning("Retrying Q%s in 5 seconds: %s", number, exc)
                time.sleep(QUESTION_RETRY_SECONDS)
                continue
            item = assessments[str(guide.question_id)]
            _store_assessment(analysis, result, item, confidence)
            logger.info("Q%s marks: %s/%s", number, item["marks"], guide.max_marks)
            break
    analysis.refresh_from_db()
    if time.monotonic() >= deadline:
        if analysis.trigger == AIAnalysis.Trigger.AUTONOMOUS:
            _route_to_human(analysis, policy, "AI paper deadline expired after 300 seconds")
            return
        raise AdmiezoAIError("AI paper deadline expired after 300 seconds")
    confidence = analysis.effective_confidence
    if analysis.trigger == AIAnalysis.Trigger.ASSISTIVE:
        with transaction.atomic():
            analysis.status = AIAnalysis.Status.COMPLETED
            analysis.completed_at = timezone.now()
            analysis.save(update_fields=["status", "completed_at", "updated_at"])
            record_event(tenant_id=analysis.tenant_id, actor_id=analysis.requested_by_id, action="ai_evaluation.assistance.completed", aggregate="AIAnalysis", aggregate_id=analysis.id, payload={"assignment_id": str(analysis.assignment_id), "confidence": str(confidence)})
    elif confidence < policy.ai_confidence_threshold:
        _route_to_human(analysis, policy)
    else:
        _commit_autonomous_result(analysis)


def process_next_analysis():
    with transaction.atomic():
        analysis = AIAnalysis.objects.select_for_update().filter(status=AIAnalysis.Status.QUEUED).order_by("created_at").first()
        if not analysis:
            return None
        analysis.status = AIAnalysis.Status.RUNNING
        analysis.started_at = timezone.now()
        analysis.attempt_count += 1
        analysis.error_message = ""
        analysis.save(update_fields=["status", "started_at", "attempt_count", "error_message", "updated_at"])
        analysis_id = analysis.id
    try:
        process_analysis(analysis_id)
    except Exception as exc:
        analysis = AIAnalysis.objects.get(id=analysis_id)
        if analysis.trigger == AIAnalysis.Trigger.AUTONOMOUS and analysis.status == AIAnalysis.Status.RUNNING:
            _route_to_human(analysis, tenant_ai_policy(analysis.tenant_id), f"AI processing failed: {exc}")
        else:
            with transaction.atomic():
                AIAnalysis.objects.filter(id=analysis_id).update(status=AIAnalysis.Status.FAILED, error_message=str(exc)[:500], completed_at=timezone.now(), updated_at=timezone.now())
                record_event(tenant_id=analysis.tenant_id, actor_id=analysis.requested_by_id, action="ai_evaluation.analysis.failed", aggregate="AIAnalysis", aggregate_id=analysis.id, payload={"error": str(exc)[:500]})
    return AIAnalysis.objects.get(id=analysis_id)
