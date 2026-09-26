import uuid
import secrets
import re
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth.models import User
from django.core.validators import validate_slug
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.core.exceptions import ValidationError

from apps.core.services import record_event
from apps.security.models import SecurityPolicy
from apps.tenancy.models import Institution, Membership, TenantAccount, TenantDomain


class TenancyError(Exception):
    pass


class TenancyConflict(TenancyError):
    pass


KIND_LEVEL = {
    Institution.Kind.UNIVERSITY: 0,
    Institution.Kind.AUTHORITY: 0,
    Institution.Kind.CAMPUS: 1,
    Institution.Kind.COLLEGE: 2,
    Institution.Kind.FACULTY: 3,
    Institution.Kind.DEPARTMENT: 4,
}

DEFAULT_MODULES = [
    "configuration", "evaluators", "receiving", "custody", "digitization",
    "anonymisation", "repository", "allocation", "assignment_governance",
    "rubrics", "evaluation", "valuation", "assessment", "operations",
    "services", "security", "audit", "enterprise",
]

BRAND_LOGO_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def branding_for_account(account):
    logo_url = ""
    if account and account.logo_storage_key:
        from apps.repository.storage import signed_object_url

        logo_url, _ = signed_object_url(method="GET", key=account.logo_storage_key)
    return {
        "name": (account.brand_name or account.root_institution.name) if account else "ADMIEZO",
        "description": account.brand_description if account else "",
        "theme": account.brand_theme if account else TenantAccount.Theme.FOREST,
        "logo_url": logo_url,
    }


