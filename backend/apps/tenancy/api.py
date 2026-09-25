from django.db import IntegrityError, transaction
from ninja import Router
from ninja.errors import HttpError

from apps.core.authz import require_roles
from apps.core.models import AuditEvent
from apps.core.services import record_event
from apps.identity_auth.services import active_session_for_request
from apps.tenancy.custom_fields import FORM_CATALOG, persist_custom_values, serialize_definition, validate_custom_values, validate_definition
from apps.tenancy.models import CustomFieldDefinition, CustomFieldRecord, Institution, Membership
from apps.tenancy.schemas import CustomFieldDefinitionIn, CustomFieldDefinitionUpdateIn, InstitutionCreateIn, InstitutionUpdateIn, TenantDomainIn, TenantLogoFinalizeIn, TenantLogoUploadIn, TenantProvisionIn, TenantSwitchIn, TenantUpdateIn
from apps.tenancy.services import add_custom_domain, branding_for_account, create_institution, create_tenant_logo_upload, finalize_tenant_logo, institution_rows, provision_tenant, tenant_control_plane_rows, tenant_rows, TenancyConflict, TenancyError, update_institution, update_tenant_account


router = Router(tags=["Enterprise configuration"])
ADMIN_ROLES = (Membership.Role.UNIVERSITY_ADMIN, Membership.Role.EXAM_CONTROLLER)


@router.get("/tenants")
def list_tenants(request):
    return tenant_rows(request.auth)


@router.get("/domain-context", auth=None)
def domain_context(request):
    domain = getattr(request, "tenant_domain", None)
    if not domain:
        return {"scope": "platform", "hostname": request.get_host().split(":", 1)[0].lower(), "university": None}
    account = domain.tenant_account
    return {
        "scope": "university",
        "hostname": domain.hostname,
        "university": {
            "id": str(account.root_institution.tenant_id),
            "name": account.root_institution.name,
            "code": account.root_institution.code,
            "status": account.status,
            "branding": branding_for_account(account),
        },
    }


@router.get("/control-plane")
def control_plane(request):
    require_roles(request, Membership.Role.PLATFORM_ADMIN)
    rows = tenant_control_plane_rows()
    return {
        "summary": {
            "universities": len(rows),
            "active": sum(item["status"] == "active" for item in rows),
            "domains": sum(len(item["domains"]) for item in rows),
            "administrators": sum(item["administrator_count"] for item in rows),
        },
        "universities": rows,
    }


@router.get("/form-fields")
def tenant_form_fields(request, form_key: str):
    membership = require_roles(
        request,
        Membership.Role.PLATFORM_ADMIN,
        Membership.Role.UNIVERSITY_ADMIN,
        Membership.Role.EXAM_CONTROLLER,
        Membership.Role.RECEIVING_OFFICER,
        Membership.Role.AUDITOR,
    )
    if form_key not in FORM_CATALOG:
        raise HttpError(422, "Unsupported form")
    rows = CustomFieldDefinition.objects.filter(
        tenant_id=membership.institution.tenant_id,
        form_key=form_key,
        is_active=True,
    )
    return {"form_key": form_key, "form_name": FORM_CATALOG[form_key], "fields": [serialize_definition(item) for item in rows]}


@router.get("/form-values")
def tenant_form_values(request, form_key: str, record_id: str):
    membership = require_roles(
        request,
        Membership.Role.PLATFORM_ADMIN,
        Membership.Role.UNIVERSITY_ADMIN,
        Membership.Role.EXAM_CONTROLLER,
        Membership.Role.RECEIVING_OFFICER,
        Membership.Role.AUDITOR,
    )
    if form_key not in FORM_CATALOG:
        raise HttpError(422, "Unsupported form")
    item = CustomFieldRecord.objects.filter(
        tenant_id=membership.institution.tenant_id,
        form_key=form_key,
        record_id=record_id,
    ).first()
    return {"form_key": form_key, "record_id": record_id, "values": item.values if item else {}}


@router.get("/control-plane/form-fields")
def control_plane_form_fields(request, tenant_id: str):
    require_roles(request, Membership.Role.PLATFORM_ADMIN)
    if not Institution.objects.filter(tenant_id=tenant_id, parent__isnull=True).exists():
        raise HttpError(404, "University was not found")
    rows = CustomFieldDefinition.objects.filter(tenant_id=tenant_id)
    return {"forms": [{"key": key, "label": label} for key, label in FORM_CATALOG.items()], "fields": [serialize_definition(item) for item in rows]}


