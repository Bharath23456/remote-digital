import os
from pathlib import Path
from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.getenv("IDENTITY_DJANGO_SECRET_KEY", "unsafe-local-identity-service-key")
DEBUG = os.getenv("IDENTITY_DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = [item.strip() for item in os.getenv("IDENTITY_ALLOWED_HOSTS", "localhost,127.0.0.1,identity-service").split(",")]
INSTALLED_APPS = ["django.contrib.auth", "django.contrib.contenttypes", "identity_store"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "django.middleware.common.CommonMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware"]
ROOT_URLCONF = "identity_boundary.urls"
TEMPLATES = []
WSGI_APPLICATION = "identity_boundary.wsgi.application"
ASGI_APPLICATION = "identity_boundary.asgi.application"
database_url = os.getenv("IDENTITY_DATABASE_URL")
if database_url:
    parsed = urlparse(database_url)
    DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": parsed.path.lstrip("/"), "USER": parsed.username, "PASSWORD": parsed.password, "HOST": parsed.hostname, "PORT": parsed.port or 5432, "CONN_MAX_AGE": 60, "OPTIONS": {"sslmode": os.getenv("IDENTITY_DB_SSLMODE", "prefer")}}}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": os.getenv("IDENTITY_SQLITE_PATH", BASE_DIR / "identity.sqlite3")}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
IDENTITY_AUTHORIZATION_KEY = os.getenv("IDENTITY_AUTHORIZATION_KEY", "local-identity-authorization-key-change-me")
IDENTITY_ENCRYPTION_KEY = os.getenv("IDENTITY_ENCRYPTION_KEY", "local-identity-encryption-key-change-me")

if not DEBUG:
    secret_values = {
        "IDENTITY_DJANGO_SECRET_KEY": SECRET_KEY,
        "IDENTITY_AUTHORIZATION_KEY": IDENTITY_AUTHORIZATION_KEY,
        "IDENTITY_ENCRYPTION_KEY": IDENTITY_ENCRYPTION_KEY,
    }
    weak = [name for name, value in secret_values.items() if len(value) < 32 or any(marker in value.lower() for marker in ("unsafe", "local", "change-me"))]
    errors = []
    if weak:
        errors.append(f"strong unique secrets required for: {', '.join(weak)}")
    if not database_url or parsed.scheme not in {"postgres", "postgresql"} or os.getenv("IDENTITY_DB_SSLMODE") not in {"require", "verify-ca", "verify-full"}:
        errors.append("an isolated PostgreSQL database with TLS is required")
    if errors:
        raise ImproperlyConfigured("Identity production configuration rejected: " + "; ".join(errors))
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 31_536_000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