def _validate_ai_governance(mode, confidence_threshold, model_name, *, tenant_id=None, api_key="", validate_model=True):
    if mode not in SecurityPolicy.AIEvaluationMode.values:
        raise TenancyError("Unsupported AI evaluation mode")
    try:
        threshold = Decimal(str(confidence_threshold)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise TenancyError("AI confidence threshold is invalid") from exc
    model = str(model_name).strip()
    if not Decimal("1") <= threshold <= Decimal("100"):
        raise TenancyError("AI confidence threshold must be between 1 and 100")
    if not re.fullmatch(r"(?:models/)?[A-Za-z0-9._-]{3,80}", model) or len(model) > 80:
        raise TenancyError("ADMIEZO AI Assistant model is invalid")
    from apps.ai_evaluation.provider import AdmiezoAIClient, AdmiezoAIError

    client = AdmiezoAIClient(api_key=api_key) if api_key else AdmiezoAIClient(tenant_id=tenant_id)
    if client.configured and validate_model:
        try:
            client.validate_model(model)
        except AdmiezoAIError as exc:
            raise TenancyError(str(exc)) from exc
    elif mode != SecurityPolicy.AIEvaluationMode.DISABLED or validate_model:
        raise TenancyError("Configure this university’s AI provider API key before saving an AI model")
    return threshold, model


def normalize_hostname(hostname):
    value = hostname.strip().lower().rstrip(".")
    if not value or len(value) > 253 or any(not label or len(label) > 63 for label in value.split(".")):
        raise TenancyError("Domain name is invalid")
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-.")
    if any(character not in allowed for character in value) or any(label.startswith("-") or label.endswith("-") for label in value.split(".")):
        raise TenancyError("Domain name is invalid")
    return value


def tenant_control_plane_rows():
    accounts = TenantAccount.objects.select_related("root_institution").prefetch_related("domains").order_by("root_institution__name")
    rows = []
    for account in accounts:
        policy = SecurityPolicy.objects.filter(tenant_id=account.root_institution.tenant_id).first()
        from apps.ai_evaluation.services import provider_configuration_status

        rows.append({
            "id": str(account.root_institution.tenant_id),
            "account_id": str(account.id),
            "institution_id": str(account.root_institution_id),
            "name": account.root_institution.name,
            "code": account.root_institution.code,
            "slug": account.slug,
            "status": account.status,
            "plan": account.plan,
            "enabled_modules": account.enabled_modules,
            "storage_quota_bytes": account.storage_quota_bytes,
            "data_region": account.data_region,
            "branding": branding_for_account(account),
            "version": account.version,
            "administrator_count": Membership.objects.filter(institution__tenant_id=account.root_institution.tenant_id, role=Membership.Role.UNIVERSITY_ADMIN, is_active=True).count(),
            "ai_policy": {
                "mode": policy.ai_evaluation_mode if policy else SecurityPolicy.AIEvaluationMode.DISABLED,
                "confidence_threshold": float(policy.ai_confidence_threshold) if policy else 85,
                "model_name": policy.ai_model_name if policy else "admiezo-ai-v1",
            },
            "ai_provider": provider_configuration_status(account.root_institution.tenant_id),
            "domains": [
                {
                    "id": str(domain.id),
                    "hostname": domain.hostname,
                    "kind": domain.kind,
                    "status": domain.status,
                    "is_primary": domain.is_primary,
                    "verification_token": domain.verification_token if domain.status == TenantDomain.Status.PENDING else "",
                }
                for domain in account.domains.all()
            ],
            "created_at": account.created_at.isoformat(),
        })
    return rows


def institution_rows(tenant_id):
    return list(
        Institution.objects.filter(tenant_id=tenant_id)
        .values("id", "name", "code", "kind", "parent_id", "policy", "is_active", "version")
        .order_by("kind", "name")
    )


def tenant_rows(user):
    tenant_ids = Membership.objects.filter(user=user, is_active=True, institution__is_active=True).values_list("institution__tenant_id", flat=True).distinct()
    roots = Institution.objects.filter(tenant_id__in=tenant_ids, parent__isnull=True, is_active=True).order_by("name")
    return [{"id": str(item.tenant_id), "name": item.name, "code": item.code, "kind": item.kind} for item in roots]


@transaction.atomic
def provision_tenant(*, actor, name, code, admin_email, admin_first_name, admin_last_name, policy, subdomain=None, plan="standard", enabled_modules=None, storage_quota_gb=10, data_region="in-primary", brand_name="", brand_description="", brand_theme="forest", ai_evaluation_mode="disabled", ai_confidence_threshold=85, ai_model_name="admiezo-ai-v1", ai_api_key=None):
    slug = (subdomain or code).strip().lower()
    try:
        validate_slug(slug)
    except ValidationError as exc:
        raise TenancyError("Subdomain must contain only lowercase letters, numbers, underscores or hyphens") from exc
    if not 2 <= len(slug) <= 63 or slug.startswith("-") or slug.endswith("-"):
        raise TenancyError("Subdomain must be between 2 and 63 characters and cannot start or end with a hyphen")
    if plan not in TenantAccount.Plan.values:
        raise TenancyError("Unsupported subscription plan")
    if not 1 <= storage_quota_gb <= 10240:
        raise TenancyError("Storage quota must be between 1 GB and 10 TB")
    if brand_theme not in TenantAccount.Theme.values:
        raise TenancyError("Unsupported university theme")
    api_key = str(ai_api_key or "").strip()
    ai_confidence_threshold, ai_model_name = _validate_ai_governance(
        ai_evaluation_mode,
        ai_confidence_threshold,
        ai_model_name,
        api_key=api_key,
        validate_model=bool(api_key) or ai_model_name != "admiezo-ai-v1",
    )
    tenant_id = uuid.uuid4()
    root = Institution.objects.create(tenant_id=tenant_id, name=name.strip(), code=code.strip().lower(), kind=Institution.Kind.UNIVERSITY, policy=policy)
    account = TenantAccount.objects.create(
        root_institution=root,
        slug=slug,
        status=TenantAccount.Status.ACTIVE,
        plan=plan,
        enabled_modules=sorted(set(enabled_modules or DEFAULT_MODULES) - {"ai_evaluation"}),
        storage_quota_bytes=storage_quota_gb * 1024 * 1024 * 1024,
        data_region=data_region.strip().lower()[:40],
        brand_name=brand_name.strip()[:120],
        brand_description=brand_description.strip()[:240],
        brand_theme=brand_theme,
    )
    hostname = normalize_hostname(f"{slug}.{settings.TENANT_BASE_DOMAIN}")
    domain = TenantDomain.objects.create(
        tenant_account=account,
        hostname=hostname,
        kind=TenantDomain.Kind.MANAGED,
        status=TenantDomain.Status.ACTIVE,
        is_primary=True,
        verified_at=timezone.now(),
    )
    email = admin_email.strip().lower()
    admin, created = User.objects.get_or_create(username=email, defaults={"email": email, "first_name": admin_first_name.strip(), "last_name": admin_last_name.strip()})
    temporary_password = ""
    if created:
        temporary_password = secrets.token_urlsafe(15)
        admin.set_password(temporary_password)
        admin.save(update_fields=["password"])
    Membership.objects.create(user=admin, institution=root, role=Membership.Role.UNIVERSITY_ADMIN, permissions=["*"], enabled_modules=account.enabled_modules, must_change_password=created)
    Membership.objects.get_or_create(user=actor, institution=root, defaults={"role": Membership.Role.PLATFORM_ADMIN, "permissions": ["*"], "enabled_modules": account.enabled_modules})
    SecurityPolicy.objects.create(
        tenant_id=tenant_id,
        ai_evaluation_mode=ai_evaluation_mode,
        ai_confidence_threshold=ai_confidence_threshold,
        ai_model_name=ai_model_name,
    )
    from apps.ai_evaluation.services import configure_provider, synchronize_ai_governance

    if api_key:
        configure_provider(tenant_id=tenant_id, actor_id=actor.id, api_key=api_key, version=0, model_name=ai_model_name)

    synchronize_ai_governance(tenant_id=tenant_id, mode=ai_evaluation_mode)
    record_event(tenant_id=tenant_id, actor_id=actor.id, action="tenancy.tenant.provisioned", aggregate="Institution", aggregate_id=root.id, payload={"code": root.code, "admin_user_id": admin.id, "hostname": domain.hostname, "plan": account.plan})
    return root, admin, account, domain, temporary_password


@transaction.atomic
def update_tenant_account(*, actor_id, tenant_id, version, changes):
    account = TenantAccount.objects.select_for_update().select_related("root_institution").filter(root_institution__tenant_id=tenant_id).first()
    if not account:
        raise TenancyError("University was not found")
    if account.version != version:
        raise TenancyConflict("University account was changed by another operator")
    if changes.get("status") and changes["status"] not in TenantAccount.Status.values:
        raise TenancyError("Unsupported university status")
    if changes.get("plan") and changes["plan"] not in TenantAccount.Plan.values:
        raise TenancyError("Unsupported subscription plan")
    if changes.get("brand_theme") and changes["brand_theme"] not in TenantAccount.Theme.values:
        raise TenancyError("Unsupported university theme")
    policy = SecurityPolicy.objects.select_for_update().filter(tenant_id=tenant_id).first() or SecurityPolicy(tenant_id=tenant_id)
    api_key = str(changes.pop("ai_api_key", "") or "").strip()
    provider_version = changes.pop("ai_provider_version", 0)
    mode = changes.get("ai_evaluation_mode", policy.ai_evaluation_mode)
    threshold = changes.get("ai_confidence_threshold", policy.ai_confidence_threshold)
    model_name = changes.get("ai_model_name", policy.ai_model_name)
    threshold, model_name = _validate_ai_governance(
        mode,
        threshold,
        model_name,
        tenant_id=tenant_id,
        api_key=api_key,
        validate_model=(
            bool(api_key)
            or model_name != policy.ai_model_name
            or (
                policy.ai_evaluation_mode == SecurityPolicy.AIEvaluationMode.DISABLED
                and mode != SecurityPolicy.AIEvaluationMode.DISABLED
            )
        ),
    )
    for field in ("status", "plan", "enabled_modules", "data_region", "brand_name", "brand_description", "brand_theme"):
        if changes.get(field) is not None:
            value = changes[field]
            if field == "enabled_modules":
                modules = set(value) - {"ai_evaluation"}
                if mode != SecurityPolicy.AIEvaluationMode.DISABLED:
                    modules.add("ai_evaluation")
                value = sorted(modules)
            elif field == "brand_name":
                value = str(value).strip()[:120]
            elif field == "brand_description":
                value = str(value).strip()[:240]
            setattr(account, field, value)
    if changes.get("storage_quota_gb") is not None:
        if not 1 <= changes["storage_quota_gb"] <= 10240:
            raise TenancyError("Storage quota must be between 1 GB and 10 TB")
        account.storage_quota_bytes = changes["storage_quota_gb"] * 1024 * 1024 * 1024
    account.version += 1
    account.save()
    active = account.status == TenantAccount.Status.ACTIVE
    if account.root_institution.is_active != active:
        account.root_institution.is_active = active
        account.root_institution.version += 1
        account.root_institution.save(update_fields=["is_active", "version", "updated_at"])
    policy.ai_evaluation_mode = mode
    policy.ai_confidence_threshold = threshold
    policy.ai_model_name = model_name
    if any(key in changes for key in ("ai_evaluation_mode", "ai_confidence_threshold", "ai_model_name")):
        policy.version = policy.version + 1 if policy.pk else 1
        policy.save()
    from apps.ai_evaluation.services import configure_provider, synchronize_ai_governance

    if api_key:
        configure_provider(
            tenant_id=tenant_id,
            actor_id=actor_id,
            api_key=api_key,
            version=provider_version,
            model_name=model_name,
        )

    synchronize_ai_governance(tenant_id=tenant_id, mode=mode)
    account.refresh_from_db()
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="tenancy.tenant.updated", aggregate="TenantAccount", aggregate_id=account.id, payload={"status": account.status, "plan": account.plan, "version": account.version})
    return account