@router.post("/control-plane/form-fields")
def create_form_field(request, payload: CustomFieldDefinitionIn):
    require_roles(request, Membership.Role.PLATFORM_ADMIN)
    if not Institution.objects.filter(tenant_id=payload.tenant_id, parent__isnull=True).exists():
        raise HttpError(404, "University was not found")
    values = validate_definition(payload.dict(exclude={"tenant_id"}))
    try:
        with transaction.atomic():
            item = CustomFieldDefinition.objects.create(tenant_id=payload.tenant_id, **values)
            record_event(tenant_id=payload.tenant_id, actor_id=request.auth.id, action="tenancy.form_field.created", aggregate="CustomFieldDefinition", aggregate_id=item.id, payload={"form_key": item.form_key, "key": item.key})
    except IntegrityError as exc:
        raise HttpError(409, "This field key already exists on the selected form") from exc
    return serialize_definition(item)


@router.patch("/control-plane/form-fields/{field_id}")
def update_form_field(request, field_id: str, payload: CustomFieldDefinitionUpdateIn):
    require_roles(request, Membership.Role.PLATFORM_ADMIN)
    with transaction.atomic():
        item = CustomFieldDefinition.objects.select_for_update().filter(id=field_id).first()
        if not item:
            raise HttpError(404, "Custom field was not found")
        if item.version != payload.version:
            raise HttpError(409, "Custom field was changed by another administrator")
        changes = payload.dict(exclude={"version"}, exclude_none=True)
        candidate = {
            "form_key": item.form_key,
            "key": item.key,
            "label": changes.get("label", item.label),
            "field_type": changes.get("field_type", item.field_type),
            "options": changes.get("options", item.options),
        }
        validate_definition(candidate)
        for key, value in changes.items():
            setattr(item, key, value)
        item.version += 1
        item.save()
        record_event(tenant_id=item.tenant_id, actor_id=request.auth.id, action="tenancy.form_field.updated", aggregate="CustomFieldDefinition", aggregate_id=item.id, payload={"changes": sorted(changes), "version": item.version})
    return serialize_definition(item)


@router.get("/control-plane/audit")
def control_plane_audit(request, limit: int = 100):
    require_roles(request, Membership.Role.PLATFORM_ADMIN)
    events = list(AuditEvent.objects.order_by("-occurred_at")[: max(1, min(limit, 500))])
    tenant_ids = {event.tenant_id for event in events}
    tenant_names = dict(
        Institution.objects.filter(tenant_id__in=tenant_ids, parent__isnull=True)
        .values_list("tenant_id", "name")
    )
    return [
        {
            "id": str(event.id),
            "university": tenant_names.get(event.tenant_id, "Unknown university"),
            "action": event.action,
            "aggregate_type": event.aggregate_type,
            "aggregate_id": event.aggregate_id,
            "actor_id": event.actor_id,
            "created_at": event.occurred_at.isoformat(),
        }
        for event in events
    ]


@router.post("/tenants")
def create_tenant(request, payload: TenantProvisionIn):
    require_roles(request, Membership.Role.PLATFORM_ADMIN)
    try:
        institution, admin, account, domain, temporary_password = provision_tenant(actor=request.auth, **payload.dict())
    except IntegrityError as exc:
        raise HttpError(409, "The tenant or administrator membership already exists") from exc
    except TenancyConflict as exc:
        raise HttpError(409, str(exc)) from exc
    except TenancyError as exc:
        raise HttpError(422, str(exc)) from exc
    return {"id": str(institution.tenant_id), "institution_id": str(institution.id), "name": institution.name, "admin_user_id": admin.id, "account_id": str(account.id), "hostname": domain.hostname, "temporary_password": temporary_password, "administrator_existing": not bool(temporary_password), "version": account.version, "branding": branding_for_account(account)}


@router.post("/tenants/switch")
def switch_tenant(request, payload: TenantSwitchIn):
    resolved_tenant_id = getattr(request, "resolved_tenant_id", None)
    if resolved_tenant_id and str(resolved_tenant_id) != payload.tenant_id:
        raise HttpError(409, "A university domain cannot switch to a different university")
    membership = Membership.objects.filter(user=request.auth, is_active=True, institution__is_active=True, institution__tenant_id=payload.tenant_id).select_related("institution").first()
    if not membership:
        raise HttpError(403, "You do not have access to that university")
    with transaction.atomic():
        session = active_session_for_request(request)
        if session:
            session.tenant_id = membership.institution.tenant_id
            session.save(update_fields=["tenant_id", "updated_at"])
        request.session["active_tenant_id"] = str(membership.institution.tenant_id)
        record_event(tenant_id=membership.institution.tenant_id, actor_id=request.auth.id, action="tenancy.tenant.switched", aggregate="Institution", aggregate_id=membership.institution.id)
    return {"tenant_id": str(membership.institution.tenant_id), "name": membership.institution.name}


