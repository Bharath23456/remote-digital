import hashlib
import hmac
import json
import os
import secrets
import time
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, unquote

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps, ImageStat


ROOT = Path(os.getenv("STORAGE_ROOT", "/data")).resolve()
REPLICA_ROOT = Path(os.getenv("STORAGE_REPLICA_ROOT", str(ROOT.parent / f"{ROOT.name}-replica"))).resolve()
BACKUP_ROOT = Path(os.getenv("STORAGE_BACKUP_ROOT", str(ROOT.parent / f"{ROOT.name}-backup"))).resolve()
SIGNING_KEY = os.getenv("STORAGE_SIGNING_KEY", "unsafe-local-storage-key").encode()
ENCRYPTION_KEY = hashlib.sha256(os.getenv("STORAGE_ENCRYPTION_KEY", "unsafe-local-encryption-key").encode()).digest()
MAX_TTL_SECONDS = 300
IMMUTABLE_PREFIXES = ("scripts-master/", "scripts-evaluation/", "proctoring/")
ALLOW_DEMO_SEED = os.getenv("STORAGE_ALLOW_DEMO_SEED", "false").lower() == "true"
ENVIRONMENT = os.getenv("STORAGE_ENVIRONMENT", "development").lower()

if ENVIRONMENT == "production":
    weak = [
        name
        for name, value in {
            "STORAGE_SIGNING_KEY": os.getenv("STORAGE_SIGNING_KEY", ""),
            "STORAGE_ENCRYPTION_KEY": os.getenv("STORAGE_ENCRYPTION_KEY", ""),
        }.items()
        if len(value) < 32 or any(marker in value.lower() for marker in ("unsafe", "local", "change-me"))
    ]
    if weak or ALLOW_DEMO_SEED:
        raise RuntimeError("Storage production configuration rejected: use strong keys and disable demo seeding")


def response(status, body=b"", headers=None):
    base = [(b"content-length", str(len(body)).encode()), (b"x-content-type-options", b"nosniff")]
    return status, base + (headers or []), body


def safe_key(raw_path):
    key = unquote(raw_path.removeprefix("/objects/")).strip("/")
    if not key or "\x00" in key or any(part in ("", ".", "..") for part in key.split("/")):
        return None
    return key


def object_paths(key):
    return object_paths_at(ROOT, key)


def object_paths_at(root, key):
    data_path = (root / key).resolve()
    if root not in data_path.parents:
        return None, None
    return data_path, data_path.with_name(f"{data_path.name}.meta.json")


def signature_payload(method, key, expires, content_type, max_bytes):
    return f"{method}\n{key}\n{expires}\n{content_type}\n{max_bytes}".encode()


def authorized(method, key, query):
    expires = query.get("expires", [""])[0]
    signature = query.get("signature", [""])[0]
    content_type = query.get("content_type", [""])[0]
    max_bytes = query.get("max_bytes", [""])[0]
    try:
        expires_at = int(expires)
    except ValueError:
        return False, content_type, 0
    now = int(time.time())
    if expires_at < now or expires_at - now > MAX_TTL_SECONDS:
        return False, content_type, 0
    expected = hmac.new(SIGNING_KEY, signature_payload(method, key, expires, content_type, max_bytes), hashlib.sha256).hexdigest()
    try:
        limit = int(max_bytes) if max_bytes else 0
    except ValueError:
        return False, content_type, 0
    return hmac.compare_digest(expected, signature), content_type, limit


def encrypt(data):
    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(ENCRYPTION_KEY).encrypt(nonce, data, None)


def decrypt(data):
    return AESGCM(ENCRYPTION_KEY).decrypt(data[:12], data[12:], None)


async def read_body(receive, limit):
    parts = []
    size = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise ConnectionError
        chunk = message.get("body", b"")
        size += len(chunk)
        if not limit or size > limit:
            raise OverflowError
        parts.append(chunk)
        if not message.get("more_body", False):
            return b"".join(parts)


