#!/usr/bin/env python3
"""Read-only operator-item preview for the tgw-lib development loop.

This host deliberately has no operational configuration, queue connection,
provider credential, or mutation route.  It renders the real published object
and web client against a bounded snapshot directory so an operator can guide
the interface before release review.
"""

from __future__ import annotations

import argparse
import base64
import copy
import ipaddress
import json
import re
import secrets
import stat
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from tgw.logging import announce_script_run
from tgw.operator_objects import build_item_operator_object
from tgw.workflow.action_cards import build_item_action_card

_SKU = re.compile(r"tgw[0-9]{15}")
_CATEGORY = re.compile(r"[0-9]+")
_PREVIEW_USERNAME = b"preview"
_BANNER = (
    '<div role="status" style="padding:7px 14px;background:#6b4f13;color:#fff3c4;'
    'font:700 13px/1.35 system-ui;text-align:center;border-bottom:1px solid #a77b21">'
    'tgw-lib development preview · snapshot data · commands held · '
    'eBay Sandbox target · seller consent not connected</div>'
)


def _resolve_directory(path: Path, *, label: str, root: Path | None = None) -> Path:
    """Resolve one real directory, rejecting symlink and containment escapes."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {path}")
    try:
        mode = path.lstat().st_mode
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{label} is unavailable: {path}") from exc
    if not stat.S_ISDIR(mode):
        raise ValueError(f"{label} is not a directory: {path}")
    if root is not None:
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"{label} resolves outside its configured root: {path}") from exc
    return resolved


def _resolve_regular_file(path: Path, *, root: Path, label: str) -> Path:
    """Resolve one bounded regular file without following a final symlink."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {path}")
    try:
        mode = path.lstat().st_mode
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{label} is unavailable: {path}") from exc
    if not stat.S_ISREG(mode):
        raise ValueError(f"{label} is not a regular file: {path}")
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} resolves outside its configured root: {path}") from exc
    return resolved


