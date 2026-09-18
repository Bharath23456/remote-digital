import asyncio
import hashlib
import hmac
import importlib
import json
import time
from io import BytesIO
from urllib.parse import urlencode

import storage_gateway.main as gateway
from PIL import Image, ImageDraw


def signed_query(module, method, key, *, content_type="", max_bytes=0, expires=None):
    expires = expires or int(time.time()) + 120
    signature = hmac.new(
        module.SIGNING_KEY,
        module.signature_payload(method, key, expires, content_type, max_bytes),
        hashlib.sha256,
    ).hexdigest()
    return urlencode(
        {"expires": expires, "signature": signature, "content_type": content_type, "max_bytes": max_bytes}
    ).encode()


async def asgi_request(module, method, key, query, body=b"", headers=None, path=None):
    sent = []
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": method,
        "path": path or f"/objects/{key}",
        "query_string": query,
        "headers": headers or [],
    }
    await module.app(scope, receive, send)
    start, content = sent
    return start["status"], dict(start["headers"]), content["body"]


def request(module, method, key, query, body=b"", headers=None, path=None):
    return asyncio.run(asgi_request(module, method, key, query, body, headers, path))


def configured_gateway(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("STORAGE_SIGNING_KEY", "test-signing-key")
    monkeypatch.setenv("STORAGE_ENCRYPTION_KEY", "test-encryption-key")
    return importlib.reload(gateway)


def test_object_is_encrypted_at_rest_and_supports_signed_ranges(monkeypatch, tmp_path):
    module = configured_gateway(monkeypatch, tmp_path)
    key = "scripts-evaluation/tenant/script/page-1.webp"
    plain = b"protected-page-bytes"
    put_query = signed_query(module, "PUT", key, content_type="image/webp", max_bytes=100)
    status, _, payload = request(module, "PUT", key, put_query, plain, [(b"content-type", b"image/webp")])
    assert status == 201
    assert hashlib.sha256(plain).hexdigest().encode() in payload
    stored, _ = module.object_paths(key)
    assert stored.read_bytes() != plain
    assert plain not in stored.read_bytes()
    replica, _ = module.object_paths_at(module.REPLICA_ROOT, key)
    backup, _ = module.object_paths_at(module.BACKUP_ROOT, key)
    assert replica.read_bytes() == stored.read_bytes()
    assert backup.read_bytes() == stored.read_bytes()

    get_query = signed_query(module, "GET", key)
    status, headers, body = request(module, "GET", key, get_query, headers=[(b"range", b"bytes=2-8")])
    assert status == 206
    assert headers[b"content-range"] == f"bytes 2-8/{len(plain)}".encode()
    assert headers[b"x-object-replication-status"] == b"completed"
    assert headers[b"x-object-backup-status"] == b"completed"
    assert body == plain[2:9]


def test_immutable_master_cannot_be_overwritten_or_deleted(monkeypatch, tmp_path):
    module = configured_gateway(monkeypatch, tmp_path)
    key = "scripts-master/tenant/script/page-1.webp"
    query = signed_query(module, "PUT", key, content_type="image/webp", max_bytes=100)
    assert request(module, "PUT", key, query, b"first", [(b"content-type", b"image/webp")])[0] == 201
    assert request(module, "PUT", key, query, b"second", [(b"content-type", b"image/webp")])[0] == 409
    delete_query = signed_query(module, "DELETE", key)
    assert request(module, "DELETE", key, delete_query)[0] == 409


def test_internal_preview_masks_in_memory_without_creating_assets(monkeypatch, tmp_path):
    module = configured_gateway(monkeypatch, tmp_path)
    source = BytesIO()
    Image.new("RGB", (100, 100), "white").save(source, format="PNG")
    module.store_object("scripts-raw/tenant/script/page-1.png", source.getvalue(), "image/png", immutable=True)
    body = json.dumps({"source_key": "scripts-raw/tenant/script/page-1.png", "regions": [{"x": 0, "y": 0, "width": 1, "height": 1}]}).encode()
    signature = hmac.new(module.SIGNING_KEY, body, hashlib.sha256).hexdigest().encode()
    status, headers, masked = request(module, "POST", "", b"", body, [(b"x-storage-signature", signature)], path="/internal/mask-preview")
    assert status == 200
    assert headers[b"content-type"] == b"image/webp"
    assert Image.open(BytesIO(masked)).convert("RGB").getpixel((50, 50))[0] < 40
    assert request(module, "POST", "", b"", body, path="/internal/mask-preview")[0] == 403
    assert not list(tmp_path.rglob("scripts-evaluation"))


def test_expired_signature_and_ciphertext_tampering_are_rejected(monkeypatch, tmp_path):
    module = configured_gateway(monkeypatch, tmp_path)
    key = "scripts-evaluation/tenant/script/page-2.webp"
    put_query = signed_query(module, "PUT", key, content_type="image/webp", max_bytes=100)
    assert request(module, "PUT", key, put_query, b"authentic", [(b"content-type", b"image/webp")])[0] == 201
    expired = signed_query(module, "GET", key, expires=int(time.time()) - 1)
    assert request(module, "GET", key, expired)[0] == 403

    stored, _ = module.object_paths(key)
    ciphertext = bytearray(stored.read_bytes())
    ciphertext[-1] ^= 1
    stored.write_bytes(ciphertext)
    assert request(module, "GET", key, signed_query(module, "GET", key))[0] == 409


def test_mutable_object_can_be_securely_deleted(monkeypatch, tmp_path):
    module = configured_gateway(monkeypatch, tmp_path)
    key = "scripts-thumbnails/tenant/script/page-1.webp"
    put_query = signed_query(module, "PUT", key, content_type="image/webp", max_bytes=100)
    assert request(module, "PUT", key, put_query, b"thumbnail", [(b"content-type", b"image/webp")])[0] == 201
    assert request(module, "DELETE", key, signed_query(module, "DELETE", key))[0] == 204
    stored, metadata = module.object_paths(key)
    assert not stored.exists()
    assert not metadata.exists()
    replica, replica_metadata = module.object_paths_at(module.REPLICA_ROOT, key)
    backup, backup_metadata = module.object_paths_at(module.BACKUP_ROOT, key)
    assert not replica.exists() and not replica_metadata.exists()
    assert not backup.exists() and not backup_metadata.exists()


def test_proctoring_media_accepts_codec_parameter_and_retention_delete(monkeypatch, tmp_path):
    module = configured_gateway(monkeypatch, tmp_path)
    key = "proctoring/tenant/session/event.webm"
    mime_type = "video/webm;codecs=vp8"
    query = signed_query(module, "PUT", key, content_type=mime_type, max_bytes=100)
    assert request(module, "PUT", key, query, b"webm-evidence", [(b"content-type", mime_type.encode())])[0] == 201
    assert request(module, "DELETE", key, signed_query(module, "DELETE", key))[0] == 204
    for root in (module.ROOT, module.REPLICA_ROOT, module.BACKUP_ROOT):
        data, metadata = module.object_paths_at(root, key)
        assert not data.exists() and not metadata.exists()


def test_scan_processing_matches_golden_digest(monkeypatch, tmp_path):
    module = configured_gateway(monkeypatch, tmp_path)
    image = Image.new("RGB", (320, 480), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((16, 16, 304, 64), outline="black", width=2)
    draw.text((24, 30), "ANONYMOUS SCRIPT PAGE 1", fill="black")
    for y in range(100, 420, 32):
        draw.line((28, y, 290, y), fill=(70, 90, 120), width=2)
    source = BytesIO()
    image.save(source, format="PNG")
    output, mime_type, metrics = module.process_scan_bytes(source.getvalue(), {"resolution_dpi": 300, "grayscale": True, "background_normalization": True, "noise_reduction": True, "contrast": 1.08, "mime_type": "image/png"})
    assert mime_type == "image/png"
    assert metrics["width"] == 320 and metrics["height"] == 480
    assert metrics["is_blank"] is False
    assert hashlib.sha256(output).hexdigest() == "b19e341ef467603a628c8cf69c65be3381f3f3d8329b7d4552147ef28159aa95"