def store_object(key, plain, mime_type, immutable=True):
    data_path, meta_path = object_paths(key)
    if immutable and data_path.exists():
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        encrypted = data_path.read_bytes()
        _write_copy(REPLICA_ROOT, key, encrypted, metadata, immutable=True)
        _write_copy(BACKUP_ROOT, key, encrypted, metadata, immutable=True)
        return metadata
    digest = hashlib.sha256(plain).hexdigest()
    encrypted = encrypt(plain)
    data_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = data_path.with_name(f".{data_path.name}.{secrets.token_hex(8)}.tmp")
    temp_path.write_bytes(encrypted)
    if immutable:
        os.link(temp_path, data_path)
        temp_path.unlink()
    else:
        os.replace(temp_path, data_path)
    metadata = {"sha256": digest, "byte_size": len(plain), "mime_type": mime_type, "encrypted": True, "created_at": int(time.time())}
    meta_path.write_text(json.dumps(metadata, separators=(",", ":")), encoding="utf-8")
    _write_copy(REPLICA_ROOT, key, encrypted, metadata, immutable=immutable)
    _write_copy(BACKUP_ROOT, key, encrypted, metadata, immutable=immutable)
    return metadata


def _write_copy(root, key, encrypted, metadata, immutable):
    data_path, meta_path = object_paths_at(root, key)
    data_path.parent.mkdir(parents=True, exist_ok=True)
    if immutable and data_path.exists():
        return
    temp_path = data_path.with_name(f".{data_path.name}.{secrets.token_hex(8)}.tmp")
    temp_path.write_bytes(encrypted)
    if immutable:
        try:
            os.link(temp_path, data_path)
        finally:
            temp_path.unlink(missing_ok=True)
    else:
        os.replace(temp_path, data_path)
    meta_path.write_text(json.dumps(metadata, separators=(",", ":")), encoding="utf-8")


def copy_status(root, key, expected_sha256):
    try:
        data_path, meta_path = object_paths_at(root, key)
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        plain = decrypt(data_path.read_bytes())
        return "completed" if metadata.get("sha256") == expected_sha256 == hashlib.sha256(plain).hexdigest() else "failed"
    except (FileNotFoundError, InvalidTag, ValueError, KeyError, json.JSONDecodeError):
        return "failed"


def ensure_copy(root, key, encrypted, metadata, immutable):
    if copy_status(root, key, metadata["sha256"]) == "completed":
        return
    data_path, meta_path = object_paths_at(root, key)
    data_path.unlink(missing_ok=True)
    meta_path.unlink(missing_ok=True)
    _write_copy(root, key, encrypted, metadata, immutable)


async def mask_image(receive, scope):
    raw = await read_body(receive, 2_000_000)
    supplied = next((value.decode() for name, value in scope.get("headers", []) if name.lower() == b"x-storage-signature"), "")
    expected = hmac.new(SIGNING_KEY, raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        return response(403, b'{"detail":"Internal storage authorization failed"}', [(b"content-type", b"application/json")])
    try:
        request = json.loads(raw)
        source_path, source_meta_path = object_paths(request["source_key"])
        source_meta = json.loads(source_meta_path.read_text(encoding="utf-8"))
        source = decrypt(source_path.read_bytes())
        image = Image.open(BytesIO(source)).convert("RGB")
        draw = ImageDraw.Draw(image)
        for region in request.get("regions", []):
            x = max(0, min(image.width, round(float(region["x"]) * image.width)))
            y = max(0, min(image.height, round(float(region["y"]) * image.height)))
            right = max(x, min(image.width, round((float(region["x"]) + float(region["width"])) * image.width)))
            bottom = max(y, min(image.height, round((float(region["y"]) + float(region["height"])) * image.height)))
            draw.rectangle((x, y, right, bottom), fill=(18, 24, 21))
        results = []
        for destination in request["destinations"]:
            output = image.copy()
            if destination.get("thumbnail"):
                output.thumbnail((360, 520))
            stream = BytesIO()
            mime_type = destination.get("mime_type", "image/webp")
            output.save(stream, format="WEBP" if mime_type == "image/webp" else "PNG", quality=88, method=4)
            metadata = store_object(destination["key"], stream.getvalue(), mime_type, immutable=True)
            results.append({"key": destination["key"], **metadata})
        payload = json.dumps({"source_sha256": source_meta["sha256"], "objects": results}).encode()
        return response(201, payload, [(b"content-type", b"application/json")])
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError, OSError):
        return response(422, b'{"detail":"Mask transform could not be completed"}', [(b"content-type", b"application/json")])


