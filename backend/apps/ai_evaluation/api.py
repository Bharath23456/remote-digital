from ninja import Router, Schema
from ninja.errors import HttpError
from pydantic import Field

from apps.configuration.models import Paper, Question
from apps.core.authz import require_roles
from apps.custody.models import Script
from apps.security.models import SecurityPolicy
from apps.tenancy.models import Membership

from .models import AIAnalysis, AIReferencePack
from .services import (
    assign_all_ready_scripts_to_ai,
    assign_paper_to_ai,
    create_reference_upload,
    extract_question_paper,
    finalize_reference_upload,
    pack_readiness,
    provider_status,
    reference_pack_for,
    save_question_guide,
    tenant_ai_policy,
)


router = Router(tags=["AI evaluation"])
ADMIN_ROLES = (Membership.Role.PLATFORM_ADMIN, Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER)


class ReferenceUploadIn(Schema):
    paper_id: str
    kind: str
    slot: int = 1
    file_name: str
    content_type: str
    maximum_bytes: int


class FinalizeUploadIn(Schema):
    version: int


class QuestionGuideIn(Schema):
    paper_id: str
    question_id: str
    question_text: str
    evaluation_guidance: str = ""
    max_marks: float
    version: int = 0


class AssignAIIn(Schema):
    paper_id: str
    maximum_scripts: int = Field(default=500, ge=1, le=5000)


class AssignAllAIIn(Schema):
    maximum_scripts: int = Field(default=5000, ge=1, le=5000)


def analysis_data(item):
    return {
        "id": str(item.id),
        "assignment_id": str(item.assignment_id),
        "script": item.assignment.script.script_code,
        "paper_id": str(item.assignment.script.paper_id),
        "paper": item.assignment.script.paper.code,
        "trigger": item.trigger,
        "status": item.status,
        "model_name": item.model_name,
        "effective_confidence": float(item.effective_confidence) if item.effective_confidence is not None else None,
        "error_message": item.error_message or (item.assignment.flag_reason if item.status == AIAnalysis.Status.LOW_CONFIDENCE else ""),
        "evaluator": item.assignment.evaluator.display_name,
        "backup_evaluator": item.assignment.backup_evaluator.display_name if item.assignment.backup_evaluator_id else None,
        "assignment_status": item.assignment.status,
        "created_at": item.created_at.isoformat(),
        "started_at": item.started_at.isoformat() if item.started_at else None,
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
    }


def _governance(request):
    membership = require_roles(request, *ADMIN_ROLES)
    tenant_id = membership.institution.tenant_id
    policy = tenant_ai_policy(tenant_id)
    provider = provider_status(tenant_id, policy.ai_model_name)
    if policy.ai_evaluation_mode == SecurityPolicy.AIEvaluationMode.DISABLED:
        raise HttpError(403, "AI evaluation is disabled for this university")
    if not provider["available"]:
        raise HttpError(503, provider["message"])
    return membership, policy, provider


def paper_data(paper, tenant_id):
    pack = reference_pack_for(tenant_id=tenant_id, paper=paper)
    guides = {item.question_id: item for item in pack.question_guides.all()}
    return {
        "id": str(paper.id),
        "code": paper.code,
        "title": paper.title,
        "subject": paper.subject.code,
        "status": paper.status,
        "ready_scripts": Script.objects.filter(
            tenant_id=tenant_id,
            paper=paper,
            state=Script.State.STORED,
        ).exclude(assignments__valuation_round=1).count(),
        "reference_pack": {
            "id": str(pack.id),
            "version": pack.version,
            "readiness": pack_readiness(pack),
            "assets": [
                {
                    "id": str(item.id),
                    "kind": item.kind,
                    "slot": item.slot,
                    "file_name": item.file_name,
                    "mime_type": item.mime_type,
                    "byte_size": item.byte_size,
                    "asset_version": item.asset_version,
                }
                for item in pack.assets.order_by("kind", "slot")
            ],
            "questions": [
                {
                    "id": str(question.id),
                    "number": question.number,
                    "sub_question": question.sub_question,
                    "max_marks": float(question.max_marks),
                    "question_text": guides[question.id].question_text if question.id in guides else "",
                    "evaluation_guidance": guides[question.id].evaluation_guidance if question.id in guides else "",
                    "version": guides[question.id].version if question.id in guides else 0,
                }
                for question in paper.questions.all()
            ],
        },
    }


