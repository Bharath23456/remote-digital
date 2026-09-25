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
    brand_name: str = ""
    brand_description: str = ""
    brand_theme: str = "forest"
    policy: dict[str, Any] = Field(default_factory=dict)
    ai_evaluation_mode: str = "disabled"
    ai_confidence_threshold: float = Field(default=85, ge=1, le=100)
    ai_model_name: str = "admiezo-ai-v1"
    ai_api_key: str | None = Field(default=None, min_length=10, max_length=512)


class TenantSwitchIn(Schema):
    tenant_id: str


class TenantUpdateIn(Schema):
    version: int
    status: str | None = None
    plan: str | None = None
    enabled_modules: list[str] | None = None
    storage_quota_gb: int | None = None
    data_region: str | None = None
    brand_name: str | None = None
    brand_description: str | None = None
    brand_theme: str | None = None
    ai_evaluation_mode: str | None = None
    ai_confidence_threshold: float | None = Field(default=None, ge=1, le=100)
    ai_model_name: str | None = None
    ai_api_key: str | None = Field(default=None, min_length=10, max_length=512)
    ai_provider_version: int = 0


class TenantLogoUploadIn(Schema):
    content_type: str
    maximum_bytes: int = Field(ge=1, le=2_000_000)


class TenantLogoFinalizeIn(Schema):
    version: int
    storage_key: str
    content_type: str


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