def _normalized_crop(image, crop):
    if not crop:
        return image
    x = max(0.0, min(1.0, float(crop.get("x", 0))))
    y = max(0.0, min(1.0, float(crop.get("y", 0))))
    width = max(0.01, min(1.0 - x, float(crop.get("width", 1))))
    height = max(0.01, min(1.0 - y, float(crop.get("height", 1))))
    return image.crop((round(x * image.width), round(y * image.height), round((x + width) * image.width), round((y + height) * image.height)))


def process_scan_bytes(source, configuration):
    image = ImageOps.exif_transpose(Image.open(BytesIO(source))).convert("RGB")
    image = _normalized_crop(image, configuration.get("crop"))
    rotation = int(configuration.get("rotation_degrees", 0)) % 360
    deskew = float(configuration.get("deskew_degrees", 0))
    if rotation:
        image = image.rotate(-rotation, expand=True, fillcolor="white")
    if deskew:
        image = image.rotate(-max(-15.0, min(15.0, deskew)), expand=True, fillcolor="white")
    if configuration.get("grayscale", True):
        image = ImageOps.grayscale(image)
    if configuration.get("background_normalization", True):
        image = ImageOps.autocontrast(image, cutoff=1)
    if configuration.get("noise_reduction", True):
        image = image.filter(ImageFilter.MedianFilter(size=3))
    contrast = max(0.5, min(2.0, float(configuration.get("contrast", 1.08))))
    image = ImageEnhance.Contrast(image).enhance(contrast)
    stats = ImageStat.Stat(image.convert("L"))
    mean = stats.mean[0]
    variance = stats.var[0]
    edge_mean = ImageStat.Stat(image.convert("L").filter(ImageFilter.FIND_EDGES)).mean[0]
    is_blank = mean > 245 and variance < 80
    quality_score = round(max(0.0, min(100.0, 35.0 + edge_mean * 2.2 + min(variance, 800.0) / 16.0)), 2)
    output = BytesIO()
    mime_type = configuration.get("mime_type", "image/webp")
    if mime_type == "image/png":
        image.save(output, format="PNG", optimize=True, dpi=(int(configuration.get("resolution_dpi", 300)),) * 2)
    else:
        mime_type = "image/webp"
        image.save(output, format="WEBP", quality=90, method=4)
    metrics = {
        "width": image.width,
        "height": image.height,
        "mean_luminance": round(mean, 3),
        "variance": round(variance, 3),
        "edge_mean": round(edge_mean, 3),
        "quality_score": quality_score,
        "is_blank": is_blank,
        "rotation_degrees": rotation,
        "deskew_degrees": deskew,
        "resolution_dpi": int(configuration.get("resolution_dpi", 300)),
    }
    return output.getvalue(), mime_type, metrics