def create_tenant_logo_upload(*, tenant_id, content_type, maximum_bytes):
    extension = BRAND_LOGO_TYPES.get(content_type)
    if not extension:
        raise TenancyError("Use a PNG, JPEG or WebP logo")
    if not 1 <= maximum_bytes <= 2_000_000:
        raise TenancyError("University logos must be smaller than 2 MB")
    if not TenantAccount.objects.filter(root_institution__tenant_id=tenant_id).exists():
        raise TenancyError("University was not found")
    from apps.repository.storage import signed_object_url

    storage_key = f"branding/{tenant_id}/{uuid.uuid4().hex}.{extension}"
    upload_url, expires_at = signed_object_url(method="PUT", key=storage_key, content_type=content_type, max_bytes=maximum_bytes)
    return storage_key, upload_url, expires_at


def finalize_tenant_logo(*, actor_id, tenant_id, version, storage_key, content_type):
    expected_prefix = f"branding/{tenant_id}/"
    if not storage_key.startswith(expected_prefix) or content_type not in BRAND_LOGO_TYPES:
        raise TenancyError("Logo upload does not belong to this university")
    from apps.repository.storage import delete_object, read_object_metadata

    try:
        metadata = read_object_metadata(storage_key)
    except (OSError, ValueError, KeyError) as exc:
        raise TenancyError("Uploaded logo could not be verified") from exc
    if metadata.mime_type != content_type or metadata.byte_size > 2_000_000:
        raise TenancyError("Uploaded logo type or size does not match the request")
    with transaction.atomic():
        account = TenantAccount.objects.select_for_update().select_related("root_institution").filter(root_institution__tenant_id=tenant_id).first()
        if not account:
            raise TenancyError("University was not found")
        if account.version != version:
            raise TenancyConflict("University account was changed by another operator")
        previous_key = account.logo_storage_key
        account.logo_storage_key = storage_key
        account.logo_mime_type = content_type
        account.version += 1
        account.save(update_fields=["logo_storage_key", "logo_mime_type", "version", "updated_at"])
        record_event(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="tenancy.branding.logo.updated",
            aggregate="TenantAccount",
            aggregate_id=account.id,
            payload={"content_type": content_type, "byte_size": metadata.byte_size, "version": account.version},
        )
    if previous_key and previous_key != storage_key:
        try:
            delete_object(previous_key)
        except OSError:
            pass
    return account


