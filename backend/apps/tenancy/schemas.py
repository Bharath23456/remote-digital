from typing import Any

from ninja import Field, Schema


class InstitutionCreateIn(Schema):
    name: str
    code: str
    kind: str
    parent_id: str | None = None
    policy: dict[str, Any] = Field(default_factory=dict)
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class InstitutionUpdateIn(Schema):
    version: int
    name: str | None = None
    policy: dict[str, Any] | None = None
    is_active: bool | None = None


class TenantProvisionIn(Schema):
    name: str
    code: str
    admin_email: str
    admin_first_name: str
    admin_last_name: str
    subdomain: str | None = None
    plan: str = "standard"
    enabled_modules: list[str] = Field(default_factory=list)
    storage_quota_gb: int = 10
    data_region: str = "in-primary"
    policy: dict[str, Any] = Field(default_factory=dict)


class TenantSwitchIn(Schema):
    tenant_id: str


class TenantUpdateIn(Schema):
    version: int
    status: str | None = None
    plan: str | None = None
    enabled_modules: list[str] | None = None
    storage_quota_gb: int | None = None
    data_region: str | None = None


class TenantDomainIn(Schema):
    hostname: str


class CustomFieldDefinitionIn(Schema):
    tenant_id: str
    form_key: str
    key: str
    label: str
    field_type: str = "text"
    required: bool = False
    options: list[str] = Field(default_factory=list)
    placeholder: str = ""
    help_text: str = ""
    sort_order: int = 0


class CustomFieldDefinitionUpdateIn(Schema):
    version: int
    label: str | None = None
    field_type: str | None = None
    required: bool | None = None
    options: list[str] | None = None
    placeholder: str | None = None
    help_text: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None