async def process_scan(receive, scope):
    raw = await read_body(receive, 2_000_000)
    supplied = next((value.decode() for name, value in scope.get("headers", []) if name.lower() == b"x-storage-signature"), "")
    expected = hmac.new(SIGNING_KEY, raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        return response(403, b'{"detail":"Internal storage authorization failed"}', [(b"content-type", b"application/json")])
    try:
        request = json.loads(raw)
        source_path, source_meta_path = object_paths(request["source_key"])
        source_meta = json.loads(source_meta_path.read_text(encoding="utf-8"))
        source = decrypt(source_path.read_bytes())
        if hashlib.sha256(source).hexdigest() != source_meta["sha256"]:
            raise ValueError
        output, mime_type, metrics = process_scan_bytes(source, request.get("configuration", {}))
        destination = request["destination"]
        metadata = store_object(destination["key"], output, destination.get("mime_type", mime_type), immutable=True)
        payload = json.dumps({"source_sha256": source_meta["sha256"], "object": {"key": destination["key"], **metadata}, "metrics": metrics}).encode()
        return response(201, payload, [(b"content-type", b"application/json")])
    except (FileNotFoundError, KeyError, ValueError, TypeError, json.JSONDecodeError, OSError, InvalidTag):
        return response(422, b'{"detail":"Scan processing could not be completed"}', [(b"content-type", b"application/json")])


async def create_demo_pages(receive, scope):
    if not ALLOW_DEMO_SEED:
        return response(404, b'{"detail":"Not found"}', [(b"content-type", b"application/json")])
    raw = await read_body(receive, 200_000)
    supplied = next((value.decode() for name, value in scope.get("headers", []) if name.lower() == b"x-storage-signature"), "")
    expected = hmac.new(SIGNING_KEY, raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        return response(403, b'{"detail":"Internal storage authorization failed"}', [(b"content-type", b"application/json")])
    try:
        request = json.loads(raw)
        image = Image.new("RGB", (1240, 1754), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((58, 50, 1182, 150), outline=(48, 62, 55), width=3)
        draw.text((82, 76), request.get("paper", "ANONYMOUS ANSWER SCRIPT")[:80], fill=(28, 43, 35))
        draw.text((940, 76), f"Page {int(request['page_number'])}", fill=(28, 43, 35))
        draw.line((58, 185, 1182, 185), fill=(120, 132, 126), width=2)
        prompts = request.get("prompts") or ["Explain the core concept with a suitable example.", "Discuss the design decisions and trade-offs."]
        y = 225
        for index, prompt in enumerate(prompts, 1):
            draw.text((72, y), f"Q{index}. {str(prompt)[:105]}", fill=(24, 31, 27))
            y += 54
            for line in range(9):
                draw.line((82, y + 31, 1154, y + 31), fill=(220, 224, 222), width=1)
                text = ["The system coordinates independent components through a clear contract.", "Each transition is validated before durable state is committed.", "This preserves consistency while allowing controlled recovery.", "The resulting design is observable, auditable and scalable."][line % 4]
                draw.text((94 + (line % 2) * 12, y + 7), text, fill=(41, 67, 112))
                y += 43
            y += 42
        results = []
        for destination in request["destinations"]:
            output = image.copy()
            if destination.get("thumbnail"):
                output.thumbnail((360, 520))
            stream = BytesIO()
            output.save(stream, format="WEBP", quality=86, method=4)
            metadata = store_object(destination["key"], stream.getvalue(), "image/webp", immutable=True)
            results.append({"key": destination["key"], **metadata})
        return response(201, json.dumps({"objects": results}).encode(), [(b"content-type", b"application/json")])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError, OSError):
        return response(422, b'{"detail":"Demo page generation failed"}', [(b"content-type", b"application/json")])


async def app(scope, receive, send):
    if scope["type"] != "http":
        return
    method = scope["method"].upper()
    path = scope["path"]
    if path == "/health":
        status, headers, body = response(200, b'{"status":"ok","service":"storage-gateway"}', [(b"content-type", b"application/json")])
    elif path == "/internal/mask" and method == "POST":
        status, headers, body = await mask_image(receive, scope)
    elif path == "/internal/process-scan" and method == "POST":
        status, headers, body = await process_scan(receive, scope)
    elif path == "/internal/demo-pages" and method == "POST":
        status, headers, body = await create_demo_pages(receive, scope)
    elif not path.startswith("/objects/") or method not in ("GET", "HEAD", "PUT", "DELETE"):
        status, headers, body = response(404, b'{"detail":"Not found"}', [(b"content-type", b"application/json")])
    else:
        key = safe_key(path)
        data_path, meta_path = object_paths(key) if key else (None, None)
        query = parse_qs(scope.get("query_string", b"").decode())
        signing_method = "GET" if method == "HEAD" else method
        allowed, content_type, max_bytes = authorized(signing_method, key or "", query)
        if not allowed:
            status, headers, body = response(403, b'{"detail":"Signed URL is invalid or expired"}', [(b"content-type", b"application/json")])
        elif method == "DELETE":
            if key.startswith("scripts-master/"):
                status, headers, body = response(409, b'{"detail":"Immutable master objects cannot be deleted"}', [(b"content-type", b"application/json")])
            elif not data_path.exists():
                status, headers, body = response(404, b'{"detail":"Object not found"}', [(b"content-type", b"application/json")])
            else:
                data_path.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
                for root in (REPLICA_ROOT, BACKUP_ROOT):
                    copy_path, copy_meta_path = object_paths_at(root, key)
                    copy_path.unlink(missing_ok=True)
                    copy_meta_path.unlink(missing_ok=True)
                status, headers, body = response(204)
        elif method == "PUT":
            request_headers = {key.decode().lower(): value.decode() for key, value in scope.get("headers", [])}
            request_type = request_headers.get("content-type", "").split(";", 1)[0].strip().lower()
            signed_type = content_type.split(";", 1)[0].strip().lower()
            if request_type != signed_type:
                status, headers, body = response(415, b'{"detail":"Content type does not match upload intent"}', [(b"content-type", b"application/json")])
            elif key.startswith(IMMUTABLE_PREFIXES) and data_path.exists():
                status, headers, body = response(409, b'{"detail":"Immutable object already exists"}', [(b"content-type", b"application/json")])
            else:
                try:
                    plain = await read_body(receive, max_bytes)
                    metadata = store_object(key, plain, content_type, immutable=key.startswith(IMMUTABLE_PREFIXES))
                    payload = json.dumps({"sha256": metadata["sha256"], "byte_size": metadata["byte_size"]}).encode()
                    status, headers, body = response(201, payload, [(b"content-type", b"application/json"), (b"etag", metadata["sha256"].encode())])
                except FileExistsError:
                    status, headers, body = response(409, b'{"detail":"Immutable object already exists"}', [(b"content-type", b"application/json")])
                except (OverflowError, ConnectionError):
                    status, headers, body = response(413, b'{"detail":"Upload exceeds the signed size limit"}', [(b"content-type", b"application/json")])
        elif not data_path.exists() or not meta_path.exists():
            status, headers, body = response(404, b'{"detail":"Object not found"}', [(b"content-type", b"application/json")])
        else:
            try:
                metadata = json.loads(meta_path.read_text(encoding="utf-8"))
                encrypted = data_path.read_bytes()
                plain = decrypt(encrypted)
                if hashlib.sha256(plain).hexdigest() != metadata["sha256"]:
                    raise ValueError
                ensure_copy(REPLICA_ROOT, key, encrypted, metadata, key.startswith(IMMUTABLE_PREFIXES))
                ensure_copy(BACKUP_ROOT, key, encrypted, metadata, key.startswith(IMMUTABLE_PREFIXES))
                range_header = next((value.decode() for name, value in scope.get("headers", []) if name.lower() == b"range"), "")
                start, end = 0, len(plain) - 1
                response_status = 200
                extra = []
                if range_header.startswith("bytes="):
                    start_text, end_text = range_header[6:].split("-", 1)
                    start = int(start_text or 0)
                    end = min(int(end_text) if end_text else len(plain) - 1, len(plain) - 1)
                    if start > end or start >= len(plain):
                        raise IndexError
                    response_status = 206
                    extra.append((b"content-range", f"bytes {start}-{end}/{len(plain)}".encode()))
                result = b"" if method == "HEAD" else plain[start : end + 1]
                headers = [
                    (b"content-type", metadata["mime_type"].encode()),
                    (b"etag", metadata["sha256"].encode()),
                    (b"x-object-sha256", metadata["sha256"].encode()),
                    (b"x-object-size", str(metadata["byte_size"]).encode()),
                    (b"accept-ranges", b"bytes"),
                    (b"cache-control", b"private, max-age=300"),
                    (b"x-object-replication-status", copy_status(REPLICA_ROOT, key, metadata["sha256"]).encode()),
                    (b"x-object-backup-status", copy_status(BACKUP_ROOT, key, metadata["sha256"]).encode()),
                    *extra,
                ]
                status, headers, body = response(response_status, result, headers)
                if method == "HEAD":
                    headers = [(name, str(metadata["byte_size"]).encode() if name == b"content-length" else value) for name, value in headers]
            except IndexError:
                status, headers, body = response(416, b"")
            except (InvalidTag, ValueError, KeyError, json.JSONDecodeError):
                status, headers, body = response(409, b'{"detail":"Object integrity check failed"}', [(b"content-type", b"application/json")])
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
