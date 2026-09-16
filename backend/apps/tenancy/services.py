import uuid
import secrets

from django.conf import settings
from django.contrib.auth.models import User
from django.core.validators import validate_slug
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.core.exceptions import ValidationError

from apps.core.services import record_event
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
    return [
        {
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
            "version": account.version,
            "administrator_count": Membership.objects.filter(institution__tenant_id=account.root_institution.tenant_id, role=Membership.Role.UNIVERSITY_ADMIN, is_active=True).count(),
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
        }
        for account in accounts
    ]


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
def provision_tenant(*, actor, name, code, admin_email, admin_first_name, admin_last_name, policy, subdomain=None, plan="standard", enabled_modules=None, storage_quota_gb=10, data_region="in-primary"):
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
    tenant_id = uuid.uuid4()
    root = Institution.objects.create(tenant_id=tenant_id, name=name.strip(), code=code.strip().lower(), kind=Institution.Kind.UNIVERSITY, policy=policy)
    account = TenantAccount.objects.create(
        root_institution=root,
        slug=slug,
        status=TenantAccount.Status.ACTIVE,
        plan=plan,
        enabled_modules=enabled_modules or DEFAULT_MODULES,
        storage_quota_bytes=storage_quota_gb * 1024 * 1024 * 1024,
        data_region=data_region.strip().lower()[:40],
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
    for field in ("status", "plan", "enabled_modules", "data_region"):
        if changes.get(field) is not None:
            setattr(account, field, changes[field])
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
    record_event(tenant_id=tenant_id, actor_id=actor_id, action="tenancy.tenant.updated", aggregate="TenantAccount", aggregate_id=account.id, payload={"status": account.status, "plan": account.plan, "version": account.version})
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
