import hashlib
import hmac
import time
import json
from dataclasses import dataclass
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from django.conf import settings


MAX_SIGNED_URL_TTL = 300


@dataclass(frozen=True)
class ObjectMetadata:
    sha256: str
    byte_size: int
    mime_type: str
    backup_status: str = "pending"
    replication_status: str = "pending"


def _signature(method: str, key: str, expires: int, content_type: str, max_bytes: int | str) -> str:
    payload = f"{method}\n{key}\n{expires}\n{content_type}\n{max_bytes}".encode()
    return hmac.new(settings.SCRIPT_STORAGE_SIGNING_KEY.encode(), payload, hashlib.sha256).hexdigest()


def signed_object_url(*, method: str, key: str, content_type: str = "", max_bytes: int = 0, ttl_seconds: int = 300, public=True):
    ttl = min(max(ttl_seconds, 1), MAX_SIGNED_URL_TTL)
    expires = int(time.time()) + ttl
    signed_maximum = max_bytes or ""
    query = urlencode(
        {
            "expires": expires,
            "signature": _signature(method, key, expires, content_type, signed_maximum),
            "content_type": content_type,
            "max_bytes": signed_maximum,
        }
    )
    base = settings.SCRIPT_STORAGE_PUBLIC_BASE if public else settings.SCRIPT_STORAGE_INTERNAL_URL
    return f"{base.rstrip('/')}/objects/{quote(key, safe='/')}?{query}", expires


def read_object_metadata(key: str) -> ObjectMetadata:
    url, _ = signed_object_url(method="GET", key=key, public=False)
    request = Request(url, method="HEAD")
    with urlopen(request, timeout=5) as response:
        return ObjectMetadata(
            sha256=response.headers["X-Object-SHA256"],
            byte_size=int(response.headers["X-Object-Size"]),
            mime_type=response.headers["Content-Type"],
            backup_status=response.headers.get("X-Object-Backup-Status", "pending"),
            replication_status=response.headers.get("X-Object-Replication-Status", "pending"),
        )


def delete_object(key: str):
    url, _ = signed_object_url(method="DELETE", key=key, public=False)
    with urlopen(Request(url, method="DELETE"), timeout=5) as response:
        if response.status != 204:
            raise OSError("Storage gateway did not confirm deletion")


def mask_object(*, source_key: str, destinations: list[dict], regions: list[dict]):
    body = json.dumps({"source_key": source_key, "destinations": destinations, "regions": regions}, separators=(",", ":")).encode()
    signature = hmac.new(settings.SCRIPT_STORAGE_SIGNING_KEY.encode(), body, hashlib.sha256).hexdigest()
    request = Request(
        f"{settings.SCRIPT_STORAGE_INTERNAL_URL.rstrip('/')}/internal/mask",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-Storage-Signature": signature},
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def process_scan_object(*, source_key: str, destination: dict, configuration: dict):
    body = json.dumps(
        {"source_key": source_key, "destination": destination, "configuration": configuration},
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(settings.SCRIPT_STORAGE_SIGNING_KEY.encode(), body, hashlib.sha256).hexdigest()
    request = Request(
        f"{settings.SCRIPT_STORAGE_INTERNAL_URL.rstrip('/')}/internal/process-scan",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-Storage-Signature": signature},
    )
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def create_demo_page(*, paper: str, page_number: int, destinations: list[dict]):
    body = json.dumps({"paper": paper, "page_number": page_number, "destinations": destinations}, separators=(",", ":")).encode()
    signature = hmac.new(settings.SCRIPT_STORAGE_SIGNING_KEY.encode(), body, hashlib.sha256).hexdigest()
    request = Request(
        f"{settings.SCRIPT_STORAGE_INTERNAL_URL.rstrip('/')}/internal/demo-pages",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-Storage-Signature": signature},
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read())
