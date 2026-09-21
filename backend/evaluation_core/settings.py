import os
from pathlib import Path
from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "unsafe-local-development-key")
DEBUG = os.getenv("DJANGO_DEBUG", "true").lower() == "true"
DEMO_PASSWORD_ONLY_LOGIN = (
    DEBUG
    and os.getenv("DEMO_PASSWORD_ONLY_LOGIN", "false").lower() == "true"
)
DEMO_SKIP_EVALUATOR_FACE_VERIFICATION = DEBUG and os.getenv("DEMO_SKIP_EVALUATOR_FACE_VERIFICATION", "false").lower() == "true"
ALLOWED_HOSTS = [item.strip() for item in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.core",
    "apps.tenancy",
    "apps.configuration",
    "apps.evaluators",
    "apps.eligibility",
    "apps.identity_auth",
    "apps.security",
    "apps.anonymisation",
    "apps.receiving",
    "apps.custody",
    "apps.repository",
    "apps.allocation",
    "apps.assignment",
    "apps.scanning",
    "apps.scan_processing",
    "apps.rubrics",
    "apps.marking",
    "apps.ai_evaluation",
    "apps.workflow",
    "apps.valuation",
    "apps.discrepancy",
    "apps.integrity",
    "apps.phase4",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.core.middleware.AuditRequestContextMiddleware",
    "apps.core.middleware.TenantDomainMiddleware",
    "apps.identity_auth.middleware.IdentitySessionMiddleware",
    "apps.core.middleware.TenantEntitlementMiddleware",
    "apps.core.middleware.EvaluatorRoleBoundaryMiddleware",
    "apps.core.middleware.IntakeDeskBoundaryMiddleware",
    "apps.security.middleware.DlpInspectionMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "evaluation_core.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "evaluation_core.wsgi.application"
ASGI_APPLICATION = "evaluation_core.asgi.application"

database_url = os.getenv("DATABASE_URL")
if database_url:
    parsed = urlparse(database_url)
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parsed.path.lstrip("/"),
        "USER": parsed.username,
        "PASSWORD": parsed.password,
        "HOST": parsed.hostname,
        "PORT": parsed.port or 5432,
        "CONN_MAX_AGE": 60,
        "OPTIONS": {"sslmode": os.getenv("DB_SSLMODE", "prefer")},
    }}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("DATA_UPLOAD_MAX_MEMORY_SIZE", "6291456"))
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
CSRF_TRUSTED_ORIGINS = [item.strip() for item in os.getenv("CSRF_TRUSTED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000,https://*.ngrok-free.app").split(",") if item.strip()]
TENANT_BASE_DOMAIN = os.getenv("TENANT_BASE_DOMAIN", "localhost").strip().lower().rstrip(".")
PLATFORM_HOSTS = {
    item.strip().lower()
    for item in os.getenv("PLATFORM_HOSTS", "localhost,127.0.0.1,platform.localhost").split(",")
    if item.strip()
}
ENFORCE_TENANT_DOMAINS = os.getenv("ENFORCE_TENANT_DOMAINS", "false").lower() == "true"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
X_FRAME_OPTIONS = "DENY"
APPLICATION_ENCRYPTION_KEY = os.getenv("APPLICATION_ENCRYPTION_KEY", "")
EVALUATOR_FACE_MATCH_THRESHOLD = os.getenv("EVALUATOR_FACE_MATCH_THRESHOLD", "0.82")
EVALUATOR_FACE_MIN_QUALITY = os.getenv("EVALUATOR_FACE_MIN_QUALITY", "0.45")
EVALUATOR_FACE_VERIFICATION_TTL_MINUTES = int(os.getenv("EVALUATOR_FACE_VERIFICATION_TTL_MINUTES", "10"))
ADMIEZO_AI_PROVIDER_BASE = os.getenv("ADMIEZO_AI_PROVIDER_BASE", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
ADMIEZO_AI_PROVIDER_MODEL = os.getenv("ADMIEZO_AI_PROVIDER_MODEL", "gemini-2.5-flash")
ADMIEZO_AI_REQUEST_TIMEOUT_SECONDS = int(os.getenv("ADMIEZO_AI_REQUEST_TIMEOUT_SECONDS", "120"))
TRUST_PROXY_RISK_HEADERS = os.getenv("TRUST_PROXY_RISK_HEADERS", "false").lower() == "true"
IDENTITY_SERVICE_URL = os.getenv("IDENTITY_SERVICE_URL", "")
DEMO_MANUAL_INTAKE_ENABLED = os.getenv("DEMO_MANUAL_INTAKE_ENABLED", "false").lower() == "true"
DEMO_MANUAL_RECOGNITION_ENABLED = os.getenv("DEMO_MANUAL_RECOGNITION_ENABLED", "false").lower() == "true"
IDENTITY_AUTHORIZATION_KEY = os.getenv("IDENTITY_AUTHORIZATION_KEY", "local-identity-authorization-key-change-me")
SCRIPT_STORAGE_URL = os.getenv("SCRIPT_STORAGE_URL", "")
SCRIPT_STORAGE_INTERNAL_URL = os.getenv("SCRIPT_STORAGE_INTERNAL_URL", "http://127.0.0.1:9000")
SCRIPT_STORAGE_PUBLIC_BASE = os.getenv("SCRIPT_STORAGE_PUBLIC_BASE", "/storage")
SCRIPT_STORAGE_SIGNING_KEY = os.getenv("SCRIPT_STORAGE_SIGNING_KEY", "local-storage-signing-key-change-me")
WAF_PROVIDER = os.getenv("WAF_PROVIDER", "")
DDOS_PROVIDER = os.getenv("DDOS_PROVIDER", "")
IDS_PROVIDER = os.getenv("IDS_PROVIDER", "")
SECURITY_MONITORING_PROVIDER = os.getenv("SECURITY_MONITORING_PROVIDER", "")
VULNERABILITY_SCANNER = os.getenv("VULNERABILITY_SCANNER", "")
SECRETS_PROVIDER = os.getenv("SECRETS_PROVIDER", "")
KMS_PROVIDER = os.getenv("KMS_PROVIDER", "")
OUTBOX_MODE = os.getenv("OUTBOX_MODE", "log")
OUTBOX_WEBHOOK_URL = os.getenv("OUTBOX_WEBHOOK_URL", "")
OUTBOX_AUTH_TOKEN = os.getenv("OUTBOX_AUTH_TOKEN", "")
APP_BASE_URL = os.getenv("APP_BASE_URL", "").rstrip("/")
OIDC_ALLOWED_PRIVATE_HOSTS = [
    item.strip().lower()
    for item in os.getenv("OIDC_ALLOWED_PRIVATE_HOSTS", "").split(",")
    if item.strip()
]
SEED_DEMO_ASSETS = os.getenv("SEED_DEMO_ASSETS", "false").lower() == "true"

if not DEBUG:
    secret_values = {
        "DJANGO_SECRET_KEY": SECRET_KEY,
        "APPLICATION_ENCRYPTION_KEY": APPLICATION_ENCRYPTION_KEY,
        "IDENTITY_AUTHORIZATION_KEY": IDENTITY_AUTHORIZATION_KEY,
        "SCRIPT_STORAGE_SIGNING_KEY": SCRIPT_STORAGE_SIGNING_KEY,
    }
    weak = [name for name, value in secret_values.items() if len(value) < 32 or any(marker in value.lower() for marker in ("unsafe", "local", "change-me"))]
    errors = []
    if weak:
        errors.append(f"strong unique secrets required for: {', '.join(weak)}")
    if not database_url or parsed.scheme not in {"postgres", "postgresql"} or os.getenv("DB_SSLMODE") not in {"require", "verify-ca", "verify-full"}:
        errors.append("PostgreSQL with DB_SSLMODE=require or stronger is required")
    if not SESSION_COOKIE_SECURE:
        errors.append("COOKIE_SECURE=true is required")
    if not APP_BASE_URL.startswith("https://"):
        errors.append("APP_BASE_URL must use HTTPS")
    if not ENFORCE_TENANT_DOMAINS or TENANT_BASE_DOMAIN in {"localhost", "127.0.0.1"}:
        errors.append("a production TENANT_BASE_DOMAIN with ENFORCE_TENANT_DOMAINS=true is required")
    if not PLATFORM_HOSTS:
        errors.append("at least one PLATFORM_HOSTS entry is required")
    if OUTBOX_MODE != "webhook" or not OUTBOX_WEBHOOK_URL.startswith("https://"):
        errors.append("OUTBOX_MODE=webhook with an HTTPS OUTBOX_WEBHOOK_URL is required")
    if errors:
        raise ImproperlyConfigured("Production configuration rejected: " + "; ".join(errors))

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 31_536_000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
