from ninja import Router, Schema
from ninja.errors import HttpError

from apps.configuration.models import Paper
from apps.core.authz import membership_for, require_roles
from apps.custody.models import Script
from apps.tenancy.models import Membership

from .models import ScanBatch, ScanJob, ScannerDevice, ScannerMaintenanceAlert
from .services import assign_next_batch, complete_job, create_batch, fail_job, failover_batch, record_heartbeat, register_scanner, transition_batch, transition_maintenance_alert


router = Router(tags=["High-speed scanning"])
ROLES = (Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER)


class ScannerIn(Schema):
    code: str
    name: str
    location: str
    topology: str = "central"
    capabilities: dict = {}
    firmware_version: str = ""


class HeartbeatIn(Schema):
    version: int
    status: str
    pages_per_minute: float = 0
    pages_scanned_today: int = 0
    failure_rate: float = 0
    telemetry: dict = {}


class BatchIn(Schema):
    reference: str
    paper_id: str
    script_ids: list[str]
    topology: str = "central"
    duplex: bool = True
    adf: bool = True
    priority: int = 3


class VersionActionIn(Schema):
    version: int
    status: str | None = None


class CompleteJobIn(Schema):
    version: int
    pages: list[dict]


class FailJobIn(Schema):
    version: int
    code: str
    message: str
    recoverable: bool = True


@router.get("/catalog")
def catalog(request):
    tenant_id = membership_for(request).institution.tenant_id
    scanners = ScannerDevice.objects.filter(tenant_id=tenant_id).order_by("code")
    batches = ScanBatch.objects.filter(tenant_id=tenant_id).select_related("paper", "scanner").order_by("priority", "created_at")
    return {
        "papers": [{"id": str(item.id), "code": item.code, "title": item.title} for item in Paper.objects.filter(tenant_id=tenant_id).order_by("code")],
        "eligible_scripts": [{"id": str(item.id), "code": item.script_code, "paper_id": str(item.paper_id), "paper": item.paper.code, "state": item.state, "pages": item.page_count, "version": item.version} for item in Script.objects.filter(tenant_id=tenant_id, state__in=[Script.State.REGISTERED, Script.State.SCANNED]).select_related("paper").order_by("script_code")],
        "scanners": [{"id": str(item.id), "code": item.code, "name": item.name, "location": item.location, "topology": item.topology, "capabilities": item.capabilities, "status": item.status, "pages_per_minute": float(item.pages_per_minute), "pages_scanned_today": item.pages_scanned_today, "failure_rate": float(item.failure_rate), "last_heartbeat_at": item.last_heartbeat_at.isoformat() if item.last_heartbeat_at else None, "version": item.version} for item in scanners],
        "batches": [{"id": str(item.id), "reference": item.reference, "paper": item.paper.code, "scanner": item.scanner.code if item.scanner else None, "status": item.status, "expected_scripts": item.expected_scripts, "completed_scripts": item.completed_scripts, "failed_scripts": item.failed_scripts, "priority": item.priority, "duplex": item.duplex, "adf": item.adf, "created_at": item.created_at.isoformat(), "version": item.version} for item in batches[:500]],
        "jobs": [{"id": str(item.id), "batch_id": str(item.batch_id), "script": item.script.script_code, "status": item.status, "expected_pages": item.expected_pages, "scanned_pages": item.scanned_pages, "attempt": item.attempt, "error_code": item.error_code, "version": item.version} for item in ScanJob.objects.filter(tenant_id=tenant_id).select_related("script")[:2000]],
        "maintenance": [{"id": str(item.id), "scanner": item.scanner.code, "severity": item.severity, "reason": item.reason, "status": item.status} for item in ScannerMaintenanceAlert.objects.filter(tenant_id=tenant_id).select_related("scanner")[:500]],
    }


@router.post("/scanners")
def create_scanner(request, payload: ScannerIn):
    membership = require_roles(request, *ROLES)
    item = register_scanner(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, **payload.dict())
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/scanners/{scanner_id}/heartbeat")
def scanner_heartbeat(request, scanner_id: str, payload: HeartbeatIn):
    membership = require_roles(request, *ROLES)
    item = record_heartbeat(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, scanner_id=scanner_id, expected_version=payload.version, **payload.dict(exclude={"version"}))
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/batches")
def add_batch(request, payload: BatchIn):
    membership = require_roles(request, *ROLES)
    tenant_id = membership.institution.tenant_id
    paper = Paper.objects.filter(id=payload.paper_id, tenant_id=tenant_id).first()
    scripts = list(Script.objects.filter(id__in=payload.script_ids, tenant_id=tenant_id))
    if not paper or len(scripts) != len(set(payload.script_ids)):
        raise HttpError(404, "Paper or one or more scripts not found")
    item = create_batch(tenant_id=tenant_id, actor_id=request.auth.id, reference=payload.reference, paper=paper, scripts=scripts, topology=payload.topology, duplex=payload.duplex, adf=payload.adf, priority=payload.priority)
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/queue/assign")
def assign_queue(request, scanner_id: str | None = None):
    membership = require_roles(request, *ROLES)
    item = assign_next_batch(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, scanner_id=scanner_id)
    return {"id": str(item.id), "scanner_id": str(item.scanner_id), "status": item.status, "version": item.version}


@router.post("/batches/{batch_id}/action")
def batch_action(request, batch_id: str, payload: VersionActionIn):
    membership = require_roles(request, *ROLES)
    if not payload.status:
        raise HttpError(422, "Target status is required")
    item = transition_batch(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, batch_id=batch_id, expected_version=payload.version, to_status=payload.status)
    return {"id": str(item.id), "status": item.status, "version": item.version}


@router.post("/batches/{batch_id}/failover")
def failover(request, batch_id: str, payload: VersionActionIn):
    membership = require_roles(request, *ROLES)
    item = failover_batch(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, batch_id=batch_id, expected_version=payload.version)
    return {"id": str(item.id), "scanner_id": str(item.scanner_id), "status": item.status, "version": item.version}


@router.post("/jobs/{job_id}/complete")
def finish_job(request, job_id: str, payload: CompleteJobIn):
    membership = require_roles(request, *ROLES)
    item = complete_job(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, job_id=job_id, expected_version=payload.version, page_manifest=payload.pages)
    return {"id": str(item.id), "status": item.status, "scanned_pages": item.scanned_pages, "version": item.version}


@router.post("/jobs/{job_id}/fail")
def mark_job_failed(request, job_id: str, payload: FailJobIn):
    membership = require_roles(request, *ROLES)
    item = fail_job(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, job_id=job_id, expected_version=payload.version, code=payload.code, message=payload.message, recoverable=payload.recoverable)
    return {"id": str(item.id), "status": item.status, "attempt": item.attempt, "version": item.version}


@router.post("/maintenance/{alert_id}/action")
def maintenance_action(request, alert_id: str, payload: VersionActionIn):
    membership = require_roles(request, *ROLES)
    if not payload.status:
        raise HttpError(422, "Target status is required")
    item = transition_maintenance_alert(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, alert_id=alert_id, target=payload.status)
    return {"id": str(item.id), "status": item.status}
