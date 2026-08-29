"""eBay Commerce Media API image adapter (PP-PHOTO-001 Phase A).

The worker prepares immutable, derived upload bytes before reserving and
dispatching a provider effect. Local-file upload is the Phase A path. The URL
adapter is separate and accepts only a configured TGW-controlled HTTPS origin.

Contract pin: Media API ``v1_beta`` image resource (1.4.0-beta, 2025-04-17).
Supported formats are JPG/JPEG, GIF, PNG, BMP, TIFF, AVIF, HEIC and WEBP;
the documented examples currently report a 12 MiB maximum and width+height
strictly below 15,000 pixels.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping
from urllib.parse import quote, urlsplit

import requests

from tgw import quota
from tgw.apis.ebay.client import capture_response, load_token

log = logging.getLogger(__name__)

_MEDIA_VERSION = 'v1_beta'
_MEDIA_ROOT = f'/commerce/media/{_MEDIA_VERSION}/image'
_PRODUCTION_ORIGIN = 'https://api.ebay.com'
_SANDBOX_ORIGIN = 'https://api.sandbox.ebay.com'
_MAX_UPLOAD_BYTES = 12 * 1024 * 1024
_MAX_DIMENSION_PX = 15_000  # compatibility name used by resize regression tests
_MAX_UPLOAD_DIMENSION_SUM_PX = 14_999
_SUPPORTED_SUFFIXES = frozenset({
    '.jpg', '.jpeg', '.gif', '.png', '.bmp', '.tif', '.tiff',
    '.avif', '.heic', '.webp',
})
_MIME = {
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
    '.png': 'image/png', '.bmp': 'image/bmp', '.tif': 'image/tiff',
    '.tiff': 'image/tiff', '.avif': 'image/avif', '.heic': 'image/heic',
    '.webp': 'image/webp',
}
_PIL_FORMAT_BY_SUFFIX = {
    '.jpg': 'JPEG', '.jpeg': 'JPEG', '.gif': 'GIF', '.png': 'PNG',
    '.bmp': 'BMP', '.tif': 'TIFF', '.tiff': 'TIFF', '.webp': 'WEBP',
    '.avif': 'AVIF', '.heic': 'HEIF',
}


class PhotoResizeError(RuntimeError):
    """The source could not be validated or its derived upload resized."""


class UploadDefinitivelyRejected(RuntimeError):
    """The provider or local contract proves no image was accepted."""


class UploadQuotaExceeded(UploadDefinitivelyRejected):
    """The provider definitively rejected for rate/quota reasons."""


class MediaUploadResult(str):
    """EPS URL compatible with legacy callers, with complete Media metadata."""

    def __new__(cls, image_url: str, metadata: Mapping[str, Any]):
        value = str.__new__(cls, image_url)
        value.metadata = dict(metadata)
        return value


@dataclass(frozen=True)
class PreparedUpload:
    photo_path: Path
    image_bytes: bytes
    mime: str
    source_sha256: str
    prepared_sha256: str
    method: str = 'createImageFromFile'
    order: int | None = None
    attempt_identity: str | None = None


def _raw_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    raw = cfg.get('raw', cfg)
    return raw if isinstance(raw, dict) else cfg


def _origin(cfg: Dict[str, Any]) -> str:
    return (_SANDBOX_ORIGIN if _raw_config(cfg).get('ebay_environment') == 'sandbox'
            else _PRODUCTION_ORIGIN)


def _error_detail(resp: requests.Response) -> str:
    try:
        body = resp.json()
    except (ValueError, TypeError):
        return resp.text[:1000]
    errors = body.get('errors') if isinstance(body, dict) else None
    if isinstance(errors, list):
        return '; '.join(
            f"{e.get('errorId', 'unknown')}: {e.get('message', '')}"
            for e in errors if isinstance(e, dict)
        )[:1000]
    return json.dumps(body, sort_keys=True)[:1000]


def _validate_and_derive(photo_path: Path) -> tuple[bytes, bytes]:
    suffix = photo_path.suffix.lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        raise UploadDefinitivelyRejected(
            f'unsupported image extension: {suffix or "(none)"}')
    raw = photo_path.read_bytes()
    if not raw:
        raise UploadDefinitivelyRejected(f'empty image: {photo_path.name}')
    try:
        from PIL import Image
    except ImportError:
        if len(raw) > _MAX_UPLOAD_BYTES:
            raise UploadDefinitivelyRejected(
                'image exceeds 12 MiB and Pillow is unavailable')
        return raw, raw
    width = height = None
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            if (width + height <= _MAX_UPLOAD_DIMENSION_SUM_PX
                    and len(raw) <= _MAX_UPLOAD_BYTES):
                return raw, raw
            scale = min(1.0, _MAX_UPLOAD_DIMENSION_SUM_PX / float(width + height))
            resized = image if scale == 1.0 else image.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                Image.LANCZOS)
            image_format = image.format or _PIL_FORMAT_BY_SUFFIX.get(suffix, 'JPEG')
            if image_format in {'HEIF', 'AVIF'}:
                image_format = 'JPEG'
            if image_format == 'JPEG' and resized.mode not in ('RGB', 'L'):
                resized = resized.convert('RGB')
            buf = io.BytesIO()
            kwargs = {'quality': 90} if image_format in {'JPEG', 'WEBP'} else {}
            resized.save(buf, format=image_format, **kwargs)
            derived = buf.getvalue()
    except Exception as exc:
        dims = f'{width}x{height}' if width is not None else 'unknown'
        raise PhotoResizeError(
            f'failed to validate/resize photo {photo_path.name} ({dims}): {exc}') from exc
    if len(derived) > _MAX_UPLOAD_BYTES:
        raise UploadDefinitivelyRejected('derived image exceeds eBay 12 MiB limit')
    return raw, derived


def _prepare_upload_bytes(photo_path: Path) -> bytes:
    """Compatibility helper returning immutable raw or in-memory derived bytes."""
    return _validate_and_derive(photo_path)[1]


def prepare_upload(cfg: Dict[str, Any], photo_path: Path, *, order: int | None = None,
                   attempt_identity: str | None = None) -> PreparedUpload:
    """Prepare exact bytes and no-write quota-check before effect dispatch."""
    if not photo_path.is_file():
        raise FileNotFoundError(f'photo not found: {photo_path}')
    raw, derived = _validate_and_derive(photo_path)
    prepared = PreparedUpload(
        photo_path=photo_path, image_bytes=derived,
        mime=_MIME[photo_path.suffix.lower()],
        source_sha256=hashlib.sha256(raw).hexdigest(),
        prepared_sha256=hashlib.sha256(derived).hexdigest(),
        order=order, attempt_identity=attempt_identity,
    )
    quota.precheck(cfg, 'ebay_eps')
    return prepared


def _receipt(resp: requests.Response, request: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        'request': dict(request),
        'response': {
            'status': resp.status_code,
            'headers': {str(k): str(v) for k, v in resp.headers.items()},
            'body_utf8': resp.content.decode('utf-8', errors='replace'),
            'body_sha256': hashlib.sha256(resp.content).hexdigest(),
        },
    }


def _dispatch(cfg: Dict[str, Any], *, method: str, path: str,
              request_receipt: Dict[str, Any], **kwargs: Any) -> MediaUploadResult:
    resp = requests.post(f'{_origin(cfg)}{path}', timeout=90, **kwargs)
    quota.record(cfg, 'ebay_eps')
    capture_response(cfg, 'media', method, request_receipt,
                     resp.status_code, resp.content)
    receipt = _receipt(resp, request_receipt)
    if resp.status_code == 429:
        quota.record_429(cfg, 'ebay_eps', method)
        raise UploadQuotaExceeded(f'{method} HTTP 429: {_error_detail(resp)}')
    if resp.status_code >= 500 or resp.status_code in {408, 425}:
        resp.raise_for_status()
    if not 200 <= resp.status_code < 300:
        raise UploadDefinitivelyRejected(
            f'{method} HTTP {resp.status_code}: {_error_detail(resp)}')
    location = resp.headers.get('Location', '')
    image_id = location.rstrip('/').rsplit('/', 1)[-1] if location else ''
    try:
        payload = resp.json() if resp.content else {}
    except ValueError as exc:
        raise RuntimeError(f'{method}: malformed success response') from exc
    image_url = payload.get('imageUrl') if isinstance(payload, dict) else None
    if not image_id or not isinstance(image_url, str) or not image_url:
        raise RuntimeError(
            f'{method}: success missing Location image identifier or imageUrl')
    return MediaUploadResult(image_url, {
        'image_id': image_id, 'location': location, 'image_url': image_url,
        'expiration_date': payload.get('expirationDate'), 'method': method,
        'api_version': _MEDIA_VERSION, 'receipt': receipt,
    })


def upload_prepared(cfg: Dict[str, Any], prepared: PreparedUpload) -> MediaUploadResult:
    """Dispatch createImageFromFile exactly once with already-prepared bytes."""
    token = load_token(cfg)
    request = {
        'method': prepared.method, 'path': f'{_MEDIA_ROOT}/create_image_from_file',
        'filename': prepared.photo_path.name, 'mime': prepared.mime,
        'source_sha256': prepared.source_sha256,
        'prepared_sha256': prepared.prepared_sha256,
        'prepared_byte_length': len(prepared.image_bytes), 'order': prepared.order,
        'attempt_identity': prepared.attempt_identity,
        'authorization_sha256': hashlib.sha256(token.encode()).hexdigest(),
    }
    result = _dispatch(
        cfg, method=prepared.method, path=request['path'], request_receipt=request,
        headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
        files={'image': (prepared.photo_path.name, prepared.image_bytes, prepared.mime)},
    )
    result.metadata.update({
        'source_sha256': prepared.source_sha256,
        'prepared_sha256': prepared.prepared_sha256, 'order': prepared.order,
        'attempt_identity': prepared.attempt_identity,
    })
    return result


def create_image_from_url(cfg: Dict[str, Any], image_url: str, *,
                          order: int | None = None,
                          attempt_identity: str | None = None) -> MediaUploadResult:
    """Future adapter for verified TGW-controlled HTTPS object URLs only."""
    parsed = urlsplit(image_url)
    configured = _raw_config(cfg).get('ebay_media_controlled_https_origins', [])
    allowed = {str(value).rstrip('/') for value in configured
               if isinstance(value, str)}
    origin = f'{parsed.scheme}://{parsed.netloc}'
    if (parsed.scheme != 'https' or not parsed.netloc or parsed.username
            or parsed.password or parsed.fragment or origin not in allowed):
        raise UploadDefinitivelyRejected(
            'createImageFromUrl requires an allowlisted TGW-controlled HTTPS object origin')
    token = load_token(cfg)
    quota.precheck(cfg, 'ebay_eps')
    request = {
        'method': 'createImageFromUrl',
        'path': f'{_MEDIA_ROOT}/create_image_from_url', 'imageUrl': image_url,
        'order': order, 'attempt_identity': attempt_identity,
        'authorization_sha256': hashlib.sha256(token.encode()).hexdigest(),
    }
    result = _dispatch(
        cfg, method='createImageFromUrl', path=request['path'],
        request_receipt=request,
        headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json',
                 'Content-Type': 'application/json'}, json={'imageUrl': image_url},
    )
    result.metadata.update({'order': order, 'attempt_identity': attempt_identity})
    return result


def get_image(cfg: Dict[str, Any], image_id: str) -> Dict[str, Any]:
    """Read-only reconciliation probe for an ID captured from Location."""
    if not image_id or '/' in image_id:
        raise ValueError('invalid Media API image identifier')
    token = load_token(cfg)
    path = f'{_MEDIA_ROOT}/{quote(image_id, safe="")}'
    quota.precheck(cfg, 'ebay_eps')
    resp = requests.get(
        f'{_origin(cfg)}{path}', headers={'Authorization': f'Bearer {token}',
                                         'Accept': 'application/json'}, timeout=30)
    quota.record(cfg, 'ebay_eps')
    capture_response(cfg, 'media', 'getImage', {'path': path},
                     resp.status_code, resp.content)
    if resp.status_code == 429:
        quota.record_429(cfg, 'ebay_eps', 'getImage')
        raise UploadQuotaExceeded('getImage HTTP 429')
    if resp.status_code == 404:
        raise UploadDefinitivelyRejected('getImage HTTP 404')
    resp.raise_for_status()
    return resp.json()


def upload_photo(cfg: Dict[str, Any], photo_path: Path) -> MediaUploadResult:
    return upload_prepared(cfg, prepare_upload(cfg, photo_path))
