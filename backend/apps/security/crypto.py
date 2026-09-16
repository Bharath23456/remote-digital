import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class SecretDecryptionError(ValueError):
    pass


def _fernet() -> Fernet:
    configured = getattr(settings, "APPLICATION_ENCRYPTION_KEY", "")
    if configured:
        key = configured.encode()
    else:
        digest = hashlib.sha256(f"admiezo:{settings.SECRET_KEY}".encode()).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise SecretDecryptionError("Stored secret cannot be decrypted with the active key") from exc
