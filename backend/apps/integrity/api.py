from ninja import Router, Schema
from ninja.errors import HttpError

from apps.core.authz import require_roles
from apps.repository.models import ScriptAsset
from apps.tenancy.models import Membership

from .models import IntegrityAlert, IntegrityCheck, IntegrityManifest
from .services import create_manifest, transition_alert, verify_manifest


router = Router(tags=["Script integrity and anti-tampering"])
ROLES = (
    Membership.Role.UNIVERSITY_ADMIN,
    Membership.Role.EXAM_CONTROLLER,
    Membership.Role.SCANNER_OPERATOR,
    Membership.Role.AUDITOR,
)


class AlertIn(Schema):
    target: str


@router.get("/catalog")
def catalog(request):
    membership = require_roles(request, *ROLES)
    tenant_id = membership.institution.tenant_id
    return {
        "unsigned_assets": [{"id": str(item.id), "script": item.script.script_code, "kind": item.kind, "page": item.page_number, "sha256": item.sha256} for item in ScriptAsset.objects.filter(tenant_id=tenant_id, deleted_at__isnull=True, integrity_manifest__isnull=True).select_related("script")[:500]],
        "manifests": [{"id": str(item.id), "asset_id": str(item.asset_id), "script": item.asset.script.script_code, "kind": item.asset.kind, "page": item.asset.page_number, "file_sha256": item.file_sha256, "status": item.verification_status, "signed_at": item.signed_at.isoformat(), "last_verified_at": item.last_verified_at.isoformat() if item.last_verified_at else None} for item in IntegrityManifest.objects.filter(tenant_id=tenant_id).select_related("asset__script")],
        "checks": [{"id": str(item.id), "manifest_id": str(item.manifest_id), "status": item.status, "checked_by": item.checked_by, "checked_at": item.checked_at.isoformat(), "observed": item.observed} for item in IntegrityCheck.objects.filter(tenant_id=tenant_id)[:1000]],
        "alerts": [{"id": str(item.id), "manifest_id": str(item.manifest_id), "script": item.manifest.asset.script.script_code, "kind": item.kind, "severity": item.severity, "status": item.status, "details": item.details, "evidence": [{"id": str(evidence.id), "type": evidence.evidence_type, "sha256": evidence.sha256, "preserved_at": evidence.preserved_at.isoformat()} for evidence in item.evidence.all()]} for item in IntegrityAlert.objects.filter(tenant_id=tenant_id).select_related("manifest__asset__script").prefetch_related("evidence")],
    }


@router.post("/assets/{asset_id}/manifest")
def manifest(request, asset_id: str):
    membership = require_roles(request, *ROLES)
    asset = ScriptAsset.objects.filter(id=asset_id, tenant_id=membership.institution.tenant_id, deleted_at__isnull=True).first()
    if not asset:
        raise HttpError(404, "Repository asset not found")
    item, created = create_manifest(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, asset=asset)
    return {"id": str(item.id), "created": created, "status": item.verification_status}


@router.post("/manifests/{manifest_id}/verify")
def verify(request, manifest_id: str):
    membership = require_roles(request, *ROLES)
    check, alert = verify_manifest(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, manifest_id=manifest_id, checked_by="operator")
    return {"id": str(check.id), "status": check.status, "alert_id": str(alert.id) if alert else None}


@router.post("/alerts/{alert_id}/action")
def alert_action(request, alert_id: str, payload: AlertIn):
    membership = require_roles(request, *ROLES)
    item = transition_alert(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, alert_id=alert_id, target=payload.target)
    return {"id": str(item.id), "status": item.status}