@transaction.atomic
def add_custom_domain(*, actor_id, tenant_id, hostname):
    account = TenantAccount.objects.select_related("root_institution").filter(root_institution__tenant_id=tenant_id).first()
    if not account:
        raise TenancyError("University was not found")
    normalized = normalize_hostname(hostname)
    if TenantDomain.objects.filter(hostname__iexact=normalized).exists():
        raise TenancyConflict("Domain is already assigned")
    domain = TenantDomain.objects.create(
        tenant_account=account,
        hostname=normalized,
        kind=TenantDomain.Kind.CUSTOM,
        verification_token=secrets.token_hex(24),
    )
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="tenancy.domain.requested", aggregate="TenantDomain", aggregate_id=domain.id, payload={"hostname": normalized})
    return domain


@transaction.atomic
def create_institution(*, tenant_id, actor_id, name, code, kind, parent_id=None, policy=None):
    if kind not in KIND_LEVEL:
        raise TenancyError("Unsupported institution type")
    parent = None
    if parent_id:
        parent = Institution.objects.filter(id=parent_id, tenant_id=tenant_id, is_active=True).first()
        if not parent:
            raise TenancyError("Parent institution was not found")
        if KIND_LEVEL[kind] <= KIND_LEVEL[parent.kind]:
            raise TenancyError("Child institution must be below its parent in the hierarchy")
    elif kind not in {Institution.Kind.UNIVERSITY, Institution.Kind.AUTHORITY}:
        raise TenancyError("This institution type requires a parent")
    try:
        institution = Institution.objects.create(
            tenant_id=tenant_id,
            name=name.strip(),
            code=code.strip().lower(),
            kind=kind,
            parent=parent,
            policy=policy or {},
        )
    except IntegrityError as exc:
        raise TenancyConflict("Institution code already exists in this tenant") from exc
    record_event(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="tenancy.institution.created",
        aggregate="Institution",
        aggregate_id=institution.id,
        payload={"code": institution.code, "kind": institution.kind, "parent_id": str(parent.id) if parent else None},
    )
    return institution


@transaction.atomic
def update_institution(*, tenant_id, actor_id, institution_id, version, changes):
    institution = Institution.objects.select_for_update().filter(id=institution_id, tenant_id=tenant_id).first()
    if not institution:
        raise TenancyError("Institution was not found")
    if institution.version != version:
        raise TenancyConflict("Institution was changed by another user")
    if institution.parent_id is None and changes.get("is_active") is False:
        raise TenancyError("Tenant root institution cannot be disabled")
    for field in ("name", "policy", "is_active"):
        if field in changes and changes[field] is not None:
            setattr(institution, field, changes[field])
    institution.version += 1
    institution.save()
    record_event(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="tenancy.institution.updated",
        aggregate="Institution",
        aggregate_id=institution.id,
        payload={"fields": sorted(changes), "version": institution.version},
    )
    return institution
