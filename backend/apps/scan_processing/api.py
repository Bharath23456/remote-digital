from ninja import Field, Router, Schema
from ninja.errors import HttpError

from apps.core.authz import membership_for, require_roles
from apps.scanning.models import ScanJob
from apps.tenancy.models import Membership

from .models import ProcessingProfile, ProcessingRun, ScanQualityException
from .services import create_profile, execute_run, queue_run, resolve_exception


router = Router(tags=["Scan processing and quality control"])
ROLES = (Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER)


class ProfileIn(Schema):
    code: str
    name: str
    configuration: dict = Field(default_factory=dict)


class RunIn(Schema):
    scan_job_id: str
    profile_id: str


class VersionIn(Schema):
    version: int


class ExceptionIn(Schema):
    version: int
    status: str
    resolution: str


@router.get("/catalog")
def catalog(request):
    tenant_id = membership_for(request).institution.tenant_id
    return {
        "profiles": [{"id": str(item.id), "code": item.code, "name": item.name, "configuration": item.configuration, "version": item.version, "active": item.is_active} for item in ProcessingProfile.objects.filter(tenant_id=tenant_id).order_by("code", "-version")],
        "runs": [{"id": str(item.id), "script": item.script.script_code, "job_id": str(item.scan_job_id), "profile": item.profile.code, "status": item.status, "stage": item.current_stage, "metrics": item.metrics, "recognition": item.recognition_summary, "version": item.version} for item in ProcessingRun.objects.filter(tenant_id=tenant_id).select_related("script", "profile")[:1000]],
        "exceptions": [{"id": str(item.id), "run_id": str(item.run_id), "script": item.run.script.script_code, "page": item.page.page_index if item.page else None, "kind": item.kind, "severity": item.severity, "status": item.status, "reason": item.reason, "resolution": item.resolution, "version": item.version} for item in ScanQualityException.objects.filter(tenant_id=tenant_id).select_related("run__script", "page")[:1000]],
    }


@router.post("/profiles")
def add_profile(request, payload: ProfileIn):
    membership = require_roles(request, *ROLES)
    item = create_profile(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, **payload.dict())
    return {"id": str(item.id), "version": item.version}


@router.post("/runs")
def add_run(request, payload: RunIn):
    membership = require_roles(request, *ROLES)
    tenant_id = membership.institution.tenant_id
    job = ScanJob.objects.filter(id=payload.scan_job_id, tenant_id=tenant_id).select_related("script").first()
    profile = ProcessingProfile.objects.filter(id=payload.profile_id, tenant_id=tenant_id, is_active=True).first()
    if not job or not profile:
        raise HttpError(404, "Scan job or active processing profile not found")
    item = queue_run(tenant_id=tenant_id, actor_id=request.auth.id, scan_job=job, profile=profile)
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/runs/{run_id}/execute")
def execute(request, run_id: str, payload: VersionIn):
    membership = require_roles(request, *ROLES)
    item = execute_run(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, run_id=run_id, expected_version=payload.version)
    return {"id": str(item.id), "status": item.status, "metrics": item.metrics, "version": item.version}


@router.post("/exceptions/{exception_id}/action")
def exception_action(request, exception_id: str, payload: ExceptionIn):
    membership = require_roles(request, *ROLES)
    item = resolve_exception(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, exception_id=exception_id, expected_version=payload.version, status=payload.status, resolution=payload.resolution)
    return {"id": str(item.id), "status": item.status, "version": item.version}
