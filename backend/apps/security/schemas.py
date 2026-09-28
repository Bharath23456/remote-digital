from typing import Any

from ninja import Field, Schema


class SecurityPolicyIn(Schema):
    version: int
    session_timeout_minutes: int
    maximum_concurrent_sessions: int
    step_up_minutes: int
    failed_login_limit: int
    lockout_minutes: int
    require_mfa: bool
    require_trusted_device: bool
    approved_networks: list[str]
    allowed_countries: list[str]
    vpn_risk_threshold: int
    alert_risk_threshold: int
    dlp_enabled: bool
    evaluation_strict_mode: bool = True
    evaluation_identity_verification_required: bool = True
    evaluation_camera_required: bool = True
    evaluation_fullscreen_required: bool = True
    evaluation_single_screen_required: bool = True
    evaluation_mobile_allowed: bool = False
    evaluation_event_recording: bool = True
    evaluation_pause_on_violation: bool = True
    evaluation_require_resume_step_up: bool = True
    evaluation_allow_clipboard: bool = False
    evaluation_allow_download: bool = False
    evaluation_allow_print: bool = False
    evaluation_session_timeout_minutes: int = 180
    evaluation_heartbeat_seconds: int = 15
    evaluation_no_face_seconds: int = 30
    evaluation_retention_days: int = 30
    ai_evaluation_mode: str = "disabled"
    ai_confidence_threshold: float = Field(default=85, ge=1, le=100)
    ai_model_name: str = "admiezo-ai-v1"


class AlertStatusIn(Schema):
    version: int
    status: str


class PrivilegedRequestIn(Schema):
    requested_role: str
    reason: str
    duration_minutes: int = 60


class PrivilegedDecisionIn(Schema):
    version: int
    approve: bool
    note: str = ""


class EmergencyGrantIn(Schema):
    user_id: int
    role: str
    incident_reference: str
    justification: str
    duration_minutes: int = 30


class MembershipAccessIn(Schema):
    role: str
    permissions: list[str]
    enabled_modules: list[str]
    is_active: bool


class MembershipCreateIn(Schema):
    first_name: str
    last_name: str
    email: str
    role: str
    permissions: list[str] = Field(default_factory=list)
    enabled_modules: list[str] = Field(default_factory=list)
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class OidcProviderIn(Schema):
    name: str
    issuer: str
    client_id: str
    client_secret: str
    scopes: str = "openid email profile"
    domain_hint: str = ""
