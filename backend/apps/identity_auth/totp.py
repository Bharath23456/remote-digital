import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote


def generate_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _decode_secret(secret: str) -> bytes:
    padding = "=" * ((8 - len(secret) % 8) % 8)
    return base64.b32decode(secret + padding, casefold=True)


def code_at(secret: str, timestamp: int | None = None, interval: int = 30, digits: int = 6) -> str:
    counter = int((timestamp if timestamp is not None else time.time()) // interval)
    digest = hmac.new(_decode_secret(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10**digits)).zfill(digits)


def verify_code(secret: str, code: str, timestamp: int | None = None, window: int = 1) -> bool:
    now = timestamp if timestamp is not None else int(time.time())
    return any(hmac.compare_digest(code_at(secret, now + offset * 30), code) for offset in range(-window, window + 1))


def provisioning_uri(secret: str, account: str, issuer: str = "ADMIEZO") -> str:
    label = quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"