def _load_json(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    resolved = _resolve_regular_file(path, root=root, label=label)
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not a JSON object: {resolved.name}")
    return value


def _loopback_host(host: str) -> bool:
    value = host.strip().strip("[]")
    if value.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _validated_preview_secret(secret: str | None, *, bind_host: str) -> bytes | None:
    if secret is None:
        if not _loopback_host(bind_host):
            raise ValueError(
                "a dedicated preview secret is required when binding to a non-loopback host"
            )
        return None
    if not isinstance(secret, str) or not secret or "\r" in secret or "\n" in secret:
        raise ValueError("preview secret must be one non-empty line")
    return secret.encode("utf-8")


def _load_preview_secret(path: Path) -> str:
    """Read only the explicitly supplied preview credential; never TGW API auth."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"preview secret file must not be a symlink: {path}")
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise ValueError(f"preview secret file is unavailable: {path}") from exc
    if not stat.S_ISREG(mode):
        raise ValueError(f"preview secret file is not a regular file: {path}")
    secret = path.read_text(encoding="utf-8").rstrip("\r\n")
    if not secret or "\r" in secret or "\n" in secret:
        raise ValueError("preview secret file must contain one non-empty line")
    return secret


def _valid_basic_auth(header: str, expected_secret: bytes) -> bool:
    if len(header) > 8192:
        return False
    scheme, separator, encoded = header.partition(" ")
    if not separator or scheme.casefold() != "basic":
        return False
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        return False
    username, separator, password = decoded.partition(b":")
    if not separator:
        return False
    username_matches = secrets.compare_digest(username, _PREVIEW_USERNAME)
    password_matches = secrets.compare_digest(password, expected_secret)
    return username_matches and password_matches


def _media_base_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("media manifest requires an HTTP(S) source_base_url")
    base = value.strip().rstrip("/")
    parsed = urlsplit(base)
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("media manifest source_base_url must be a credential-free HTTP(S) URL")
    return base


def _media_name(value: Any) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("preview media names must be non-empty basenames")
    decoded = unquote(value)
    for candidate in (value, decoded):
        if (
            candidate in {".", ".."}
            or ".." in candidate
            or "/" in candidate
            or "\\" in candidate
            or "?" in candidate
            or "#" in candidate
            or "\x00" in candidate
            or any(ord(character) < 32 for character in candidate)
            or Path(candidate).name != candidate
        ):
            raise ValueError(f"malformed preview media name: {value!r}")
    return value


def create_app(
    *,
    source_root: Path,
    data_root: Path,
    media_manifest: Path,
    bind_host: str = "127.0.0.1",
    preview_secret: str | None = None,
) -> FastAPI:
    """Create a read-only app from one fully validated in-memory snapshot."""
    expected_secret = _validated_preview_secret(preview_secret, bind_host=bind_host)
    source_root = _resolve_directory(source_root, label="preview source root")
    data_root = _resolve_directory(data_root, label="preview data root")
    manifest = _load_json(
        media_manifest,
        root=data_root,
        label="preview media manifest",
    )
    manifest_items = manifest.get("items")
    if not isinstance(manifest_items, dict):
        raise ValueError("media manifest requires an items object")
    media_base = _media_base_url(manifest.get("source_base_url"))
    html_path = _resolve_regular_file(
        source_root / "src/tgw/static/operator_item.html",
        root=source_root,
        label="operator item client",
    )
    html_source = html_path.read_text(encoding="utf-8")
    if "<body>" not in html_source:
        raise ValueError("operator item client has no body element")
    preview_html = html_source.replace("<body>", "<body>" + _BANNER, 1)

    item_data_root = _resolve_directory(
        data_root / "ItemData",
        root=data_root,
        label="preview ItemData directory",
    )
    item_paths: dict[str, Path] = {}
    items: dict[str, dict[str, Any]] = {}
    for entry in item_data_root.iterdir():
        if not _SKU.fullmatch(entry.name):
            continue
        item_directory = _resolve_directory(
            entry,
            root=item_data_root,
            label=f"preview item directory {entry.name}",
        )
        item_file = _resolve_regular_file(
            item_directory / f"{entry.name}.json",
            root=item_data_root,
            label=f"preview item snapshot {entry.name}",
        )
        item = _load_json(
            item_file,
            root=item_data_root,
            label=f"preview item snapshot {entry.name}",
        )
        if str(item.get("sku") or "") != entry.name:
            raise ValueError(f"preview item snapshot identity mismatch: {entry.name}")
        item_paths[entry.name] = item_file
        items[entry.name] = item
    if not items:
        raise ValueError("preview data contains no item snapshots")

    contexts: dict[str, dict[str, Any]] = {}
    for path in data_root.iterdir():
        match = re.fullmatch(r"category-context-([0-9]+)\.json", path.name)
        if not match:
            continue
        category_id = match.group(1)
        contexts[category_id] = _load_json(
            path,
            root=data_root,
            label=f"preview category context {category_id}",
        )

    for category_id, context in contexts.items():
        if context.get("category_name"):
            continue
        for item in items.values():
            draft = item.get("draft_listing") if isinstance(item.get("draft_listing"), dict) else {}
            item_category = str(draft.get("category_id") or item.get("ebay_category_id") or "")
            if item_category == category_id:
                context["category_name"] = (
                    draft.get("category_name") or item.get("ebay_category_name")
                )
                break

    media_names: dict[str, tuple[str, ...]] = {}
    for sku, raw_names in manifest_items.items():
        if not isinstance(sku, str) or not _SKU.fullmatch(sku):
            raise ValueError(f"media manifest contains an invalid SKU: {sku!r}")
        if not isinstance(raw_names, list):
            raise ValueError(f"media manifest entry for {sku} must be a list")
        names = tuple(_media_name(name) for name in raw_names)
        if len(names) != len(set(names)):
            raise ValueError(f"media manifest entry for {sku} contains duplicate names")
        media_names[sku] = names
    missing_media_entries = sorted(set(items) - set(media_names))
    if missing_media_entries:
        raise ValueError(
            "media manifest is missing preview items: " + ", ".join(missing_media_entries)
        )

    def require_item(sku: str) -> dict[str, Any]:
        if not _SKU.fullmatch(sku) or sku not in items:
            raise HTTPException(status_code=404, detail=f"preview item not found: {sku}")
        return items[sku]

    def category_context(category_id: str) -> dict[str, Any]:
        if not _CATEGORY.fullmatch(category_id) or category_id not in contexts:
            raise HTTPException(
                status_code=404,
                detail=f"preview category context not found: {category_id}",
            )
        return copy.deepcopy(contexts[category_id])

    def category_node(category_id: str) -> dict[str, Any]:
        context = category_context(category_id)
        name = str(context.get("category_name") or category_id)
        return {
            "id": category_id,
            "name": name,
            "path": name,
            "leaf": True,
            "marketplace_id": "EBAY_US",
            "source": "preview-snapshot",
        }

    operator_objects: dict[str, dict[str, Any]] = {}
    for sku, item in items.items():
        path = item_paths[sku]
        draft = item.get("draft_listing") if isinstance(item.get("draft_listing"), dict) else {}
        category_id = str(draft.get("category_id") or item.get("ebay_category_id") or "")
        context = copy.deepcopy(contexts.get(category_id, {}))
        context["primary_category_node"] = (
            category_node(category_id) if category_id in contexts else None
        )
        secondary_category_id = str(draft.get("secondary_category_id") or "")
        context["secondary_category_node"] = (
            category_node(secondary_category_id)
            if secondary_category_id in contexts
            else None
        )
        media = [
            {
                "kind": "image",
                "name": name,
                "url": f"{media_base}/{quote(sku, safe='')}/{quote(name, safe='')}",
                "position": position,
                "primary": position == 0,
            }
            for position, name in enumerate(media_names[sku])
        ]
        card = build_item_action_card(path, (), item_document=item)
        published = build_item_operator_object(
            item=item,
            workflow_card=card,
            category_context=context,
            media=media,
            media_status={"state": "ready" if media else "empty", "reason": None},
        )
        for command in published["commands"]:
            command["enabled"] = False
            command["reason"] = (
                "Held in the read-only tgw-lib snapshot preview; no item, queue, "
                "or provider mutation is connected."
            )
        if any(command.get("enabled") for command in published["commands"]):
            raise ValueError(f"preview command hold failed for {sku}")
        operator_objects[sku] = published

    app = FastAPI(
        title="TGW operator item development preview",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def preview_boundary(request: Request, call_next):
        def secure(response):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data: http://tgw-prod:7373 https:; "
                "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                "connect-src 'self'; frame-ancestors 'none'"
            )
            return response

        if expected_secret is not None and not _valid_basic_auth(
            request.headers.get("Authorization", ""),
            expected_secret,
        ):
            response = JSONResponse(
                status_code=401,
                content={
                    "ok": False,
                    "code": "development_preview_auth_required",
                    "detail": "Dedicated preview authentication is required.",
                },
                headers={"WWW-Authenticate": 'Basic realm="TGW operator preview"'},
            )
            return secure(response)
        if request.method not in {"GET", "HEAD"}:
            response = JSONResponse(
                status_code=405,
                content={
                    "ok": False,
                    "code": "development_preview_read_only",
                    "detail": (
                        "This tgw-lib snapshot preview holds every mutation. "
                        "eBay Sandbox effects are not connected yet."
                    ),
                },
            )
            response.headers["Allow"] = "GET, HEAD"
            return secure(response)
        response = await call_next(request)
        return secure(response)

    @app.get("/")
    def root() -> RedirectResponse:
        preferred = "tgw202606021107459"
        sku = preferred if preferred in items else sorted(items)[0]
        return RedirectResponse(f"/form/operator/items/{sku}", status_code=307)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "schema": "tgw-operator-preview-health/v1",
            "host_role": "tgw-lib-development-preview",
            "data_mode": "bounded-snapshots",
            "mutation_mode": "held",
            "provider_mode": "ebay-sandbox-awaiting-seller-consent",
            "items": sorted(items),
        }

    @app.get("/form/home")
    @app.get("/form/items")
    def item_index() -> HTMLResponse:
        links = "".join(
            f'<li><a href="/form/operator/items/{sku}">{sku}</a></li>'
            for sku in sorted(items)
        )
        return HTMLResponse(
            "<!doctype html><html><title>TGW Preview</title><body>"
            + _BANNER
            + "<main style='font:16px system-ui;padding:24px'><h1>Preview items</h1><ul>"
            + links
            + "</ul></main></body></html>"
        )

    @app.get("/form/operator/items/{sku}")
    def item_form(sku: str) -> HTMLResponse:
        require_item(sku)
        return HTMLResponse(preview_html)

    @app.get("/api/operator/items/{sku}")
    def item_object(sku: str) -> dict[str, Any]:
        require_item(sku)
        return {"ok": True, "object": copy.deepcopy(operator_objects[sku])}

    @app.get("/api/ebay/category-context/{category_id}")
    def get_category_context(category_id: str) -> dict[str, Any]:
        return category_context(category_id)

    @app.get("/api/ebay/category-node/{category_id}")
    def get_category_node(category_id: str) -> dict[str, Any]:
        return {"ok": True, **category_node(category_id)}

    @app.get("/api/ebay/category-children")
    def get_category_children(parent_id: str = Query(default="")) -> dict[str, Any]:
        parent = category_node(parent_id) if parent_id else None
        children = [] if parent_id else [category_node(category_id) for category_id in sorted(contexts)]
        return {"ok": True, "parent": parent, "children": children}

    @app.get("/api/ebay/category-search")
    def search_categories(q: str = Query(default="")) -> dict[str, Any]:
        needle = q.strip().casefold()
        results = []
        for category_id in sorted(contexts):
            context = category_context(category_id)
            name = str(context.get("category_name") or category_id)
            if not needle or needle in name.casefold() or needle in category_id:
                results.append(
                    {
                        "id": category_id,
                        "name": name,
                        "path": name,
                        "leaf": True,
                        "marketplace_id": "EBAY_US",
                        "source": "preview-snapshot",
                    }
                )
        return {"ok": True, "results": results}

    return app


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--media-manifest", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7374)
    parser.add_argument(
        "--preview-secret-file",
        type=Path,
        help=(
            "file containing the dedicated preview-only HTTP Basic password "
            "for username 'preview'; "
            "required for non-loopback binds and never replaced by the production API key"
        ),
    )
    args = parser.parse_args()
    try:
        preview_secret = (
            _load_preview_secret(args.preview_secret_file)
            if args.preview_secret_file is not None
            else None
        )
        app = create_app(
            source_root=args.source_root,
            data_root=args.data_root,
            media_manifest=args.media_manifest,
            bind_host=args.host,
            preview_secret=preview_secret,
        )
    except ValueError as exc:
        parser.error(str(exc))
    announce_script_run(
        "operator_item_preview.py",
        "serve a read-only operator-item preview from bounded tgw-lib snapshots",
        source_root=str(args.source_root),
        data_root=str(args.data_root),
        media_manifest=str(args.media_manifest),
        host=args.host,
        port=args.port,
    )
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        access_log=False,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