@router.get("/catalog")
def catalog(request):
    membership, policy, provider = _governance(request)
    tenant_id = membership.institution.tenant_id
    papers = Paper.objects.filter(tenant_id=tenant_id).select_related("subject").prefetch_related("questions", "ai_reference_pack__assets", "ai_reference_pack__question_guides").order_by("code")
    analyses = AIAnalysis.objects.filter(tenant_id=tenant_id).select_related("assignment__script__paper", "assignment__evaluator", "assignment__backup_evaluator").order_by("-created_at")[:200]
    return {
        "provider": provider,
        "governance": {
            "mode": policy.ai_evaluation_mode,
            "confidence_threshold": float(policy.ai_confidence_threshold),
            "model_name": policy.ai_model_name,
        },
        "papers": [paper_data(item, tenant_id) for item in papers],
        "analyses": [analysis_data(item) for item in analyses],
    }


@router.post("/reference-uploads")
def reference_upload(request, payload: ReferenceUploadIn):
    membership, _policy, _provider = _governance(request)
    paper = Paper.objects.filter(id=payload.paper_id, tenant_id=membership.institution.tenant_id).first()
    if not paper:
        raise HttpError(404, "Paper not found")
    upload, url, expires = create_reference_upload(
        tenant_id=membership.institution.tenant_id,
        actor_id=request.auth.id,
        paper=paper,
        kind=payload.kind,
        slot=payload.slot,
        file_name=payload.file_name,
        content_type=payload.content_type,
        maximum_bytes=payload.maximum_bytes,
    )
    return {"id": str(upload.id), "upload_url": url, "expires_at": expires, "headers": {"Content-Type": upload.content_type}, "version": upload.version}


@router.post("/reference-uploads/{upload_id}/finalize")
def finalize_reference(request, upload_id: str, payload: FinalizeUploadIn):
    membership, _policy, _provider = _governance(request)
    asset = finalize_reference_upload(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, upload_id=upload_id, expected_version=payload.version)
    return {"id": str(asset.id), "kind": asset.kind, "slot": asset.slot, "file_name": asset.file_name, "asset_version": asset.asset_version}


@router.put("/question-guides/{question_id}")
def update_question_guide(request, question_id: str, payload: QuestionGuideIn):
    membership, _policy, _provider = _governance(request)
    tenant_id = membership.institution.tenant_id
    paper = Paper.objects.filter(id=payload.paper_id, tenant_id=tenant_id).first()
    question = Question.objects.filter(id=question_id, paper=paper).first() if paper else None
    if not paper or not question or str(question.id) != payload.question_id:
        raise HttpError(404, "Question not found")
    pack = reference_pack_for(tenant_id=tenant_id, paper=paper)
    guide = save_question_guide(
        tenant_id=tenant_id,
        actor_id=request.auth.id,
        pack=pack,
        question=question,
        question_text=payload.question_text,
        evaluation_guidance=payload.evaluation_guidance,
        max_marks=payload.max_marks,
        version=payload.version,
    )
    return {"id": str(guide.id), "version": guide.version}


@router.post("/papers/{paper_id}/extract-questions")
def extract_questions(request, paper_id: str):
    membership, policy, _provider = _governance(request)
    tenant_id = membership.institution.tenant_id
    paper = Paper.objects.filter(id=paper_id, tenant_id=tenant_id).first()
    if not paper:
        raise HttpError(404, "Paper not found")
    pack = AIReferencePack.objects.filter(tenant_id=tenant_id, paper=paper).first()
    if not pack:
        raise HttpError(409, "Upload the question paper first")
    return extract_question_paper(tenant_id=tenant_id, actor_id=request.auth.id, pack=pack, model_name=policy.ai_model_name)


@router.post("/assignments")
def assign_ai(request, payload: AssignAIIn):
    membership, _policy, _provider = _governance(request)
    tenant_id = membership.institution.tenant_id
    paper = Paper.objects.filter(id=payload.paper_id, tenant_id=tenant_id, status=Paper.Status.FROZEN).first()
    if not paper:
        raise HttpError(409, "A frozen paper configuration is required")
    analyses = assign_paper_to_ai(tenant_id=tenant_id, actor_id=request.auth.id, paper=paper, maximum_scripts=payload.maximum_scripts)
    return {"queued": len(analyses), "analysis_ids": [str(item.id) for item in analyses]}


@router.post("/assignments/all-ready")
def assign_all_ai(request, payload: AssignAllAIIn):
    membership, _policy, _provider = _governance(request)
    analyses = assign_all_ready_scripts_to_ai(
        tenant_id=membership.institution.tenant_id,
        actor_id=request.auth.id,
        maximum_scripts=payload.maximum_scripts,
    )
    return {"queued": len(analyses), "analysis_ids": [str(item.id) for item in analyses]}
