"""
tgw.http_server — FastAPI HTTP service (tgw-http).

Exposes inventory and pipeline operations over HTTP on port 7373.
Shared API for MC console copyin operations and the Flutter app.

Auth: Bearer <api_key> — key stored in secrets_root/tgw-api-key.json
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import sqlite3
import subprocess
import time
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.errors
import psycopg2.extras
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, Security, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import draft_sync, inventory_record
from .assets import ordered_photos as _ordered_photos
from .bootstrap_host_integration import configured_bootstrap_deployment_provider
from .config import DEFAULT_CONFIG
from .ebay.category_aspect_migration import (
    apply_category_aspect_migration,
    detect_category_orphaned_aspects,
)
from .ebay.description import build_listing_description
from .ebay.draft_specifics import get_ebay_aspects, set_ebay_aspects
from .ebay.draft_specifics import is_envelope as _is_ebay_draft_envelope
from .ebay.inventory_diff import apply_inventory_diff, diff_ebay_draft_to_inventory
from .item_mutation import item_generation
from .items import _archive_before_overwrite, atomic_write_json, create_item, locationupdate
from .operator_console_host import configured_authority_principal, configured_console_mount
from .operator_console_plugin import mount_operator_console
from .plan_authority import AuthorityPrincipal, PrincipalRole
from .queue import state_machine
from .readiness import check_ebay, readiness_html
from .resolver import load_item_doc

log = logging.getLogger(__name__)

_DISPLAY_TZ = ZoneInfo("America/Los_Angeles")


def _local_ts(raw: Any, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Format a timestamp (datetime, or ISO string with/without offset) in the
    operator's local timezone for display.

    Session 41: postgres's session timezone is GMT, so every queue_jobs
    timestamp comes back as UTC. Every display site in this file truncated
    the raw ISO string (e.g. `str(ts)[:16]`) to shorten it for the table —
    which strips the `+00:00` offset entirely, leaving a bare value that
    looks like local time but is actually UTC (off by the PST/PDT offset —
    confirmed live: an operator read a dead-lettered job's timestamp as
    "7 hours in the future"). Always convert through this helper instead of
    slicing the raw string directly.
    """
    if not raw:
        return ""
    if isinstance(raw, datetime):
        dt = raw
    else:
        try:
            dt = datetime.fromisoformat(str(raw))
        except ValueError:
            return str(raw)[:16]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_DISPLAY_TZ).strftime(fmt)


# ---------------------------------------------------------------------------
# Worker pipeline tooltip text — sourced from TGW-Pipeline-Flow.md
_WORKER_TOOLTIPS: Dict[str, str] = {
    "token_refresh": "OAuth token refresh via eBay API; fires when token expires within 30 min",
    "pm_intake": "Reads Plan-vault inbox notes → classifies → patches the separate Plan update repository",
    "catalog_rebuild": "Rebuilds JSON catalog + SQLite + location tree from all ItemData",
    "thumbnail_gen": "Generates SKU thumbnail from primary photo (Pillow)",
    "bundle_intake": "Polls incoming/newitems/, creates item stubs, enqueues ai_identify",
    "multi_intake": "Splits multi-item bundles into individual child SKUs",
    "ai_identify": "Sends photo to Ollama vision model → extracts title, category, condition",
    "ebay_draft": "Fetches eBay aspects, fills with AI (Qwen2.5), builds draft_listing block",
    "ebay_upload": "Uploads photos to eBay EPS via Trading API → permanent picture URLs",
    "ebay_price": "Browse API comps search → sets launch price + price_comps on offer",
    "ebay_stage": "Inventory API upsert + creates UNPUBLISHED offer; item visible in Seller Hub",
    "ebay_publish": "Publishes offer to eBay (manual trigger only); writes listing_id + reprice_schedule",
    "ebay_price_reducer": "Applies reprice schedule stages (launch → retail → move) via Inventory API",
    "ebay_sync": "Fetches all eBay offers → syncs status back to item JSON every 6 h",
    "ebay_legacy_sync": "GetMyeBaySelling + GetOrders via Trading API; marks sold items",
    "ebay_dole": "Self-scheduling; publishes oldest ready items at configured dole rate",
    "ebay_sku_migrate": "Batches Class A live listings: delist → rename SKU → relist (hourly)",
    "velocity_stats": "Computes nightly category velocity stats for repricer tuning",
    "echo": "No-op test worker — echoes payload and succeeds",
}

# A row in queue_jobs is not necessarily runnable: historically producers
# could enqueue a queue whose systemd consumer was not installed.  Keep this
# short cache so operator pages state that distinction without running a
# systemctl subprocess for every item rendered.
_QUEUE_CONSUMER_CACHE: Dict[str, Dict[str, str]] = {}
_QUEUE_CONSUMER_CACHE_AT: float = 0.0
_QUEUE_CONSUMER_CACHE_TTL_S = 10.0


def _queue_consumers(queue_names: List[str]) -> Dict[str, Dict[str, str]]:
    """Return live consumer availability for queue names.

    ``queued`` is only actionable when its worker unit is active.  A missing
    unit means the row is deliberately parked by configuration, not a worker
    that is merely slow.  This distinction belongs in the operator surface,
    rather than requiring a database investigation.
    """
    global _QUEUE_CONSUMER_CACHE_AT
    names = sorted({str(name) for name in queue_names if name})
    if not names:
        return {}
    now = time.monotonic()
    if now - _QUEUE_CONSUMER_CACHE_AT > _QUEUE_CONSUMER_CACHE_TTL_S:
        refreshed: Dict[str, Dict[str, str]] = {}
        for queue_name in names:
            unit = f"tgw-worker@{queue_name}.service"
            try:
                result = subprocess.run(
                    ["systemctl", "show", unit, "--property=LoadState,ActiveState", "--value"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                values = [line.strip() for line in result.stdout.splitlines() if line.strip()]
                load_state = values[0] if values else "not-found"
                active_state = values[1] if len(values) > 1 else "unknown"
            except Exception:
                load_state, active_state = "unknown", "unknown"
            if load_state == "not-found":
                status, reason = "no_consumer", "No worker is configured; job is parked."
            elif active_state == "active":
                status, reason = "active", "Worker is active."
            elif active_state == "unknown":
                status, reason = "unknown", "Worker status is unavailable."
            else:
                status, reason = "inactive", "Worker is configured but not running."
            refreshed[queue_name] = {
                "unit": unit,
                "status": status,
                "reason": reason,
            }
        _QUEUE_CONSUMER_CACHE.clear()
        _QUEUE_CONSUMER_CACHE.update(refreshed)
        _QUEUE_CONSUMER_CACHE_AT = now
    return {
        name: _QUEUE_CONSUMER_CACHE.get(
            name,
            {
                "unit": f"tgw-worker@{name}.service",
                "status": "unknown",
                "reason": "Worker status is unavailable.",
            },
        )
        for name in names
    }


# Module-level state (set during lifespan startup)
# ---------------------------------------------------------------------------

_cfg: Dict[str, Any] = {}
_api_key: str = ""
_machine_api_key: str = ""
_web_password: str = ""  # human-memorable login password; falls back to _api_key if unset
_sessions: Dict[str, float] = {}  # token → expiry timestamp (epoch seconds)

_security = HTTPBearer(auto_error=False)

# Listing index cache for webhook lookups: {listing_id: json_path}
_listing_index: Dict[str, Path] = {}
_listing_index_built_at: float = 0.0
_LISTING_INDEX_TTL = 600  # rebuild every 10 min

# Dashboard: pending_offers cache (eBay API is slow; 5 min TTL)
_pending_offers_cache: Optional[int] = None
_pending_offers_cache_at: float = 0.0
_PENDING_OFFERS_TTL = 300


class CodingProvisionStart(BaseModel):
    todo_id: int = Field(gt=0)
    object_generation: str | None = None
    source_commit: str | None = None


class CodingWorkerClaim(BaseModel):
    host: str = Field(min_length=1)
    envelope_hash: str = Field(min_length=1)
    location: Dict[str, Any]
    snapshot: Dict[str, Any]


class CodingWorkerLease(BaseModel):
    lease_token: str = Field(min_length=1)


class CodingWorkerComplete(CodingWorkerLease):
    result: Dict[str, Any]


class CodingWorkerFail(CodingWorkerLease):
    error: str = Field(min_length=1, max_length=2000)
    result: Dict[str, Any] | None = None


def _get_listing_index() -> Dict[str, Path]:
    global _listing_index, _listing_index_built_at
    if time.time() - _listing_index_built_at > _LISTING_INDEX_TTL:
        from .ebay.pull import build_listing_index

        _listing_index = build_listing_index(_cfg["itemdata_root"])
        _listing_index_built_at = time.time()
        log.info("ebay_webhook: listing index rebuilt (%d entries)", len(_listing_index))
    return _listing_index


def _listing_index_built_at_reset() -> None:
    global _listing_index_built_at
    _listing_index_built_at = 0.0


PIPELINE_ACTIONS = {
    "ai_identify",
    "resync_photos",
    "accept_proposals",
    "dismiss_proposals",
    "catalog_rebuild",
    "thumbnail_gen",
    "approve",
    "archive",
    "migrate_unblock",
    "review_mark_ready",
    "sync_from_ebay",
    "reset_draft_from_live",
    "set_ready",
    "unset_ready",
}


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _cfg, _api_key, _machine_api_key, _web_password
    from tgw.config import load_operational_config

    _cfg = load_operational_config(DEFAULT_CONFIG)
    state_machine.init(_cfg["postgres_dsn"])

    key_path: Path = _cfg["secrets_root"] / "tgw-api-key.json"
    if not key_path.exists():
        raise RuntimeError(f"API key file not found: {key_path}")
    key_data = json.loads(key_path.read_text(encoding="utf-8"))
    _api_key = key_data["api_key"]
    _machine_api_key = key_data.get("machine_api_key", "")
    if not _machine_api_key or secrets.compare_digest(_machine_api_key.encode(), _api_key.encode()):
        raise RuntimeError("tgw-api-key.json requires a distinct non-empty machine_api_key")
    _web_password = key_data.get("web_password") or _api_key

    # PP-AIOPS-001: start NATS publisher and set API attribution context
    try:
        from .apis.nats_client import init_nats
        from .items import set_mutation_context

        init_nats(_cfg)
        set_mutation_context("api:operator")
    except Exception as exc:
        log.debug("nats init skipped: %s", exc)

    # PP-QUOTA-001: operator-facing server — interactive quota context
    # (never budget-halted, only counted)
    try:
        from tgw import quota

        quota.set_context("interactive", "tgw-http")
    except Exception as exc:
        log.debug("quota context skipped: %s", exc)

    log.info("tgw-http started on port 7373")
    yield
    log.info("tgw-http shutting down")


app = FastAPI(
    title="tgw-http",
    version="1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


class _NoCacheStaticFiles(StaticFiles):
    """Force revalidation on every request (Dave, 2026-07-17): a plain
    StaticFiles mount lets browsers cache tgw.js/tgw.css indefinitely, so a
    server-restarted fix (e.g. syncURLParam/getURLParam) can silently keep
    running the OLD cached script and throw ReferenceErrors that look like
    a broken fix rather than a stale cache. These files are small and
    change often during active work — correctness here matters more than
    saving a browser round-trip."""

    def is_not_modified(self, *args, **kwargs) -> bool:  # pragma: no cover - trivial
        return False

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", _NoCacheStaticFiles(directory=str(_STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Auth — Bearer token (CLI/Flutter) or browser session cookie
# ---------------------------------------------------------------------------

_SESSION_COOKIE = "tgw_session"
_SESSION_MAX_AGE = 365 * 86400  # 1 year — expires only when user clears browser data

_LOGIN_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TGW — Sign in</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0d0d0d;color:#ccc;font-family:system-ui,sans-serif;
     display:flex;align-items:center;justify-content:center;min-height:100vh}}
.box{{background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:32px 28px;width:320px}}
h1{{font-size:1.1em;color:#9bf;margin-bottom:20px}}
label{{font-size:.8em;color:#888;display:block;margin-bottom:6px}}
input[type=password]{{width:100%;background:#111;border:1px solid #444;border-radius:4px;
  color:#eee;padding:8px 10px;font-size:.95em;margin-bottom:14px}}
input[type=password]:focus{{outline:none;border-color:#4a8ade}}
button{{width:100%;background:#1a4a8a;color:#fff;border:none;border-radius:4px;
  padding:10px;font-size:.95em;cursor:pointer}}
button:hover{{background:#2a5a9a}}
.err{{color:#f77;font-size:.82em;margin-top:10px}}
</style>
</head>
<body>
<div class="box">
  <h1>TGW</h1>
  <form method="post" action="/login">
    <input type="hidden" name="next" value="{next}">
    <label for="key">Password</label>
    <input type="password" id="key" name="key" autofocus autocomplete="current-password">
    <button type="submit">Sign in</button>
    {error}
  </form>
</div>
</body>
</html>"""


def _safe_next_path(path: str) -> str:
    """Only allow same-origin relative paths as a post-login redirect target.

    Rejects protocol-relative URLs (//evil.com) that browsers resolve as
    absolute, and anything not starting with a single leading slash.
    """
    if not path or not path.startswith("/") or path.startswith("//") or path.startswith("/\\"):
        return "/form/home"
    return path


def _require_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(_security),
    request: Request = None,
) -> str:
    if credentials and secrets.compare_digest(credentials.credentials.encode(), _api_key.encode()):
        return "operator:api-key"
    tok = request.cookies.get(_SESSION_COOKIE) if request else None
    if tok and _sessions.get(tok, 0) > time.time():
        return "operator:web-session"
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")


def _require_fence_patch_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(_security),
    request: Request = None,
) -> str:
    """Authenticate the item-write fence without trusting caller headers.

    The dedicated machine credential is intentionally accepted only at this
    boundary.  It is not a second general API credential and cannot authorize
    operator, Plan-authority, or provider-effect endpoints.
    """
    if (
        credentials
        and _machine_api_key
        and secrets.compare_digest(credentials.credentials.encode(), _machine_api_key.encode())
    ):
        return "machine:item-write-fence"
    return _require_auth(credentials, request)


def _configured_authority_principal(field: str, role: PrincipalRole, binding: str) -> AuthorityPrincipal:
    """Construct an authority principal from server configuration only."""
    try:
        return configured_authority_principal(
            _cfg,
            field=field,
            role=role,
            authentication_binding=binding,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"named Plan authority {role.value} principal is not configured",
        ) from exc


def _require_plan_operator(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(_security),
    request: Request = None,
) -> AuthorityPrincipal:
    """Map an authenticated API key or web session to one named operator.

    Authentication mechanism labels are not durable operator identities.  The
    host must explicitly bind each accepted mechanism to a configured named
    principal before any authority mutation or console read can occur.
    """
    mechanism = _require_auth(credentials, request)
    if mechanism == "operator:api-key":
        return _configured_authority_principal(
            "plan_authority_operator_api_principal",
            PrincipalRole.OPERATOR,
            "api-key",
        )
    if mechanism == "operator:web-session":
        return _configured_authority_principal(
            "plan_authority_operator_session_principal",
            PrincipalRole.OPERATOR,
            "web-session",
        )
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid Plan authority operator")


def _require_plan_executor(request: Request) -> AuthorityPrincipal:
    """Authenticate a dedicated effect executor, never an operator session.

    Operator credentials may request/decide and inspect authority.  They cannot
    redeem it: the executor needs its separately configured secret and a named
    executor identity, which keeps the HTTP /consume capability out of the
    normal browser/API-key role.
    """
    reference = _cfg.get("plan_authority_executor_credential_env")
    supplied = request.headers.get("X-TGW-Executor-Authorization", "")
    expected = os.environ.get(reference) if isinstance(reference, str) else None
    if not expected or not supplied.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid Plan authority executor")
    if not secrets.compare_digest(supplied.removeprefix("Bearer ").encode(), expected.encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid Plan authority executor")
    if request.headers.get("X-TGW-Executor-Identity"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="client-supplied executor identity is forbidden")
    return _configured_authority_principal(
        "plan_authority_executor_principal",
        PrincipalRole.EXECUTOR,
        f"credential-env:{reference}",
    )


AUTH = Depends(_require_auth)

# Consolidated PlanAuthority console. The late-bound host adapter performs no
# database or Plan access at import time and reuses this service's auth seam.
mount_operator_console(
    app,
    configured_console_mount(
        lambda: _cfg,
        require_operator=_require_plan_operator,
        require_executor=_require_plan_executor,
        bootstrap_provider_factory=configured_bootstrap_deployment_provider,
    ),
)


def _require_coding_worker(request: Request) -> str:
    """Authenticate a coding worker with its dedicated referenced secret."""
    coding = _cfg.get("coding")
    if not isinstance(coding, dict):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="coding worker is not configured")
    reference = coding.get("worker_credential_env")
    supplied = request.headers.get("X-TGW-Worker-Authorization", "")
    identity = request.headers.get("X-TGW-Worker-Identity", "")
    expected = os.environ.get(reference) if isinstance(reference, str) and reference else None
    if not expected or not supplied.startswith("Bearer ") or not identity:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid coding worker credential")
    if not secrets.compare_digest(supplied.removeprefix("Bearer ").encode(), expected.encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid coding worker credential")
    if identity != coding.get("worker_identity"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid coding worker identity")
    return identity


WORKER_AUTH = Depends(_require_coding_worker)


@app.get("/api/coding/worker/requests/next")
def coding_worker_next(worker_identity: str = WORKER_AUTH):
    from .coding_provision import next_request

    try:
        return next_request(_cfg, worker_identity) or {}
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/coding/requests", dependencies=[AUTH])
def coding_provision_start(body: CodingProvisionStart):
    """Persist a request-safe coding job; the tgw-lib worker resolves and
    validates its local worktree envelope after claim."""
    from .coding_provision import create_request

    try:
        return create_request(
            _cfg,
            todo_id=body.todo_id,
            object_generation=body.object_generation,
            source_commit=body.source_commit,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/coding/requests/{request_id}", dependencies=[AUTH])
def coding_provision_status(request_id: str):
    from .coding_provision import get_request

    try:
        return get_request(_cfg, request_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/coding/requests/{request_id}/stop", dependencies=[AUTH])
def coding_provision_stop(request_id: str):
    from .coding_provision import stop_request

    try:
        return stop_request(_cfg, request_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/coding/access-status", dependencies=[AUTH])
def coding_access_status(request_id: str | None = None):
    from .coding_provision import access_status

    try:
        return access_status(_cfg, request_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/coding/worker/requests/{request_id}")
def coding_worker_request(request_id: str, worker_identity: str = WORKER_AUTH):
    from .coding_provision import get_request

    try:
        document = get_request(_cfg, request_id)
        if document.get("worker_identity") != worker_identity:
            raise HTTPException(status_code=403, detail="request is assigned to another worker")
        return document
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/coding/worker/requests/{request_id}/claim")
def coding_worker_claim(request_id: str, body: CodingWorkerClaim, worker_identity: str = WORKER_AUTH):
    from .coding_provision import claim_request

    try:
        return claim_request(_cfg, request_id=request_id, local_host=body.host, worker_identity=worker_identity, envelope_hash=body.envelope_hash, location=body.location, snapshot=body.snapshot)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/coding/worker/requests/{request_id}/start")
def coding_worker_start(request_id: str, body: CodingWorkerLease, worker_identity: str = WORKER_AUTH):
    from .coding_provision import start_request

    try:
        return start_request(_cfg, request_id=request_id, worker_identity=worker_identity, lease_token=body.lease_token)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/coding/worker/requests/{request_id}/complete")
def coding_worker_complete(request_id: str, body: CodingWorkerComplete, worker_identity: str = WORKER_AUTH):
    from .coding_provision import complete_request

    try:
        return complete_request(_cfg, request_id=request_id, worker_identity=worker_identity, lease_token=body.lease_token, result=body.result)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/coding/worker/requests/{request_id}/fail")
def coding_worker_fail(request_id: str, body: CodingWorkerFail, worker_identity: str = WORKER_AUTH):
    from .coding_provision import fail_request

    try:
        return fail_request(_cfg, request_id=request_id, worker_identity=worker_identity, lease_token=body.lease_token, error=body.error, result=body.result)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.middleware("http")
async def _session_guard(request: Request, call_next):
    """Redirect unauthenticated /form/* requests to the login page."""
    if request.url.path.startswith("/form/"):
        tok = request.cookies.get(_SESSION_COOKIE)
        if not (tok and _sessions.get(tok, 0) > time.time()):
            from urllib.parse import quote

            dest = request.url.path
            if request.url.query:
                dest += "?" + request.url.query
            return RedirectResponse(f"/login?next={quote(dest)}", status_code=303)
    return await call_next(request)


@app.get("/login")
def login_get(next: str = "/form/home"):
    import html as _html

    return HTMLResponse(_LOGIN_HTML.format(next=_html.escape(next), error=""))


@app.post("/login")
async def login_post(
    next: str = Form(default="/form/home"),
    key: str = Form(default=""),
):
    import html as _html

    if _web_password and secrets.compare_digest(key.encode(), _web_password.encode()):
        # Prune expired sessions before adding a new one
        now = time.time()
        expired = [t for t, exp in _sessions.items() if exp <= now]
        for t in expired:
            del _sessions[t]
        tok = secrets.token_hex(32)
        _sessions[tok] = now + _SESSION_MAX_AGE
        dest = _safe_next_path(next)
        resp = RedirectResponse(dest, status_code=303)
        resp.set_cookie(
            _SESSION_COOKIE,
            tok,
            httponly=True,
            samesite="strict",
            max_age=_SESSION_MAX_AGE,
        )
        return resp
    html = _LOGIN_HTML.format(next=_html.escape(next), error='<p class="err">Invalid key.</p>')
    return HTMLResponse(html, status_code=401)


# ---------------------------------------------------------------------------
# SQLite helper
# ---------------------------------------------------------------------------


def _sqlite_conn() -> sqlite3.Connection:
    con = sqlite3.connect(str(_cfg["sqlite_catalog_path"]), check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class PatchBody(BaseModel):
    fields: Dict[str, Any]


class ActionBody(BaseModel):
    action: str
    options: Optional[Dict[str, Any]] = None


class WorkflowGoalBody(BaseModel):
    goal_profile_id: str = "tgw.ebay_listable"
    scopes: List[str] = Field(default_factory=list)
    authority_ttl_seconds: int = 300


class OperatorCommandBody(BaseModel):
    command_id: str
    object_generation: str
    values: Dict[str, Any] = Field(default_factory=dict)
    authority_ttl_seconds: int = Field(300, ge=30, le=900)


class SetTemplateBody(BaseModel):
    template_key: str


class BulkBody(BaseModel):
    field: str
    value: str
    skus: Optional[List[str]] = None
    location: Optional[str] = None
    status: Optional[str] = None
    search: Optional[str] = None
    limit: int = Field(0, ge=0)


class BulkActionBody(BaseModel):
    skus: List[str]
    action: str


class PMChatBody(BaseModel):
    message: str
    history: List[Dict[str, str]] = Field(default_factory=list)


class PMActionBody(BaseModel):
    type: str
    agent: Optional[str] = None
    body: Optional[str] = None
    priority: int = 50
    text: Optional[str] = None


class OfferRespondBody(BaseModel):
    listing_id: str
    action: str
    counter_price: Optional[float] = None
    dry_run: bool = True
    by: str = "operator"


class RevisionApplyBody(BaseModel):
    dry_run: bool = True
    by: str = "operator"


class PhotoOrderBody(BaseModel):
    order: List[str]


class InventoryLockBody(BaseModel):
    key: str
    locked: bool


class InventoryDiffApplyBody(BaseModel):
    # todo #1417: the checked-subset of keys from the diff panel's default-
    # checked checkboxes. Values are NEVER trusted from the client — the
    # apply action re-diffs live and only writes keys still an active diff
    # at call time (tgw.ebay.inventory_diff.apply_inventory_diff).
    keys: List[str]


class CategoryAspectMigrationApplyBody(BaseModel):
    # todo #1471: the checked-subset of keys from the migration panel's
    # default-checked (= discard from eBay, move to Set A) checkboxes.
    # Values are NEVER trusted from the client — the apply action
    # re-detects live and only migrates keys still actually orphaned at
    # call time (tgw.ebay.category_aspect_migration.
    # apply_category_aspect_migration).
    keys: List[str]


class AppendBody(BaseModel):
    op: str
    data: Dict[str, Any]
    # PP-INTAKE-004 Phase 1a: caller signals the capture session is done —
    # triggers the ai_reidentify refinement pass (or the fallback first-time
    # identify, if the early-fire threshold was never reached) once the full
    # photo set for this SKU exists.
    session_complete: Optional[bool] = None


class EbayWriteBody(BaseModel):
    ebay_offer: Optional[Dict[str, Any]] = None
    ebay_listing: Optional[Dict[str, Any]] = None
    ebay_submitted: Optional[Dict[str, Any]] = None
    ebay_live: Optional[Dict[str, Any]] = None
    # Fields the caller explicitly asserts it OWNS and intends to refresh —
    # e.g. ["price_comps"] from ebay_price, ["photo_verify"] from ebay_repush.
    # Everyone else stays blocked from touching a protected field (#1189).
    allow_protected: Optional[List[str]] = None


class CreateItemBody(BaseModel):
    sku: str
    data: Dict[str, Any]


# ---------------------------------------------------------------------------
# GET /api/items — search catalog
# ---------------------------------------------------------------------------


@app.get("/api/items", dependencies=[AUTH])
def list_items(
    search: str = "",
    location: str = "",
    status_filter: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 200,
    offset: int = 0,
) -> Dict[str, Any]:
    db_path = _cfg["sqlite_catalog_path"]
    if not db_path.exists():
        raise HTTPException(status_code=503, detail="SQLite catalog not built")

    clauses: List[str] = []
    params: List[Any] = []

    # Hide archived/deleted unless explicitly filtered for
    if not status_filter:
        clauses.append("(status IS NULL OR status NOT IN ('archived', 'deleted'))")

    if search:
        clauses.append("(title LIKE ? OR sku LIKE ? OR json_extract(data, '$.sku_old') LIKE ?)")
        params += [f"%{search}%", f"%{search}%", f"%{search}%"]
    if location:
        clauses.append("location = ?")
        params.append(location)
    if status_filter == "__eligible__":
        # Eligible for listing (Dave, s42; blank-status fix #1377): new /
        # In Stock, and not currently on eBay (no Active listing, no
        # PUBLISHED offer). Ended listings qualify — they can be relisted.
        # Sold/disposed/archived excluded by the status allow-list. Items
        # with no status field at all (never stamped by the intake
        # pipeline — a real, common gap, not a data error) count as
        # eligible too, matching how the default "All" view already
        # treats blank status as active/non-terminal (see the
        # `if not status_filter` clause above). This is the feed for
        # one-at-a-time listing runs.
        clauses.append("(status IS NULL OR status = '' OR LOWER(status) IN ('new', 'in stock'))")
        clauses.append("(json_extract(data, '$.ebay_listing.status') IS NULL OR json_extract(data, '$.ebay_listing.status') NOT IN ('Active', 'PUBLISHED'))")
        clauses.append("(json_extract(data, '$.ebay_offer.status') IS NULL OR json_extract(data, '$.ebay_offer.status') != 'PUBLISHED')")
    elif status_filter:
        clauses.append("status = ?")
        params.append(status_filter)
    if date_from:
        # SKU encodes date as tgwYYYYMMDD... — substring compare
        clauses.append("substr(sku, 4, 8) >= ?")
        params.append(date_from[:8])
    if date_to:
        clauses.append("substr(sku, 4, 8) <= ?")
        params.append(date_to[:8])

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    count_sql = f"SELECT COUNT(*) FROM catalog {where}"
    sql = (
        f"SELECT sku, title, location, status, price, qty, image, attribute_set,"
        f" json_extract(data, '$.ebay_listing.listing_id') AS ebay_listing_id,"
        f" json_extract(data, '$.ebay_listing.status') AS ebay_listing_status,"
        f" json_extract(data, '$.ebay_offer.offer_id') AS ebay_offer_id,"
        f" json_extract(data, '$.ebay_offer.ready_at') AS ebay_ready_at,"
        f" CASE WHEN json_extract(data, '$.draft_listing') IS NOT NULL THEN 1 ELSE 0 END AS has_draft"
        f" FROM catalog {where} ORDER BY sku DESC LIMIT ? OFFSET ?"
    )

    con = _sqlite_conn()
    try:
        total = con.execute(count_sql, params).fetchone()[0]
        rows = [dict(r) for r in con.execute(sql, params + [limit, offset]).fetchall()]
    finally:
        con.close()

    return {"ok": True, "count": total, "items": rows}


# ---------------------------------------------------------------------------
# GET /api/items/pending-revision — items with a non-empty revision_draft
# Must be registered before /api/items/{sku} so the literal path wins.
# ---------------------------------------------------------------------------


@app.get("/api/items/pending-revision", dependencies=[AUTH])
def get_pending_revisions() -> Dict[str, Any]:
    """Return items that have a non-empty revision_draft, with draft details."""
    db_path = _cfg.get("sqlite_catalog_path")
    items: List[Dict[str, Any]] = []

    if db_path and Path(db_path).exists():
        try:
            with _sqlite_conn() as con:
                rows = con.execute("SELECT sku, title, location, data FROM catalog WHERE json_extract(data, '$.revision_draft') IS NOT NULL ORDER BY sku").fetchall()
            for row in rows:
                draft: Dict[str, Any] = {}
                try:
                    doc = json.loads(row["data"] or "{}")
                    draft = doc.get("revision_draft") or {}
                except Exception:
                    pass
                if not draft or not draft.get("delta"):
                    continue
                items.append(
                    {
                        "sku": row["sku"],
                        "title": row["title"] or "",
                        "location": row["location"] or "",
                        "draft": draft,
                    }
                )
        except Exception as exc:
            log.warning("pending-revision: catalog query failed: %s", exc)

    return {"ok": True, "items": items, "count": len(items)}


# ---------------------------------------------------------------------------
# GET /api/items/review-queue — post-draft items awaiting human approval
# Must be registered before /api/items/{sku} so the literal path wins.
# ---------------------------------------------------------------------------


@app.get("/api/items/review-queue", dependencies=[AUTH])
def get_review_queue() -> Dict[str, Any]:
    """Items where ebay_draft completed (draft_listing present) but not yet staged.

    Excludes items that already have an ebay_offer.offer_id (staged/ready/listed)
    or an ebay_listing (published). Returns compact display data for the review UI.
    """
    db_path = _cfg.get("sqlite_catalog_path")
    items: List[Dict[str, Any]] = []

    if db_path and Path(db_path).exists():
        try:
            with _sqlite_conn() as con:
                rows = con.execute(
                    """
                    SELECT sku, title, location, status, price, data
                    FROM catalog
                    WHERE json_extract(data, '$.draft_listing') IS NOT NULL
                      AND (json_extract(data, '$.ebay_offer.offer_id') IS NULL
                           OR json_extract(data, '$.ebay_offer.offer_id') = '')
                      AND json_extract(data, '$.ebay_listing.listing_id') IS NULL
                    ORDER BY sku
                    """
                ).fetchall()
        except Exception as exc:
            log.warning("review-queue: catalog query failed: %s", exc)
            rows = []
        for row in rows:
            doc: Dict[str, Any] = {}
            try:
                doc = json.loads(row["data"] or "{}")
            except Exception:
                pass
            draft = doc.get("draft_listing") or {}
            items.append(
                {
                    "sku": row["sku"],
                    "title": draft.get("title") or row["title"] or "",
                    "location": row["location"] or "",
                    "status": row["status"] or "",
                    "price": draft.get("price") if draft.get("price") is not None else row["price"],
                    "condition": draft.get("condition") or doc.get("condition") or "",
                    "condition_label": draft.get("condition_label") or "",
                    "condition_description": draft.get("description") or "",
                    "category_id": draft.get("category_id") or doc.get("ebay_category_id") or "",
                    "category_name": draft.get("category_name") or doc.get("ebay_category_name") or "",
                    "shipping_profile": draft.get("shipping_profile") or doc.get("shipping_profile") or "",
                    "quality": draft.get("quality") or {},
                    "aspects_required_total": draft.get("aspects_required_total"),
                    "aspects_required_filled": draft.get("aspects_required_filled"),
                    "category_confidence": draft.get("category_confidence") or doc.get("category_confidence") or "",
                    "offline_draft": bool(draft.get("offline_draft") or doc.get("offline_draft")),
                    "lookup_category_name": (doc.get("product_lookup") or {}).get("category_name") or "",
                    "lookup_category_id": str((doc.get("product_lookup") or {}).get("ebay_category_id") or ""),
                }
            )

    return {"ok": True, "items": items, "count": len(items)}


# ---------------------------------------------------------------------------
# GET /api/items/{sku} — full item detail
# ---------------------------------------------------------------------------


def _workflow_attempt_rows(sku: str, limit: int = 100) -> List[Dict[str, Any]]:
    """Join attempts by canonical entity identity, retaining legacy SKU rows."""
    with psycopg2.connect(_cfg["postgres_dsn"]) as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT job_id::text, queue_name, state, entity_type, entity_id,
                       payload_json, attempt_count, max_attempts,
                       error_code, error_detail, not_before,
                       created_at, updated_at, finished_at
                  FROM queue_jobs
                 WHERE (entity_type = 'item' AND entity_id = %s)
                    OR payload_json->>'sku' = %s
                 ORDER BY created_at DESC
                 LIMIT %s
                """,
                (sku, sku, limit),
            )
            rows = [dict(row) for row in cur.fetchall()]
    consumers = _queue_consumers([str(row.get("queue_name") or "") for row in rows])
    for row in rows:
        row["consumer"] = consumers.get(str(row.get("queue_name") or ""), {})
        not_before = row.get("not_before")
        if row.get("state") in {"queued", "retry_wait"} and isinstance(not_before, datetime) and not_before > datetime.now(timezone.utc):
            # A deliberate delayed/repeating job is not a stalled job.  Keep
            # the timestamp in the row so the item view can say when it is
            # due instead of offering an unexplained queued state.
            row["scheduled_at"] = not_before
        payload = row.get("payload_json") or {}
        result = payload.get("result") if isinstance(payload, dict) else None
        governed = isinstance(payload, dict) and all(payload.get(key) for key in ("treatment_id", "graph_id", "object_generation"))
        ambiguous = isinstance(result, dict) and result.get("outcome") in {
            "ambiguous",
            "reconciliation_required",
        }
        row["retry_allowed"] = row.get("state") == "dead_letter" and not governed and not ambiguous
    return rows


def _workflow_reconciled_provider_effect_ids(attempts: List[Dict[str, Any]]) -> frozenset[str]:
    """Return terminally reconciled effects referenced by this item's jobs."""
    effect_ids = {
        result.get("evidence", {}).get("provider_effect_id")
        for row in attempts
        for result in [(row.get("payload_json") or {}).get("result")]
        if isinstance(result, dict) and isinstance(result.get("evidence"), dict) and isinstance(result["evidence"].get("provider_effect_id"), str)
    }
    if not effect_ids:
        return frozenset()
    with psycopg2.connect(_cfg["postgres_dsn"]) as con:
        with con.cursor() as cur:
            cur.execute(
                "SELECT effect_id FROM provider_effects WHERE effect_id = ANY(%s) AND state IN ('succeeded', 'rejected')",
                (list(effect_ids),),
            )
            return frozenset(str(row[0]) for row in cur.fetchall())


def _workflow_reconciliation_rows(sku: str, limit: int = 100) -> Dict[str, Any]:
    """Return only privacy-safe ledger columns needed to reconcile one item.

    Request/authority JSON and provider result bodies are deliberately excluded.
    This is observation only: it neither contacts a provider nor changes a ledger.
    """
    queries = {
        "effects": """
            SELECT effect_id, provider, operation, entity_type, entity_id,
                   object_generation, graph_id, treatment_id, treatment_version,
                   condition_hash, state, error_detail,
                   created_at, dispatched_at, finished_at, updated_at
              FROM provider_effects
             WHERE entity_type = 'item' AND entity_id = %s
             ORDER BY created_at DESC LIMIT %s
        """,
        "authorities": """
            SELECT authority_id, operator_identity, surface, entity_id,
                   goal_profile_id, goal_profile_version, object_generation,
                   provider_identity, scopes, issued_at, expires_at,
                   superseded_at, superseded_by
              FROM operator_authorities
             WHERE entity_id = %s
             ORDER BY issued_at DESC LIMIT %s
        """,
        "observations": """
            SELECT observation_id, observation_type, provider, provider_identity,
                   sku, offer_id, object_generation, graph_id, condition_hash,
                   content_identity, outcome, observed_at, created_at
              FROM provider_observations
             WHERE sku = %s
             ORDER BY created_at DESC LIMIT %s
        """,
    }
    result: Dict[str, Any] = {}
    with psycopg2.connect(_cfg["postgres_dsn"]) as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for name, query in queries.items():
                cur.execute(query, (sku, limit))
                result[name] = [dict(row) for row in cur.fetchall()]
    return result


def _workflow_provider_identity() -> str:
    migration = _cfg.get("workflow_migration")
    if migration is None and isinstance(_cfg.get("raw"), dict):
        migration = _cfg["raw"].get("workflow_migration")
    value = migration.get("ebay_provider_identity") if isinstance(migration, dict) else None
    return value if isinstance(value, str) else ""


def _current_item_operator_object(sku: str) -> Dict[str, Any]:
    """Read and publish one exact current server-owned item object."""
    from .operator_objects import build_item_operator_object
    from .workflow.action_cards import build_item_action_card

    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"sku not found: {sku}")
    try:
        item = load_item_doc(json_path)
        attempts = _workflow_attempt_rows(sku)
        reconciled_effect_ids = _workflow_reconciled_provider_effect_ids(attempts)
        workflow_card = build_item_action_card(
            json_path,
            attempts,
            provider_identity=_workflow_provider_identity(),
            reconciled_provider_effect_ids=reconciled_effect_ids,
        )
        draft = item.get("draft_listing") if isinstance(item.get("draft_listing"), dict) else {}
        category_id = str(draft.get("category_id") or item.get("ebay_category_id") or "")
        current_condition = str(draft.get("condition_enum") or draft.get("condition") or "")
        category_context = ebay_category_context(category_id, current_condition=current_condition) if category_id and category_id != "99" else {}
        category_context = dict(category_context)
        groups_path = _cfg.get("category_groups_path")
        if groups_path and Path(groups_path).exists():
            raw_groups = json.loads(Path(groups_path).read_text(encoding="utf-8"))
            category_context["category_groups"] = [
                {"value": key, "label": str(group.get("name") or key),
                 "size_class": str(group.get("size_class") or ""), "ai_hint": str(group.get("ai_hint") or ""),
                 "ebay_categories": [str(value) for value in group.get("ebay_categories", []) if str(value)]}
                for key, group in raw_groups.get("groups", {}).items() if isinstance(group, dict)
            ]
            category_context["record_condition_vocabulary"] = list((raw_groups.get("condition_factors") or {}).keys())
            category_context["record_attribute_vocabulary"] = raw_groups.get("attribute_vocabulary") or raw_groups.get("attributes") or {}
        try:
            store_categories, _store_refreshed, _store_error = _store_categories_snapshot(_cfg)
        except (KeyError, OSError, ValueError):
            store_categories = []
        try:
            fulfillment, _fulfillment_refreshed, _fulfillment_error = _fulfillment_policies_snapshot(_cfg)
        except (KeyError, OSError, ValueError):
            fulfillment = {}
        category_context["store_categories"] = [{"value": entry["id"], "label": entry["name"]} for entry in store_categories if isinstance(entry.get("id"), str) and isinstance(entry.get("name"), str)]
        category_context["fulfillment_policies"] = [{"value": policy_id, "label": label} for policy_id, label in sorted(fulfillment.items(), key=lambda item: (item[1].casefold(), item[0]))]
        return build_item_operator_object(
            item=item,
            workflow_card=workflow_card,
            category_context=category_context,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"operator object unavailable: {exc}") from exc


@app.get("/api/operator/items", dependencies=[AUTH])
def operator_item_catalog(
    search: str = "",
    status_filter: str = "",
    limit: int = 200,
    offset: int = 0,
) -> Dict[str, Any]:
    """Publish catalog navigation without inventing item workflow state."""
    catalog = list_items(
        search=search,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )
    return {
        "ok": True,
        "schema": "tgw-operator-catalog/v1",
        "count": catalog["count"],
        "items": [
            {
                **item,
                "object_url": f"/api/operator/items/{item['sku']}",
                "web_url": f"/form/operator/items/{item['sku']}",
            }
            for item in catalog["items"]
        ],
    }


@app.get("/api/operator/items/{sku}", dependencies=[AUTH])
def get_item_operator_object(sku: str) -> Dict[str, Any]:
    return {"ok": True, "object": _current_item_operator_object(sku)}


@app.post("/api/operator/items/{sku}/commands")
def execute_item_operator_command(
    sku: str,
    body: OperatorCommandBody,
    operator_identity: str = Depends(_require_auth),
) -> Dict[str, Any]:
    """Execute only a command supplied by the current published object."""
    published = _current_item_operator_object(sku)
    current_generation = published["object_generation"]
    if body.object_generation != current_generation:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "generation_conflict",
                "expected": current_generation,
                "received": body.object_generation,
                "refresh": f"/api/operator/items/{sku}",
            },
        )
    commands = {command["id"]: command for command in published["commands"]}
    command = commands.get(body.command_id)
    if command is None:
        raise HTTPException(status_code=400, detail="command is not published for this object")
    if not command["enabled"]:
        raise HTTPException(
            status_code=409,
            detail={"code": "command_held", "reason": command.get("reason")},
        )
    local_save_commands = {"save-inventory", "save-listing-draft"}
    if body.command_id in local_save_commands and not body.values:
        raise HTTPException(status_code=422, detail=f"{body.command_id} requires server-published field values")

    checked_values: Dict[str, Any] = {}
    if body.values:
        from .operator_objects import validate_operator_command_values

        try:
            checked_values = validate_operator_command_values(
                published,
                body.command_id,
                body.values,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if body.command_id == "save-inventory":
            item_fields = checked_values.get("item_fields", {})
            patch_fields = {**item_fields}
            selected_group = patch_fields.get("category_group")
            if selected_group:
                options = published["field_schema"].get("category_groups", [])
                selected = next((option for option in options if option.get("value") == selected_group), None)
                if selected is None:
                    raise HTTPException(status_code=422, detail="category_group is not a published TGW group")
                patch_fields.update(category_group=selected_group, size_class=selected.get("size_class", ""), ai_hint=selected.get("ai_hint", ""))
                current_record = published["item"]["record"]
                current_draft = current_record.get("draft_listing") if isinstance(current_record.get("draft_listing"), dict) else {}
                if not (current_draft.get("category_id") or current_record.get("ebay_category_id")):
                    categories = selected.get("ebay_categories") or []
                    if categories:
                        patch_fields["draft_listing"] = {"category_id": categories[0]}
        elif body.command_id == "save-listing-draft":
            patch_fields = {
                "draft_listing": checked_values.get("draft_listing", {}),
            }
        else:
            patch_fields = {"draft_listing": checked_values}
        patch_item(
            sku,
            PatchBody(fields=patch_fields),
            Request({
                "type": "http",
                "headers": [],
                "_tgw_operator_object_capability": _OPERATOR_OBJECT_CAPABILITY,
            }),
            operator_identity,
        )
        published = _current_item_operator_object(sku)
        command = next(item for item in published["commands"] if item["id"] == body.command_id)
        if not command["enabled"]:
            raise HTTPException(
                status_code=409,
                detail={"code": "command_held_after_update", "reason": command.get("reason")},
            )

    if body.command_id in local_save_commands:
        return {
            "ok": True,
            "command_id": body.command_id,
            "authority_scope": command["authority_scope"],
            "object_generation": published["object_generation"],
            "refresh": f"/api/operator/items/{sku}",
        }

    provider_identity = _workflow_provider_identity()
    if not provider_identity:
        raise HTTPException(status_code=503, detail="provider identity is not configured")
    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    try:
        if body.command_id == "list-item":
            from .workflow.listing_migration import authorize_and_dispatch_next_listing_effect

            result, dispatched, authority_id, authority_created = authorize_and_dispatch_next_listing_effect(
                json_path,
                operator_identity=operator_identity,
                surface="http:operator-object:list-item",
                provider_identity=provider_identity,
                ttl_seconds=body.authority_ttl_seconds,
            )
        else:
            from .workflow.listing_migration import authorize_and_dispatch_update_item

            result, dispatched, authority_id, authority_created = authorize_and_dispatch_update_item(
                json_path,
                operator_identity=operator_identity,
                surface="http:operator-object:update-item",
                provider_identity=provider_identity,
                ttl_seconds=body.authority_ttl_seconds,
            )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "ok": True,
        "command_id": body.command_id,
        "authority_scope": command["authority_scope"],
        "authority_id": authority_id,
        "authority_created": authority_created,
        "graph_id": result.graph.graph_id,
        "object_generation": result.graph.object_generation,
        "dispatched": bool(dispatched and dispatched.enqueued),
        "job_id": dispatched.job_id if dispatched else "",
        "held_external": list(result.held_external),
        "operator_gates": list(result.operator_gates),
        "refresh": f"/api/operator/items/{sku}",
    }


@app.get("/api/items/{sku}/workflow", dependencies=[AUTH])
def item_workflow(sku: str) -> Dict[str, Any]:
    """Return the current read-only workflow Action Card for one item."""
    from .workflow.action_cards import build_item_action_card

    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"sku not found: {sku}")
    try:
        attempts = _workflow_attempt_rows(sku)
        reconciled_effect_ids = _workflow_reconciled_provider_effect_ids(attempts)
        card = build_item_action_card(
            json_path,
            attempts,
            provider_identity=_workflow_provider_identity(),
            reconciled_provider_effect_ids=reconciled_effect_ids,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"workflow projection unavailable: {exc}")
    return {"ok": True, "workflow": card}


@app.get("/api/items/{sku}/workflow-reconciliation", dependencies=[AUTH])
def item_workflow_reconciliation(sku: str) -> Dict[str, Any]:
    """Expose a read-only, privacy-safe reconciliation bundle for one item."""
    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"sku not found: {sku}")
    try:
        item = load_item_doc(json_path)
        ledgers = _workflow_reconciliation_rows(sku)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"workflow reconciliation unavailable: {exc}") from exc
    offer = item.get("ebay_offer") if isinstance(item.get("ebay_offer"), dict) else {}
    listing = item.get("ebay_listing") if isinstance(item.get("ebay_listing"), dict) else {}
    return {
        "ok": True,
        "schema": "workflow-reconciliation/v1",
        "entity_id": sku,
        "provider_identity": _workflow_provider_identity(),
        "canonical_markers": {
            "stage": {key: offer.get(key) for key in ("provider_effect_id", "offer_id", "stage_content_identity")},
            "publish": {key: listing.get(key) for key in ("provider_effect_id", "listing_id", "offer_id", "published_at")},
        },
        **ledgers,
    }


@app.post("/api/items/{sku}/workflow-goal")
def request_workflow_goal(
    sku: str,
    body: WorkflowGoalBody,
    operator_identity: str = Depends(_require_auth),
) -> Dict[str, Any]:
    """Evaluate an operator goal; dispatch at most one local treatment."""
    from .workflow.listing_migration import authorize_and_request_item_goal
    from .workflow.profiles import get_profile

    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"sku not found: {sku}")
    try:
        goal = get_profile(body.goal_profile_id)
    except KeyError:
        raise HTTPException(status_code=400, detail="unknown workflow goal") from None
    if not goal.identity.startswith("tgw."):
        raise HTTPException(status_code=400, detail="only TGW item goals are accepted")
    migration = _cfg.get("workflow_migration")
    if migration is None and isinstance(_cfg.get("raw"), dict):
        migration = _cfg["raw"].get("workflow_migration")
    migration = migration if isinstance(migration, dict) else {}
    provider_identity = migration.get("ebay_provider_identity", "")
    if not isinstance(provider_identity, str) or not provider_identity.strip():
        raise HTTPException(status_code=503, detail="provider identity is not configured")
    try:
        result, authority_id, authority_created = authorize_and_request_item_goal(
            json_path,
            goal,
            operator_identity=operator_identity,
            surface="http:item-workflow-goal",
            provider_identity=provider_identity,
            scopes=tuple(body.scopes),
            ttl_seconds=body.authority_ttl_seconds,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "ok": True,
        "sku": sku,
        "goal": {"id": goal.identity, "version": goal.version},
        "graph_id": result.graph.graph_id,
        "object_generation": result.graph.object_generation,
        "dispatched": bool(result.dispatched and result.dispatched.enqueued),
        "job_id": result.dispatched.job_id if result.dispatched else "",
        "held_external": list(result.held_external),
        "operator_gates": list(result.operator_gates),
        "authority_id": authority_id,
        "authority_created": authority_created,
    }


@app.get("/api/items/{sku}", dependencies=[AUTH])
def get_item(sku: str) -> Dict[str, Any]:
    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        # Check sku_history — this SKU may have been renamed during migration
        try:
            with psycopg2.connect(_cfg["postgres_dsn"]) as con:
                with con.cursor() as cur:
                    cur.execute(
                        "SELECT sku_new FROM sku_history WHERE sku_old = %s ORDER BY changed_at DESC LIMIT 1",
                        (sku,),
                    )
                    row = cur.fetchone()
        except Exception:
            row = None
        if row and isinstance(row[0], str) and row[0]:
            new_sku = row[0]
            new_path = _cfg["itemdata_root"] / new_sku / f"{new_sku}.json"
            if new_path.exists():
                from fastapi.responses import RedirectResponse

                return RedirectResponse(
                    url=f"/api/items/{new_sku}",
                    status_code=301,
                )
        raise HTTPException(status_code=404, detail=f"sku not found: {sku}")

    item = load_item_doc(json_path)

    # Attach media file lists (images in photo_order order)
    sku_dir = json_path.parent
    item["_images"] = [p.name for p in _ordered_photos(item, sku_dir)]
    videos = [p.name for p in sorted(sku_dir.iterdir()) if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}]
    item["_videos"] = videos

    # Attach recent queue job states for this SKU
    try:
        with psycopg2.connect(_cfg["postgres_dsn"]) as con:
            with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT job_id::text, queue_name, state, attempt_count,
                           created_at, updated_at, finished_at,
                           error_code, error_detail
                      FROM queue_jobs
                     WHERE payload_json->>'sku' = %s
                     ORDER BY created_at DESC
                     LIMIT 50
                    """,
                    (sku,),
                )
                jobs = [dict(r) for r in cur.fetchall()]
                for j in jobs:
                    for k in ("created_at", "updated_at", "finished_at"):
                        if j[k] is not None:
                            j[k] = j[k].isoformat()
        item["_queue_jobs"] = jobs
    except Exception as e:
        log.warning("queue job fetch failed for %s: %s", sku, e)
        item["_queue_jobs"] = []

    return {"ok": True, "item": item}


# ---------------------------------------------------------------------------
# PATCH /api/items/{sku} — update fields
# ---------------------------------------------------------------------------


_OPERATOR_OBJECT_CAPABILITY = object()


@app.patch("/api/items/{sku}")
def patch_item(
    sku: str,
    body: PatchBody,
    request: Request,
    operator_identity: str = Depends(_require_fence_patch_auth),
) -> Dict[str, Any]:
    if "sku" in body.fields:
        raise HTTPException(status_code=400, detail="sku field is immutable")
    if not body.fields:
        raise HTTPException(status_code=400, detail="no fields provided")

    # Only the in-process published-command executor can possess this identity
    # sentinel. HTTP headers/query/body values cannot manufacture it.
    operator_object_write = (
        request.scope.get("_tgw_operator_object_capability")
        is _OPERATOR_OBJECT_CAPABILITY
    )

    # Todo #1464 (Tigwa's field-set-boundary audit, invariant C12/C14): a
    # caller-supplied FULL Set A/Set B envelope bypasses the sanctioned
    # accessor's own diff/provenance logic entirely — _apply_patch's
    # existing envelope branch just shallow-replaces whatever shape it's
    # given, no history computed, no previous-value check. That's fine for
    # ai_identify.py's own legitimate use (it builds the envelope itself via
    # inventory_record.set_inventory_fields() before ever reaching HTTP —
    # the fence call here is just transport for an already-sanctioned
    # write), but nothing stopped ANY other caller with the shared API key
    # from doing the same with an arbitrary, hand-built envelope and a
    # forged/absent history — silently corrupting either set with no
    # accessor ever having seen the change. A bare partial dict (the eBay
    # Draft Editor's own normal save path, todo #1461) is unaffected — that
    # already routes through the real accessor below and keeps working.
    # Gate: only the separately authenticated machine fence may submit an
    # envelope-shaped value here. X-TGW-Caller is attribution only: it is
    # client-controlled and therefore never an authorization signal.
    _is_machine_caller = operator_identity == "machine:item-write-fence"
    workflow_evidence_fields = {
        "ebay_offer",
        "ebay_listing",
        "ebay_submitted",
        "ebay_live",
        "draft_listing_state",
        "status",
    }
    forbidden_evidence = sorted(workflow_evidence_fields.intersection(body.fields))
    if forbidden_evidence:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "workflow_evidence_write_required",
                "fields": forbidden_evidence,
                "reason": (
                    "provider and listing-lifecycle evidence may not be written "
                    "through the generic item PATCH fence"
                ),
            },
        )
    if "draft_listing" in body.fields and not operator_object_write:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "operator_object_command_required",
                "field": "draft_listing",
                "command_endpoint": f"/api/operator/items/{sku}/commands",
                "reason": "listing drafts may be changed only through a published command and object generation",
            },
        )
    if not _is_machine_caller:
        _ia = body.fields.get("item_attributes")
        if isinstance(_ia, dict) and inventory_record.is_envelope(_ia):
            raise HTTPException(
                status_code=422,
                detail=("item_attributes must be a bare field-update dict, not a full Set A envelope, from a non-machine caller — invariant C12/C14, todo #1464"),
            )
        _dl_for_gate = body.fields.get("draft_listing")
        _isp = _dl_for_gate.get("item_specifics") if isinstance(_dl_for_gate, dict) else None
        if isinstance(_isp, dict) and _is_ebay_draft_envelope(_isp):
            raise HTTPException(
                status_code=422,
                detail=("draft_listing.item_specifics must be a bare field-update dict, not a full Set B envelope, from a non-machine caller — invariant C12/C14, todo #1464"),
            )

    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"sku not found: {sku}")

    # Handle location specially — must keep location tree in sync
    location_value: Optional[str] = None
    if "location" in body.fields:
        location_value = body.fields.pop("location")

    # The published operator-object command owns draft lifecycle changes.
    # Generic PATCH, including the machine fence, cannot synthesize this
    # evaluator-visible evidence.

    doc_before = load_item_doc(json_path)

    # Self-resolving guard findings: if this edit fixes the persisted
    # condition (e.g. no_price_set and the patch sets a price), clear the
    # finding in the same write — the operator should never have to clear
    # an error the system can verify is gone. Rejection errors are kept:
    # editing one field does not prove the rejected content was fixed.
    _new_dl_fields = body.fields.get("draft_listing")
    if isinstance(_new_dl_fields, dict) and doc_before.get("pipeline_error"):
        _merged_dl = {**(doc_before.get("draft_listing") or {}), **_new_dl_fields}
        _resolved = draft_sync.resolve_pipeline_error(doc_before["pipeline_error"], _merged_dl, clear_rejections=False)
        if _resolved is None:
            body.fields["pipeline_error"] = None

    # listing_description is a derived cache (AI description + boilerplate +
    # picklist line, built by build_listing_description()) that stage_draft()
    # prefers over the plain description field when pushing to eBay (sync.py
    # ~line 453). If a patch edits draft_listing.description without also
    # supplying listing_description, the cache goes stale and every future
    # eBay push silently re-sends the old text — found live on
    # tgw202605040949058, where 9 ebay_stage jobs "succeeded" while pushing
    # stale AI text over the operator's edits. Regenerate it here so the
    # cache can never outlive the field it's derived from.
    if isinstance(_new_dl_fields, dict) and "description" in _new_dl_fields and "listing_description" not in _new_dl_fields:
        _merged_dl_for_desc = {**(doc_before.get("draft_listing") or {}), **_new_dl_fields}
        _item_for_desc = {**doc_before, "draft_listing": _merged_dl_for_desc}
        _new_dl_fields["listing_description"] = build_listing_description(_item_for_desc, _cfg)

    # PP-CONDITION-ENUM-001 / todo #1562: draft_listing.condition_enum is a
    # known-vocabulary field (10 Inventory API enum strings) — a caller
    # sending anything else (e.g. the raw human-readable "condition" label,
    # which is exactly the corruption path that dead-lettered
    # tgw202605051124483 at ebay_stage) must be REJECTED, not silently
    # shallow-merged in by _apply_patch. Checked before _apply_patch runs so
    # the bad value never reaches disk. Global-vocabulary check only (not
    # narrowed to the item's category's allowed subset) — deliberately
    # conservative: a value that isn't even a real enum is always wrong,
    # while a real-but-category-mismatched enum is a softer case the
    # Draft Editor's dropdown already surfaces for operator review.
    if isinstance(_new_dl_fields, dict) and "condition_enum" in _new_dl_fields:
        _ce = _new_dl_fields.get("condition_enum")
        from .apis.ebay.conditions import is_known_condition_enum

        if _ce and not is_known_condition_enum(_ce):
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=422,
                content={
                    "ok": False,
                    "error": f"condition_enum {_ce!r} is not a valid eBay Inventory API condition enum — rejected, not saved",
                    "field": "condition_enum",
                },
            )

    updated_keys, resulting_generation = _apply_patch(json_path, body.fields)

    # Price edits leave a history trail (session 42): manual/UI price changes
    # previously appended nothing to price_history — Dave's $82.99 and every
    # other hand-set price was invisible in the pricing panel. Any patch that
    # changes draft_listing.price gets an audit event, whoever the caller is.
    try:
        _new_dl = body.fields.get("draft_listing")
        if isinstance(_new_dl, dict) and "price" in _new_dl:
            _old_p = (doc_before.get("draft_listing") or {}).get("price")
            _new_p = _new_dl.get("price")
            if _new_p is not None and str(_new_p) != str(_old_p):
                _caller_id = request.headers.get("X-TGW-Caller", "operator")
                _hist = (doc_before.get("price_history") or []) + [
                    {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "price": float(_new_p),
                        "previous_price": float(_old_p) if _old_p not in (None, "") else None,
                        "stage": None,
                        "label": "price edited",
                        "source": _caller_id,
                    }
                ]
                _apply_patch(json_path, {"price_history": _hist})
    except (TypeError, ValueError) as _exc:
        log.warning("price_history append skipped for %s: %s", sku, _exc)

    if location_value is not None:
        result = locationupdate(_cfg, sku, location_value)
        if result.get("ok"):
            updated_keys.append("location")
        else:
            # invariant C11: a failed location move is a finding, not a log
            # line — otherwise the operator/Flutter UI sees false success
            # ("updated": ["location"]) while the item is silently misfiled.
            log.warning("location tree update failed for %s: %s", sku, result)
            _persist_finding(
                json_path,
                sku,
                "location_update_failed",
                f"locationupdate() failed: {result}",
                "patch_item:location",
            )

    _enqueue_catalog_rebuild(f"http_patch:{sku}")

    return {
        "ok": True,
        "sku": sku,
        "updated": updated_keys,
        "resulting_generation": resulting_generation,
    }


# ---------------------------------------------------------------------------
# POST /api/items/{sku}/append — typed list append (PP-FENCE-001 Layer 3)
# ---------------------------------------------------------------------------

_APPEND_OP_TO_FIELD: Dict[str, Optional[str]] = {
    "vision_result": "vision_results",
    "photo": "photos",
    "price_event": "price_history",
    "history_event": None,  # target determined by data["type"]
}
_HISTORY_SUBTYPES = {"title", "description", "location"}


@app.post("/api/items/{sku}/append", dependencies=[AUTH])
def append_item(sku: str, body: AppendBody) -> Dict[str, Any]:
    if body.op not in _APPEND_OP_TO_FIELD:
        raise HTTPException(
            status_code=400,
            detail=f"unknown op {body.op!r}; valid: {sorted(_APPEND_OP_TO_FIELD)}",
        )
    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"sku not found: {sku}")

    field = _APPEND_OP_TO_FIELD[body.op]
    if field is None:
        subtype = body.data.get("type", "")
        if subtype not in _HISTORY_SUBTYPES:
            raise HTTPException(
                status_code=400,
                detail=f"history_event requires data.type in {_HISTORY_SUBTYPES}",
            )
        field = f"{subtype}_history"

    entry = {**body.data, "appended_at": datetime.now(timezone.utc).isoformat()}
    doc = load_item_doc(json_path)
    lst = doc.get(field)
    if not isinstance(lst, list):
        lst = []
    lst.append(entry)
    _apply_patch(json_path, {field: lst})
    _enqueue_catalog_rebuild(f"append:{sku}:{body.op}")

    # PP-INTAKE-004 Phase 1a: incremental-ID trigger. Only the photo-append
    # path can move the running photo count, so only it needs to check.
    if body.op == "photo":
        _maybe_early_identify(json_path, sku, photo_count=len(lst))

    if body.session_complete:
        _maybe_session_complete_identify(json_path, sku)

    return {"ok": True, "sku": sku, "op": body.op, "field": field}


def _maybe_early_identify(json_path: "Path", sku: str, photo_count: int) -> None:
    """PP-INTAKE-004 Phase 1a: fire ai_identify the first time the running
    photo count crosses ai_identify's own cloud batch size
    (`_MAX_PHOTOS_CLOUD`, currently 6 — Dave, 2026-07-04: threshold = the ID
    call's own batch size, not an arbitrary smaller number), mirroring what
    `bundle_intake.py` does after its stability wait. Only fires once — the
    guard is "hasn't already run" (`ai_identified` not yet true); once it
    has, later photos land quietly and refinement is via the
    `ai_reidentify` flag on session completion instead (see
    `_maybe_session_complete_identify`), never a repeat of this early fire.
    """
    from tgw.workers.ai_identify import _MAX_PHOTOS_CLOUD

    if photo_count < _MAX_PHOTOS_CLOUD:
        return

    doc = load_item_doc(json_path)
    if doc.get("ai_identified"):
        return  # already identified once; early-fire's job is done

    try:
        state_machine.enqueue_job(
            queue_name="ai_identify",
            payload={"sku": sku, "origin": "operator"},
            entity_type="item",
            entity_id=sku,
            dedupe_key=f"ai_identify:{sku}",
            max_attempts=3,
        )
        log.info(
            "early ai_identify enqueued for %s (%d photos >= threshold %d)",
            sku,
            photo_count,
            _MAX_PHOTOS_CLOUD,
        )
    except psycopg2.errors.UniqueViolation:
        pass  # already queued, coalescing
    except Exception as exc:
        log.warning("failed to enqueue early ai_identify for %s: %s", sku, exc)


def _maybe_session_complete_identify(json_path: "Path", sku: str) -> None:
    """PP-INTAKE-004 Phase 1a: session-completion signal.

    - If ai_identify already ran (early-fire path above, or any other
      route) and the session is now complete, set `ai_reidentify: true` —
      the existing re-scan mechanism (`workers/ai_identify.py` already
      checks this flag and re-runs with the full photo set) gives one
      refinement pass now that the full capture is in.
    - If ai_identify never ran at all (fallback — e.g. a quick item that
      only ever gets 2-3 shots, never crossing the early-fire threshold),
      enqueue it directly with whatever smaller photo set exists rather
      than waiting indefinitely for a batch that isn't coming.
    """
    doc = load_item_doc(json_path)
    if doc.get("ai_identified"):
        try:
            _apply_patch(json_path, {"ai_reidentify": True})
            log.info("session-complete: ai_reidentify set for %s", sku)
        except Exception as exc:
            log.warning("failed to set ai_reidentify for %s: %s", sku, exc)
        return

    try:
        state_machine.enqueue_job(
            queue_name="ai_identify",
            payload={"sku": sku, "origin": "operator"},
            entity_type="item",
            entity_id=sku,
            dedupe_key=f"ai_identify:{sku}",
            max_attempts=3,
        )
        log.info("session-complete fallback: ai_identify enqueued for %s", sku)
    except psycopg2.errors.UniqueViolation:
        pass  # already queued, coalescing
    except Exception as exc:
        log.warning("failed to enqueue fallback ai_identify for %s: %s", sku, exc)


# ---------------------------------------------------------------------------
# POST /api/items/{sku}/ebay-write — deep-merge eBay blocks (PP-FENCE-001 Layer 3)
# ---------------------------------------------------------------------------

# Sub-fields workers must NOT clobber when merging eBay blocks
_EBAY_WRITE_PROTECTED: Dict[str, set] = {
    "ebay_offer": {"price_comps", "staged_at"},
    "ebay_listing": {"photo_verify"},
    "ebay_submitted": set(),
    "ebay_live": set(),
}


@app.post("/api/items/{sku}/ebay-write", dependencies=[AUTH])
def ebay_write(sku: str, body: EbayWriteBody) -> Dict[str, Any]:
    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"sku not found: {sku}")

    incoming = {
        "ebay_offer": body.ebay_offer,
        "ebay_listing": body.ebay_listing,
        "ebay_submitted": body.ebay_submitted,
        "ebay_live": body.ebay_live,
    }
    if not any(v is not None for v in incoming.values()):
        raise HTTPException(status_code=400, detail="no eBay blocks provided")

    changed_fields, resulting_generation = _apply_ebay_write(
        json_path,
        sku,
        ebay_offer=body.ebay_offer,
        ebay_listing=body.ebay_listing,
        ebay_submitted=body.ebay_submitted,
        ebay_live=body.ebay_live,
        allow_protected=body.allow_protected,
    )
    _enqueue_catalog_rebuild(f"ebay_write:{sku}")
    return {
        "ok": True,
        "sku": sku,
        "changed_fields": changed_fields,
        "resulting_generation": resulting_generation,
    }


# ---------------------------------------------------------------------------
# Internal write helpers — all item data writes route through these
# (PP-FENCE-001 Session C: UI layer must not call atomic_write_json directly)
# ---------------------------------------------------------------------------


def _enqueue_catalog_rebuild(reason: str) -> None:
    """Coalesced catalog rebuild — 30s delay, single dedupe key."""
    try:
        state_machine.enqueue_catalog_rebuild(reason)
    except Exception:
        pass


def _enqueue_thumbnail_gen(sku: str, reason: str) -> None:
    """Thumbnail regen — same enqueue shape as bundle_intake's initial-intake
    call (PP-CATALOG-INCR-001 CI-3). Only call this when a write actually
    touched image/photo_order — before this packet, thumbnail_gen was ONLY
    ever enqueued once at initial bundle_intake, so a later photo reorder or
    primary-photo change through the fence never refreshed the thumbnail at
    all (a real latent gap, not just an over-triggering one)."""
    try:
        state_machine.enqueue_job(
            queue_name="thumbnail_gen",
            # C10 stamp — this fires from the HTTP fence in response to a
            # write, same class as every other operator-adjacent enqueue in
            # this file (invariant C10, test_operator_origin_sourcescan.py).
            payload={"sku": sku, "origin": "operator"},
            entity_type="item",
            entity_id=sku,
            dedupe_key=f"thumbnail_gen:{sku}",
            max_attempts=3,
        )
    except psycopg2.errors.UniqueViolation:
        pass  # already queued, coalescing
    except Exception:
        log.warning("thumbnail_gen enqueue failed for %s (%s)", sku, reason)


def _persist_finding(json_path: "Path", sku: str, code: str, detail: str, source: str) -> None:
    """Write a C11 guard finding to pipeline_error — a persisted, queryable
    reason the pipeline skipped/failed something, never just a log line.
    Canonical {code, detail, ts, source} shape (see draft_sync.py,
    workers/ebay_stage.py, workers/ebay_publish.py). Best-effort: if the
    persist itself fails, that failure is only a log line — there is nowhere
    lower to record it.

    _skip_catalog_upsert=True on the inner _apply_patch call: this write
    must never be the trigger for its own recursive catalog-upsert-failure
    finding (see _apply_patch's identical parameter) — a pipeline_error
    write being one cycle behind in the SQLite catalog is low-stakes
    compared to an infinite recursion if the upsert is persistently broken.
    """
    try:
        _apply_patch(
            json_path,
            {
                "pipeline_error": {
                    "code": code,
                    "detail": detail,
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "source": source,
                }
            },
            _skip_catalog_upsert=True,
        )
    except Exception:
        log.exception("failed to persist %s finding for %s", code, sku)


def _apply_patch(
    json_path: "Path",
    fields: Dict[str, Any],
    _skip_catalog_upsert: bool = False,
) -> Tuple[List[str], str]:
    """Core item patch: deep-merge dict fields, write atomically, schedule rebuild.

    Fields with value None are deleted from the document.
    Returns the keys written or deleted and the exact generation of the
    document passed to the committing atomic write.  Callers that drive a
    workflow continuation must use this generation instead of reopening the
    item path after the write, because an independent writer may legitimately
    commit between the fence write and a later pathname read.

    _skip_catalog_upsert: True only for _persist_finding's own internal
    {"pipeline_error": ...}-only recursive call — skips both the SQLite
    upsert (avoiding infinite recursion if the upsert is persistently
    broken) AND the implicit catalog_verified invalidation below (an
    internal side-record write must never have field-presence side effects
    on the caller's actual edit).
    """
    fields = dict(fields)
    # Captured before the dmk-pop loop below removes draft_listing/
    # item_attributes from `fields` — those two keys are still real changed
    # keys for _changed_keys' purposes (audit-publish, thumbnail trigger)
    # even though they're routed through the accessor path below instead of
    # the plain doc.update(fields) merge. Code-review finding, 2026-07-18:
    # using post-pop fields.keys() here silently dropped draft_listing/
    # item_attributes edits from both the PATCH response's "updated" list
    # and CI-1's mutation-audit stream.
    _original_field_keys = list(fields.keys())
    doc = load_item_doc(json_path)
    _before_doc = dict(doc)
    for dmk in ("draft_listing", "item_attributes"):
        if dmk in fields and isinstance(fields[dmk], dict):
            existing = doc.get(dmk) or {}
            incoming = fields.pop(dmk)
            # todo #1418 / invariant C12: item_attributes is now a self-describing
            # Set A envelope ({_set, version, updated_at, fields}), not a bare
            # dict. A caller that already sends a full envelope (the sanctioned
            # accessor's output, e.g. tgw.inventory_record.set_inventory_fields)
            # is handled by the plain shallow-merge below — every envelope key
            # gets overwritten wholesale, which is a correct full replace. A
            # caller that still sends a bare partial dict of field updates (a
            # legacy/UI path not yet migrated onto the accessor — see #1416)
            # must NOT have those keys merged onto the envelope's top level
            # (that would silently corrupt _set/version/fields with sibling
            # scalar keys) — route it through the accessor instead so the
            # envelope shape is always preserved.
            if dmk == "item_attributes" and not inventory_record.is_envelope(incoming):
                patch = inventory_record.set_inventory_fields(doc, incoming, source="http_patch", applied_by="operator")
                doc["item_attributes"] = patch["item_attributes"]
                doc["item_attributes_history"] = patch["item_attributes_history"]
            elif dmk == "draft_listing":
                # todo #1416 point 3: the eBay Draft Editor's aspects form
                # (saveEbayDraft()) now sends its edits nested inside
                # draft_listing.item_specifics, matching every other Draft
                # Editor field (title, price, condition_enum, ...). Same
                # envelope-corruption risk as item_attributes above applies
                # here: a bare partial {name: value} dict of aspect edits
                # must NOT be shallow-merged directly onto item_specifics's
                # envelope (that would clobber _set/version/fields with
                # sibling scalar keys) — route it through the sanctioned Set
                # B accessor (tgw.ebay.draft_specifics) instead, same
                # discipline as item_attributes's accessor routing above.
                incoming_specifics = incoming.pop("item_specifics", None) if isinstance(incoming, dict) else None
                existing.update(incoming)
                doc[dmk] = existing
                if isinstance(incoming_specifics, dict) and not _is_ebay_draft_envelope(incoming_specifics):
                    sp_patch = set_ebay_aspects(doc, incoming_specifics, source="http_patch", applied_by="operator")
                    existing["item_specifics"] = sp_patch["item_specifics"]
                    existing["item_specifics_history"] = sp_patch["item_specifics_history"]
                elif incoming_specifics is not None:
                    # Already a full envelope (accessor output moving onward, e.g.
                    # accept_proposals) — plain replace, no re-diffing needed.
                    existing["item_specifics"] = incoming_specifics
                doc[dmk] = existing

                # Padlock auto-sync (Dave, 2026-07-18): every eBay-draft save
                # pushes its aspects into item_attributes for any key NOT
                # explicitly locked — replaces the old "visit a separate
                # diff panel and check off every field" two-step (todo
                # #1417) as the default path; #1417's diff/apply endpoints
                # stay for anything not covered by this sync.
                _draft_fields: Dict[str, Any] = dict(get_ebay_aspects(doc))
                if _draft_fields:
                    _ia_sync = inventory_record.sync_from_draft(doc, _draft_fields, source="draft_sync", applied_by="operator")
                    doc["item_attributes"] = _ia_sync["item_attributes"]
                    doc["item_attributes_history"] = _ia_sync["item_attributes_history"]

                # Same padlock idea, applied to the "base data" fields that
                # live at the TOP LEVEL of the item, not inside item_attributes
                # (title, description — Dave, 2026-07-18: "this worked for
                # aspects/item specifics but not for title or any of the
                # other base data"). These are what the page header and
                # main fields panel actually display, so a fix that only
                # touched item_attributes.fields never showed up where the
                # operator was looking. Locked via the same locked_keys list
                # (key names "title"/"description"), so one shared lock
                # vocabulary covers both aspect-style and top-level facts.
                for _base_key, _draft_val in (
                    ("title", existing.get("title")),
                    ("description", existing.get("description")),
                ):
                    if _draft_val and not inventory_record.is_locked(doc, _base_key):
                        doc[_base_key] = _draft_val
            else:
                existing.update(incoming)
                doc[dmk] = existing
    to_delete = [k for k, v in fields.items() if v is None]
    for k in to_delete:
        doc.pop(k, None)
        fields.pop(k)
    doc.update(fields)
    # Code-review fix, 2026-07-19: skip the implicit catalog_verified
    # invalidation for internal side-record writes (_persist_finding's own
    # {"pipeline_error": ...}-only recursive call). Without this guard, any
    # ordinary field patch that happened to trigger a finding-persist (e.g.
    # a failed sqlite upsert) would have its own catalog_verified value
    # silently clobbered moments later by the unrelated internal write —
    # exactly the "correction takes effect but a follow-up write silently
    # reverts it" failure class invariant C14 exists to prevent, just
    # self-inflicted by this function instead of a separate caller.
    if not _skip_catalog_upsert and "catalog_verified" not in fields:
        doc.pop("catalog_verified", None)

    # todo #1522 / invariant C14: an operator's direct edit (including a
    # clear) of a top-level padlock-synced base field (title/description)
    # must keep draft_listing's own copy of that field in agreement,
    # otherwise the "Padlock auto-sync" block above silently resurrects
    # the pre-edit value from a now-stale draft_listing on the very next
    # unrelated draft_listing save (e.g. a price edit) — the base field
    # never diverges from the draft in the first place, so there is
    # nothing stale left for the auto-sync to overwrite it with. This
    # mirrors base -> draft; the auto-sync block above still governs the
    # opposite draft -> base direction and still honors the lock.
    if isinstance(doc.get("draft_listing"), dict):
        for _base_key in ("title", "description"):
            if _base_key in fields:
                doc["draft_listing"][_base_key] = fields[_base_key]
    atomic_write_json(json_path, doc, pretty=_cfg.get("pretty", True), archive_root=_cfg.get("archive_root"))
    resulting_generation = item_generation(doc)
    _sku_for_mutation = doc.get("sku") or json_path.stem
    # Atomic per-item SQLite catalog upsert (PP-CATALOG-INCR-001 CI-2) — keeps
    # the inventory webui's data source live-accurate without waiting for the
    # periodic full rebuild. Best-effort: a catalog-projection hiccup must
    # never fail a write that already succeeded against the source of truth.
    # Code-review finding, 2026-07-18: a discard_revision-specific C11 guard
    # was deleted on the premise this upsert made it redundant, but the
    # upsert's own failure path was log-only — the invariant went
    # unenforced. Fixed at the root here instead of restoring the
    # endpoint-specific guard: any _apply_patch caller's upsert failure now
    # persists a C11 finding (unless _skip_catalog_upsert, which also skips
    # the finding-persist recursion guard's own inner call).
    if not _skip_catalog_upsert:
        try:
            from .sqlite_catalog import upsert_catalog_row

            upsert_catalog_row(_cfg, doc)
        except Exception as _uc_exc:
            log.warning("sqlite catalog upsert failed for %s: %s", _sku_for_mutation, _uc_exc)
            _persist_finding(
                json_path,
                _sku_for_mutation,
                "sqlite_catalog_upsert_failed",
                f"SQLite catalog upsert failed after write: {_uc_exc}",
                "apply_patch",
            )
    _changed_keys = _original_field_keys
    # Publish to audit stream (PP-AIOPS-001 Phase 1 / PP-CATALOG-INCR-001 CI-1) —
    # fire-and-forget. This is the real fence choke point essentially all write
    # traffic (worker patches, bulk edits) goes through; items.py's _write_field
    # already fed the stream for the CLI-only path, this closes the gap for the
    # HTTP path, which is the one that matters for the incremental catalog design.
    try:
        from .apis.nats_client import publish_mutation

        for _ck in _changed_keys:
            publish_mutation(
                sku=_sku_for_mutation,
                field=_ck,
                old_value=_before_doc.get(_ck),
                new_value=doc.get(_ck),
                source="http_patch",
            )
    except Exception:
        pass
    # Thumbnail regen (PP-CATALOG-INCR-001 CI-3) — only on the fields that
    # actually affect what the thumbnail shows, not on every write.
    if "image" in _changed_keys or "photo_order" in _changed_keys:
        _enqueue_thumbnail_gen(_sku_for_mutation, reason=f"http_patch:{','.join(_changed_keys)}")
    return _changed_keys, resulting_generation


def _apply_ebay_write(
    json_path: "Path",
    sku: str,
    *,
    ebay_offer: Optional[Dict[str, Any]] = None,
    ebay_listing: Optional[Dict[str, Any]] = None,
    ebay_submitted: Optional[Dict[str, Any]] = None,
    ebay_live: Optional[Dict[str, Any]] = None,
    allow_protected: Optional[List[str]] = None,
) -> Tuple[List[str], str]:
    """eBay block deep-merge with field protection — same logic as POST /ebay-write.

    Protected sub-fields (price_comps, staged_at, photo_verify) are restored
    to their existing value by default — a generic resync (e.g. ebay_sync
    re-saving its own stale snapshot of ebay_offer/ebay_listing) must never
    clobber a fresher value it doesn't know about (#1189). The one or two
    workers that actually OWN a protected field pass its name via
    allow_protected to intentionally refresh/clear it.
    """
    allow_protected_set = set(allow_protected or ())
    incoming = {
        "ebay_offer": ebay_offer,
        "ebay_listing": ebay_listing,
        "ebay_submitted": ebay_submitted,
        "ebay_live": ebay_live,
    }
    doc = load_item_doc(json_path)
    _before_doc = dict(doc)
    changed: List[str] = []
    for block_key, incoming_block in incoming.items():
        if incoming_block is None:
            continue
        protected = _EBAY_WRITE_PROTECTED.get(block_key, set())
        existing = doc.get(block_key) or {}
        if not isinstance(existing, dict):
            existing = {}
        if block_key == "ebay_offer" and "price" in incoming_block:
            stored_price = existing.get("price")
            incoming_price = incoming_block.get("price")
            if stored_price and incoming_price and str(stored_price) != str(incoming_price):
                log.warning(
                    "ebay_write price divergence for %s: stored=%s incoming=%s",
                    sku,
                    stored_price,
                    incoming_price,
                )
        merged = {**existing, **incoming_block}
        for pf in protected:
            if pf in existing and pf not in allow_protected_set:
                merged[pf] = existing[pf]
        doc[block_key] = merged
        changed.append(block_key)
    atomic_write_json(json_path, doc, pretty=_cfg.get("pretty", True), archive_root=_cfg.get("archive_root"))
    resulting_generation = item_generation(doc)
    # SQLite catalog upsert — see _apply_patch's identical block (PP-CATALOG-INCR-001 CI-2/C11 fix).
    try:
        from .sqlite_catalog import upsert_catalog_row

        upsert_catalog_row(_cfg, doc)
    except Exception as _uc_exc:
        log.warning("sqlite catalog upsert failed for %s: %s", sku, _uc_exc)
        _persist_finding(
            json_path,
            sku,
            "sqlite_catalog_upsert_failed",
            f"SQLite catalog upsert failed after write: {_uc_exc}",
            "apply_ebay_write",
        )
    # Publish to audit stream — see _apply_patch's identical block (PP-CATALOG-INCR-001 CI-1).
    try:
        from .apis.nats_client import publish_mutation

        for _ck in changed:
            publish_mutation(
                sku=sku,
                field=_ck,
                old_value=_before_doc.get(_ck),
                new_value=doc.get(_ck),
                source="http_ebay_write",
            )
    except Exception:
        pass
    return changed, resulting_generation


# ---------------------------------------------------------------------------
# POST /api/items — item creation (PP-FENCE-001 Layer 3)
# ---------------------------------------------------------------------------

_SKU_RE = re.compile(r"^tgw\d{17,20}$")


@app.post("/api/items", dependencies=[AUTH])
def create_item_endpoint(body: CreateItemBody) -> Dict[str, Any]:
    if not _SKU_RE.match(body.sku):
        raise HTTPException(
            status_code=400,
            detail=f"invalid sku format {body.sku!r}; must match tgwYYYYMMDDHHMMSSmmm",
        )
    try:
        json_path = create_item(_cfg, body.sku, body.data)
    except FileExistsError:
        raise HTTPException(status_code=409, detail=f"sku already exists: {body.sku}")

    # Atomic per-item SQLite catalog upsert (PP-CATALOG-INCR-001 CI-4,
    # 2026-07-18) — a brand-new item needs its first catalog row immediately,
    # not after the hourly reconciliation timer; items.create_item() has no
    # publish_mutation/upsert hook of its own (pre-existing gap, out of scope
    # here), so this endpoint does it directly.
    try:
        from .sqlite_catalog import upsert_catalog_row

        upsert_catalog_row(_cfg, load_item_doc(json_path))
    except Exception as _uc_exc:
        log.warning("sqlite catalog upsert failed for new item %s: %s", body.sku, _uc_exc)

    return {"ok": True, "sku": body.sku, "path": str(json_path)}


# ---------------------------------------------------------------------------
# POST /api/bulk/preview + /api/bulk/apply — bulk field edit (PP-BULKEDIT-001)
# ---------------------------------------------------------------------------


def _bulk_selectors(body: BulkBody) -> Dict[str, Any]:
    sel: Dict[str, Any] = {}
    if body.skus:
        sel["skus"] = body.skus
    if body.location:
        sel["location"] = body.location
    if body.status:
        sel["status"] = body.status
    if body.search:
        sel["search"] = body.search
    return sel


@app.post("/api/bulk/preview", dependencies=[AUTH])
def bulk_preview(body: BulkBody) -> Dict[str, Any]:
    """Dry-run: return matched items with current vs. proposed value. No writes."""
    from .items import bulk_edit

    sel = _bulk_selectors(body)
    if not sel:
        raise HTTPException(status_code=400, detail="no selector — give skus or a filter")
    return bulk_edit(_cfg, sel, body.field, body.value, apply=False, limit=body.limit)


@app.post("/api/bulk/apply", dependencies=[AUTH])
def bulk_apply(body: BulkBody) -> Dict[str, Any]:
    """Apply the bulk edit through the item fence, then enqueue a catalog rebuild."""
    from .items import bulk_edit

    sel = _bulk_selectors(body)
    if not sel:
        raise HTTPException(status_code=400, detail="no selector — give skus or a filter")
    result = bulk_edit(_cfg, sel, body.field, body.value, apply=True, limit=body.limit)
    # Gate on count (writes happened), not ok — partial success still mutated
    # item JSONs and must refresh the catalog.
    if result.get("count"):
        try:
            state_machine.enqueue_catalog_rebuild("http_bulk")
        except Exception:
            pass
    return result


# ---------------------------------------------------------------------------
# POST /api/bulk/action — bulk pipeline actions from Flutter browse selection
# ---------------------------------------------------------------------------

_BULK_PIPELINE_ACTIONS = {"ai_identify"}
_BULK_VALID_ACTIONS = _BULK_PIPELINE_ACTIONS | {"set_ready", "mark_sold", "delete", "approve", "archive"}


@app.post("/api/bulk/action")
def bulk_action(
    body: BulkActionBody,
    operator_identity: str = Depends(_require_auth),
) -> Dict[str, Any]:
    """Fan out an action across a list of SKUs.

    Listing-workflow actions are intentionally absent. Publication and
    restaging are issued only by the current operator object's ``list-item``
    and ``update-item`` commands after workflow evaluation.
    """
    if body.action not in _BULK_VALID_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown bulk action {body.action!r}; valid: {sorted(_BULK_VALID_ACTIONS)}",
        )
    if not body.skus:
        raise HTTPException(status_code=400, detail="no skus provided")

    if body.action == "set_ready":
        from .ready import set_ready

        return set_ready(_cfg, body.skus)

    if body.action == "approve":
        done: List[str] = []
        errors: List[str] = []
        for sku in body.skus:
            json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
            if not json_path.exists():
                errors.append(f"{sku}: not found")
                continue
            try:
                _apply_patch(json_path, {"status": "Ready"})
                done.append(sku)
            except Exception as exc:
                errors.append(f"{sku}: {exc}")
                continue
        _enqueue_catalog_rebuild(f"bulk_{body.action}")
        return {
            "ok": bool(done) and not errors,
            "count": len(done),
            "done": done,
            "errors": errors,
        }

    if body.action in ("mark_sold", "delete", "archive"):
        status_map = {"mark_sold": "Sold", "delete": "deleted", "archive": "archived"}
        new_status = status_map[body.action]
        done: List[str] = []
        errors: List[str] = []
        for sku in body.skus:
            json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
            if not json_path.exists():
                errors.append(f"{sku}: not found")
                continue
            try:
                if body.action == "mark_sold":
                    # Same quantity-decrement rule as ebay/pull.py mark_item_sold:
                    # a bulk "mark sold" click accounts for one unit sold, not the
                    # whole quantity — jumping straight to status=Sold hides
                    # remaining unsold units on multi-qty items.
                    #
                    # Single read-decide-write (not a separate lookup read
                    # followed by _apply_patch's own independent re-read):
                    # two reads open a TOCTOU window where a concurrent real
                    # sale (ebay/pull.py mark_item_sold, via an eBay webhook)
                    # landing between them gets silently clobbered by a
                    # `remaining` value computed from the stale first read.
                    doc = load_item_doc(json_path)
                    draft = dict(doc.get("draft_listing") or {})
                    current_qty = int(draft.get("quantity") or 1)
                    remaining = max(0, current_qty - 1)
                    draft["quantity"] = remaining
                    doc["draft_listing"] = draft
                    if remaining == 0:
                        doc["status"] = new_status
                    atomic_write_json(json_path, doc, pretty=_cfg.get("pretty", True), archive_root=_cfg.get("archive_root"))
                else:
                    fields = {"status": new_status}
                    if body.action == "delete":
                        fields["deleted_at"] = datetime.now(timezone.utc).isoformat()
                    _apply_patch(json_path, fields)
                done.append(sku)
            except Exception as exc:
                errors.append(f"{sku}: {exc}")
        _enqueue_catalog_rebuild(f"bulk_{body.action}")
        return {"ok": not errors, "count": len(done), "done": done, "errors": errors}

    # Pipeline actions — enqueue a job per SKU
    queued: List[str] = []
    skipped: List[str] = []
    errors: List[str] = []
    for sku in body.skus:
        json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
        if not json_path.exists():
            errors.append(f"{sku}: not found")
            continue
        try:
            result = item_action(
                sku,
                ActionBody(action=body.action),
                operator_identity=operator_identity,
            )
            if result.get("ok"):
                queued.append(sku)
            else:
                errors.append(f"{sku}: {result.get('detail') or result.get('status') or 'held'}")
        except Exception as exc:
            err = str(exc)
            if "unique" in err.lower() or "duplicate" in err.lower():
                skipped.append(sku)
            else:
                errors.append(f"{sku}: {err}")
    return {
        "ok": not errors,
        "count": len(queued),
        "queued": queued,
        "skipped": skipped,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# GET /api/items/{sku}/thumbnail — serve thumbnail
# ---------------------------------------------------------------------------


@app.get("/api/items/{sku}/thumbnail", dependencies=[AUTH])
def get_thumbnail(sku: str):
    thumb = _cfg["thumbnail_root"] / f"{sku}.jpg"
    if not thumb.exists():
        raise HTTPException(status_code=404, detail=f"no thumbnail for {sku}")
    return FileResponse(str(thumb), media_type="image/jpeg")


# ---------------------------------------------------------------------------
# POST /api/items/{sku}/action — enqueue pipeline stage
# ---------------------------------------------------------------------------


def _normalize_draft_condition_for_provider(json_path: "Path") -> Dict[str, str] | None:
    """Persist the nearest honest category-valid condition before dispatch.

    eBay accepts an inventory-item PUT containing a globally valid condition
    enum, but can reject the later publish because that enum is not valid for
    the offer's category.  Authorization and staged-content identities must be
    created *after* this deterministic downgrade, never around content the
    provider will reject or silently reinterpret.
    """
    from .apis.ebay.conditions import (
        allowed_conditions_for_category,
        best_condition_for_enum,
    )

    doc = load_item_doc(json_path)
    draft = doc.get("draft_listing")
    if not isinstance(draft, dict):
        return None
    category_id = str(draft.get("category_id") or "").strip()
    current = str(draft.get("condition_enum") or "").strip()
    if not category_id or not current:
        return None
    allowed = allowed_conditions_for_category(_cfg, category_id)
    if not allowed or any(item.get("condition_enum") == current for item in allowed):
        return None
    remap = best_condition_for_enum(_cfg, category_id, current)
    if remap is None:
        raise HTTPException(
            status_code=409,
            detail=(f"condition {current!r} is not valid for eBay category {category_id}; select a category-valid condition"),
        )
    updated = dict(draft)
    updated.update(
        {
            "condition_id": remap["condition_id"],
            "condition_label": remap["condition_label"],
            "condition_enum": remap["condition_enum"],
        }
    )
    _apply_patch(json_path, {"draft_listing": updated})
    return remap


@app.post("/api/items/{sku}/action")
def item_action(
    sku: str,
    body: ActionBody,
    operator_identity: str = Depends(_require_auth),
) -> Dict[str, Any]:
    action = body.action
    if action not in PIPELINE_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown action {action!r}; valid: {sorted(PIPELINE_ACTIONS)}",
        )

    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists() and action != "catalog_rebuild":
        raise HTTPException(status_code=404, detail=f"sku not found: {sku}")

    try:
        if action == "approve":
            # Human approval of AI draft — set status=Ready, no job enqueued
            _apply_patch(json_path, {"status": "Ready"})
            _enqueue_catalog_rebuild(f"approve:{sku}")
            return {"ok": True, "sku": sku, "action": "approve", "status": "Ready"}
        elif action == "catalog_rebuild":
            state_machine.enqueue_catalog_rebuild(f"manual:{sku}", delay_seconds=5.0)
        elif action == "archive":
            _apply_patch(json_path, {"status": "archived"})
            _enqueue_catalog_rebuild(f"archive:{sku}")
            return {"ok": True, "sku": sku, "action": "archive", "status": "archived"}

        elif action == "migrate_unblock":
            import json as _json
            from pathlib import Path as _Path

            was_blocked_val = load_item_doc(json_path).get("sku_migrate_blocked")
            _apply_patch(json_path, {"sku_migrate_blocked": None, "sku_migrate_skip": None})
            # Remove from blocked registry
            registry_path = _Path("/opt/TGW/var/migrate-blocked.json")
            try:
                if registry_path.exists():
                    registry = _json.loads(registry_path.read_text(encoding="utf-8"))
                    registry.pop(sku, None)
                    tmp = registry_path.with_suffix(".tmp")
                    tmp.write_text(_json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")
                    tmp.replace(registry_path)
            except Exception:
                pass
            return {"ok": True, "sku": sku, "action": "migrate_unblock", "was_blocked": was_blocked_val is not None}

        elif action == "review_mark_ready":
            from datetime import datetime as _dt
            from datetime import timezone as _tz

            doc = load_item_doc(json_path)
            rb = doc.get("review_block") or {}
            if not rb:
                return {"ok": True, "sku": sku, "action": "review_mark_ready", "note": "no review_block present"}
            rb["ready"] = True
            rb["retested_at"] = _dt.now(_tz.utc).isoformat()
            _apply_patch(json_path, {"review_block": rb})
            return {"ok": True, "sku": sku, "action": "review_mark_ready", "stage": rb.get("stage"), "reason_code": rb.get("reason_code")}

        elif action == "accept_proposals":
            doc = load_item_doc(json_path)
            rev = doc.get("revision_draft") or {}
            delta = rev.get("delta") or {}
            if not delta:
                return {"ok": False, "detail": "no revision_draft delta to accept"}
            # todo #1416 point 4: accepted proposals target draft_listing.
            # item_specifics (Set B), via the sanctioned tgw.ebay.draft_specifics
            # accessor — NOT item_attributes (Set A). This is the action's own
            # button banner's stated contract ("copies proposals into your
            # draft — review then Update Listing to push to eBay"): the
            # accepted values must land somewhere the existing "Update
            # Listing"/ebay_stage push path actually reads from
            # (sync.py:_build_offer_bodies reads ONLY item_specifics), which
            # item_attributes never was. Prior to this fix the accepted delta
            # was silently written to the wrong set and never reached eBay —
            # confirmed live during this packet's investigation (two
            # ebay_stage jobs "succeeded" while pushing unchanged content).
            #
            # NOT routed through revision.cmd_revise_apply (point 6): that
            # function requires an existing Inventory API offer_id and pushes
            # live immediately (fresh GET -> compose -> PUT, no staging), which
            # would break accept_proposals' own two-step contract ("accept"
            # stages, a separate "Update Listing" click pushes) and would hard-
            # fail for any item with a pending revision_draft but no live offer
            # yet (a normal pre-publish state). Both mechanisms remain
            # legitimate, distinct consumers of the same revision_draft.delta
            # shape for two different UI flows; what's fixed here is that
            # accept_proposals now writes to the SET that the staged-push path
            # actually reads, closing the boundary bug without collapsing two
            # intentionally different flows into one.
            dl_touched = False
            dl_patch: Dict[str, Any] = {}
            if "item_specifics" in delta and isinstance(delta["item_specifics"], dict):
                dl_patch = set_ebay_aspects(doc, delta["item_specifics"], source="accept_proposals", applied_by="operator")
                dl_touched = True
            dl2 = doc.get("draft_listing") or {}
            if dl_touched:
                dl2 = dict(dl2)
                dl2["item_specifics"] = dl_patch["item_specifics"]
                dl2["item_specifics_history"] = dl_patch["item_specifics_history"]
            if "title" in delta:
                dl2["title"] = delta["title"]
                dl_touched = True
            if "description" in delta:
                # Runner-review fix (todo #1416): accept_proposals is a
                # separate endpoint (item_action) from patch_item(), which
                # is the only place the #1415 listing_description
                # regeneration lived. Without this, an accepted description
                # proposal reintroduces the exact stale-push bug #1415 fixed
                # (listing_description never regenerated, eBay pushes keep
                # sending old text) through this second code path.
                dl2["description"] = delta["description"]
                _item_for_desc = {**doc, "draft_listing": dl2}
                dl2["listing_description"] = build_listing_description(_item_for_desc, _cfg)
                dl_touched = True
            proposal_fields: Dict[str, Any] = {"revision_draft": None}
            if dl_touched:
                proposal_fields["draft_listing"] = dl2
            _apply_patch(json_path, proposal_fields)
            _enqueue_catalog_rebuild(f"accept_proposals:{sku}")
            return {"ok": True, "sku": sku, "action": "accept_proposals"}

        elif action == "dismiss_proposals":
            if "revision_draft" not in load_item_doc(json_path):
                return {"ok": True, "sku": sku, "note": "no revision_draft present"}
            _apply_patch(json_path, {"revision_draft": None})
            return {"ok": True, "sku": sku, "action": "dismiss_proposals"}

        elif action == "resync_photos":
            # On-demand photo re-verify + re-push (Dave, 2026-07-17): photos
            # don't need pushing on every "Update Listing" click, so this is
            # a separate action rather than baked into a listing update — see
            # tgw.ebay.repush's module docstring.
            #
            # Live bug found same day (tgw202605041013227): photo_order can
            # have more photos than ebay_photos (the EPS-hosted set) if
            # ebay_upload only ran once and never re-ran after photos were
            # added later. repush only re-PUTs already-hosted URLs — the
            # Inventory API can't accept a raw local path — so resync alone
            # can never fix this; the missing photos need ebay_upload first.
            # Detect the gap and queue that instead of silently no-op'ing.
            #
            # Second live bug, same day (tgw202605051849352): photo_order
            # itself can be empty/stale while 26 real files sit on disk —
            # it's a display-order cache, not the source of truth. Using
            # len(photo_order) here compared 0 local vs 1 hosted and found
            # "no gap" on an item that was actually missing 25 photos.
            # ordered_photos() is what ebay_upload.py itself trusts (falls
            # back to a directory scan when photo_order is empty) — use
            # the same source so this check can't disagree with upload.
            from tgw.assets import ordered_photos
            from tgw.workflow.item_snapshot import _photo_sync_state

            doc = load_item_doc(json_path)
            local_photo_count = len(ordered_photos(doc, json_path.parent))
            hosted_count = len([entry for entry in (doc.get("ebay_photos") or []) if isinstance(entry, dict) and entry.get("url")])
            photo_ready, photo_reason, photo_fingerprint = _photo_sync_state(
                doc,
                json_path.parent,
            )
            if not photo_ready:
                from .workflow.listing_migration import authorize_and_request_item_goal
                from .workflow.profiles import TGW_EBAY_STAGED

                migration = _cfg.get("workflow_migration")
                if migration is None and isinstance(_cfg.get("raw"), dict):
                    migration = _cfg["raw"].get("workflow_migration")
                migration = migration if isinstance(migration, dict) else {}
                result, authority_id, authority_created = authorize_and_request_item_goal(
                    json_path,
                    TGW_EBAY_STAGED,
                    operator_identity=operator_identity,
                    surface="http:item-action:resync-photos",
                    provider_identity=migration.get("ebay_provider_identity", ""),
                    scopes=("upload", "stage"),
                )
                dispatched = result.dispatched
                return {
                    "ok": dispatched is not None,
                    "sku": sku,
                    "action": "resync_photos",
                    "status": "workflow_dispatched" if dispatched is not None else "held",
                    "upload_queued": dispatched is not None and dispatched.queue_name == "ebay_upload",
                    "job_id": dispatched.job_id if dispatched is not None else "",
                    "graph_id": result.graph.graph_id,
                    "object_generation": result.graph.object_generation,
                    "held_external": list(result.held_external),
                    "operator_gates": list(result.operator_gates),
                    "authority_id": authority_id,
                    "authority_created": authority_created,
                    "local_photo_count": local_photo_count,
                    "hosted_count": hosted_count,
                    "photo_fingerprint": photo_fingerprint,
                    "detail": (
                        f"{photo_reason} — the workflow evaluated the current item "
                        + ("and dispatched its next treatment" if dispatched is not None else "and held dispatch")
                    ),
                }

            # A true fingerprint means the current local set, hosted set, and
            # published image order already agree.  Do not create a second
            # provider-effect path merely to repeat the same PUT.
            return {
                "ok": True,
                "sku": sku,
                "action": "resync_photos",
                "status": "already_satisfied",
                "submitted_count": hosted_count,
                "confirmed_count": hosted_count,
                "photo_fingerprint": photo_fingerprint,
            }

        elif action == "sync_from_ebay":
            try:
                job_id = state_machine.enqueue_job(
                    queue_name="ebay_sync",
                    payload={"sku": sku, "reason": "manual", "origin": "operator"},
                    entity_type="item",
                    entity_id=sku,
                    max_attempts=2,
                    dedupe_key=f"ebay_sync:sku:{sku}",
                )
            except psycopg2.errors.UniqueViolation:
                job_id = None
            return {"ok": True, "sku": sku, "action": "sync_from_ebay", "job_id": job_id}

        elif action == "reset_draft_from_live":
            # PP-ACTIONCONSOLE-001 / broker B1a (M4): operator's "live is
            # better, start over" — re-pin the draft to the ebay_live mirror
            # via the shared lifecycle primitive. C11-safe: guard findings
            # are cleared only if the pinned draft resolves them.
            doc = load_item_doc(json_path)
            try:
                _pin_fields = draft_sync.pin_draft_to_live(doc)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            _apply_patch(json_path, _pin_fields)
            return {"ok": True, "sku": sku, "action": "reset_draft_from_live"}

        elif action == "set_ready":
            from .ready import set_ready as _set_ready

            result = _set_ready(_cfg, [sku])
            if result.get("marked"):
                # Proactively start photo upload so it's ready when the dole cycle stages the item.
                try:
                    state_machine.enqueue_job(
                        queue_name="ebay_upload",
                        payload={"sku": sku, "origin": "operator"},
                        entity_type="item",
                        entity_id=sku,
                        dedupe_key=f"ebay_upload:{sku}",
                        max_attempts=5,
                    )
                except Exception:
                    pass
                return {
                    "ok": True,
                    "sku": sku,
                    "action": "set_ready",
                    "note": "approved into the ready pool — NOTE: the dole worker is not installed yet, nothing publishes from the pool (todo #1113); use 'List on eBay' to publish this item now",
                }
            err = (result.get("errors") or ["unknown error"])[0]
            return {"ok": False, "sku": sku, "detail": err}

        elif action == "unset_ready":
            from .ready import unset_ready as _unset_ready

            result = _unset_ready(_cfg, [sku])
            if result.get("unmarked"):
                return {"ok": True, "sku": sku, "action": "unset_ready", "note": "removed from ready pool"}
            err = (result.get("errors") or ["unknown error"])[0]
            return {"ok": False, "sku": sku, "detail": err}

        elif action == "ai_identify":
            migration = _cfg.get("workflow_migration")
            if migration is None and isinstance(_cfg.get("raw"), dict):
                migration = _cfg["raw"].get("workflow_migration")
            migration = migration if isinstance(migration, dict) else {}
            mode = migration.get("item_ai_identify_fanout", "workflow")
            if mode != "workflow":
                raise HTTPException(
                    status_code=503,
                    detail=f"invalid item_ai_identify_fanout mode {mode!r}",
                )
            if mode == "workflow":
                from .workflow.listing_migration import request_item_goal
                from .workflow.profiles import TGW_EBAY_DRAFTED

                # Durable pending intent: the evaluator and queued treatment
                # must bind the exact generation visible to a fresh worker.
                _apply_patch(
                    json_path,
                    {
                        "ai_reidentify": True,
                        "ai_redraft_requested": True,
                    },
                )
                result = request_item_goal(
                    json_path,
                    TGW_EBAY_DRAFTED,
                    origin="operator",
                    operator_identity=operator_identity,
                    operator_surface="http:item-action:ai-identify",
                )
                if result.dispatched is None:
                    return {
                        "ok": False,
                        "sku": sku,
                        "action": action,
                        "status": "held",
                        "job_id": "",
                        "held_external": list(result.held_external),
                        "operator_gates": list(result.operator_gates),
                        "ownership_conflicts": list(result.graph.ownership_conflicts),
                        "reconciliation_gates": list(result.graph.reconciliation_gates),
                    }
                job_id = result.dispatched.job_id

        else:
            job_id = state_machine.enqueue_job(
                queue_name=action,
                payload={"sku": sku, "origin": "operator"},
                entity_type="item",
                entity_id=sku,
                dedupe_key=f"{action}:{sku}",
                max_attempts=5,
            )
    except HTTPException:
        raise
    except Exception as e:
        err = str(e)
        if "unique" in err.lower() or "duplicate" in err.lower():
            return {"ok": True, "sku": sku, "action": action, "status": "already_queued"}
        raise HTTPException(status_code=500, detail=err)

    return {"ok": True, "sku": sku, "action": action, "job_id": job_id}


# ---------------------------------------------------------------------------
# GET /api/health — platform health check (Flutter home screen, audible alerts)
# ---------------------------------------------------------------------------


@app.get("/api/health", dependencies=[AUTH])
def api_health() -> Dict[str, Any]:
    """Mirror ``tgw health`` output as JSON.

    Returns the full ``check_all()`` result plus a ``dead_letter_count`` field.
    HTTP 503 when ``ok`` is False so Flutter can detect failures by status code.
    """
    from .health import check_all

    result = check_all(_cfg)

    # Append dead_letter_count — quick postgres query, swallowed on error.
    dead_letter_count = 0
    try:
        with psycopg2.connect(_cfg["postgres_dsn"]) as con:
            with con.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM queue_jobs WHERE state = 'dead_letter'")
                dead_letter_count = cur.fetchone()[0]
    except Exception:
        pass
    result["dead_letter_count"] = dead_letter_count

    if not result["ok"]:
        raise HTTPException(status_code=503, detail=result)
    return result


# ---------------------------------------------------------------------------
# GET /api/queue/status — job counts
# ---------------------------------------------------------------------------


@app.get("/api/queue/status", dependencies=[AUTH])
def queue_status() -> Dict[str, Any]:
    try:
        with psycopg2.connect(_cfg["postgres_dsn"]) as con:
            with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT queue_name, state, COUNT(*) AS count,
                           COUNT(*) FILTER (
                               WHERE state IN ('queued', 'retry_wait')
                                 AND not_before > NOW()
                           ) AS scheduled_count
                      FROM queue_jobs
                     GROUP BY queue_name, state
                     ORDER BY queue_name, state
                    """
                )
                rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"postgres error: {e}")

    # Restructure as {queue_name: {state: count}}
    by_queue: Dict[str, Dict[str, int]] = {}
    scheduled: Dict[str, int] = {}
    for row in rows:
        q = row["queue_name"]
        s = row["state"]
        by_queue.setdefault(q, {})[s] = row["count"]
        scheduled[q] = scheduled.get(q, 0) + int(row.get("scheduled_count") or 0)

    return {
        "ok": True,
        "queues": by_queue,
        "scheduled": scheduled,
        "consumers": _queue_consumers(list(by_queue)),
    }


# ---------------------------------------------------------------------------
# GET /api/queue/daily_stats — date-scoped per-queue outcome counts
# ---------------------------------------------------------------------------
# GET /api/todos — list open todos (read-only operator access)
# ---------------------------------------------------------------------------


@app.get("/api/todos", dependencies=[AUTH])
def api_todos(agent: str = ""):
    """Return open todos from the todo_items table."""
    try:
        with psycopg2.connect(_cfg["postgres_dsn"]) as con:
            with con.cursor() as cur:
                if agent:
                    cur.execute(
                        "SELECT id, agent, priority, body, added_at FROM todo_items WHERE state = %s AND agent = %s ORDER BY priority DESC, id",
                        ("open", agent),
                    )
                else:
                    cur.execute(
                        "SELECT id, agent, priority, body, added_at FROM todo_items WHERE state = %s ORDER BY priority DESC, id",
                        ("open",),
                    )
                rows = cur.fetchall()
        items = [
            {
                "id": r[0],
                "agent": r[1],
                "priority": r[2],
                "title": (r[3] or "")[:200],
                "added": str(r[4]) if r[4] else "",
            }
            for r in rows
        ]
        return {"ok": True, "count": len(items), "items": items}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# (PP-QUEUESTATS-001: queue_status() above is lifetime-cumulative and cannot
#  answer "how many succeeded/failed TODAY" — this reads queue_daily_stats,
#  a Postgres view over the append-only queue_job_history ledger, so retried
#  jobs (which reset queue_jobs.finished_at back to NULL) still count for the
#  day they actually succeeded/failed. Day boundary is midnight
#  America/Los_Angeles, matching quota.py's eBay-reset convention.)
# ---------------------------------------------------------------------------


@app.get("/api/queue/daily_stats", dependencies=[AUTH])
def queue_daily_stats(date: Optional[str] = None) -> Dict[str, Any]:
    """Per-queue succeeded/failed counts for one day (default: today, LA tz).

    Also returns a by_hour breakdown per queue — deliberately not collapsed
    to a single number, so this can serve as the baseline data source for
    future per-queue surge/anomaly detection (not built yet, out of scope
    for this endpoint) without a schema/API change later.
    """
    if date:
        try:
            stat_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
    else:
        stat_date = datetime.now(_DISPLAY_TZ).date()

    try:
        with psycopg2.connect(_cfg["postgres_dsn"]) as con:
            with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT queue_name, stat_hour, state, job_count
                      FROM queue_daily_stats
                     WHERE stat_date = %s
                     ORDER BY queue_name, stat_hour
                    """,
                    (stat_date,),
                )
                rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"postgres error: {e}")

    by_queue: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        q = row["queue_name"]
        entry = by_queue.setdefault(q, {"succeeded": 0, "failed": 0, "dead_letter": 0, "by_hour": []})
        state = row["state"]
        count = int(row["job_count"])
        if state in ("succeeded", "failed", "dead_letter"):
            entry[state] += count
        entry["by_hour"].append(
            {
                "hour": row["stat_hour"].isoformat() if row["stat_hour"] else None,
                "state": state,
                "count": count,
            }
        )

    return {"ok": True, "date": stat_date.isoformat(), "tz": "America/Los_Angeles", "queues": by_queue}


# ---------------------------------------------------------------------------
# GET /api/migrate/blocked — items blocked in SKU migration (need human review)
# ---------------------------------------------------------------------------


@app.get("/api/migrate/blocked", dependencies=[AUTH])
def get_migrate_blocked() -> Dict[str, Any]:
    import json as _json
    from pathlib import Path as _Path

    registry_path = _Path("/opt/TGW/var/migrate-blocked.json")
    if not registry_path.exists():
        return {"ok": True, "count": 0, "items": []}
    try:
        registry = _json.loads(registry_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"could not read blocked registry: {exc}")
    items = [{"sku": sku, **entry} for sku, entry in registry.items()]
    return {"ok": True, "count": len(items), "items": items}


# ---------------------------------------------------------------------------
# GET /api/review — all items with review_block.ready=false (PP-REVIEW-001 P1)
# ---------------------------------------------------------------------------


@app.get("/api/review", dependencies=[AUTH])
def get_review_items(stage: Optional[str] = None, reason_code: Optional[str] = None) -> Dict[str, Any]:
    """Return all items that need human review, optionally filtered by stage/reason_code."""
    import sqlite3 as _sqlite3

    db_path = _cfg.get("sqlite_catalog_path")
    if not db_path or not db_path.exists():
        raise HTTPException(status_code=503, detail="catalog not built")
    try:
        con = _sqlite3.connect(db_path)
        try:
            rows = con.execute(
                "SELECT sku, title, json_extract(data, '$.review_block') as rb "
                "FROM catalog "
                "WHERE json_extract(data, '$.review_block') IS NOT NULL "
                "  AND json_extract(data, '$.review_block.ready') IS NOT 1"
            ).fetchall()
        finally:
            con.close()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"catalog query failed: {exc}")

    import json as _json

    items = []
    for sku, title, rb_json in rows:
        try:
            rb = _json.loads(rb_json) if rb_json else {}
        except Exception:
            rb = {}
        if stage and rb.get("stage") != stage:
            continue
        if reason_code and rb.get("reason_code") != reason_code:
            continue
        items.append({"sku": sku, "title": title or "", "review_block": rb})

    # Group by stage then reason_code for easy UI rendering
    grouped: Dict[str, Any] = {}
    for item in items:
        rb = item["review_block"]
        s = rb.get("stage", "unknown")
        rc = rb.get("reason_code", "UNKNOWN_ERROR")
        grouped.setdefault(s, {}).setdefault(rc, []).append(item)

    return {
        "ok": True,
        "count": len(items),
        "items": items,
        "grouped": grouped,
    }


# ---------------------------------------------------------------------------
# GET /api/system/workers — systemd unit states (PP-EDITOR-001 Phase 3j)
# ---------------------------------------------------------------------------


@app.get("/api/system/workers", dependencies=[AUTH])
def system_workers() -> Dict[str, Any]:
    """Return systemd active/sub state for all tgw-worker@ units + tgw-http."""
    from .queue import WORKER_QUEUES

    units = [f"tgw-worker@{q}.service" for q in WORKER_QUEUES] + ["tgw-http.service"]
    workers: List[Dict[str, Any]] = []

    try:
        r = subprocess.run(
            ["systemctl", "show", "--no-pager", "--property=Id,ActiveState,SubState,MainPID", *units],
            capture_output=True,
            text=True,
            timeout=8,
        )
        # systemctl show outputs blank-line-separated blocks per unit
        block: Dict[str, str] = {}
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line:
                if block.get("Id"):
                    workers.append(
                        {
                            "unit": block["Id"],
                            "active": block.get("ActiveState", "unknown"),
                            "sub": block.get("SubState", "unknown"),
                            "pid": int(block["MainPID"]) if block.get("MainPID", "0").isdigit() and block["MainPID"] != "0" else None,
                        }
                    )
                block = {}
            else:
                k, _, v = line.partition("=")
                block[k] = v
        # flush last block
        if block.get("Id"):
            workers.append(
                {
                    "unit": block["Id"],
                    "active": block.get("ActiveState", "unknown"),
                    "sub": block.get("SubState", "unknown"),
                    "pid": int(block["MainPID"]) if block.get("MainPID", "0").isdigit() and block["MainPID"] != "0" else None,
                }
            )
    except Exception as exc:
        log.warning("system_workers: systemctl query failed: %s", exc)
        # Fall back: return all as unknown
        workers = [{"unit": u, "active": "unknown", "sub": "unknown", "pid": None} for u in units]

    up = sum(1 for w in workers if w["active"] == "active")
    return {"ok": True, "workers": workers, "up": up, "total": len(units)}


# POST /api/jobs/{job_id}/requeue — re-enqueue a dead-letter job (Phase 3j)
# ---------------------------------------------------------------------------


@app.post("/api/jobs/{job_id}/requeue")
def requeue_job(
    job_id: str,
    operator_identity: str = Depends(_require_auth),
) -> Dict[str, Any]:
    """Re-enqueue a dead-letter job with a fresh dedupe key so it can run again."""
    try:
        with psycopg2.connect(_cfg["postgres_dsn"]) as con:
            with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT job_id, queue_name, payload_json, state, max_attempts,
                           entity_type, entity_id, operation, handler_family,
                           priority
                      FROM queue_jobs
                     WHERE job_id = %s
                    """,
                    (job_id,),
                )
                row = cur.fetchone()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"postgres error: {exc}")

    if not isinstance(row, dict):
        raise HTTPException(status_code=404, detail=f"job not found: {job_id}")

    if row["state"] != "dead_letter":
        raise HTTPException(
            status_code=400,
            detail=f"job {job_id} is in state {row['state']!r}, not dead_letter",
        )

    payload = dict(row["payload_json"]) if row["payload_json"] else {}
    payload_sku = payload.get("sku")
    sku = payload_sku.strip() if isinstance(payload_sku, str) else ""
    dedupe_entity = sku or job_id[:8]
    # PP-ACTIONCONSOLE-001 improvement loop: mark operator-triggered retries so
    # the ledger can be mined for "same failure, same manual fix, keeps working"
    # patterns — candidates for automatic retry policy.
    payload["operator_retry"] = True
    payload["origin"] = "operator"
    payload["retried_from_job"] = str(job_id)
    new_dedupe = f"{row['queue_name']}:{dedupe_entity}:requeue:{time.time_ns()}"

    # Old dead-letter rows predate the canonical entity manifest.  Re-enqueuing
    # one with enqueue_job's generic defaults creates a fresh row that modern
    # workers must reject before doing any useful work.  A payload-bound SKU is
    # definitive per-item identity, so migrate it at this boundary.  Queue-level
    # jobs without a SKU retain their recorded manifest instead.
    if sku:
        entity_type = "item"
        entity_id = sku
    else:
        entity_type = row.get("entity_type") or "generic"
        entity_id = row.get("entity_id") or ""

    # A retry is a request to try the operation again *now*, not permission to
    # replay a stale queue envelope.  Item jobs created before workflow
    # migration lack the current graph/generation/authority bindings and modern
    # provider workers must reject them.  Re-enter through the same item action
    # dispatcher as the visible button so it evaluates current state and issues
    # a fresh, exactly-bound job (or reports a truthful hold).
    action_by_queue = {
        "ai_identify": "ai_identify",
        "ebay_sync": "sync_from_ebay",
        "thumbnail_gen": "thumbnail_gen",
        "catalog_rebuild": "catalog_rebuild",
    }
    governed_listing_queues = {
        "ebay_draft", "ebay_upload", "ebay_price", "ebay_stage", "ebay_publish",
    }
    if sku and str(row["queue_name"]) in governed_listing_queues:
        return {
            "ok": False,
            "job_id": str(job_id),
            "new_job_id": "",
            "queue": str(row["queue_name"]),
            "status": "held",
            "hold_code": "CURRENT_OPERATOR_OBJECT_REQUIRED",
            "detail": (
                "historical listing jobs cannot be replayed; fetch the current "
                "published operator object and submit an enabled command"
            ),
            "operator_object": f"/api/operator/items/{sku}",
            "command_endpoint": f"/api/operator/items/{sku}/commands",
        }
    current_action = action_by_queue.get(str(row["queue_name"])) if sku else None
    if current_action:
        result = item_action(
            sku,
            ActionBody(action=current_action),
            operator_identity=operator_identity,
        )
        result = dict(result)
        result["retried_from_job"] = str(job_id)
        result["retry_mode"] = "current_item_action"
        result["new_job_id"] = result.get("job_id") or ""
        result["queue"] = str(row["queue_name"])
        if not result.get("ok") and "detail" not in result:
            result["detail"] = f"current {current_action} dispatch {result.get('status', 'held')}"
        return result

    try:
        new_job_id = state_machine.enqueue_job(
            queue_name=row["queue_name"],
            payload=payload,
            entity_type=entity_type,
            entity_id=entity_id,
            operation=row.get("operation") or "run",
            handler_family=row.get("handler_family") or row["queue_name"],
            priority=(row.get("priority") if isinstance(row.get("priority"), int) and not isinstance(row.get("priority"), bool) else None),
            dedupe_key=new_dedupe,
            max_attempts=row.get("max_attempts") or 3,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"enqueue failed: {exc}")

    return {"ok": True, "job_id": job_id, "new_job_id": new_job_id, "queue": row["queue_name"]}


# ---------------------------------------------------------------------------
# GET /api/ebay/aspects/{category_id} — aspect list for offer form
# ---------------------------------------------------------------------------


@app.get("/api/ebay/aspects/{category_id}", dependencies=[AUTH])
def ebay_aspects(category_id: str) -> Dict[str, Any]:
    try:
        from .apis.ebay.specifics import get_aspects

        aspects = get_aspects(_cfg, category_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"eBay aspects error: {e}")
    return {"ok": True, "category_id": category_id, "aspects": aspects}


# ---------------------------------------------------------------------------
# GET /api/ebay/category-context/{category_id}
# Unified category data: conditions, aspects, store category, pricing hints.
# Single call used by the editor to drive all category-specific form fields.
# ---------------------------------------------------------------------------


@app.get("/api/ebay/category-context/{category_id}", dependencies=[AUTH])
def ebay_category_context(category_id: str, current_condition: str = "") -> Dict[str, Any]:
    from .ebay.pricing import _groups_reverse, _load_groups

    # ── Conditions — real per-category eBay policy, not a fabricated superset ──
    # (session 39: the old _CONDITION_ID_MAP fanned one real conditionId like 3000
    # "Used" out into three invented grades — USED_EXCELLENT/GOOD/ACCEPTABLE — none
    # of which eBay actually allows for categories with only a single "Used" bucket)
    conditions: List[Dict[str, str]] = []
    category_recognized = False
    item_condition_required: Optional[bool] = None
    required_flag_valid = False
    try:
        from .apis.ebay.conditions import allowed_conditions_for_category, condition_policy_for_category

        try:
            policy = condition_policy_for_category(_cfg, category_id)
            category_recognized = policy["recognized"]
            item_condition_required = policy["item_condition_required"]
            required_flag_valid = policy["required_flag_valid"]
        except Exception:
            pass

        seen: set = set()
        for c in allowed_conditions_for_category(_cfg, category_id):
            if c["condition_enum"] not in seen:
                conditions.append({"enum": c["condition_enum"], "label": c["condition_label"]})
                seen.add(c["condition_enum"])
    except Exception as exc:
        log.warning("category-context: conditions load error: %s", exc)

    # ── Condition remap — never upgrade condition when category changes ────────
    # (session 39: switching category could otherwise leave a stale/invalid enum
    # in place, or — per Dave — silently jump to a better grade like "Like New".
    # best_condition_for_enum() already implements the correct same-or-worse-only
    # remap; it just wasn't wired into the live category-change UI path.)
    condition_remap: Optional[Dict[str, str]] = None
    if current_condition and current_condition not in {c["enum"] for c in conditions}:
        try:
            from .apis.ebay.conditions import best_condition_for_enum

            remap = best_condition_for_enum(_cfg, category_id, current_condition)
            if remap:
                condition_remap = {"enum": remap["condition_enum"], "label": remap["condition_label"]}
        except Exception as exc:
            log.warning("category-context: condition remap error: %s", exc)

    # ── Aspects from eBay API ────────────────────────────────────────────
    # No real eBay category has zero item specifics (at minimum: Country/Region
    # of Manufacture, and most also carry California Prop 65 Warning) — an empty
    # list here means the lookup FAILED (e.g. Taxonomy API rate-limited), not that
    # the category genuinely has none. aspects_error carries that distinction to
    # the UI so it can say "lookup failed, retry" instead of "no specifics".
    aspects: List[Any] = []
    aspects_error: Optional[str] = None
    try:
        from .apis.ebay.specifics import get_aspects

        aspects = get_aspects(_cfg, category_id)
    except Exception as exc:
        log.warning("category-context: aspects error: %s", exc)
        aspects_error = str(exc)

    # ── Group data from category-groups.json ─────────────────────────────
    _load_groups(_cfg)  # warm cache
    grp_key = (_groups_reverse or {}).get(str(category_id))
    grp: Dict[str, Any] = {}
    if grp_key:
        from .ebay.pricing import _groups_cache

        grp = (_groups_cache or {}).get("groups", {}).get(grp_key, {})

    pricing = grp.get("pricing") or {}
    store_category = grp.get("store_category") or ""
    store_category_id = grp.get("store_category_id")
    size_class = grp.get("size_class") or ""
    group_name = grp.get("name") or ""

    # ── Fulfillment policy for this category ─────────────────────────────
    fulfillment_id = (_cfg.get("fulfillment_policy_by_category") or {}).get(str(category_id)) or _cfg.get("fulfillment_policy_id") or ""
    if not fulfillment_id:
        _pol_path = _cfg.get("catalog_root")
        if _pol_path:
            _pol_file = _pol_path / "ebay-fulfillment-policies.json"
            if _pol_file.exists():
                try:
                    import json as _pj

                    _pdata = _pj.loads(_pol_file.read_text())
                    _fids = _pdata.get("fulfillment", {})
                    # prefer FC4 by name, else first entry
                    fulfillment_id = next((pid for pid, name in _fids.items() if name == "FC4"), next(iter(_fids), ""))
                except Exception:
                    pass

    return {
        "ok": True,
        "category_id": category_id,
        "conditions": conditions,
        "condition_remap": condition_remap,
        "category_recognized": category_recognized,
        "item_condition_required": item_condition_required,
        "required_flag_valid": required_flag_valid,
        "aspects": aspects,
        "aspects_error": aspects_error,
        "store_category": store_category,
        "store_category_id": store_category_id,
        "group_name": group_name,
        "size_class": size_class,
        "pricing": {
            "floor": pricing.get("floor"),
            "typical_used": pricing.get("typical_used"),
            "typical_new": pricing.get("typical_new"),
        },
        "fulfillment_policy_id": fulfillment_id,
    }


# GET /api/ebay/category-search?q=...
# Live eBay category type-ahead — returns up to 15 suggestions with breadcrumb paths.
# ---------------------------------------------------------------------------


@app.get("/api/ebay/category-search", dependencies=[AUTH])
def ebay_category_search(q: str = "") -> Dict[str, Any]:
    if not q or len(q.strip()) < 2:
        return {"ok": True, "results": []}
    try:
        from .apis.ebay.taxonomy import search_categories_local

        results = search_categories_local(_cfg, q.strip(), limit=20)
        return {"ok": True, "results": results}
    except Exception as exc:
        log.warning("category-search error: %s", exc)
        return {"ok": False, "detail": str(exc), "results": []}


# GET /api/ebay/category-node/{category_id} — resolve a raw operator-typed ID
# ---------------------------------------------------------------------------


@app.get("/api/ebay/category-node/{category_id}", dependencies=[AUTH])
def ebay_category_node(category_id: str) -> Dict[str, Any]:
    try:
        from .apis.ebay.taxonomy import get_category_node

        node = get_category_node(_cfg, category_id)
        if not node:
            return {"ok": False, "detail": "unknown category id"}
        return {"ok": True, **node}
    except Exception as exc:
        log.warning("category-node error: %s", exc)
        return {"ok": False, "detail": str(exc)}


# GET /api/ebay/category-children?parent_id=... — tree-browse navigation
# ---------------------------------------------------------------------------


@app.get("/api/ebay/category-children", dependencies=[AUTH])
def ebay_category_children(parent_id: str = "") -> Dict[str, Any]:
    try:
        from .apis.ebay.taxonomy import get_category_children, get_category_node

        children = get_category_children(_cfg, parent_id or None)
        parent = get_category_node(_cfg, parent_id) if parent_id else None
        return {"ok": True, "parent": parent, "children": children}
    except Exception as exc:
        log.warning("category-children error: %s", exc)
        return {"ok": False, "detail": str(exc), "children": []}


# GET /api/ebay/store-categories
# Live-authoritative eBay store custom categories (GetStore), TTL-cached;
# falls back to category-groups.json's set only if the live call itself
# raises (network/auth failure) — an empty live response is trusted as-is,
# since a genuinely empty store has no custom categories to offer. Fixes
# the store-category dropdown authority gap found in the 2026-07-18
# Seller Hub UI triage: the option list used to come only from
# category-groups.json, a local/config-assembled list never checked
# against the live account (PP-SELLERHUB-001, todo #1546).
# ---------------------------------------------------------------------------

_LIVE_STORE_CATS_CACHE: Dict[str, Any] = {"data": None, "at": 0.0}
_LIVE_STORE_CATS_TTL = 900  # 15 min — bounds live-call frequency without letting the list go stale for long


def _store_categories_from_groups(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build valid Store Category options from the configured local groups.

    Item editing uses this local source so its category selectors do not depend
    on GetStore. The live adapter may use the same helper only as its fallback.
    Preserve distinct category IDs even if their display names match, and never
    emit an empty/``None`` ID as a selectable value.
    """
    seen: Dict[str, str] = {}
    try:
        cg_path = cfg.get("category_groups_path")
        if cg_path and Path(cg_path).exists():
            cg = json.loads(Path(cg_path).read_text())
            for grp in cg.get("groups", cg).values():
                name = str(grp.get("store_category") or grp.get("store_category_name") or "").strip()
                raw_sid = grp.get("store_category_id")
                if raw_sid is None:
                    continue
                sid = str(raw_sid).strip()
                if name and sid and sid not in seen:
                    seen[sid] = name
    except Exception:
        pass
    return [{"id": sid, "name": name} for sid, name in sorted(seen.items(), key=lambda item: (item[1].casefold(), item[0]))]


def _store_categories_snapshot(cfg: Dict[str, Any]) -> Tuple[List[Dict[str, str]], Optional[str], Optional[str]]:
    """Load the validated last-known-good GetStore snapshot for page rendering.

    Returns (results, refreshed_at, error). A missing/corrupt snapshot is kept
    distinct from a legitimate, successfully refreshed empty eBay result.
    """
    catalog_root = cfg.get("catalog_root")
    path = (Path(catalog_root) / "ebay-store-categories.json") if catalog_root else None
    if not path or not path.exists():
        return [], None, "eBay Store category snapshot unavailable"
    try:
        payload = json.loads(path.read_text())
        raw_results = payload["results"]
        if not isinstance(raw_results, list):
            raise ValueError("results is not a list")
        seen: Dict[str, str] = {}
        for row in raw_results:
            if not isinstance(row, dict):
                raise ValueError("result is not an object")
            raw_id = row.get("id")
            raw_name = row.get("name")
            if not isinstance(raw_id, str) or not isinstance(raw_name, str):
                raise ValueError("result id and name must be strings")
            sid = raw_id.strip()
            name = raw_name.strip()
            if not sid or not name:
                raise ValueError("result has a blank id or name")
            if sid in seen and seen[sid] != name:
                raise ValueError(f"duplicate id {sid!r} has conflicting names")
            seen[sid] = name
        results = [{"id": sid, "name": name} for sid, name in sorted(seen.items(), key=lambda item: (item[1].casefold(), item[0]))]
        raw_refreshed_at = payload.get("refreshed_at")
        if raw_refreshed_at is not None and not isinstance(raw_refreshed_at, str):
            raise ValueError("refreshed_at must be a string or null")
        return results, (raw_refreshed_at or "").strip() or None, None
    except Exception as exc:
        return [], None, f"eBay Store category snapshot invalid: {exc}"


def _live_store_categories(cfg: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], bool]:
    """Return (results, used_fallback)."""
    now = time.time()
    cached = _LIVE_STORE_CATS_CACHE["data"]
    if cached is not None and (now - _LIVE_STORE_CATS_CACHE["at"]) < _LIVE_STORE_CATS_TTL:
        return cached, False
    try:
        from .apis.ebay.trading import get_store_categories

        live = get_store_categories(cfg)
        if not isinstance(live, list):
            raise ValueError("GetStore response is not a category list")
        seen: Dict[str, str] = {}
        for category in live:
            if not isinstance(category, dict):
                raise ValueError("GetStore category is not an object")
            raw_id = category.get("id")
            raw_name = category.get("name")
            if not isinstance(raw_id, str) or not isinstance(raw_name, str):
                raise ValueError("GetStore category id and name must be strings")
            sid = raw_id.strip()
            name = raw_name.strip()
            if not sid or not name:
                raise ValueError("GetStore category has a blank id or name")
            if sid in seen and seen[sid] != name:
                raise ValueError(f"duplicate Store category id {sid!r} has conflicting names")
            seen[sid] = name
        results = [{"id": sid, "name": name} for sid, name in sorted(seen.items(), key=lambda item: (item[1].casefold(), item[0]))]
        catalog_root = cfg.get("catalog_root")
        if not catalog_root:
            raise RuntimeError("catalog_root is not configured; cannot persist Store category snapshot")
        atomic_write_json(
            Path(catalog_root) / "ebay-store-categories.json",
            {
                "source": "ebay_get_store",
                "refreshed_at": datetime.now(timezone.utc).isoformat(),
                "results": results,
            },
        )
        _LIVE_STORE_CATS_CACHE["data"] = results
        _LIVE_STORE_CATS_CACHE["at"] = now
        return results, False
    except Exception as exc:
        snapshot, _refreshed_at, snapshot_error = _store_categories_snapshot(cfg)
        if snapshot_error:
            log.warning("live store-categories fetch failed and no valid eBay snapshot exists; using local mapping: %s", exc)
            return _store_categories_from_groups(cfg), True
        log.warning("live store-categories fetch failed; preserving last-known-good eBay snapshot: %s", exc)
        # Never update the snapshot/cache timestamp on failure: the prior eBay
        # observation remains intact and the next explicit refresh retries live.
        return snapshot, True


@app.get("/api/ebay/store-categories", dependencies=[AUTH])
def ebay_store_categories() -> Dict[str, Any]:
    """Return local Store-category reference data without crossing eBay."""
    results, refreshed_at, error = _store_categories_snapshot(_cfg)
    if error:
        results = _store_categories_from_groups(_cfg)
        return {
            "ok": False,
            "results": results,
            "source": "local_mapping",
            "refreshed_at": None,
            "error": error,
        }
    return {
        "ok": True,
        "results": results,
        "source": "snapshot",
        "refreshed_at": refreshed_at,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Live-authoritative fulfillment (shipping) policies — TTL-cached, falling
# back to the static ebay-fulfillment-policies.json cache only if the live
# call fails. Fixes the fulfillment-policy dropdown authority gap found in
# the 2026-07-18 Seller Hub UI triage: the dropdown used to be driven only
# by that static cache file, refreshed by nothing (PP-SELLERHUB-001, #1547).
# ---------------------------------------------------------------------------

_LIVE_FULFILLMENT_POLICIES_CACHE: Dict[str, Any] = {"data": None, "at": 0.0}
_LIVE_FULFILLMENT_POLICIES_TTL = 900  # 15 min


def _fulfillment_policies_snapshot(cfg: Dict[str, Any]) -> Tuple[Dict[str, str], Optional[str], Optional[str]]:
    """Load the validated last-known-good Account API fulfillment snapshot."""
    catalog_root = cfg.get("catalog_root")
    path = (Path(catalog_root) / "ebay-fulfillment-policies.json") if catalog_root else None
    if not path or not path.exists():
        return {}, None, "eBay fulfillment-policy snapshot unavailable"
    try:
        payload = json.loads(path.read_text())
        raw = payload["fulfillment"]
        if not isinstance(raw, dict):
            raise ValueError("fulfillment is not an object")
        results: Dict[str, str] = {}
        for raw_id, raw_name in raw.items():
            if not isinstance(raw_id, str) or not isinstance(raw_name, str):
                raise ValueError("policy id and name must be strings")
            policy_id = raw_id.strip()
            name = raw_name.strip()
            if not policy_id or not name:
                raise ValueError("policy has a blank id or name")
            results[policy_id] = name
        raw_refreshed_at = payload.get("refreshed_at")
        if raw_refreshed_at is not None and not isinstance(raw_refreshed_at, str):
            raise ValueError("refreshed_at must be a string or null")
        return results, (raw_refreshed_at or "").strip() or None, None
    except Exception as exc:
        return {}, None, f"eBay fulfillment-policy snapshot invalid: {exc}"


def _live_fulfillment_policies(cfg: Dict[str, Any]) -> Tuple[Dict[str, str], bool]:
    """Return ({policy_id: name}, used_fallback)."""
    now = time.time()
    cached = _LIVE_FULFILLMENT_POLICIES_CACHE["data"]
    if cached is not None and (now - _LIVE_FULFILLMENT_POLICIES_CACHE["at"]) < _LIVE_FULFILLMENT_POLICIES_TTL:
        return cached, False
    try:
        from .ebay.sync import get_fulfillment_policies_full

        live = get_fulfillment_policies_full(cfg)
        if not isinstance(live, list):
            raise ValueError("Account API response is not a fulfillment-policy list")
        results: Dict[str, str] = {}
        for policy in live:
            if not isinstance(policy, dict):
                raise ValueError("fulfillment policy is not an object")
            raw_id = policy.get("id")
            raw_name = policy.get("name")
            if not isinstance(raw_id, str) or not isinstance(raw_name, str):
                raise ValueError("fulfillment policy id and name must be strings")
            policy_id = raw_id.strip()
            name = raw_name.strip()
            if not policy_id or not name:
                raise ValueError("fulfillment policy has a blank id or name")
            if policy_id in results and results[policy_id] != name:
                raise ValueError(f"duplicate fulfillment policy id {policy_id!r} has conflicting names")
            results[policy_id] = name
        catalog_root = cfg.get("catalog_root")
        if not catalog_root:
            raise RuntimeError("catalog_root is not configured; cannot persist fulfillment-policy snapshot")
        path = Path(catalog_root) / "ebay-fulfillment-policies.json"
        payload: Dict[str, Any] = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text())
                if isinstance(existing, dict):
                    payload = existing
            except Exception:
                pass
        payload.update(
            {
                "source": "ebay_account_api",
                "refreshed_at": datetime.now(timezone.utc).isoformat(),
                "fulfillment": results,
            }
        )
        atomic_write_json(path, payload)
        _LIVE_FULFILLMENT_POLICIES_CACHE["data"] = results
        _LIVE_FULFILLMENT_POLICIES_CACHE["at"] = now
        return results, False
    except Exception as exc:
        snapshot, _refreshed_at, snapshot_error = _fulfillment_policies_snapshot(cfg)
        if snapshot_error:
            log.warning("live fulfillment-policy fetch failed and no valid snapshot exists: %s", exc)
            return {}, True
        log.warning("live fulfillment-policy fetch failed; preserving last-known-good snapshot: %s", exc)
        return snapshot, True


@app.post("/api/ebay/reference-data/refresh", dependencies=[AUTH])
def refresh_ebay_reference_data() -> Dict[str, Any]:
    """Explicitly reconcile stable eBay selector data into local snapshots."""
    _LIVE_STORE_CATS_CACHE.update({"data": None, "at": 0.0})
    _LIVE_FULFILLMENT_POLICIES_CACHE.update({"data": None, "at": 0.0})
    store_categories, store_preserved = _live_store_categories(_cfg)
    fulfillment_policies, fulfillment_preserved = _live_fulfillment_policies(_cfg)
    return {
        "ok": not (store_preserved or fulfillment_preserved),
        "store_categories": {
            "count": len(store_categories),
            "preserved_snapshot": store_preserved,
        },
        "fulfillment_policies": {
            "count": len(fulfillment_policies),
            "preserved_snapshot": fulfillment_preserved,
        },
    }


# ---------------------------------------------------------------------------
# GET /api/locations — distinct locations from SQLite
# ---------------------------------------------------------------------------


@app.get("/api/locations", dependencies=[AUTH])
def list_locations() -> Dict[str, Any]:
    db_path = _cfg["sqlite_catalog_path"]
    if not db_path.exists():
        raise HTTPException(status_code=503, detail="SQLite catalog not built")

    con = _sqlite_conn()
    try:
        rows = con.execute("SELECT DISTINCT location FROM catalog WHERE location != '' ORDER BY location").fetchall()
    finally:
        con.close()

    return {"ok": True, "locations": [r[0] for r in rows]}


# ---------------------------------------------------------------------------
# GET /api/catalog/snapshot — atomic SQLite snapshot for Flutter offline sync
# PP-PORTABLE-CATALOG-001 Phase 2
# ---------------------------------------------------------------------------


@app.get("/api/catalog/snapshot", dependencies=[AUTH])
def catalog_snapshot(background_tasks: BackgroundTasks):
    """
    Stream an atomic backup of tgwcatalog.db for Flutter offline-first sync.
    Uses sqlite3.Connection.backup() — safe to call while catalog is live.
    Returns the db file as application/octet-stream.
    """
    import os
    import tempfile

    db_path = _cfg["sqlite_catalog_path"]
    if not db_path.exists():
        raise HTTPException(status_code=503, detail="SQLite catalog not built — run tgw build-sqlite")

    fd, tmp_path = tempfile.mkstemp(suffix=".db", prefix="tgwcatalog_snapshot_")
    os.close(fd)
    try:
        src_con = sqlite3.connect(str(db_path))
        try:
            dst_con = sqlite3.connect(tmp_path)
            try:
                src_con.backup(dst_con)
            finally:
                dst_con.close()
        finally:
            src_con.close()
    except Exception:
        # backup() raised before the unlink task was ever scheduled — clean
        # up here instead of leaking a multi-MB temp file per failed sync.
        os.unlink(tmp_path)
        raise

    background_tasks.add_task(os.unlink, tmp_path)
    return FileResponse(
        tmp_path,
        media_type="application/octet-stream",
        filename="tgwcatalog.db",
    )


# ---------------------------------------------------------------------------
# GET /api/category-groups — template list for intake form
# ---------------------------------------------------------------------------


@app.get("/api/category-groups", dependencies=[AUTH])
def list_category_groups() -> Dict[str, Any]:
    groups_path = _cfg.get("category_groups_path")
    if not groups_path or not Path(groups_path).exists():
        raise HTTPException(status_code=503, detail="category-groups.json not found")
    raw = json.loads(Path(groups_path).read_text(encoding="utf-8"))
    groups = raw.get("groups", {})
    result = []
    for key, grp in groups.items():
        result.append(
            {
                "key": key,
                "name": grp.get("name", key),
                "size_class": grp.get("size_class", ""),
                "ai_hint": grp.get("ai_hint", ""),
                "floor": grp.get("pricing", {}).get("floor"),
                "typical_used": grp.get("pricing", {}).get("typical_used"),
            }
        )
    return {"ok": True, "count": len(result), "groups": result}


# ---------------------------------------------------------------------------
# POST /api/items/{sku}/set-template — apply category-group template
# ---------------------------------------------------------------------------


@app.post("/api/items/{sku}/set-template", dependencies=[AUTH])
def set_item_template(sku: str, body: SetTemplateBody) -> Dict[str, Any]:
    groups_path = _cfg.get("category_groups_path")
    if not groups_path or not Path(groups_path).exists():
        raise HTTPException(status_code=503, detail="category-groups.json not found")

    raw = json.loads(Path(groups_path).read_text(encoding="utf-8"))
    grp = raw.get("groups", {}).get(body.template_key)
    if grp is None:
        raise HTTPException(status_code=400, detail=f"unknown template_key: {body.template_key!r}")

    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"sku not found: {sku}")

    doc = load_item_doc(json_path)

    # Build template fields (same logic as tgw set-template CLI)
    fields: Dict[str, Any] = {"category_group": body.template_key}
    if grp.get("size_class"):
        fields["size_class"] = grp["size_class"]
    group_hint = grp.get("ai_hint", "").strip()
    if group_hint:
        existing_hint = doc.get("ai_hint", "").strip()
        if existing_hint and existing_hint != group_hint:
            fields["ai_hint"] = f"{group_hint}; {existing_hint}"
        else:
            fields["ai_hint"] = group_hint
    cats = grp.get("ebay_categories", [])
    if cats and not doc.get("ebay_category_id"):
        fields["ebay_category_id"] = cats[0]

    _apply_patch(json_path, fields)
    _enqueue_catalog_rebuild(f"set_template:{sku}")

    return {"ok": True, "sku": sku, "template_key": body.template_key, "applied": fields, "group_name": grp.get("name", body.template_key)}


# ---------------------------------------------------------------------------
# DELETE /api/items/{sku} — soft-delete item (status → deleted)
# ---------------------------------------------------------------------------


@app.delete("/api/items/{sku}", dependencies=[AUTH])
def delete_item(sku: str) -> Dict[str, Any]:
    """Soft-delete an item: set status=deleted in JSON, enqueue catalog rebuild.

    Does NOT touch eBay — caller must end active listings separately.
    Does NOT remove the ItemData folder so the data is recoverable.
    """
    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"sku not found: {sku}")

    _apply_patch(json_path, {"status": "deleted", "deleted_at": datetime.now(timezone.utc).isoformat()})
    _enqueue_catalog_rebuild(f"delete:{sku}")
    return {"ok": True, "sku": sku, "status": "deleted"}


# ---------------------------------------------------------------------------
# POST /api/items/{sku}/photo-order — save user-defined photo ordering
# ---------------------------------------------------------------------------


@app.post("/api/items/{sku}/photo-order", dependencies=[AUTH])
def set_photo_order(sku: str, body: PhotoOrderBody) -> Dict[str, Any]:
    """Persist a user-defined photo order to the item JSON as photo_order: [...]."""
    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"sku not found: {sku}")
    _apply_patch(json_path, {"photo_order": [n for n in body.order if n]})
    _enqueue_catalog_rebuild(f"photo_order:{sku}")
    return {"ok": True, "sku": sku, "order": [n for n in body.order if n]}


# ---------------------------------------------------------------------------
# GET/POST /api/items/{sku}/inventory-diff[/apply] — eBay Draft -> Inventory
# Record reverse flow (todo #1417, PP-LISTEDITOR-001). Deliberately its own,
# separate code path from accept_proposals (the FORWARD proposal system,
# revision_draft -> draft_listing.item_specifics) — different data, different
# destination (item_attributes / Set A), no shared write path (spec point 6).
# ---------------------------------------------------------------------------


@app.get("/api/items/{sku}/inventory-diff", dependencies=[AUTH])
def get_inventory_diff(sku: str) -> Dict[str, Any]:
    """Read-only: current eBay-draft -> inventory-record diff for `sku`.
    Never mutates anything, callable any time (spec point 2). Recomputed
    live on every call — no stored "diff" or "dismissed" state (spec point
    5; see this packet's result manifest for the sticky-vs-resurface
    design confirmation)."""
    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"sku not found: {sku}")
    doc = load_item_doc(json_path)
    diffs = diff_ebay_draft_to_inventory(doc)
    return {"ok": True, "sku": sku, "diffs": diffs}


@app.post("/api/items/{sku}/inventory-lock", dependencies=[AUTH])
def set_inventory_lock(sku: str, body: InventoryLockBody) -> Dict[str, Any]:
    """Toggle whether one item_attributes key auto-syncs from the eBay
    draft (Dave, 2026-07-18 padlock design). Locking/unlocking is metadata
    about future sync behavior, not a fact about the item — never touches
    item_attributes_history, unlike every other Set A write path here."""
    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"sku not found: {sku}")
    doc = load_item_doc(json_path)
    patch = inventory_record.set_locked(doc, body.key, body.locked)
    _apply_patch(json_path, patch)
    return {"ok": True, "sku": sku, "key": body.key, "locked": body.locked}


@app.post("/api/items/{sku}/inventory-diff/apply", dependencies=[AUTH])
def apply_inventory_diff_endpoint(sku: str, body: InventoryDiffApplyBody) -> Dict[str, Any]:
    """Write ONLY the checked subset of keys into item_attributes (Set A),
    with provenance (spec point 4). A genuinely new, explicit, named write
    path into Set A — NOT routed through _apply_patch's generic dict merge
    (this calls the sanctioned tgw.ebay.inventory_diff.apply_inventory_diff
    function, itself built on tgw.inventory_record's accessor, then hands
    the resulting full envelope onward to _apply_patch, which is safe: an
    already-enveloped item_attributes value is a plain full replace, per
    _apply_patch's own is_envelope() branch)."""
    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"sku not found: {sku}")
    doc = load_item_doc(json_path)
    patch = apply_inventory_diff(doc, body.keys, applied_by="operator")
    if not patch:
        # Nothing in the requested key set is still an active diff —
        # idempotent no-op, not an error (spec point 5).
        return {"ok": True, "sku": sku, "applied": [], "note": "no active diff for requested keys"}
    _apply_patch(
        json_path,
        {
            "item_attributes": patch["item_attributes"],
            "item_attributes_history": patch["item_attributes_history"],
        },
    )
    _enqueue_catalog_rebuild(f"inventory_diff_apply:{sku}")
    return {"ok": True, "sku": sku, "applied": patch["applied_keys"]}


# ---------------------------------------------------------------------------
# GET/POST /api/items/{sku}/category-aspect-migration[/apply] — todo #1471,
# PP-LISTEDITOR-001, invariant C14 lineage. A category change can leave Set B
# (draft_listing.item_specifics) aspects the CURRENT category no longer
# recognizes — eBay's own Seller Hub discards those; TGW's push doesn't
# (confirmed live incident, todo #1470's companion finding). This panel
# matches eBay's discard-as-default behavior WITHOUT deleting the data
# (Prime Directive 1) — checked keys move into item_attributes (Set A)
# instead of eBay, unchecked keys stay on eBay as (now legitimate, operator-
# kept) custom aspects. Deliberately its own code path, same shape as
# inventory-diff above but a different data source/destination/direction —
# no shared write path (spec point 6 discipline, same as that panel).
# ---------------------------------------------------------------------------


@app.get("/api/items/{sku}/category-aspect-migration", dependencies=[AUTH])
def get_category_aspect_migration(sku: str) -> Dict[str, Any]:
    """Read-only: Set B aspects the item's CURRENT category no longer
    recognizes. Never mutates anything, callable any time. Recomputed live
    on every call — no stored/dismissed state, same idempotency discipline
    as the inventory-diff panel."""
    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"sku not found: {sku}")
    doc = load_item_doc(json_path)
    orphaned = detect_category_orphaned_aspects(_cfg, doc)
    return {"ok": True, "sku": sku, "orphaned": orphaned}


@app.post("/api/items/{sku}/category-aspect-migration/apply", dependencies=[AUTH])
def apply_category_aspect_migration_endpoint(sku: str, body: CategoryAspectMigrationApplyBody) -> Dict[str, Any]:
    """Move the checked subset of category-orphaned aspects from Set B
    into Set A, removing them from Set B — a genuinely new, explicit,
    named write path for BOTH sets, not routed through _apply_patch's
    generic dict merge (calls the sanctioned
    tgw.ebay.category_aspect_migration.apply_category_aspect_migration
    function, itself built on the two sets' own accessors, then hands the
    resulting full envelopes onward to _apply_patch — safe, same
    already-enveloped-is-a-plain-replace branch the inventory-diff apply
    endpoint above relies on)."""
    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"sku not found: {sku}")
    doc = load_item_doc(json_path)
    patch = apply_category_aspect_migration(doc, body.keys, cfg=_cfg, applied_by="operator")
    if not patch:
        # Nothing in the requested key set is still an active orphan —
        # idempotent no-op, not an error (same reasoning as inventory-diff).
        return {"ok": True, "sku": sku, "migrated": [], "note": "no active orphaned aspects for requested keys"}
    _apply_patch(
        json_path,
        {
            "item_attributes": patch["item_attributes"],
            "item_attributes_history": patch["item_attributes_history"],
            "draft_listing": patch["draft_listing"],
        },
    )
    _enqueue_catalog_rebuild(f"category_aspect_migration_apply:{sku}")
    return {"ok": True, "sku": sku, "migrated": patch["migrated_keys"]}


# ---------------------------------------------------------------------------
# GET /api/items/{sku}/assets — ordered photo list (Stage 1 asset fence)
# ---------------------------------------------------------------------------


@app.get("/api/items/{sku}/assets", dependencies=[AUTH])
def list_assets(sku: str) -> Dict[str, Any]:
    """Return photos in photo_order display order with position metadata."""
    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"sku not found: {sku}")
    doc = load_item_doc(json_path)
    photos = _ordered_photos(doc, json_path.parent)
    assets = [{"name": p.name, "url": f"/media/{sku}/{p.name}", "position": i} for i, p in enumerate(photos)]
    return {"ok": True, "sku": sku, "count": len(assets), "assets": assets}


# ---------------------------------------------------------------------------
# DELETE /api/items/{sku}/assets/{filename} — remove a photo
# ---------------------------------------------------------------------------


@app.delete("/api/items/{sku}/assets/{filename}", dependencies=[AUTH])
def delete_asset(sku: str, filename: str) -> Dict[str, Any]:
    """Delete a photo from disk and remove it from photo_order."""
    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"sku not found: {sku}")
    sku_dir = json_path.parent
    target = sku_dir / filename
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"file not found: {filename}")
    try:
        target.resolve().relative_to(sku_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="path traversal not allowed")
    archive_root = _cfg.get("archive_root")
    if archive_root:
        _archive_before_overwrite(archive_root, target)
    target.unlink()
    doc = load_item_doc(json_path)
    order = doc.get("photo_order") or []
    if filename in order:
        _apply_patch(json_path, {"photo_order": [n for n in order if n != filename]})
    return {"ok": True, "sku": sku, "deleted": filename}


# ---------------------------------------------------------------------------
# POST /api/items/{sku}/remove-comp — operator removes a bad comp listing
# ---------------------------------------------------------------------------


class RemoveCompBody(BaseModel):
    url: str  # comp listing URL to remove (unique key within price_comps.items)


@app.post("/api/items/{sku}/remove-comp", dependencies=[AUTH])
def remove_comp(sku: str, body: RemoveCompBody) -> Dict[str, Any]:
    """Remove one comp listing from price_comps.items and recalculate stats."""
    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"sku not found: {sku}")
    doc = load_item_doc(json_path)
    eo = doc.get("ebay_offer") or {}
    comps = eo.get("price_comps") or {}
    items = comps.get("items") or []
    before = len(items)
    kept = [ci for ci in items if ci.get("url", "").split("&mkcid")[0] != body.url.split("&mkcid")[0]]
    if len(kept) == before:
        raise HTTPException(status_code=404, detail="comp not found by url")
    # Recalculate stats from kept active items (exclude outliers/llm_dropped).
    # Use the same nearest-rank formula as ebay/pricing.py._compute_stats —
    # a separate linear-interpolation formula here made stored comps stats
    # silently shift for unrelated reasons whenever an operator edited comps.
    from .ebay.pricing import _compute_stats

    active_prices = [ci["price"] for ci in kept if not ci.get("outlier") and not ci.get("llm_dropped") and ci.get("price") is not None]
    comps["items"] = kept
    comps.update(_compute_stats(active_prices) or {"count": 0})
    eo["price_comps"] = comps
    doc["ebay_offer"] = eo
    atomic_write_json(json_path, doc, pretty=_cfg.get("pretty", True), archive_root=_cfg.get("archive_root"))
    return {"ok": True, "sku": sku, "removed": body.url, "remaining": len(kept)}


# ---------------------------------------------------------------------------
# GET /api/items/{sku}/hint-trail — identification history
# ---------------------------------------------------------------------------


@app.get("/api/items/{sku}/hint-trail", dependencies=[AUTH])
def get_hint_trail(sku: str) -> Dict[str, Any]:
    if ".." in sku:
        raise HTTPException(status_code=400, detail="invalid sku")
    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"sku not found: {sku}")
    doc = load_item_doc(json_path)
    history = doc.get("identification_history", [])
    return {"ok": True, "sku": sku, "count": len(history), "history": history}


# ---------------------------------------------------------------------------
# GET /form/intake/{sku} — mobile intake form (HTML)
# ---------------------------------------------------------------------------

_STATIC_HEAD = '<link rel="stylesheet" href="/static/tgw.css"><link rel="stylesheet" href="/static/nav.css">'
_STATIC_FOOT = '<script src="/static/tgw.js"></script><script src="/static/nav.js"></script>'


# Shared multi-mode category picker: type-to-search (local cached tree, no
# live eBay quota), type digits to resolve a raw category ID, or Browse the
# tree by drilling into branches. Used for both the primary and 2nd category.
_CATEGORY_PICKER_JS = """
function initCatPicker(cfg){
  var inp=document.getElementById(cfg.searchId);
  var hid=document.getElementById(cfg.catIdFieldId);
  var dd=document.getElementById(cfg.ddId);
  var bc=document.getElementById(cfg.bcId);
  var browseBtn=document.getElementById(cfg.browseBtnId);
  var browsePanel=document.getElementById(cfg.browsePanelId);
  if(!inp||!hid||!dd)return;
  var _timer=null, _idx=-1, _items=[], _pendingId=null;

  function selectCategory(cid,cname){
    hid.value=cid;
    if(bc)bc.textContent=cid+' \\u00b7 '+cname;
    if(typeof flagFieldInvalid==='function')flagFieldInvalid(inp,false);
    inp.value='';
    dd.style.display='none';dd.innerHTML='';
    _idx=-1;_items=[];_pendingId=null;
    if(browsePanel)browsePanel.style.display='none';
    var patch={};
    patch[cfg.idField]=cid;
    patch[cfg.nameField]=cname;
    fetch('/api/items/'+window._ITEM_SKU,{
      method:'PATCH',
      headers:authHeaders({'Content-Type':'application/json'}),
      body:JSON.stringify({fields:{draft_listing:patch}})
    }).then(function(r){return r.json();}).then(function(d){
      if(d.ok&&cfg.primary&&typeof loadCatCtx==='function')loadCatCtx(cid);
    });
  }

  function highlight(){
    Array.from(dd.querySelectorAll('.cat-opt')).forEach(function(el,i){
      el.style.background=(i===_idx)?'#2a2a3a':'';
    });
  }

  function renderResults(results){
    _items=results;_idx=-1;
    if(!results.length){dd.style.display='none';dd.innerHTML='';return;}
    var html='';
    results.forEach(function(s,i){
      html+='<div class="cat-opt" data-i="'+i+'" style="padding:6px 10px;cursor:pointer;'
        +'border-bottom:1px solid #2a2a2a;font-size:.85em">'
        +'<span style="color:#eee">'+s.name+'</span>'
        +(s.path?'<div style="color:#556;font-size:.78em">'+s.path+'</div>':'')
        +'</div>';
    });
    dd.innerHTML=html;dd.style.display='block';
    Array.from(dd.querySelectorAll('.cat-opt')).forEach(function(el){
      el.addEventListener('mouseenter',function(){_idx=parseInt(el.dataset.i,10);highlight();});
      el.addEventListener('click',function(){
        var s=_items[parseInt(el.dataset.i,10)];
        selectCategory(s.id,s.name);
      });
    });
  }

  function showError(msg){
    _items=[];_idx=-1;_pendingId=null;
    dd.innerHTML='<div style="padding:6px 10px;font-size:.82em;color:#e88">'+msg+'</div>';
    dd.style.display='block';
  }

  function renderIdConfirm(node){
    _pendingId=node;
    dd.innerHTML='<div class="cat-opt" data-idconfirm="1" style="padding:6px 10px;'
      +'cursor:pointer;font-size:.85em;background:#132213">'
      +'<span style="color:#8e8">Use category '+node.id+' \\u2014 press Enter</span>'
      +'<div style="color:#556;font-size:.78em">'+node.path
      +(node.leaf?'':' (branch, not a leaf \\u2014 eBay requires a leaf category)')+'</div>'
      +'</div>';
    dd.style.display='block';
    var el=dd.querySelector('[data-idconfirm]');
    if(el)el.addEventListener('click',function(){selectCategory(node.id,node.name);});
  }

  inp.addEventListener('input',function(){
    clearTimeout(_timer);
    var q=inp.value.trim();
    _pendingId=null;
    if(!q){dd.style.display='none';return;}
    var isId=/^[0-9]+$/.test(q);
    _timer=setTimeout(function(){
      if(isId){
        fetch('/api/ebay/category-node/'+encodeURIComponent(q),{headers:authHeaders()})
          .then(function(r){return r.json();})
          .then(function(d){
            if(d.ok)renderIdConfirm(d);
            else showError(d.detail&&d.detail.indexOf('429')>=0
              ?'eBay category lookup is rate-limited right now \\u2014 try again later'
              :(d.detail||'unknown category id'));
          })
          .catch(function(){showError('Network error');});
        return;
      }
      if(q.length<2){dd.style.display='none';return;}
      fetch('/api/ebay/category-search?q='+encodeURIComponent(q),{headers:authHeaders()})
        .then(function(r){return r.json();})
        .then(function(d){
          if(!d.ok){
            showError(d.detail&&d.detail.indexOf('429')>=0
              ?'eBay category search is rate-limited right now \\u2014 try again later'
              :'Category search failed');
            return;
          }
          renderResults(d.results||[]);
        })
        .catch(function(){showError('Network error');});
    },150);
  });

  inp.addEventListener('keydown',function(e){
    if(e.key==='ArrowDown'){
      if(!_items.length)return;
      e.preventDefault();_idx=(_idx+1)%_items.length;highlight();
    }else if(e.key==='ArrowUp'){
      if(!_items.length)return;
      e.preventDefault();_idx=(_idx-1+_items.length)%_items.length;highlight();
    }else if(e.key==='Enter'){
      e.preventDefault();
      if(_pendingId){selectCategory(_pendingId.id,_pendingId.name);return;}
      if(_idx>=0&&_items[_idx]){selectCategory(_items[_idx].id,_items[_idx].name);return;}
      if(_items.length===1)selectCategory(_items[0].id,_items[0].name);
    }else if(e.key==='Escape'){
      dd.style.display='none';
    }
  });

  document.addEventListener('click',function(e){
    if(!dd.contains(e.target)&&e.target!==inp)dd.style.display='none';
  });

  if(browseBtn&&browsePanel){
    var _stack=[];
    function renderBrowse(parentId){
      fetch('/api/ebay/category-children?parent_id='+encodeURIComponent(parentId||''),{headers:authHeaders()})
        .then(function(r){return r.json();})
        .then(function(d){
          if(!d.ok){
            browsePanel.innerHTML='<div style="padding:6px 10px;font-size:.82em;color:#e88">'
              +(d.detail&&d.detail.indexOf('429')>=0
                ?'eBay category tree is rate-limited right now \\u2014 try again later'
                :'Category browse failed')+'</div>';
            return;
          }
          var crumb='<span data-crumb-i="-1" style="cursor:pointer;text-decoration:underline;color:#8ac">Top</span>'
            +_stack.map(function(s,i){
              return ' \\u00bb <span data-crumb-i="'+i+'" style="cursor:pointer;text-decoration:underline;color:#8ac">'+s.name+'</span>';
            }).join('');
          var list=d.children.map(function(c){
            return '<div class="cat-browse-opt" data-id="'+c.id+'" data-name="'+c.name.replace(/"/g,'&quot;')+'" '
              +'data-leaf="'+(c.leaf?1:0)+'" style="padding:5px 8px;cursor:pointer;border-bottom:1px solid #2a2a2a;'
              +'font-size:.85em;display:flex;justify-content:space-between">'
              +'<span>'+c.name+'</span>'+(c.leaf?'':'<span style="color:#556">\\u203a</span>')+'</div>';
          }).join('');
          browsePanel.innerHTML='<div style="font-size:.78em;margin-bottom:4px">'+crumb+'</div>'
            +'<div style="max-height:220px;overflow-y:auto">'+(list||'<span style="color:#556;font-size:.8em">No subcategories</span>')+'</div>';
          browsePanel.querySelectorAll('[data-crumb-i]').forEach(function(el){
            el.addEventListener('click',function(){
              var i=parseInt(el.dataset.crumbI,10);
              if(i===-1){_stack=[];renderBrowse(null);}
              else{_stack=_stack.slice(0,i+1);renderBrowse(_stack[i].id);}
            });
          });
          browsePanel.querySelectorAll('.cat-browse-opt').forEach(function(el){
            el.addEventListener('click',function(){
              var cid=el.dataset.id,cname=el.dataset.name,leaf=el.dataset.leaf==='1';
              if(leaf)selectCategory(cid,cname);
              else{_stack.push({id:cid,name:cname});renderBrowse(cid);}
            });
          });
        });
    }
    browseBtn.addEventListener('click',function(){
      var isOpen=browsePanel.style.display==='block';
      if(isOpen){browsePanel.style.display='none';return;}
      dd.style.display='none';
      _stack=[];renderBrowse(null);browsePanel.style.display='block';
    });
    document.addEventListener('click',function(e){
      if(!browsePanel.contains(e.target)&&e.target!==browseBtn)browsePanel.style.display='none';
    });
  }
}
function initCatSearch(){
  initCatPicker({searchId:'dl-cat-search',catIdFieldId:'dl-cat-id',ddId:'dl-cat-dropdown',
    bcId:'dl-cat-breadcrumb',browseBtnId:'dl-cat-browse-btn',browsePanelId:'dl-cat-browse-panel',
    idField:'category_id',nameField:'category_name',primary:true});
}
function initCatSearch2(){
  initCatPicker({searchId:'dl-cat2-search',catIdFieldId:'dl-cat2-id',ddId:'dl-cat2-dropdown',
    bcId:'dl-cat2-breadcrumb',browseBtnId:'dl-cat2-browse-btn',browsePanelId:'dl-cat2-browse-panel',
    idField:'secondary_category_id',nameField:'secondary_category_name',primary:false});
}
"""

# Module-level constant — avoids nested quote hell in f-string script blocks
_CATEGORY_CONTEXT_IIFE = "function loadCatCtx(catId){\n  var prefill=window._DL_PREFILL||{};\n  var loading=document.getElementById('aspects-loading');\n  var form=document.getElementById('aspects-form');\n  if(!catId){if(loading)loading.textContent='No category.';return;}\n  var curCondSel=document.getElementById('dl-condition-select');\n  var curCondQ=curCondSel&&curCondSel.value?'?current_condition='+encodeURIComponent(curCondSel.value):'';\n  fetch('/api/ebay/category-context/'+encodeURIComponent(catId)+curCondQ,{headers:authHeaders()})\n  .then(function(r){return r.json();}).then(function(d){\n    if(!d||!d.ok){if(loading)loading.textContent='Context load failed.';return;}\n    window._CAT_CTX=d;\n    var sel=document.getElementById('dl-condition-select');\n    if(sel&&d.conditions&&d.conditions.length){\n      var curVal=sel.value;\n      var stillValid=d.conditions.some(function(c){return c.enum===curVal;});\n      var html='';\n      if(!curVal)html+='<option value=\"\" selected disabled>\\u2014 select \\u2014</option>';\n      d.conditions.forEach(function(c){\n        html+='<option value=\"'+c.enum+'\"'+(c.enum===curVal?' selected':'')+'>'+c.label+'</option>';\n      });\n      if(curVal&&!stillValid){\n        if(d.condition_remap){\n          curVal=d.condition_remap.enum;\n          html=html.replace('<option value=\"'+curVal+'\"','<option value=\"'+curVal+'\" selected');\n        }else{\n          html+='<option value=\"'+curVal+'\" selected>'+curVal+' \\u2014 not valid for this category, please fix</option>';\n        }\n      }\n      sel.innerHTML=html;\n      flagFieldInvalid(sel,!!(curVal&&!stillValid&&!d.condition_remap));\n      if(d.condition_remap&&curVal===d.condition_remap.enum){\n        fetch('/api/items/'+window._ITEM_SKU,{method:'PATCH',\n          headers:authHeaders({'Content-Type':'application/json'}),\n          body:JSON.stringify({fields:{draft_listing:{condition_enum:curVal}}})});\n      }\n      var cn=document.getElementById('condition-policy-note');\n      var nl=d.conditions.length;\n      if(cn)cn.textContent=nl+(nl===1?' condition':' conditions')+' allowed'+(d.condition_remap?' \\u2014 category changed, condition auto-matched to nearest same-or-worse: '+d.condition_remap.label:'')+((curVal&&!stillValid&&!d.condition_remap)?' \\u2014 current value invalid, please re-select':'');\n    }\n    if(d.fulfillment_policy_id){\n      var fsel=document.getElementById('dl-ship-input');\n      var fhint=document.getElementById('dl-ship-hint');\n      if(fsel&&!fsel.value){\n        for(var fi=0;fi<fsel.options.length;fi++){\n          if(fsel.options[fi].value===d.fulfillment_policy_id){fsel.value=d.fulfillment_policy_id;break;}\n        }\n        if(fsel.value){\n          fetch('/api/items/'+window._ITEM_SKU,{method:'PATCH',\n            headers:authHeaders({'Content-Type':'application/json'}),\n            body:JSON.stringify({fields:{draft_listing:{shipping_profile:fsel.value}}})});\n        }\n      }\n      if(fhint&&!fsel.value)fhint.textContent='suggested: '+d.fulfillment_policy_id;\n    }\n    if(d.store_category){\n      var sch=document.getElementById('store-cat-hint');\n      if(sch)sch.textContent='suggested: '+d.store_category;\n    }\n    if(d.group_name){\n      var gh=document.getElementById('category-group-hint');\n      if(gh){\n        var pt=d.pricing&&d.pricing.typical_used?' · typical $'+d.pricing.typical_used.toFixed(2):'';\n        var pf=d.pricing&&d.pricing.floor?' · floor $'+d.pricing.floor.toFixed(2):'';\n        gh.textContent='group: '+d.group_name+pf+pt;\n      }\n    }\n    if(loading)loading.style.display='none';\n    if(!form)return;\n    if(!d.aspects||!d.aspects.length){\n      form.innerHTML=d.aspects_error\n        ?'<span style=\"color:#e88;font-size:.82em\">Item specifics lookup failed (\\u2018'+d.aspects_error+'\\u2019) \\u2014 every eBay category has specifics; this is a lookup error, not an empty category. <a href=\"#\" onclick=\"loadCatCtx(\\''+catId+'\\');return false\" style=\"color:#8ac\">Retry</a></span>'\n        :'<span style=\"color:#556;font-size:.82em\">No specifics returned for this category \\u2014 unexpected, please verify manually</span>';\n      return;\n    }\n    var html='';\n    d.aspects.forEach(function(asp){\n      var badge=asp.required\n        ?'<span style=\"font-size:.7em;background:#3a1a1a;color:#c44;border-radius:3px;padding:1px 5px;margin-left:4px\">REQ</span>'\n        :'<span style=\"font-size:.7em;background:#2a2a0a;color:#aa0;border-radius:3px;padding:1px 5px;margin-left:4px\">REC</span>';\n      // Three-layer merge: operator edits (blue) > proposed (yellow) > live (baseline)\n      var liveVal=(window._LIVE_ASPECTS&&window._LIVE_ASPECTS[asp.name]!==undefined?(window._LIVE_ASPECTS[asp.name]||'').toString():'');\n      var proposedVal=(window._PROPOSED_ASPECTS&&window._PROPOSED_ASPECTS[asp.name]!==undefined?(window._PROPOSED_ASPECTS[asp.name]||'').toString():'');\n      var editVal=(prefill[asp.name]!==undefined?(prefill[asp.name]||'').toString():'');\n      var cur,layer;\n      if(editVal){cur=editVal;layer=(editVal!==liveVal)?'edit':'same';}\n      else if(proposedVal){cur=proposedVal;layer=(proposedVal!==liveVal)?'proposed':'same';}\n      else{cur=liveVal;layer=liveVal?'live':'empty';}\n      cur=cur.replace(/\"/g,'&quot;');\n      var reqEmpty=asp.required&&!cur;\n      // Colours by layer\n      var bord=reqEmpty?'#c44':(layer==='edit'?'#44c':(layer==='proposed'?'#884':'#444'));\n      var bg=reqEmpty?'#1a0a0a':(layer==='edit'?'#0a0a1a':(layer==='proposed'?'#1a1a00':'#1a1a1a'));\n      // Hint: show live value when overridden or proposed differs\n      var liveHint=(layer==='edit'||layer==='proposed')&&liveVal\n        ?'<div style=\"font-size:.7em;color:#445;margin-top:1px\">live: '+liveVal.replace(/</g,'&lt;').replace(/>/g,'&gt;')+'</div>'\n        :'';\n      var inp;\n      if(asp.allowed_values&&asp.allowed_values.length&&asp.mode==='SELECTION_ONLY'){\n        var offList=cur&&asp.allowed_values.indexOf(cur)===-1;\n        var opts=asp.allowed_values.map(function(v){\n          return '<option value=\"'+v+'\"'+(v===cur?' selected':'')+'>'+v+'</option>';\n        }).join('');\n        if(offList)opts='<option value=\"'+cur+'\" selected>'+cur+' \\u2014 not in this category\\u2019s list, please verify</option>'+opts;\n        inp='<select data-aspect=\"'+asp.name+'\" data-initial=\"'+cur+'\" style=\"background:'+bg+';color:#eee;border:1px solid '+(offList?'#c84':bord)+';border-radius:3px;padding:2px 5px;font-size:.85em\"><option value=\"\">—</option>'+opts+'</select>';\n      }else{\n        var dlid='dl-asp-'+asp.name.replace(/[^a-zA-Z0-9]/g,'-');\n        var dlopts=asp.allowed_values&&asp.allowed_values.length\n          ?'<datalist id=\"'+dlid+'\">'+asp.allowed_values.map(function(v){return '<option value=\"'+v+'\"></option>';}).join('')+'</datalist>'\n          :'';\n        inp='<input type=\"text\"'+(dlopts?' list=\"'+dlid+'\"':'')+' data-aspect=\"'+asp.name+'\" data-initial=\"'+cur+'\" value=\"'+cur+'\"'\n           +' style=\"background:'+bg+';color:#eee;border:1px solid '+bord+';border-radius:3px;padding:2px 5px;font-size:.85em;width:200px\">'\n           +dlopts;\n      }\n      html+='<div class=\"frow\"'+(reqEmpty?' style=\"border-left:2px solid #944;padding-left:4px\"':'')+'>  <span class=\"fn\" style=\"font-size:.82em\">'+asp.name+badge+'</span><span class=\"fv\">'+inp+liveHint+'</span></div>';\n    });\n    var covered={};\n    d.aspects.forEach(function(asp){covered[asp.name]=true;});\n    Object.keys(prefill).forEach(function(name){\n      if(covered[name])return;\n      var xcur=(prefill[name]||'').toString().replace(/\"/g,'&quot;');\n      var xkey=name.replace(/\"/g,'&quot;');\n      var xbadge='<span style=\"font-size:.7em;background:#1a2a3a;color:#8ac;border-radius:3px;padding:1px 5px;margin-left:4px\" title=\"A seller-defined custom aspect \u2014 not in this category\u2019s standard list, but a real eBay field, pushed live like any other\">CUSTOM ASPECT</span>';\n      var xcb='<input type=\"checkbox\" class=\"aspect-keep-cb\" data-aspect-key=\"'+xkey+'\" checked title=\"Checked = keep on this eBay listing. Uncheck = discard at Save (moved to the Inventory Record as a superset, never deleted).\" style=\"margin-right:4px;vertical-align:middle\">';\n      var xinp='<input type=\"text\" data-aspect=\"'+name+'\" data-initial=\"'+xcur+'\" value=\"'+xcur+'\" style=\"background:#1a1a2a;color:#eee;border:1px solid #446;border-radius:3px;padding:2px 5px;font-size:.85em;width:200px\">';\n      html+='<div class=\"frow\"><span class=\"fn\" style=\"font-size:.82em\">'+xcb+name+xbadge+'</span><span class=\"fv\">'+xinp+'</span></div>';\n    });\n\n    var missingReq=d.aspects.filter(function(a){return a.required&&!(prefill[a.name]||'');}).length;if(missingReq>0){html='<div style=\"margin-bottom:8px;padding:5px 8px;background:#1a0808;border:1px solid #844;border-radius:3px;font-size:.78em;color:#e88\">'+missingReq+' required aspect'+(missingReq===1?'':'s')+' missing values — fill before staging</div>'+html;}form.innerHTML=html;\n  }).catch(function(){\n    if(loading)loading.textContent='Category context load failed.';\n  });\n}\ndocument.addEventListener('DOMContentLoaded',function(){\n  if(window._DL_CAT_ID)loadCatCtx(window._DL_CAT_ID);\n  if(typeof initCatSearch==='function')initCatSearch();\n  if(typeof initCatSearch2==='function')initCatSearch2();\n  if(typeof loadInventoryDiff==='function')loadInventoryDiff();\n});\n"

# The category-context script predates Python formatting and is deliberately a
# single quoted constant. Apply validation additions as exact substitutions so
# missing required data has both a visible state and an accessible DOM state.
_CATEGORY_CONTEXT_IIFE = (
    _CATEGORY_CONTEXT_IIFE.replace(
        "if(!catId){if(loading)loading.textContent='No category.';return;}",
        "if(!catId){flagFieldInvalid('dl-cat-search',true);if(loading){loading.textContent='Category required before item specifics can be checked.';loading.style.color='#e88';}return;}",
    )
    .replace(
        """      if(curVal&&!stillValid){
        if(d.condition_remap){
          curVal=d.condition_remap.enum;
          html=html.replace('<option value="'+curVal+'"','<option value="'+curVal+'" selected');
        }else{
          html+='<option value="'+curVal+'" selected>'+curVal+' \\u2014 not valid for this category, please fix</option>';
        }
      }
      sel.innerHTML=html;
      flagFieldInvalid(sel,!!(curVal&&!stillValid&&!d.condition_remap));
      if(d.condition_remap&&curVal===d.condition_remap.enum){
        fetch('/api/items/'+window._ITEM_SKU,{method:'PATCH',
          headers:authHeaders({'Content-Type':'application/json'}),
          body:JSON.stringify({fields:{draft_listing:{condition_enum:curVal}}})});
      }
      var cn=document.getElementById('condition-policy-note');
      var nl=d.conditions.length;
      if(cn)cn.textContent=nl+(nl===1?' condition':' conditions')+' allowed'+(d.condition_remap?' \\u2014 category changed, condition auto-matched to nearest same-or-worse: '+d.condition_remap.label:'')+((curVal&&!stillValid&&!d.condition_remap)?' \\u2014 current value invalid, please re-select':'');""",
        """      if(curVal&&!stillValid){
        html+='<option value="'+curVal+'" selected disabled>'+curVal+' \\u2014 not valid for this category, please fix</option>';
      }
      sel.innerHTML=html;
      flagFieldInvalid(sel,!!(curVal&&!stillValid));
      var cn=document.getElementById('condition-policy-note');
      var nl=d.conditions.length;
      if(cn)cn.textContent=nl+(nl===1?' condition':' conditions')+' allowed'+(d.condition_remap?' \\u2014 condition remap available: '+d.condition_remap.label+'; select it explicitly':'')+((curVal&&!stillValid)?' \\u2014 current value invalid, please re-select or clear':'');""",
    )
    .replace(
        "var html='';\n    d.aspects.forEach",
        "var html='';var missingReq=0;\n    d.aspects.forEach",
    )
    .replace(
        "var reqEmpty=asp.required&&!cur;",
        "var reqEmpty=asp.required&&!cur;if(reqEmpty)missingReq++;",
    )
    .replace(
        "<select data-aspect=\"'+asp.name+'\" data-initial=\"'+cur+'\"",
        "<select data-aspect=\"'+asp.name+'\" data-required=\"'+(asp.required?'true':'false')+'\" aria-invalid=\"'+(reqEmpty?'true':'false')+'\" data-initial=\"'+cur+'\"",
    )
    .replace(
        " data-aspect=\"'+asp.name+'\" data-initial=\"'+cur+'\" value=\"'+cur+'\"",
        " data-aspect=\"'+asp.name+'\" data-required=\"'+(asp.required?'true':'false')+'\" aria-invalid=\"'+(reqEmpty?'true':'false')+'\" data-initial=\"'+cur+'\" value=\"'+cur+'\"",
    )
    .replace(
        "inp='<input type=\"text\"'+(dlopts?' list=\"'+dlid+'\"':'')+' data-aspect=\"'+asp.name+'\"",
        "var maxAttr=asp.max_length?' maxlength=\"'+asp.max_length+'\"':'';"
        "var maxHint=asp.max_length?' <span style=\"font-size:.7em;color:#888\">max '+asp.max_length+'</span>':'';"
        "inp='<input type=\"text\"'+(dlopts?' list=\"'+dlid+'\"':'')+maxAttr+' data-aspect=\"'+asp.name+'\"",
    )
    .replace(
        "+dlopts;\n      }\n      html+='<div class=\"frow\"'+(reqEmpty",
        "+dlopts+maxHint;\n      }\n      html+='<div class=\"frow\"'+(reqEmpty",
    )
    .replace(
        "var missingReq=d.aspects.filter(function(a){return a.required&&!(prefill[a.name]||'');}).length;if(missingReq>0)",
        "if(missingReq>0)",
    )
    .replace(
        "form.innerHTML=html;\n  }).catch(function(){",
        "form.innerHTML=html;"
        "if(window._PE_FIELD){form.querySelectorAll('[data-aspect]').forEach(function(el){"
        "if(el.dataset.aspect===window._PE_FIELD){flagFieldInvalid(el,true);el.title=window._PE_DETAIL||'';}});}"
        "\n  }).catch(function(){",
    )
)

# ---------------------------------------------------------------------------
# GET /form/intake — intake landing page (HTML)
# ---------------------------------------------------------------------------

_INTAKE_LANDING_CSS = (
    ".skuinput-row{display:flex;gap:6px;margin-top:4px}"
    ".skuinput-row input{flex:1;margin:0}"
    ".btn-sm{padding:10px 16px;background:#1a4a8a;color:#fff;border:none;border-radius:6px;"
    " cursor:pointer;font-size:.9em;white-space:nowrap;flex-shrink:0}"
    ".btn-sm:active{background:#143a6a}"
    ".scan-hint{font-size:.78em;color:#666;margin:4px 0 0}"
    ".section{margin-bottom:16px}"
    ".section-label{font-size:.75em;text-transform:uppercase;letter-spacing:.08em;color:#666;margin-bottom:6px}"
    ".recent-list{list-style:none;margin:0;padding:0}"
    ".recent-item{display:flex;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid #1e1e1e}"
    ".recent-item:last-child{border-bottom:none}"
    ".ri-sku{font-family:monospace;font-size:.78em;color:#7fbfff;text-decoration:none;flex-shrink:0}"
    ".ri-sku:hover{color:#bdf}"
    ".ri-title{font-size:.82em;color:#ccc;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
    ".ri-status{font-size:.72em;padding:2px 7px;border-radius:10px;white-space:nowrap;flex-shrink:0}"
    ".st-instock{background:#1e2e1e;color:#9c9;border:1px solid #2a4a2a}"
    ".st-inprogress{background:#1a2a4a;color:#9af;border:1px solid #2a4a7a}"
    ".st-ready{background:#1a4a1a;color:#7f7;border:1px solid #2a6a2a}"
    ".st-staged{background:#1a3a4a;color:#7cf;border:1px solid #2a5a7a}"
    ".st-listed{background:#2a1a4a;color:#c9f;border:1px solid #4a2a7a}"
    ".st-other{background:#2a2a2a;color:#888;border:1px solid #444}"
    ".inprog-link{display:block;margin-top:10px;font-size:.85em;color:#7fbfff;text-decoration:none}"
    ".inprog-link:hover{color:#bdf}"
)

_INTAKE_LANDING_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TGW Intake</title>
{static_head}
<style>{landing_css}</style>
</head>
<body>
<h2>Intake</h2>

<div class="section">
  <label>SKU / Barcode</label>
  <div class="skuinput-row">
    <input id="sku-input" type="text" placeholder="Enter SKU or scan barcode…"
           autocomplete="off" spellcheck="false" inputmode="text">
    <button class="btn-sm" onclick="goIntake()">Open</button>
  </div>
  <div class="scan-hint">Scan the barcode label to jump directly to the intake form.</div>
</div>

<div class="section">
  <div class="section-label">Recent Intakes</div>
  <div id="recent-list"><span style="color:#555;font-size:.85em">Loading…</span></div>
  <a class="inprog-link" href="/form/items">View all inventory →</a>
</div>

{static_foot}
<script>
window.TGW_API_KEY = {api_key_json};

document.getElementById('sku-input').addEventListener('keydown', function(e) {{
  if (e.key === 'Enter') goIntake();
}});
document.getElementById('sku-input').focus();

function goIntake() {{
  var sku = document.getElementById('sku-input').value.trim();
  if (sku) window.location = '/form/intake/' + encodeURIComponent(sku);
}}

function statusCls(st) {{
  var m = {{
    'In Stock': 'st-instock',
    'In Progress': 'st-inprogress',
    'Ready': 'st-ready',
    'Staged': 'st-staged',
    'Listed': 'st-listed',
  }};
  return m[st] || 'st-other';
}}

async function loadRecent() {{
  var el = document.getElementById('recent-list');
  try {{
    var r = await fetch('/api/items?limit=20', {{headers: authHeaders()}});
    if (!r.ok) {{ el.innerHTML = '<span style="color:#f77;font-size:.85em">Failed to load.</span>'; return; }}
    var d = await r.json();
    var items = d.items || [];
    if (!items.length) {{
      el.innerHTML = '<span style="color:#555;font-size:.85em">No items found.</span>';
      return;
    }}
    var html = '<ul class="recent-list">';
    items.forEach(function(it) {{
      var st = it.status || '';
      var cls = statusCls(st);
      html += '<li class="recent-item">' +
        '<a class="ri-sku" href="/form/intake/' + encodeURIComponent(it.sku) + '">' +
        escapeHtml(it.sku.slice(-12)) + '</a>' +
        '<span class="ri-title">' + escapeHtml(it.title || '(no title)') + '</span>' +
        '<span class="ri-status ' + cls + '">' + escapeHtml(st || '—') + '</span>' +
        '</li>';
    }});
    el.innerHTML = html + '</ul>';
  }} catch(e) {{
    el.innerHTML = '<span style="color:#f77;font-size:.85em">Error: ' + escapeHtml(e.message) + '</span>';
  }}
}}

loadRecent();
</script>
</body>
</html>
"""


@app.get("/form/intake")
def intake_landing():
    """Intake landing page — SKU/barcode entry, recent intakes list. No Bearer auth."""
    from fastapi.responses import HTMLResponse

    html = _INTAKE_LANDING_HTML.format(
        static_head=_STATIC_HEAD,
        static_foot=_STATIC_FOOT,
        landing_css=_INTAKE_LANDING_CSS,
        api_key_json=json.dumps(""),
    )
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# Intake form extra CSS (passed as format arg to avoid escaping CSS braces)
# ---------------------------------------------------------------------------

_INTAKE_FORM_EXTRA_CSS = (
    ".item-badges{display:flex;gap:6px;margin-bottom:10px;flex-wrap:wrap}"
    ".badge{font-size:.75em;padding:3px 9px;border-radius:10px}"
    ".badge-photo{background:#1a2a3a;color:#7af;border:1px solid #2a4a6a}"
    ".badge-photo-warn{background:#3a2a0a;color:#fb7;border:1px solid #6a4a10}"
    ".badge-status{background:#1e2a1e;color:#9c9;border:1px solid #2a4a2a}"
    ".badge-job{background:#1a2a4a;color:#aac;border:1px solid #2a3a6a;"
    " font-family:monospace;font-size:.7em}"
    ".badge-job.active{background:#2a3a0a;color:#cf7;border-color:#4a6a10}"
    ".badge-job.err{background:#3a1a1a;color:#f99;border-color:#5a2a2a}"
    ".action-btns{display:flex;gap:8px;margin-top:6px}"
    ".btn-action{flex:1;padding:12px 8px;background:#1a3a5a;color:#adf;"
    " border:2px solid #2a5a8a;border-radius:6px;cursor:pointer;font-size:.9em}"
    ".btn-action:active{background:#0a2a4a}"
    ".btn-action:disabled{opacity:.4;cursor:not-allowed}"
    ".detail-link{color:#7fbfff;font-size:.88em;text-decoration:none}"
    ".detail-link:hover{color:#bdf}"
    ".lb-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.88);z-index:9999;"
    "  align-items:center;justify-content:center;cursor:zoom-out}"
    ".lb-overlay.open{display:flex}"
    ".lb-img{max-width:92vw;max-height:88vh;object-fit:contain;border-radius:6px;"
    "  box-shadow:0 4px 32px rgba(0,0,0,.8)}"
    ".lb-close{position:absolute;top:14px;right:18px;background:none;border:none;"
    "  color:#aaa;font-size:1.8em;cursor:pointer;line-height:1}"
    ".lb-close:hover{color:#fff}"
    ".video-strip{margin-top:8px}"
    ".video-strip-hdr{font-size:.65em;text-transform:uppercase;letter-spacing:.08em;"
    "  color:#fb7;background:#2a1a00;border:1px solid #5a3a00;border-radius:4px;"
    "  display:inline-block;padding:1px 6px;margin-bottom:4px}"
    ".strip-vid{width:60px;height:45px;object-fit:cover;border-radius:4px;cursor:pointer;"
    "  border:2px solid #5a3a00;background:#111}"
    ".strip-vid:hover{border-color:#fb7}"
    ".video-item{display:inline-block;position:relative}"
)


_INTAKE_FORM_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Intake: {sku_short}</title>
{static_head}
<style>{intake_extra_css}</style>
</head>
<body>
<h2>Intake Form</h2>
<div class="sku">{sku}</div>

<div class="item-badges">
  <span id="photo-badge" class="badge {photo_cls}">{n_photos} photo{photo_plural}</span>
  <span id="status-badge" class="badge badge-status">{item_status_disp}</span>
  <span id="job-badge" class="badge badge-job" style="display:none"></span>
</div>

<label>Template</label>
<div class="chips" id="chips">{chips_html}</div>
<input type="hidden" id="tpl_key" value="{current_template}">

<div class="field-row">
  <div>
    <label>Weight (oz)</label>
    <input type="number" id="weight_oz" step="0.1" min="0" placeholder="e.g. 4.5"
           value="{weight_oz}">
  </div>
  <div>
    <label>Barcode</label>
    <input type="text" id="barcode" inputmode="numeric" placeholder="UPC / EAN / ISBN"
           value="{barcode}">
  </div>
</div>

<label>AI Hint</label>
<input type="text" id="ai_hint" placeholder="brief item description for AI"
       value="{ai_hint}">

<label>Condition</label>
<select id="condition">
  {condition_options}
</select>

<label>Pipeline Actions</label>
<div class="action-btns">
  <button class="btn-action" id="btn-identify" onclick="triggerAction('ai_identify')">{identify_label}</button>
</div>
<div class="msg" id="action-msg"></div>

<button class="btn" onclick="submitForm()">Save</button>
<div style="margin-top:10px;text-align:center">
  <a class="detail-link" href="/form/operator/items/{sku}">View detail &rarr;</a>
</div>
<div class="msg" id="msg"></div>

{static_foot}
<script>
const SKU = {sku_json};
const API = '/api/items/' + SKU;
const AUTH = 'Bearer {api_key}';
window.TGW_API_KEY = {api_key_json};

initChips('#chips', c => {{ document.getElementById('tpl_key').value = c.dataset.key; }});

var pollTimer = null;
var TERMINAL = new Set(['succeeded', 'dead_letter', 'failed', 'cancelled']);

function updateBadges(item) {{
  if (!item) return null;
  var jobs = item._queue_jobs || [];
  var latest = jobs[0] || null;
  var pb = document.getElementById('photo-badge');
  var n = (item._images || []).length;
  pb.textContent = n + (n === 1 ? ' photo' : ' photos');
  pb.className = 'badge ' + (n === 0 ? 'badge-photo-warn' : 'badge-photo');
  var sb = document.getElementById('status-badge');
  sb.textContent = item.status || '—';
  var jb = document.getElementById('job-badge');
  if (latest) {{
    jb.style.display = '';
    jb.textContent = latest.queue_name + ': ' + latest.state;
    jb.className = 'badge badge-job';
    if (!TERMINAL.has(latest.state)) {{ jb.classList.add('active'); }}
    else if (latest.state === 'dead_letter' || latest.state === 'failed') {{ jb.classList.add('err'); }}
  }} else {{
    jb.style.display = 'none';
  }}
  return {{jobs: jobs, latest: latest}};
}}

async function fetchItem() {{
  try {{
    var r = await fetch(API, {{headers: authHeaders()}});
    if (!r.ok) return null;
    var d = await r.json();
    return d.ok ? d.item : null;
  }} catch(e) {{ return null; }}
}}

function startPolling() {{
  if (pollTimer) return;
  pollTimer = setInterval(async function() {{
    var item = await fetchItem();
    var result = updateBadges(item);
    if (!result || !result.latest || TERMINAL.has(result.latest.state)) {{
      clearInterval(pollTimer);
      pollTimer = null;
    }}
  }}, 5000);
}}

async function triggerAction(action) {{
  var msg = document.getElementById('action-msg');
  msg.className = 'msg';
  msg.textContent = '';
  var bi = document.getElementById('btn-identify');
  bi.disabled = true;
  try {{
    var r = await fetch(API + '/action', {{
      method: 'POST',
      headers: {{'Authorization': AUTH, 'Content-Type': 'application/json'}},
      body: JSON.stringify({{action: action}})
    }});
    var d = await r.json().catch(function() {{ return {{}}; }});
    if (r.ok && d.ok) {{
      msg.className = 'msg ok';
      msg.textContent = action + ' queued ✔';
      startPolling();
    }} else {{
      msg.className = 'msg err';
      msg.textContent = d.detail || ('action failed: ' + r.status);
    }}
  }} catch(e) {{
    msg.className = 'msg err';
    msg.textContent = 'Network error: ' + e.message;
  }} finally {{
    bi.disabled = false;
  }}
}}

fetchItem().then(function(item) {{
  var result = updateBadges(item);
  if (result && result.latest && !TERMINAL.has(result.latest.state)) {{
    startPolling();
  }}
}});

async function submitForm() {{
  const msg = document.getElementById('msg');
  msg.className = 'msg';
  msg.textContent = '';

  const tpl = document.getElementById('tpl_key').value;
  const w   = document.getElementById('weight_oz').value;
  const bc  = document.getElementById('barcode').value.trim();
  const hnt = document.getElementById('ai_hint').value.trim();
  const cnd = document.getElementById('condition').value;

  if (tpl && tpl !== {current_template_json}) {{
    const r = await fetch('/api/items/' + SKU + '/set-template', {{
      method: 'POST',
      headers: {{'Authorization': AUTH, 'Content-Type': 'application/json'}},
      body: JSON.stringify({{template_key: tpl}})
    }});
    if (!r.ok) {{
      const e = await r.json().catch(() => ({{}}));
      msg.className = 'msg err';
      msg.textContent = 'Template error: ' + (e.detail || r.status);
      return;
    }}
  }}

  const fields = {{}};
  if (w)   fields.weight_oz = parseFloat(w);
  if (bc)  fields.barcode = bc;
  if (hnt) fields.ai_hint = hnt;
  if (cnd) fields.condition = cnd;

  if (Object.keys(fields).length > 0) {{
    const r = await fetch(API, {{
      method: 'PATCH',
      headers: {{'Authorization': AUTH, 'Content-Type': 'application/json'}},
      body: JSON.stringify({{fields}})
    }});
    if (!r.ok) {{
      const e = await r.json().catch(() => ({{}}));
      msg.className = 'msg err';
      msg.textContent = 'Save error: ' + (e.detail || r.status);
      return;
    }}
  }}

  msg.className = 'msg ok';
  msg.textContent = 'Saved ✔';
}}
</script>
</body>
</html>
"""

_CONDITIONS = ["", "New", "Like New", "Very Good", "Good", "Acceptable"]


@app.get("/form/intake/{sku}")
def intake_form(sku: str, request: Request):
    """Mobile-friendly intake form — no Bearer auth, relies on network trust."""
    import html as _html

    from fastapi.responses import HTMLResponse

    if ".." in sku:
        raise HTTPException(status_code=400, detail="invalid sku")

    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        from .resolver import find_current_sku

        current = find_current_sku(_cfg, sku)
        if current:
            json_path = _cfg["itemdata_root"] / current / f"{current}.json"
            sku = current
        else:
            return HTMLResponse(f"<h2>SKU not found: {_html.escape(sku)}</h2>", status_code=404)

    doc = load_item_doc(json_path)

    groups_path = _cfg.get("category_groups_path")
    groups: Dict[str, Any] = {}
    if groups_path and Path(groups_path).exists():
        raw = json.loads(Path(groups_path).read_text(encoding="utf-8"))
        groups = raw.get("groups", {})

    current_template = doc.get("category_group", "")

    chips_html = ""
    for key, grp in groups.items():
        active = "active" if key == current_template else ""
        name = grp.get("name", key)
        chips_html += f'<button class="chip {active}" data-key="{key}">{name}</button>'

    cond_val = doc.get("condition", "")
    cond_opts = ""
    for c in _CONDITIONS:
        sel = "selected" if c == cond_val else ""
        cond_opts += f'<option value="{c}" {sel}>{c if c else "— not set —"}</option>'

    weight = _html.escape(str(doc.get("weight_oz", "")))
    barcode = _html.escape(str(doc.get("barcode", doc.get("upc", ""))))
    ai_hint = _html.escape(str(doc.get("ai_hint", "")))
    sku_short = sku[-9:]

    # Photo count from filesystem (server-side; JS keeps it live via polling)
    sku_dir = json_path.parent
    n_photos = sum(1 for p in sku_dir.iterdir() if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp"})
    photo_cls = "badge-photo-warn" if n_photos == 0 else "badge-photo"
    photo_plural = "" if n_photos == 1 else "s"

    item_status = doc.get("status", "")
    item_status_disp = item_status or "Unknown"
    identify_label = "Re-identify" if doc.get("ai_identified") else "Start Identify"

    html = _INTAKE_FORM_HTML.format(
        sku=sku,
        sku_short=sku_short,
        sku_json=json.dumps(sku),
        static_head=_STATIC_HEAD,
        static_foot=_STATIC_FOOT,
        intake_extra_css=_INTAKE_FORM_EXTRA_CSS,
        chips_html=chips_html,
        current_template=current_template,
        current_template_json=json.dumps(current_template),
        weight_oz=weight,
        barcode=barcode,
        ai_hint=ai_hint,
        condition_options=cond_opts,
        api_key="",
        api_key_json=json.dumps(""),
        n_photos=n_photos,
        photo_cls=photo_cls,
        photo_plural=photo_plural,
        item_status_disp=item_status_disp,
        identify_label=identify_label,
    )
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# GET /form/bulk — tablet-first bulk editor (HTML, filter → preview → apply)
# ---------------------------------------------------------------------------

_BULK_EXTRA_CSS = """
table{width:100%;border-collapse:collapse;margin-top:10px;font-size:.8em}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #333}
th{color:#aaa}
"""

_BULK_FORM_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TGW Bulk Edit</title>
{static_head}
<style>{extra_css}</style>
</head>
<body>
<h2>Bulk Edit</h2>
<div class="sku">filter → preview → apply</div>

<label>Field to set</label>
<div class="chips" id="fields">
  <button class="chip active" data-f="title">title</button>
  <button class="chip" data-f="location">location</button>
  <button class="chip" data-f="status">status</button>
  <button class="chip" data-f="ai_hint">ai_hint</button>
  <button class="chip" data-f="shipping_profile">shipping_profile</button>
</div>
<input type="hidden" id="field" value="title">

<label>New value</label>
<input type="text" id="value" placeholder="value to write to every matched item">

<div class="field-row">
  <div><label>Filter: location</label><input type="text" id="f_location" placeholder="e.g. SHELF01"></div>
  <div><label>Filter: status</label><input type="text" id="f_status" placeholder="e.g. In Stock"></div>
</div>
<label>Filter: search text</label>
<input type="text" id="f_search" placeholder="free-text substring (matches any field)">
<label>Limit (0 = all matched)</label>
<input type="number" id="f_limit" value="0" min="0">

<button class="btn" onclick="preview()">Preview</button>
<div class="msg" id="msg"></div>
<div id="results"></div>
<button class="btn" id="applyBtn" style="display:none;background:#7a3a10" onclick="apply()">
  Apply to matched items</button>

{static_foot}
<script>
const AUTH = 'Bearer {api_key}';
initChips('#fields', c => {{ document.getElementById('field').value = c.dataset.f; }});

function body() {{
  const b = {{
    field: document.getElementById('field').value,
    value: document.getElementById('value').value,
    limit: parseInt(document.getElementById('f_limit').value || '0', 10),
  }};
  const loc = document.getElementById('f_location').value.trim();
  const st  = document.getElementById('f_status').value.trim();
  const sr  = document.getElementById('f_search').value.trim();
  if (loc) b.location = loc;
  if (st)  b.status = st;
  if (sr)  b.search = sr;
  return b;
}}

async function post(url) {{
  return fetch(url, {{
    method: 'POST',
    headers: {{'Authorization': AUTH, 'Content-Type': 'application/json'}},
    body: JSON.stringify(body())
  }});
}}

async function preview() {{
  const msg = document.getElementById('msg');
  const res = document.getElementById('results');
  document.getElementById('applyBtn').style.display = 'none';
  msg.className = 'msg'; msg.textContent = 'Loading…'; msg.style.display = 'block';
  res.innerHTML = '';
  const r = await post('/api/bulk/preview');
  const d = await r.json().catch(() => ({{}}));
  if (!r.ok || d.ok === false) {{
    msg.className = 'msg err';
    msg.textContent = 'Error: ' + (d.detail || d.error || r.status);
    return;
  }}
  msg.style.display = 'none';
  if (!d.count) {{ res.innerHTML = '<p>No items matched.</p>'; return; }}
  let html = '<p><b>' + d.count + '</b> item(s) would get <b>' + d.field +
             '</b> = <b>' + escapeHtml(d.value) + '</b></p><table><tr>' +
             '<th>SKU</th><th>Current</th><th>Title</th></tr>';
  d.preview.forEach(p => {{
    html += '<tr><td>' + p.sku + '</td><td>' + escapeHtml(String(p.current)) +
            '</td><td>' + escapeHtml(p.title) + '</td></tr>';
  }});
  res.innerHTML = html + '</table>';
  document.getElementById('applyBtn').style.display = 'block';
}}

async function apply() {{
  const msg = document.getElementById('msg');
  msg.className = 'msg'; msg.textContent = 'Applying…'; msg.style.display = 'block';
  const r = await post('/api/bulk/apply');
  const d = await r.json().catch(() => ({{}}));
  if (!r.ok || d.ok === false) {{
    msg.className = 'msg err';
    msg.textContent = 'Error: ' + (d.detail || d.error || r.status);
    return;
  }}
  msg.className = 'msg ok';
  msg.textContent = 'Applied to ' + d.count + ' item(s)' +
                    (d.failed && d.failed.length ? ' — ' + d.failed.length + ' failed' : '') + ' ✔';
  document.getElementById('applyBtn').style.display = 'none';
}}
</script>
</body>
</html>
"""


@app.get("/form/bulk")
def bulk_form(request: Request):
    """Tablet-first bulk editor — no Bearer auth on the page (network trust);
    the embedded JS calls the authenticated /api/bulk/* endpoints."""
    from fastapi.responses import HTMLResponse

    html = _BULK_FORM_HTML.format(
        static_head=_STATIC_HEAD,
        static_foot=_STATIC_FOOT,
        extra_css=_BULK_EXTRA_CSS,
        api_key="",
    )
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# GET /form/todos — tablet-first open-todo dashboard (PP-TODO-001, Round 4 #34)
# ---------------------------------------------------------------------------

_TODOS_EXTRA_CSS = """
table{width:100%;border-collapse:collapse;margin:2px 0 16px}
th,td{text-align:left;padding:8px 6px;border-bottom:1px solid #333;font-size:.9em;vertical-align:top}
th{color:#aaa;font-size:.72em;text-transform:uppercase;letter-spacing:.04em}
td.id{color:#4a8ade;font-variant-numeric:tabular-nums;white-space:nowrap}
td.p{color:#caa;font-variant-numeric:tabular-nums;white-space:nowrap}
td.src{color:#777;font-size:.8em;white-space:nowrap}
.agent{display:flex;align-items:baseline;gap:8px;margin:18px 0 2px}
.agent h3{margin:0;font-size:1em;color:#7fbfff;text-transform:capitalize}
.agent .count{font-size:.8em;color:#aaa}
.allclear{margin-top:24px;padding:16px;border-radius:8px;background:#1a4a1a;color:#7f7;text-align:center;font-size:1.05em}
.total{font-size:.82em;color:#999;margin-bottom:6px}
.todos-ctrl{display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap}
.todos-ctrl label{font-size:.85em;color:#aaa;margin:0}
.todos-ctrl select{width:auto;padding:6px 10px;font-size:.85em}
.task-body{max-height:2.8em;overflow:hidden;cursor:pointer;line-height:1.4;
  transition:max-height .15s}
.task-body.expanded{max-height:none}
.task-expand{color:#555;font-size:.78em;cursor:pointer;user-select:none;display:none}
.task-expand.visible{display:inline}
.copy-btn{background:#1e1e1e;border:1px solid #444;color:#aaa;border-radius:4px;
  padding:2px 8px;cursor:pointer;font-size:.78em;white-space:nowrap;font-family:inherit}
.copy-btn:hover{background:#2a2a2a;color:#eee}
.copy-btn.copied{color:#7f7;border-color:#3a6a3a}
.pp-badge{display:inline-block;padding:1px 6px;background:#1a2a4a;color:#7af;border-radius:4px;
  font-size:.72em;text-decoration:none;margin:0 4px 2px 0;white-space:nowrap}
.pp-badge:hover{background:#1a3a6a}
"""


_TODOS_JS = """
<script>
(function() {
  // Agent filter
  var sel = document.getElementById('agent-sel');
  if (sel) sel.addEventListener('change', function() {
    var v = sel.value;
    document.querySelectorAll('[data-agent]').forEach(function(el) {
      el.style.display = (v === '' || el.dataset.agent === v) ? '' : 'none';
    });
  });

  // Click-to-expand task body; show expand toggle for long bodies
  document.querySelectorAll('.task-body').forEach(function(el) {
    var full = el.textContent;
    // If text is taller than its max-height, show it as truncated with toggle
    if (el.scrollHeight > el.clientHeight + 4) {
      var tog = el.nextElementSibling;
      if (tog && tog.classList.contains('task-expand')) {
        tog.classList.add('visible');
        tog.textContent = '▸ more';
      }
      el.addEventListener('click', function() {
        var expanded = el.classList.toggle('expanded');
        if (tog) tog.textContent = expanded ? '▴ less' : '▸ more';
      });
    }
  });

  // Copy-to-clipboard buttons
  document.querySelectorAll('.copy-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var text = btn.dataset.body || '';
      navigator.clipboard.writeText(text).then(function() {
        btn.textContent = 'Copied!'; btn.classList.add('copied');
        setTimeout(function() { btn.textContent = '📋'; btn.classList.remove('copied'); }, 1500);
      }).catch(function() {
        btn.textContent = '!'; setTimeout(function() { btn.textContent = '📋'; }, 1200);
      });
    });
  });
})();
</script>
"""


def _extract_pp_refs(body: str) -> "list[str]":
    """Extract PP-XXX-NNN references from a todo body string."""
    return list(dict.fromkeys(re.findall(r"PP-[A-Z0-9]+-\d+", body)))


def _render_todos_html(rows) -> str:
    """Build the todos dashboard HTML from open todo rows (grouped by agent)."""
    import html as _html

    head = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>TGW Todos</title>" + _STATIC_HEAD + "<style>" + _TODOS_EXTRA_CSS + "</style>"
        "</head><body>"
        "<h2>Open Todos</h2>"
    )
    if not rows:
        return head + '<div class="allclear">✓ All clear — no open todos.</div>' + _STATIC_FOOT + _TODOS_JS + "</body></html>"

    # Preserve todo_list ordering (agent, priority, id); group consecutively.
    groups: "list[tuple[str, list]]" = []
    for r in rows:
        agent = r.get("agent", "?")
        if not groups or groups[-1][0] != agent:
            groups.append((agent, []))
        groups[-1][1].append(r)

    # Build agent filter dropdown
    agent_counts = {agent: len(items) for agent, items in groups}
    filter_opts = '<option value="">All agents</option>'
    for ag, cnt in sorted(agent_counts.items()):
        filter_opts += f'<option value="{_html.escape(str(ag))}">{_html.escape(str(ag))} ({cnt})</option>'

    parts = [
        head,
        f'<div class="todos-ctrl"><label for="agent-sel">Agent:</label><select id="agent-sel">{filter_opts}</select></div>',
        f'<div class="total">{len(rows)} open item(s)</div>',
    ]

    for agent, items in groups:
        agent_esc = _html.escape(str(agent))
        parts.append(f'<div class="agent" data-agent="{agent_esc}"><h3>{agent_esc}</h3><span class="count">{len(items)} open</span></div>')
        parts.append(f'<table data-agent="{agent_esc}"><tr><th>ID</th><th>P</th><th>Task</th><th>Src</th><th></th></tr>')
        for it in items:
            body_raw = str(it.get("body", ""))
            body_esc = _html.escape(body_raw)
            pp_refs = _extract_pp_refs(body_raw)
            pp_badges = "".join(f'<a class="pp-badge" href="/docs/plan/TGW-Master-Plan.md" title="{r}">{r}</a>' for r in pp_refs)
            body_cell = f'<div class="task-body" data-agent="{agent_esc}">{body_esc}</div><span class="task-expand"></span>' + (pp_badges if pp_badges else "")
            parts.append(
                f'<tr data-agent="{agent_esc}">'
                f'<td class="id">#{_html.escape(str(it.get("id", "")))}</td>'
                f'<td class="p">{_html.escape(str(it.get("priority", "")))}</td>'
                f"<td>{body_cell}</td>"
                f'<td class="src">{_html.escape(str(it.get("source", "")))}</td>'
                f'<td><button class="copy-btn" data-body="{body_esc}" title="Copy task text">📋</button></td>'
                "</tr>"
            )
        parts.append("</table>")
    parts.append(_STATIC_FOOT)
    parts.append(_TODOS_JS)
    parts.append("</body></html>")
    return "".join(parts)


@app.get("/form/todos")
def todos_form(request: Request):
    """Tablet-first open-todo dashboard — no Bearer auth (network trust), like
    /form/intake and /form/bulk. Read-only view of `tgw todo` grouped by agent."""
    from fastapi.responses import HTMLResponse

    from tgw import todo

    try:
        rows = todo.todo_list()
    except Exception as exc:  # DB down → still render a page, don't 500
        body = (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>TGW Todos</title>" + _STATIC_HEAD + "<style>" + _TODOS_EXTRA_CSS + "</style>"
            "</head><body>"
            "<h2>Open Todos</h2>"
            f'<div class="msg err" style="display:block">todo store unavailable: {exc}</div>' + _STATIC_FOOT + "</body></html>"
        )
        return HTMLResponse(body, status_code=200)
    return HTMLResponse(_render_todos_html(rows))


# ---------------------------------------------------------------------------
# GET /form/runs — agent run trace dashboard (PP-AGENTTRACE-001 Phase 3, #1582)
# ---------------------------------------------------------------------------

_RUNS_EXTRA_CSS = """
table.runs{width:100%;border-collapse:collapse;margin-top:10px}
table.runs th,table.runs td{text-align:left;padding:8px 6px;border-bottom:1px solid #333;font-size:.85em;vertical-align:top}
table.runs th{color:#aaa;font-size:.72em;text-transform:uppercase;letter-spacing:.04em}
td.run-id{color:#4a8ade;font-family:monospace;font-size:.85em;white-space:nowrap}
td.transcript{color:#666;font-size:.78em;word-break:break-all;max-width:260px}
.st-badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:.78em}
.st-completed{background:#1a3a1a;color:#7f7;border:1px solid #2a5a2a}
.st-failed,.st-killed{background:#2a1a1a;color:#f77;border:1px solid #5a2a2a}
.st-running,.st-escalated{background:#3a2a0a;color:#fb7;border:1px solid #6a4a1a}
.runs-ctrl{display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap}
.runs-ctrl label{font-size:.85em;color:#aaa;margin:0}
.runs-ctrl select{width:auto;padding:6px 10px;font-size:.85em}
.runs-ctrl input[type=search]{padding:6px 10px;font-size:.85em;min-width:220px}
.runs-total{font-size:.82em;color:#999;margin-bottom:6px}
"""

_RUNS_JS = """
<script>
(function() {
  var agentSel = document.getElementById('runs-agent-sel');
  var statusSel = document.getElementById('runs-status-sel');
  var search = document.getElementById('runs-search');

  function applyFilters() {
    var agent = agentSel ? agentSel.value : '';
    var st = statusSel ? statusSel.value : '';
    var q = search ? search.value.trim().toLowerCase() : '';
    document.querySelectorAll('tr[data-run-id]').forEach(function(row) {
      var okAgent = (agent === '' || row.dataset.agent === agent);
      var okStatus = (st === '' || row.dataset.status === st);
      var okSearch = (q === '' || (row.dataset.search || '').indexOf(q) !== -1);
      row.style.display = (okAgent && okStatus && okSearch) ? '' : 'none';
    });
  }

  if (agentSel) agentSel.addEventListener('change', applyFilters);
  if (statusSel) statusSel.addEventListener('change', applyFilters);
  if (search) search.addEventListener('input', applyFilters);
})();
</script>
"""


def _runs_status_class(status: str) -> str:
    status = (status or "").lower()
    if status in ("completed",):
        return "st-completed"
    if status in ("failed", "killed"):
        return "st-failed"
    if status in ("running", "escalated"):
        return "st-running"
    return ""


def _render_runs_html(rows) -> str:
    """Build the agent-runs dashboard HTML (PP-AGENTTRACE-001 Phase 3).

    Matches _render_todos_html()'s structure: _STATIC_HEAD/_STATIC_FOOT shared
    dark theme, client-side filtering, escaped cells. Reuses
    agent_trace_render's pure helpers (_short_run_id/_duration_cell) for
    display-format parity with the Obsidian render (Phase 2)."""
    import html as _html
    from datetime import datetime, timezone

    from tgw.agent_trace_render import _duration_cell, _short_run_id

    head = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>TGW Agent Runs</title>" + _STATIC_HEAD + "<style>" + _RUNS_EXTRA_CSS + "</style>"
        "</head><body>"
        "<h2>Agent Runs</h2>"
    )
    if not rows:
        return head + '<div class="allclear">✓ No agent runs recorded yet.</div>' + _STATIC_FOOT + "</body></html>"

    agent_types = sorted({str(r.get("agent_type", "")) for r in rows if r.get("agent_type")})
    statuses = sorted({str(r.get("status", "")) for r in rows if r.get("status")})

    agent_opts = '<option value="">All agent types</option>' + "".join(f'<option value="{_html.escape(a)}">{_html.escape(a)}</option>' for a in agent_types)
    status_opts = '<option value="">All statuses</option>' + "".join(f'<option value="{_html.escape(s)}">{_html.escape(s)}</option>' for s in statuses)

    now = datetime.now(tz=timezone.utc)

    parts = [
        head,
        '<div class="runs-ctrl">'
        f'<label for="runs-agent-sel">Agent:</label><select id="runs-agent-sel">{agent_opts}</select>'
        f'<label for="runs-status-sel">Status:</label><select id="runs-status-sel">{status_opts}</select>'
        '<label for="runs-search">Search:</label>'
        '<input type="search" id="runs-search" placeholder="pp_ref / todo_id / summary">'
        "</div>",
        f'<div class="runs-total">{len(rows)} run(s)</div>',
        '<table class="runs"><tr><th>Run ID</th><th>Agent Type</th><th>PP/Todo</th><th>Host</th><th>Status</th><th>Started</th><th>Duration</th><th>Summary</th><th>Transcript</th></tr>',
    ]

    for row in rows:
        run_id = str(row.get("run_id", ""))
        agent_type = str(row.get("agent_type", ""))
        status = str(row.get("status", ""))
        pp_ref = row.get("pp_ref") or ""
        todo_id = row.get("todo_id")
        host = str(row.get("host", "") or "")
        summary = str(row.get("summary", "") or "")
        transcript_path = str(row.get("transcript_path", "") or "")
        started = row.get("started_at")
        started_str = started.strftime("%Y-%m-%d %H:%M UTC") if started else ""

        ref_bits = []
        if pp_ref:
            ref_bits.append(f'<a class="pp-badge" href="/docs/plan/TGW-Master-Plan.md" title="{_html.escape(pp_ref)}">{_html.escape(pp_ref)}</a>')
        if todo_id is not None:
            ref_bits.append(f"#{_html.escape(str(todo_id))}")
        ref_cell = " ".join(ref_bits)

        search_blob = " ".join(str(x) for x in (pp_ref, todo_id if todo_id is not None else "", summary)).lower()

        status_cls = _runs_status_class(status)

        parts.append(
            f'<tr data-run-id="{_html.escape(run_id)}" data-agent="{_html.escape(agent_type)}" '
            f'data-status="{_html.escape(status)}" data-search="{_html.escape(search_blob)}">'
            f'<td class="run-id" title="{_html.escape(run_id)}">{_html.escape(_short_run_id(run_id))}</td>'
            f"<td>{_html.escape(agent_type)}</td>"
            f"<td>{ref_cell}</td>"
            f"<td>{_html.escape(host)}</td>"
            f'<td><span class="st-badge {status_cls}">{_html.escape(status)}</span></td>'
            f"<td>{_html.escape(started_str)}</td>"
            f"<td>{_html.escape(_duration_cell(row, now))}</td>"
            f"<td>{_html.escape(summary)}</td>"
            f'<td class="transcript">{_html.escape(transcript_path)}</td>'
            "</tr>"
        )

    parts.append("</table>")
    parts.append(_STATIC_FOOT)
    parts.append(_RUNS_JS)
    parts.append("</body></html>")
    return "".join(parts)


@app.get("/form/runs")
def runs_form(request: Request):
    """Agent run trace dashboard — no Bearer auth (network trust via
    _session_guard middleware), like /form/todos. Read-only view of the
    agent_runs table (PP-AGENTTRACE-001 Phase 1/2)."""
    from fastapi.responses import HTMLResponse

    from tgw.queue import state_machine

    try:
        rows = state_machine.list_agent_runs()
    except Exception as exc:  # DB down -> still render a page, don't 500
        body = (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>TGW Agent Runs</title>" + _STATIC_HEAD + "<style>" + _RUNS_EXTRA_CSS + "</style>"
            "</head><body>"
            "<h2>Agent Runs</h2>"
            f'<div class="msg err" style="display:block">agent_runs tracker unavailable: {exc}</div>' + _STATIC_FOOT + "</body></html>"
        )
        return HTMLResponse(body, status_code=200)
    return HTMLResponse(_render_runs_html(rows))


# ---------------------------------------------------------------------------
# GET /form/search — full-text (recoll) search bar (PP-KNOWLEDGE-001 R2, #1147)
# ---------------------------------------------------------------------------

_SEARCH_EXTRA_CSS = """
.search-bar{display:flex;gap:8px;margin-bottom:14px}
.search-bar input[type=search]{flex:1;padding:10px 12px;font-size:1em}
.search-bar button{padding:10px 18px}
.search-meta{font-size:.82em;color:#999;margin-bottom:10px}
.search-err{padding:10px 14px;border-radius:6px;background:#4a1a1a;color:#f77;margin-bottom:10px}
table.results{width:100%;border-collapse:collapse}
table.results th,table.results td{text-align:left;padding:8px 6px;border-bottom:1px solid #333;font-size:.88em;vertical-align:top}
table.results th{color:#aaa;font-size:.72em;text-transform:uppercase;letter-spacing:.04em}
td.mtype{color:#7af;white-space:nowrap;font-size:.82em}
td.rsize{color:#999;white-space:nowrap;font-variant-numeric:tabular-nums}
.rurl{color:#4a8ade;word-break:break-all;font-size:.85em}
.rabs{color:#888;font-size:.82em;margin-top:2px}
"""


def _render_search_html(query: str, result: Optional[Dict[str, Any]]) -> str:
    """Full-text search page — server-rendered, no-auth (network trust) like
    /form/todos and /form/intake. GET-only (?q=...), so results are
    bookmarkable/linkable and don't require client-side JS to work."""
    import html as _html

    head = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>TGW Search</title>" + _STATIC_HEAD + "<style>" + _SEARCH_EXTRA_CSS + "</style>"
        "</head><body>"
        "<h2>Full-text Search</h2>"
        '<form class="search-bar" method="get" action="/form/search">'
        f'<input type="search" name="q" placeholder="search the whole knowledge index…" value="{_html.escape(query)}" autofocus>'
        '<button type="submit">Search</button>'
        "</form>"
    )
    parts = [head]
    if result is None:
        parts.append('<div class="search-meta">Type a query above — recoll query language: implicit AND, -exclude, field:term, "phrase", OR.</div>')
    elif not result.get("ok"):
        parts.append(f'<div class="search-err">{_html.escape(str(result.get("error", "search failed")))}</div>')
    else:
        parts.append(f'<div class="search-meta">{result["count"]} result(s) for "{_html.escape(result["query"])}" — {result["elapsed_ms"]:.0f} ms</div>')
        if result["results"]:
            parts.append('<table class="results"><tr><th>Type</th><th>Result</th><th>Size</th></tr>')
            for row in result["results"]:
                url = row.get("url", "")
                title = row.get("title") or url.rsplit("/", 1)[-1]
                abstract = row.get("abstract", "")
                size = row.get("fbytes", "")
                size_str = f"{int(size):,} B" if size.isdigit() else ""
                parts.append(
                    "<tr>"
                    f'<td class="mtype">{_html.escape(row.get("mtype", ""))}</td>'
                    f"<td><div>{_html.escape(title)}</div>"
                    f'<div class="rurl">{_html.escape(url)}</div>' + (f'<div class="rabs">{_html.escape(abstract.strip())}</div>' if abstract.strip() else "") + "</td>"
                    f'<td class="rsize">{size_str}</td>'
                    "</tr>"
                )
            parts.append("</table>")
    parts.append(_STATIC_FOOT)
    parts.append("</body></html>")
    return "".join(parts)


@app.get("/form/search")
def search_form(q: str = ""):
    """Web UI search bar over the recoll knowledge index (PP-KNOWLEDGE-001 R2,
    todo #1147, Track R). No-auth form page, matching /form/todos/intake/bulk
    (network trust). Empty q renders the bar with no results yet."""
    from fastapi.responses import HTMLResponse

    from tgw.search_full import run_full_text_search

    q = (q or "").strip()
    result = run_full_text_search(q) if q else None
    return HTMLResponse(_render_search_html(q, result))


@app.get("/api/search/full-text", dependencies=[AUTH])
def api_search_full_text(q: str = "", limit: int = 20):
    """JSON full-text search endpoint (PP-KNOWLEDGE-001 R2) — same recoll
    query as /form/search and `tgw search --full-text`, for programmatic/
    tablet-app callers. Auth-gated like the rest of /api/*."""
    from tgw.search_full import run_full_text_search

    result = run_full_text_search(q, limit=limit)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "search failed"))
    return result


# ---------------------------------------------------------------------------
# GET /form/history/{sku_old} — historical-catalog lookup (todo #1054)
# ---------------------------------------------------------------------------

_historical_index_by_sku_old: Optional[Dict[str, Dict[str, Any]]] = None


def _load_historical_index_by_sku_old() -> Dict[str, Dict[str, Any]]:
    """Build (and cache for process lifetime) sku_old -> historical record,
    merging historical-tgwcatalog.json and historical-master-catalog.json.
    Both files carry their own sku_old field that matches current items'
    sku_old exactly (confirmed live) — no case-normalization guessing needed.
    Static snapshot files; never auto-refreshes, matching the category-tree
    cache's own no-auto-expiry convention."""
    global _historical_index_by_sku_old
    if _historical_index_by_sku_old is not None:
        return _historical_index_by_sku_old

    catalog_root = Path(_cfg.get("catalog_root", "/opt/TGW/data/ItemCatalog"))
    index: Dict[str, Dict[str, Any]] = {}

    tgwcat_path = catalog_root / "historical-tgwcatalog.json"
    if tgwcat_path.exists():
        try:
            for rec in json.loads(tgwcat_path.read_text(encoding="utf-8")).values():
                so = rec.get("sku_old")
                if so:
                    index.setdefault(so, rec)
        except (OSError, ValueError) as exc:
            log.warning("historical-tgwcatalog.json unreadable: %s", exc)

    mastercat_path = catalog_root / "historical-master-catalog.json"
    if mastercat_path.exists():
        try:
            for rec in json.loads(mastercat_path.read_text(encoding="utf-8")):
                so = rec.get("sku_old")
                if so:
                    index.setdefault(so, rec)
        except (OSError, ValueError) as exc:
            log.warning("historical-master-catalog.json unreadable: %s", exc)

    _historical_index_by_sku_old = index
    return index


@app.get("/form/history/{sku_old}")
def history_form(sku_old: str):
    """Historical-catalog lookup by sku_old — linked from item detail's
    'SKU (old)' field. Gated by the standard /form/* session-cookie wall
    (_session_guard middleware) like every other /form/* page."""
    import html as _html

    rec = _load_historical_index_by_sku_old().get(sku_old)
    if rec is None:
        body = f"<h2>History</h2><p>No historical record found for <code>{_html.escape(sku_old)}</code>.</p>"
    else:
        rows = "".join(f"<tr><td>{_html.escape(str(k))}</td><td>{_html.escape(str(v))}</td></tr>" for k, v in sorted(rec.items()) if v not in (None, ""))
        body = f'<h2>History — {_html.escape(sku_old)}</h2><table class="dtable"><tbody>{rows}</tbody></table>'
    page = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>TGW History</title>" + _STATIC_HEAD + "</head><body>" + body + _STATIC_FOOT + "</body></html>"
    )
    return HTMLResponse(page)


# ---------------------------------------------------------------------------
# GET/POST /form/suggest — punctuation-safe suggestion entry (PP-CAPTURE-001,
# Round 5 #44). Plain HTML form: no JS, no shell quoting, no Bearer auth
# (network trust, like /form/intake and /form/todos).
# ---------------------------------------------------------------------------


def _render_suggest_html(msg: str = "", ok: bool = False) -> str:
    """Build the suggestion-entry page; optionally show a result banner."""
    import html as _html

    banner = ""
    if msg:
        cls = "ok" if ok else "err"
        banner = f'<div class="msg {cls}" style="display:block">{_html.escape(msg)}</div>'
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>TGW Suggest</title>" + _STATIC_HEAD + "</head><body>"
        "<h2>Add Suggestion</h2>"
        '<form method="post" action="/form/suggest">'
        "<label>Suggestion — any punctuation is safe here</label>"
        '<textarea name="text" required autofocus placeholder="idea, task, note ..."></textarea>'
        '<button class="btn" type="submit">Add to SUGGESTIONS.md</button>'
        "</form>" + banner + _STATIC_FOOT + "</body></html>"
    )


@app.get("/form/suggest")
def suggest_form():
    """Suggestion-entry form — appends to vault SUGGESTIONS.md via cmd_suggest."""
    from fastapi.responses import HTMLResponse

    return HTMLResponse(_render_suggest_html())


@app.post("/form/suggest")
async def suggest_submit(request: Request):
    """Handle the form post. Whitespace (including newlines from the textarea)
    is collapsed to single spaces so every entry stays one `- [ ]` checklist
    line — multi-line text would break SUGGESTIONS.md's per-line format."""
    from fastapi.responses import HTMLResponse

    from .api import cmd_suggest

    form = await request.form()
    text = " ".join(str(form.get("text", "")).split())
    if not text:
        return HTMLResponse(_render_suggest_html("empty suggestion — nothing written"))
    try:
        result = cmd_suggest(_cfg, text)
    except Exception as exc:  # vault path missing/unwritable → report, don't 500
        return HTMLResponse(_render_suggest_html(f"write failed: {exc}"))
    return HTMLResponse(_render_suggest_html(f"added: {result['written']}", ok=True))


# ---------------------------------------------------------------------------
# GET/POST /form/size-classes — config-backed shipping size-class editor
# (PP-STORAGE-001, todo #1557). This only edits size_class_ranges in the main
# API config; it does not invoke eBay or any listing workflow.
# ---------------------------------------------------------------------------


_SIZE_RANGE_FIELDS = (
    ("weight_min", "Weight min (oz)", "weight_oz", 0),
    ("weight_max", "Weight max (oz)", "weight_oz", 1),
    ("length_min", "Length min (in)", "l", 0),
    ("length_max", "Length max (in)", "l", 1),
    ("width_min", "Width min (in)", "w", 0),
    ("width_max", "Width max (in)", "w", 1),
    ("height_min", "Height min (in)", "h", 0),
    ("height_max", "Height max (in)", "h", 1),
)


def _size_class_config_path() -> Path:
    """Return the exact config file used to start this server."""
    return Path(_cfg.get("config_path", DEFAULT_CONFIG))


def _read_size_class_config() -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Reload config from disk so the admin page never overwrites newer edits."""
    from .config import load_json_strict

    path = _size_class_config_path()
    raw = load_json_strict(path)
    if not isinstance(raw, dict):
        raise ValueError("top-level config must be a JSON object")
    ranges = raw.get("size_class_ranges", {})
    if not isinstance(ranges, dict):
        raise ValueError("size_class_ranges must be a JSON object")
    return raw, ranges


def _range_value(entry: Any, key: str, index: int) -> Any:
    if not isinstance(entry, dict):
        return None
    if key == "weight_oz":
        pair = entry.get(key)
    else:
        dims = entry.get("dims_in", {})
        pair = dims.get(key) if isinstance(dims, dict) else None
    return pair[index] if isinstance(pair, list) and len(pair) == 2 else None


def _render_size_classes_html(ranges: Dict[str, Any], msg: str = "", ok: bool = False) -> str:
    import html as _html

    banner = ""
    if msg:
        cls = "ok" if ok else "err"
        banner = f'<div class="msg {cls}" style="display:block">{_html.escape(msg)}</div>'
    rows = []
    for name, entry in ranges.items():
        values = []
        for _, _, key, index in _SIZE_RANGE_FIELDS:
            value = _range_value(entry, key, index)
            values.append("—" if value is None else _html.escape(str(value)))
        rows.append(
            '<tr><td><button class="edit-size-class" type="button" '
            f'data-name="{_html.escape(str(name), quote=True)}">'
            f"{_html.escape(str(name))}</button></td>" + "".join(f"<td>{value}</td>" for value in values) + "</tr>"
        )
    table_rows = "".join(rows) or '<tr><td colspan="9">No size classes configured yet.</td></tr>'
    inputs = "".join(f'<label>{label}<input type="number" inputmode="decimal" min="0" step="any" name="{field}" id="{field}" placeholder="unset"></label>' for field, label, _, _ in _SIZE_RANGE_FIELDS)
    # JSON is embedded in a script element. Escaping every '<' prevents a
    # pre-existing config string from terminating that element with </script>.
    ranges_json = json.dumps(ranges).replace("<", "\\u003c")
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>TGW Size Classes</title>" + _STATIC_HEAD + """<style>
.size-table{width:100%;border-collapse:collapse;margin:12px 0 24px;font-size:.85em}
.size-table th,.size-table td{border:1px solid #333;padding:7px;text-align:right}
.size-table th:first-child,.size-table td:first-child{text-align:left}
.edit-size-class{background:none;border:0;color:#8ac;text-decoration:underline;cursor:pointer;padding:0}
.range-grid{display:grid;grid-template-columns:repeat(2,minmax(130px,1fr));gap:10px}
@media(min-width:760px){.range-grid{grid-template-columns:repeat(4,minmax(130px,1fr))}}
.range-grid label{margin:0}.range-grid input{width:100%;box-sizing:border-box}
</style></head><body><h2>Shipping Size Classes</h2>"""
        "<p>Blank bounds are saved as <code>null</code>. Click a class name to edit it.</p>" + banner + '<table class="size-table"><thead><tr><th>Class</th><th>Weight min</th><th>Weight max</th>'
        "<th>Length min</th><th>Length max</th><th>Width min</th><th>Width max</th>"
        "<th>Height min</th><th>Height max</th></tr></thead><tbody>" + table_rows + "</tbody></table>"
        '<h3 id="editor-title">Add size class</h3><form method="post" action="/form/size-classes">'
        '<label>Class name<input name="name" id="class-name" required maxlength="80" '
        'pattern="[A-Za-z0-9][A-Za-z0-9_.-]*" placeholder="e.g. medium_box"></label>'
        '<div class="range-grid">' + inputs + "</div>"
        '<button class="btn" type="submit">Save size class</button> '
        '<button class="btn" type="button" id="clear-editor">Add another</button></form>'
        f"<script>const sizeClassRanges={ranges_json};\n"
        """function clearEditor(){document.querySelector('form').reset();document.getElementById('class-name').readOnly=false;document.getElementById('editor-title').textContent='Add size class';}
document.querySelectorAll('.edit-size-class').forEach(function(btn){btn.addEventListener('click',function(){var n=btn.dataset.name,e=sizeClassRanges[n]||{},d=e.dims_in||{};document.getElementById('class-name').value=n;document.getElementById('class-name').readOnly=true;var vals={weight_min:(e.weight_oz||[])[0],weight_max:(e.weight_oz||[])[1],length_min:(d.l||[])[0],length_max:(d.l||[])[1],width_min:(d.w||[])[0],width_max:(d.w||[])[1],height_min:(d.h||[])[0],height_max:(d.h||[])[1]};Object.keys(vals).forEach(function(k){document.getElementById(k).value=vals[k]==null?'':vals[k];});document.getElementById('editor-title').textContent='Edit '+n;document.getElementById('editor-title').scrollIntoView({behavior:'smooth'});});});
document.getElementById('clear-editor').addEventListener('click',clearEditor);</script>""" + _STATIC_FOOT + "</body></html>"
    )


@app.get("/form/size-classes")
def size_classes_form():
    try:
        _, ranges = _read_size_class_config()
        return HTMLResponse(_render_size_classes_html(ranges))
    except Exception as exc:
        return HTMLResponse(_render_size_classes_html({}, f"config read failed: {exc}"), status_code=500)


@app.post("/form/size-classes")
async def size_classes_submit(request: Request):
    import math

    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", name):
        try:
            _, ranges = _read_size_class_config()
        except Exception:
            ranges = {}
        return HTMLResponse(_render_size_classes_html(ranges, "invalid class name"), status_code=400)

    parsed: Dict[str, Optional[float]] = {}
    try:
        for field, _, _, _ in _SIZE_RANGE_FIELDS:
            text = str(form.get(field, "")).strip()
            value = None if not text else float(text)
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError(f"{field.replace('_', ' ')} must be a non-negative number")
            parsed[field] = value
        for prefix in ("weight", "length", "width", "height"):
            low, high = parsed[f"{prefix}_min"], parsed[f"{prefix}_max"]
            if low is not None and high is not None and low > high:
                raise ValueError(f"{prefix} minimum cannot exceed maximum")
        raw, ranges = _read_size_class_config()
        ranges[name] = {
            "weight_oz": [parsed["weight_min"], parsed["weight_max"]],
            "dims_in": {
                "l": [parsed["length_min"], parsed["length_max"]],
                "w": [parsed["width_min"], parsed["width_max"]],
                "h": [parsed["height_min"], parsed["height_max"]],
            },
        }
        raw["size_class_ranges"] = ranges
        atomic_write_json(_size_class_config_path(), raw, pretty=True)
        _cfg["raw"] = raw
        return HTMLResponse(_render_size_classes_html(ranges, f"saved {name}", ok=True))
    except ValueError as exc:
        try:
            _, ranges = _read_size_class_config()
        except Exception:
            ranges = {}
        return HTMLResponse(_render_size_classes_html(ranges, str(exc)), status_code=400)
    except Exception as exc:
        try:
            _, ranges = _read_size_class_config()
        except Exception:
            ranges = {}
        return HTMLResponse(_render_size_classes_html(ranges, f"config write failed: {exc}"), status_code=500)


# ---------------------------------------------------------------------------
# POST /api/suggest — JSON suggestion endpoint for the nav popup (no Bearer
# auth, network trust — same policy as /form/suggest and /form/todos).
# ---------------------------------------------------------------------------


@app.post("/api/suggest")
async def api_suggest(request: Request) -> Dict[str, Any]:
    """Network-trust JSON suggest endpoint used by the nav popup overlay.
    Accepts {text: str}, appends to SUGGESTIONS.md, returns {ok, written}."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    text = " ".join(str(body.get("text", "")).split())
    if not text:
        raise HTTPException(status_code=400, detail="empty suggestion")
    from .api import cmd_suggest

    try:
        result = cmd_suggest(_cfg, text)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"write failed: {exc}")
    return {"ok": True, "written": result.get("written", "")}


# ---------------------------------------------------------------------------
# POST /api/inbox/upload — drop a file into the plan inbox (Flutter app)
# ---------------------------------------------------------------------------

_INBOX_MAX_BYTES = 512 * 1024  # 512 KB; inbox notes are text, not media


@app.post("/api/inbox/upload", dependencies=[AUTH])
async def api_inbox_upload(file: UploadFile = File(...)) -> Dict[str, Any]:
    """Upload a file to the plan inbox with a timestamp prefix.

    Intended for the Flutter editor app — lets the operator drop a note or
    document into the pm_intake inbox from the tablet.  Only ``.md`` files
    are picked up by the pm_intake worker; other extensions land in the inbox
    directory but require manual action.

    Returns ``{ok, filename}`` on success.
    """
    orig_name = file.filename or "upload.md"
    if "/" in orig_name or "\\" in orig_name or ".." in orig_name:
        raise HTTPException(status_code=400, detail="invalid filename")
    raw_name = Path(orig_name).name
    if not raw_name or raw_name.startswith("."):
        raise HTTPException(status_code=400, detail="invalid filename")

    content = await file.read()
    if len(content) > _INBOX_MAX_BYTES:
        raise HTTPException(status_code=413, detail="file too large (max 512 KB)")

    ts = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    dest_name = f"{ts}_{raw_name}"

    inbox_dir: Path = _cfg["plan_inbox_path"]
    inbox_dir.mkdir(parents=True, exist_ok=True)
    dest = (inbox_dir / dest_name).resolve()
    if not dest.is_relative_to(inbox_dir.resolve()):
        raise HTTPException(status_code=400, detail="invalid filename")

    dest.write_bytes(content)
    return {"ok": True, "filename": dest_name}


# ---------------------------------------------------------------------------
# GET /media/{sku}/{filename} — serve item media (no auth, network trust)
# GET /thumb/{sku}           — serve thumbnail  (no auth, for <img src>)
# ---------------------------------------------------------------------------


@app.get("/media/{sku}/{filename}")
def get_media(sku: str, filename: str):
    """Serve a photo/video from ItemData. No Bearer auth — network trust."""
    if ".." in sku:
        raise HTTPException(status_code=400, detail="invalid sku")
    safe = Path(filename).name
    if safe != filename or not safe or safe.startswith("."):
        raise HTTPException(status_code=400, detail="invalid filename")
    if Path(safe).suffix.lower() not in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov", ".mkv", ".webm"}:
        raise HTTPException(status_code=400, detail="unsupported media type")
    itemdata_root = _cfg["itemdata_root"].resolve()
    p = (itemdata_root / sku / safe).resolve()
    if not p.is_relative_to(itemdata_root):
        raise HTTPException(status_code=400, detail="invalid sku")
    if not p.exists():
        raise HTTPException(status_code=404, detail="media not found")
    return FileResponse(str(p))


@app.get("/thumb/{sku}")
def get_thumb_noauth(sku: str):
    """Serve SKU thumbnail without auth — needed for <img src> in browse UI."""
    if ".." in sku:
        raise HTTPException(status_code=400, detail="invalid sku")
    thumb = _cfg["thumbnail_root"] / f"{sku}.jpg"
    if thumb.exists():
        return FileResponse(str(thumb), media_type="image/jpeg")
    itemdata_root = _cfg["itemdata_root"].resolve()
    sku_dir = (itemdata_root / sku).resolve()
    if not sku_dir.is_relative_to(itemdata_root):
        raise HTTPException(status_code=400, detail="invalid sku")
    if sku_dir.exists():
        for p in sorted(sku_dir.iterdir()):
            if p.suffix.lower() in {".jpg", ".jpeg", ".png"}:
                return FileResponse(str(p))
    raise HTTPException(status_code=404, detail=f"no thumbnail for {sku}")


# ---------------------------------------------------------------------------
# /form/items  — inventory browse + detail (no Bearer auth, network trust)
# ---------------------------------------------------------------------------

_ITEMS_EXTRA_CSS = """
.hdr{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:4px 0 12px;
  border-bottom:1px solid #333;margin-bottom:10px}
.hdr h2{margin:0;font-size:1.1em;flex-shrink:0}
.hdr input{flex:1;min-width:130px;padding:8px;font-size:.9em;background:#222;
  color:#eee;border:1px solid #444;border-radius:6px}
.summary{font-size:.8em;color:#aaa;margin-bottom:8px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:10px}
.card{display:flex;flex-direction:column;background:#1a1a1a;border:1px solid #333;
  border-radius:8px;overflow:hidden;transition:border-color .15s}
.card:hover{border-color:#4a8ade}
.card-inner{display:flex;flex-direction:column;text-decoration:none;color:inherit}
.card-inner .thumb{width:100%;aspect-ratio:4/3;object-fit:cover;background:#111}
.card-body{padding:8px}
.card-sku{font-size:.7em;color:#aaa;font-family:monospace}
.card-title{font-size:.85em;margin:4px 0;color:#ddd;line-height:1.3;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.card-meta{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:4px;font-size:.75em;color:#aaa}
.price{color:#7fbfff;font-weight:bold}
.sbadge{padding:2px 6px;border-radius:10px;font-size:.72em}
.s-in-stock{background:#1a3a1a;color:#7f7}.s-listed{background:#1a2a4a;color:#7af}
.s-staged{background:#3a2a0a;color:#fb7}.s-sold{background:#2a1a1a;color:#f77}
.pager{text-align:center;margin-top:14px;font-size:.9em;color:#aaa}
.pager button{padding:8px 20px;background:#1a3a5a;color:#7af;border:1px solid #2a5a8a;
  border-radius:6px;cursor:pointer;font-size:.9em}
.pager button:hover{background:#1a4a6a}
/* card checkbox + selection */
.card-chk-wrap{position:relative;height:0;z-index:2}
.card-chk{position:absolute;top:6px;left:6px;width:16px;height:16px;cursor:pointer;accent-color:#4a8ade}
.card.selected{border-color:#4a8ade;box-shadow:0 0 0 2px rgba(74,138,222,.2)}
/* selection bar */
.sel-bar{display:none;position:sticky;top:0;z-index:10;background:#1a2a3a;
  border:1px solid #2a5a8a;border-radius:6px;padding:6px 12px;margin-bottom:10px;
  color:#cce;font-size:.85em;align-items:center;gap:8px;flex-wrap:wrap}
.sel-bar.vis{display:flex}
.sel-clr{background:none;border:none;color:#f99;cursor:pointer;font-size:.85em;padding:0 2px}
.bulk-sel{padding:3px 6px;background:#1a1a2a;color:#bbb;border:1px solid #2a3a5a;border-radius:4px;font-size:.8em;font-family:inherit;cursor:pointer}
.bulk-run{padding:3px 12px;background:#1a3a1a;color:#7f7;border:1px solid #2a6a2a;border-radius:4px;cursor:pointer;font-size:.8em;font-family:inherit;white-space:nowrap}
.bulk-run:hover:not(:disabled){background:#1e4a1e}
.bulk-run:disabled{opacity:.35;cursor:default}
/* eBay state mini-badges */
.eb{padding:1px 6px;border-radius:9px;font-size:.68em;font-weight:600;text-decoration:none;white-space:nowrap}
.eb-listed{background:#1a3a1a;color:#7f7;border:1px solid #2a5a2a}
.eb-listed:hover{background:#1e4a1e}
.eb-ready{background:#1a3a3a;color:#4ff;border:1px solid #1a6a6a}
.eb-staged{background:#2a1a3a;color:#d7f;border:1px solid #5a2a8a}
.eb-draft{background:#3a2a00;color:#fb7;border:1px solid #6a4a00}
.eb-none{background:#252525;color:#666;border:1px solid #333}
.eb-sold{background:#2a1a1a;color:#f77;border:1px solid #5a2a2a}
/* card layout refinements */
.card-sku{font-size:.67em;color:#777;font-family:monospace;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.card-loc{color:#aaa;font-family:inherit;font-style:normal}
.card-status{display:flex;gap:5px;align-items:center;margin-top:4px;flex-wrap:wrap}
.card-meta{display:flex;gap:8px;align-items:center;margin-top:3px;font-size:.75em;color:#aaa}
.card-cat{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#888}
/* action dropdown + run button */
.card-btns{display:flex;gap:4px;padding:5px 6px;background:#151515;border-top:1px solid #222}
.csel{flex:1;padding:3px 5px;background:#1a1a2a;color:#bbb;border:1px solid #2a2a3a;
  border-radius:4px;font-size:.72em;font-family:inherit;cursor:pointer;min-width:0}
.csel:focus{outline:none;border-color:#4a8ade}
.crun{padding:3px 10px;background:#1a2a3a;color:#7af;border:1px solid #2a5a8a;
  border-radius:4px;cursor:pointer;font-size:.78em;flex-shrink:0;font-family:inherit}
.crun:hover:not(:disabled){background:#1a3a5a}
.crun:disabled{opacity:.35;cursor:default}
.loading,.no-results{padding:20px;text-align:center;color:#888}
/* detail */
.back{display:inline-block;color:#4a8ade;text-decoration:none;margin-bottom:10px;font-size:.9em}
.sku-hdr{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:10px}
.sku-hdr .slabel{font-family:monospace;color:#888;font-size:.85em}
.sku-hdr .stitle{font-size:1.05em;color:#eee}
.detail-layout{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:680px){.detail-layout{grid-template-columns:1fr}}
.gallery .main-photo{width:100%;border-radius:6px;background:#111;cursor:pointer;display:block}
.strip{display:flex;gap:5px;flex-wrap:wrap;margin-top:6px}
.strip img{width:60px;height:45px;object-fit:cover;border-radius:4px;cursor:pointer;
  border:2px solid transparent;background:#111}
.strip img.active{border-color:#4a8ade}
.dfields{display:flex;flex-direction:column;gap:12px}
.dsec{background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:10px}
.dsec h3{margin:0 0 8px;font-size:.78em;text-transform:uppercase;letter-spacing:.06em;color:#888}
.frow{display:flex;gap:6px;padding:4px 0;border-bottom:1px solid #262626;font-size:.85em}
.frow:last-child{border-bottom:none}
.fn{color:#888;width:120px;flex-shrink:0;font-size:.8em}
.fv{color:#ddd;word-break:break-word}
.fv a{color:#4a8ade}
.diff-hdr{font-size:.78em;text-transform:uppercase;letter-spacing:.06em;color:#fb7;margin:0 0 6px}
.diff-meta{font-family:monospace;font-size:.72em;color:#666;margin-bottom:8px}
.dtable{width:100%;border-collapse:collapse;font-size:.82em}
.dtable th{color:#666;text-align:left;padding:4px 8px;border-bottom:1px solid #2a2a2a;
  font-weight:normal;font-size:.75em;text-transform:uppercase}
.dtable td{padding:5px 8px;border-bottom:1px solid #1e1e1e;vertical-align:top;word-break:break-word}
.dfield{color:#888;font-family:monospace;width:110px}
.dwas{color:#f77}.dnow{color:#7f7}
.spec-tbl{width:100%;border-collapse:collapse;font-size:.82em;margin-top:2px}
.spec-tbl th{color:#555;text-align:left;padding:2px 6px;border-bottom:1px solid #2a2a2a;font-weight:normal;font-size:.75em;text-transform:uppercase}
.spec-tbl td{padding:3px 6px;border-bottom:1px solid #1a1a1a;vertical-align:top}
.spec-k{color:#888;width:40%;font-size:.9em}
.spec-v{color:#ccc}
.listing-preview{background:#111;border:1px solid #2a2a2a;border-radius:4px;padding:8px 10px;font-size:.8em;color:#bbb;max-height:180px;overflow-y:auto;line-height:1.5}
.listing-preview p{margin:0 0 6px}
.listing-preview p:last-child{margin:0}
.listing-desc-row{align-items:flex-start}
.jtable{width:100%;border-collapse:collapse;font-size:.78em}
.jtable th{color:#666;text-align:left;padding:4px 6px;border-bottom:1px solid #2a2a2a;
  font-weight:normal;font-size:.75em;text-transform:uppercase}
.jtable td{padding:4px 6px;border-bottom:1px solid #1e1e1e;vertical-align:top}
.js-succeeded{color:#7f7}.js-queued,.js-leased,.js-running{color:#7af}
.js-retry-wait{color:#fb7}.js-cancelled{color:#888}
.js-failed,.js-dead-letter{color:#f77}
.ebay-links{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.ebay-btn{display:inline-block;padding:7px 13px;border-radius:6px;font-size:.82em;
  font-weight:600;text-decoration:none;border:1px solid}
.ebay-btn-primary{background:#1a3a5a;color:#7af;border-color:#2a6a9a}
.ebay-btn-primary:hover{background:#1a4a6a}
.ebay-btn-sec{background:#1a1a2a;color:#aaa;border-color:#333}
.ebay-btn-sec:hover{background:#222}
.offer-badge{display:inline-flex;align-items:center;padding:6px 12px;background:#3a2a00;
  color:#fb7;border:1px solid #6a4a00;border-radius:6px;font-size:.82em;font-weight:600;
  text-decoration:none}
.offer-badge:hover{background:#4a3a00}
.danger-zone{margin-top:18px;border-top:1px solid #332}
.act-row{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
.act-btn{padding:7px 14px;background:#1a2a3a;color:#cce;border:1px solid #2a4a6a;
  border-radius:6px;cursor:pointer;font-size:.84em}
.act-btn:hover:not(:disabled){background:#1a3a5a}
.act-delete{background:#2a1a1a;color:#f99;border-color:#5a2a2a}
.act-delete:hover:not(:disabled){background:#3a1a1a}
.act-warn{background:#2a1a00;color:#fb7;border-color:#5a3a00}
.act-warn:hover:not(:disabled){background:#3a2a00}
.act-publish{background:#1a3a1a;color:#8e8;border-color:#2a6a2a}
.act-publish:hover:not(:disabled){background:#1a4a1a}
.act-disabled,.act-btn:disabled{opacity:.45;cursor:not-allowed}
.lbadge{display:inline-block;padding:1px 7px;border-radius:9px;font-size:.7em;
  vertical-align:middle;margin-left:5px;font-weight:600}
.lbadge-active{background:#1a3a1a;color:#7f7;border:1px solid #2a5a2a}
.lbadge-inactive{background:#2a2a1a;color:#ab7;border:1px solid #4a4a1a}
.lbadge-pending{background:#2a1a00;color:#fb7;border:1px solid #5a3a00}
/* per-page selector and view toggle */
.hdr-controls{display:flex;gap:8px;align-items:center;margin-left:auto;flex-shrink:0}
.pg-sel{background:#222;color:#ccc;border:1px solid #444;border-radius:4px;
  padding:5px 8px;font-size:.85em;font-family:inherit}
.vtbtn{padding:5px 10px;background:#222;color:#888;border:1px solid #333;
  border-radius:4px;cursor:pointer;font-size:.82em;font-family:inherit}
.vtbtn.active{background:#1a2a3a;color:#7af;border-color:#2a5a8a}
/* list/table view */
.list-table{width:100%;border-collapse:collapse;font-size:.85em}
.list-table th{color:#666;text-align:left;padding:7px 8px;border-bottom:1px solid #333;
  font-size:.78em;text-transform:uppercase;white-space:nowrap}
.list-table td{padding:6px 8px;border-bottom:1px solid #1e1e1e;vertical-align:middle}
.list-table tr:hover td{background:#141414}
.list-table .lt-thumb{width:48px;height:36px;object-fit:cover;border-radius:3px;background:#111}
.list-table .lt-sku{font-family:monospace;color:#888;font-size:.75em;white-space:nowrap}
.list-table .lt-title{color:#ddd;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.list-table .lt-price{color:#7fbfff;font-weight:bold;text-align:right;white-space:nowrap}
.list-table .lt-run{padding:2px 8px;font-size:.75em}
.list-table a{color:inherit;text-decoration:none;display:contents}
.list-table .lt-btn{padding:3px 8px;background:#1a2a3a;color:#7af;border:1px solid #2a4a6a;
  border-radius:4px;cursor:pointer;font-size:.75em;white-space:nowrap}
.list-table .lt-btn:hover{background:#1a3a5a}
/* inline editing in detail page */
.fv.editable{cursor:text;position:relative}
.fv.editable:hover{color:#cce;text-decoration:underline dotted #444}
.fv.editable:hover::after{content:' ✎';font-size:.7em;color:#4a8ade;opacity:.7}
.fv-edit{width:100%;box-sizing:border-box;background:#0d1a2a;color:#cce;
  border:1px solid #2a5a8a;border-radius:3px;padding:2px 6px;
  font-size:.85em;font-family:inherit}
.fv-saved{animation:fvFlash .7s ease}
@keyframes fvFlash{0%{background:#1a4a1a}100%{background:transparent}}
/* strip photo reorder */
.strip-item{position:relative;display:inline-block}
.strip-mv{position:absolute;top:1px;left:1px;background:rgba(0,0,0,.7);color:#7af;
  border:none;border-radius:3px;padding:1px 4px;cursor:pointer;font-size:.65em;
  line-height:1.4;display:none;z-index:2}
.strip-item:hover .strip-mv{display:block}
.strip-mv:hover{background:rgba(10,60,130,.85)}
.dleft{display:flex;flex-direction:column;gap:12px}
.dleft-log{display:flex;flex-direction:column;gap:12px}
.gallery{overflow:hidden}
details.dsec>summary::-webkit-details-marker{display:none}
details.dsec[open]>summary span:last-child{transform:rotate(90deg)}
"""

_BROWSE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TGW Inventory</title>
{static_head}
<style>{extra_css}</style>
</head>
<body>
<div class="hdr">
  <h2>TGW Inventory</h2>
  <input id="sq" type="search" placeholder="search title or SKU…" oninput="df()">
  <input id="loc" type="text" placeholder="location…" oninput="df()">
  <div class="hdr-controls">
    <select class="pg-sel" id="pg-sel" onchange="load(0)">
      <option value="15">15/page</option>
      <option value="30" selected>30/page</option>
      <option value="60">60/page</option>
      <option value="100">100/page</option>
    </select>
    <button class="vtbtn active" id="vt-card" onclick="setView('card')">Cards</button>
    <button class="vtbtn" id="vt-list" onclick="setView('list')">List</button>
  </div>
</div>
<div class="chips" id="status-chips" style="margin-bottom:10px">
  <button class="chip active" data-s="">All</button>
  <button class="chip" data-s="__eligible__" title="new / In Stock, not on eBay — ready to list">Eligible</button>
  <button class="chip" data-s="In Stock">In Stock</button>
  <button class="chip" data-s="Listed">Listed</button>
  <button class="chip" data-s="Staged">Staged</button>
  <button class="chip" data-s="Sold">Sold</button>
</div>
<div class="summary" id="sum"></div>
<div class="sel-bar" id="sel-bar">
  <span id="sel-count"></span>
  <button class="sel-clr" onclick="_clearSel()">✕</button>
  <select class="bulk-sel" id="bulk-sel" onchange="document.getElementById('bulk-run').disabled=!this.value">
    <option value="">Action…</option>
    <optgroup label="Identify &amp; Draft">
      <option value="ai_identify">Re-identify</option>
      <option value="ebay_draft">Re-draft</option>
      <option value="ebay_price">Re-price</option>
    </optgroup>
    <optgroup label="Pipeline">
      <option value="approve">Set Ready</option>
      <option value="ebay_stage">Stage</option>
      <option value="ebay_publish">Publish</option>
    </optgroup>
    <optgroup label="⚠ Destructive">
      <option value="mark_sold">Mark Sold</option>
      <option value="archive">Archive</option>
      <option value="delete">Delete</option>
    </optgroup>
  </select>
  <button class="bulk-run" id="bulk-run" disabled onclick="_bulkActSel()">▶ Run</button>
</div>
<div id="grid-wrap"><div class="grid" id="grid"><div class="loading">Loading…</div></div></div>
<div class="pager" id="pager"></div>
{static_foot}
<script>
window.TGW_API_KEY={api_key!r};
const AUTH='Bearer {api_key}';
const esc=escapeHtml;
let _off=0,_total=0,_view='card';
const scls=s=>({{
  'in stock':'s-in-stock','listed':'s-listed','staged':'s-staged','sold':'s-sold'
}})[(s||'').toLowerCase()]||'';
function getLim(){{return parseInt(document.getElementById('pg-sel').value)||30;}}
function setView(v,skipLoad){{
  _view=v;
  document.getElementById('vt-card').classList.toggle('active',v==='card');
  document.getElementById('vt-list').classList.toggle('active',v==='list');
  syncURLParam('view',v==='list'?'list':'');
  if(!skipLoad)load(0);
}}
function _ebayBadge(it){{
  const lid=it.ebay_listing_id,oid=it.ebay_offer_id,rat=it.ebay_ready_at;
  const lst=(it.ebay_listing_status||'').toLowerCase();
  const sold=(it.status||'').toLowerCase()==='sold';
  // "Listed" means LIVE — an Ended listing keeps its listing_id forever, and
  // badging it Listed misled the Eligible view (Dave, s42 one-at-a-time test).
  const live=lid&&(lst==='active'||lst==='published'||lst==='');
  if(sold) return '<span class="eb eb-sold">Sold</span>';
  if(live) return `<a class="eb eb-listed" href="https://www.ebay.com/itm/${{esc(lid)}}" target="_blank" onclick="event.stopPropagation()">Listed ↗</a>`;
  if(lid&&lst==='ended') return `<a class="eb eb-none" href="https://www.ebay.com/itm/${{esc(lid)}}" target="_blank" onclick="event.stopPropagation()">Ended</a>`;
  if(oid&&rat) return '<span class="eb eb-ready">Ready</span>';
  if(oid) return '<span class="eb eb-staged">Staged</span>';
  if(it.has_draft) return '<span class="eb eb-draft">Needs Review</span>';
  return '<span class="eb eb-none">Not Listed</span>';
}}
const _ALL_ACTIONS=[
  ['ai_identify','Re-identify'],['approve','Set Ready'],
  ['mark_sold','Mark Sold'],['archive','Archive'],
];
function _cardActions(it){{
  const lid=it.ebay_listing_id,oid=it.ebay_offer_id,rat=it.ebay_ready_at;
  const lst=(it.ebay_listing_status||'').toLowerCase();
  const live=lid&&(lst==='active'||lst==='published'||lst==='');
  const sold=(it.status||'').toLowerCase()==='sold';
  const acts=[];
  if(!sold){{
    acts.push(['ai_identify','Re-identify']);
  }}
  if(!live&&!sold){{
    acts.push(['approve','Set Ready']);
  }}
  if(live){{
    acts.push(['mark_sold','Mark Sold']);
  }}
  if(sold) acts.push(['ai_identify','Re-identify']);
  acts.push(['archive','Archive']);
  return acts;
}}
function _cardOpts(it){{
  const ctx=_cardActions(it);
  const ctxKeys=new Set(ctx.map(([v])=>v));
  const ctxHtml=ctx.map(([v,l])=>`<option value="${{v}}">${{l}}</option>`).join('');
  const rest=_ALL_ACTIONS.filter(([v])=>!ctxKeys.has(v));
  const restHtml=rest.length?`<optgroup label="─ All ─">${{rest.map(([v,l])=>`<option value="${{v}}">${{l}}</option>`).join('')}}</optgroup>`:'';
  return ctxHtml+restHtml;
}}
function _cardHtml(it){{
  const pf=parseFloat(it.price);const price=isNaN(pf)?'—':'$'+pf.toFixed(2);
  const loc=it.location?` · <em class="card-loc">${{esc(it.location)}}</em>`:'';
  const cat=esc(it.attribute_set||'');
  const sel=_sel.has(it.sku);
  const opts=_cardOpts(it);
  return `<div class="card${{sel?' selected':''}}" data-sku="${{esc(it.sku)}}">
  <div class="card-chk-wrap"><input type="checkbox" class="card-chk"${{sel?' checked':''}} onclick="_togSel(event,'${{esc(it.sku)}}')"></div>
  <a href="/form/operator/items/${{it.sku}}" class="card-inner">
    <img class="thumb" src="/thumb/${{it.sku}}" loading="lazy" alt="" onerror="this.style.visibility='hidden'">
  </a>
  <div class="card-body">
    <div class="card-sku">${{esc(it.sku)}}${{loc}}</div>
    <div class="card-title"><a href="/form/operator/items/${{it.sku}}" style="color:inherit;text-decoration:none">${{esc(it.title||'')}}</a></div>
    <div class="card-status">${{_ebayBadge(it)}}<span class="sbadge ${{scls(it.status)}}">${{esc(it.status||'—')}}</span></div>
    <div class="card-meta"><span class="price">${{price}}</span><span class="card-cat">${{cat}}</span></div>
  </div>
  <div class="card-btns">
    <select class="csel" onchange="this.nextElementSibling.disabled=!this.value"><option value="">Action…</option>${{opts}}</select>
    <button class="crun" disabled onclick="cact(event,'${{esc(it.sku)}}',this.previousElementSibling.value)">▶</button>
  </div></div>`;
}}
const _sel=new Set();
function _togSel(e,sku){{
  e.stopPropagation();
  _sel.has(sku)?_sel.delete(sku):_sel.add(sku);
  const card=document.querySelector(`.card[data-sku="${{sku}}"]`);
  if(card){{card.classList.toggle('selected',_sel.has(sku));const chk=card.querySelector('.card-chk');if(chk)chk.checked=_sel.has(sku);}}
  _renderSelBar();
}}
function _clearSel(){{
  _sel.clear();
  document.querySelectorAll('.card.selected').forEach(c=>{{c.classList.remove('selected');const chk=c.querySelector('.card-chk');if(chk)chk.checked=false;}});
  const bs=document.getElementById('bulk-sel');if(bs)bs.value='';
  const br=document.getElementById('bulk-run');if(br)br.disabled=true;
  _renderSelBar();
}}
function _renderSelBar(){{
  const bar=document.getElementById('sel-bar');if(!bar)return;
  if(!_sel.size){{bar.classList.remove('vis');return;}}
  bar.classList.add('vis');
  document.getElementById('sel-count').textContent=_sel.size+' selected';
}}
const _BULK_CONFIRM={{
  mark_sold:'Mark {{n}} item(s) as Sold?',
  delete:'Delete {{n}} item(s) locally?',
  archive:'Archive {{n}} item(s)? They will be hidden from the catalog.',
}};
async function _bulkAct(action){{
  const skus=[..._sel];if(!skus.length)return;
  const tmpl=_BULK_CONFIRM[action];
  if(tmpl&&!confirm(tmpl.replace('{{n}}',skus.length)))return;
  const btn=document.getElementById('bulk-run');
  if(btn){{btn.disabled=true;btn.textContent='…';}}
  try{{
    const r=await fetch('/api/bulk/action',{{method:'POST',headers:{{Authorization:AUTH,'Content-Type':'application/json'}},body:JSON.stringify({{skus,action}})}});
    const d=await r.json();
    const msg=d.ok?`${{action}}: ${{d.count??skus.length}} queued/done`:`${{action}} errors: ${{(d.errors||[]).slice(0,3).join('; ')}}`;
    alert(msg);
    const reload=action==='mark_sold'||action==='delete'||action==='archive';
    _clearSel();
    if(reload)load(0);
  }}catch(err){{alert('Network error: '+err);}}
  finally{{
    if(btn){{btn.textContent='▶ Run';btn.disabled=!document.getElementById('bulk-sel')?.value;}}
  }}
}}
function _bulkActSel(){{
  const action=document.getElementById('bulk-sel')?.value;
  if(action)_bulkAct(action);
}}
function _rowHtml(it){{
  const pf=parseFloat(it.price);const price=isNaN(pf)?'—':'$'+pf.toFixed(2);
  return `<tr>
    <td><img class="lt-thumb" src="/thumb/${{it.sku}}" loading="lazy" onerror="this.style.visibility='hidden'" alt=""></td>
    <td><a href="/form/operator/items/${{it.sku}}" class="lt-sku">${{esc(it.sku)}}</a></td>
    <td><a href="/form/operator/items/${{it.sku}}" class="lt-title">${{esc(it.title||'')}}</a></td>
    <td><span class="sbadge ${{scls(it.status)}}">${{esc(it.status||'—')}}</span></td>
    <td style="color:#888;font-size:.8em">${{esc(it.location||'')}}</td>
    <td class="lt-price">${{price}}</td>
    <td style="display:flex;gap:4px">
      <select class="csel" style="font-size:.72em" onchange="this.nextElementSibling.disabled=!this.value"><option value="">Action…</option>${{_cardOpts(it)}}</select>
      <button class="crun lt-run" disabled onclick="cact(event,'${{esc(it.sku)}}',this.previousElementSibling.value)">▶</button>
    </td></tr>`;
}}
async function cact(e,sku,action){{
  e.preventDefault();e.stopPropagation();
  const btn=e.currentTarget,orig=btn.textContent;
  btn.disabled=true;btn.textContent='…';
  try{{
    const r=await fetch('/api/items/'+sku+'/action',{{
      method:'POST',headers:{{Authorization:AUTH,'Content-Type':'application/json'}},
      body:JSON.stringify({{action}})
    }});
    const d=await r.json();
    btn.textContent=d.ok?'✓':'!';
  }}catch(err){{btn.textContent='!';}}
  setTimeout(()=>{{btn.textContent=orig;btn.disabled=false;}},1600);
}}
async function _fetchPage(offset,append){{
  const search=document.getElementById('sq').value;
  const loc=document.getElementById('loc').value;
  const status=document.querySelector('#status-chips .chip.active')?.dataset.s??'';
  const lim=getLim();
  const p=new URLSearchParams({{limit:lim,offset:offset}});
  if(search)p.set('search',search);
  if(loc)p.set('location',loc);
  if(status)p.set('status_filter',status);
  if(!append)document.getElementById('grid').innerHTML='<div class="loading">Loading…</div>';
  let r,d;
  try{{r=await fetch('/api/items?'+p,{{headers:{{Authorization:AUTH}}}});d=await r.json();}}
  catch(e){{
    if(!append)document.getElementById('grid').innerHTML='<div class="loading">Network error</div>';
    return;
  }}
  if(!r.ok||!d.ok){{
    if(!append)document.getElementById('grid').innerHTML='<div class="loading">Error: '+esc(d.detail||d.error||r.status)+'</div>';
    return;
  }}
  _total=d.count;
  _off=offset+d.items.length;
  document.getElementById('sum').textContent=d.count+' item'+(d.count===1?'':'s');
  if(!d.items.length&&!append){{
    document.getElementById('grid').innerHTML='<div class="no-results">No items found.</div>';
    document.getElementById('pager').innerHTML='';
    return;
  }}
  if(_view==='list'){{
    const tbody=append?document.getElementById('lt-body'):null;
    if(!append||!document.getElementById('lt-body')){{
      document.getElementById('grid-wrap').innerHTML=
        '<table class="list-table"><thead><tr>'+
        '<th></th><th>SKU</th><th>Title</th><th>Status</th><th>Location</th><th>Price</th><th></th>'+
        '</tr></thead><tbody id="lt-body">'+d.items.map(_rowHtml).join('')+'</tbody></table>';
    }}else{{
      const tb=document.getElementById('lt-body');
      if(tb)tb.insertAdjacentHTML('beforeend',d.items.map(_rowHtml).join(''));
    }}
  }}else{{
    const html=d.items.map(_cardHtml).join('');
    if(!document.getElementById('grid')){{
      document.getElementById('grid-wrap').innerHTML='<div class="grid" id="grid"></div>';
    }}
    if(append){{
      const grid=document.getElementById('grid');
      if(grid){{const tmp=document.createElement('div');tmp.innerHTML=html;while(tmp.firstChild)grid.appendChild(tmp.firstChild);}}
    }}else{{
      document.getElementById('grid-wrap').innerHTML='<div class="grid" id="grid">'+html+'</div>';
    }}
  }}
  const rem=_total-_off;
  document.getElementById('pager').innerHTML=rem>0
    ?`<button onclick="loadMore()">Load more (${{rem}} remaining)</button>`
    :(_off>lim?'<span style="color:#555;font-size:.85em">All items loaded</span>':'');
}}
function load(o){{_off=0;_fetchPage(o??0,false);}}
function loadMore(){{_fetchPage(_off,true);}}
let _t;
function df(){{
  clearTimeout(_t);
  syncURLParam('search',document.getElementById('sq').value);
  syncURLParam('location',document.getElementById('loc').value);
  _t=setTimeout(()=>load(0),300);
}}
initChips('#status-chips',c=>{{syncURLParam('status',c.dataset.s);load(0);}});
document.getElementById('pg-sel').addEventListener('change',()=>syncURLParam('page_size',getLim()));

// Restore view state from the URL (bookmark / Back-button return) before
// the first load — reads back whatever syncURLParam() above last wrote.
(function _restoreFromURL(){{
  const st=getURLParam('status');
  if(st){{
    document.querySelectorAll('#status-chips .chip').forEach(c=>{{
      c.classList.toggle('active',c.dataset.s===st);
    }});
  }}
  const search=getURLParam('search'); if(search)document.getElementById('sq').value=search;
  const loc=getURLParam('location'); if(loc)document.getElementById('loc').value=loc;
  const ps=getURLParam('page_size'); if(ps)document.getElementById('pg-sel').value=ps;
  const v=getURLParam('view'); if(v==='list')setView('list',true);
}})();
load(0);
</script>
</body>
</html>
"""


_GENERIC_CONDITION_FALLBACK: List[Tuple[str, str]] = [
    ("NEW", "New"),
    ("LIKE_NEW", "Like New"),
    ("EXCELLENT_REFURBISHED", "Excellent – Refurbished"),
    ("VERY_GOOD_REFURBISHED", "Very Good – Refurbished"),
    ("GOOD_REFURBISHED", "Good – Refurbished"),
    ("USED_EXCELLENT", "Used – Excellent"),
    ("USED_VERY_GOOD", "Used – Very Good"),
    ("USED_GOOD", "Used – Good"),
    ("USED_ACCEPTABLE", "Used – Acceptable"),
    ("FOR_PARTS_OR_NOT_WORKING", "For Parts / Not Working"),
]


def _build_condition_options(current_enum: str, category_id: str = "") -> Tuple[str, bool]:
    """Return <option> tags for eBay condition enum dropdown.

    Sourced from the item's real per-category eBay condition policy (Metadata API
    get_item_condition_policies, cached in tgw.apis.ebay.conditions) — eBay groups
    all categories into ~26 condition sets and most categories allow only 1-2 of
    the 10 generic Inventory API condition enums, not all of them. Previously this
    always rendered the full generic list regardless of category, which both
    over-offered choices eBay would reject at publish time and under-labeled the
    ones it does allow (e.g. conditionId 3000 is simply "Used" for many categories,
    not "Used – Excellent/Good/Acceptable" as separate grades).

    Falls back to the generic list only when no cached policy exists for this
    category (e.g. not yet in the Metadata API cache).
    """
    conds: List[Tuple[str, str]] = []
    if category_id:
        try:
            from .apis.ebay.conditions import allowed_conditions_for_category

            seen: set = set()
            for c in allowed_conditions_for_category(_cfg, category_id):
                pair = (c["condition_enum"], c["condition_label"])
                if pair[0] not in seen:
                    conds.append(pair)
                    seen.add(pair[0])
        except Exception as exc:
            log.warning("condition options: policy lookup failed for category %s: %s", category_id, exc)

    if not conds:
        conds = list(_GENERIC_CONDITION_FALLBACK)

    # If the currently-saved enum isn't in the allowed set (e.g. a stale value from
    # before this fix, or the category changed since it was set), surface it anyway
    # so the operator sees and corrects it rather than have it silently vanish.
    # PP-CONDITION-ENUM-001 / todo #1562: this is also the invalid-flag signal
    # the caller uses to redden the <select> border on initial page render —
    # same shared flagFieldInvalid() treatment the dynamic loadCatCtx() JS
    # re-render path already applies via its own `stillValid` check.
    is_invalid = bool(current_enum) and current_enum not in {v for v, _ in conds}
    if is_invalid:
        conds = [(current_enum, f"{current_enum} — not valid for this category, please fix")] + conds

    opts = []
    if not current_enum:
        opts.append('<option value="" selected disabled>— select —</option>')
    for val, lbl in conds:
        sel = " selected" if val == current_enum else ""
        opts.append(f'<option value="{val}"{sel}>{lbl}</option>')
    return "".join(opts), is_invalid


def _render_item_detail_html(
    sku: str,
    item: Dict[str, Any],
    images: List[str],
    videos: List[str],
    jobs: List[Dict[str, Any]],
    api_key: str = "",
    workflow_card: Dict[str, Any] | None = None,
    operator_object: Dict[str, Any] | None = None,
) -> str:
    import html as _html

    h = _html.escape

    operator_object_html = ""
    if operator_object:
        operator_workflow = operator_object.get("workflow") or {}
        field_schema = operator_object.get("field_schema") or {}
        condition = field_schema.get("condition") or {}
        command_buttons = "".join(
            _abtn_html
            for command in operator_object.get("commands") or []
            for _abtn_html in [
                (
                    '<button class="act-btn" '
                    + ("" if command.get("enabled") else "disabled ")
                    + f'title="{h(str(command.get("reason") or ""))}" '
                    + 'style="background:#102a18;border-color:#4a4;color:#8e8" '
                    + f'onclick="executePublishedCommand({json.dumps(command.get("id"))})">'
                    + h(str(command.get("label") or command.get("id") or "Command"))
                    + "</button>"
                )
            ]
        )
        reasons = operator_workflow.get("reasons") or []
        reason_html = '<ul style="margin:6px 0 0">' + "".join(f"<li>{h(str(reason))}</li>" for reason in reasons) + "</ul>" if reasons else ""
        operator_object_html = (
            '<section id="published-operator-object" style="border:1px solid #4a6;'
            'background:#101b16;border-radius:8px;padding:10px 14px;margin:10px 0">'
            '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">'
            "<strong>Current item workflow</strong>"
            f'<span style="color:#9bd">{h(str(operator_workflow.get("state") or "unknown"))}</span>'
            f'<span style="font-size:.76em;color:#789">generation '
            f"{h(str(operator_object.get('object_generation') or ''))[:12]}</span>"
            f'<span style="margin-left:auto;display:flex;gap:7px">{command_buttons}</span>'
            "</div>"
            f'<div style="font-size:.8em;color:#9a9;margin-top:5px">Condition: '
            f"{h(str(condition.get('label') or condition.get('value') or 'not set'))}</div>"
            f"{reason_html}</section>"
        )

    workflow_card_html = ""
    photo_fingerprint: Dict[str, Any] | None = None
    item_photos_fingerprint: Dict[str, Any] | None = None
    if workflow_card:
        goal = workflow_card.get("goal") or {}
        fingerprints = workflow_card.get("fingerprints") or []
        photo_fingerprint = next((fp for fp in fingerprints if fp.get("condition_id") == "photos_uploaded"), None)
        item_photos_fingerprint = next((fp for fp in fingerprints if fp.get("condition_id") == "item_has_photos"), None)
        photo_state_html = ""
        if photo_fingerprint:
            photo_ready = photo_fingerprint.get("result") == "true"
            photo_reason = "; ".join(str(reason) for reason in photo_fingerprint.get("reasons", []))
            photo_state_html = (
                f'<div id="photo-sync-fingerprint" style="margin:7px 0;padding:6px 9px;'
                f"border-radius:5px;background:{'#102a18' if photo_ready else '#30220b'};"
                f'color:{"#8e8" if photo_ready else "#fd8"}">'
                f"<strong>{'Photo sync ready' if photo_ready else 'Waiting for photo sync'}</strong>"
                f" — {h(photo_reason)}</div>"
            )
        fp_html = "".join(
            f"<li><code>{h(str(fp.get('condition_id', '')))}</code>: "
            f"{h(str(fp.get('result', '')))} — "
            f"{h('; '.join(str(reason) for reason in fp.get('reasons', [])))} "
            f"<small>[{h(', '.join(str(ref.get('identity', '')) for ref in fp.get('evidence', [])))}]</small></li>"
            for fp in fingerprints
        )
        actions = workflow_card.get("legal_actions") or []
        actions_html = (
            "".join(f"<li><code>{h(str(action.get('treatment_id', '')))}</code> — {h(str(action.get('action', '')))}</li>" for action in actions)
            or "<li>None — waiting for evidence, authority, or goal satisfaction.</li>"
        )
        gates = list(workflow_card.get("operator_gates") or [])
        waits = workflow_card.get("waiting_treatments") or []
        waits_html = "".join(f"<li><code>{h(str(wait.get('treatment_id', '')))}</code> — {h('; '.join(str(reason) for reason in wait.get('reasons', [])))}</li>" for wait in waits) or "<li>None.</li>"
        active = workflow_card.get("active_attempts") or []
        active_html = (
            "".join(
                f"<li><code>{h(str(attempt.get('treatment_id') or attempt.get('queue_name') or ''))}</code> — "
                f"{h(str(attempt.get('state', '')))}"
                f"{' until ' + h(str(attempt.get('not_before'))) if attempt.get('not_before') else ''} "
                f"(job {h(str(attempt.get('job_id', '')))})</li>"
                for attempt in active
            )
            or "<li>None.</li>"
        )
        gate_html = '<div style="color:#f99"><strong>Operator gates:</strong> ' + h(", ".join(str(gate) for gate in gates)) + "</div>" if gates else ""
        workflow_card_html = (
            '<section id="workflow-action-card" style="border:1px solid #345;'
            'background:#111a22;border-radius:8px;padding:10px 14px;margin:10px 0">'
            '<h3 style="margin:0 0 6px">Workflow Action Card</h3>'
            f"<div>Goal: <code>{h(str(goal.get('id', '')))}</code> "
            f"v{h(str(goal.get('version', '')))}</div>"
            f'<div style="font-size:.76em;color:#789">Generation '
            f"{h(str(workflow_card.get('object_generation', '')))[:12]} · graph "
            f"{h(str(workflow_card.get('graph_id', '')))[:12]}</div>"
            f"{photo_state_html}{gate_html}<details><summary>Fingerprints ({len(fingerprints)})</summary>"
            f"<ul>{fp_html}</ul></details><details><summary>Waits ({len(waits)})</summary>"
            f"<ul>{waits_html}</ul></details><div><strong>Legal actions</strong><ul>{actions_html}</ul></div>"
            f"<details><summary>Active attempts ({len(active)})</summary><ul>{active_html}</ul></details>"
            f'<div style="font-size:.8em;color:#789">Attempt history: '
            f"{len(workflow_card.get('attempts') or [])}</div></section>"
        )

    def fv(key: str) -> str:
        v = item.get(key)
        return h(str(v)) if v is not None else '<span style="color:#444">—</span>'

    def fr(label: str, val: str = "", key: str = "", editable: bool = False, lockable: bool = False) -> str:
        display = val if val else fv(key)
        # Padlock for top-level "base data" fields (Dave, 2026-07-18: title/
        # description need the same follow-eBay-unless-locked behavior as
        # item_attributes aspects — same toggleInventoryLock()/lock list,
        # just keyed by the top-level field name instead of an aspect name).
        lock_html = ""
        if lockable and key:
            _locked = inventory_record.is_locked(item, key)
            lock_html = (
                f" <button onclick='toggleInventoryLock({json.dumps(key)},{json.dumps(_locked)},this)' "
                f'title="{"Locked — click to let this follow the eBay draft again" if _locked else "Unlocked — click to freeze this at its current value"}" '
                f'style="background:none;border:none;cursor:pointer;font-size:.85em;padding:0 2px">{"🔒" if _locked else "🔓"}</button>'
            )
        if editable and key:
            raw = item.get(key)
            raw_str = h(str(raw)) if raw is not None else ""
            return (
                f'<div class="frow"><span class="fn">{label}</span><span class="fv editable" data-field="{h(key)}" data-raw="{raw_str}" title="Double-click to edit">{display}</span>{lock_html}</div>'
            )
        return f'<div class="frow"><span class="fn">{label}</span><span class="fv">{display}</span>{lock_html}</div>'

    # Gallery
    if images:
        main_src = f"/media/{h(sku)}/{h(images[0])}"
        strip_items = []
        for i, img in enumerate(images):
            mv_buttons = ""
            if i > 1:
                mv_buttons += f'<button class="strip-mv" onclick="mvFront({i})" title="Move to front">⇑</button>'
            if i > 0:
                mv_buttons += f'<button class="strip-mv" style="top:{"1px" if i <= 1 else "22px"}" onclick="mvPhoto({i})" title="Move earlier">↑</button>'
            strip_items.append(
                f'<div class="strip-item">'
                f'<img src="/media/{h(sku)}/{h(img)}" class="{"active" if i == 0 else ""}"'
                f' onclick="smP(this,{i})" loading="lazy" alt="" data-name="{h(img)}">'
                f"{mv_buttons}"
                f"</div>"
            )
        strip = "".join(strip_items)
        photos_json = json.dumps(images)
        gallery_html = (
            f'<div class="gallery">'
            f'<div class="lb-overlay" id="lb" onclick="lbClose()">'
            f'<button class="lb-close" onclick="lbClose();event.stopPropagation()">✕</button>'
            f'<img class="lb-img" id="lb-img" src="data:," alt="">'
            f"</div>"
            f'<img class="main-photo" id="mp" src="{main_src}" alt="" onclick="lbOpen(this.src)">'
            f'<div class="strip" id="photo-strip">{strip}</div>'
            f"</div>"
            f"<script>"
            f"var _photos={photos_json};"
            f"function smP(el,idx){{"
            f"document.getElementById('mp').src=el.src;"
            f"document.querySelectorAll('.strip img').forEach(i=>i.classList.remove('active'));"
            f"el.classList.add('active');"
            f"}}"
            f"function lbOpen(src){{var o=document.getElementById('lb');o.classList.add('open');document.getElementById('lb-img').src=src;}}"
            f"function lbClose(){{document.getElementById('lb').classList.remove('open');}}"
            f"document.addEventListener('keydown',function(e){{if(e.key==='Escape')lbClose();}});"
            f"function refreshStrip(){{"
            f"var strip=document.getElementById('photo-strip');"
            f"if(!strip)return;"
            f"var nameToItem={{}};"
            f"strip.querySelectorAll('.strip-item').forEach(function(el){{"
            f"var img=el.querySelector('img[data-name]');"
            f"if(img)nameToItem[img.dataset.name]=el;"
            f"}});"
            f"_photos.forEach(function(n){{if(nameToItem[n])strip.appendChild(nameToItem[n]);}});"
            f"var mp=document.getElementById('mp');"
            f"if(mp&&_photos.length&&nameToItem[_photos[0]]){{"
            f"var fi=nameToItem[_photos[0]].querySelector('img');if(fi)mp.src=fi.src;}}"
            f"strip.querySelectorAll('.strip-item img').forEach(function(i,idx){{"
            f"i.classList.toggle('active',idx===0);}});"
            f"}}"
            f"function mvPhoto(idx){{"
            f"if(idx<1)return;"
            f"var t=_photos[idx-1];_photos[idx-1]=_photos[idx];_photos[idx]=t;"
            f"refreshStrip();savePhotoOrder();"
            f"}}"
            f"function mvFront(idx){{"
            f"if(idx<1)return;"
            f"var item=_photos.splice(idx,1)[0];"
            f"_photos.unshift(item);"
            f"refreshStrip();savePhotoOrder();"
            f"}}"
            f"function savePhotoOrder(){{"
            f"fetch('/api/items/'+_SKU+'/photo-order',{{"
            f"method:'POST',"
            f"headers:authHeaders({{'Content-Type':'application/json'}}),"
            f"body:JSON.stringify({{order:_photos}})"
            f"}}).then(r=>r.json()).then(d=>{{"
            f"if(d.ok)location.reload();"
            f"else alert('Photo order save failed: '+(d.detail||'error'));"
            f"}}).catch(e=>alert('Network error: '+e));"
            f"}}"
            f"</script>"
        )
    else:
        gallery_html = '<div style="color:#555;padding:30px;text-align:center">No photos</div>'

    # Video strip
    if videos:
        video_items = "".join(
            f'<div class="strip-item video-item"><video src="/media/{h(sku)}/{h(vid)}" class="strip-vid" onclick="window.open(this.src,\'_blank\')" preload="none"></video></div>' for vid in videos
        )
        video_strip = f'<div class="video-strip"><div class="video-strip-hdr">VIDEO</div><div class="strip">{video_items}</div></div>'
        gallery_html += video_strip

    # eBay sub-docs
    eb = item.get("ebay_listing") or {}
    eo = item.get("ebay_offer") or {}
    _ebay_live_raw = item.get("ebay_live") or {}
    dl = item.get("draft_listing") or {}

    listing_id = eb.get("listing_id") or item.get("listing_id", "")
    listing_url = eb.get("listing_url") or item.get("listing_url", "")
    listing_status = (eb.get("status") or "").strip()
    offer_status = (eo.get("status") or "").strip()
    offer_price = eo.get("price")
    is_active = listing_status.lower() in ("active",) or offer_status.upper() in ("PUBLISHED",)
    _is_staged = offer_status.upper() in ("UNPUBLISHED", "PUBLISHED")
    is_ready = bool(eo.get("ready_at")) and offer_status.upper() == "UNPUBLISHED"

    # Resolved display price (offer > internal > draft)
    _price_val = offer_price if offer_price is not None else item.get("price")
    if _price_val is None:
        _price_val = dl.get("price")
    try:
        price_str = f"${float(_price_val):.2f}" if _price_val is not None else "—"
    except (ValueError, TypeError):
        price_str = "—"

    # eBay listing section HTML
    ebay_link_parts: List[str] = []
    if listing_url:
        ebay_link_parts.append(f'<a class="ebay-btn ebay-btn-primary" href="{h(listing_url)}" target="_blank" rel="noopener noreferrer">View on eBay ↗</a>')
    if listing_id:
        sh_url = f"https://www.ebay.com/sh/lst/active?keyword={h(listing_id)}"
        ebay_link_parts.append(f'<a class="ebay-btn ebay-btn-sec" href="{sh_url}" target="_blank" rel="noopener noreferrer">Seller Hub ↗</a>')
    if is_active:
        ebay_link_parts.append('<a class="ebay-btn ebay-btn-sec" href="https://messages.ebay.com/" target="_blank" rel="noopener noreferrer">eBay Messages ↗</a>')
    if listing_id:
        ebay_link_parts.append('<span id="offer-badge-wrap"></span>')
    ebay_links_html = f'<div class="ebay-links">{"".join(ebay_link_parts)}</div>' if ebay_link_parts else ""

    # eBay listing confirmed data
    def _safe_price(v: Any) -> str:
        try:
            return f"${float(v):.2f}" if v is not None else "—"
        except (ValueError, TypeError):
            return "—"

    _live_price = eb.get("live_price")
    _live_price_html = ""
    if _live_price is not None:
        _lp_str = _safe_price(_live_price)
        if offer_price is not None:
            try:
                _diverged = abs(float(_live_price) - float(offer_price)) > 0.01
            except (TypeError, ValueError):
                _diverged = False
            if _diverged:
                _lp_str += f' <span style="color:#f99;font-size:.79em">⚠ differs from offer ({_safe_price(offer_price)})</span>'
        _live_price_html = fr("Live Price (eBay)", _lp_str)
    listing_section = (
        fr("Listing ID", h(listing_id) if listing_id else "")
        + fr("eBay Status", h(listing_status) if listing_status else "")
        + fr("Published", h(_local_ts(eb.get("published_at"))))
        + fr("API", h(str(eb.get("api", "") or "")))
        + _live_price_html
        + ebay_links_html
    )

    # Pricing history expandable section (todo 877)
    _comps = eo.get("price_comps") or {} if eo else {}
    _price_source = eo.get("price_source", "") if eo else ""
    _target_price = eo.get("target_price") if eo else None
    _priced_at = _local_ts(eo.get("priced_at")) if eo else ""
    _category_group_name = item.get("category_group", "")
    # category-group floor/typical from config (via item fields populated by ebay_price)
    _floor = None
    _typical = None
    try:
        _cg_raw = _cfg.get("raw", {}).get("category_groups", {}).get(_category_group_name, {}) if _cfg else {}
        _floor = _cg_raw.get("floor_used") or _cg_raw.get("floor")
        _typical = _cg_raw.get("typical_used") or _cg_raw.get("typical")
    except Exception:
        pass
    _ph_rows = ""

    def _cfmt(v: Any) -> str:
        try:
            return f"${float(v):.2f}"
        except (TypeError, ValueError):
            return "—"

    # PP-ACTIONCONSOLE-001: comp stats/listings dropped from here — they were
    # redundant with the comps range-bar panel next to the price field in the
    # editor, which is the single place comps appear now.
    if _floor is not None:
        _ph_rows += f'<div class="frow"><span class="fn">Category floor</span><span class="fv">${float(_floor):.2f}</span></div>'
    if _typical is not None:
        _ph_rows += f'<div class="frow"><span class="fn">Category typical</span><span class="fv">${float(_typical):.2f}</span></div>'
    if _target_price is not None:
        _ph_rows += f'<div class="frow"><span class="fn">Repricer floor</span><span class="fv">${float(_target_price):.2f}</span></div>'
    if _price_source:
        _ph_rows += f'<div class="frow"><span class="fn">Price source</span><span class="fv">{h(_price_source)}</span></div>'
    if _priced_at:
        _ph_rows += f'<div class="frow"><span class="fn">Priced at</span><span class="fv">{h(_priced_at)}</span></div>'
    # (comp listings table removed — see comps range-bar panel in the editor;
    # _ph_rows carries pricing context rows into the left-column Price History)

    offer_section = (
        (
            fr("Offer ID", h(str(eo.get("offer_id", "") or "")))
            + fr("Offer Status", h(offer_status) if offer_status else "")
            + fr("Offer Price", _safe_price(offer_price))
            + fr("eBay Category", h(str(eo.get("category_id", "") or "")))
            + fr("Quantity", h(str(eo.get("quantity", "") or "")))
            + fr("Published At", h(_local_ts(eo.get("published_at"))))
            + fr("Staged At", h(_local_ts(eo.get("staged_at"))))
        )
        if eo
        else '<div class="frow"><span class="fv" style="color:#555">No offer yet</span></div>'
    )

    # Draft listing data (what we prepared for eBay)
    if dl:
        _dl_raw_price = dl.get("price")
        if _dl_raw_price is None and offer_price is not None:
            dl_price_str = _safe_price(offer_price) + ' <span style="color:#888;font-size:.78em">(from offer)</span>'
        else:
            dl_price_str = _safe_price(_dl_raw_price)
        q = dl.get("quality") or {}
        q_score = q.get("score", "—")
        q_flags = ", ".join(q.get("flags", [])) or "—"
        req_fill = dl.get("aspects_required_filled", "—")
        req_total = dl.get("aspects_required_total", "—")
        rec_fill = dl.get("aspects_recommended_filled", "—")
        rec_total = dl.get("aspects_recommended_total", "—")
        title_flags = ", ".join(dl.get("title_flags", [])) or "—"
        _dl_store_cat = str(dl.get("store_category_name") or dl.get("store_category") or "")
        _dl_ship = str(dl.get("shipping_profile") or dl.get("fulfillment_policy_id") or "")
        # Build item_specifics table
        # todo #1418: Set B read via tgw.ebay.draft_specifics (the sanctioned accessor)
        _specifics = get_ebay_aspects(item)
        if _specifics:
            _spec_rows = "".join(f'<tr><td class="spec-k">{h(k)}</td><td class="spec-v">{h(str(v))}</td></tr>' for k, v in sorted(_specifics.items()))
            _spec_html = f'<table class="spec-tbl"><tr><th>Aspect</th><th>Value</th></tr>{_spec_rows}</table>'
        else:
            _spec_html = '<span style="color:#555">None filled</span>'
        _spec_note = f' <span style="color:#888;font-size:.75em">({req_fill}/{req_total} req, {rec_fill}/{rec_total} rec)</span>'
        # Listing description preview — strip the hidden picklist line before display
        import re as _re

        _ld_raw = str(dl.get("listing_description") or dl.get("description") or "")
        _ld_clean = _re.sub(r"<p>[^<]*tgw-pl::[^<]*</p>", "", _ld_raw).strip()
        _ld_html = f'<div class="listing-preview">{_ld_clean}</div>' if _ld_clean else '<span style="color:#555">—</span>'
        _offline_warn = (
            '<div style="background:#2a1a00;border:1px solid #664400;border-radius:6px;'
            'padding:6px 10px;margin-bottom:6px;color:#fb7;font-size:.82em">'
            "⚠ Offline draft — created without live eBay data; aspects and category may be incomplete."
            "</div>"
            if dl.get("offline_draft")
            else ""
        )
        _cat_conf = dl.get("category_confidence") or ""
        _pl_cat = (item.get("product_lookup") or {}).get("category_name") or ""
        _pl_cat_id = (item.get("product_lookup") or {}).get("ebay_category_id") or ""
        if _cat_conf and _cat_conf not in ("high", ""):
            _cat_conf_warn = f'<span style="color:#fb7;font-size:.79em"> ⚠ confidence: {h(_cat_conf)}</span>'
            if _pl_cat and _pl_cat != dl.get("category_name", ""):
                _cat_conf_warn += (
                    f'<div style="margin-top:4px;font-size:.79em;color:#aaa">'
                    f"AI: {h(str(dl.get('category_id', '')))} · {h(str(dl.get('category_name', '')))} "
                    f"vs Lookup: {h(_pl_cat_id)} · {h(_pl_cat)}"
                    f"</div>"
                )
        else:
            _cat_conf_warn = ""
        _draft_section = (
            _offline_warn
            + fr("Draft Title", h(str(dl.get("title", "") or "")))
            + fr("Category Sent", h(f"{dl.get('category_id', '')} · {dl.get('category_name', '')}") + _cat_conf_warn)
            + (fr("Store Category", h(_dl_store_cat)) if _dl_store_cat else "")
            + (fr("Shipping Policy", h(_dl_ship)) if _dl_ship else "")
            + fr("Condition Sent", h(f"{dl.get('condition_label', '')} ({dl.get('condition_enum', '')})"))
            + fr("Draft Price", dl_price_str)
            + fr("Quality Score", h(str(q_score)))
            + fr("Quality Flags", h(q_flags))
            + fr("Title Flags", h(title_flags))
            + f'<div class="frow"><span class="fn">Item Specifics{_spec_note}</span><span class="fv">{_spec_html}</span></div>'
            + f'<div class="frow listing-desc-row"><span class="fn">Listing Description</span><span class="fv">{_ld_html}</span></div>'
        )
    else:
        _draft_section = "&"

    # Revision draft diff
    _revision_draft_html = ""
    revision = item.get("revision_draft")
    if revision:
        delta = revision.get("delta") or {}
        baseline = revision.get("baseline") or {}
        snap = baseline.get("snapshot") or {}
        by = h(str(revision.get("by", "")))
        at = h(str(revision.get("at", "")))[:19]
        bhash = h(str(baseline.get("hash", "")))[:12]
        if delta:
            rev_rows = "".join(f'<tr><td class="dfield">{h(f)}</td><td class="dwas">{h(str(snap.get(f, "—")))}</td><td class="dnow">{h(str(v))}</td></tr>' for f, v in delta.items())
            _revision_draft_html = (
                f'<h3 class="diff-hdr">Revision Draft</h3>'
                f'<div class="diff-meta">by {by} · {at} · baseline {bhash}…</div>'
                f'<table class="dtable">'
                f"<tr><th>Field</th><th>Current</th><th>Proposed</th></tr>"
                f"{rev_rows}"
                f"</table>"
            )

    # Pipeline jobs
    jobs_html = ""
    if jobs:
        job_rows = ""
        for j in jobs[:10]:
            state = j.get("state", "")
            sc = "js-" + state.replace("_", "-").lower()
            ts = _local_ts(j.get("updated_at") or j.get("finished_at") or j.get("created_at"))
            _err_full = str(j.get("error_detail") or "")
            _err_short = _err_full[:80]
            # PP-COHESION-001: retry_wait is a transient/expected backoff, not
            # a fatal error like failed/dead_letter — give it a distinct
            # warning (yellow) color instead of the same red so an operator
            # scanning the pipeline log can tell "will retry itself" apart
            # from "needs a human" at a glance (Dave, 2026-07-14).
            if state in ("failed", "dead_letter"):
                _err_color, _err_bg = "#f99", "#1a0a0a"
            elif state == "retry_wait":
                _err_color, _err_bg = "#fd8", "#2a2000"
            else:
                _err_color, _err_bg = "#f99", "#1a0a0a"
            if _err_full:
                err = (
                    f'<details style="display:inline">'
                    f'<summary style="cursor:pointer;color:{_err_color};font-size:.8em;list-style:none">'
                    f"{h(_err_short)}{'…' if len(_err_full) > 80 else ''}</summary>"
                    f'<pre style="white-space:pre-wrap;word-break:break-all;font-size:.75em;'
                    f'color:{_err_color};margin:4px 0;padding:4px;background:{_err_bg};border-radius:4px">'
                    f"{h(_err_full)}</pre>"
                    f"</details>"
                )
            else:
                err = ""
            qn = j.get("queue_name", "")
            tip = _WORKER_TOOLTIPS.get(qn, "")
            tip_attr = f' title="{h(tip)}"' if tip else ""
            consumer = j.get("consumer") if isinstance(j.get("consumer"), dict) else {}
            consumer_status = str(consumer.get("status") or "")
            consumer_reason = str(consumer.get("reason") or "")
            consumer_html = ""
            scheduled_at = j.get("scheduled_at")
            if scheduled_at is not None:
                consumer_html = f' <span style="color:#8df;font-size:.78em">— scheduled {_local_ts(scheduled_at)}</span>'
            if state in ("queued", "leased", "running", "retry_wait") and consumer_status != "active":
                consumer_html += f' <span title="{h(consumer_reason)}" style="color:#fd8;font-size:.78em">— {h(consumer_reason)}</span>'
            # PP-ACTIONCONSOLE-001: contextual repair — a Retry button appears
            # ONLY on actionable failure states (zero buttons in the happy path).
            _retry_btn = ""
            if state == "dead_letter" and j.get("job_id") and j.get("retry_allowed", True):
                _retry_btn = (
                    f' <button class="act-btn" style="font-size:.72em;padding:1px 8px;background:#2a0d0d;border-color:#a44;color:#e88" onclick="retryJob(\'{h(str(j["job_id"]))}\')">Retry</button>'
                )
            job_rows += f'<tr><td{tip_attr}>{h(qn)}</td><td class="{sc}">{h(state)}{consumer_html}{_retry_btn}</td><td style="color:#666;font-size:.8em">{h(ts)}</td><td style="color:{_err_color};font-size:.8em">{err}</td></tr>'
        jobs_html = f'<table class="jtable"><tr><th>Queue</th><th>State</th><th>Updated</th><th>Error</th></tr>{job_rows}</table>'

    title = item.get("title", "")

    # Status badge for the listing section header
    listing_badge = ""
    if is_active:
        listing_badge = ' <span class="lbadge lbadge-active">Active</span>'
    elif listing_id:
        listing_badge = ' <span class="lbadge lbadge-inactive">Inactive</span>'

    # Offer badge for offer section
    offer_badge = ""
    if offer_status:
        badge_cls = "lbadge-active" if offer_status.upper() == "PUBLISHED" else "lbadge-pending"
        offer_badge = f' <span class="lbadge {badge_cls}">{h(offer_status)}</span>'

    # Effective qty: top-level → draft_listing.quantity
    _qty_raw = item.get("qty")
    _qty_display = (
        h(str(_qty_raw))
        if _qty_raw is not None
        else (h(str(dl.get("quantity"))) + ' <span style="color:#555;font-size:.78em">(from draft)</span>' if dl.get("quantity") is not None else '<span style="color:#444">—</span>')
    )
    # Effective price display — draft_listing.price is operator-set, show it first
    _price_raw = item.get("price")
    _dl_price_raw = dl.get("price")
    if _dl_price_raw is not None:
        try:
            _price_display = "$" + f"{float(_dl_price_raw):.2f}" + ' <span style="color:#555;font-size:.78em">(draft)</span>'
        except (ValueError, TypeError):
            _price_display = h(str(_dl_price_raw))
    elif _price_raw is not None:
        _price_display = h(price_str)
    elif offer_price is not None:
        _price_display = h(price_str) + ' <span style="color:#555;font-size:.78em">(from eBay offer)</span>'
    else:
        _price_display = '<span style="color:#444">—</span>'

    # ── Price history section (single merged pricing display, left column) ────
    # PP-ACTIONCONSOLE-001: absorbed the old offer-section "Pricing History"
    # dropdown — price-change events + pricing context in ONE place.
    _ph_events = item.get("price_history") or []
    _phev_table = ""
    if _ph_events:
        _phev_rows = ""
        for _ev in reversed(_ph_events):
            _ev_ts = h(_local_ts(_ev.get("ts")))
            _ev_price = h(f"${float(_ev.get('price', 0)):.2f}")
            _ev_prev = h(f"${float(_ev.get('previous_price', 0)):.2f}") if _ev.get("previous_price") is not None else "—"
            _ev_label = h(str(_ev.get("label") or _ev.get("stage") or ""))
            _ev_src = h(str(_ev.get("source") or ""))
            _phev_rows += (
                f"<tr>"
                f'<td style="color:#888;font-size:.8em">{_ev_ts}</td>'
                f'<td style="color:#bfb;font-weight:600">{_ev_price}</td>'
                f'<td style="color:#888">{_ev_prev}</td>'
                f'<td style="color:#aaa">{_ev_label}</td>'
                f'<td style="color:#666;font-size:.78em">{_ev_src}</td>'
                f"</tr>"
            )
        _phev_table = f'<table class="jtable"><tr><th>When</th><th>Price</th><th>Previous</th><th>Stage</th><th>Source</th></tr>{_phev_rows}</table>'
    if _phev_table or _ph_rows:
        price_history_html = (
            '<div class="dsec">'
            "<h3>Pricing History" + (f' <span style="font-size:.7em;color:#555;font-weight:normal">{len(_ph_events)} events</span>' if _ph_events else "") + "</h3>"
            '<div style="font-size:.73em;color:#556;margin-bottom:6px">Every price change recorded — launch through markdowns</div>'
            + _phev_table
            + (f'<div style="margin-top:6px;border-top:1px solid #222;padding-top:6px">{_ph_rows}</div>' if _ph_rows else "")
            + "</div>"
        )
    else:
        price_history_html = ""

    # ── Reprice schedule section ───────────────────────────────────────────────
    _rps = item.get("reprice_schedule") or []
    if _rps:
        _rps_rows = ""
        for _st in _rps:
            _st_label = h(str(_st.get("label") or _st.get("stage") or ""))
            _st_price = h(f"${float(_st.get('price', 0)):.2f}") if _st.get("price") is not None else "—"
            _st_due = h(_local_ts(_st.get("due_at")))
            _st_done = h(_local_ts(_st.get("done_at")))
            _done_cls = "color:#888" if _st.get("done_at") else "color:#fb7"
            _rps_rows += (
                f"<tr>"
                f'<td style="color:#aaa">{_st_label}</td>'
                f'<td style="color:#bfb">{_st_price}</td>'
                f'<td style="color:#888;font-size:.8em">{_st_due}</td>'
                f'<td style="{_done_cls};font-size:.8em">{_st_done or "pending"}</td>'
                f"</tr>"
            )
        reprice_schedule_html = (
            f'<div class="dsec">'
            f'<h3>Reprice Schedule <span style="font-size:.7em;color:#555;font-weight:normal">{len(_rps)} stages</span></h3>'
            f'<div style="font-size:.73em;color:#556;margin-bottom:6px">Automated markdown plan — pending stages fire automatically</div>'
            f'<table class="jtable">'
            f"<tr><th>Stage</th><th>Price</th><th>Due</th><th>Done</th></tr>"
            f"{_rps_rows}"
            f"</table>"
            f"</div>"
        )
    else:
        reprice_schedule_html = ""

    # ── Product lookup section ─────────────────────────────────────────────────
    _pl = item.get("product_lookup") or {}
    if _pl:
        _pl_rows = ""
        for _k, _v in (
            ("Brand", _pl.get("brand")),
            ("MPN", _pl.get("mpn")),
            ("MSRP", f"${float(_pl['msrp']):.2f}" if _pl.get("msrp") else None),
            ("Source", _pl.get("source")),
            ("Title", _pl.get("title")),
            ("UPC", _pl.get("upc")),
        ):
            if _v:
                _pl_rows += f'<div class="frow"><span class="fn">{h(_k)}</span><span class="fv">{h(str(_v))}</span></div>'
        product_lookup_html = (
            (
                f'<div class="dsec">'
                f'<h3>Product Lookup <span style="font-size:.7em;color:#555;font-weight:normal">{h(str(_pl.get("source", "")))})</span></h3>'
                f'<div style="font-size:.73em;color:#556;margin-bottom:6px">Structured product data enriching pricing and aspects</div>'
                f"{_pl_rows}"
                f"</div>"
            )
            if _pl_rows
            else ""
        )
    else:
        product_lookup_html = ""

    # ── Identification history section ─────────────────────────────────────────
    _id_hist = item.get("identification_history") or []
    if _id_hist:
        _idh_rows = ""
        for _ev in reversed(_id_hist[-10:]):  # most recent 10
            _idh_ts = h(_local_ts(_ev.get("ts")))
            _idh_event = h(str(_ev.get("event") or ""))
            _idh_title = h(str(_ev.get("title") or ""))
            _idh_cat = h(str(_ev.get("category") or ""))
            _idh_model = h(str(_ev.get("model") or ""))
            _idh_round = h(str(_ev.get("round") or ""))
            _idh_rows += (
                f"<tr>"
                f'<td style="color:#888;font-size:.79em">{_idh_ts}</td>'
                f'<td style="color:#7af;font-size:.79em">{_idh_event} r{_idh_round}</td>'
                f'<td style="color:#ddd;font-size:.79em">{_idh_title[:60]}</td>'
                f'<td style="color:#aaa;font-size:.79em">{_idh_cat[:30]}</td>'
                f'<td style="color:#666;font-size:.75em">{_idh_model}</td>'
                f"</tr>"
            )
        identification_history_html = (
            f'<div class="dsec">'
            f"<details>"
            f'<summary style="cursor:pointer;color:#4a8ade;font-weight:600;font-size:.9em">'
            f'Identification History <span style="color:#555;font-weight:normal">({len(_id_hist)} rounds)</span>'
            f"</summary>"
            f'<div style="font-size:.73em;color:#556;margin:4px 0 6px">AI identification rounds — title, category, model used</div>'
            f'<table class="jtable">'
            f"<tr><th>When</th><th>Event</th><th>Title</th><th>Category</th><th>Model</th></tr>"
            f"{_idh_rows}"
            f"</table>"
            f"</details>"
            f"</div>"
        )
    else:
        identification_history_html = ""

    # ── Phase 1A: EPS photo strip ─────────────────────────────────────────────
    _inv_item = _ebay_live_raw.get("inventory_item") or {}
    _eps_urls = (_inv_item.get("product") or {}).get("imageUrls") or (dl or {}).get("imageUrls") or []
    _local_eps = [e.get("url") for e in (item.get("ebay_photos") or []) if e.get("url")]
    _display_eps = _eps_urls or _local_eps
    if _display_eps:
        _eps_thumbs = "".join(
            f'<a href="{h(u)}" target="_blank" rel="noopener noreferrer"><img src="{h(u)}" style="height:80px;width:80px;object-fit:cover;border-radius:4px;border:1px solid #333;cursor:pointer"></a>'
            for u in _display_eps[:24]  # eBay's own per-listing max — the header count
        )  # already reports len(_display_eps); this cap must
        # match it or the strip silently under-renders
        # against its own label (Dave, 2026-07-17: an
        # old [:12] slice made photos look missing that
        # were actually live on eBay all along)
        _eps_strip_html = (
            f'<div id="eps-photos" class="dsec">'
            f"<h3>Photos on eBay"
            f' <span style="font-size:.7em;color:#555;font-weight:normal">'
            f"EPS hosted · {len(_display_eps)} photo(s)</span>"
            f' <button onclick="resyncPhotos()" style="font-size:.7em;margin-left:8px;padding:2px 8px;cursor:pointer">Resync Photos</button>'
            f' <span id="resync-photos-result" style="font-size:.7em;color:#8af;margin-left:6px"></span>'
            f"</h3>"
            f'<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:6px">'
            f"{_eps_thumbs}</div>"
            f"</div>"
        )
    else:
        _eps_strip_html = (
            '<div id="eps-photos" class="dsec">'
            '<div style="color:#555;font-size:.85em">No photos on eBay yet — run ebay_upload to upload photos to EPS'
            ' <button onclick="resyncPhotos()" style="font-size:.9em;margin-left:8px;padding:2px 8px;cursor:pointer">Resync Photos</button>'
            ' <span id="resync-photos-result" style="font-size:.9em;color:#8af;margin-left:6px"></span>'
            "</div></div>"
        )

    # ── Phase 1A: eBay live collapsible panel ──────────────────────────────────
    _el_synced = eb.get("synced_at") or ""
    if _ebay_live_raw:
        _el_prod = _inv_item.get("product") or {}
        _el_title = _el_prod.get("title") or "—"
        _el_aspects = _el_prod.get("aspects") or {}
        _el_offer = _ebay_live_raw.get("offer") or {}
        _el_price_v = (_el_offer.get("pricingSummary") or {}).get("price") or {}
        _el_price_str = f"${float(_el_price_v['value']):.2f}" if _el_price_v.get("value") else "—"
        _el_asp_rows = (
            "".join(
                f'<tr><td style="color:#8af;font-size:.8em;padding:2px 8px 2px 0">{h(k)}</td><td style="color:#ccc;font-size:.8em">{h(", ".join(v) if isinstance(v, list) else str(v))}</td></tr>'
                for k, v in sorted(_el_aspects.items())
            )
            if _el_aspects
            else ('<tr><td colspan="2" style="color:#555;font-size:.8em">none</td></tr>')
        )
        _sync_lbl = f'<span style="font-size:.73em;color:#556;font-weight:normal;margin-left:6px">synced {h(_el_synced[:19])}</span>' if _el_synced else ""
        _ebay_live_html = (
            '<div class="dsec"><details open>'
            f'<summary style="cursor:pointer;color:#4a8ade;font-weight:600;font-size:.9em">'
            f"eBay Live Data{_sync_lbl}</summary>"
            '<div style="font-size:.73em;color:#556;margin:4px 0 6px">'
            "Raw eBay mirror — what eBay currently holds. Not edited here.</div>"
            + fr("Live title", h(_el_title))
            + fr("Live price", _el_price_str)
            + fr("Category", h(str(_el_offer.get("categoryId") or "—")))
            + '<div style="margin-top:6px;font-size:.8em;color:#778">Aspects on eBay:</div>'
            + f'<table style="margin-top:4px">{_el_asp_rows}</table>'
            + "</details></div>"
        )
    else:
        _ebay_live_html = ""

    # ── Readiness checklist ────────────────────────────────────────────────────
    _item_for_readiness = dict(item)
    _item_for_readiness["_catalog_root"] = _cfg.get("catalog_root")
    _readiness_html_str = readiness_html(check_ebay(_item_for_readiness))

    # ── Price comps range bar + detail panel ────────────────────────────────────
    _st_val = h(str(item.get("search_terms") or ""))
    _comps = eo.get("price_comps") or {}
    _price_source = eo.get("price_source") or ""
    _priced_at = (eo.get("priced_at") or "")[:10]
    if _comps and _comps.get("max") and _comps.get("min") is not None:
        try:
            _cp_min = float(_comps.get("min", 0))
            _cp_p25 = float(_comps.get("p25", 0))
            _cp_med = float(_comps.get("median", 0))
            _cp_p75 = float(_comps.get("p75", 0))
            _cp_max = float(_comps.get("max", 0))
            _cp_cnt = int(_comps.get("count", 0))
            _cp_rng = max(_cp_max - _cp_min, 0.01)

            def _pct(v):
                return max(0, min(100, int((v - _cp_min) / _cp_rng * 100)))

            # Confidence badge
            _conf = _comps.get("confidence") or eo.get("price_confidence") or ""
            _n_out = _comps.get("outlier_count", 0)
            _n_drop = _comps.get("llm_dropped_count", 0)
            _conf_col = {"high": "#4a4", "medium": "#aa0", "low": "#c44"}.get(_conf, "#556")
            _conf_badge = (
                (
                    f'<span style="font-size:.72em;font-weight:600;margin-left:6px;'
                    f"padding:1px 5px;border-radius:3px;background:{_conf_col}22;"
                    f'color:{_conf_col};border:1px solid {_conf_col}44">{_conf}</span>'
                )
                if _conf
                else ""
            )
            # Count badge: red <5, yellow 5-9, grey ≥10
            _cnt_col = "#c44" if _cp_cnt < 5 else ("#aa0" if _cp_cnt < 10 else "#556")
            _cnt_badge = (
                f'<span style="font-size:.75em;color:{_cnt_col};'
                f'font-weight:600;margin-left:4px">{_cp_cnt} comps'
                + (" ⚠" if _cp_cnt < 5 else "")
                + (_conf_badge)
                + (f' <span style="font-size:.85em;color:#556">({_n_out} outlier{"s" if _n_out != 1 else ""} removed)</span>' if _n_out else "")
                + (f' <span style="font-size:.85em;color:#556">({_n_drop} irrelevant filtered)</span>' if _n_drop else "")
                + "</span>"
            )
            # Source label
            _src_lbl = (f'<span style="font-size:.72em;color:#445;margin-left:8px">{h(_price_source)}' + (f" · {_priced_at}" if _priced_at else "") + "</span>") if _price_source else ""
            # Range bar
            _bar = (
                f'<div style="position:relative;height:14px;background:#1a1a1a;'
                f'border-radius:3px;border:1px solid #333;margin-bottom:3px">'
                f'<div style="position:absolute;left:{_pct(_cp_p25)}%;'
                f'right:{100 - _pct(_cp_p75)}%;height:100%;background:#1a3a1a;border-radius:2px"></div>'
                f'<div style="position:absolute;left:{_pct(_cp_med)}%;width:2px;'
                f'height:100%;background:#4a4"></div>'
                f"</div>"
                f'<div style="display:flex;justify-content:space-between;font-size:.73em;color:#556">'
                f"<span>min ${_cp_min:.2f}</span>"
                f'<span style="color:#7a7">p25 ${_cp_p25:.2f}</span>'
                f'<span style="color:#afa">▲ med ${_cp_med:.2f}</span>'
                f'<span style="color:#7a7">p75 ${_cp_p75:.2f}</span>'
                f"<span>max ${_cp_max:.2f}</span>"
                f"</div>"
            )
            # Individual comp rows
            _comp_items = _comps.get("items") or []
            if _comp_items:

                def _ci_row(ci):
                    _is_out = ci.get("outlier", False)
                    _is_drop = ci.get("llm_dropped", False)
                    _excluded = _is_out or _is_drop
                    _row_op = "0.45" if _excluded else "1"
                    _title_dec = "line-through" if _excluded else "none"
                    _tag = ""
                    if _is_out:
                        _tag = '<span style="font-size:.72em;color:#c44;margin-left:4px" title="Price is statistical outlier (IQR)">outlier</span>'
                    elif _is_drop:
                        _reason = h(ci.get("llm_reason", ""))
                        _tag = f'<span style="font-size:.72em;color:#aa0;margin-left:4px" title="{_reason}">filtered ⓘ</span>'
                    _raw_url = ci.get("url", "") or ""
                    _url = h(_raw_url + ("&mkcid=1&mkrid=711-53200-19255-0&siteid=0&campid=5338722076&toolid=10001&mkevt=1" if _raw_url else ""))
                    _url_js = _raw_url.replace("'", "\\'")
                    _rm_btn = (
                        f"<button onclick=\"removeComp('{_url_js}')\" "
                        f'title="Remove this comp from pricing data" '
                        f'style="background:none;border:none;color:#c44;cursor:pointer;'
                        f'font-size:.9em;padding:0 4px;line-height:1;opacity:.7" '
                        f">✕</button>"
                    )
                    return (
                        f'<tr style="border-bottom:1px solid #1a1a1a;opacity:{_row_op}">'
                        f'<td style="padding:4px 8px 4px 0;font-size:.8em;color:#ccc;max-width:300px;word-break:break-word">'
                        f"{_rm_btn}"
                        f'<a href="{_url}" target="_blank" rel="noopener noreferrer" '
                        f'style="color:#7af;text-decoration:{_title_dec}">'
                        f"{h(ci.get('title', ''))[:80]}{'…' if len(ci.get('title', '')) > 80 else ''}</a>"
                        f"{_tag}</td>"
                        f'<td style="padding:4px 6px;font-size:.8em;color:#7a7;white-space:nowrap">'
                        f"{h(str(ci.get('condition', ''))[:30])}</td>"
                        f'<td style="padding:4px 0 4px 6px;font-size:.85em;color:#afa;white-space:nowrap;'
                        f'font-weight:600">${ci.get("price", 0):.2f}</td>'
                        f"</tr>"
                    )

                _comp_rows = "".join(_ci_row(ci) for ci in _comp_items)
                _n_active = sum(1 for ci in _comp_items if not ci.get("outlier") and not ci.get("llm_dropped"))
                _comp_detail = (
                    f'<details style="margin-top:6px">'
                    f'<summary style="cursor:pointer;font-size:.78em;color:#4a8ade;'
                    f'list-style:none;user-select:none">▶ Show {len(_comp_items)} comp listings ({_n_active} used for price)</summary>'
                    f'<table style="width:100%;border-collapse:collapse;margin-top:6px">'
                    f"<thead><tr>"
                    f'<th style="font-size:.73em;color:#556;text-align:left;padding:2px 8px 4px 0;'
                    f'border-bottom:1px solid #333">Title</th>'
                    f'<th style="font-size:.73em;color:#556;text-align:left;padding:2px 6px;'
                    f'border-bottom:1px solid #333">Condition</th>'
                    f'<th style="font-size:.73em;color:#556;text-align:left;padding:2px 0 4px 6px;'
                    f'border-bottom:1px solid #333">Price</th>'
                    f"</tr></thead>"
                    f"<tbody>{_comp_rows}</tbody>"
                    f"</table>"
                    f"<script>"
                    f"function removeComp(url){{"
                    f'  if(!confirm("Remove this comp from pricing data?"))return;'
                    f'  fetch("/api/items/{sku}/remove-comp",{{method:"POST",'
                    f'    headers:{{"Content-Type":"application/json","Authorization":"Bearer "+window._apiKey}},'
                    f"    body:JSON.stringify({{url:url}})}}"
                    f'  ).then(r=>r.json()).then(d=>{{if(d.ok)location.reload();else alert("Error: "+JSON.stringify(d));}});'
                    f"}}"
                    f"</script>"
                    f"</details>"
                )
            else:
                _comp_detail = '<div style="font-size:.75em;color:#445;margin-top:4px">Individual comp listings not saved — click Re-price to capture them.</div>'
            _price_comps_bar = '<div style="margin:6px 0 2px"><span style="font-size:.78em;color:#778">Market comps</span>' + _cnt_badge + _src_lbl + "</div>" + _bar + _comp_detail
        except (TypeError, ValueError):
            _price_comps_bar = ""
    else:
        _price_comps_bar = ""

    # ── Three-layer aspect data: live/current-draft (Set B) / proposed (pipeline, not
    # yet accepted) / edits (operator, form's current value) ──
    import json as _json

    # todo #1416 point 3: the aspects form (#aspects-form / saveEbayDraft())
    # is Set B's own editing surface — it prefills from and PATCHes into
    # draft_listing.item_specifics ONLY, never item_attributes (Set A).
    # Since operator edits now save directly into the same field this reads
    # as "live," the prefill layer and the live layer are the same value by
    # construction going forward (an operator's saved edit IS the current
    # draft state) — no separate Set A "override" layer exists anymore.
    _spec = get_ebay_aspects(item)  # Set B: current draft_listing.item_specifics
    _rev = item.get("revision_draft") or {}
    _rev_delta = _rev.get("delta") or {}
    _proposed_aspects = _rev_delta.get("item_specifics") or {}
    _has_proposals = bool(_rev_delta)

    _cat_id_for_aspects = str((dl or {}).get("category_id") or item.get("ebay_category_id") or "")
    _aspects_prefill_json = _json.dumps(_spec)  # Set B current values — the form's own field
    _live_aspects_json = _json.dumps(_spec)  # same Set B values, used as the "live" comparison baseline
    _proposed_aspects_json = _json.dumps(_proposed_aspects)  # pipeline proposals
    _aspects_cat_json = _json.dumps(_cat_id_for_aspects)
    _proposals_meta_json = _json.dumps(
        {
            "title": _rev_delta.get("title") or "",
            "description": _rev_delta.get("description") or "",
            "by": _rev.get("by") or "pipeline",
            "at": _local_ts(_rev.get("at")),
            "count": len(_rev_delta),
        }
    )

    # ── eBay Draft editor section (Phase 1B) ───────────────────────────────────
    _dl_title_len = len((dl or {}).get("title") or "")
    _dl_title_val = h((dl or {}).get("title") or "")
    _dl_price_val = str((dl or {}).get("price") or "")
    _dl_cond_val = h((dl or {}).get("condition_enum") or (dl or {}).get("condition") or "")
    _dl_cond_lbl = h((dl or {}).get("condition_label") or (dl or {}).get("condition_description") or "")
    _dl_desc_val = h((dl or {}).get("description") or item.get("description") or "")
    # AI Identify resolves the authoritative eBay category before ebay_draft
    # exists. The editor previously ignored that valid result until a draft was
    # generated, rendering an empty category and hiding the category aspects.
    _dl_cat_id_raw = str((dl or {}).get("category_id") or item.get("ebay_category_id") or "").strip()
    _dl_cat_name_raw = str((dl or {}).get("category_name") or item.get("ebay_category_name") or "").strip()
    _dl_cat_missing = not _dl_cat_id_raw or _dl_cat_id_raw == "99"
    _dl_cat_name = h(_dl_cat_name_raw)
    _dl_cat_id_v = h(_dl_cat_id_raw)
    _dl_cat_warning_html = '<span style="color:#e88">Required — choose an eBay category before staging</span>' if _dl_cat_missing else ""
    _dl_cat_aria_invalid = "true" if _dl_cat_missing else "false"
    _dl_cat_bg = "#1a0a0a" if _dl_cat_missing else "#1a1a1a"
    _dl_cat_border = "#c44" if _dl_cat_missing else "#444"
    _dl_ship_val = str((dl or {}).get("shipping_profile") or (dl or {}).get("fulfillment_policy_id") or "")
    _dl_return_val = str((dl or {}).get("return_policy_id") or "")
    # PP-OFFER-001 follow-up (todo #1256): Best Offer is a per-item Inventory
    # API field (offer.listingPolicies.bestOfferTerms), not an account
    # default -- previously not exposed anywhere in TGW, so whatever a
    # listing showed was either an eBay category default or an untracked
    # manual Seller Hub change (invariant C11 drift class).
    # Tri-state (None/True/False), not a checkbox: audit#1143 code-review
    # follow-up on #1256 -- a plain checkbox can't represent "unset," so
    # saveEbayDraft() was unconditionally sending the checkbox's current
    # (always-defined) checked state on every save, silently forcing
    # best_offer_enabled=false the first time an operator saved ANY
    # unrelated field on an item that had never touched Best Offer,
    # defeating the "unset means don't touch" contract this same field
    # documents in tgw.ebay.sync._build_offer_bodies.
    _dl_bo_raw = (dl or {}).get("best_offer_enabled")
    _dl_bo_select_val = "true" if _dl_bo_raw is True else ("false" if _dl_bo_raw is False else "")
    _dl_bo_accept = (dl or {}).get("best_offer_auto_accept_price")
    _dl_bo_decline = (dl or {}).get("best_offer_auto_decline_price")
    _dl_bo_accept_val = h(str(_dl_bo_accept)) if _dl_bo_accept not in (None, "") else ""
    _dl_bo_decline_val = h(str(_dl_bo_decline)) if _dl_bo_decline not in (None, "") else ""
    _dl_store_cat_id = str((dl or {}).get("store_category_id") or "")
    if not _dl_store_cat_id:
        _cg_key = item.get("category_group", "")
        if _cg_key:
            try:
                import json as _jsc2

                _cg2 = _jsc2.loads(Path(_cfg["category_groups_path"]).read_text())
                _grp2 = _cg2.get("groups", _cg2).get(_cg_key, {})
                _dl_store_cat_id = str(_grp2.get("store_category_id") or "")
            except Exception:
                pass
    _dl_store_cat2_id = str((dl or {}).get("secondary_store_category_id") or "")
    _dl_cat2_id_v = h(str((dl or {}).get("secondary_category_id") or ""))
    _dl_cat2_name = h(str((dl or {}).get("secondary_category_name") or ""))
    # Item editing renders the validated last-known-good eBay snapshot. A
    # missing/corrupt snapshot falls back to the local mapping only to preserve
    # existing editability, and is visibly not represented as complete eBay data.
    _sc_results, _sc_refreshed_at, _sc_error = _store_categories_snapshot(_cfg)
    if _sc_error:
        _sc_results = _store_categories_from_groups(_cfg)
    _sc_list = [(r["name"], str(r["id"])) for r in _sc_results]

    def _store_cat_options_html(selected_id: str) -> str:
        opts = '<option value="">— not set —</option>'
        known_ids = {store_id for _name, store_id in _sc_list}
        if selected_id and selected_id not in known_ids:
            stored_name = f"Stored category {selected_id}"
            opts += f'<option value="{h(selected_id)}" data-name="{h(stored_name)}" selected>{h(stored_name)} — not in current snapshot</option>'
        opts += "".join(f'<option value="{h(_si)}" data-name="{h(_sn)}"{" selected" if _si == selected_id else ""}>{h(_sn)} ({h(_si)})</option>' for _sn, _si in _sc_list)
        return opts

    _store_cat_opts_html = _store_cat_options_html(_dl_store_cat_id)
    _store_cat2_opts_html = _store_cat_options_html(_dl_store_cat2_id)
    if _sc_error:
        _store_cat_fallback_html = f'<span style="font-size:.7em;color:#c84;margin-left:6px" title="{h(_sc_error)}">⚠ local mapping only; eBay snapshot unavailable ({len(_sc_list)} mapped)</span>'
    else:
        _store_cat_fallback_html = (
            f'<span style="font-size:.7em;color:#585;margin-left:6px" '
            f'title="last successful GetStore refresh: {h(_sc_refreshed_at or "timestamp unavailable")}">'
            f"eBay snapshot: {len(_sc_list)} categories</span>"
        )
    import json as _json2

    _cat_root = _cfg.get("catalog_root")
    _pol_cache = (_cat_root / "ebay-fulfillment-policies.json") if _cat_root else None
    _return_opts: dict = {}
    if _pol_cache and _pol_cache.exists():
        try:
            _pd = _json2.loads(_pol_cache.read_text())
            _return_opts = _pd.get("return", {})
            if not _dl_return_val and _return_opts:
                _dl_return_val = next(iter(_return_opts))
        except Exception:
            pass
    # Fulfillment selectors render only the validated last-known-good Account
    # API snapshot. Reconciliation is explicit and never coupled to page load.
    _fulfillment_opts, _fo_refreshed_at, _fo_error = _fulfillment_policies_snapshot(_cfg)
    _ship_opts_html = ""
    if _dl_ship_val and _dl_ship_val not in _fulfillment_opts:
        _ship_opts_html += f'<option value="{h(_dl_ship_val)}" selected>{h(_dl_ship_val)} — not in current snapshot</option>'
    _ship_opts_html += "".join(
        f'<option value="{h(pid)}"{" selected" if pid == _dl_ship_val else ""}>{h(name)} ({h(pid[:8])}…)</option>'
        for pid, name in sorted(_fulfillment_opts.items(), key=lambda kv: (kv[1].casefold(), kv[0]))
    )
    if _fo_error:
        _fo_fallback_html = f'<span style="font-size:.7em;color:#c84;margin-left:6px" title="{h(_fo_error)}">⚠ eBay fulfillment-policy snapshot unavailable</span>'
    else:
        _fo_fallback_html = (
            f'<span style="font-size:.7em;color:#585;margin-left:6px" '
            f'title="last successful Account API refresh: {h(_fo_refreshed_at or "timestamp unavailable")}">'
            f"eBay snapshot: {len(_fulfillment_opts)} fulfillment policies</span>"
        )
    # Live fulfillment policy beside the selector (session 42: the selector kept
    # showing the operator's FC4 while eBay actually had FC8 — the divergence was
    # invisible). Mirrored home by ebay_sync; red when it disagrees with the draft.
    _live_ship = str((item.get("ebay_offer") or {}).get("fulfillment_policy_id") or "")
    if _live_ship:
        _ls_name = _fulfillment_opts.get(_live_ship) or f"{_live_ship[:10]}…"
        _ls_mismatch = bool(_dl_ship_val) and _live_ship != _dl_ship_val
        _live_ship_html = (
            f'<span style="font-size:.72em;margin-left:8px;'
            f'color:{"#e88" if _ls_mismatch else "#585"}" '
            f'title="fulfillment policy currently on the live eBay listing (mirrored by ebay_sync)">'
            f"live: {h(_ls_name)}{' — differs from draft, Update Listing to apply' if _ls_mismatch else ''}</span>"
        )
    else:
        _live_ship_html = ""
    _return_opts_html = "".join(
        f'<option value="{pid}"{" selected" if pid == _dl_return_val else ""}>{h(name)} ({pid[:8]}…)</option>' for pid, name in sorted(_return_opts.items(), key=lambda kv: kv[1])
    )
    # Build proposals banner if pipeline has proposed changes
    # Actions section — context-aware based on eBay status
    import json as _json

    _ak_json2 = _json.dumps(api_key)
    _sku_json2 = _json.dumps(sku)
    _listing_id_json = _json.dumps(listing_id)
    _is_active_js = "true" if is_active else "false"

    # Pipeline error — two writer schemas exist and both must render:
    # eBay HTTP rejections write {worker, error, raw, at} (ebay_stage/ebay_publish
    # 4xx paths); local guard findings write {code, detail, ts, source} (e.g. the
    # no_price_set C11 finding, written precisely so this page can say what to do).
    _pe = item.get("pipeline_error")
    _pe_norm: Optional[Dict[str, Any]] = None
    if _pe and isinstance(_pe, dict):
        if _pe.get("error"):
            _pe_norm = {
                "heading": f"eBay rejected {_pe.get('worker', '?')}",
                "detail": _pe["error"],
                "raw": _pe.get("raw", ""),
                "at": _pe.get("at"),
                "code": _pe.get("code"),
                "field": _pe.get("field"),
            }
        elif _pe.get("detail") or _pe.get("code"):
            _pe_code = _pe.get("code")
            _pe_norm = {
                "heading": (f"eBay rejected {_pe.get('source', '?')}" if _pe_code == "ebay_rejected" else f"{_pe.get('source', 'pipeline')} stopped: {_pe_code or 'error'}"),
                "detail": _pe.get("detail") or str(_pe_code or ""),
                "raw": _pe.get("raw", ""),
                "at": _pe.get("ts"),
                "code": _pe_code,
                "field": _pe.get("field"),
            }
    if _pe_norm:
        _pe_when = _local_ts(_pe_norm["at"])
        _pe_raw_js = _json.dumps(_pe_norm["raw"])
        _raw_btn = ('<button class="act-btn" style="font-size:.78em;padding:2px 8px" onclick="toggleRawError()">Show raw</button>') if _pe_norm["raw"] else ""
        _pipeline_error_html = (
            f'<div id="pipeline-error-box" style="margin-bottom:12px;padding:10px 14px;'
            f'background:#1a0505;border:1px solid #844;border-radius:4px;font-size:.82em;color:#e88">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">'
            f"<strong>{h(_pe_norm['heading'])}</strong>"
            f'<span style="color:#666;font-size:.85em">{_pe_when} UTC</span></div>'
            f'<div style="color:#f99;margin-bottom:6px">{h(_pe_norm["detail"])}</div>'
            f'<div style="display:flex;gap:8px;flex-wrap:wrap">'
            f"{_raw_btn}"
            f'<button class="act-btn act-warn" style="font-size:.78em;padding:2px 8px" '
            f'onclick="clearPipelineError()">Clear error</button></div>'
            f'<pre id="pipeline-error-raw" style="display:none;margin-top:8px;font-size:.75em;'
            f'color:#a77;white-space:pre-wrap;word-break:break-all;max-height:160px;overflow:auto">'
            f"</pre></div>"
            # PP-CONDITION-ENUM-001 / todo #1562: when the persisted
            # pipeline_error names the errant draft field (e.g. eBay's own
            # "Could not serialize field [condition]" resolved to
            # "condition_enum"), flag that field red on page load using the
            # same shared flagFieldInvalid() the two condition-select render
            # paths use — the operator sees the problem the moment the item
            # opens, not just after touching the field. Only fields with a
            # known rendered element are wired here; an unmapped field name
            # (or none) is a no-op, not an error.
            f"<script>var _PE_RAW={_pe_raw_js};window._PE_FIELD={_json.dumps(_pe_norm.get('field'))};"
            f"window._PE_DETAIL={_json.dumps(_pe_norm.get('detail') or '')};"
            f"document.addEventListener('DOMContentLoaded',function(){{"
            f"  var _peFieldEls={{condition_enum:'dl-condition-select',title:'dl-title-input'}};"
            f"  var _peEl=window._PE_FIELD&&_peFieldEls[window._PE_FIELD]?document.getElementById(_peFieldEls[window._PE_FIELD]):null;"
            f"  if(_peEl){{flagFieldInvalid(_peEl,true);_peEl.title=window._PE_DETAIL;}}"
            f"}});</script>"
        )
    else:
        _pipeline_error_html = ""

    _is_published = offer_status.upper() == "PUBLISHED"
    _is_unpublished_offer = offer_status.upper() == "UNPUBLISHED"
    _has_draft = bool(dl.get("title"))

    # ── PP-ACTIONCONSOLE-001: state-driven action line ─────────────────────────
    # One row of state-appropriate actions replaces the pipeline breadcrumb and
    # the Publish-gate/Pipeline-Tools split. The buttons ARE the indicators:
    # green = ready, yellow = working/pending, red = error/destructive,
    # grey = nothing to do. Operator-visible states only — "staged" stays hidden.
    _is_sold = str(item.get("status") or "").lower() == "sold"
    _sold_quantity_raw = dl.get("quantity", item.get("quantity", 0))
    try:
        _sold_quantity = 0 if isinstance(_sold_quantity_raw, bool) else int(_sold_quantity_raw or 0)
    except (TypeError, ValueError):
        _sold_quantity = 0
    _working = any(j.get("state") in ("pending", "running", "claimed", "retry") for j in jobs)
    # A dead_letter older than the last re-baseline is superseded history:
    # the manager made draft == offer after that failure (broker B1a), so it
    # is no longer an actionable state. Newer dead_letters are live failures.
    _baseline_at = item.get("baseline_at")

    def _parse_ts(ts: str) -> Optional[datetime]:
        """Parse a queue_jobs/item timestamp, normalizing to tz-aware UTC.

        queue_jobs timestamps are stored in mixed shapes — some offset-aware
        (e.g. '2026-07-25T00:29:18+00:00'), some offset-naive (e.g.
        '2026-07-25T00:32:25') — but naive ones are already UTC in this
        system. Comparing a naive and an aware datetime directly raises
        TypeError, so every parsed value is normalized to aware-UTC here
        before it's ever compared.
        """
        try:
            dt = datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def _job_finished_at(j: Dict[str, Any]) -> Optional[datetime]:
        """Best available terminal timestamp for comparing related jobs."""
        ts = j.get("finished_at") or j.get("updated_at") or j.get("created_at")
        if not ts:
            return None
        return _parse_ts(ts)

    def _after_baseline(j: Dict[str, Any]) -> bool:
        if not _baseline_at:
            return True
        job_at = _job_finished_at(j)
        if job_at is None:
            return True
        baseline_dt = _parse_ts(_baseline_at)
        if baseline_dt is None:
            return True
        return job_at > baseline_dt

    def _superseded_by_success(j: Dict[str, Any]) -> bool:
        """Keep terminal failures in history, but do not make an older failure
        actionable after the same SKU's later successful queue step."""
        failed_at = _job_finished_at(j)
        queue_name = j.get("queue_name")
        if failed_at is None or not queue_name:
            return False
        return any(other.get("queue_name") == queue_name and other.get("state") == "succeeded" and (other_at := _job_finished_at(other)) is not None and other_at > failed_at for other in jobs)

    def _duplicate_provider_effect_lost_to_success(j: Dict[str, Any]) -> bool:
        """Hide only the known duplicate-stage loser, never a real failure.

        A List click used to race its draft-save auto-stage against the governed
        listing stage.  The loser reports a binding mismatch after the winning
        stage has already committed.  It remains in the job ledger, but is not
        an operator repair state when the same generation has a successful
        sibling stage receipt.
        """
        if j.get("state") != "dead_letter" or j.get("queue_name") != "ebay_stage" or "provider effect binding mismatch" not in str(j.get("error_detail") or ""):
            return False
        payload = j.get("payload_json")
        generation = payload.get("object_generation") if isinstance(payload, dict) else None
        return (
            isinstance(generation, str)
            and bool(generation)
            and any(
                other.get("queue_name") == "ebay_stage"
                and other.get("state") == "succeeded"
                and isinstance(other.get("payload_json"), dict)
                and other["payload_json"].get("object_generation") == generation
                for other in jobs
            )
        )

    _has_error = bool(_pe_norm) or any(
        j.get("state") == "dead_letter"
        and not j.get("provider_effect_reconciled")
        and not (
            dl.get("price") is not None
            and isinstance((j.get("payload_json") or {}).get("result"), dict)
            and ((j.get("payload_json") or {}).get("result", {}).get("evidence") or {}).get("reason_code") == "PRICE_REQUIRES_OPERATOR_INPUT"
        )
        and _after_baseline(j)
        and not _superseded_by_success(j)
        and not _duplicate_provider_effect_lost_to_success(j)
        for j in jobs
    )
    _needs_photo_resync = bool(photo_fingerprint and photo_fingerprint.get("result") == "false" and item_photos_fingerprint and item_photos_fingerprint.get("result") == "true")
    # A missing draft price is itself the actionable state.  Do not depend on
    # a worker having also persisted the newer ``no_price_set`` finding: older
    # ebay_price dead letters (and workers which correctly refuse to invent a
    # price when no positive evidence exists) may leave only the empty draft
    # field plus the queue ledger.  In that state Retry cannot help; the
    # operator needs the price editor.
    _needs_price = bool((dl.get("title") and dl.get("price") is None) or (_pe_norm and _pe_norm.get("code") == "no_price_set"))
    # Also catches pre-existing findings written by the OLD path (an actual
    # eBay API rejection, code='ebay_rejected', before the ebay_stage.py
    # pre-flight guard existed) whose detail is specifically the 80-char
    # title rejection — so already-dead-lettered items (e.g. tgw202605051752520)
    # get the same "Trim Title" affordance without needing to be re-staged
    # first just to get a fresh finding in the new shape.
    _needs_title_trim = bool(_pe_norm and (_pe_norm.get("code") == "title_too_long" or (_pe_norm.get("code") == "ebay_rejected" and "80 characters" in (_pe_norm.get("detail") or ""))))
    # Draft-vs-live divergence: does the draft differ from what eBay holds?
    _live_inv = (_ebay_live_raw.get("inventory_item") or {}) if _ebay_live_raw else {}
    _live_offer_raw = (_ebay_live_raw.get("offer") or {}) if _ebay_live_raw else {}
    _diverged = False
    if is_active and _ebay_live_raw:
        _lp = (_live_offer_raw.get("pricingSummary") or {}).get("price") or {}
        _live_product = _live_inv.get("product") or {}
        try:
            _price_differs = dl.get("price") is not None and _lp.get("value") is not None and abs(float(dl["price"]) - float(_lp["value"])) > 0.01
        except (TypeError, ValueError):
            _price_differs = False
        _diverged = (
            _price_differs
            or (dl.get("title") and dl["title"] != _live_product.get("title"))
            or (dl.get("quantity") is not None and _live_offer_raw.get("availableQuantity") is not None and int(dl["quantity"]) != int(_live_offer_raw["availableQuantity"]))
        )

    def _abtn(label: str, onclick: str, color: str, *, disabled: bool = False, title: str = "", push: bool = False) -> str:
        colors = {
            "green": "background:#0d2a0d;border-color:#4a4;color:#8e8",
            "yellow": "background:#2a2a0a;border-color:#aa4;color:#cc8",
            "red": "background:#2a0d0d;border-color:#a44;color:#e88",
            "grey": "background:#1a1a1a;border-color:#333;color:#667",
            "blue": "background:#1a2a3a;border-color:#2a4a6a;color:#cce",
        }
        dis = " disabled" if disabled else ""
        push_s = "margin-left:auto;" if push else ""
        title_a = f' title="{h(title)}"' if title else ""
        return f'<button class="act-btn"{dis}{title_a} style="{push_s}{colors[color]}" onclick="{onclick}">{label}</button>'

    _line: List[str] = []
    if _is_sold:
        _line.append(
            _abtn(
                "Sold",
                "",
                "grey",
                disabled=True,
                title=("Sold status is authoritative — explicitly restore inventory and change status before relisting"),
            )
        )
    elif _needs_price:
        # A guard finding with a known fix gets its affordance regardless of
        # listing state — Retry cannot resolve a missing price, the editor can.
        _line.append(
            _abtn(
                "Set Price",
                "var p=document.getElementById('dl-price-input');if(p){p.scrollIntoView({behavior:'smooth',block:'center'});p.focus();}",
                "red",
                title=("Live on eBay, but the draft has no operator-set price — set one, then Update Item to push it live") if is_active else "The draft has no price — set one, then List on eBay",
            )
        )
        if not is_active and _has_draft:
            _line.append(_abtn("List on eBay", "listOnEbay()", "green", title="Save draft, run every needed step, and publish"))
    elif _needs_title_trim:
        # Same shape as _needs_price: Retry cannot fix an over-length title,
        # the editor can. The title field holds the FULL untruncated text
        # (seo/title.py deliberately doesn't auto-truncate) — this scrolls to
        # it so the operator can trim by double-click-deleting words, same
        # workflow eBay's own bulk-CSV editor uses.
        _line.append(
            _abtn(
                "Trim Title",
                "var t=document.getElementById('dl-title-input');if(t){t.scrollIntoView({behavior:'smooth',block:'center'});t.focus();}",
                "red",
                title=(f"Title is {len((dl or {}).get('title') or '')} chars — eBay allows at most 80. Trim it, then Save Draft and List on eBay."),
            )
        )
        if not is_active and _has_draft:
            _line.append(_abtn("List on eBay", "listOnEbay()", "green", title="Save draft, run every needed step, and publish"))
    elif _needs_photo_resync:
        # Photo repair belongs beside the photo evidence, where the dedicated
        # Resync Photos control already exists.  The action line remains the
        # operator command surface: List issues the publish-capable grant and
        # the server graph runs upload, stage, and publish in order.
        if _has_draft:
            _line.append(
                _abtn(
                    "List on eBay",
                    "listOnEbay()",
                    "green",
                    title=("Start the full listing workflow; it will synchronize photos before staging and publishing"),
                )
            )
    elif _has_error and not is_active:

        def _job_reason_code(job: Dict[str, Any]) -> str:
            """Read a worker reason from its persisted queue payload.

            ``_workflow_attempt_rows`` returns database rows, where provider
            results live under ``payload_json.result``.  Accepting a top-level
            result as well keeps the renderer usable by callers which already
            project that nested field, but the persisted shape is authoritative.
            """
            result = job.get("result")
            if not isinstance(result, dict):
                payload = job.get("payload_json")
                result = payload.get("result") if isinstance(payload, dict) else None
            evidence = result.get("evidence") if isinstance(result, dict) else None
            if not isinstance(evidence, dict):
                return ""
            return str(evidence.get("reason_code") or "")

        _retryable_failure = next(
            (
                j
                for j in jobs
                if j.get("state") == "dead_letter"
                and _after_baseline(j)
                and not _superseded_by_success(j)
                and not _duplicate_provider_effect_lost_to_success(j)
                and j.get("job_id")
                and j.get("retry_allowed", True)
            ),
            None,
        )
        _receipt_identity_failure = next(
            (
                j
                for j in jobs
                if j.get("queue_name") == "ebay_upload"
                and j.get("state") == "dead_letter"
                and _after_baseline(j)
                and not _superseded_by_success(j)
                and _job_reason_code(j) == "INVALID_RECEIPT_IDENTITY"
            ),
            None,
        )
        if _retryable_failure:
            _line.append(
                _abtn(
                    "Retry",
                    f"retryJob({h(_json.dumps(str(_retryable_failure['job_id'])))})",
                    "red",
                    title=f"Retry failed {_retryable_failure.get('queue_name') or 'pipeline'} job",
                )
            )
        elif _receipt_identity_failure and _has_draft:
            # The failed job is intentionally not blind-retryable: its graph and
            # receipt identities are stale.  Once a current draft exists, the
            # safe recovery is a new operator-authorized publish evaluation,
            # which binds the current graph/source/generation and leaves the old
            # dead letter in the audit history.  Without this branch the page
            # offered only "Needs attention", even after the receipt producer
            # was corrected, leaving no way for the operator to launch that
            # replacement attempt (live incident tgw202510161310076).
            _line.append(
                _abtn(
                    "List on eBay",
                    "listOnEbay()",
                    "green",
                    title=("Start a fresh listing attempt with the current draft and current receipt identity; the historical failure is retained"),
                )
            )
        else:
            _line.append(
                _abtn(
                    "Needs attention",
                    "var j=document.getElementById('jobs-section');if(j)j.scrollIntoView({behavior:'smooth'});",
                    "red",
                    title="This failure requires reconciliation rather than a blind retry",
                )
            )
    elif _working:
        _line.append(_abtn("Working…", "", "yellow", disabled=True, title="Pipeline is running — refresh to update"))
    elif is_active:
        # The live listing is the ground truth — a failed re-run must never
        # mask it behind a bare Retry (which would only fail the same way).
        # The error becomes a directed affordance instead.
        if _pe_norm:
            # Keyed off the persisted finding (C11), not the job ledger — a
            # historical dead_letter whose finding was resolved is not a state.
            _line.append(
                _abtn(
                    "Needs attention",
                    "var b=document.getElementById('pipeline-error-box');if(b)b.scrollIntoView({behavior:'smooth'});",
                    "red",
                    title="Live on eBay, but the last pipeline step failed — see the error box",
                )
            )
        elif _has_error:
            # Dead-letter in the ledger with no persisted finding (worker
            # crash paths write none) — quieter, but never silent.
            _line.append(
                _abtn(
                    "Needs attention",
                    "var j=document.getElementById('jobs-section');if(j)j.scrollIntoView({behavior:'smooth'});",
                    "red",
                    title="Live on eBay, but a pipeline job failed — see job history",
                )
            )
        if _diverged:
            _line.append(_abtn("Update Item", "updateItem()", "yellow", title="Draft has unpushed changes — push to eBay in place"))
        else:
            _line.append(_abtn("Update Item", "updateItem()", "grey", title="Draft matches the live listing"))
    elif _has_draft:
        _line.append(_abtn("List on eBay", "listOnEbay()", "green", title="Save draft, run every needed step, and publish"))
        if _is_unpublished_offer:
            _apv_chk = " checked" if is_ready else ""
            _line.append(
                f'<label style="display:flex;align-items:center;gap:5px;font-size:.8em;'
                f'color:#8a8;cursor:pointer;padding:0 6px">'
                f'<input type="checkbox"{_apv_chk} onchange="toggleApprove(this)">'
                f"queue for auto-listing "
                f'<span style="color:#b66" title="approved items collect in the ready '
                f"pool but the ebay_dole worker is not installed — nothing publishes "
                f'from the pool yet (todo #1113)">(inactive)</span></label>'
            )
        _line.append(_abtn("Reset Draft", "resetDraft()", "blue", title="Discard edits and regenerate the draft from the catalog record"))
    else:
        _line.append(_abtn("Prepare Listing", "prepareListing()", "green", title="Run identification and draft the eBay listing"))

    _line.append(
        _abtn(
            "AI Reidentify" if item.get("ai_identified") else "AI Identify",
            "triggerAction('ai_identify')",
            "blue",
            title=("Run image identification again using the current photos" if item.get("ai_identified") else "Run image identification using the current photos"),
        )
    )

    if is_active and not _is_sold:
        # Always operator-accessible on a live item (Dave, s46): this is THE
        # component that resolves the draft interface to the live data — the
        # broken-draft states (e.g. draft price empty vs live price set) are
        # exactly the ones the _diverged heuristic can't see.
        _line.append(_abtn("Reset Draft", "resetDraftFromLive()", "blue", title="Discard local edits — re-pin the draft to what is live on eBay"))

    _line.append(_abtn("Archive", ("triggerAction('archive','Archive this item? It will be hidden from the catalog.')"), "grey", push=True))
    _line.append(_abtn("Delete", "deleteItem()", "grey"))
    if listing_url:
        _line.append(f'<a class="ebay-btn ebay-btn-primary" style="align-self:center" href="{h(listing_url)}" target="_blank" rel="noopener noreferrer">View on eBay ↗</a>')

    _gate_html = '<div class="act-row" id="action-line">' + "".join(_line) + "</div>"
    _pipeline_bar = ""  # removed — the action line and tabs carry the state

    _proposals_banner = ""
    if _has_proposals:
        _pr_by = h(_rev.get("by") or "pipeline")
        _pr_at = _local_ts(_rev.get("at"))
        _pr_cnt = len(_rev_delta)
        _pr_asp = len(_proposed_aspects)
        _pr_title = _rev_delta.get("title") or ""
        _pr_desc = _rev_delta.get("description") or ""
        _detail_lines = []
        if _pr_asp:
            _detail_lines.append(f"{_pr_asp} aspect{'s' if _pr_asp != 1 else ''}")
        if _pr_title:
            _detail_lines.append(f"title → {h(_pr_title[:60])}{'…' if len(_pr_title) > 60 else ''}")
        if _pr_desc:
            _detail_lines.append("description")
        _detail_str = ", ".join(_detail_lines) or f"{_pr_cnt} field{'s' if _pr_cnt != 1 else ''}"
        _proposals_banner = (
            f'<div id="proposals-banner" style="margin-bottom:12px;padding:10px 14px;'
            f'background:#1a1a00;border:1px solid #664;border-radius:4px;font-size:.82em;color:#cc8">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">'
            f"<strong>Pipeline proposed changes</strong>"
            f'<span style="color:#665;font-size:.85em">{_pr_by} · {_pr_at} UTC</span></div>'
            f'<div style="color:#aa8;margin-bottom:8px">{_detail_str}</div>'
            f'<div style="font-size:.75em;color:#665;margin-bottom:8px">'
            f"Yellow highlights show proposed values. Edit any field to override. "
            f"Accept copies proposals into your draft — review then Update Listing to push to eBay.</div>"
            f'<div style="display:flex;gap:8px">'
            f'<button class="act-btn act-publish" style="font-size:.78em;padding:3px 10px;background:#1a1a00;border-color:#884" '
            f'onclick="acceptProposals()">Accept All Proposals</button>'
            f'<button class="act-btn" style="font-size:.78em;padding:3px 10px" '
            f'onclick="dismissProposals()">Dismiss</button>'
            f"</div></div>"
        )

    # PP-CONDITION-ENUM-001 / todo #1562: computed before the big f-string
    # block below so the initial server-rendered <select> can be flagged red
    # on first paint (via inline JS calling the shared flagFieldInvalid())
    # the same way loadCatCtx()'s dynamic re-render already flags it —
    # both render paths must use the one shared visual-flag mechanism.
    _cond_opts_html, _cond_is_invalid = _build_condition_options((dl or {}).get("condition_enum") or (dl or {}).get("condition") or "", _cat_id_for_aspects)

    _ebay_draft_editor = (
        '<div id="dl-section" class="dsec">'
        "<h3>Listing</h3>" + f'<div class="frow" id="dl-title">'
        # Title
        f'<span class="fn">eBay Title</span>'
        f'<span class="fv" style="flex:1">'
        f'<input id="dl-title-input" type="text" maxlength="80" value="{_dl_title_val}" '
        f'style="width:100%;background:#1a1a1a;color:#eee;'
        f"border:1px solid {'#c44' if _dl_title_len > 80 else '#444'};"
        f'border-radius:4px;padding:4px 6px;font-size:.9em" '
        f"oninput=\"updateCharCount(this,80,'dl-title-count')\">"
        f'<span id="dl-title-count" style="font-size:.75em;margin-left:4px;'
        f'color:{"#c44" if _dl_title_len > 80 else ("#aa0" if _dl_title_len > 72 else "#556")}">'
        f"{_dl_title_len}/80</span>"
        f"</span></div>"
        # Category — search to change
        f'<div class="frow" id="dl-category">'
        f'<span class="fn">Category</span>'
        f'<span class="fv" style="flex:1;position:relative">'
        f'<div id="dl-cat-breadcrumb" style="font-size:.82em;color:#aaa;margin-bottom:3px">'
        f"{_dl_cat_id_v}{'&nbsp;·&nbsp;' + _dl_cat_name if _dl_cat_name else ''}"
        f"{_dl_cat_warning_html}"
        f"</div>"
        f'<div style="display:flex;gap:4px;align-items:flex-start">'
        f'<input id="dl-cat-search" type="text" placeholder="Search name, type an ID, or Browse…" '
        f'aria-invalid="{_dl_cat_aria_invalid}" '
        f'autocomplete="off" '
        f'style="flex:1;background:{_dl_cat_bg};color:#eee;'
        f"border:1px solid {_dl_cat_border};"
        f'border-radius:4px;padding:3px 6px;font-size:.88em">'
        f'<a href="#" id="dl-cat-browse-btn" onclick="return false" '
        f'style="display:inline-block;padding:3px 8px;border-radius:4px;font-size:.8em;'
        f'background:#2a2a2a;color:#9ab;border:1px solid #444;white-space:nowrap">Browse</a>'
        f"</div>"
        f'<input type="hidden" id="dl-cat-id" value="{_dl_cat_id_v}">'
        f'<div id="dl-cat-dropdown" style="display:none;position:absolute;top:100%;left:0;right:0;'
        f"background:#1e1e1e;border:1px solid #555;border-radius:4px;z-index:50;"
        f'max-height:220px;overflow-y:auto"></div>'
        f'<div id="dl-cat-browse-panel" style="display:none;position:absolute;top:100%;left:0;right:0;'
        f"background:#1e1e1e;border:1px solid #555;border-radius:4px;z-index:49;"
        f'padding:6px;margin-top:2px"></div>'
        f'<span id="category-group-hint" style="font-size:.72em;color:#445;display:block;margin-top:2px"></span>'
        f"</span></div>"
        # Secondary eBay category
        f'<div class="frow" id="dl-category2">'
        f'<span class="fn">2nd Category</span>'
        f'<span class="fv" style="flex:1;position:relative">'
        f'<div id="dl-cat2-breadcrumb" style="font-size:.82em;color:#aaa;margin-bottom:3px">'
        f"{_dl_cat2_id_v}{'&nbsp;·&nbsp;' + _dl_cat2_name if _dl_cat2_name else ''}"
        f"</div>"
        f'<div style="display:flex;gap:4px;align-items:flex-start">'
        f'<input id="dl-cat2-search" type="text" placeholder="Search name, type an ID, or Browse…" '
        f'autocomplete="off" '
        f'style="flex:1;background:#1a1a1a;color:#eee;border:1px solid #444;'
        f'border-radius:4px;padding:3px 6px;font-size:.88em">'
        f'<a href="#" id="dl-cat2-browse-btn" onclick="return false" '
        f'style="display:inline-block;padding:3px 8px;border-radius:4px;font-size:.8em;'
        f'background:#2a2a2a;color:#9ab;border:1px solid #444;white-space:nowrap">Browse</a>'
        f"</div>"
        f'<input type="hidden" id="dl-cat2-id" value="{_dl_cat2_id_v}">'
        f'<div id="dl-cat2-dropdown" style="display:none;position:absolute;top:100%;left:0;right:0;'
        f"background:#1e1e1e;border:1px solid #555;border-radius:4px;z-index:50;"
        f'max-height:220px;overflow-y:auto"></div>'
        f'<div id="dl-cat2-browse-panel" style="display:none;position:absolute;top:100%;left:0;right:0;'
        f"background:#1e1e1e;border:1px solid #555;border-radius:4px;z-index:49;"
        f'padding:6px;margin-top:2px"></div>'
        f"</span></div>"
        # Store category — select from known groups
        f'<div class="frow" id="dl-store-category">'
        f'<span class="fn">Store cat 1</span>'
        f'<span class="fv">'
        f'<select id="dl-store-cat-select" '
        f'style="background:#1a1a1a;color:#eee;border:1px solid #444;border-radius:4px;padding:3px 6px;font-size:.88em">' + _store_cat_opts_html + "</select>"
        '<span id="store-cat-hint" style="font-size:.72em;color:#445;margin-left:8px"></span>' + _store_cat_fallback_html + "</span></div>"
        # Secondary store category
        '<div class="frow" id="dl-store-category2">'
        '<span class="fn">Store cat 2</span>'
        '<span class="fv">'
        '<select id="dl-store-cat2-select" '
        'style="background:#1a1a1a;color:#eee;border:1px solid #444;border-radius:4px;padding:3px 6px;font-size:.88em">' + _store_cat2_opts_html + "</select>"
        "</span></div>"
        # Best Offer (todo #1256) — per-item Inventory API field, not an
        # account default; enabling/disabling here is now authoritative.
        # Tri-state select, not a checkbox — "not set" is a real, distinct
        # value (leave eBay's category default alone), not just "false".
        f'<div class="frow" id="dl-best-offer">'
        f'<span class="fn">Best Offer</span>'
        f'<span class="fv">'
        f'<select id="dl-best-offer-enabled" '
        f'style="background:#1a1a1a;color:#eee;border:1px solid #444;border-radius:4px;padding:3px 6px;font-size:.85em">'
        f'<option value=""{" selected" if _dl_bo_select_val == "" else ""}>— not set (eBay default) —</option>'
        f'<option value="true"{" selected" if _dl_bo_select_val == "true" else ""}>Enabled</option>'
        f'<option value="false"{" selected" if _dl_bo_select_val == "false" else ""}>Disabled</option>'
        f"</select>"
        f'<input type="text" id="dl-best-offer-accept" placeholder="auto-accept $" value="{_dl_bo_accept_val}" '
        f'style="background:#1a1a1a;color:#eee;border:1px solid #444;border-radius:4px;padding:3px 6px;'
        f'font-size:.85em;width:110px;margin-left:8px">'
        f'<input type="text" id="dl-best-offer-decline" placeholder="auto-decline $" value="{_dl_bo_decline_val}" '
        f'style="background:#1a1a1a;color:#eee;border:1px solid #444;border-radius:4px;padding:3px 6px;'
        f'font-size:.85em;width:110px;margin-left:6px">'
        f"</span></div>"
        # Condition
        '<div class="frow" id="dl-condition">'
        '<span class="fn">Condition</span>'
        '<span class="fv">'
        f'<select id="dl-condition-select" '
        f'style="background:#1a1a1a;color:#eee;border:1px solid '
        f'{"#c44" if _cond_is_invalid else "#444"};border-radius:4px;padding:3px 6px">' + _cond_opts_html + f"</select>"
        f'<span id="condition-policy-note" style="font-size:.72em;color:#445;margin-left:8px"></span>'
        f"</span></div>"
        f'<div class="frow" id="dl-condition-desc">'
        f'<span class="fn">Cond. notes</span>'
        f'<span class="fv" style="flex:1">'
        f'<input id="dl-cond-desc-input" type="text" '
        f'value="{h((dl or dict()).get("condition_description") or "")}" '
        f'placeholder="e.g. minor wear on corners, no missing pieces" '
        f'style="width:100%;background:#1a1a1a;color:#eee;border:1px solid #444;'
        f'border-radius:4px;padding:4px 6px;font-size:.85em">'
        f"</span></div>"
        # Price with comps bar
        f'<div class="frow" id="dl-price">'
        f'<span class="fn">Price</span>'
        f'<span class="fv" style="flex:1">'
        f'<input id="dl-price-input" type="number" step="0.01" min="0" value="{h(_dl_price_val)}" '
        f'style="width:120px;background:#1a1a1a;color:#eee;'
        f"border:1px solid {'#c44' if (dl or {}).get('price') is None else '#444'};"
        f'border-radius:4px;padding:4px 6px;font-size:.9em">'
        f"{_price_comps_bar}"
        f"</span></div>"
        # Search terms — own frow, never nested inside a span
        f'<div class="frow" id="dl-search-terms">'
        f'<span class="fn">Search terms</span>'
        f'<span class="fv" style="flex:1">'
        f'<input id="search-terms-input" type="text" value="{_st_val}" '
        f'placeholder="e.g. vintage acetone bottle" '
        f'style="width:100%;background:#1a1a1a;color:#eee;border:1px solid #444;'
        f'border-radius:4px;padding:4px 6px;font-size:.88em" '
        f"onkeydown=\"if(event.key==='Enter')saveAndReprice()\">"
        f'<div style="margin-top:5px;display:flex;gap:6px;align-items:center;flex-wrap:wrap">'
        f'<a href="#" onclick="saveAndReprice();return false" '
        f'style="display:inline-block;padding:3px 10px;border-radius:4px;font-size:.8em;'
        f"font-weight:600;background:#1e3a5f;color:#7af;border:1px solid #2a5080;"
        f'text-decoration:none">'
        f"Save &amp; Re-price</a>"
        f'<a href="#" onclick="(function(){{'
        f"  var t=document.getElementById('search-terms-input');"
        f"  var q=t?t.value.trim():'';"
        f"  if(q)window.open('https://www.ebay.com/sch/i.html?_nkw='+encodeURIComponent(q)+'&LH_Complete=1&LH_Sold=1','_blank');"
        f'}})();return false" '
        f'style="display:inline-block;padding:3px 10px;border-radius:4px;font-size:.8em;'
        f"font-weight:600;background:#1a3a1a;color:#4d4;border:1px solid #2a5a2a;"
        f'text-decoration:none">'
        f"eBay Sold ↗</a>"
        f'<a href="#" onclick="(function(){{'
        f"  var t=document.getElementById('search-terms-input');"
        f"  var q=t?t.value.trim():'';"
        f"  if(q)window.open('https://www.ebay.com/sch/i.html?_nkw='+encodeURIComponent(q),'_blank');"
        f'}})();return false" '
        f'style="display:inline-block;padding:3px 10px;border-radius:4px;font-size:.8em;'
        f"font-weight:600;background:#1a1a2a;color:#88c;border:1px solid #2a2a4a;"
        f'text-decoration:none">'
        f"eBay Active ↗</a>"
        f'<a href="#" onclick="(function(){{'
        f"  var t=document.getElementById('search-terms-input');"
        f"  var q=t?t.value.trim():'';"
        f"  if(q)window.open('https://www.ebay.com/sh/research?marketplace=EBAY-US&keywords='+encodeURIComponent(q),'_blank');"
        f'}})();return false" '
        f'style="display:inline-block;padding:3px 10px;border-radius:4px;font-size:.8em;'
        f"font-weight:600;background:#2a1a0a;color:#c84;border:1px solid #4a2a0a;"
        f'text-decoration:none">'
        f"Terapeak ↗</a>"
        f'<span id="reprice-msg" style="color:#4a4;font-size:.8em"></span>'
        f"</div>"
        f"</span></div>"
        # Description
        f'<div class="frow" id="dl-description">'
        f'<span class="fn">Description</span>'
        f'<span class="fv" style="flex:1">'
        f'<textarea id="dl-desc-input" rows="3" '
        f'style="width:100%;background:#1a1a1a;color:#eee;border:1px solid #444;'
        f'border-radius:4px;padding:4px 6px;font-size:.85em;resize:vertical">'
        f"{_dl_desc_val}</textarea>"
        f"</span></div>"
        # Shipping
        f'<div class="frow" id="dl-shipping">'
        f'<span class="fn">Fulfillment</span>'
        f'<span class="fv">'
        f'<select id="dl-ship-input" '
        f'style="background:#1a1a1a;color:#eee;border:1px solid #444;border-radius:4px;padding:3px 6px;font-size:.85em">'
        f'<option value="">— auto-resolved —</option>'
        f"{_ship_opts_html}"
        f"</select>"
        f'<span id="dl-ship-hint" style="font-size:.72em;color:#445;margin-left:8px"></span>'
        f"{_fo_fallback_html}"
        f"{_live_ship_html}"
        f"</span></div>"
        f'<div class="frow" id="dl-returns">'
        f'<span class="fn">Returns</span>'
        f'<span class="fv">'
        f'<select id="dl-return-select" '
        f'style="background:#1a1a1a;color:#eee;border:1px solid #444;border-radius:4px;padding:3px 6px;font-size:.85em">'
        f"{_return_opts_html}"
        f"</select>"
        f"</span></div>"
        # Aspects
        f'<div id="dl-aspects" style="margin-top:10px">'
        f'<div style="font-size:.83em;color:#aaa;font-weight:600;margin-bottom:6px">'
        f"Item Specifics / Aspects</div>"
        f'<div id="aspects-loading" style="color:#556;font-size:.82em">Loading aspects…</div>'
        f'<div id="aspects-form"></div>'
        # Todo #1470 (live incident, 2026-07-16, Dave: "how will this account
        # for custom aspect fields? that is where the solution lies"): a
        # category-defined aspect is only ever whatever the Item Specifics
        # lookup returns for the current category, but eBay's own Inventory
        # API accepts additional seller-defined "custom aspects" beyond that
        # list — real, intentional, buyer-visible fields. Without an explicit
        # way to add one, every custom aspect on this item so far arrived by
        # accident (an AI draft under a since-changed category), not by
        # design — and the read-only "surface what's already stored" half of
        # this fix alone would just recreate the same invisible-field problem
        # the next time a category changes. This control lets an operator
        # deliberately add one; addCustomAspect() appends a row with the same
        # data-aspect/data-initial="" contract every other aspect input uses,
        # so saveEbayDraft()'s existing collection loop picks it up with zero
        # changes there.
        f'<div style="margin-top:6px;display:flex;gap:6px;align-items:center">'
        f'<input type="text" id="new-aspect-name" placeholder="Custom aspect name" '
        f'style="background:#1a1a1a;color:#eee;border:1px solid #444;border-radius:3px;'
        f'padding:2px 6px;font-size:.82em;width:160px">'
        f'<input type="text" id="new-aspect-value" placeholder="Value" '
        f'style="background:#1a1a1a;color:#eee;border:1px solid #444;border-radius:3px;'
        f'padding:2px 6px;font-size:.82em;width:160px">'
        f'<button class="act-btn" style="font-size:.78em;padding:2px 8px" '
        f'onclick="addCustomAspect()">+ Add custom aspect</button>'
        f'<span id="new-aspect-msg" style="font-size:.78em;color:#c44"></span>'
        f"</div>"
        f"</div>"
        # Todo #1472 (Dave, 2026-07-16): the old standalone "Aspects not in
        # this category" migration panel (a separate always-checked
        # confirm()+immediate-apply flow, disconnected from the main Save)
        # is retired here. Its checkbox-to-discard semantics now live
        # inline in #aspects-form itself, alongside each custom/orphaned
        # aspect's own input — one Save Draft click drives both. See
        # `.aspect-keep-cb` in `_CATEGORY_CONTEXT_IIFE` and the
        # saveEbayDraft() discard chain below; the underlying detect/apply
        # endpoints (todo #1471) are unchanged, only re-wired to this
        # single entry point instead of their own panel + button.
        # Explicit Save (restored 2026-07-10, todo #1318): List on eBay / Update
        # Item save the draft first as part of their own flow, but the
        # _has_error-and-not-is_active action-line state renders ONLY "Retry"
        # (which just scrolls to the error box — it never calls saveEbayDraft()).
        # An operator fixing a rejected field (e.g. a too-long title) had no way
        # to persist the edit at all without first clicking "Clear error" to
        # reach a different action-line state — not discoverable, and the old
        # rejected content kept getting resubmitted. This button covers every
        # state, not just the error one, since draft edits should always be
        # explicitly savable regardless of what the action line shows.
        f'<div style="margin-top:12px;display:flex;gap:8px;align-items:center">'
        f'<button class="act-btn" onclick="saveEbayDraft()" '
        f'style="background:#1a3a5a">Save Draft</button>'
        f'<span id="dl-save-msg" style="font-size:.82em;color:#4a4"></span>'
        f"</div>" + f"</div>"
        # hidden data for JS
        f"<script>"
        f"window._DL_CAT_ID = {_aspects_cat_json};"
        f"window._DL_PREFILL = {_aspects_prefill_json};"
        f"window._LIVE_ASPECTS = {_live_aspects_json};"
        f"window._PROPOSED_ASPECTS = {_proposed_aspects_json};"
        f"window._DL_PROPOSALS = {_proposals_meta_json};"
        f"</script>"
    )

    # PP-ACTIONCONSOLE-001: the "eBay Status" dropdown is gone — its content
    # graduated into the Live Listing tab (listing_section + offer_section).

    _left_log_html = (
        '<div class="dleft-log">'
        + _readiness_html_str
        + _eps_strip_html
        + (f'<div class="dsec" id="jobs-section"><h3>Pipeline Jobs</h3>{jobs_html}</div>' if jobs_html else "")
        + price_history_html
        + reprice_schedule_html
        + product_lookup_html
        + identification_history_html
        + "</div>"
    )

    # ── PP-ACTIONCONSOLE-001: tabbed right column ──────────────────────────────
    # Editor tab always exists; the Live Listing tab appears only when the item
    # is on eBay — the tab's existence IS the "this item is live" indicator.
    # Sold: the live tab becomes "Sold Listing" and moves to the front; the
    # editor stays behind it.
    _has_live_tab = bool(listing_id or _ebay_live_raw)
    _live_tab_label = "Sold Listing" if _is_sold else "Live Listing"
    _default_tab = "live" if (_is_sold and _has_live_tab) else "editor"

    def _tab_btn(name: str, label: str) -> str:
        active = " dtab-active" if name == _default_tab else ""
        return f'<button class="dtab-btn{active}" data-tab="{name}" onclick="showTab(\'{name}\')">{label}</button>'

    _tab_bar = (
        "<style>.dtab-btn{padding:6px 16px;background:#141414;color:#889;border:1px solid #2a2a2a;"
        "border-bottom:none;border-radius:6px 6px 0 0;cursor:pointer;font-size:.85em}"
        ".dtab-btn.dtab-active{background:#1a1a1a;color:#cce;border-color:#3a3a3a;font-weight:600}"
        "</style>"
        '<div class="dtab-bar" style="display:flex;gap:4px;margin-bottom:0">'
        + (_tab_btn("live", _live_tab_label) if (_has_live_tab and _is_sold) else "")
        + _tab_btn("editor", "Editor")
        + (_tab_btn("live", _live_tab_label) if (_has_live_tab and not _is_sold) else "")
        + "</div>"
    )

    _editor_panel_open = '<div class="dtab-panel" data-tab="editor"' + (' style="display:none"' if _default_tab != "editor" else "") + ">"

    fields_html = (
        '<div class="dfields">'
        # ── Inventory Record — TGW's canonical layer, NOT part of the eBay
        # listing workflow. Standalone at the top (own redesign effort later).
        + '<div id="catalog-section" class="dsec">'
        '<h3>Inventory Record <span style="color:#2a4a6a;font-size:.72em;font-weight:normal">dbl-click to edit · <a href="#" onclick="saveCatalog();return false" style="color:#4a8;font-size:.9em">Save to Catalog</a> <span id="catalog-save-msg" style="font-size:.82em;color:#4a4"></span></span></h3>'
        '<div style="font-size:.73em;color:#556;margin-bottom:6px">Canonical TGW data — Title/Description follow the eBay draft unless 🔒 locked (Dave, 2026-07-18); everything else here is never overwritten by marketplace sync</div>'
        + fr("Title", key="title", editable=True, lockable=True)
        + fr("Condition", key="condition", editable=True)
        + fr("AI hint", key="ai_hint", editable=True)
        + fr("Description", key="description", editable=True, lockable=True)
        + fr("Brand", key="brand", editable=True)
        + fr("Model", key="model", editable=True)
        + fr("Category group", key="category_group")
        + fr("Barcode", key="barcode")
        + fr("Price", _price_display + '<span style="font-size:.7em;color:#3a6a3a;margin-left:5px">(set in eBay Draft Editor below)</span>')
        + fr("Qty", _qty_display, key="qty", editable=True)
        + fr("Floor price", key="floor_price", editable=True)
        + fr("Cost (what we paid)", key="cost", editable=True)
        + fr("Location", key="location", editable=True)
        + fr("Weight (oz)", key="weight_oz", editable=True)
        + fr("Size class", key="size_class")
        + fr("Verified", key="verified")
        + fr("Alt text", h(str(dl.get("alt_text") or item.get("alt_text") or "")))
        + fr("Status", key="status", editable=True)
        + fr("Manufacturer", key="manufacturer", editable=True)
        + fr("Country of mfr", key="country_of_manufacture", editable=True)
        + fr("Model number", key="model_number", editable=True)
        + (
            fr("SKU (old)", h(str(item.get("sku_old", "") or "")) + f' <a href="/form/history/{urllib.parse.quote(str(item.get("sku_old")))}" style="color:#4a8;font-size:.85em">History &rarr;</a>')
            if item.get("sku_old")
            else ""
        )
        + (fr("UPC", h(str(item.get("upc", "") or ""))) if item.get("upc") else "")
        + (fr("ISBN", h(str(item.get("isbn", "") or ""))) if item.get("isbn") else "")
        + (fr("Part number", h(str(item.get("part_number", "") or ""))) if item.get("part_number") else "")
        + (
            # todo #1416 point 5: this panel is explicitly labeled "Canonical
            # TGW data — never overwritten by marketplace sync" (see the
            # `<div>` a few lines up), so it must show Set A
            # (`item_attributes`) UNMERGED — never blended with Set B
            # (`draft_listing.item_specifics`) into one ambiguous key/value
            # row, which was #1418's confirmed bug (`{**isp, **ia}`, Set A
            # silently winning on any overlapping key with no way to tell
            # which set a viewer was looking at). The eBay-side value is
            # shown as a clearly-separate, dimmed secondary line under the
            # same row only when it exists AND differs, so a viewer can
            # compare without either value being ambiguous about its set.
            # todo #1475 (Dave, 2026-07-16: "the initial item draft view after
            # import should show all filled fields, not just the ones the
            # eBay category requires or recommends... gives the operator all
            # the data we have to choose from"): every Set A key not already
            # present in Set B gets a "+ Add to listing" button, wired to
            # addFromInventory() (shared row-builder with addCustomAspect(),
            # #1472's own aspect-keep-cb pattern) — clicking it adds the value
            # into #aspects-form as a custom aspect, same as if the operator
            # typed it in themselves. Purely additive UI; no write happens
            # until the operator clicks Add AND then Save Draft.
            # Padlock design (Dave, 2026-07-18): a key with no lock icon set
            # just always follows the eBay draft — that sync happens
            # automatically on every Save Draft (_apply_patch), not from
            # anything on this page. The lock icon is the ONLY thing this
            # panel controls directly; clicking it flips the key's synced/
            # frozen state and reloads so the row reflects it immediately.
            lambda ia, isp, locked: (
                (
                    '<div style="margin-top:6px;border-top:1px solid #222;padding-top:6px">'
                    '<div style="font-size:.75em;color:#556;margin-bottom:4px">Inventory Record specifics'
                    ' <span style="font-weight:normal">— 🔓 follows the eBay draft automatically, 🔒 frozen at its current value</span></div>'
                    + "".join(
                        f'<div class="frow"><span class="fn" style="font-size:.82em">{h(k)}</span>'
                        f'<span class="fv" style="font-size:.82em">{h(str(v))}' + f" <button onclick='toggleInventoryLock({json.dumps(k)},{json.dumps(k in locked)},this)' "
                        # Single-quoted onclick (Dave, 2026-07-18 live report:
                        # "the lock buttons have no effect, but they are
                        # there"): json.dumps() always double-quotes its
                        # string output, and this attribute used to be
                        # double-quoted too — the browser's HTML parser
                        # terminated the attribute at the FIRST embedded `"`,
                        # e.g. onclick="toggleInventoryLock(" was the entire
                        # parsed value. Button rendered fine; click did
                        # nothing, because there was no valid handler left to
                        # run. Same latent bug existed in the pre-existing
                        # "+ Add to listing" button below — fixed here too.
                        f'title="{"Locked — click to let this key follow the eBay draft again" if k in locked else "Unlocked — click to freeze this key at its current value"}" '
                        f'style="background:none;border:none;cursor:pointer;font-size:.85em;padding:0 2px">{"🔒" if k in locked else "🔓"}</button>'
                        + (
                            f'<div style="font-size:.72em;color:#556;margin-top:1px">eBay value: {h(str(isp[k]))}</div>'
                            if k in isp and str(isp[k]) != str(v)
                            else (
                                f'<button class="act-btn" style="font-size:.7em;padding:1px 6px;margin-left:8px" '
                                f"onclick='addFromInventory({json.dumps(k)},{json.dumps(str(v))},this)'>+ Add to listing</button>"
                                if v and k not in isp and k != "Title"
                                else ""
                            )
                        )
                        + "</span></div>"
                        for k, v in sorted(ia.items())
                    )
                    + "</div>"
                )
                if ia
                else (
                    '<div style="margin-top:6px;border-top:1px solid #222;padding-top:6px">'
                    '<span style="font-size:.75em;color:#333">No inventory record specifics yet — fill in eBay Draft Editor below</span>'
                    "</div>"
                )
            )
        )(inventory_record.get_inventory_fields(item), get_ebay_aspects(item), set(inventory_record.get_locked_keys(item)))
        + "</div>"
        # ── eBay -> Inventory Record sync panel (todo #1417, PP-LISTEDITOR-001).
        # DELIBERATELY separate from the "Pipeline proposed changes" banner
        # below (accept_proposals, forward direction, revision_draft ->
        # draft_listing.item_specifics): this panel is the REVERSE direction
        # (draft_listing.item_specifics -> item_attributes), a different data
        # source and destination, its own button, no shared action name — an
        # operator can never confuse the two (spec point 3). Populated by JS
        # (loadInventoryDiff()) via GET /api/items/{sku}/inventory-diff so the
        # diff is always freshly recomputed, never stale server-rendered state.
        + '<div id="inv-diff-panel" class="dsec" style="display:none;margin-top:10px;'
        'padding:10px 14px;background:#0a1a1a;border:1px solid #366;border-radius:4px">'
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">'
        '<strong style="font-size:.85em;color:#8cc">eBay → Inventory Record sync</strong>'
        '<span style="font-size:.72em;color:#568">values eBay/AI resolved that differ from the universal record</span>'
        "</div>"
        '<div style="font-size:.75em;color:#568;margin-bottom:8px">'
        "Every row is checked by default — uncheck to skip a field. Unchecked/skipped "
        "fields simply reappear here next time (nothing is dismissed).</div>"
        '<div id="inv-diff-rows"></div>'
        '<div style="display:flex;gap:8px;margin-top:8px">'
        '<button class="act-btn act-publish" style="font-size:.78em;padding:3px 10px;'
        'background:#0a1a1a;border-color:#366" onclick="applyInventoryDiff()">'
        "Apply Checked to Inventory Record</button>"
        "</div></div>"
        # ── eBay Listing — the draft/live workflow section (PP-ACTIONCONSOLE-001).
        # Visually separated from the Inventory Record above: its own header,
        # action line, and Editor/Live tabs.
         + '<div id="ebay-listing-block" style="margin-top:18px;padding-top:14px;'
        'border-top:2px solid #2a3a4a">'
        '<h3 style="margin:0 0 10px;font-size:.78em;text-transform:uppercase;'
        'letter-spacing:.06em;color:#6a8ab5">eBay Listing</h3>'
        + _pipeline_error_html
        + _gate_html
        + _tab_bar
        + _editor_panel_open
        # — Pipeline proposals banner (inside the editor tab, above the draft form)
        + (_proposals_banner if _has_proposals else "")
        # — eBay Draft editor
        + _ebay_draft_editor
        + "</div>"  # close editor tab panel
        # — Live/Sold Listing tab: read-only view of what eBay holds
        + (
            (
                '<div class="dtab-panel" data-tab="live"'
                + (' style="display:none"' if _default_tab != "live" else "")
                + ">"
                + '<div class="dsec">'
                + f"<h3>{_live_tab_label}{listing_badge}{offer_badge}"
                + (
                    '<a href="#" onclick="triggerAction(\'sync_from_ebay\');return false" style="float:right;color:#4a8ade;font-size:.85em;font-weight:normal">⟳ Sync from eBay</a>'
                    if listing_id
                    else ""
                )
                + "</h3>"
                + listing_section
                + offer_section
                + "</div>"
                + _ebay_live_html
                + "</div>"
            )
            if _has_live_tab
            else ""
        )
        + "</div>"  # close ebay-listing-block
        + "</div>"
    )

    # PP-ACTIONCONSOLE-001: the Pipeline Tools button set is gone from this page —
    # troubleshooting controls belong on a separate ops surface; repair actions
    # appear contextually on failed pipeline-job lines. Archive/Delete moved into
    # the action line. Only the shared JS remains here.
    _prep_action_json = _json.dumps("ebay_draft" if item.get("ai_identified") else "ai_identify")
    actions_html = (
        f"<script>"
        f"window.TGW_API_KEY={_ak_json2};"
        f"var _SKU={_sku_json2};"
        f"var _LISTING_ID={_listing_id_json};"
        f"var _IS_ACTIVE={_is_active_js};"
        f"var _PREP_ACTION={_prep_action_json};"
        f"window._ITEM_SKU={_sku_json2};"
        # ── Action-line handlers (state-driven console) ──
        f"function prepareListing(){{"
        f"  if(!confirm('Prepare this listing?\\nRuns identification and drafts the eBay listing.'))return;"
        f"  triggerAction(_PREP_ACTION);"
        f"}}"
        f"function listOnEbay(){{"
        f"  if(!confirm('List this item on eBay?\\nSaves the draft, runs every needed step, and publishes.'))return;"
        f"  saveEbayDraft(function(){{executePublishedCommand('list-item');}},'publish');"
        f"}}"
        f"function updateItem(){{"
        f"  if(!confirm('Push draft changes to the live listing?\\nUpdates in place without ending the listing.'))return;"
        f"  saveEbayDraft(function(){{executePublishedCommand('update-item');}});"
        f"}}"
        f"function executePublishedCommand(commandId){{"
        f"  fetch('/api/operator/items/'+_SKU,{{headers:authHeaders()}})"
        f"  .then(function(r){{return r.json();}}).then(function(view){{"
        f"    var obj=view&&view.object;"
        f"    var command=obj&&(obj.commands||[]).find(function(c){{return c.id===commandId;}});"
        f"    if(!command)throw new Error('Command is not published for this item.');"
        f"    if(!command.enabled)throw new Error(command.reason||'Command is held by the server.');"
        f"    return fetch('/api/operator/items/'+_SKU+'/commands',{{method:'POST',"
        f"      headers:authHeaders({{'Content-Type':'application/json'}}),"
        f"      body:JSON.stringify({{command_id:command.id,object_generation:obj.object_generation,values:{{}}}})}});"
        f"  }}).then(function(r){{return r.json().then(function(d){{return [r,d];}});}})"
        f"  .then(function(pair){{if(!pair[0].ok)throw new Error(JSON.stringify(pair[1].detail||pair[1]));location.reload();}})"
        f"  .catch(function(e){{alert('Command held: '+e.message);}});"
        f"}}"
        f"function relistItem(){{"
        f"  if(!confirm('Relist this item on eBay?\\nCheck quantity in the Inventory Record first.'))return;"
        f"  saveEbayDraft(function(){{triggerAction('ebay_publish');}},'publish');"
        f"}}"
        f"function resetDraft(){{"
        f"  if(!confirm('Reset the draft?\\nDiscards draft edits and regenerates from the catalog record.'))return;"
        f"  triggerAction('ebay_draft');"
        f"}}"
        f"function resetDraftFromLive(){{"
        f"  if(!confirm('Reset the draft from the live listing?\\nDiscards local edits — the draft will match eBay again.'))return;"
        f"  triggerAction('reset_draft_from_live');"
        f"}}"
        f"function toggleApprove(cb){{"
        f"  triggerAction(cb.checked?'set_ready':'unset_ready');"
        f"}}"
        f"function retryPipeline(){{"
        f"  var el=document.getElementById('pipeline-error-box')||document.getElementById('jobs-section');"
        f"  if(el)el.scrollIntoView({{behavior:'smooth',block:'center'}});"
        f"}}"
        f"function retryJob(jobId){{"
        f"  if(!confirm('Retry this failed job?'))return;"
        f"  fetch('/api/jobs/'+jobId+'/requeue',{{method:'POST',headers:authHeaders()}})"
        f"  .then(function(r){{return r.json();}}).then(function(d){{"
        f"    if(d.ok)waitForAction(d.action||'',d.new_job_id||d.job_id||'');"
        f"    else alert('Retry failed: '+(d.detail||'error'));"
        f"  }}).catch(function(e){{alert('Network error: '+e);}});"
        f"}}"
        f"function showTab(name){{"
        f"  document.querySelectorAll('.dtab-panel').forEach(function(p){{p.style.display=p.dataset.tab===name?'':'none';}});"
        f"  document.querySelectorAll('.dtab-btn').forEach(function(b){{b.classList.toggle('dtab-active',b.dataset.tab===name);}});"
        f"}}"
        f"function triggerAction(action,confirmMsg){{"
        f"  if(confirmMsg&&!confirm(confirmMsg))return;"
        f"  fetch('/api/items/'+_SKU+'/action',{{"
        f"    method:'POST',"
        f"    headers:authHeaders({{'Content-Type':'application/json'}}),"
        f"    body:JSON.stringify({{action:action}})"
        f"  }}).then(function(r){{return r.json();}}).then(function(d){{"
        f"    if(d.ok&&action==='archive'){{window.location.href='/form/items';return;}}"
        f"    if(d.ok&&(action==='set_ready'||action==='unset_ready'||action==='reset_draft_from_live')){{location.reload();return;}}"
        f"    if(d.ok&&(action==='ai_identify'||action==='ebay_draft'||action==='ebay_price'||action==='ebay_upload'||action==='ebay_publish'||action==='ebay_stage')){{waitForAction(action,d.job_id||'');return;}}"
        f"    alert(d.ok ? 'Action queued: '+action : 'Error: '+(d.detail||'failed'));"
        f"  }}).catch(function(e){{alert('Network error: '+e);}});"
        f"}}"
        f"function waitForAction(action,jobId){{"
        f"  var deadline=Date.now()+120000;"
        f"  var label=document.getElementById('pipeline-action-status');"
        f"  if(!label){{label=document.createElement('div');label.id='pipeline-action-status';"
        f"    label.style.cssText='position:fixed;right:16px;bottom:16px;padding:8px 12px;background:#182638;border:1px solid #4878a8;border-radius:5px;color:#bdddff;z-index:9999';document.body.appendChild(label);}}"
        f"  label.textContent='Working: '+(action||'retry')+'…';"
        f"  function poll(){{fetch('/api/items/'+_SKU,{{headers:authHeaders()}})"
        f"    .then(function(r){{return r.json();}}).then(function(d){{"
        f"      var item=(d&&d.item)||{{}};var jobs=item._queue_jobs||[];"
        f"      var job=jobId?jobs.find(function(j){{return j.job_id===jobId;}}):null;"
        f"      var active=job&&['queued','leased','running','retry_wait'].indexOf(job.state)>=0;"
        f"      var aiPending=(action==='ai_identify'||action==='ebay_draft')&&"
        f"        (item.ai_reidentify===true||item.ai_redraft_requested===true||jobs.some(function(j){{return ['ai_identify','ebay_draft'].indexOf(j.queue_name)>=0&&['queued','leased','running','retry_wait'].indexOf(j.state)>=0;}}));"
        f"      if((job&&!active&&!aiPending)||(!jobId&&!aiPending)||Date.now()>=deadline){{location.reload();return;}}"
        f"      setTimeout(poll,1500);"
        f"    }}).catch(function(){{if(Date.now()>=deadline)location.reload();else setTimeout(poll,1500);}});}}"
        f"  setTimeout(poll,750);"
        f"}}"
        f"function publishNow(){{"
        f"  if(!confirm('Publish this item to eBay NOW?\\nIt will go live immediately at the current offer price.'))return;"
        f"  triggerAction('ebay_publish');"
        f"}}"
        f"function acceptProposals(){{"
        f"  if(!confirm('Accept all pipeline proposals?\\nThis copies proposed values into your draft.'+"
        f"'\\nReview then click Update Listing to push to eBay.'))return;"
        f"  fetch('/api/items/'+_SKU+'/action',{{method:'POST',"
        f"    headers:authHeaders({{'Content-Type':'application/json'}}),"
        f"    body:JSON.stringify({{action:'accept_proposals'}})}}"
        f"  ).then(function(r){{return r.json();}}).then(function(d){{"
        f"    if(d.ok)location.reload();else alert('Accept failed: '+(d.detail||'error'));"
        f"  }});"
        f"}}"
        f"function resyncPhotos(){{"
        f"  var el=document.getElementById('resync-photos-result');"
        f"  if(el)el.textContent='resyncing…';"
        f"  fetch('/api/items/'+_SKU+'/action',{{method:'POST',"
        f"    headers:authHeaders({{'Content-Type':'application/json'}}),"
        f"    body:JSON.stringify({{action:'resync_photos'}})}}"
        f"  ).then(function(r){{return r.json();}}).then(function(d){{"
        f"    if(!el)return;"
        f"    if(d.ok&&d.upload_queued){{el.textContent=d.detail;}}"
        f"    else if(d.ok){{"
        f"      el.textContent=d.confirmed_count+'/'+d.submitted_count+' confirmed live — reloading…';"
        # Reload so the "Photos on eBay" strip (and everything else on the
        # page) picks up the just-refreshed ebay_live/ebay_submitted data —
        # otherwise the button reports success while the page keeps
        # showing whatever was rendered at initial page load (Dave,
        # 2026-07-17: "it says 24/24... but only one shows on the webui").
        f"      setTimeout(function(){{location.reload();}},900);"
        f"    }}"
        f"    else{{el.textContent='failed: '+(d.detail||'error');}}"
        f"  }}).catch(function(e){{if(el)el.textContent='network error';}});"
        f"}}"
        f"function dismissProposals(){{"
        f"  fetch('/api/items/'+_SKU+'/action',{{method:'POST',"
        f"    headers:authHeaders({{'Content-Type':'application/json'}}),"
        f"    body:JSON.stringify({{action:'dismiss_proposals'}})}}"
        f"  ).then(function(r){{return r.json();}}).then(function(d){{"
        f"    if(d.ok)location.reload();else alert('Dismiss failed: '+(d.detail||'error'));"
        f"  }});"
        f"}}"
        # ── loadInventoryDiff / applyInventoryDiff — todo #1417, the REVERSE
        # (eBay Draft -> Inventory Record) sync panel. Deliberately its own
        # fetch/render/apply cycle, no shared code with accept/dismiss
        # Proposals above (different data, different destination — spec
        # point 6/3). Always re-fetches live on load; no client-cached diff
        # state is trusted across a page action.
        f"function loadInventoryDiff(){{"
        f"  fetch('/api/items/'+_SKU+'/inventory-diff',{{headers:authHeaders()}})"
        f"  .then(function(r){{return r.json();}}).then(function(d){{"
        f"    var panel=document.getElementById('inv-diff-panel');"
        f"    var rows=document.getElementById('inv-diff-rows');"
        f"    if(!panel||!rows)return;"
        f"    if(!d.ok||!d.diffs||!d.diffs.length){{panel.style.display='none';rows.innerHTML='';return;}}"
        f"    var html='';"
        f"    d.diffs.forEach(function(fd){{"
        f"      var invVal=(fd.inventory_value===null||fd.inventory_value===undefined)?'(none)':String(fd.inventory_value);"
        f'      html+=\'<div class="frow"><span class="fn" style="font-size:.82em">\''
        f'        +\'<label><input type="checkbox" class="inv-diff-cb" data-key="\'+fd.key.replace(/"/g,\'&quot;\')+\'" checked> \''
        f"        +fd.key+'</label></span>"
        f'        <span class="fv" style="font-size:.82em">\'+String(fd.ebay_value)'
        f"        +'<div style=\"font-size:.72em;color:#568;margin-top:1px\">inventory record: '+invVal"
        f"        +' &middot; source: '+(fd.source||'?')+(fd.detected_at?(' &middot; '+fd.detected_at):'')+'</div>'"
        f"        +'</span></div>';"
        f"    }});"
        f"    rows.innerHTML=html;"
        f"    panel.style.display='';"
        f"  }}).catch(function(){{}});"
        f"}}"
        f"function applyInventoryDiff(){{"
        f"  var checked=[];"
        f"  document.querySelectorAll('.inv-diff-cb:checked').forEach(function(cb){{checked.push(cb.dataset.key);}});"
        f"  if(!checked.length){{alert('No rows checked — nothing to apply.');return;}}"
        f"  fetch('/api/items/'+_SKU+'/inventory-diff/apply',{{method:'POST',"
        f"    headers:authHeaders({{'Content-Type':'application/json'}}),"
        f"    body:JSON.stringify({{keys:checked}})}}"
        f"  ).then(function(r){{return r.json();}}).then(function(d){{"
        f"    if(d.ok)location.reload();else alert('Apply failed: '+(d.detail||'error'));"
        f"  }}).catch(function(e){{alert('Network error: '+e);}});"
        f"}}"
        f"function clearPipelineError(){{"
        f"  fetch('/api/items/'+_SKU,{{method:'PATCH',"
        f"    headers:authHeaders({{'Content-Type':'application/json'}}),"
        f"    body:JSON.stringify({{fields:{{pipeline_error:null}}}})}}"
        f"  ).then(function(r){{return r.json();}}).then(function(d){{"
        f"    if(d.ok)location.reload();else alert('Clear failed: '+(d.detail||'error'));"
        f"  }});"
        f"}}"
        f"function toggleRawError(){{"
        f"  var el=document.getElementById('pipeline-error-raw');"
        f"  if(!el)return;"
        f"  if(el.style.display==='none'){{el.textContent=_PE_RAW;el.style.display='block';}}"
        f"  else{{el.style.display='none';}}"
        f"}}"
        f"function deleteItem(){{"
        f"  var msg='Delete '+_SKU+'?\\nThis marks the item as deleted.\\nThe ItemData folder is preserved.';"
        f"  if(_IS_ACTIVE){{msg+='\\n\\n⚠️ Active eBay listing (ID: '+_LISTING_ID+').\\nActive marketplace state must be resolved through a governed listing command before local deletion.';}} "
        f"  if(!confirm(msg))return;"
        f"  fetch('/api/items/'+_SKU,{{"
        f"    method:'DELETE',"
        f"    headers:authHeaders()"
        f"  }}).then(function(r){{return r.json();}}).then(function(d){{"
        f"    if(d.ok){{window.location.href='/form/items';}}"
        f"    else{{alert('Delete failed: '+(d.detail||'unknown error'));}}"
        f"  }}).catch(function(e){{alert('Network error: '+e);}});"
        f"}}"
        f"document.querySelectorAll('.fv.editable').forEach(function(span){{"
        f"  span.addEventListener('dblclick',function(){{"
        f"    var field=span.dataset.field;"
        f"    var oldVal=span.dataset.raw!==undefined?span.dataset.raw:span.textContent;"
        f"    if(oldVal==='—')oldVal='';"
        f"    var inp=document.createElement('input');"
        f"    inp.type='text';inp.value=oldVal;inp.className='fv fv-edit';"
        f"    span.parentNode.replaceChild(inp,span);"
        f"    inp.focus();inp.select();"
        f"    function commit(){{"
        f"      var newVal=inp.value;"
        f"      inp.disabled=true;"
        f"      fetch('/api/items/'+_SKU,{{"
        f"        method:'PATCH',"
        f"        headers:authHeaders({{'Content-Type':'application/json'}}),"
        f"        body:JSON.stringify({{fields:{{[field]:newVal}}}})"
        f"      }}).then(function(r){{return r.json();}}).then(function(d){{"
        f"        if(d.ok){{"
        f"          span.textContent=newVal||'—';"
        f"          span.dataset.raw=newVal;"
        f"          inp.parentNode.replaceChild(span,inp);"
        f"          span.classList.add('fv-saved');"
        f"          setTimeout(function(){{span.classList.remove('fv-saved');}},800);"
        f"        }}else{{"
        f"          alert('Save failed: '+(d.detail||'error'));"
        f"          inp.parentNode.replaceChild(span,inp);"
        f"        }}"
        f"      }}).catch(function(e){{"
        f"        alert('Network error: '+e);"
        f"        inp.parentNode.replaceChild(span,inp);"
        f"      }});"
        f"    }}"
        f"    function cancel(){{inp.parentNode.replaceChild(span,inp);}}"
        f"    inp.addEventListener('keydown',function(e){{"
        f"      if(e.key==='Enter'){{e.preventDefault();commit();}}"
        f"      if(e.key==='Escape')cancel();"
        f"    }});"
        f"    inp.addEventListener('blur',function(){{if(!inp.disabled)commit();}});"
        f"  }});"
        f"}});"
        # ── flagFieldInvalid — PP-CONDITION-ENUM-001 / todo #1562 ──────────────
        # One shared function for "flag this field red when invalid," so every
        # draft field (title length, condition enum, and any future
        # save-error-tagged field) gets the same visible treatment instead of
        # each growing its own bespoke border-color check. Callable from
        # inline onload code (no element handle needed — pass an id) or with
        # an element reference directly.
        f"function flagFieldInvalid(elOrId,isInvalid){{"
        f"  var el=(typeof elOrId==='string')?document.getElementById(elOrId):elOrId;"
        f"  if(!el)return;"
        f"  el.style.borderColor=isInvalid?'#c44':'#444';"
        f"  el.setAttribute('aria-invalid',isInvalid?'true':'false');"
        f"}}"
        # ── updateCharCount ───────────────────────────────────────────────────
        f"function updateCharCount(inp,max,countId){{"
        f"  var n=inp.value.length;"
        f"  var el=document.getElementById(countId);"
        f"  flagFieldInvalid(inp,n>max);"
        f"  if(!el)return;"
        f"  el.textContent=n+'/'+max;"
        f"  el.style.color=n>max?'#c44':(n>max*0.9?'#aa0':'#556');"
        f"}}"
        # ── saveCatalog ───────────────────────────────────────────────────────
        f"function saveCatalog(){{"
        f"  var fields={{}};"
        f'  ["title","condition","floor_price","cost","location","ai_hint"].forEach(function(k){{'
        f"    var span=document.querySelector('.fv.editable[data-field=\"'+k+'\"]');"
        f"    if(span&&span.dataset.raw!==undefined)fields[k]=span.dataset.raw;"
        f"  }});"
        f"  if(!Object.keys(fields).length){{alert('No catalog fields to save.');return;}}"
        f"  var msg=document.getElementById('catalog-save-msg');"
        f"  if(msg)msg.textContent='Saving…';"
        f"  fetch('/api/items/'+_SKU,{{"
        f"    method:'PATCH',"
        f"    headers:authHeaders({{'Content-Type':'application/json'}}),"
        f"    body:JSON.stringify({{fields:fields}})"
        f"  }}).then(function(r){{return r.json();}}).then(function(d){{"
        f"    if(msg){{msg.textContent=d.ok?'✓ Saved':' Error: '+(d.detail||'failed');"
        f"    msg.style.color=d.ok?'#4a4':'#c44';"
        f"    if(d.ok)setTimeout(function(){{msg.textContent='';}},2000);}}"
        f"  }}).catch(function(e){{if(msg){{msg.textContent='Network error';msg.style.color='#c44';}}}});"
        f"}}"
        # ── saveEbayDraft ─────────────────────────────────────────────────────
        f"function saveEbayDraft(done,intent){{"
        f"  var dl={{}};"
        f"  var t=document.getElementById('dl-title-input');"
        f"  if(t)dl.title=t.value;"
        f"  var p=document.getElementById('dl-price-input');"
        f"  if(p&&p.value!=='')dl.price=parseFloat(p.value)||null;"
        f"  var c=document.getElementById('dl-condition-select');"
        f"  if(c&&c.value)dl.condition_enum=c.value;"
        f"  var cd=document.getElementById('dl-cond-desc-input');"
        f"  if(cd)dl.condition_description=cd.value;"
        f"  var desc=document.getElementById('dl-desc-input');"
        f"  if(desc)dl.description=desc.value;"
        f"  var ship=document.getElementById('dl-ship-input');"
        f"  if(ship&&ship.value)dl.shipping_profile=ship.value;"
        f"  var ret=document.getElementById('dl-return-select');"
        f"  if(ret&&ret.value)dl.return_policy_id=ret.value;"
        f"  var scat=document.getElementById('dl-store-cat-select');"
        f"  if(scat&&scat.value){{"
        f"    dl.store_category_id=scat.value;"
        f"    var scopt=scat.selectedOptions&&scat.selectedOptions[0];"
        f'    if(scopt)dl.store_category_name=scopt.dataset.name||scopt.textContent.replace(/\\s*\\(\\d+\\)\\s*$/,"");'
        f"  }}"
        f"  var scat2=document.getElementById('dl-store-cat2-select');"
        f"  if(scat2)dl.secondary_store_category_id=scat2.value||null;"
        f"  var boSel=document.getElementById('dl-best-offer-enabled');"
        f"  if(boSel&&boSel.value!=='')dl.best_offer_enabled=(boSel.value==='true');"
        f"  var boAcc=document.getElementById('dl-best-offer-accept');"
        f"  if(boAcc&&boAcc.value!==''){{var boAccN=parseFloat(boAcc.value);dl.best_offer_auto_accept_price=isNaN(boAccN)?null:boAccN;}}"
        f"  var boDec=document.getElementById('dl-best-offer-decline');"
        f"  if(boDec&&boDec.value!==''){{var boDecN=parseFloat(boDec.value);dl.best_offer_auto_decline_price=isNaN(boDecN)?null:boDecN;}}"
        f"  var cat2id=document.getElementById('dl-cat2-id');"
        f"  if(cat2id)dl.secondary_category_id=cat2id.value||null;"
        # todo #1416 point 3: this form (#aspects-form) is Set B's own
        # editing surface (draft_listing.item_specifics) — it must PATCH
        # directly into draft_listing, exactly like every other Draft
        # Editor field above (title/price/condition/etc.), never into
        # item_attributes (Set A). The nested dl.item_specifics key is
        # unwrapped server-side by _apply_patch's draft_listing branch
        # through the sanctioned tgw.ebay.draft_specifics accessor.
        #
        # Todo #1461: this used to gate inclusion on `if(v)` — an operator
        # clearing a field's value produced v==='' which was silently
        # dropped from the payload entirely, so the backend's merge
        # (set_ebay_aspects) never even saw an attempted change and the old
        # value stuck forever ("delete Material, save, reverts every
        # time" — confirmed live 2026-07-16, and it affected every aspect
        # field uniformly since this loop is shared by all of them). Fix:
        # compare against each input's `data-initial` (the value it was
        # rendered with) and send the key whenever it actually changed,
        # including a change TO empty — matches every other field on this
        # form, which is always sent unconditionally if the element exists.
        f"  var aspInputs=document.querySelectorAll('#aspects-form [data-aspect]');"
        f"  var attrs={{}};"
        f"  aspInputs.forEach(function(el){{"
        f"    var k=el.dataset.aspect;var v=el.value.trim();"
        f"    var init=el.dataset.initial||'';"
        f"    if(v!==init)attrs[k]=v;"
        f"  }});"
        f"  if(Object.keys(attrs).length)dl.item_specifics=attrs;"
        f"  var patch={{draft_listing:dl}};"
        f"  var msg=document.getElementById('dl-save-msg');"
        # todo #1472 (Dave, 2026-07-16): "if the aspect is not in the list
        # of required or recommended aspects it gets a check box, default
        # checked, meaning keep... Unchecking means discard at save." A
        # custom/orphaned aspect's `.aspect-keep-cb` (rendered above and in
        # addCustomAspect()) is the discard signal — collected here so ONE
        # Save Draft click drives both the normal field save AND the
        # discard-to-Inventory-Record move, rather than the two disconnected
        # actions (main Save + a separate always-checked migration panel)
        # this replaces. Reuses #1471's sanctioned apply endpoint verbatim
        # (its own live re-detection + Set B removal / Set A write) — no new
        # merge path, spec point 6 discipline.
        f"  var discardKeys=[];"
        f"  document.querySelectorAll('.aspect-keep-cb:not(:checked)').forEach(function(cb){{discardKeys.push(cb.dataset.aspectKey);}});"
        f"  if(msg)msg.textContent='Saving…';"
        f"  fetch('/api/items/'+_SKU,{{"
        f"    method:'PATCH',"
        f"    headers:authHeaders({{'Content-Type':'application/json','X-TGW-Draft-Intent':intent||''}}),"
        f"    body:JSON.stringify({{fields:patch}})"
        f"  }}).then(function(r){{return r.json();}}).then(function(d){{"
        f"    if(!d.ok){{"
        f"      if(msg){{msg.textContent=' Error: '+(d.detail||'failed');msg.style.color='#c44';}}"
        f"      return;"
        f"    }}"
        f"    if(!discardKeys.length){{"
        f"      if(msg){{msg.textContent='✓ Saved';msg.style.color='#4a4';setTimeout(function(){{msg.textContent='';}},2000);}}"
        f"      if(typeof done==='function')done();"
        f"      return;"
        f"    }}"
        f"    if(msg)msg.textContent='Saved — discarding '+discardKeys.length+' unchecked aspect(s)…';"
        f"    fetch('/api/items/'+_SKU+'/category-aspect-migration/apply',{{"
        f"      method:'POST',"
        f"      headers:authHeaders({{'Content-Type':'application/json'}}),"
        f"      body:JSON.stringify({{keys:discardKeys}})"
        f"    }}).then(function(r2){{return r2.json();}}).then(function(d2){{"
        f"      if(msg){{"
        f"        msg.textContent=d2.ok?'✓ Saved (moved '+(d2.migrated?d2.migrated.length:0)+' to Inventory Record)':'Saved, but discard failed: '+(d2.detail||'error');"
        f"        msg.style.color=d2.ok?'#4a4':'#c44';"
        f"        if(d2.ok)setTimeout(function(){{msg.textContent='';}},2500);"
        f"      }}"
        f"      if(typeof done==='function')done();"
        f"    }}).catch(function(e2){{if(msg){{msg.textContent='Saved, but discard network error';msg.style.color='#c44';}}}});"
        f"  }}).catch(function(e){{if(msg){{msg.textContent='Network error';msg.style.color='#c44';}}}});"
        f"}}"
        # ── buildAspectRow / addCustomAspect / addFromInventory ──────────────
        # todo #1470: lets an operator deliberately add a seller-defined
        # custom aspect (a real eBay Inventory API capability, not just
        # leftover category-mismatch data). buildAspectRow() is the shared
        # row-builder (same data-aspect/data-initial="" contract every other
        # aspect input uses, so saveEbayDraft()'s collection loop and #1472's
        # aspect-keep-cb discard checkbox pick it up unchanged) — used by
        # both the manual "+ Add custom aspect" control and #1475's "+ Add
        # to listing" buttons on the Inventory Record specifics panel above
        # (Dave, 2026-07-16: "the initial item draft view after import
        # should show all filled fields... gives the operator all the data
        # we have to choose from").
        f"function buildAspectRow(name,val){{"
        f"  var esc=function(s){{return (s||'').replace(/\"/g,'&quot;');}};"
        f"  var row=document.createElement('div');"
        f"  row.className='frow';"
        f'  row.innerHTML=\'<span class="fn" style="font-size:.82em">\''
        f'    +\'<input type="checkbox" class="aspect-keep-cb" data-aspect-key="\'+esc(name)+\'" checked \''
        f"    +'title=\"Checked = keep on this eBay listing. Uncheck = discard at Save (moved to the Inventory Record as a superset, never deleted).\" '"
        f"    +'style=\"margin-right:4px;vertical-align:middle\">'+esc(name)"
        f"    +'<span style=\"font-size:.7em;background:#1a2a3a;color:#8ac;border-radius:3px;'"
        f'    +\'padding:1px 5px;margin-left:4px" title="A seller-defined custom aspect">CUSTOM ASPECT</span></span>\''
        f'    +\'<span class="fv"><input type="text" data-aspect="\'+esc(name)+\'" data-initial="" value="\'+esc(val)+\'"\''
        f"    +' style=\"background:#1a1a2a;color:#eee;border:1px solid #446;border-radius:3px;'"
        f"    +'padding:2px 5px;font-size:.85em;width:200px\"></span>';"
        f"  return row;"
        f"}}"
        f"function addCustomAspect(){{"
        f"  var nameEl=document.getElementById('new-aspect-name');"
        f"  var valEl=document.getElementById('new-aspect-value');"
        f"  var msgEl=document.getElementById('new-aspect-msg');"
        f"  var name=(nameEl&&nameEl.value||'').trim();"
        f"  var val=(valEl&&valEl.value||'').trim();"
        f"  if(msgEl)msgEl.textContent='';"
        f"  if(!name){{if(msgEl)msgEl.textContent='Name required';return;}}"
        f"  var form=document.getElementById('aspects-form');"
        f"  if(!form)return;"
        f"  var existing=form.querySelectorAll('[data-aspect]');"
        f"  for(var i=0;i<existing.length;i++){{"
        f"    if(existing[i].dataset.aspect===name){{"
        f"      if(msgEl)msgEl.textContent='Already exists — edit it above instead';"
        f"      return;"
        f"    }}"
        f"  }}"
        f"  form.appendChild(buildAspectRow(name,val));"
        f"  if(nameEl)nameEl.value='';"
        f"  if(valEl)valEl.value='';"
        f"}}"
        f"function addFromInventory(name,val,btn){{"
        f"  var form=document.getElementById('aspects-form');"
        f"  if(!form){{if(btn){{btn.disabled=true;btn.textContent='form not ready';}}return;}}"
        f"  var existing=form.querySelectorAll('[data-aspect]');"
        f"  for(var i=0;i<existing.length;i++){{"
        f"    if(existing[i].dataset.aspect===name){{"
        f"      if(!existing[i].value)existing[i].value=val;"
        f"      if(btn){{btn.disabled=true;btn.textContent='✓ In listing';}}"
        f"      return;"
        f"    }}"
        f"  }}"
        f"  form.appendChild(buildAspectRow(name,val));"
        f"  if(btn){{btn.disabled=true;btn.textContent='✓ Added — click Save Draft';}}"
        f"}}"
        f"function toggleInventoryLock(key,currentlyLocked,btn){{"
        f"  if(btn)btn.disabled=true;"
        f"  fetch('/api/items/'+_SKU+'/inventory-lock',{{method:'POST',"
        f"    headers:authHeaders({{'Content-Type':'application/json'}}),"
        f"    body:JSON.stringify({{key:key,locked:!currentlyLocked}})}}"
        f"  ).then(function(r){{return r.json();}}).then(function(d){{"
        f"    if(d.ok)location.reload();else{{if(btn){{btn.disabled=false;}}alert('Lock toggle failed: '+(d.detail||'error'));}}"
        f"  }}).catch(function(e){{if(btn)btn.disabled=false;alert('Network error: '+e);}});"
        f"}}"
        # ── saveAndReprice ────────────────────────────────────────────────────
        f"function saveAndReprice(){{"
        f'  var st=document.getElementById("search-terms-input");'
        f'  var msg=document.getElementById("reprice-msg");'
        f'  var terms=st?st.value.trim():"";'
        f'  if(msg)msg.textContent="Saving…";'
        f'  fetch("/api/items/"+_SKU,{{'
        f'    method:"PATCH",'
        f'    headers:authHeaders({{"Content-Type":"application/json"}}),'
        f"    body:JSON.stringify({{fields:{{search_terms:terms}}}})"
        f"  }}).then(function(r){{return r.json();}}).then(function(d){{"
        f'    if(!d.ok){{if(msg){{msg.textContent="Save failed";msg.style.color="#c44";}}return;}}'
        f'    if(msg)msg.textContent="Saved — pricing…";'
        f'    fetch("/api/items/"+_SKU+"/action",{{'
        f'      method:"POST",'
        f'      headers:authHeaders({{"Content-Type":"application/json"}}),'
        f'      body:JSON.stringify({{action:"ebay_price"}})'
        f"    }}).then(function(r){{return r.json();}}).then(function(d){{"
        f'      if(msg){{msg.textContent=d.ok?"✓ Re-price queued":"Price queue failed";'
        f'      msg.style.color=d.ok?"#4a4":"#c44";}}'
        f"      if(d.ok)setTimeout(function(){{location.reload();}},3500);"
        f'    }}).catch(function(e){{if(msg){{msg.textContent="Network error";msg.style.color="#c44";}}}});'
        f'  }}).catch(function(e){{if(msg){{msg.textContent="Network error";msg.style.color="#c44";}}}});'
        f"}}"
        # ── category picker (search / ID entry / tree browse, shared) ─────────
        + _CATEGORY_PICKER_JS
        +
        # ── category context IIFE (conditions + aspects + store cat + hints) ──────
        _CATEGORY_CONTEXT_IIFE
        + "</script>\n"
    )

    # Offer count badge: JS fetches /api/offers (cached), filters to this SKU.
    offer_script = ""
    if listing_id and api_key:
        import json as _json

        _ak_json = _json.dumps(api_key)
        _sku_json = _json.dumps(sku)
        offer_script = (
            f"<script>\n"
            f"window.TGW_API_KEY={_ak_json};\n"
            f"(function(){{\n"
            f"  var _sku={_sku_json};\n"
            f"  fetch('/api/offers',{{headers:authHeaders()}})\n"
            f"    .then(function(r){{return r.json();}})\n"
            f"    .then(function(d){{\n"
            f"      if(!d||!d.ok||!d.offers)return;\n"
            f"      var n=d.offers.filter(function(o){{return o.sku===_sku;}}).length;\n"
            f"      if(!n)return;\n"
            f"      var wrap=document.getElementById('offer-badge-wrap');\n"
            f"      if(!wrap)return;\n"
            f"      var a=document.createElement('a');\n"
            f"      a.className='offer-badge';\n"
            f"      a.href='/form/offers?sku='+encodeURIComponent(_sku);\n"
            f"      a.textContent=n+(n===1?' pending offer':' pending offers');\n"
            f"      wrap.appendChild(a);\n"
            f"    }}).catch(function(){{}});\n"
            f"}})();\n"
            f"</script>\n"
        )

    # review_block banner — shown when an item is parked in needs_review (item 5)
    _rb = item.get("review_block") or {}
    review_block_html = ""
    if _rb and not _rb.get("ready"):
        _rb_stage = h(str(_rb.get("stage") or ""))
        _rb_code = h(str(_rb.get("reason_code") or ""))
        _rb_err = h(str(_rb.get("error") or ""))
        _rb_sugg = h(str(_rb.get("suggestion") or ""))
        _rb_at = h(_local_ts(_rb.get("flagged_at")))
        _rb_detail = f'<div style="font-size:.82em;color:#f99;margin-top:4px">{_rb_err}</div>' if _rb_err else ""
        _rb_sugg_html = f'<div style="font-size:.82em;color:#fb7;margin-top:4px">Suggestion: {_rb_sugg}</div>' if _rb_sugg else ""
        review_block_html = (
            f'<div style="background:#3a1010;border:1.5px solid #8a2020;border-radius:8px;'
            f'padding:10px 14px;margin:10px 0;color:#f77;font-size:.9em">'
            f"<strong>⛔ Blocked in review</strong>"
            f" — stage: <code>{_rb_stage}</code>"
            f", reason: <code>{_rb_code}</code>"
            f"{(' · flagged ' + _rb_at) if _rb_at else ''}"
            f"{_rb_detail}"
            f"{_rb_sugg_html}"
            f'<div style="font-size:.79em;color:#884;margin-top:6px">'
            f'<a href="/form/needs-review" style="color:#aaa">← All blocked items</a>'
            f" · Use <code>tgw migrate-unblock {h(sku)}</code> or click Mark Ready in the blocked queue after resolving.</div>"
            f"</div>"
        )

    return (
        f"<!DOCTYPE html>\n<html lang='en'>\n<head>\n"
        f"<meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{h(sku)} — TGW</title>" + _STATIC_HEAD + f"<style>{_ITEMS_EXTRA_CSS}</style>"
        f"</head>\n<body>\n"
        f'<a class="back" href="/form/items">← Inventory</a>\n'
        # Fast jump straight back to a filtered inventory view (Dave,
        # 2026-07-17): the browse page's status chips now persist their
        # filter into the URL, so these can just be plain links to it —
        # no JS/fetch needed here, matches the same filter values/labels.
        f'<div class="chips" style="margin:4px 0 8px">'
        f'<a class="chip" style="color:#ccc;text-decoration:none" href="/form/items">All</a>'
        f'<a class="chip" style="color:#ccc;text-decoration:none" href="/form/items?status=__eligible__">Eligible</a>'
        f'<a class="chip" style="color:#ccc;text-decoration:none" href="/form/items?status=In+Stock">In Stock</a>'
        f'<a class="chip" style="color:#ccc;text-decoration:none" href="/form/items?status=Listed">Listed</a>'
        f'<a class="chip" style="color:#ccc;text-decoration:none" href="/form/items?status=Staged">Staged</a>'
        f'<a class="chip" style="color:#ccc;text-decoration:none" href="/form/items?status=Sold">Sold</a>'
        f"</div>"
        f'<div class="sku-hdr">'
        f'<span class="slabel">{h(sku)}</span>'
        f'<span class="stitle">{h(title)}</span>'
        f"</div>\n"
        f"{operator_object_html}"
        f"{workflow_card_html}"
        f"{review_block_html}"
        f'<div class="detail-layout">'
        f'<div class="dleft">{gallery_html}{_left_log_html}</div>'
        f"{fields_html[:-6]}"
        f"{actions_html}"
        f"</div>"
        f"</div>\n" + _STATIC_FOOT + offer_script + "</body></html>"
    )


@app.get("/form/items")
def items_browse_form():
    """Inventory browse — card grid with search/filter. No Bearer auth (network trust)."""
    from fastapi.responses import HTMLResponse

    html = _BROWSE_HTML.format(
        static_head=_STATIC_HEAD,
        static_foot=_STATIC_FOOT,
        extra_css=_ITEMS_EXTRA_CSS,
        api_key="",
    )
    return HTMLResponse(html, headers={"Cache-Control": "no-store, no-cache"})


@app.get("/form/operator/items/{sku}")
def operator_item_form(sku: str):
    """Thin item client: render only the current published API object."""
    from fastapi.responses import HTMLResponse

    if ".." in sku or not sku:
        return HTMLResponse("<h2>invalid sku</h2>", status_code=400)
    client = Path(__file__).with_name("static").joinpath("operator_item.html")
    return HTMLResponse(
        client.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, no-cache"},
    )


@app.get("/form/items/{sku}")
def item_detail_form(sku: str):
    """Retired direct-action detail URL; canonical UI is the thin adapter."""
    from fastapi.responses import HTMLResponse

    if ".." in sku:
        return HTMLResponse("<h2>invalid sku</h2>", status_code=400)
    return RedirectResponse(
        url=f"/form/operator/items/{urllib.parse.quote(sku, safe='')}",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        headers={"Cache-Control": "no-store, no-cache"},
    )


def _retired_item_detail_form_source(sku: str):  # pragma: no cover
    """Unrouted migration reference for fields not yet represented by W13."""
    # Historical server-rendered implementation retained temporarily as dead
    # source for field-edit migration archaeology. It has no registered route
    # or reachable branch after W13 and cannot issue commands.
    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        return HTMLResponse(f"<h2>SKU not found: {sku}</h2>", status_code=404)

    item = load_item_doc(json_path)

    sku_dir = json_path.parent
    images: List[str] = [p.name for p in _ordered_photos(item, sku_dir)]
    videos: List[str] = [p.name for p in sorted(sku_dir.iterdir()) if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}]

    jobs: List[Dict[str, Any]] = []
    workflow_card: Dict[str, Any] | None = None
    operator_object: Dict[str, Any] | None = None
    try:
        attempts = _workflow_attempt_rows(sku)
        reconciled_effect_ids = _workflow_reconciled_provider_effect_ids(attempts)
        jobs = attempts[:10]
        for j in jobs:
            for k in ("created_at", "finished_at"):
                if j.get(k) is not None and hasattr(j[k], "isoformat"):
                    j[k] = j[k].isoformat()
        from .workflow.action_cards import build_item_action_card

        workflow_card = build_item_action_card(
            json_path,
            attempts,
            provider_identity=_workflow_provider_identity(),
            reconciled_provider_effect_ids=reconciled_effect_ids,
        )
        reconciled_jobs = {str(attempt["job_id"]) for attempt in workflow_card.get("attempts", []) if attempt.get("provider_effect_reconciled") and attempt.get("job_id")}
        for job in jobs:
            job["provider_effect_reconciled"] = str(job.get("job_id")) in reconciled_jobs
        from .operator_objects import build_item_operator_object

        draft = item.get("draft_listing") if isinstance(item.get("draft_listing"), dict) else {}
        category_id = str(draft.get("category_id") or item.get("ebay_category_id") or "")
        current_condition = str(draft.get("condition_enum") or draft.get("condition") or "")
        category_context = ebay_category_context(category_id, current_condition=current_condition) if category_id and category_id != "99" else {}
        operator_object = build_item_operator_object(
            item=item,
            workflow_card=workflow_card,
            category_context=category_context,
        )
    except Exception as exc:
        log.warning("queue job fetch failed for %s: %s", sku, exc)

    return HTMLResponse(
        _render_item_detail_html(
            sku,
            item,
            images,
            videos,
            jobs,
            "",
            workflow_card=workflow_card,
            operator_object=operator_object,
        ),
        headers={"Cache-Control": "no-store, no-cache"},
    )


# ---------------------------------------------------------------------------
# GET /api/dashboard — home dashboard summary (PP-EDITOR-001 Phase 3b)
# ---------------------------------------------------------------------------


@app.get("/api/dashboard", dependencies=[AUTH])
def dashboard() -> Dict[str, Any]:
    """Single call returning all action-card counts for the home dashboard.

    Fields:
      needs_review      items with ebay_draft done (draft_listing) but no offer_id
      pending_offers    GetBestOffers Pending count; None when eBay API is unavailable
      needs_photos      items with no image in the catalog
      has_revision_draft items with a pending revision_draft
      dead_letter_count dead_letter jobs in Postgres
      ready_count       items in the ready pool (offer_id + UNPUBLISHED + ready_at)
      worker_health     {up, total} from systemctl
    """
    from .queue import WORKER_QUEUES

    result: Dict[str, Any] = {"ok": True}

    # --- SQLite-backed counts ---
    db_path = _cfg.get("sqlite_catalog_path")
    if db_path and Path(db_path).exists():
        try:
            con = _sqlite_conn()
            try:
                cols = {row[1] for row in con.execute("PRAGMA table_info(catalog)").fetchall()}
                if "data" in cols:
                    row = con.execute(
                        """
                        SELECT
                          COUNT(CASE WHEN json_extract(data,'$.draft_listing') IS NOT NULL
                                      AND (json_extract(data,'$.ebay_offer.offer_id') IS NULL
                                           OR json_extract(data,'$.ebay_offer.offer_id') = '')
                                     THEN 1 END),
                          COUNT(CASE WHEN image IS NULL OR image = '' THEN 1 END),
                          COUNT(CASE WHEN json_extract(data,'$.revision_draft') IS NOT NULL
                                     THEN 1 END),
                          COUNT(CASE WHEN json_extract(data,'$.ebay_offer.ready_at') IS NOT NULL
                                      AND json_extract(data,'$.ebay_offer.offer_id') IS NOT NULL
                                      AND json_extract(data,'$.ebay_offer.status') = 'UNPUBLISHED'
                                     THEN 1 END),
                          COUNT(CASE WHEN json_extract(data,'$.review_block') IS NOT NULL
                                      AND json_extract(data,'$.review_block.ready') IS NOT 1
                                     THEN 1 END)
                        FROM catalog
                        """
                    ).fetchone()
                    result["needs_review"] = row[0]
                    result["needs_photos"] = row[1]
                    result["has_revision_draft"] = row[2]
                    result["ready_count"] = row[3]
                    result["blocked_count"] = row[4]
                else:
                    row = con.execute("SELECT COUNT(*) FROM catalog WHERE image IS NULL OR image = ''").fetchone()
                    result["needs_review"] = None
                    result["needs_photos"] = row[0]
                    result["has_revision_draft"] = None
                    result["ready_count"] = None
            finally:
                con.close()
        except Exception as exc:
            log.warning("dashboard: SQLite query failed: %s", exc)
            result.update(needs_review=None, needs_photos=None, has_revision_draft=None, ready_count=None, blocked_count=None)
    else:
        result.update(needs_review=None, needs_photos=None, has_revision_draft=None, ready_count=None, blocked_count=None)

    # --- PostgreSQL: dead_letter_count ---
    dead_letter_count = 0
    try:
        with psycopg2.connect(_cfg["postgres_dsn"]) as con:
            with con.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM queue_jobs WHERE state = 'dead_letter'")
                dead_letter_count = cur.fetchone()[0]
    except Exception as exc:
        log.warning("dashboard: dead_letter query failed: %s", exc)
    result["dead_letter_count"] = dead_letter_count

    # --- eBay pending_offers (cached, null on failure) ---
    global _pending_offers_cache, _pending_offers_cache_at
    if _pending_offers_cache is not None and time.time() - _pending_offers_cache_at < _PENDING_OFFERS_TTL:
        result["pending_offers"] = _pending_offers_cache
    else:
        try:
            from .apis.ebay.trading import get_best_offers

            offers = list(get_best_offers(_cfg, status="Pending"))
            _pending_offers_cache = len(offers)
            _pending_offers_cache_at = time.time()
            result["pending_offers"] = _pending_offers_cache
        except Exception as exc:
            _raw = str(exc)
            if "429" in _raw or "21919188" in _raw or "rate" in _raw.lower():
                log.warning("dashboard: GetBestOffers rate limited — suppressing: %s", exc)
                _pending_offers_cache = 0
            else:
                log.warning("dashboard: GetBestOffers failed: %s", exc)
            _pending_offers_cache_at = time.time()
            result["pending_offers"] = _pending_offers_cache  # stale or None

    # --- Worker health via systemctl ---
    total = len(WORKER_QUEUES)
    try:
        units = [f"tgw-worker@{q}.service" for q in WORKER_QUEUES]
        r = subprocess.run(
            ["systemctl", "is-active", *units],
            capture_output=True,
            text=True,
            timeout=5,
        )
        up = sum(1 for line in r.stdout.splitlines() if line.strip() == "active")
    except Exception as exc:
        log.warning("dashboard: systemctl query failed: %s", exc)
        up = -1
    result["worker_health"] = {"up": up, "total": total}

    return result


# ---------------------------------------------------------------------------
# GET /api/activity — recent queue job completions (activity feed)
# ---------------------------------------------------------------------------


@app.get("/api/activity", dependencies=[AUTH])
def activity(limit: int = 15) -> Dict[str, Any]:
    """Last N queue jobs with a finished_at timestamp, newest first."""
    n = min(max(limit, 1), 50)
    try:
        with psycopg2.connect(_cfg["postgres_dsn"]) as con:
            with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT job_id, queue_name, state,
                           payload_json->>'sku' AS sku,
                           finished_at, error_detail
                      FROM queue_jobs
                     WHERE finished_at IS NOT NULL
                     ORDER BY finished_at DESC
                     LIMIT %s
                    """,
                    (n,),
                )
                jobs = [dict(r) for r in cur.fetchall()]
                for j in jobs:
                    fa = j.get("finished_at")
                    if fa is not None and hasattr(fa, "isoformat"):
                        j["finished_at"] = fa.isoformat()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"postgres error: {exc}")
    return {"ok": True, "count": len(jobs), "jobs": jobs}


# ---------------------------------------------------------------------------
# POST /api/pm/chat — PM chat (PP-EDITOR-001 Phase 3d)
# POST /api/pm/action — execute a PM-proposed action
# ---------------------------------------------------------------------------

_PM_SYSTEM_PROMPT = """\
You are TGW-PM, the operations assistant for Trader Grim's Warehouse — a resale \
eBay business run by Dave (DaveBuko-Webkulap). TGW is a Python-based inventory \
and eBay automation platform.

You have access to live system status below. Answer factually from this data. \
If something isn't in the provided context, say so rather than guessing.

Keep responses under 200 words. Plain text only — no markdown headers.

When appropriate, end your response with an ACTIONS block (required on every reply):

ACTIONS: [{"type": "add_todo", "agent": "claude", "body": "Task description", "priority": 50}]
ACTIONS: [{"type": "add_suggestion", "text": "text of suggestion"}]
ACTIONS: [{"type": "none"}]

Valid agents: claude, gemini, sokoban (database tasks), admin, operator, tigwa.
Priority: 10 (urgent) to 90 (low). Default 50.
Always end with ACTIONS — use none when no action is warranted.\
"""


def _build_pm_context() -> str:
    """Gather live system stats and return a compact text summary for the PM model."""
    lines: List[str] = []

    # Open todos by agent
    try:
        from . import todo as _todo

        todos = _todo.todo_list()
        by_agent: Dict[str, int] = {}
        for t in todos:
            a = t.get("agent") or "unknown"
            by_agent[a] = by_agent.get(a, 0) + 1
        todo_str = ", ".join(f"{a}:{n}" for a, n in sorted(by_agent.items()))
        lines.append(f"Open todos: {len(todos)} total ({todo_str or 'none'})")
    except Exception as exc:
        lines.append(f"Open todos: unavailable ({exc})")

    # Queue depths and dead letters
    try:
        with psycopg2.connect(_cfg["postgres_dsn"]) as con:
            with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT queue_name, state, COUNT(*) AS n FROM queue_jobs GROUP BY queue_name, state ORDER BY queue_name, state")
                qrows = [dict(r) for r in cur.fetchall()]
        active = {r["queue_name"]: r["n"] for r in qrows if r["state"] in ("queued", "claimed")}
        dead = sum(r["n"] for r in qrows if r["state"] == "dead_letter")
        lines.append("Active queues: " + (", ".join(f"{k}={v}" for k, v in active.items()) or "all idle"))
        lines.append(f"Dead-letter jobs: {dead}" + (" — NEEDS ATTENTION" if dead else ""))
    except Exception as exc:
        lines.append(f"Queue/dead-letter: unavailable ({exc})")

    # Inventory summary from SQLite catalog
    try:
        db_path = _cfg.get("sqlite_catalog_path")
        if db_path and Path(db_path).exists():
            con = _sqlite_conn()
            try:
                row = con.execute(
                    "SELECT COUNT(*), COUNT(CASE WHEN LOWER(status) IN ('published','live','listed') THEN 1 END), COUNT(CASE WHEN LOWER(status) = 'staged' THEN 1 END) FROM catalog"
                ).fetchone()
                if row:
                    lines.append(f"Inventory: {row[0]} total items, {row[1]} live on eBay, {row[2]} staged")
            finally:
                con.close()
    except Exception as exc:
        lines.append(f"Inventory count: unavailable ({exc})")

    # Pending offers (cached from last dashboard poll)
    if _pending_offers_cache is not None:
        lines.append(f"Pending eBay best offers: {_pending_offers_cache}")

    return "\n".join(lines)


@app.post("/api/pm/chat", dependencies=[AUTH])
def pm_chat(body: PMChatBody) -> Dict[str, Any]:
    """Call the PM chat model with live context and return {message, actions}."""
    from .apis.llm import call_model, get_task_model

    context = _build_pm_context()
    system = _PM_SYSTEM_PROMPT + f"\n\nLIVE SYSTEM STATUS:\n{context}"

    msg_list: List[Dict[str, str]] = [{"role": "system", "content": system}]
    for h in body.history[-8:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            msg_list.append({"role": h["role"], "content": h["content"]})
    msg_list.append({"role": "user", "content": body.message})

    provider, model = get_task_model(_cfg, "pm_chat")
    if provider not in ("openrouter", "anthropic_direct"):
        raise HTTPException(status_code=503, detail=f"pm_chat does not support provider {provider!r}")

    try:
        raw = call_model("pm_chat", "", "", _cfg, provider=provider, model=model, messages=msg_list)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"PM model unavailable: {exc}")

    # Split on the LAST ACTIONS: marker so incidental use of the word in the
    # body doesn't cause the real action block to be discarded.
    msg_text = raw
    actions: List[Dict[str, Any]] = [{"type": "none"}]
    if "ACTIONS:" in raw:
        head, tail = raw.rsplit("ACTIONS:", 1)
        msg_text = head.strip()
        try:
            actions = json.loads(tail.strip())
            if not isinstance(actions, list):
                actions = [{"type": "none"}]
        except Exception:
            actions = [{"type": "none"}]

    return {"ok": True, "message": msg_text, "actions": actions}


@app.post("/api/pm/action", dependencies=[AUTH])
def pm_action(body: PMActionBody) -> Dict[str, Any]:
    """Execute a PM-proposed action confirmed by the operator."""
    if body.type == "add_todo":
        if not body.agent or not body.body:
            raise HTTPException(status_code=400, detail="add_todo requires agent and body")
        from . import todo as _todo

        result = _todo.todo_add(
            agent=body.agent,
            body=body.body,
            priority=body.priority,
            source="pm_chat",
        )
        return {"ok": True, "message": f"Todo #{result['id']} added for {body.agent}", **result}

    if body.type == "add_suggestion":
        if not body.text:
            raise HTTPException(status_code=400, detail="add_suggestion requires text")
        from .api import cmd_suggest

        result = cmd_suggest(_cfg, body.text)
        return {"ok": True, "message": f"Suggestion added: {result.get('written', '')}", **result}

    raise HTTPException(status_code=400, detail=f"unknown action type: {body.type!r}")


# ---------------------------------------------------------------------------
# GET /api/offers — pending Best Offers with item context (PP-EDITOR-001 3g)
# POST /api/offers/{offer_id}/respond — accept / counter / decline
# ---------------------------------------------------------------------------


def _offer_location(sku: str) -> str:
    """Return location from the SQLite catalog for a given SKU, or ''."""
    if not sku:
        return ""
    try:
        with _sqlite_conn() as con:
            row = con.execute("SELECT location FROM catalog WHERE sku = ?", (sku,)).fetchone()
        return (row["location"] or "") if row else ""
    except Exception:
        return ""


@app.get("/api/offers", dependencies=[AUTH])
def get_offers() -> Dict[str, Any]:
    """Return pending Best Offers enriched with location and pct_of_ask."""
    from .offers import cmd_offers_list

    result = cmd_offers_list(_cfg, pending_only=True)
    if not result.get("ok"):
        return result

    for offer in result["offers"]:
        offer["location"] = _offer_location(offer.get("sku", ""))
        ask = offer.get("listing_price")
        bid = offer.get("offer_price")
        offer["pct_of_ask"] = round(bid / ask * 100, 1) if ask and bid else None

    return result


@app.post("/api/offers/{offer_id}/respond", dependencies=[AUTH])
def respond_offer(offer_id: str, body: OfferRespondBody) -> Dict[str, Any]:
    """Accept, counter, or decline a Best Offer."""
    from .offers import cmd_offers_respond

    return cmd_offers_respond(
        _cfg,
        offer_id=offer_id,
        listing_id=body.listing_id,
        action=body.action,
        counter_price=body.counter_price,
        dry_run=body.dry_run,
        by=body.by,
    )


@app.get("/api/offers/limits", dependencies=[AUTH])
def get_offers_limits() -> Dict[str, Any]:
    """Return GetBestOffers API call budget from GetAPIAccessRules."""
    from .apis.ebay.trading import get_api_access_rules

    try:
        rules = get_api_access_rules(_cfg)
    except Exception:
        return {"ok": True, "limits": None}

    if not rules:
        return {"ok": True, "limits": None}

    r = rules[0]
    return {
        "ok": True,
        "limits": {
            "daily_limit": r["daily_limit"],
            "daily_used": r["daily_used"],
            "hourly_limit": r["hourly_limit"],
            "hourly_used": r["hourly_used"],
        },
    }


# ---------------------------------------------------------------------------
# GET /form/offers — Best Offers management UI (PP-EDITOR-001 Phase 3g)
# ---------------------------------------------------------------------------

_OFFERS_EXTRA_CSS = (
    ".offer-card{background:#1a1a1a;border:1px solid #333;border-radius:10px;"
    "  padding:14px;margin-bottom:14px;display:grid;"
    "  grid-template-columns:72px 1fr;gap:12px;align-items:start}"
    "@media(min-width:600px){.offer-card{grid-template-columns:80px 1fr}}"
    ".offer-thumb{width:72px;height:72px;object-fit:cover;border-radius:6px;"
    "  border:1px solid #333;background:#111;flex-shrink:0}"
    ".offer-thumb-placeholder{width:72px;height:72px;border-radius:6px;"
    "  border:1px solid #333;background:#111;display:flex;align-items:center;"
    "  justify-content:center;color:#444;font-size:1.4em}"
    ".offer-body{min-width:0}"
    ".offer-title{font-size:.92em;font-weight:600;color:#ddd;margin-bottom:5px;"
    "  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
    ".offer-meta{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:8px;align-items:center}"
    ".offer-price{font-size:.85em;color:#aaa}"
    ".offer-price strong{color:#eee}"
    ".offer-pct{font-size:1.5em;font-weight:bold;min-width:56px;text-align:right;"
    "  line-height:1}"
    ".pct-high{color:#7f7}"
    ".pct-mid{color:#fb7}"
    ".pct-low{color:#f77}"
    ".offer-loc{font-size:.76em;color:#888;background:#1e1e1e;border:1px solid #2a2a2a;"
    "  border-radius:4px;padding:2px 6px}"
    ".offer-loc.warn{color:#fb7;border-color:#4a3a00;background:#2a1e00}"
    ".offer-buyer{font-size:.76em;color:#777}"
    ".offer-actions{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;align-items:center}"
    ".btn-accept{padding:8px 16px;background:#1a4a1a;color:#7f7;border:1px solid #3a8a3a;"
    "  border-radius:6px;cursor:pointer;font-size:.85em;font-weight:600}"
    ".btn-accept:hover{background:#1e5a1e}"
    ".btn-decline{padding:8px 16px;background:#3a1a1a;color:#f77;border:1px solid #7a3a3a;"
    "  border-radius:6px;cursor:pointer;font-size:.85em;font-weight:600}"
    ".btn-decline:hover{background:#4a1a1a}"
    ".btn-counter{padding:8px 16px;background:#1a2a4a;color:#7af;border:1px solid #2a5a8a;"
    "  border-radius:6px;cursor:pointer;font-size:.85em;font-weight:600}"
    ".btn-counter:hover{background:#1a3a5a}"
    ".counter-wrap{display:flex;gap:6px;align-items:center}"
    ".counter-input{width:90px;flex-shrink:0;padding:8px;margin:0;font-size:.88em}"
    ".offer-expiry{font-size:.73em;color:#555;margin-top:4px}"
    ".dry-bar{background:#1a2a3a;border:1px solid #2a4a6a;border-radius:8px;"
    "  padding:10px 14px;margin-bottom:14px;display:flex;align-items:center;gap:12px;"
    "  font-size:.87em;color:#aaa}"
    ".dry-badge{background:#2a4a6a;color:#7af;border-radius:4px;padding:2px 8px;"
    "  font-size:.78em;font-weight:600;text-transform:uppercase;flex-shrink:0}"
    ".dry-badge.live{background:#2a4a1a;color:#7f7}"
    ".toggle-wrap{display:flex;align-items:center;gap:8px;margin-left:auto}"
    ".toggle{position:relative;display:inline-block;width:40px;height:22px}"
    ".toggle input{opacity:0;width:0;height:0}"
    ".slider{position:absolute;cursor:pointer;top:0;left:0;right:0;bottom:0;"
    "  background:#2a2a2a;border-radius:22px;transition:.25s}"
    ".slider:before{position:absolute;content:'';height:16px;width:16px;left:3px;bottom:3px;"
    "  background:#888;border-radius:50%;transition:.25s}"
    ".toggle input:checked+.slider{background:#1a4a1a}"
    ".toggle input:checked+.slider:before{transform:translateX(18px);background:#7f7}"
    ".empty-state{padding:32px;text-align:center;color:#555;font-size:.95em}"
    ".resp-flash{padding:6px 10px;border-radius:5px;font-size:.82em;margin-top:6px;"
    "  display:none}"
    ".resp-flash.ok{background:#1a3a1a;color:#7f7;display:block}"
    ".resp-flash.err{background:#3a1a1a;color:#f77;display:block}"
    ".reload-btn{background:none;border:none;color:#4a8ade;cursor:pointer;"
    "  font-size:.82em;padding:0;text-decoration:underline;margin-left:8px}"
    ".rate-limit-bar{background:#111;border:1px solid #2a2a2a;border-radius:6px;"
    "  padding:6px 12px;font-size:.78em;color:#666;margin-bottom:10px;display:none}"
    ".rate-limit-bar.loaded{display:block}"
)

_OFFERS_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TGW — Best Offers</title>
{static_head}
<style>{offers_css}</style>
</head>
<body>
<h2>Best Offers <span id="offer-count" style="font-size:.65em;color:#666;font-weight:normal"></span>
  <button class="reload-btn" onclick="load()">&#8635; Refresh</button>
</h2>

<div class="rate-limit-bar" id="rl-bar"></div>

<div id="sku-filter-bar" style="display:none;background:#1a2a1a;border:1px solid #2a4a2a;
  border-radius:6px;padding:8px 12px;margin-bottom:10px;font-size:.85em;color:#7f7">
  Filtered to SKU: <strong id="sku-filter-val"></strong>
  <a href="/form/offers" style="color:#888;margin-left:10px;font-size:.85em">Clear filter</a>
</div>

<div class="dry-bar">
  <span class="dry-badge" id="dry-badge">DRY RUN</span>
  <span id="dry-label">Actions are previewed only — no eBay API calls.</span>
  <div class="toggle-wrap">
    <span style="font-size:.78em">Go Live</span>
    <label class="toggle" title="Enable live eBay submission">
      <input type="checkbox" id="live-toggle" onchange="onLiveToggle()">
      <span class="slider"></span>
    </label>
  </div>
</div>

<div id="offers-list"><span style="color:#555">Loading…</span></div>

{static_foot}
<script>
window.TGW_API_KEY = {api_key_json};
var DRY = true;

function onLiveToggle() {{
  DRY = !document.getElementById('live-toggle').checked;
  var badge = document.getElementById('dry-badge');
  var label = document.getElementById('dry-label');
  if (DRY) {{
    badge.textContent = 'DRY RUN'; badge.className = 'dry-badge';
    label.textContent = 'Actions are previewed only — no eBay API calls.';
  }} else {{
    badge.textContent = 'LIVE'; badge.className = 'dry-badge live';
    label.textContent = 'Actions will be submitted to eBay immediately.';
  }}
}}

function pctClass(pct) {{
  if (pct === null || pct === undefined) return '';
  if (pct >= 85) return 'pct-high';
  if (pct >= 70) return 'pct-mid';
  return 'pct-low';
}}

function formatPct(pct) {{
  if (pct === null || pct === undefined) return '?';
  return pct.toFixed(1) + '%';
}}

function fmtPrice(p) {{
  if (p === null || p === undefined) return '—';
  return '$' + Number(p).toFixed(2);
}}

function fmtExpiry(s) {{
  if (!s) return '';
  try {{
    var d = new Date(s);
    return 'Expires ' + d.toLocaleDateString(undefined, {{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}});
  }} catch(e) {{ return s; }}
}}

function flashId(offerId) {{ return 'flash-' + offerId.replace(/[^a-z0-9]/gi,'_'); }}

function renderOffers(data) {{
  var el = document.getElementById('offers-list');
  var countEl = document.getElementById('offer-count');
  if (!data || !data.ok) {{
    el.innerHTML = '<div class="resp-flash err" style="display:block">Failed to load offers: ' +
      escapeHtml((data && data.error) || 'unknown error') + '</div>';
    countEl.textContent = '';
    return;
  }}
  if (!data.offers || data.offers.length === 0) {{
    el.innerHTML = '<div class="empty-state">No pending Best Offers right now.</div>';
    countEl.textContent = '';
    return;
  }}
  countEl.textContent = '(' + data.offers.length + ' pending)';
  var html = '';
  data.offers.forEach(function(o) {{
    var pct = o.pct_of_ask;
    var loc = o.location || '';
    var locCls = loc ? '' : ' warn';
    var locLabel = loc || 'location unknown';
    var thumbUrl = o.sku ? '/thumb/' + encodeURIComponent(o.sku) : '';
    var thumbHtml = thumbUrl
      ? '<img class="offer-thumb" src="' + thumbUrl + '" onerror="this.style.display=\\'none\\';this.nextElementSibling.style.display=\\'flex\\'" loading="lazy">' +
        '<div class="offer-thumb-placeholder" style="display:none">&#128247;</div>'
      : '<div class="offer-thumb-placeholder">&#128247;</div>';
    html += '<div class="offer-card">';
    html += '<div style="display:flex;flex-direction:column;align-items:center;gap:6px">' +
            thumbHtml + '</div>';
    html += '<div class="offer-body">';
    html += '<div class="offer-title">' + escapeHtml(o.title || o.sku || 'Unknown item') + '</div>';
    html += '<div class="offer-meta">';
    html += '<span class="offer-price">Ask: <strong>' + fmtPrice(o.listing_price) + '</strong></span>';
    html += '<span class="offer-price">Offer: <strong>' + fmtPrice(o.offer_price) + '</strong></span>';
    html += '<span class="offer-pct ' + pctClass(pct) + '">' + formatPct(pct) + '</span>';
    html += '<span class="offer-loc' + locCls + '">' + escapeHtml(locLabel) + '</span>';
    if (o.buyer) html += '<span class="offer-buyer">from ' + escapeHtml(o.buyer) + '</span>';
    html += '</div>';
    if (o.expiry) html += '<div class="offer-expiry">' + escapeHtml(fmtExpiry(o.expiry)) + '</div>';
    html += '<div class="offer-actions">';
    html += '<button class="btn-accept" onclick="respond(' +
      JSON.stringify(o.offer_id) + ',' + JSON.stringify(o.listing_id) + ',\\'Accept\\',null)">Accept</button>';
    html += '<div class="counter-wrap">' +
      '<input class="counter-input" id="cp-' + escapeHtml(o.offer_id) + '" type="number" ' +
      'min="0.01" step="0.01" placeholder="$0.00">' +
      '<button class="btn-counter" onclick="respondCounter(' +
      JSON.stringify(o.offer_id) + ',' + JSON.stringify(o.listing_id) + ')">Counter</button>' +
      '</div>';
    html += '<button class="btn-decline" onclick="respond(' +
      JSON.stringify(o.offer_id) + ',' + JSON.stringify(o.listing_id) + ',\\'Decline\\',null)">Decline</button>';
    html += '</div>';
    html += '<div class="resp-flash" id="' + flashId(o.offer_id) + '"></div>';
    html += '</div></div>';
  }});
  el.innerHTML = html;
}}

var _skuFilter = new URLSearchParams(window.location.search).get('sku') || '';
(function() {{
  if (!_skuFilter) return;
  var bar = document.getElementById('sku-filter-bar');
  var val = document.getElementById('sku-filter-val');
  if (bar) bar.style.display = '';
  if (val) val.textContent = _skuFilter;
}})();

async function load() {{
  var el = document.getElementById('offers-list');
  el.innerHTML = '<span style="color:#555">Loading…</span>';
  try {{
    var r = await fetch('/api/offers', {{headers: authHeaders()}});
    var d = await r.json();
    if (_skuFilter && d && d.ok && d.offers) {{
      d.offers = d.offers.filter(function(o) {{ return o.sku === _skuFilter; }});
    }}
    renderOffers(d);
  }} catch(e) {{
    el.innerHTML = '<div class="resp-flash err" style="display:block">Network error: ' + escapeHtml(String(e)) + '</div>';
  }}
}}

async function respond(offerId, listingId, action, counterPrice) {{
  var flash = document.getElementById(flashId(offerId));
  if (flash) {{ flash.className = 'resp-flash'; flash.textContent = ''; }}
  var body = {{listing_id: listingId, action: action, dry_run: DRY, by: 'operator'}};
  if (counterPrice !== null) body.counter_price = counterPrice;
  try {{
    var r = await fetch('/api/offers/' + encodeURIComponent(offerId) + '/respond', {{
      method: 'POST',
      headers: authHeaders({{'Content-Type': 'application/json'}}),
      body: JSON.stringify(body),
    }});
    var d = await r.json();
    if (d.ok) {{
      var msg = DRY ? '[dry-run] ' : '';
      msg += action + ' sent';
      if (d.counter_price !== null && d.counter_price !== undefined)
        msg += ' @ $' + Number(d.counter_price).toFixed(2);
      if (flash) {{ flash.className = 'resp-flash ok'; flash.textContent = msg; }}
      if (!DRY) setTimeout(load, 1200);
    }} else {{
      if (flash) {{ flash.className = 'resp-flash err'; flash.textContent = 'Error: ' + escapeHtml(d.error || 'unknown'); }}
    }}
  }} catch(e) {{
    if (flash) {{ flash.className = 'resp-flash err'; flash.textContent = 'Network error: ' + escapeHtml(String(e)); }}
  }}
}}

function respondCounter(offerId, listingId) {{
  var inp = document.getElementById('cp-' + offerId);
  var val = inp ? parseFloat(inp.value) : NaN;
  if (isNaN(val) || val <= 0) {{
    var flash = document.getElementById(flashId(offerId));
    if (flash) {{ flash.className = 'resp-flash err'; flash.textContent = 'Enter a valid counter price first.'; }}
    return;
  }}
  respond(offerId, listingId, 'Counter', val);
}}

async function loadLimits() {{
  try {{
    var r = await fetch('/api/offers/limits', {{headers: authHeaders()}});
    var d = await r.json();
    if (!d || !d.ok || !d.limits) return;
    var l = d.limits;
    var bar = document.getElementById('rl-bar');
    if (!bar) return;
    bar.textContent = 'GetBestOffers: ' + l.daily_used + '/' + l.daily_limit
      + ' calls today \u00b7 ' + l.hourly_used + '/' + l.hourly_limit + ' this hour';
    bar.classList.add('loaded');
  }} catch(e) {{ /* hide silently */ }}
}}

loadLimits();
load();
</script>
</body>
</html>
"""


@app.get("/form/offers")
def offers_form():
    """Best Offers management — pending offers with inline Accept/Counter/Decline.
    No Bearer auth (network trust); JS embeds the key for API calls."""
    from fastapi.responses import HTMLResponse

    html = _OFFERS_HTML.format(
        static_head=_STATIC_HEAD,
        static_foot=_STATIC_FOOT,
        offers_css=_OFFERS_EXTRA_CSS,
        api_key_json=json.dumps(""),
    )
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# POST /api/items/{sku}/revision/apply — run cmd_revise_apply
# DELETE /api/items/{sku}/revision — discard revision_draft
# GET /form/revisions — revision review UI (PP-EDITOR-001 Phase 3h)
# (GET /api/items/pending-revision registered earlier, before /{sku})
# ---------------------------------------------------------------------------


@app.post("/api/items/{sku}/revision/apply", dependencies=[AUTH])
def apply_revision(sku: str, body: RevisionApplyBody) -> Dict[str, Any]:
    """Apply or preview the stored revision_draft for a SKU."""
    from .revision import cmd_revise_apply

    result = cmd_revise_apply(_cfg, sku, dry_run=body.dry_run, by=body.by)
    if result.get("ok") and result.get("applied"):
        # Refresh the local live-mirror so the UI reflects what was just pushed.
        # The revision has ALREADY landed on eBay at this point — a queue/DB
        # hiccup enqueuing the follow-up sync must not turn into a 500 to the
        # operator (who would then retry an already-applied revision) and
        # must not vanish silently either (invariant C11): persist a finding
        # so local stays known-desynced until a sync actually runs.
        json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
        try:
            result["sync_job_id"] = state_machine.enqueue_job(
                queue_name="ebay_sync",
                payload={"sku": sku, "reason": "revision_apply", "origin": "operator"},
                entity_type="item",
                entity_id=sku,
                max_attempts=2,
                dedupe_key=f"ebay_sync:sku:{sku}",
            )
        except psycopg2.errors.UniqueViolation:
            # A sync for this sku is already pending — not an error, the
            # existing job already covers this revision_apply follow-up.
            result["sync_job_id"] = None
        except Exception as exc:
            log.exception("failed to enqueue post-revision ebay_sync for %s", sku)
            result["sync_job_id"] = None
            result["sync_enqueue_error"] = str(exc)
            _persist_finding(
                json_path,
                sku,
                "revision_sync_not_queued",
                f"revision applied on eBay but follow-up ebay_sync enqueue failed: {exc}",
                "apply_revision",
            )
    return result


@app.delete("/api/items/{sku}/revision", dependencies=[AUTH])
def discard_revision(sku: str) -> Dict[str, Any]:
    """Remove revision_draft from the item JSON without applying it."""
    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"sku not found: {sku}")
    try:
        doc = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"read error: {exc}")
    if "revision_draft" not in doc:
        return {"ok": True, "sku": sku, "note": "no revision_draft present"}
    # PP-CATALOG-INCR-001 CI-4 (2026-07-18): the catalog_rebuild enqueue +
    # C11 not-queued guard that used to live here is now dead weight —
    # _apply_patch above already synchronously upserts this SKU's SQLite
    # catalog row (CI-2), so there is no longer a "did the rebuild get
    # queued" question to guard against for this endpoint.
    _apply_patch(json_path, {"revision_draft": None})
    return {"ok": True, "sku": sku, "discarded": True}


_REVISIONS_EXTRA_CSS = (
    ".rev-card{background:#1a1a1a;border:1px solid #333;border-radius:10px;"
    "  padding:14px;margin-bottom:14px}"
    ".rev-header{display:flex;gap:10px;align-items:flex-start;margin-bottom:10px}"
    ".rev-thumb{width:60px;height:60px;object-fit:cover;border-radius:5px;"
    "  border:1px solid #333;background:#111;flex-shrink:0}"
    ".rev-thumb-ph{width:60px;height:60px;border-radius:5px;border:1px solid #333;"
    "  background:#111;display:flex;align-items:center;justify-content:center;"
    "  color:#444;font-size:1.2em;flex-shrink:0}"
    ".rev-info{min-width:0;flex:1}"
    ".rev-title{font-size:.92em;font-weight:600;color:#ddd;margin-bottom:3px;"
    "  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
    ".rev-meta{font-size:.76em;color:#777;display:flex;gap:8px;flex-wrap:wrap}"
    ".rev-loc{color:#888;background:#1e1e1e;border:1px solid #2a2a2a;"
    "  border-radius:3px;padding:1px 5px}"
    ".diff-table{width:100%;border-collapse:collapse;font-size:.82em;margin:8px 0}"
    ".diff-table th{text-align:left;padding:5px 8px;color:#777;font-size:.72em;"
    "  text-transform:uppercase;letter-spacing:.04em;border-bottom:1px solid #2a2a2a}"
    ".diff-table td{padding:5px 8px;border-bottom:1px solid #1e1e1e;vertical-align:top}"
    ".diff-table .dfield{color:#aaa;font-family:monospace;white-space:nowrap}"
    ".diff-table .dwas{color:#c66;word-break:break-all}"
    ".diff-table .dnow{color:#6c6;word-break:break-all}"
    ".rev-actions{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap;align-items:center}"
    ".btn-apply{padding:8px 18px;background:#1a4a1a;color:#7f7;border:1px solid #3a8a3a;"
    "  border-radius:6px;cursor:pointer;font-size:.85em;font-weight:600}"
    ".btn-apply:hover{background:#1e5a1e}"
    ".btn-discard{padding:8px 18px;background:#3a1a1a;color:#f77;border:1px solid #7a3a3a;"
    "  border-radius:6px;cursor:pointer;font-size:.85em;font-weight:600}"
    ".btn-discard:hover{background:#4a1a1a}"
    ".rev-flash{padding:6px 10px;border-radius:5px;font-size:.82em;margin-top:6px;display:none}"
    ".rev-flash.ok{background:#1a3a1a;color:#7f7;display:block}"
    ".rev-flash.err{background:#3a1a1a;color:#f77;display:block}"
    ".dry-bar{background:#1a2a3a;border:1px solid #2a4a6a;border-radius:8px;"
    "  padding:10px 14px;margin-bottom:14px;display:flex;align-items:center;gap:12px;"
    "  font-size:.87em;color:#aaa}"
    ".dry-badge{background:#2a4a6a;color:#7af;border-radius:4px;padding:2px 8px;"
    "  font-size:.78em;font-weight:600;text-transform:uppercase;flex-shrink:0}"
    ".dry-badge.live{background:#2a4a1a;color:#7f7}"
    ".toggle-wrap{display:flex;align-items:center;gap:8px;margin-left:auto}"
    ".toggle{position:relative;display:inline-block;width:40px;height:22px}"
    ".toggle input{opacity:0;width:0;height:0}"
    ".slider{position:absolute;cursor:pointer;top:0;left:0;right:0;bottom:0;"
    "  background:#2a2a2a;border-radius:22px;transition:.25s}"
    ".slider:before{position:absolute;content:'';height:16px;width:16px;left:3px;bottom:3px;"
    "  background:#888;border-radius:50%;transition:.25s}"
    ".toggle input:checked+.slider{background:#1a4a1a}"
    ".toggle input:checked+.slider:before{transform:translateX(18px);background:#7f7}"
    ".empty-state{padding:32px;text-align:center;color:#555;font-size:.95em}"
    ".blocked-drift{background:#3a1a00;border:1px solid #6a3a00;border-radius:6px;"
    "  padding:8px 10px;font-size:.8em;color:#fb7;margin-top:6px}"
    ".reload-btn{background:none;border:none;color:#4a8ade;cursor:pointer;"
    "  font-size:.82em;padding:0;text-decoration:underline;margin-left:8px}"
)

_REVISIONS_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TGW — Revisions</title>
{static_head}
<style>{revisions_css}</style>
</head>
<body>
<h2>Pending Revisions <span id="rev-count" style="font-size:.65em;color:#666;font-weight:normal"></span>
  <button class="reload-btn" onclick="load()">&#8635; Refresh</button>
</h2>

<div class="dry-bar">
  <span class="dry-badge" id="dry-badge">DRY RUN</span>
  <span id="dry-label">Apply is previewed only — no eBay API calls.</span>
  <div class="toggle-wrap">
    <span style="font-size:.78em">Go Live</span>
    <label class="toggle" title="Enable live eBay submission">
      <input type="checkbox" id="live-toggle" onchange="onLiveToggle()">
      <span class="slider"></span>
    </label>
  </div>
</div>

<div id="rev-list"><span style="color:#555">Loading…</span></div>

{static_foot}
<script>
window.TGW_API_KEY = {api_key_json};
var DRY = true;

function onLiveToggle() {{
  DRY = !document.getElementById('live-toggle').checked;
  var badge = document.getElementById('dry-badge');
  var label = document.getElementById('dry-label');
  if (DRY) {{
    badge.textContent = 'DRY RUN'; badge.className = 'dry-badge';
    label.textContent = 'Apply is previewed only — no eBay API calls.';
  }} else {{
    badge.textContent = 'LIVE'; badge.className = 'dry-badge live';
    label.textContent = 'Apply will write to eBay immediately.';
  }}
}}

function flashId(sku) {{ return 'rf-' + sku.replace(/[^a-z0-9]/gi,'_'); }}

function renderRevisions(data) {{
  var el = document.getElementById('rev-list');
  var countEl = document.getElementById('rev-count');
  if (!data || !data.ok) {{
    el.innerHTML = '<div class="rev-flash err" style="display:block">Failed to load: ' +
      escapeHtml((data && data.error) || 'unknown error') + '</div>';
    countEl.textContent = '';
    return;
  }}
  if (!data.items || data.items.length === 0) {{
    el.innerHTML = '<div class="empty-state">No pending revision drafts.</div>';
    countEl.textContent = '';
    return;
  }}
  countEl.textContent = '(' + data.items.length + ' pending)';
  var html = '';
  data.items.forEach(function(item) {{
    var draft = item.draft || {{}};
    var delta = draft.delta || {{}};
    var baseline = draft.baseline || {{}};
    var snap = baseline.snapshot || {{}};
    var by = escapeHtml(draft.by || '');
    var at = escapeHtml((draft.created_at || '').slice(0,16));
    var bh = escapeHtml((baseline.hash || '').slice(0,12));
    var sku = item.sku;
    var thumbUrl = '/thumb/' + encodeURIComponent(sku);
    var thumbHtml = '<img class="rev-thumb" src="' + thumbUrl +
      '" onerror="this.style.display=\\'none\\';this.nextElementSibling.style.display=\\'flex\\'" loading="lazy">' +
      '<div class="rev-thumb-ph" style="display:none">&#128247;</div>';

    html += '<div class="rev-card" id="card-' + escapeHtml(sku) + '">';
    html += '<div class="rev-header">' + thumbHtml;
    html += '<div class="rev-info">';
    html += '<div class="rev-title">' + escapeHtml(item.title || sku) + '</div>';
    html += '<div class="rev-meta">';
    if (item.location) html += '<span class="rev-loc">' + escapeHtml(item.location) + '</span>';
    html += '<span>' + escapeHtml(sku) + '</span>';
    if (by) html += '<span>by ' + by + '</span>';
    if (at) html += '<span>' + at + '</span>';
    if (bh) html += '<span>baseline ' + bh + '…</span>';
    html += '</div></div></div>';

    html += '<table class="diff-table"><tr><th>Field</th><th>Current</th><th>Proposed</th></tr>';
    Object.keys(delta).forEach(function(field) {{
      html += '<tr>' +
        '<td class="dfield">' + escapeHtml(field) + '</td>' +
        '<td class="dwas">' + escapeHtml(String(snap[field] !== undefined ? snap[field] : '—')) + '</td>' +
        '<td class="dnow">' + escapeHtml(String(delta[field])) + '</td>' +
        '</tr>';
    }});
    html += '</table>';

    html += '<div class="rev-actions">';
    html += '<button class="btn-apply" onclick="applyRev(' + JSON.stringify(sku) + ')">Apply</button>';
    html += '<button class="btn-discard" onclick="discardRev(' + JSON.stringify(sku) + ')">Discard</button>';
    html += '</div>';
    html += '<div class="rev-flash" id="' + flashId(sku) + '"></div>';
    html += '</div>';
  }});
  el.innerHTML = html;
}}

async function load() {{
  var el = document.getElementById('rev-list');
  el.innerHTML = '<span style="color:#555">Loading…</span>';
  try {{
    var r = await fetch('/api/items/pending-revision', {{headers: authHeaders()}});
    var d = await r.json();
    renderRevisions(d);
  }} catch(e) {{
    el.innerHTML = '<div class="rev-flash err" style="display:block">Network error: ' + escapeHtml(String(e)) + '</div>';
  }}
}}

async function applyRev(sku) {{
  var flash = document.getElementById(flashId(sku));
  if (flash) {{ flash.className = 'rev-flash'; flash.textContent = ''; }}
  try {{
    var r = await fetch('/api/items/' + encodeURIComponent(sku) + '/revision/apply', {{
      method: 'POST',
      headers: authHeaders({{'Content-Type': 'application/json'}}),
      body: JSON.stringify({{dry_run: DRY, by: 'operator'}}),
    }});
    var d = await r.json();
    if (d.ok) {{
      var msg = (DRY ? '[dry-run] ' : '') + 'Apply OK';
      if (d.diff_lines && d.diff_lines.length) msg += ' — ' + d.diff_lines[0];
      if (flash) {{ flash.className = 'rev-flash ok'; flash.textContent = msg; }}
      if (!DRY) setTimeout(load, 1200);
    }} else {{
      var errMsg = d.error || 'unknown error';
      if (d.blocking_drift && d.blocking_drift.length) {{
        errMsg += ' (blocking drift: ' + d.blocking_drift.map(function(bd) {{ return bd.field; }}).join(', ') + ')';
      }}
      if (flash) {{ flash.className = 'rev-flash err'; flash.textContent = 'Error: ' + escapeHtml(errMsg); }}
    }}
  }} catch(e) {{
    if (flash) {{ flash.className = 'rev-flash err'; flash.textContent = 'Network error: ' + escapeHtml(String(e)); }}
  }}
}}

async function discardRev(sku) {{
  var flash = document.getElementById(flashId(sku));
  if (flash) {{ flash.className = 'rev-flash'; flash.textContent = ''; }}
  try {{
    var r = await fetch('/api/items/' + encodeURIComponent(sku) + '/revision', {{
      method: 'DELETE',
      headers: authHeaders(),
    }});
    var d = await r.json();
    if (d.ok) {{
      var card = document.getElementById('card-' + sku);
      if (card) {{ card.style.opacity = '.4'; card.style.pointerEvents = 'none'; }}
      if (flash) {{ flash.className = 'rev-flash ok'; flash.textContent = 'Discarded.'; }}
      setTimeout(load, 900);
    }} else {{
      if (flash) {{ flash.className = 'rev-flash err'; flash.textContent = 'Error: ' + escapeHtml(d.error || 'unknown'); }}
    }}
  }} catch(e) {{
    if (flash) {{ flash.className = 'rev-flash err'; flash.textContent = 'Network error: ' + escapeHtml(String(e)); }}
  }}
}}

load();
</script>
</body>
</html>
"""


@app.get("/form/revisions")
def revisions_form():
    """Revision draft review — diff table, Apply/Discard per item, Go Live toggle.
    No Bearer auth (network trust); JS embeds the key for API calls."""
    from fastapi.responses import HTMLResponse

    html = _REVISIONS_HTML.format(
        static_head=_STATIC_HEAD,
        static_foot=_STATIC_FOOT,
        revisions_css=_REVISIONS_EXTRA_CSS,
        api_key_json=json.dumps(""),
    )
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# GET /form/review — post-draft review queue (PP-EDITOR-001 Phase 3i)
# ---------------------------------------------------------------------------

_REVIEW_EXTRA_CSS = (
    ".rq-card{background:#1a1a1a;border:1px solid #333;border-radius:10px;"
    "  padding:12px 14px;margin-bottom:10px;display:flex;gap:12px;align-items:flex-start}"
    ".rq-thumb{width:70px;height:70px;object-fit:cover;border-radius:6px;"
    "  border:1px solid #333;background:#111;flex-shrink:0}"
    ".rq-thumb-ph{width:70px;height:70px;border-radius:6px;border:1px solid #333;"
    "  background:#111;display:flex;align-items:center;justify-content:center;"
    "  color:#444;font-size:1.4em;flex-shrink:0}"
    ".rq-body{min-width:0;flex:1}"
    ".rq-title{font-size:.93em;font-weight:600;color:#ddd;margin-bottom:4px;"
    "  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
    ".rq-meta{font-size:.76em;color:#777;display:flex;gap:8px;flex-wrap:wrap;margin-bottom:6px}"
    ".rq-chip{background:#1e1e1e;border:1px solid #2a2a2a;border-radius:3px;padding:1px 6px;color:#888}"
    ".rq-price{color:#bfb;font-weight:600}"
    ".rq-cond{color:#aaa}"
    ".rq-cat{color:#7af}"
    ".rq-actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center}"
    ".btn-approve{padding:7px 16px;background:#1a4a1a;color:#7f7;border:1px solid #3a8a3a;"
    "  border-radius:6px;cursor:pointer;font-size:.83em;font-weight:600}"
    ".btn-approve:hover{background:#1e5a1e}"
    ".btn-redraft{padding:7px 14px;background:#2a1a3a;color:#b7f;border:1px solid #5a3a7a;"
    "  border-radius:6px;cursor:pointer;font-size:.83em;font-weight:600}"
    ".btn-redraft:hover{background:#3a1a4a}"
    ".btn-edit{padding:7px 14px;background:#1a1a2a;color:#7af;border:1px solid #3a4a7a;"
    "  border-radius:6px;text-decoration:none;font-size:.83em;font-weight:600;"
    "  display:inline-block;line-height:1.4}"
    ".btn-edit:hover{background:#1a2a3a}"
    ".rq-flash{padding:5px 9px;border-radius:5px;font-size:.8em;margin-top:5px;display:none}"
    ".rq-flash.ok{background:#1a3a1a;color:#7f7;display:block}"
    ".rq-flash.err{background:#3a1a1a;color:#f77;display:block}"
    ".rq-flash.info{background:#1a2a3a;color:#7af;display:block}"
    ".empty-state{padding:32px;text-align:center;color:#555;font-size:.95em}"
    ".approve-all-bar{display:flex;align-items:center;gap:12px;margin-bottom:14px;"
    "  padding:10px 14px;background:#1a2a1a;border:1px solid #2a4a2a;border-radius:8px}"
    ".btn-approve-all{padding:8px 20px;background:#1a5a1a;color:#7f7;border:1px solid #4a9a4a;"
    "  border-radius:6px;cursor:pointer;font-size:.87em;font-weight:600}"
    ".btn-approve-all:hover{background:#1e6a1e}"
    ".approve-all-label{font-size:.85em;color:#7a9a7a}"
    ".reload-btn{background:none;border:none;color:#4a8ade;cursor:pointer;"
    "  font-size:.82em;padding:0;text-decoration:underline;margin-left:8px}"
    ".quality-ok{color:#7f7;font-size:.78em}"
    ".quality-warn{color:#fb7;font-size:.78em}"
)

_REVIEW_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TGW — Review Queue</title>
{static_head}
<style>{review_css}</style>
</head>
<body>
<h2>Review Queue <span id="rq-count" style="font-size:.65em;color:#666;font-weight:normal"></span>
  <button class="reload-btn" onclick="load()">&#8635; Refresh</button>
</h2>

<div id="approve-all-bar" style="display:none" class="approve-all-bar">
  <button class="btn-approve-all" onclick="approveAll()">&#10003; Approve All</button>
  <span class="approve-all-label" id="approve-all-label">Approve every item in this queue.</span>
</div>

<div id="rq-list"><span style="color:#555">Loading…</span></div>

{static_foot}
<script>
window.TGW_API_KEY = {api_key_json};
var _items = [];

function flashId(sku) {{ return 'rqf-' + sku.replace(/[^a-z0-9]/gi,'_'); }}

function fmtPrice(p) {{
  if (p === null || p === undefined) return '<span style="color:#666">unpriced</span>';
  return '$' + Number(p).toFixed(2);
}}

function qualityHtml(q) {{
  if (!q || typeof q !== 'object') return '';
  var score = q.score !== undefined ? q.score : null;
  if (score === null) return '';
  var cls = score >= 70 ? 'quality-ok' : 'quality-warn';
  var out = '<span class="' + cls + '">Q:' + score + '</span>';
  var flags = q.flags || [];
  if (flags.length) {{
    out += ' <span class="quality-warn" style="font-size:.76em">⚑ ' + escapeHtml(flags.join(', ')) + '</span>';
  }}
  return out;
}}

function aspectHtml(item) {{
  var tot = item.aspects_required_total;
  var fil = item.aspects_required_filled;
  if (!tot) return '';
  var ok = fil >= tot;
  var cls = ok ? 'quality-ok' : 'quality-warn';
  return '<span class="' + cls + '">' + fil + '/' + tot + ' aspects</span>';
}}

function renderQueue(data) {{
  var el = document.getElementById('rq-list');
  var countEl = document.getElementById('rq-count');
  var bar = document.getElementById('approve-all-bar');
  if (!data || !data.ok) {{
    el.innerHTML = '<div class="rq-flash err" style="display:block">Failed to load: ' +
      escapeHtml((data && data.error) || 'unknown error') + '</div>';
    countEl.textContent = '';
    bar.style.display = 'none';
    return;
  }}
  _items = data.items || [];
  if (_items.length === 0) {{
    el.innerHTML = '<div class="empty-state">No items awaiting review. All caught up!</div>';
    countEl.textContent = '';
    bar.style.display = 'none';
    // Update nav badge to zero
    var navBadge = document.getElementById('nav-review-count');
    if (navBadge) navBadge.textContent = '';
    return;
  }}
  countEl.textContent = '(' + _items.length + ' pending)';
  bar.style.display = _items.length > 1 ? 'flex' : 'none';
  document.getElementById('approve-all-label').textContent =
    'Approve all ' + _items.length + ' items in this queue.';
  // Update nav badge
  var navBadge = document.getElementById('nav-review-count');
  if (navBadge) navBadge.textContent = _items.length > 0 ? String(_items.length) : '';

  var html = '';
  _items.forEach(function(item) {{
    var sku = item.sku;
    var thumbUrl = '/thumb/' + encodeURIComponent(sku);
    var editUrl = '/form/operator/items/' + encodeURIComponent(sku);
    var thumbHtml = '<img class="rq-thumb" src="' + thumbUrl +
      '" onerror="this.style.display=\\'none\\';this.nextElementSibling.style.display=\\'flex\\'" loading="lazy">' +
      '<div class="rq-thumb-ph" style="display:none">&#128247;</div>';

    html += '<div class="rq-card" id="card-' + escapeHtml(sku) + '">';
    html += thumbHtml;
    html += '<div class="rq-body">';
    html += '<div class="rq-title">' + escapeHtml(item.title || sku) + '</div>';
    html += '<div class="rq-meta">';
    html += '<span class="rq-chip rq-price">' + fmtPrice(item.price) + '</span>';
    if (item.condition) html += '<span class="rq-chip rq-cond">' + escapeHtml(item.condition) + '</span>';
    if (item.category_name || item.category_id) {{
      var cat = item.category_name || ('Cat ' + item.category_id);
      html += '<span class="rq-chip rq-cat">' + escapeHtml(cat) + '</span>';
    }}
    if (item.location) html += '<span class="rq-chip">' + escapeHtml(item.location) + '</span>';
    var qh = qualityHtml(item.quality);
    if (qh) html += qh;
    var ah = aspectHtml(item);
    if (ah) html += ah;
    if (item.category_confidence && item.category_confidence !== 'high') {{
      var catWarn = '<span class="quality-warn" style="font-size:.76em">⚠ conf: ' +
                   escapeHtml(item.category_confidence) + '</span>';
      if (item.lookup_category_name && item.lookup_category_name !== item.category_name) {{
        catWarn += '<div style="font-size:.73em;color:#aaa;margin-top:2px">' +
          'AI: ' + escapeHtml(item.category_id || '') + ' · ' + escapeHtml(item.category_name || '') +
          ' vs Lookup: ' + escapeHtml(item.lookup_category_id || '') + ' · ' + escapeHtml(item.lookup_category_name) +
          '</div>';
      }}
      html += catWarn;
    }}
    if (item.offline_draft) {{
      html += '<span class="quality-warn" style="font-size:.76em">⚠ offline draft</span>';
    }}
    html += '</div>';
    html += '<div class="rq-actions">';
    html += '<button class="btn-approve" onclick="approveOne(' + JSON.stringify(sku) + ')">&#10003; Approve</button>';
    html += '<button class="btn-redraft" onclick="redraftOne(' + JSON.stringify(sku) + ')">&#8635; Re-draft</button>';
    html += '<a class="btn-edit" href="' + escapeHtml(editUrl) + '" target="_blank">&#9998; Edit</a>';
    html += '</div>';
    html += '<div class="rq-flash" id="' + flashId(sku) + '"></div>';
    html += '</div></div>';
  }});
  el.innerHTML = html;
}}

async function load() {{
  var el = document.getElementById('rq-list');
  el.innerHTML = '<span style="color:#555">Loading…</span>';
  try {{
    var r = await fetch('/api/items/review-queue', {{headers: authHeaders()}});
    var d = await r.json();
    renderQueue(d);
  }} catch(e) {{
    el.innerHTML = '<div class="rq-flash err" style="display:block">Network error: ' + escapeHtml(String(e)) + '</div>';
  }}
}}

async function approveOne(sku) {{
  var flash = document.getElementById(flashId(sku));
  if (flash) {{ flash.className = 'rq-flash'; flash.textContent = ''; }}
  try {{
    var r = await fetch('/api/items/' + encodeURIComponent(sku) + '/action', {{
      method: 'POST',
      headers: authHeaders({{'Content-Type': 'application/json'}}),
      body: JSON.stringify({{action: 'approve'}}),
    }});
    var d = await r.json();
    if (d.ok) {{
      var card = document.getElementById('card-' + sku);
      if (card) {{ card.style.opacity = '.4'; card.style.pointerEvents = 'none'; }}
      if (flash) {{ flash.className = 'rq-flash ok'; flash.textContent = 'Approved — status set to Ready.'; }}
      setTimeout(load, 900);
    }} else {{
      if (flash) {{ flash.className = 'rq-flash err'; flash.textContent = 'Error: ' + escapeHtml(d.error || d.detail || 'unknown'); }}
    }}
  }} catch(e) {{
    if (flash) {{ flash.className = 'rq-flash err'; flash.textContent = 'Network error: ' + escapeHtml(String(e)); }}
  }}
}}

async function redraftOne(sku) {{
  var flash = document.getElementById(flashId(sku));
  if (flash) {{ flash.className = 'rq-flash'; flash.textContent = ''; }}
  try {{
    var r = await fetch('/api/items/' + encodeURIComponent(sku) + '/action', {{
      method: 'POST',
      headers: authHeaders({{'Content-Type': 'application/json'}}),
      body: JSON.stringify({{action: 'ebay_draft'}}),
    }});
    var d = await r.json();
    if (d.ok) {{
      if (flash) {{ flash.className = 'rq-flash info'; flash.textContent = 'Re-draft queued.'; }}
    }} else {{
      if (flash) {{ flash.className = 'rq-flash err'; flash.textContent = 'Error: ' + escapeHtml(d.error || d.detail || 'unknown'); }}
    }}
  }} catch(e) {{
    if (flash) {{ flash.className = 'rq-flash err'; flash.textContent = 'Network error: ' + escapeHtml(String(e)); }}
  }}
}}

async function approveAll() {{
  var label = document.getElementById('approve-all-label');
  label.textContent = 'Approving…';
  var skus = _items.map(function(i) {{ return i.sku; }});
  var done = 0, failed = 0;
  for (var i = 0; i < skus.length; i++) {{
    try {{
      var r = await fetch('/api/items/' + encodeURIComponent(skus[i]) + '/action', {{
        method: 'POST',
        headers: authHeaders({{'Content-Type': 'application/json'}}),
        body: JSON.stringify({{action: 'approve'}}),
      }});
      var d = await r.json();
      if (d.ok) {{ done++; }} else {{ failed++; }}
    }} catch(e) {{ failed++; }}
    label.textContent = 'Approved ' + done + '/' + skus.length + (failed ? ' (' + failed + ' errors)' : '') + '…';
  }}
  label.textContent = 'Done — approved ' + done + (failed ? ', ' + failed + ' errors' : '') + '.';
  setTimeout(load, 1200);
}}

load();
</script>
</body>
</html>
"""


@app.get("/form/drafts")
def drafts_form():
    """Retired direct-action queue; use canonical item operator objects."""
    return RedirectResponse(
        url="/form/items?status=Draft",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        headers={"Cache-Control": "no-store, no-cache"},
    )


@app.get("/form/review")
def review_form_redirect():
    """Backward-compatible redirect to the read-only inventory queue."""
    return RedirectResponse(
        url="/form/items?status=Draft",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        headers={"Cache-Control": "no-store, no-cache"},
    )


# ---------------------------------------------------------------------------
# GET /form/needs-review — blocked items dashboard (PP-UI-INTEGRITY-001 Phase 3)
# ---------------------------------------------------------------------------

_NEEDS_REVIEW_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TGW — Needs Review</title>
{static_head}
<style>
.nr-group{{margin-bottom:22px}}
.nr-group-hdr{{font-size:.82em;font-weight:700;text-transform:uppercase;letter-spacing:.06em;
  color:#888;padding:6px 0 4px;border-bottom:1px solid #222;margin-bottom:8px}}
.nr-card{{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:8px;
  padding:10px 14px;margin-bottom:6px;display:flex;gap:12px;align-items:flex-start}}
.nr-card.ready{{opacity:.45;border-color:#1a3a1a}}
.nr-body{{min-width:0;flex:1}}
.nr-sku{{font-family:monospace;font-size:.78em;color:#4a8ade}}
.nr-title{{font-size:.9em;color:#ddd;margin:2px 0 4px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}}
.nr-meta{{font-size:.76em;color:#666;display:flex;gap:8px;flex-wrap:wrap;margin-bottom:6px}}
.nr-chip{{background:#111;border:1px solid #222;border-radius:3px;padding:1px 6px}}
.nr-reason{{color:#f99}}.nr-stage{{color:#7af}}.nr-at{{color:#666}}
.nr-error{{font-size:.78em;color:#f77;margin-bottom:4px;word-break:break-word}}
.nr-sugg{{font-size:.78em;color:#fb7;margin-bottom:6px}}
.nr-actions{{display:flex;gap:8px;flex-wrap:wrap}}
.btn-nr-ready{{padding:6px 14px;background:#1a4a1a;color:#7f7;border:1px solid #3a8a3a;
  border-radius:6px;cursor:pointer;font-size:.81em;font-weight:600}}
.btn-nr-ready:hover{{background:#1e5a1e}}
.btn-nr-view{{padding:6px 12px;background:#1a1a2a;color:#7af;border:1px solid #3a4a7a;
  border-radius:6px;text-decoration:none;font-size:.81em;font-weight:600;
  display:inline-block;line-height:1.4}}
.btn-nr-view:hover{{background:#1a2a3a}}
.nr-flash{{padding:4px 8px;border-radius:4px;font-size:.78em;margin-top:4px;display:none}}
.nr-flash.ok{{background:#1a3a1a;color:#7f7;display:block}}
.nr-flash.err{{background:#3a1a1a;color:#f77;display:block}}
.empty-nr{{padding:32px;text-align:center;color:#555;font-size:.95em}}
.nr-counts{{font-size:.78em;color:#666;margin-bottom:14px}}
</style>
</head>
<body>
<h2>Needs Review
  <span id="nr-total" style="font-size:.6em;color:#666;font-weight:normal"></span>
  <button style="background:none;border:none;color:#4a8ade;cursor:pointer;font-size:.8em;
    text-decoration:underline;margin-left:8px" onclick="load()">&#8635; Refresh</button>
</h2>
<div id="nr-list"><span style="color:#555">Loading…</span></div>

{static_foot}
<script>
window.TGW_API_KEY = {api_key_json};

function flashId(sku) {{ return 'nrf-' + sku.replace(/[^a-z0-9]/gi,'_'); }}

function renderNeeds(data) {{
  var el = document.getElementById('nr-list');
  var tot = document.getElementById('nr-total');
  if (!data || !data.ok) {{
    el.innerHTML = '<div style="color:#f77;padding:16px">Failed to load: ' +
      escapeHtml((data && data.error) || 'unknown') + '</div>';
    return;
  }}
  var items = data.items || [];
  tot.textContent = '(' + items.length + ' blocked)';
  if (!items.length) {{
    el.innerHTML = '<div class="empty-nr">No items blocked — all clear.</div>';
    return;
  }}
  // Group by stage → reason_code (server sends grouped too, but build locally for live updates)
  var grouped = {{}};
  items.forEach(function(it) {{
    var rb = it.review_block || {{}};
    var stage = rb.stage || 'unknown';
    var rc    = rb.reason_code || 'UNKNOWN_ERROR';
    var gkey  = stage + '/' + rc;
    if (!grouped[gkey]) grouped[gkey] = {{stage: stage, rc: rc, items: []}};
    grouped[gkey].items.push(it);
  }});
  var html = '';
  Object.keys(grouped).sort().forEach(function(gkey) {{
    var g = grouped[gkey];
    html += '<div class="nr-group">';
    html += '<div class="nr-group-hdr">' + escapeHtml(g.stage) +
            ' · ' + escapeHtml(g.rc) +
            ' <span style="color:#555;font-weight:400">(' + g.items.length + ')</span></div>';
    g.items.forEach(function(it) {{
      var rb  = it.review_block || {{}};
      var sku = it.sku;
      var fid = flashId(sku);
      var editUrl = '/form/operator/items/' + encodeURIComponent(sku);
      html += '<div class="nr-card" id="nr-card-' + escapeHtml(sku) + '">';
      html += '<div class="nr-body">';
      html += '<div class="nr-sku">' + escapeHtml(sku) + '</div>';
      html += '<div class="nr-title">' + escapeHtml(it.title || '—') + '</div>';
      html += '<div class="nr-meta">';
      html += '<span class="nr-chip nr-stage">' + escapeHtml(rb.stage || '') + '</span>';
      html += '<span class="nr-chip nr-reason">' + escapeHtml(rb.reason_code || '') + '</span>';
      if (rb.flagged_at) {{
        html += '<span class="nr-chip nr-at">flagged ' +
                escapeHtml(String(rb.flagged_at).slice(0,10)) + '</span>';
      }}
      if (rb.retested_at) {{
        html += '<span class="nr-chip" style="color:#aaa">retested ' +
                escapeHtml(String(rb.retested_at).slice(0,10)) + '</span>';
      }}
      html += '</div>';
      if (rb.error) {{
        html += '<div class="nr-error">' + escapeHtml(rb.error) + '</div>';
      }}
      if (rb.suggestion) {{
        html += '<div class="nr-sugg">&#128161; ' + escapeHtml(rb.suggestion) + '</div>';
      }}
      html += '<div class="nr-actions">';
      html += '<button class="btn-nr-ready" onclick="markReady(' + JSON.stringify(sku) + ')">&#10003; Mark Ready</button>';
      html += '<a class="btn-nr-view" href="' + escapeHtml(editUrl) + '" target="_blank">&#9654; View Item</a>';
      html += '</div>';
      html += '<div class="nr-flash" id="' + fid + '"></div>';
      html += '</div></div>';
    }});
    html += '</div>';
  }});
  el.innerHTML = html;
}}

async function markReady(sku) {{
  var flash = document.getElementById(flashId(sku));
  if (flash) {{ flash.className = 'nr-flash'; flash.textContent = ''; }}
  try {{
    var r = await fetch('/api/items/' + encodeURIComponent(sku) + '/action', {{
      method: 'POST',
      headers: authHeaders({{'Content-Type': 'application/json'}}),
      body: JSON.stringify({{action: 'review_mark_ready'}}),
    }});
    var d = await r.json();
    if (d.ok) {{
      var card = document.getElementById('nr-card-' + sku);
      if (card) {{ card.classList.add('ready'); card.style.pointerEvents = 'none'; }}
      if (flash) {{ flash.className = 'nr-flash ok';
                   flash.textContent = 'Marked ready — re-queue from item detail page.'; }}
      setTimeout(load, 1500);
    }} else {{
      if (flash) {{ flash.className = 'nr-flash err';
                   flash.textContent = 'Error: ' + escapeHtml(d.error || d.detail || 'unknown'); }}
    }}
  }} catch(e) {{
    if (flash) {{ flash.className = 'nr-flash err';
                 flash.textContent = 'Network error: ' + escapeHtml(String(e)); }}
  }}
}}

async function load() {{
  var el = document.getElementById('nr-list');
  el.innerHTML = '<span style="color:#555">Loading…</span>';
  try {{
    var r = await fetch('/api/review', {{headers: authHeaders()}});
    var d = await r.json();
    renderNeeds(d);
    // update nav badge if present
    var nb = document.getElementById('nav-blocked-count');
    if (nb && d.ok) nb.textContent = d.count > 0 ? String(d.count) : '';
  }} catch(e) {{
    document.getElementById('nr-list').innerHTML =
      '<div style="color:#f77;padding:16px">Network error: ' + escapeHtml(String(e)) + '</div>';
  }}
}}

load();
</script>
</body>
</html>
"""


@app.get("/form/needs-review")
def needs_review_form():
    """Blocked items dashboard — items with review_block.ready=false (PP-UI-INTEGRITY-001 P3)."""
    from fastapi.responses import HTMLResponse

    html = _NEEDS_REVIEW_HTML.format(
        static_head=_STATIC_HEAD,
        static_foot=_STATIC_FOOT,
        api_key_json=json.dumps(""),
    )
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# GET /form/pipeline — pipeline monitor + dead-letter manager (Phase 3j)
# ---------------------------------------------------------------------------

_PIPELINE_EXTRA_CSS = (
    ".pl-section{margin-bottom:22px}"
    ".pl-label{font-size:.75em;text-transform:uppercase;letter-spacing:.08em;color:#666;"
    "  margin-bottom:8px;display:flex;align-items:center;gap:8px}"
    ".pl-label .auto-badge{background:#1a3a1a;color:#7a7;border:1px solid #2a5a2a;"
    "  border-radius:4px;font-size:.78em;padding:1px 6px;font-weight:normal;"
    "  text-transform:none;letter-spacing:0}"
    ".pl-label .countdown{color:#555;font-size:.85em;margin-left:auto}"
    ".pl-table{width:100%;border-collapse:collapse;font-size:.84em}"
    ".pl-table th{text-align:left;padding:6px 10px;color:#666;font-size:.72em;"
    "  text-transform:uppercase;letter-spacing:.04em;border-bottom:1px solid #2a2a2a}"
    ".pl-table td{padding:6px 10px;border-bottom:1px solid #1e1e1e;vertical-align:middle}"
    ".pl-table tr:last-child td{border-bottom:none}"
    ".pl-table .qname{font-family:monospace;color:#aaa}"
    ".pl-table .n-queued{color:#7af;font-weight:600}"
    ".pl-table .n-run{color:#fb7;font-weight:600}"
    ".pl-table .n-done{color:#888}"
    ".pl-table .n-dead{color:#f77;font-weight:600}"
    ".pl-table .n-zero{color:#333}"
    ".pl-table .elapsed{color:#aaa;font-size:.9em}"
    ".pl-table .sku-cell{font-family:monospace;font-size:.82em;color:#bbb}"
    ".pl-table .act-btns{display:flex;gap:6px;justify-content:flex-end}"
    ".btn-requeue{padding:5px 12px;background:#1a3a1a;color:#7f7;border:1px solid #3a7a3a;"
    "  border-radius:5px;cursor:pointer;font-size:.8em;font-weight:600}"
    ".btn-requeue:hover{background:#1e4a1e}"
    ".btn-cancel{padding:5px 12px;background:#2a1a1a;color:#f77;border:1px solid #5a2a2a;"
    "  border-radius:5px;cursor:pointer;font-size:.8em;font-weight:600}"
    ".btn-cancel:hover{background:#3a1a1a}"
    ".dl-flash{font-size:.8em;margin-left:8px;display:none}"
    ".dl-flash.ok{color:#7f7;display:inline}"
    ".dl-flash.err{color:#f77;display:inline}"
    ".pill{display:inline-block;border-radius:10px;padding:2px 8px;font-size:.72em;"
    "  font-weight:600;text-transform:uppercase;letter-spacing:.03em}"
    ".pill.active{background:#1a4a1a;color:#7f7;border:1px solid #3a8a3a}"
    ".pill.inactive{background:#2a2a2a;color:#666;border:1px solid #333}"
    ".pill.failed{background:#3a1a1a;color:#f77;border:1px solid #6a3a3a}"
    ".pill.unknown{background:#1a1a2a;color:#77a;border:1px solid #2a2a4a}"
    ".worker-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:6px}"
    ".w-card{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:6px;"
    "  padding:7px 10px;display:flex;justify-content:space-between;align-items:center;"
    "  gap:6px;font-size:.83em}"
    ".w-card .w-name{font-family:monospace;color:#999;overflow:hidden;text-overflow:ellipsis;"
    "  white-space:nowrap;font-size:.88em}"
    ".w-card.active-card{border-color:#2a5a2a}"
    ".w-card.failed-card{border-color:#5a2a2a}"
    ".empty-state{padding:24px;text-align:center;color:#555;font-size:.9em}"
    ".err-box{padding:10px;border:1px solid #5a2a2a;border-radius:6px;color:#f77;"
    "  background:#1e1010;font-size:.84em;margin-bottom:12px}"
    ".reload-btn{background:none;border:none;color:#4a8ade;cursor:pointer;"
    "  font-size:.82em;padding:0;text-decoration:underline;margin-left:8px}"
    ".error-row{color:#f77;font-size:.8em;padding:3px 10px 6px;font-style:italic}"
)

_PIPELINE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TGW — Pipeline</title>
{static_head}
<style>{pipeline_css}</style>
</head>
<body>
<h2>Pipeline Monitor
  <button class="reload-btn" onclick="loadAll()">&#8635; Refresh</button>
  <span id="countdown" style="font-size:.6em;color:#555;margin-left:8px"></span>
</h2>

<div id="err-box" class="err-box" style="display:none"></div>

<!-- Queue depths -->
<div class="pl-section">
  <div class="pl-label">Queue Depths</div>
  <div id="queue-table"><span style="color:#555">Loading…</span></div>
</div>

<!-- Active jobs -->
<div class="pl-section">
  <div class="pl-label">Active Jobs</div>
  <div id="active-table"><span style="color:#555">Loading…</span></div>
</div>

<!-- Dead-letter jobs -->
<div class="pl-section">
  <div class="pl-label">Dead-Letter Jobs</div>
  <div id="dead-table"><span style="color:#555">Loading…</span></div>
</div>

<!-- Workers -->
<div class="pl-section">
  <div class="pl-label">Workers</div>
  <div id="workers-grid"><span style="color:#555">Loading…</span></div>
</div>

{static_foot}
<script>
window.TGW_API_KEY = {api_key_json};

var _refreshInterval = 30;
var _secondsLeft = _refreshInterval;
var _timer = null;

function startCountdown() {{
  _secondsLeft = _refreshInterval;
  clearInterval(_timer);
  _timer = setInterval(function() {{
    _secondsLeft--;
    var el = document.getElementById('countdown');
    if (el) el.textContent = '(auto-refresh in ' + _secondsLeft + 's)';
    if (_secondsLeft <= 0) {{
      clearInterval(_timer);
      loadAll();
    }}
  }}, 1000);
}}

function numCell(n, cls) {{
  if (n === null || n === undefined || n === 0) return '<td class="n-zero">—</td>';
  return '<td class="' + cls + '">' + n + '</td>';
}}

function fmtElapsed(started) {{
  if (!started) return '—';
  var s = Math.round((Date.now() - new Date(started).getTime()) / 1000);
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s/60) + 'm ' + (s%60) + 's';
  return Math.floor(s/3600) + 'h ' + Math.floor((s%3600)/60) + 'm';
}}

function fmtTime(iso) {{
  if (!iso) return '—';
  var d = new Date(iso);
  return d.toLocaleTimeString(undefined, {{hour:'2-digit', minute:'2-digit', second:'2-digit'}});
}}

function fmtAge(iso) {{
  if (!iso) return '';
  var s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s/60) + 'm ago';
  if (s < 86400) return Math.floor(s/3600) + 'h ago';
  return Math.floor(s/86400) + 'd ago';
}}

function renderQueues(data, daily) {{
  var el = document.getElementById('queue-table');
  if (!data || !data.ok) {{
    el.innerHTML = '<div class="err-box">Failed to load queue status.</div>';
    return;
  }}
  var queues = data.queues || {{}};
  var dailyQueues = (daily && daily.ok) ? (daily.queues || {{}}) : {{}};
  var dailyOk = !!(daily && daily.ok);
  var names = Object.keys(queues).sort();
  if (names.length === 0) {{
    el.innerHTML = '<div class="empty-state">No queue activity.</div>';
    return;
  }}
  var html = '<table class="pl-table"><tr>' +
    '<th>Queue</th><th>Consumer</th><th>Ready</th><th>Scheduled</th><th>Running</th>' +
    '<th title="Succeeded today (' + (dailyOk ? escapeHtml(daily.date + ' ' + daily.tz) : 'date-scoped') +
      '), from queue_daily_stats">Done today</th>' +
    '<th title="Failed or dead-lettered today (' + (dailyOk ? escapeHtml(daily.date + ' ' + daily.tz) : 'date-scoped') +
      '), from queue_daily_stats">Failed today</th>' +
    '<th title="Lifetime dead-letter backlog awaiting review (queue_jobs, not date-scoped)">DL backlog</th></tr>';
  names.forEach(function(q) {{
    var s = queues[q] || {{}};
    var consumer = (data.consumers || {{}})[q] || {{status:'unknown', reason:'Worker status unavailable.'}};
    var consumerText = consumer.status === 'active' ? 'active' : consumer.reason;
    var consumerClass = consumer.status === 'active' ? 'n-done' : (consumer.status === 'no_consumer' ? 'n-dead' : 'n-queued');
    var d = dailyQueues[q] || {{succeeded: 0, failed: 0, dead_letter: 0}};
    var scheduled = (data.scheduled || {{}})[q] || 0;
    var pending = (s.queued || 0) + (s.leased || 0) + (s.retry_wait || 0) - scheduled;
    var running = s.running || 0;
    var doneToday = dailyOk ? (d.succeeded || 0) : null;
    var failedToday = dailyOk ? ((d.failed || 0) + (d.dead_letter || 0)) : null;
    var dlBacklog = s.dead_letter || 0;
    html += '<tr>' +
      '<td class="qname">' + escapeHtml(q) + '</td>' +
      '<td class="' + consumerClass + '" title="' + escapeHtml(consumer.reason || '') + '">' + escapeHtml(consumerText) + '</td>' +
      numCell(pending || null, 'n-queued') +
      numCell(scheduled || null, 'n-queued') +
      numCell(running || null, 'n-run') +
      (dailyOk ? numCell(doneToday || null, 'n-done') : '<td class="n-zero">?</td>') +
      (dailyOk ? numCell(failedToday || null, 'n-dead') : '<td class="n-zero">?</td>') +
      numCell(dlBacklog || null, 'n-dead') +
      '</tr>';
  }});
  html += '</table>';
  el.innerHTML = html;
}}

function renderActive(jobs) {{
  var el = document.getElementById('active-table');
  var active = (jobs || []).filter(function(j) {{
    return j.state === 'running' || j.state === 'leased';
  }});
  if (active.length === 0) {{
    el.innerHTML = '<div class="empty-state">No jobs currently running.</div>';
    return;
  }}
  var html = '<table class="pl-table"><tr>' +
    '<th>Queue</th><th>SKU</th><th>State</th><th>Elapsed</th><th>Started</th></tr>';
  active.forEach(function(j) {{
    html += '<tr>' +
      '<td class="qname">' + escapeHtml(j.queue_name) + '</td>' +
      '<td class="sku-cell">' + escapeHtml(j.sku || '—') + '</td>' +
      '<td>' + escapeHtml(j.state) + '</td>' +
      '<td class="elapsed">' + fmtElapsed(j.started_at) + '</td>' +
      '<td style="color:#777;font-size:.85em">' + fmtTime(j.started_at) + '</td>' +
      '</tr>';
  }});
  html += '</table>';
  el.innerHTML = html;
}}

function renderDead(jobs) {{
  var el = document.getElementById('dead-table');
  var dead = (jobs || []).filter(function(j) {{ return j.state === 'dead_letter'; }});
  if (dead.length === 0) {{
    el.innerHTML = '<div class="empty-state">No dead-letter jobs. &#10003;</div>';
    return;
  }}
  var html = '<table class="pl-table"><tr>' +
    '<th>Queue</th><th>SKU</th><th>Error</th><th>Age</th><th style="text-align:right">Actions</th></tr>';
  dead.forEach(function(j) {{
    var fid = 'dlf-' + j.job_id.replace(/-/g,'').slice(0,12);
    html += '<tr id="dlr-' + escapeHtml(j.job_id) + '">' +
      '<td class="qname">' + escapeHtml(j.queue_name) + '</td>' +
      '<td class="sku-cell">' + escapeHtml(j.sku || '—') + '</td>' +
      '<td style="color:#888;font-size:.8em;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' +
        escapeHtml((j.error_detail || '').slice(0,80)) + '</td>' +
      '<td style="color:#777;font-size:.85em;white-space:nowrap">' + fmtAge(j.finished_at) + '</td>' +
      '<td class="act-btns">' +
        '<button class="btn-requeue" onclick="requeueJob(' + JSON.stringify(j.job_id) + ',' + JSON.stringify(fid) + ')">Re-queue</button>' +
        '<button class="btn-cancel" onclick="cancelJob(' + JSON.stringify(j.job_id) + ',' + JSON.stringify(fid) + ')">Cancel</button>' +
        '<span class="dl-flash" id="' + fid + '"></span>' +
      '</td>' +
      '</tr>';
    if (j.error_detail) {{
      html += '<tr><td colspan="5" class="error-row">' + escapeHtml(j.error_detail.slice(0,200)) + '</td></tr>';
    }}
  }});
  html += '</table>';
  el.innerHTML = html;
}}

function renderWorkers(data) {{
  var el = document.getElementById('workers-grid');
  if (!data || !data.ok) {{
    el.innerHTML = '<div class="err-box">Failed to load worker status.</div>';
    return;
  }}
  var workers = data.workers || [];
  if (workers.length === 0) {{
    el.innerHTML = '<div class="empty-state">No worker info available.</div>';
    return;
  }}
  var html = '<div style="font-size:.82em;color:#666;margin-bottom:8px">' +
    data.up + ' / ' + data.total + ' active</div>';
  html += '<div class="worker-grid">';
  workers.forEach(function(w) {{
    var cls = w.active === 'active' ? 'active' : (w.active === 'failed' ? 'failed' : 'unknown');
    var cardCls = 'w-card' + (cls === 'active' ? ' active-card' : (cls === 'failed' ? ' failed-card' : ''));
    var name = w.unit.replace(/^tgw-worker@/, '').replace(/[.]service$/, '');
    if (name === 'tgw-http') name = 'tgw-http';
    html += '<div class="' + cardCls + '">' +
      '<span class="w-name" title="' + escapeHtml(w.unit) + '">' + escapeHtml(name) + '</span>' +
      '<span class="pill ' + cls + '">' + escapeHtml(w.active) + '</span>' +
      '</div>';
  }});
  html += '</div>';
  el.innerHTML = html;
}}

async function requeueJob(jobId, flashId) {{
  var flash = document.getElementById(flashId);
  if (flash) {{ flash.className = 'dl-flash'; flash.textContent = ''; }}
  try {{
    var r = await fetch('/api/jobs/' + encodeURIComponent(jobId) + '/requeue', {{
      method: 'POST',
      headers: authHeaders(),
    }});
    var d = await r.json();
    if (d.ok) {{
      if (flash) {{ flash.className = 'dl-flash ok'; flash.textContent = 'Re-queued!'; }}
      setTimeout(loadAll, 1200);
    }} else {{
      if (flash) {{ flash.className = 'dl-flash err'; flash.textContent = d.detail || d.error || 'Error'; }}
    }}
  }} catch(e) {{
    if (flash) {{ flash.className = 'dl-flash err'; flash.textContent = 'Network error'; }}
  }}
}}

async function cancelJob(jobId, flashId) {{
  var flash = document.getElementById(flashId);
  if (flash) {{ flash.className = 'dl-flash'; flash.textContent = ''; }}
  try {{
    var r = await fetch('/api/jobs/' + encodeURIComponent(jobId) + '/cancel', {{
      method: 'POST',
      headers: authHeaders(),
    }});
    var d = await r.json();
    if (d.ok) {{
      var row = document.getElementById('dlr-' + jobId);
      if (row) {{ row.style.opacity = '.4'; row.style.pointerEvents = 'none'; }}
      if (flash) {{ flash.className = 'dl-flash ok'; flash.textContent = 'Cancelled.'; }}
      setTimeout(loadAll, 1500);
    }} else {{
      if (flash) {{ flash.className = 'dl-flash err'; flash.textContent = d.detail || d.error || 'Error'; }}
    }}
  }} catch(e) {{
    if (flash) {{ flash.className = 'dl-flash err'; flash.textContent = 'Network error'; }}
  }}
}}

async function loadAll() {{
  startCountdown();
  // Queue depths (current state) + daily outcome stats (date-scoped)
  try {{
    var r = await fetch('/api/queue/status', {{headers: authHeaders()}});
    var statusData = await r.json();
    var dailyData = null;
    try {{
      var rd = await fetch('/api/queue/daily_stats', {{headers: authHeaders()}});
      dailyData = await rd.json();
    }} catch(e2) {{
      dailyData = null;
    }}
    renderQueues(statusData, dailyData);
  }} catch(e) {{
    document.getElementById('queue-table').innerHTML =
      '<div class="err-box">Network error: ' + escapeHtml(String(e)) + '</div>';
  }}

  // Active + dead-letter jobs from a combined query
  try {{
    var r2 = await fetch('/api/pipeline/jobs', {{headers: authHeaders()}});
    var d2 = await r2.json();
    if (d2.ok) {{
      renderActive(d2.jobs);
      renderDead(d2.jobs);
    }} else {{
      var msg = '<div class="err-box">Failed to load jobs.</div>';
      document.getElementById('active-table').innerHTML = msg;
      document.getElementById('dead-table').innerHTML = msg;
    }}
  }} catch(e) {{
    var msg = '<div class="err-box">Network error: ' + escapeHtml(String(e)) + '</div>';
    document.getElementById('active-table').innerHTML = msg;
    document.getElementById('dead-table').innerHTML = msg;
  }}

  // Workers
  try {{
    var r3 = await fetch('/api/system/workers', {{headers: authHeaders()}});
    renderWorkers(await r3.json());
  }} catch(e) {{
    document.getElementById('workers-grid').innerHTML =
      '<div class="err-box">Network error: ' + escapeHtml(String(e)) + '</div>';
  }}
}}

loadAll();
</script>
</body>
</html>
"""


@app.get("/form/pipeline")
def pipeline_form():
    """Pipeline monitor + dead-letter manager. No Bearer auth (network trust)."""
    from fastapi.responses import HTMLResponse

    html = _PIPELINE_HTML.format(
        static_head=_STATIC_HEAD,
        static_foot=_STATIC_FOOT,
        pipeline_css=_PIPELINE_EXTRA_CSS,
        api_key_json=json.dumps(""),
    )
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# GET /api/pipeline/jobs — active + dead-letter jobs for the pipeline monitor
# ---------------------------------------------------------------------------


@app.get("/api/pipeline/jobs", dependencies=[AUTH])
def pipeline_jobs() -> Dict[str, Any]:
    """Return running/leased and dead_letter jobs for the pipeline monitor.

    Also includes failed/retry_wait so the operator can see what's struggling.
    """
    try:
        with psycopg2.connect(_cfg["postgres_dsn"]) as con:
            with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT job_id::text, queue_name, state,
                           payload_json->>'sku' AS sku,
                           started_at, finished_at, created_at,
                           error_detail, attempt_count, max_attempts
                      FROM queue_jobs
                     WHERE state IN ('running', 'leased', 'dead_letter', 'failed', 'retry_wait')
                     ORDER BY
                       CASE state
                         WHEN 'running'  THEN 0
                         WHEN 'leased'   THEN 1
                         WHEN 'retry_wait' THEN 2
                         WHEN 'failed'   THEN 3
                         WHEN 'dead_letter' THEN 4
                       END,
                       created_at DESC
                     LIMIT 200
                    """
                )
                jobs = [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"postgres error: {exc}")

    for j in jobs:
        j["consumer"] = _queue_consumers([str(j.get("queue_name") or "")]).get(str(j.get("queue_name") or ""), {})
        for ts_field in ("started_at", "finished_at", "created_at"):
            v = j.get(ts_field)
            if v is not None and hasattr(v, "isoformat"):
                j[ts_field] = v.isoformat()

    return {"ok": True, "jobs": jobs, "count": len(jobs)}


# ---------------------------------------------------------------------------
# POST /api/jobs/{job_id}/cancel — cancel a dead-letter or queued job
# ---------------------------------------------------------------------------


@app.post("/api/jobs/{job_id}/cancel", dependencies=[AUTH])
def cancel_job(job_id: str) -> Dict[str, Any]:
    """Cancel a dead-letter (or queued/retry_wait) job."""
    try:
        with psycopg2.connect(_cfg["postgres_dsn"]) as con:
            with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT state FROM queue_jobs WHERE job_id = %s",
                    (job_id,),
                )
                row = cur.fetchone()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"postgres error: {exc}")

    if not isinstance(row, dict):
        raise HTTPException(status_code=404, detail=f"job not found: {job_id}")

    cancellable = {"dead_letter", "queued", "retry_wait", "failed"}
    if row["state"] not in cancellable:
        raise HTTPException(
            status_code=400,
            detail=f"job {job_id} is in state {row['state']!r}; can only cancel: {sorted(cancellable)}",
        )

    try:
        with psycopg2.connect(_cfg["postgres_dsn"]) as con:
            with con.cursor() as cur:
                # WHERE-state guard closes the race between the SELECT above
                # and this UPDATE: a worker leasing the job in between would
                # otherwise get silently stomped either direction.
                cur.execute(
                    "UPDATE queue_jobs SET state = 'cancelled' WHERE job_id = %s AND state = ANY(%s)",
                    (job_id, list(cancellable)),
                )
                cancelled = cur.rowcount > 0
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"cancel failed: {exc}")

    if not cancelled:
        raise HTTPException(
            status_code=409,
            detail=f"job {job_id} changed state before it could be cancelled (race with a worker lease) — refresh and retry",
        )

    return {"ok": True, "job_id": job_id, "cancelled": True}


# ---------------------------------------------------------------------------
# GET /api/system/info — disk, token, sync stamp, job-state counts (Phase 3k)
# ---------------------------------------------------------------------------


@app.get("/api/system/info", dependencies=[AUTH])
def system_info() -> Dict[str, Any]:
    """Infrastructure snapshot: disk usage, eBay token detail, sync stamp, job states."""
    import shutil

    # Disk usage for key paths
    disk_paths = {
        "itemdata": _cfg.get("itemdata_root", Path("/opt/TGW/data/ItemData")),
        "logs": Path("/opt/TGW/var/log"),
        "backup": Path("/opt/TGW/var/local/backups"),
    }
    disk: Dict[str, Any] = {}
    for label, path in disk_paths.items():
        try:
            usage = shutil.disk_usage(str(path))
            disk[label] = {
                "path": str(path),
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
                "pct": round(usage.used / usage.total * 100, 1) if usage.total else 0,
            }
        except Exception as exc:
            disk[label] = {"path": str(path), "error": str(exc)}

    # eBay token detail
    token_info: Dict[str, Any] = {"exists": False, "ok": False}
    try:
        token_path: Path = _cfg["ebay_token_path"]
        if token_path.exists():
            mtime = token_path.stat().st_mtime
            doc = json.loads(token_path.read_text(encoding="utf-8"))
            raw_exp = doc.get("expires_at") or doc.get("expiry") or doc.get("expire_time")
            expires_at: Optional[float] = None
            remaining: Optional[int] = None
            if raw_exp:
                try:
                    expires_at = float(raw_exp)
                    remaining = int(expires_at - time.time())
                except (ValueError, TypeError):
                    pass
            token_info = {
                "exists": True,
                "mtime": mtime,
                "expires_at": expires_at,
                "remaining_seconds": remaining,
                "ok": remaining is None or remaining > 300,
            }
        else:
            token_info = {"exists": False, "ok": False, "error": "file not found"}
    except Exception as exc:
        token_info = {"exists": False, "ok": False, "error": str(exc)}

    # Last offline-sync stamp (catalog .db mtime is the proxy)
    sync_info: Dict[str, Any] = {}
    try:
        db_path = _cfg.get("sqlite_catalog_path")
        if db_path and db_path.exists():
            sync_info = {"catalog_mtime": db_path.stat().st_mtime, "path": str(db_path)}
        else:
            sync_info = {"catalog_mtime": None, "path": str(db_path) if db_path else None}
    except Exception as exc:
        sync_info = {"error": str(exc)}

    # Postgres job-table row counts by state
    job_states: Dict[str, int] = {}
    try:
        with psycopg2.connect(_cfg["postgres_dsn"]) as con:
            with con.cursor() as cur:
                cur.execute("SELECT state, COUNT(*) FROM queue_jobs GROUP BY state ORDER BY state")
                for row in cur.fetchall():
                    job_states[str(row[0])] = int(row[1])
    except Exception as exc:
        job_states["_error"] = str(exc)

    return {
        "ok": True,
        "disk": disk,
        "ebay_token": token_info,
        "sync": sync_info,
        "job_states": job_states,
    }


# ---------------------------------------------------------------------------
# POST /api/system/workers/{unit}/restart — restart a systemd unit (Phase 3k)
# ---------------------------------------------------------------------------


@app.post("/api/system/workers/{unit}/restart", dependencies=[AUTH])
def restart_worker(unit: str) -> Dict[str, Any]:
    """Restart a tgw-worker or tgw-http systemd unit via sudo systemctl."""
    from .queue import WORKER_QUEUES

    allowed = {f"tgw-worker@{q}.service" for q in WORKER_QUEUES} | {"tgw-http.service"}
    if unit not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"unit {unit!r} is not in the allowed restart set",
        )

    try:
        r = subprocess.run(
            ["sudo", "-n", "systemctl", "restart", unit],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if r.returncode != 0:
            return {
                "ok": False,
                "unit": unit,
                "returncode": r.returncode,
                "stderr": r.stderr.strip(),
            }
        return {"ok": True, "unit": unit, "returncode": r.returncode}
    except Exception as exc:
        return {"ok": False, "unit": unit, "error": str(exc)}


# ---------------------------------------------------------------------------
# GET /form/system — full system health page (PP-EDITOR-001 Phase 3k)
# ---------------------------------------------------------------------------

_SYSTEM_EXTRA_CSS = (
    ".sy-section{{margin-bottom:24px}}"
    ".sy-label{{font-size:.75em;text-transform:uppercase;letter-spacing:.08em;color:#666;"
    "  margin-bottom:8px;display:flex;align-items:center;gap:8px}}"
    ".sy-table{{width:100%;border-collapse:collapse;font-size:.84em}}"
    ".sy-table th{{text-align:left;padding:6px 10px;color:#666;font-size:.72em;"
    "  text-transform:uppercase;letter-spacing:.04em;border-bottom:1px solid #2a2a2a}}"
    ".sy-table td{{padding:6px 10px;border-bottom:1px solid #1e1e1e;vertical-align:middle}}"
    ".sy-table tr:last-child td{{border-bottom:none}}"
    ".chip-pass{{display:inline-block;background:#1a3a1a;color:#7f7;border:1px solid #3a6a3a;"
    "  border-radius:10px;padding:2px 9px;font-size:.72em;font-weight:600;"
    "  text-transform:uppercase;letter-spacing:.03em}}"
    ".chip-warn{{display:inline-block;background:#3a2a00;color:#fb7;border:1px solid #6a5000;"
    "  border-radius:10px;padding:2px 9px;font-size:.72em;font-weight:600;"
    "  text-transform:uppercase;letter-spacing:.03em}}"
    ".chip-fail{{display:inline-block;background:#3a1a1a;color:#f77;border:1px solid #7a3a3a;"
    "  border-radius:10px;padding:2px 9px;font-size:.72em;font-weight:600;"
    "  text-transform:uppercase;letter-spacing:.03em}}"
    ".sy-detail{{color:#888;font-size:.82em;margin-top:2px}}"
    ".token-box{{padding:14px 16px;background:#1a1a1a;border:1px solid #2a2a2a;"
    "  border-radius:8px;display:flex;align-items:center;gap:14px}}"
    ".token-countdown{{font-size:1.9em;font-weight:700;font-variant-numeric:tabular-nums;line-height:1}}"
    ".token-ok{{color:#7f7}}.token-warn{{color:#fb7}}.token-crit{{color:#f77}}"
    ".token-meta{{font-size:.78em;color:#666;margin-top:4px}}"
    ".disk-row{{display:flex;flex-direction:column;gap:4px;margin-bottom:10px}}"
    ".disk-row:last-child{{margin-bottom:0}}"
    ".disk-label{{font-size:.8em;color:#aaa;display:flex;justify-content:space-between}}"
    ".disk-bar-bg{{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:4px;height:10px;overflow:hidden}}"
    ".disk-bar-fill{{height:100%;border-radius:4px;transition:width .3s}}"
    ".disk-fill-ok{{background:#2a5a2a}}.disk-fill-warn{{background:#5a4a00}}.disk-fill-crit{{background:#5a1a1a}}"
    ".disk-meta{{font-size:.72em;color:#555}}"
    ".state-grid{{display:flex;flex-wrap:wrap;gap:8px}}"
    ".state-chip{{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:6px;"
    "  padding:7px 12px;font-size:.83em;display:flex;flex-direction:column;align-items:center;gap:2px;"
    "  min-width:90px;text-align:center}}"
    ".state-chip .sc-n{{font-size:1.3em;font-weight:700;line-height:1}}"
    ".state-chip .sc-l{{font-size:.7em;color:#666;text-transform:uppercase;letter-spacing:.04em}}"
    ".sc-queued .sc-n{{color:#7af}}.sc-running .sc-n{{color:#fb7}}"
    ".sc-succeeded .sc-n{{color:#7f7}}.sc-dead_letter .sc-n,.sc-failed .sc-n{{color:#f77}}"
    ".sc-retry_wait .sc-n{{color:#fb7}}.sc-cancelled .sc-n{{color:#555}}"
    ".sy-sync-row{{display:flex;gap:10px;align-items:center;font-size:.87em}}"
    ".sy-sync-val{{color:#aaa}}.sy-sync-age{{color:#555;font-size:.85em}}"
    ".w-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:6px}}"
    ".wc{{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:6px;"
    "  padding:7px 10px;display:flex;justify-content:space-between;align-items:center;gap:6px}}"
    ".wc .wn{{font-family:monospace;color:#999;font-size:.83em;overflow:hidden;"
    "  text-overflow:ellipsis;white-space:nowrap;flex:1}}"
    ".wc.wc-active{{border-color:#2a5a2a}}.wc.wc-failed{{border-color:#5a2a2a}}"
    ".pill-a{{display:inline-block;border-radius:10px;padding:2px 7px;font-size:.7em;"
    "  font-weight:600;text-transform:uppercase;letter-spacing:.03em}}"
    ".pill-active{{background:#1a4a1a;color:#7f7;border:1px solid #3a8a3a}}"
    ".pill-inactive{{background:#2a2a2a;color:#666;border:1px solid #333}}"
    ".pill-failed{{background:#3a1a1a;color:#f77;border:1px solid #6a3a3a}}"
    ".pill-unknown{{background:#1a1a2a;color:#77a;border:1px solid #2a2a4a}}"
    ".btn-restart{{padding:4px 9px;background:#1a2a3a;color:#7af;border:1px solid #2a4a6a;"
    "  border-radius:4px;cursor:pointer;font-size:.73em;font-weight:600;flex-shrink:0}}"
    ".btn-restart:hover{{background:#1e3a5a}}"
    ".restart-flash{{font-size:.75em;margin-left:4px;display:none}}"
    ".restart-flash.ok{{color:#7f7;display:inline}}.restart-flash.err{{color:#f77;display:inline}}"
    ".err-box{{padding:10px;border:1px solid #5a2a2a;border-radius:6px;color:#f77;"
    "  background:#1e1010;font-size:.84em;margin-bottom:12px}}"
    ".empty-state{{padding:20px;text-align:center;color:#555;font-size:.9em}}"
    ".reload-btn{{background:none;border:none;color:#4a8ade;cursor:pointer;"
    "  font-size:.82em;padding:0;text-decoration:underline;margin-left:8px}}"
)

_SYSTEM_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TGW — System</title>
{static_head}
<style>{system_css}</style>
</head>
<body>
<h2>System Health
  <button class="reload-btn" onclick="loadAll()">&#8635; Refresh</button>
</h2>

<div id="err-box" class="err-box" style="display:none"></div>

<!-- Health checks -->
<div class="sy-section">
  <div class="sy-label">Platform Health Checks</div>
  <div id="health-checks"><span style="color:#555">Loading…</span></div>
</div>

<!-- eBay token -->
<div class="sy-section">
  <div class="sy-label">eBay Token</div>
  <div id="token-box"><span style="color:#555">Loading…</span></div>
</div>

<!-- Disk usage -->
<div class="sy-section">
  <div class="sy-label">Disk Usage</div>
  <div id="disk-usage"><span style="color:#555">Loading…</span></div>
</div>

<!-- Job state counts -->
<div class="sy-section">
  <div class="sy-label">Job Queue States</div>
  <div id="job-states"><span style="color:#555">Loading…</span></div>
</div>

<!-- Last sync stamp -->
<div class="sy-section">
  <div class="sy-label">Offline-Sync Stamp</div>
  <div id="sync-stamp"><span style="color:#555">Loading…</span></div>
</div>

<!-- Workers -->
<div class="sy-section">
  <div class="sy-label">Workers</div>
  <div id="workers-grid"><span style="color:#555">Loading…</span></div>
</div>

{static_foot}
<script>
window.TGW_API_KEY = {api_key_json};

var _tokenExpiry = null;
var _tokenTick = null;

function fmtBytes(n) {{
  if (n === null || n === undefined) return '—';
  if (n >= 1e12) return (n/1e12).toFixed(1) + ' TB';
  if (n >= 1e9)  return (n/1e9).toFixed(1) + ' GB';
  if (n >= 1e6)  return (n/1e6).toFixed(1) + ' MB';
  return (n/1e3).toFixed(0) + ' KB';
}}

function fmtAge(epochSec) {{
  if (!epochSec) return '—';
  var s = Math.round(Date.now()/1000 - epochSec);
  if (s < 0) return 'just now';
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s/60) + 'm ago';
  if (s < 86400) return Math.floor(s/3600) + 'h ago';
  return Math.floor(s/86400) + 'd ago';
}}

function fmtTimestamp(epochSec) {{
  if (!epochSec) return '—';
  var d = new Date(epochSec * 1000);
  return d.toLocaleDateString() + ' ' + d.toLocaleTimeString(undefined, {{hour:'2-digit',minute:'2-digit'}});
}}

function fmtCountdown(seconds) {{
  if (seconds === null || seconds === undefined) return '—';
  if (seconds < 0) return 'EXPIRED';
  var h = Math.floor(Math.abs(seconds) / 3600);
  var m = Math.floor((Math.abs(seconds) % 3600) / 60);
  var s = Math.abs(seconds) % 60;
  if (h > 0) return h + 'h ' + m + 'm';
  if (m > 0) return m + 'm ' + s + 's';
  return s + 's';
}}

function tokenClass(seconds) {{
  if (seconds === null || seconds === undefined) return 'token-ok';
  if (seconds < 0)      return 'token-crit';
  if (seconds < 7200)   return 'token-crit';
  if (seconds < 43200)  return 'token-warn';
  return 'token-ok';
}}

function renderHealthChecks(data) {{
  var el = document.getElementById('health-checks');
  if (!data || !data.checks) {{
    el.innerHTML = '<div class="err-box">Health data unavailable.</div>';
    return;
  }}
  var html = '<table class="sy-table"><tr>' +
    '<th>Check</th><th>Status</th><th>Detail</th><th style="text-align:right">ms</th></tr>';
  data.checks.forEach(function(c) {{
    var chipCls = c.ok ? (c.warn ? 'chip-warn' : 'chip-pass') : 'chip-fail';
    var chipTxt = c.ok ? (c.warn ? 'WARN' : 'PASS') : 'FAIL';
    html += '<tr>' +
      '<td style="font-family:monospace;color:#aaa;font-size:.85em">' + escapeHtml(c.check) + '</td>' +
      '<td><span class="' + chipCls + '">' + chipTxt + '</span></td>' +
      '<td class="sy-detail">' + escapeHtml(c.detail || '') + '</td>' +
      '<td style="text-align:right;color:#444;font-size:.78em">' + (c.elapsed_ms || '') + '</td>' +
      '</tr>';
  }});
  html += '</table>';
  el.innerHTML = html;
}}

function renderTokenBox(info) {{
  var el = document.getElementById('token-box');
  if (!info || !info.ok && !info.exists) {{
    el.innerHTML = '<div class="err-box">Token info unavailable.</div>';
    return;
  }}
  var tok = info.ebay_token || {{}};
  var rem = tok.remaining_seconds;
  var cls = tokenClass(rem);
  var countdown = fmtCountdown(rem);
  var mtime = tok.mtime ? fmtTimestamp(tok.mtime) : '—';
  var expAt = tok.expires_at ? fmtTimestamp(tok.expires_at) : '—';
  _tokenExpiry = tok.expires_at || null;

  var chipCls = tok.ok ? 'chip-pass' : 'chip-fail';
  var chipTxt = tok.ok ? 'VALID' : (tok.exists ? 'EXPIRED' : 'MISSING');
  el.innerHTML = '<div class="token-box">' +
    '<div>' +
      '<div class="token-countdown ' + cls + '" id="token-countdown">' + escapeHtml(countdown) + '</div>' +
      '<div class="token-meta">Expires: ' + escapeHtml(expAt) + '</div>' +
      '<div class="token-meta">File mtime: ' + escapeHtml(mtime) + '</div>' +
    '</div>' +
    '<span class="' + chipCls + '" style="align-self:flex-start;margin-top:4px">' + chipTxt + '</span>' +
    '</div>';

  // Start live countdown tick
  if (_tokenTick) clearInterval(_tokenTick);
  if (_tokenExpiry !== null) {{
    _tokenTick = setInterval(function() {{
      var nowSec = Date.now() / 1000;
      var remNow = Math.round(_tokenExpiry - nowSec);
      var cdEl = document.getElementById('token-countdown');
      if (cdEl) {{
        cdEl.textContent = fmtCountdown(remNow);
        cdEl.className = 'token-countdown ' + tokenClass(remNow);
      }}
    }}, 1000);
  }}
}}

function renderDiskUsage(info) {{
  var el = document.getElementById('disk-usage');
  var disk = (info || {{}}).disk || {{}};
  var labels = {{itemdata: 'ItemData', logs: 'Logs', backup: 'Backups'}};
  var html = '';
  Object.keys(labels).forEach(function(key) {{
    var d = disk[key] || {{}};
    if (d.error) {{
      html += '<div class="disk-row"><div class="disk-label"><span>' + labels[key] + '</span>'
        + '<span style="color:#f77">' + escapeHtml(d.error) + '</span></div></div>';
      return;
    }}
    var pct = d.pct || 0;
    var fillCls = pct > 90 ? 'disk-fill-crit' : (pct > 75 ? 'disk-fill-warn' : 'disk-fill-ok');
    html += '<div class="disk-row">' +
      '<div class="disk-label">' +
        '<span>' + labels[key] + ' <span style="color:#555;font-size:.88em">' + escapeHtml(d.path || '') + '</span></span>' +
        '<span>' + pct + '%</span>' +
      '</div>' +
      '<div class="disk-bar-bg"><div class="disk-bar-fill ' + fillCls + '" style="width:' + pct + '%"></div></div>' +
      '<div class="disk-meta">' + fmtBytes(d.used) + ' used of ' + fmtBytes(d.total) + ' — ' + fmtBytes(d.free) + ' free</div>' +
      '</div>';
  }});
  el.innerHTML = html || '<div class="empty-state">No disk data available.</div>';
}}

function renderJobStates(info) {{
  var el = document.getElementById('job-states');
  var states = (info || {{}}).job_states || {{}};
  var keys = Object.keys(states).filter(function(k) {{ return k !== '_error'; }});
  if (!keys.length) {{
    el.innerHTML = '<div class="empty-state">No job data. ' + (states._error ? escapeHtml(states._error) : '') + '</div>';
    return;
  }}
  var order = ['queued','leased','running','retry_wait','succeeded','failed','dead_letter','cancelled'];
  keys.sort(function(a,b) {{
    var ai = order.indexOf(a), bi = order.indexOf(b);
    if (ai < 0) ai = 99; if (bi < 0) bi = 99;
    return ai - bi;
  }});
  var html = '<div class="state-grid">';
  keys.forEach(function(k) {{
    html += '<div class="state-chip sc-' + k + '">' +
      '<span class="sc-n">' + (states[k] || 0) + '</span>' +
      '<span class="sc-l">' + escapeHtml(k.replace(/_/g,' ')) + '</span>' +
      '</div>';
  }});
  html += '</div>';
  el.innerHTML = html;
}}

function renderSyncStamp(info) {{
  var el = document.getElementById('sync-stamp');
  var sync = (info || {{}}).sync || {{}};
  if (sync.error) {{
    el.innerHTML = '<div class="err-box">' + escapeHtml(sync.error) + '</div>';
    return;
  }}
  var mtime = sync.catalog_mtime;
  var html = '<div class="sy-sync-row">' +
    '<span class="sy-sync-val">' + (mtime ? fmtTimestamp(mtime) : '—') + '</span>' +
    '<span class="sy-sync-age">' + (mtime ? fmtAge(mtime) : 'catalog not found') + '</span>' +
    '<span style="color:#444;font-size:.8em">' + escapeHtml(sync.path || '') + '</span>' +
    '</div>';
  el.innerHTML = html;
}}

function renderWorkers(data) {{
  var el = document.getElementById('workers-grid');
  if (!data || !data.ok) {{
    el.innerHTML = '<div class="err-box">Worker data unavailable.</div>';
    return;
  }}
  var workers = data.workers || [];
  if (!workers.length) {{
    el.innerHTML = '<div class="empty-state">No worker info.</div>';
    return;
  }}
  var html = '<div style="font-size:.82em;color:#666;margin-bottom:8px">' +
    data.up + ' / ' + data.total + ' active</div>';
  html += '<div class="w-grid">';
  workers.forEach(function(w) {{
    var cls = w.active === 'active' ? 'active' : (w.active === 'failed' ? 'failed' : 'unknown');
    var cardCls = 'wc' + (cls === 'active' ? ' wc-active' : (cls === 'failed' ? ' wc-failed' : ''));
    var pillCls = 'pill-a pill-' + cls;
    var name = w.unit.replace(/^tgw-worker@/, '').replace(/[.]service$/, '');
    if (w.unit === 'tgw-http.service') name = 'tgw-http';
    var fid = 'rf-' + w.unit.replace(/[^a-z0-9]/gi,'').slice(0,16);
    html += '<div class="' + cardCls + '">' +
      '<span class="wn" title="' + escapeHtml(w.unit) + '">' + escapeHtml(name) + '</span>' +
      '<span class="' + pillCls + '">' + escapeHtml(w.active) + '</span>' +
      '<button class="btn-restart" onclick="restartWorker(' + JSON.stringify(w.unit) + ',' + JSON.stringify(fid) + ')"' +
        ' title="Restart ' + escapeHtml(w.unit) + '">&#8635;</button>' +
      '<span class="restart-flash" id="' + fid + '"></span>' +
      '</div>';
  }});
  html += '</div>';
  el.innerHTML = html;
}}

async function restartWorker(unit, flashId) {{
  if (!confirm('Restart ' + unit + '?\\n\\nThe worker will stop and restart. In-progress jobs may be re-queued.')) return;
  var flash = document.getElementById(flashId);
  if (flash) {{ flash.className = 'restart-flash'; flash.textContent = ''; }}
  try {{
    var r = await fetch('/api/system/workers/' + encodeURIComponent(unit) + '/restart', {{
      method: 'POST',
      headers: authHeaders(),
    }});
    var d = await r.json().catch(function() {{ return {{}}; }});
    if (d.ok) {{
      if (flash) {{ flash.className = 'restart-flash ok'; flash.textContent = 'Restarted'; }}
      setTimeout(function() {{ loadWorkers(); }}, 2000);
    }} else {{
      var msg = d.stderr || d.error || d.detail || 'Error';
      if (flash) {{ flash.className = 'restart-flash err'; flash.textContent = msg.slice(0, 60); }}
    }}
  }} catch(e) {{
    if (flash) {{ flash.className = 'restart-flash err'; flash.textContent = 'Network error'; }}
  }}
}}

async function loadWorkers() {{
  try {{
    var r = await fetch('/api/system/workers', {{headers: authHeaders()}});
    renderWorkers(await r.json());
  }} catch(e) {{
    document.getElementById('workers-grid').innerHTML =
      '<div class="err-box">Network error: ' + escapeHtml(String(e)) + '</div>';
  }}
}}

async function loadAll() {{
  try {{
    var results = await Promise.all([
      fetch('/api/health', {{headers: authHeaders()}}).then(function(r) {{ return r.json().catch(function() {{ return {{}}; }}); }}).catch(function() {{ return null; }}),
      fetch('/api/system/workers', {{headers: authHeaders()}}).then(function(r) {{ return r.json().catch(function() {{ return {{}}; }}); }}).catch(function() {{ return null; }}),
      fetch('/api/system/info', {{headers: authHeaders()}}).then(function(r) {{ return r.json().catch(function() {{ return {{}}; }}); }}).catch(function() {{ return null; }}),
    ]);
    var health = results[0];
    var workers = results[1];
    var info = results[2];

    // Health data may come back wrapped in detail when ok=False (503)
    if (health && !health.checks && health.detail && health.detail.checks) {{
      health = health.detail;
    }}

    renderHealthChecks(health);
    renderTokenBox(info);
    renderDiskUsage(info);
    renderJobStates(info);
    renderSyncStamp(info);
    renderWorkers(workers);
  }} catch(e) {{
    var eb = document.getElementById('err-box');
    if (eb) {{ eb.style.display = ''; eb.textContent = 'Load failed: ' + String(e); }}
  }}
}}

loadAll();
</script>
</body>
</html>
"""


@app.get("/form/system")
def system_form():
    """Full system health page. No Bearer auth (network trust)."""
    from fastapi.responses import HTMLResponse

    html = _SYSTEM_HTML.format(
        static_head=_STATIC_HEAD,
        static_foot=_STATIC_FOOT,
        system_css=_SYSTEM_EXTRA_CSS,
        api_key_json=json.dumps(""),
    )
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# GET /form/home — home dashboard (PP-EDITOR-001 Phase 3c)
# ---------------------------------------------------------------------------

_HOME_EXTRA_CSS = (
    ".section{margin-bottom:18px}"
    ".section-label{font-size:.75em;text-transform:uppercase;letter-spacing:.08em;color:#666;margin-bottom:6px}"
    ".ok-chip{background:#1a3a1a;border-color:#4a8a4a;color:#9f9}"
    ".err-chip{background:#3a1a1a;border-color:#8a4a4a;color:#f99}"
    ".card-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}"
    "@media(min-width:560px){.card-grid{grid-template-columns:repeat(3,1fr)}}"
    ".acard{background:#1a1a1a;border:2px solid #333;border-radius:8px;padding:12px 10px;"
    "  text-decoration:none;color:inherit;display:block;transition:border-color .15s}"
    ".acard:hover{border-color:#555}"
    ".acard.alert{border-color:#6a3a00}.acard.ok{border-color:#1a4a1a}"
    ".acard.info{border-color:#1a3a5a}.acard.err{border-color:#5a1a1a}"
    ".acard .count{font-size:1.8em;font-weight:bold;line-height:1;margin-bottom:4px}"
    ".acard .alabel{font-size:.78em;color:#888}"
    ".acard.alert .count{color:#fb7}.acard.ok .count{color:#7f7}"
    ".acard.info .count{color:#7af}.acard.err .count{color:#f77}"
    ".intake-row{display:flex;gap:6px;margin-top:4px}"
    ".intake-row input{flex:1;margin:0}"
    ".btn-sm{padding:10px 16px;background:#1a4a8a;color:#fff;border:none;border-radius:6px;"
    "  cursor:pointer;font-size:.9em;white-space:nowrap;flex-shrink:0}"
    ".btn-sm:active{background:#143a6a}"
    ".two-col{display:grid;grid-template-columns:1fr;gap:14px;margin-top:14px}"
    "@media(min-width:640px){.two-col{grid-template-columns:3fr 2fr}}"
    "h3.subsec{font-size:.85em;text-transform:uppercase;letter-spacing:.06em;color:#888;margin:0 0 8px}"
    ".activity-list{list-style:none;margin:0;padding:0;font-size:.82em}"
    ".activity-list li{display:flex;gap:6px;padding:5px 0;border-bottom:1px solid #1e1e1e;align-items:center}"
    ".activity-list li:last-child{border-bottom:none}"
    ".aj-q{color:#aaa;width:90px;flex-shrink:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:.8em}"
    ".aj-st{width:64px;flex-shrink:0;text-align:center;font-size:.72em;padding:2px 4px;border-radius:3px}"
    ".st-succeeded{background:#1a3a1a;color:#9f9}"
    ".st-dead_letter,.st-failed{background:#3a1a1a;color:#f99}"
    ".st-retry_wait{background:#3a2a0a;color:#fb7}"
    ".st-cancelled{background:#2a2a2a;color:#888}"
    ".aj-sku{color:#4a8ade;font-family:monospace;font-size:.78em;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
    ".aj-ts{color:#555;white-space:nowrap;font-size:.72em}"
    ".start-links{display:flex;flex-direction:column;gap:5px}"
    ".start-links a{color:#7fbfff;text-decoration:none;font-size:.88em;padding:7px 10px;"
    "  background:#1a1a2a;border-radius:6px;border:1px solid #2a2a3a}"
    ".start-links a:hover{background:#2a2a4a;color:#fff}"
    ".pm-wrap{margin-top:14px;border:1px solid #2a2a3a;border-radius:8px;overflow:hidden;"
    "  display:flex;flex-direction:column;height:320px}"
    ".pm-header{background:#1a1a2a;padding:7px 12px;font-size:.75em;text-transform:uppercase;"
    "  letter-spacing:.08em;color:#888;border-bottom:1px solid #2a2a3a;flex-shrink:0}"
    ".pm-messages{flex:1;overflow-y:auto;padding:10px;display:flex;flex-direction:column;gap:7px}"
    ".pm-msg{max-width:92%;padding:7px 10px;border-radius:7px;font-size:.83em;line-height:1.4;"
    "  white-space:pre-wrap;word-break:break-word}"
    ".pm-msg.user{align-self:flex-end;background:#1a3a5a;color:#cce}"
    ".pm-msg.assistant{align-self:flex-start;background:#1a1a2a;color:#ccc;border:1px solid #2a2a3a}"
    ".pm-typing{padding:7px 12px;font-size:.8em;color:#555;display:none;"
    "  animation:pmPulse 1.2s ease-in-out infinite;flex-shrink:0}"
    "@keyframes pmPulse{0%,100%{opacity:.3}50%{opacity:.9}}"
    ".pm-input-row{display:flex;gap:6px;padding:7px 8px;border-top:1px solid #2a2a3a;"
    "  background:#111;flex-shrink:0}"
    ".pm-input-row input{flex:1;margin:0;font-size:.87em}"
    ".pm-toast{position:fixed;bottom:16px;right:16px;background:#1a2a3a;"
    "  border:1px solid #2a4a6a;border-radius:8px;padding:12px 14px;"
    "  font-size:.84em;color:#ccc;max-width:300px;z-index:1000;"
    "  box-shadow:0 4px 12px rgba(0,0,0,.4);animation:fadeIn .2s ease}"
    "@keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}"
    ".pm-toast .tlabel{color:#888;font-size:.76em;text-transform:uppercase;letter-spacing:.05em;"
    "  margin-bottom:5px}"
    ".pm-toast .tbody{margin-bottom:8px;line-height:1.4;word-break:break-word}"
    ".pm-toast .tbtns{display:flex;gap:6px}"
    ".pm-toast .btn-ok{background:#1a4a1a;color:#7f7;border:none;border-radius:4px;"
    "  padding:5px 10px;cursor:pointer;font-size:.8em}"
    ".pm-toast .btn-no{background:#2a2a2a;color:#888;border:none;border-radius:4px;"
    "  padding:5px 10px;cursor:pointer;font-size:.8em}"
)

_HOME_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TGW Home</title>
{static_head}
<style>{home_css}</style>
</head>
<body>
<h2>TGW Dashboard</h2>

<div class="section">
  <div class="section-label">System Health <span id="worker-chip"></span></div>
  <div class="chips" id="health-strip"><span style="color:#555">Loading…</span></div>
</div>

<div class="section">
  <div class="section-label">Action Queue</div>
  <div class="card-grid" id="action-cards"><span style="color:#555">Loading…</span></div>
</div>

<div class="section">
  <div class="section-label">Quick Intake</div>
  <div class="intake-row">
    <input id="intake-sku" type="text" placeholder="Enter SKU (e.g. tgw20260614…)"
           autocomplete="off" spellcheck="false">
    <button class="btn-sm" onclick="goIntake()">Open Form</button>
  </div>
</div>

<div class="two-col">
  <div>
    <h3 class="subsec">Recent Activity</h3>
    <div id="activity"><span style="color:#555">Loading…</span></div>
  </div>
  <div>
    <h3 class="subsec">Start Here</h3>
    <div class="start-links">
      <a href="/form/items">Browse Inventory</a>
      <a href="/form/bulk">Bulk Edit</a>
      <a href="/form/todos">Open Todos</a>
      <a href="/form/suggest">Add Suggestion</a>
    </div>
    <div class="pm-wrap" id="pm-chat">
      <div class="pm-header">PM Chat</div>
      <div class="pm-messages" id="pm-messages"></div>
      <div class="pm-typing" id="pm-typing">PM is thinking…</div>
      <div class="pm-input-row">
        <input id="pm-input" type="text" placeholder="Ask the PM…" autocomplete="off">
        <button class="btn-sm" id="pm-send" onclick="pmSend()">Send</button>
      </div>
    </div>
  </div>
</div>

{static_foot}
<script>
window.TGW_API_KEY = {api_key_json};

function timeAgo(iso) {{
  if (!iso) return '';
  var s = Math.floor((Date.now() - new Date(iso)) / 1000);
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm';
  if (s < 86400) return Math.floor(s / 3600) + 'h';
  return Math.floor(s / 86400) + 'd';
}}

function showDetail(name, detail) {{
  alert(name + ': ' + (detail || 'ok'));
}}

async function fetchJ(url) {{
  try {{
    var r = await fetch(url, {{headers: authHeaders()}});
    var d = await r.json().catch(function() {{ return {{}}; }});
    return r.ok ? d : (d.detail && typeof d.detail === 'object' ? d.detail : d);
  }} catch(e) {{ return null; }}
}}

function renderHealth(data) {{
  var el = document.getElementById('health-strip');
  if (!data || !data.checks) {{
    el.innerHTML = '<span class="chip err-chip">health unavailable</span>';
    return;
  }}
  var html = '';
  data.checks.forEach(function(c) {{
    var cls = c.ok ? 'ok-chip' : 'err-chip';
    var tip = escapeHtml(c.detail || (c.ok ? 'ok' : 'fail'));
    html += '<button class="chip ' + cls + '" title="' + tip + '"' +
            ' onclick="showDetail(' + JSON.stringify(c.check) + ',' + JSON.stringify(c.detail || '') + ')">' +
            escapeHtml(c.check) + '</button>';
  }});
  el.innerHTML = html;
}}

function renderDashboard(data) {{
  var el = document.getElementById('action-cards');
  if (!data || !data.ok) {{
    el.innerHTML = '<span style="color:#f77;font-size:.85em">dashboard unavailable</span>';
    return;
  }}
  var cards = [
    {{key:'blocked_count',     label:'Blocked',        href:'/form/needs-review', cls:function(v){{return v>0?'err':'';}}}},
    {{key:'needs_review',      label:'Pending Drafts', href:'/form/drafts',       cls:function(v){{return v>0?'alert':'';}}}},
    {{key:'pending_offers',    label:'Pending Offers', href:'/form/offers',       cls:function(v){{return v>0?'info':'';}}}},
    {{key:'needs_photos',      label:'Need Photos',    href:'/form/items',        cls:function(v){{return v>0?'alert':'';}}}},
    {{key:'has_revision_draft',label:'Revision Drafts',href:'/form/revisions',    cls:function(v){{return v>0?'info':'';}}}},
    {{key:'dead_letter_count', label:'Dead Letters',   href:'/form/pipeline',     cls:function(v){{return v>0?'err':'';}}}},
    {{key:'ready_count',       label:'Ready to List',  href:'/form/items',        cls:function(v){{return v>0?'ok':'';}}}},
  ];
  var html = '';
  cards.forEach(function(c) {{
    var v = data[c.key];
    var disp = (v === null || v === undefined) ? '?' : v;
    var extra = (v !== null && v !== undefined) ? c.cls(v) : '';
    var cls = 'acard' + (extra ? ' ' + extra : '');
    if (c.href) {{
      html += '<a class="' + cls + '" href="' + c.href + '">' +
              '<div class="count">' + disp + '</div>' +
              '<div class="alabel">' + escapeHtml(c.label) + '</div></a>';
    }} else {{
      html += '<div class="' + cls + '">' +
              '<div class="count">' + disp + '</div>' +
              '<div class="alabel">' + escapeHtml(c.label) + '</div></div>';
    }}
  }});
  el.innerHTML = html;

  var wh = data.worker_health || {{}};
  if (wh.total !== undefined) {{
    var allOk = wh.up >= 0 && wh.up === wh.total;
    var wCls = wh.up < 0 ? 'err-chip' : (allOk ? 'ok-chip' : 'err-chip');
    var wLabel = wh.up < 0 ? 'Workers ?' : ('Workers ' + wh.up + '/' + wh.total);
    document.getElementById('worker-chip').innerHTML =
      '<span class="chip ' + wCls + '" style="font-size:.75em;padding:4px 8px">' + wLabel + '</span>';
  }}
}}

function renderActivity(data) {{
  var el = document.getElementById('activity');
  if (!data || !data.ok) {{
    el.innerHTML = '<span style="color:#888;font-size:.85em">activity unavailable</span>';
    return;
  }}
  if (!data.jobs || !data.jobs.length) {{
    el.innerHTML = '<span style="color:#555;font-size:.85em">No recent activity.</span>';
    return;
  }}
  var html = '<ul class="activity-list">';
  data.jobs.forEach(function(j) {{
    var sc = 'st-' + (j.state || '').replace(/_/g, '_');
    html += '<li>' +
      '<span class="aj-q" title="' + escapeHtml(j.queue_name) + '">' + escapeHtml(j.queue_name) + '</span>' +
      '<span class="aj-st ' + sc + '">' + escapeHtml(j.state || '') + '</span>' +
      '<span class="aj-sku">' + escapeHtml(j.sku || '') + '</span>' +
      '<span class="aj-ts">' + timeAgo(j.finished_at) + '</span>' +
      '</li>';
  }});
  el.innerHTML = html + '</ul>';
}}

function goIntake() {{
  var sku = document.getElementById('intake-sku').value.trim();
  if (sku) window.location = '/form/intake/' + encodeURIComponent(sku);
}}
document.getElementById('intake-sku').addEventListener('keydown', function(e) {{
  if (e.key === 'Enter') goIntake();
}});

Promise.all([
  fetchJ('/api/health').then(renderHealth),
  fetchJ('/api/dashboard').then(renderDashboard),
  fetchJ('/api/activity').then(renderActivity),
]);

// ---------------------------------------------------------------------------
// PM Chat
// ---------------------------------------------------------------------------
var PM_HK = 'tgw-pm-h';
var pmHistory = [];

function pmLoad() {{
  try {{ var h = sessionStorage.getItem(PM_HK); if (h) pmHistory = JSON.parse(h); }} catch(e) {{}}
  pmRender();
}}

function pmSave() {{
  try {{ sessionStorage.setItem(PM_HK, JSON.stringify(pmHistory.slice(-20))); }} catch(e) {{}}
}}

function pmRender() {{
  var el = document.getElementById('pm-messages');
  if (!pmHistory.length) {{
    el.innerHTML = '<div style="color:#444;font-size:.82em;text-align:center;padding:18px 6px">'
      + 'Ask: what needs doing? how many dead letters? how many items staged?</div>';
    return;
  }}
  var html = '';
  pmHistory.forEach(function(m) {{
    html += '<div class="pm-msg ' + (m.role==='user'?'user':'assistant') + '">'
      + escapeHtml(m.content) + '</div>';
  }});
  el.innerHTML = html;
  el.scrollTop = el.scrollHeight;
}}

async function pmSend() {{
  var inp = document.getElementById('pm-input');
  var msg = inp.value.trim();
  if (!msg) return;
  inp.value = '';
  pmHistory.push({{role:'user', content:msg}});
  pmRender(); pmSave();
  document.getElementById('pm-typing').style.display = '';
  var btn = document.getElementById('pm-send');
  btn.disabled = true;
  try {{
    var r = await fetch('/api/pm/chat', {{
      method: 'POST',
      headers: Object.assign({{'Content-Type':'application/json'}}, authHeaders()),
      body: JSON.stringify({{message:msg, history:pmHistory.slice(-9,-1)}}),
    }});
    var d = await r.json().catch(function(){{return {{}};}});
    var txt = d.message || d.detail || '(no response)';
    pmHistory.push({{role:'assistant', content:txt}});
    pmRender(); pmSave();
    if (d.actions) d.actions.forEach(function(a) {{ if (a.type && a.type!=='none') pmToast(a); }});
  }} catch(e) {{
    pmHistory.push({{role:'assistant', content:'Error: '+e.message}});
    pmRender(); pmSave();
  }} finally {{
    document.getElementById('pm-typing').style.display = 'none';
    btn.disabled = false;
    document.getElementById('pm-messages').scrollTop = 9999;
  }}
}}

function pmDismiss(btn) {{ var t=btn.parentNode; while(t&&!t.classList.contains('pm-toast'))t=t.parentNode; if(t)t.remove(); }}
function pmToast(action) {{
  var el = document.createElement('div');
  el.className = 'pm-toast';
  var label = action.type==='add_todo' ? 'Add Todo' : action.type==='add_suggestion' ? 'Add Suggestion' : 'Action';
  var body = action.type==='add_todo'
    ? '['+escapeHtml(action.agent||'?')+' p'+(action.priority||50)+'] '+escapeHtml(action.body||'')
    : escapeHtml(action.text||'');
  el.innerHTML = '<div class="tlabel">'+label+'</div>'
    +'<div class="tbody">'+body+'</div>'
    +'<div class="tbtns">'
    +'<button class="btn-ok" onclick="pmConfirm(this)">Confirm</button>'
    +'<button class="btn-no" onclick="pmDismiss(this)">Dismiss</button>'
    +'</div>';
  el.dataset.action = JSON.stringify(action);
  document.body.appendChild(el);
  setTimeout(function(){{ if(el.parentNode) el.remove(); }}, 30000);
}}

async function pmConfirm(btn) {{
  var toast = btn.closest('.pm-toast');
  var action;
  try {{ action = JSON.parse(toast.dataset.action); }} catch(e) {{ toast.remove(); return; }}
  btn.disabled = true; btn.textContent = '…';
  try {{
    var r = await fetch('/api/pm/action', {{
      method:'POST',
      headers: Object.assign({{'Content-Type':'application/json'}}, authHeaders()),
      body: JSON.stringify(action),
    }});
    var d = await r.json().catch(function(){{return {{}};}});
    if (d.ok) {{
      toast.innerHTML = '<div style="color:#7f7;font-size:.85em">'+(d.message||'Done')+'</div>';
    }} else {{
      toast.innerHTML = '<div style="color:#f77;font-size:.85em">Error: '
        +escapeHtml(d.detail||'failed')+'</div>';
    }}
    setTimeout(function(){{ if(toast.parentNode) toast.remove(); }}, 4000);
  }} catch(e) {{
    toast.innerHTML = '<div style="color:#f77;font-size:.85em">Network error</div>';
    setTimeout(function(){{ if(toast.parentNode) toast.remove(); }}, 4000);
  }}
}}

document.getElementById('pm-input').addEventListener('keydown', function(e) {{
  if (e.key==='Enter' && !e.shiftKey) {{ e.preventDefault(); pmSend(); }}
}});
pmLoad();
</script>
</body>
</html>
"""


@app.get("/form/home")
def home_form():
    """Home dashboard — health strip, action cards, quick intake, activity feed.
    No Bearer auth (network trust); JS embeds the key for API calls."""
    from fastapi.responses import HTMLResponse

    html = _HOME_HTML.format(
        static_head=_STATIC_HEAD,
        static_foot=_STATIC_FOOT,
        home_css=_HOME_EXTRA_CSS,
        api_key_json=json.dumps(""),
    )
    return HTMLResponse(html, headers={"Cache-Control": "no-store, no-cache"})


# ---------------------------------------------------------------------------
# GET /form/links — external links hub (PP-EDITOR-001 Phase 3e)
# ---------------------------------------------------------------------------

_LINKS_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TGW Links</title>
{static_head}
<style>
.links-section{{margin-bottom:22px}}
.section-label{{font-size:.75em;text-transform:uppercase;letter-spacing:.08em;
  color:#666;margin-bottom:8px}}
.links-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}}
@media(min-width:500px){{.links-grid{{grid-template-columns:repeat(3,1fr)}}}}
@media(min-width:760px){{.links-grid{{grid-template-columns:repeat(4,1fr)}}}}
.lcard{{background:#1a1a1a;border:1px solid #333;border-radius:8px;
  padding:12px 10px;text-decoration:none;color:inherit;display:block;
  transition:border-color .15s,background .15s}}
.lcard:hover{{border-color:#555;background:#222}}
.lcard-title{{font-size:.9em;font-weight:600;color:#ccc;margin-bottom:3px}}
.lcard-desc{{font-size:.76em;color:#666;line-height:1.3}}
.lcard.ebay{{border-color:#2a3a1a}}.lcard.ebay:hover{{border-color:#4a7a3a}}
.lcard.ebay .lcard-title{{color:#9d9}}
.lcard.ai{{border-color:#1a2a3a}}.lcard.ai:hover{{border-color:#3a6a9a}}
.lcard.ai .lcard-title{{color:#9bf}}
.lcard.infra{{border-color:#2a2a1a}}.lcard.infra:hover{{border-color:#6a6a3a}}
.lcard.infra .lcard-title{{color:#dd9}}
.lcard.research{{border-color:#2a1a2a}}.lcard.research:hover{{border-color:#6a3a6a}}
.lcard.research .lcard-title{{color:#c9c}}
</style>
</head>
<body>
<h2>External Links</h2>

<div class="links-section">
  <div class="section-label">eBay</div>
  <div class="links-grid">
    <a href="https://www.ebay.com/sh/ovw" target="_blank" rel="noopener noreferrer" class="lcard ebay">
      <div class="lcard-title">Seller Hub</div>
      <div class="lcard-desc">Overview &amp; insights</div>
    </a>
    <a href="https://www.ebay.com/sh/lst/active" target="_blank" rel="noopener noreferrer" class="lcard ebay">
      <div class="lcard-title">Active Listings</div>
      <div class="lcard-desc">Manage live inventory</div>
    </a>
    <a href="https://www.ebay.com/sh/ord" target="_blank" rel="noopener noreferrer" class="lcard ebay">
      <div class="lcard-title">Orders</div>
      <div class="lcard-desc">Sales &amp; fulfilment</div>
    </a>
    <a href="https://messages.ebay.com/" target="_blank" rel="noopener noreferrer" class="lcard ebay">
      <div class="lcard-title">Messages / Offers</div>
      <div class="lcard-desc">Buyer messages &amp; best offers</div>
    </a>
    <a href="https://www.ebay.com/sh/returns" target="_blank" rel="noopener noreferrer" class="lcard ebay">
      <div class="lcard-title">Returns</div>
      <div class="lcard-desc">Open return cases</div>
    </a>
    <a href="https://www.ebay.com/sh/perf/listing" target="_blank" rel="noopener noreferrer" class="lcard ebay">
      <div class="lcard-title">Performance</div>
      <div class="lcard-desc">Seller level &amp; metrics</div>
    </a>
    <a href="https://www.ebay.com/sh/mkt/promotions" target="_blank" rel="noopener noreferrer" class="lcard ebay">
      <div class="lcard-title">Promotions</div>
      <div class="lcard-desc">Sale events &amp; offers</div>
    </a>
    <a href="https://www.ebay.com/sh/reports/fee-statement" target="_blank" rel="noopener noreferrer" class="lcard ebay">
      <div class="lcard-title">Fees</div>
      <div class="lcard-desc">Invoices &amp; fee statements</div>
    </a>
  </div>
</div>

<div class="links-section">
  <div class="section-label">AI / ML</div>
  <div class="links-grid">
    <a href="https://aistudio.google.com/" target="_blank" rel="noopener noreferrer" class="lcard ai">
      <div class="lcard-title">Google AI Studio</div>
      <div class="lcard-desc">Gemini prompt playground</div>
    </a>
    <a href="https://openrouter.ai/settings/overview" target="_blank" rel="noopener noreferrer" class="lcard ai">
      <div class="lcard-title">OpenRouter</div>
      <div class="lcard-desc">Dashboard &amp; usage</div>
    </a>
    <a href="https://console.anthropic.com/" target="_blank" rel="noopener noreferrer" class="lcard ai">
      <div class="lcard-title">Anthropic Console</div>
      <div class="lcard-desc">Claude API &amp; usage</div>
    </a>
  </div>
</div>

<div class="links-section">
  <div class="section-label">Infrastructure</div>
  <div class="links-grid">
    <a href="https://login.tailscale.com/admin/" target="_blank" rel="noopener noreferrer" class="lcard infra">
      <div class="lcard-title">Tailscale</div>
      <div class="lcard-desc">VPN admin &amp; devices</div>
    </a>
    <a href="https://github.com/trader-grim/trader-grims-warehouse" target="_blank" rel="noopener noreferrer" class="lcard infra">
      <div class="lcard-title">GitHub Repo</div>
      <div class="lcard-desc">Source code &amp; history</div>
    </a>
  </div>
</div>

<div class="links-section">
  <div class="section-label">Research</div>
  <div class="links-grid">
    <a href="https://www.ebay.com/sch/i.html?LH_Sold=1&LH_Complete=1&_sop=13" target="_blank" rel="noopener noreferrer" class="lcard research">
      <div class="lcard-title">eBay Sold Listings</div>
      <div class="lcard-desc">Completed sales search</div>
    </a>
    <a href="https://www.discogs.com/sell/list" target="_blank" rel="noopener noreferrer" class="lcard research">
      <div class="lcard-title">Discogs Marketplace</div>
      <div class="lcard-desc">Vinyl &amp; media pricing</div>
    </a>
  </div>
</div>

{static_foot}
</body>
</html>
"""


@app.get("/form/links")
def links_form():
    """External links hub — eBay, AI/ML, infrastructure, research. No auth, no API calls."""
    from fastapi.responses import HTMLResponse

    return HTMLResponse(
        _LINKS_HTML.format(
            static_head=_STATIC_HEAD,
            static_foot=_STATIC_FOOT,
        )
    )


# ---------------------------------------------------------------------------
# GET /docs  /docs/{path}  — vault markdown renderer (PP-EDITOR-001 Phase 3f)
# ---------------------------------------------------------------------------

# Directories shown in sidebar (in display order); values relative to vault root.
_DOCS_NAV: list[tuple[str, str]] = [
    ("Runbooks", "reference/runbooks"),
    ("Reference", "reference"),
    ("Plan", "plan"),
]

# Reference sub-dirs excluded from the flat "Reference" section.
_DOCS_REF_SKIP = {"runbooks", "research"}

_DOCS_EXTRA_CSS = (
    ".docs-layout{display:grid;grid-template-columns:220px 1fr;gap:0;min-height:calc(100vh - 40px)}"
    "@media(max-width:640px){.docs-layout{grid-template-columns:1fr}}"
    ".docs-sidebar{border-right:1px solid #333;padding:10px 10px 20px;overflow-y:auto;"
    "  max-height:calc(100vh - 40px);position:sticky;top:0}"
    "@media(max-width:640px){.docs-sidebar{border-right:none;border-bottom:1px solid #333;"
    "  max-height:none;position:static}}"
    ".docs-section{margin-bottom:14px}"
    ".docs-sec-label{font-size:.7em;text-transform:uppercase;letter-spacing:.1em;color:#555;"
    "  padding:0 4px;margin-bottom:4px}"
    ".docs-link{display:block;padding:4px 6px;border-radius:4px;font-size:.8em;color:#999;"
    "  text-decoration:none;line-height:1.35;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
    ".docs-link:hover{background:#2a2a3a;color:#ccc}"
    ".docs-link.active{background:#1a3060;color:#9bf;font-weight:600}"
    ".docs-content{padding:10px 16px;min-width:0}"
    ".docs-content h1{font-size:1.2em;border-bottom:1px solid #333;padding-bottom:6px;margin-top:4px}"
    ".docs-content h2{font-size:1.05em;margin-top:20px;color:#9bf}"
    ".docs-content h3{font-size:.95em;color:#aaa;margin-top:14px}"
    ".docs-content h4,.docs-content h5{font-size:.88em;color:#888;margin-top:10px}"
    ".docs-content p{line-height:1.6;margin:8px 0;font-size:.88em}"
    ".docs-content ul,.docs-content ol{padding-left:18px;font-size:.88em;line-height:1.6}"
    ".docs-content li{margin:2px 0}"
    ".docs-content code{font-family:monospace;background:#1e1e1e;border:1px solid #333;"
    "  border-radius:3px;padding:1px 4px;font-size:.85em;color:#bfb}"
    ".docs-content pre{background:#111;border:1px solid #333;border-radius:6px;padding:10px;"
    "  overflow-x:auto;margin:8px 0}"
    ".docs-content pre code{background:none;border:none;padding:0;font-size:.82em;color:#ccc}"
    ".docs-content table{border-collapse:collapse;font-size:.82em;width:100%;margin:8px 0}"
    ".docs-content th,.docs-content td{border:1px solid #333;padding:5px 8px;text-align:left;"
    "  vertical-align:top}"
    ".docs-content th{background:#1a1a2a;color:#aaa}"
    ".docs-content blockquote{border-left:3px solid #444;margin:8px 0 8px 0;padding:4px 12px;"
    "  color:#888;font-style:italic}"
    ".docs-content a{color:#4a8ade}"
    ".docs-content hr{border:none;border-top:1px solid #333;margin:14px 0}"
    ".docs-404{padding:20px;color:#888;font-size:.9em}"
)


def _docs_plan_binding() -> dict[str, str]:
    """Return the only public Plan-docs source: a pinned clean materialization."""
    from tgw.plan_graph import approved_plan_binding

    if _cfg.get("plan_projection_path") is not None:
        raise ValueError("CANONICAL_PLAN_CONTEXT_REQUIRED: docs are served by registered tgw-context on tgw-lib")

    return approved_plan_binding(
        Path(_cfg.get("standalone_plan_root") or "/opt/TGW/library/plans"),
        approved_plan_commit=_cfg.get("plan_approved_commit"),
        approved_solution_hash=_cfg.get("plan_approved_solution_hash"),
        git_path=str(_cfg.get("plan_git_path") or "git"),
    )


def _vault_root() -> Path:
    """Return the pinned standalone Plan docs root; never the legacy source vault."""
    return Path(_docs_plan_binding()["plan_root"])


def _list_docs_sections() -> list[tuple[str, list[tuple[str, str]]]]:
    """Return [(section_label, [(rel_path, display_name), ...]), ...]."""
    vault = _vault_root()
    result = []
    for label, rel_dir in _DOCS_NAV:
        d = vault / rel_dir
        if not d.exists():
            continue
        files = []
        for f in sorted(d.iterdir()):
            if not f.is_file() or f.suffix.lower() != ".md":
                continue
            rel = f.relative_to(vault).as_posix()
            display = f.stem.replace("-", " ").replace("_", " ")
            files.append((rel, display))
        if files:
            result.append((label, files))
    return result


def _docs_sidebar_html(sections: list[tuple[str, list[tuple[str, str]]]], current: str) -> str:
    import html as _html

    parts: list[str] = ['<nav class="docs-sidebar">']
    for label, files in sections:
        parts.append(f'<div class="docs-section"><div class="docs-sec-label">{_html.escape(label)}</div>')
        for rel, display in files:
            active = " active" if rel == current else ""
            parts.append(f'<a class="docs-link{active}" href="/docs/{rel}" title="{_html.escape(rel)}">{_html.escape(display)}</a>')
        parts.append("</div>")
    parts.append("</nav>")
    return "".join(parts)


def _docs_page_html(title: str, body_html: str, sidebar_html: str, plan_commit: str) -> str:
    import html as _html

    return (
        "<!DOCTYPE html><html lang='en'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_html.escape(title)} — TGW Docs</title>"
        + _STATIC_HEAD
        + f"<style>{_DOCS_EXTRA_CSS}</style>"
        + "</head><body>"
        + f'<div class="docs-plan-binding" data-plan-commit="{_html.escape(plan_commit)}">'
        + f"Approved Plan: <code>{_html.escape(plan_commit)}</code></div>"
        + '<div class="docs-layout">'
        + sidebar_html
        + f'<main class="docs-content">{body_html}</main>'
        + "</div>"
        + _STATIC_FOOT
        + "</body></html>"
    )


@app.get("/docs")
def docs_index_redirect():
    """Redirect /docs to the runbook index."""
    from fastapi.responses import RedirectResponse

    try:
        _docs_plan_binding()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"approved Plan docs unavailable: {exc}")

    return RedirectResponse(url="/docs/reference/runbooks/INDEX.md", status_code=302)


@app.get("/docs/{path:path}")
def docs_page(path: str):
    """Render a vault markdown file as HTML. No Bearer auth (network trust).

    Path traversal: only files within the plan vault root are served.
    Only .md extensions are accepted.
    """
    import mistune
    from fastapi.responses import HTMLResponse

    # Only .md files
    if not path.lower().endswith(".md"):
        raise HTTPException(status_code=404, detail="only .md files are served here")

    try:
        binding = _docs_plan_binding()
        vault = Path(binding["plan_root"])
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"approved Plan docs unavailable: {exc}")
    # Resolve to absolute path and check it stays within vault
    try:
        resolved = (vault / path).resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid path")

    vault_resolved = vault.resolve()
    try:
        resolved.relative_to(vault_resolved)
    except ValueError:
        raise HTTPException(status_code=403, detail="path outside vault")

    if not resolved.exists():
        raise HTTPException(status_code=404, detail=f"doc not found: {path}")

    try:
        content = resolved.read_text(encoding="utf-8")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"read error: {exc}")

    md = mistune.create_markdown(
        escape=True,
        plugins=["table", "strikethrough"],
    )
    body_html = md(content)

    sections = _list_docs_sections()
    sidebar_html = _docs_sidebar_html(sections, path)
    title = Path(path).stem.replace("-", " ").replace("_", " ")

    return HTMLResponse(_docs_page_html(title, body_html, sidebar_html, binding["plan_commit"]))


# ---------------------------------------------------------------------------
# POST /webhooks/ebay/notification — eBay push notification (no Bearer auth)
# ---------------------------------------------------------------------------


@app.post("/webhooks/ebay/notification")
async def ebay_notification_webhook(request: Request) -> Dict[str, Any]:
    """
    Receive eBay FixedPriceTransaction push notifications.
    No Bearer auth — eBay can't send it. Signature-verified instead.
    Always returns {"ack": "Success"} to prevent eBay retry storms.
    """
    from .apis.ebay.notifications import parse_sold_notification, verify_notification_signature
    from .ebay.pull import mark_item_sold as _mark_item_sold

    body = await request.body()
    log.debug("ebay_webhook: received %d bytes", len(body))

    if not verify_notification_signature(body, _cfg):
        log.warning("ebay_webhook: invalid signature — rejected")
        raise HTTPException(status_code=400, detail="invalid signature")

    event = parse_sold_notification(body)
    if event is None:
        # Ping/test notification from eBay — just ack it
        log.info("ebay_webhook: non-sold notification (ping or unknown type)")
        return {"ack": "Success"}

    listing_id = event["listing_id"]
    index = _get_listing_index()
    json_path = index.get(listing_id)

    # Cache miss — maybe a newly listed item; do a targeted scan
    if json_path is None or not json_path.exists():
        log.info("ebay_webhook: listing %s not in index — rebuilding", listing_id)
        _listing_index_built_at_reset()
        index = _get_listing_index()
        json_path = index.get(listing_id)

    if json_path is None or not json_path.exists():
        log.warning("ebay_webhook: no local item for listing_id=%s — acking anyway", listing_id)
        return {"ack": "Success"}

    synced_at = datetime.now(timezone.utc).isoformat()
    try:
        did_mark = _mark_item_sold(
            json_path,
            order_id=event["order_id"],
            buyer=event["buyer"],
            sale_price=event["sale_price"],
            quantity=event["quantity"],
            sale_date=event["sale_date"],
            synced_at=synced_at,
            cfg=_cfg,
        )
        if did_mark:
            log.info("ebay_webhook: marked sold listing_id=%s", listing_id)
            try:
                state_machine.enqueue_catalog_rebuild("ebay_webhook_sold")
            except Exception:
                pass
    except Exception as exc:
        log.error("ebay_webhook: mark failed listing_id=%s: %s", listing_id, exc)

    return {"ack": "Success"}


@app.post("/api/cli", dependencies=[AUTH])
async def api_cli(request: Request):
    import subprocess

    body = await request.json()
    cmd = body.get("command", "")
    args = body.get("args", [])
    BLOCKED = {
        "update",
        "update-where",
        "update-title",
        "update-location",
        "update-verified",
        "update-status",
        "set-shipping",
        "bulk",
        "price-freeship",
        "hint",
        "data-scrub",
        "revise",
        "alt-text",
        "enqueue-sku",
        "requeue-identify",
        "resolve-legacy",
        "ready",
        "publish",
        "alt-text-batch",
        "ebay-pull",
        "import-sold-csv",
        "sku-migrate",
        "migrate-unblock",
        "migrate-restore",
        "restart-workers",
        "restart-ebay-token",
        "nix-bundle-usb",
        "set-context",
        "clear-context",
        "set-template",
        "create-item",
        "serve",
        "flake",
    }
    if cmd in BLOCKED:
        return {"ok": False, "error": f"{cmd} is write-protected"}
    fc = ["/opt/TGW/.venvironments/tgw/bin/tgw", cmd] + [str(a) for a in args]
    try:
        p = subprocess.run(fc, capture_output=True, text=True, timeout=30)
        return {"ok": True, "command": cmd, "exit_code": p.returncode, "stdout": p.stdout[:50000], "stderr": p.stderr[:5000]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timed out"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