@router.patch("/tenants/{tenant_id}")
def edit_tenant(request, tenant_id: str, payload: TenantUpdateIn):
    require_roles(request, Membership.Role.PLATFORM_ADMIN)
    try:
        account = update_tenant_account(actor_id=request.auth.id, tenant_id=tenant_id, version=payload.version, changes=payload.dict(exclude={"version"}, exclude_none=True))
    except TenancyConflict as exc:
        raise HttpError(409, str(exc)) from exc
    except TenancyError as exc:
        raise HttpError(422, str(exc)) from exc
    return {"id": str(account.root_institution.tenant_id), "status": account.status, "plan": account.plan, "version": account.version, "branding": branding_for_account(account)}


@router.post("/tenants/{tenant_id}/branding/logo-upload")
def create_logo_upload(request, tenant_id: str, payload: TenantLogoUploadIn):
    require_roles(request, Membership.Role.PLATFORM_ADMIN)
    try:
        storage_key, upload_url, expires_at = create_tenant_logo_upload(
            tenant_id=tenant_id,
            content_type=payload.content_type,
            maximum_bytes=payload.maximum_bytes,
        )
    except TenancyError as exc:
        raise HttpError(422, str(exc)) from exc
    return {"storage_key": storage_key, "upload_url": upload_url, "expires_at": expires_at, "headers": {"Content-Type": payload.content_type}}


@router.post("/tenants/{tenant_id}/branding/logo-finalize")
def finalize_logo_upload(request, tenant_id: str, payload: TenantLogoFinalizeIn):
    require_roles(request, Membership.Role.PLATFORM_ADMIN)
    try:
        account = finalize_tenant_logo(
            actor_id=request.auth.id,
            tenant_id=tenant_id,
            version=payload.version,
            storage_key=payload.storage_key,
            content_type=payload.content_type,
        )
    except TenancyConflict as exc:
        raise HttpError(409, str(exc)) from exc
    except TenancyError as exc:
        raise HttpError(422, str(exc)) from exc
    return {"version": account.version, "branding": branding_for_account(account)}


@router.post("/tenants/{tenant_id}/domains")
def create_custom_domain(request, tenant_id: str, payload: TenantDomainIn):
    require_roles(request, Membership.Role.PLATFORM_ADMIN)
    try:
        domain = add_custom_domain(actor_id=request.auth.id, tenant_id=tenant_id, hostname=payload.hostname)
    except TenancyConflict as exc:
        raise HttpError(409, str(exc)) from exc
    except TenancyError as exc:
        raise HttpError(422, str(exc)) from exc
    return {"id": str(domain.id), "hostname": domain.hostname, "status": domain.status, "verification_record": f"_admiezo-verification.{domain.hostname}", "verification_token": domain.verification_token}


@router.get("/institutions")
def list_institutions(request):
    membership = require_roles(request, *ADMIN_ROLES, Membership.Role.AUDITOR)
    return institution_rows(membership.institution.tenant_id)


@router.post("/institutions")
def add_institution(request, payload: InstitutionCreateIn):
    membership = require_roles(request, *ADMIN_ROLES)
    tenant_id = membership.institution.tenant_id
    values = payload.dict()
    custom_fields = validate_custom_values(tenant_id=tenant_id, form_key="institution", values=values.pop("custom_fields"))
    try:
        institution = create_institution(
            tenant_id=tenant_id,
            actor_id=request.auth.id,
            **values,
        )
    except TenancyConflict as exc:
        raise HttpError(409, str(exc)) from exc
    except TenancyError as exc:
        raise HttpError(422, str(exc)) from exc
    persist_custom_values(tenant_id=tenant_id, actor_id=request.auth.id, form_key="institution", record_id=institution.id, values=custom_fields)
    return {"id": str(institution.id), "version": institution.version}


@router.patch("/institutions/{institution_id}")
def edit_institution(request, institution_id: str, payload: InstitutionUpdateIn):
    membership = require_roles(request, *ADMIN_ROLES)
    try:
        institution = update_institution(
            tenant_id=membership.institution.tenant_id,
            actor_id=request.auth.id,
            institution_id=institution_id,
            version=payload.version,
            changes=payload.dict(exclude={"version"}, exclude_none=True),
        )
    except TenancyConflict as exc:
        raise HttpError(409, str(exc)) from exc
    except TenancyError as exc:
        raise HttpError(404, str(exc)) from exc
    return {"id": str(institution.id), "version": institution.version, "is_active": institution.is_active}
