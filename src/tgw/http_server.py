"""
tgw.http_server — FastAPI HTTP service (tgw-http).

Exposes inventory and pipeline operations over HTTP on port 7373.
Shared API for MC console copyin operations and the Flutter app.

Auth: Bearer <api_key> — key stored in secrets_root/tgw-api-key.json
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import sqlite3
import subprocess
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Request, Security, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import DEFAULT_CONFIG, load_config
from .items import atomic_write_json, locationupdate
from .queue import state_machine
from .resolver import load_item_doc

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Worker pipeline tooltip text — sourced from TGW-Pipeline-Flow.md
_WORKER_TOOLTIPS: Dict[str, str] = {
    "token_refresh":     "OAuth token refresh via eBay API; fires when token expires within 30 min",
    "pm_intake":         "Reads plan-vault inbox notes → Ollama classifies → patches Master Plan",
    "catalog_rebuild":   "Rebuilds JSON catalog + SQLite + location tree from all ItemData",
    "thumbnail_gen":     "Generates SKU thumbnail from primary photo (Pillow)",
    "bundle_intake":     "Polls incoming/newitems/, creates item stubs, enqueues ai_identify",
    "multi_intake":      "Splits multi-item bundles into individual child SKUs",
    "ai_identify":       "Sends photo to Ollama vision model → extracts title, category, condition",
    "ebay_draft":        "Fetches eBay aspects, fills with AI (Qwen2.5), builds draft_listing block",
    "ebay_upload":       "Uploads photos to eBay EPS via Trading API → permanent picture URLs",
    "ebay_price":        "Browse API comps search → sets launch price + price_comps on offer",
    "ebay_stage":        "Inventory API upsert + creates UNPUBLISHED offer; item visible in Seller Hub",
    "ebay_publish":      "Publishes offer to eBay (manual trigger only); writes listing_id + reprice_schedule",
    "ebay_price_reducer":"Applies reprice schedule stages (launch → retail → move) via Inventory API",
    "ebay_sync":         "Fetches all eBay offers → syncs status back to item JSON every 6 h",
    "ebay_legacy_sync":  "GetMyeBaySelling + GetOrders via Trading API; marks sold items",
    "ebay_dole":         "Self-scheduling; publishes oldest ready items at configured dole rate",
    "ebay_sku_migrate":  "Batches Class A live listings: delist → rename SKU → relist (hourly)",
    "velocity_stats":    "Computes nightly category velocity stats for repricer tuning",
    "echo":              "No-op test worker — echoes payload and succeeds",
}

# Module-level state (set during lifespan startup)
# ---------------------------------------------------------------------------

_cfg: Dict[str, Any] = {}
_api_key: str = ""
_web_key: str = ""  # per-process session key embedded in form-page HTML; rotates on restart

_security = HTTPBearer()

# Listing index cache for webhook lookups: {listing_id: json_path}
_listing_index: Dict[str, Path] = {}
_listing_index_built_at: float = 0.0
_LISTING_INDEX_TTL = 600  # rebuild every 10 min

# Dashboard: pending_offers cache (eBay API is slow; 5 min TTL)
_pending_offers_cache: Optional[int] = None
_pending_offers_cache_at: float = 0.0
_PENDING_OFFERS_TTL = 300


def _get_listing_index() -> Dict[str, Path]:
    global _listing_index, _listing_index_built_at
    if time.time() - _listing_index_built_at > _LISTING_INDEX_TTL:
        from .workers.ebay_legacy_sync import _build_listing_index
        _listing_index = _build_listing_index(_cfg['itemdata_root'])
        _listing_index_built_at = time.time()
        log.info('ebay_webhook: listing index rebuilt (%d entries)', len(_listing_index))
    return _listing_index


def _listing_index_built_at_reset() -> None:
    global _listing_index_built_at
    _listing_index_built_at = 0.0

PIPELINE_ACTIONS = {
    "ai_identify",
    "ebay_draft",
    "ebay_upload",
    "ebay_price",
    "ebay_stage",
    "ebay_publish",
    "catalog_rebuild",
    "thumbnail_gen",
    "approve",
}


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _cfg, _api_key, _web_key
    _cfg = load_config(DEFAULT_CONFIG)
    state_machine.init(_cfg["postgres_dsn"])

    key_path: Path = _cfg["secrets_root"] / "tgw-api-key.json"
    if not key_path.exists():
        raise RuntimeError(f"API key file not found: {key_path}")
    _api_key = json.loads(key_path.read_text(encoding="utf-8"))["api_key"]
    _web_key = secrets.token_hex(32)

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

_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

def _require_auth(credentials: HTTPAuthorizationCredentials = Security(_security)) -> None:
    if credentials.credentials not in (_api_key, _web_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")


AUTH = Depends(_require_auth)


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

    if search:
        clauses.append("(title LIKE ? OR sku LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]
    if location:
        clauses.append("location = ?")
        params.append(location)
    if status_filter:
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
        f"SELECT sku, title, location, status, price, qty, image,"
        f" json_extract(data, '$.ebay_listing.listing_id') AS ebay_listing_id,"
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
                rows = con.execute(
                    "SELECT sku, title, location, data FROM catalog"
                    " WHERE json_extract(data, '$.revision_draft') IS NOT NULL"
                    " ORDER BY sku"
                ).fetchall()
            for row in rows:
                draft: Dict[str, Any] = {}
                try:
                    doc = json.loads(row["data"] or "{}")
                    draft = doc.get("revision_draft") or {}
                except Exception:
                    pass
                if not draft or not draft.get("delta"):
                    continue
                items.append({
                    "sku": row["sku"],
                    "title": row["title"] or "",
                    "location": row["location"] or "",
                    "draft": draft,
                })
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
            items.append({
                "sku": row["sku"],
                "title": draft.get("title") or row["title"] or "",
                "location": row["location"] or "",
                "status": row["status"] or "",
                "price": draft.get("price") if draft.get("price") is not None else row["price"],
                "condition": draft.get("condition") or doc.get("condition") or "",
                "category_id": draft.get("category_id") or doc.get("ebay_category_id") or "",
                "category_name": draft.get("category_name") or doc.get("ebay_category_name") or "",
                "quality": draft.get("quality") or {},
                "aspects_required_total": draft.get("aspects_required_total"),
                "aspects_required_filled": draft.get("aspects_required_filled"),
            })

    return {"ok": True, "items": items, "count": len(items)}


# ---------------------------------------------------------------------------
# GET /api/items/{sku} — full item detail
# ---------------------------------------------------------------------------

@app.get("/api/items/{sku}", dependencies=[AUTH])
def get_item(sku: str) -> Dict[str, Any]:
    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"sku not found: {sku}")

    item = load_item_doc(json_path)

    # Attach media file lists
    images, videos = [], []
    for p in sorted(json_path.parent.iterdir()):
        if not p.is_file():
            continue
        suf = p.suffix.lower()
        if suf in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
            images.append(p.name)
        elif suf in {".mp4", ".mov", ".mkv", ".webm"}:
            videos.append(p.name)
    item["_images"] = images
    item["_videos"] = videos

    # Attach recent queue job states for this SKU
    try:
        with psycopg2.connect(_cfg["postgres_dsn"]) as con:
            with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT queue_name, state, attempt_count,
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

@app.patch("/api/items/{sku}", dependencies=[AUTH])
def patch_item(sku: str, body: PatchBody) -> Dict[str, Any]:
    if "sku" in body.fields:
        raise HTTPException(status_code=400, detail="sku field is immutable")
    if not body.fields:
        raise HTTPException(status_code=400, detail="no fields provided")

    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"sku not found: {sku}")

    # Handle location specially — must keep location tree in sync
    location_value: Optional[str] = None
    if "location" in body.fields:
        location_value = body.fields.pop("location")

    # Atomic multi-field update: load → merge → write
    doc = load_item_doc(json_path)
    doc.update(body.fields)
    if "catalog_verified" not in body.fields:
        doc.pop("catalog_verified", None)
    atomic_write_json(json_path, doc, pretty=_cfg.get("pretty", True))

    if location_value is not None:
        result = locationupdate(_cfg, sku, location_value)
        if not result.get("ok"):
            log.warning("location tree update failed for %s: %s", sku, result)

    # Enqueue coalesced catalog rebuild
    try:
        state_machine.enqueue_job(
            queue_name="catalog_rebuild",
            payload={"reason": f"http_patch:{sku}"},
            dedupe_key="catalog_rebuild:pending",
            not_before=time.time() + 30,
            max_attempts=3,
        )
    except Exception:
        pass

    updated_keys = list(body.fields.keys())
    if location_value is not None:
        updated_keys.append("location")

    return {"ok": True, "sku": sku, "updated": updated_keys}


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
            state_machine.enqueue_job(
                queue_name="catalog_rebuild",
                payload={"reason": "http_bulk"},
                dedupe_key="catalog_rebuild:pending",
                not_before=time.time() + 30,
                max_attempts=3,
            )
        except Exception:
            pass
    return result


# ---------------------------------------------------------------------------
# POST /api/bulk/action — bulk pipeline actions from Flutter browse selection
# ---------------------------------------------------------------------------

_BULK_PIPELINE_ACTIONS = {"ai_identify", "ebay_price", "ebay_draft", "ebay_stage", "ebay_upload"}
_BULK_VALID_ACTIONS = _BULK_PIPELINE_ACTIONS | {"set_ready", "mark_sold", "delete"}


@app.post("/api/bulk/action", dependencies=[AUTH])
def bulk_action(body: BulkActionBody) -> Dict[str, Any]:
    """Fan out an action across a list of SKUs.

    Actions: ai_identify, ebay_price, ebay_draft, ebay_stage, set_ready,
    mark_sold, delete. Pipeline actions enqueue jobs; set_ready / mark_sold /
    delete write item JSON and enqueue a catalog rebuild.
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

    if body.action in ("mark_sold", "delete"):
        new_status = "Sold" if body.action == "mark_sold" else "deleted"
        done: List[str] = []
        errors: List[str] = []
        for sku in body.skus:
            json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
            if not json_path.exists():
                errors.append(f"{sku}: not found")
                continue
            try:
                doc = load_item_doc(json_path)
                doc["status"] = new_status
                if body.action == "delete":
                    doc["deleted_at"] = datetime.now().isoformat()
                atomic_write_json(json_path, doc, pretty=_cfg.get("pretty", True))
                done.append(sku)
            except Exception as exc:
                errors.append(f"{sku}: {exc}")
        try:
            state_machine.enqueue_job(
                queue_name="catalog_rebuild",
                payload={"reason": f"bulk_{body.action}"},
                dedupe_key="catalog_rebuild:pending",
                not_before=time.time() + 30,
                max_attempts=3,
            )
        except Exception:
            pass
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
            if body.action == "ai_identify":
                doc = load_item_doc(json_path)
                doc["ai_reidentify"] = True
                atomic_write_json(json_path, doc, pretty=_cfg.get("pretty", True))
            state_machine.enqueue_job(
                queue_name=body.action,
                payload={"sku": sku},
                dedupe_key=f"{body.action}:{sku}",
                max_attempts=5,
            )
            queued.append(sku)
        except Exception as exc:
            err = str(exc)
            if "unique" in err.lower() or "duplicate" in err.lower():
                skipped.append(sku)
            else:
                errors.append(f"{sku}: {err}")
    return {"ok": True, "count": len(queued), "queued": queued, "skipped": skipped, "errors": errors}


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

@app.post("/api/items/{sku}/action", dependencies=[AUTH])
def item_action(sku: str, body: ActionBody) -> Dict[str, Any]:
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
            doc = load_item_doc(json_path)
            doc["status"] = "Ready"
            atomic_write_json(json_path, doc, pretty=_cfg.get("pretty", True))
            try:
                state_machine.enqueue_job(
                    queue_name="catalog_rebuild",
                    payload={"reason": f"approve:{sku}"},
                    dedupe_key="catalog_rebuild:pending",
                    not_before=time.time() + 30,
                    max_attempts=3,
                )
            except Exception:
                pass
            return {"ok": True, "sku": sku, "action": "approve", "status": "Ready"}
        elif action == "catalog_rebuild":
            job_id = state_machine.enqueue_job(
                queue_name="catalog_rebuild",
                payload={"reason": f"manual:{sku}"},
                dedupe_key="catalog_rebuild:pending",
                not_before=time.time() + 5,
                max_attempts=3,
            )
        elif action == "ai_identify":
            # Clear ai_identified so the worker will run
            doc = load_item_doc(json_path)
            doc["ai_reidentify"] = True
            atomic_write_json(json_path, doc, pretty=_cfg.get("pretty", True))
            job_id = state_machine.enqueue_job(
                queue_name="ai_identify",
                payload={"sku": sku},
                dedupe_key=f"ai_identify:{sku}",
                max_attempts=3,
            )
        else:
            job_id = state_machine.enqueue_job(
                queue_name=action,
                payload={"sku": sku},
                dedupe_key=f"{action}:{sku}",
                max_attempts=5,
            )
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
                cur.execute(
                    "SELECT COUNT(*) FROM queue_jobs WHERE state = 'dead_letter'"
                )
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
                    SELECT queue_name, state, COUNT(*) AS count
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
    for row in rows:
        q = row["queue_name"]
        s = row["state"]
        by_queue.setdefault(q, {})[s] = row["count"]

    return {"ok": True, "queues": by_queue}


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
            ["systemctl", "show", "--no-pager",
             "--property=Id,ActiveState,SubState,MainPID",
             *units],
            capture_output=True, text=True, timeout=8,
        )
        # systemctl show outputs blank-line-separated blocks per unit
        block: Dict[str, str] = {}
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line:
                if block.get("Id"):
                    workers.append({
                        "unit": block["Id"],
                        "active": block.get("ActiveState", "unknown"),
                        "sub": block.get("SubState", "unknown"),
                        "pid": int(block["MainPID"]) if block.get("MainPID", "0").isdigit() and block["MainPID"] != "0" else None,
                    })
                block = {}
            else:
                k, _, v = line.partition("=")
                block[k] = v
        # flush last block
        if block.get("Id"):
            workers.append({
                "unit": block["Id"],
                "active": block.get("ActiveState", "unknown"),
                "sub": block.get("SubState", "unknown"),
                "pid": int(block["MainPID"]) if block.get("MainPID", "0").isdigit() and block["MainPID"] != "0" else None,
            })
    except Exception as exc:
        log.warning("system_workers: systemctl query failed: %s", exc)
        # Fall back: return all as unknown
        workers = [
            {"unit": u, "active": "unknown", "sub": "unknown", "pid": None}
            for u in units
        ]

    up = sum(1 for w in workers if w["active"] == "active")
    return {"ok": True, "workers": workers, "up": up, "total": len(units)}


# ---------------------------------------------------------------------------
# POST /api/jobs/{job_id}/requeue — re-enqueue a dead-letter job (Phase 3j)
# ---------------------------------------------------------------------------

@app.post("/api/jobs/{job_id}/requeue", dependencies=[AUTH])
def requeue_job(job_id: str) -> Dict[str, Any]:
    """Re-enqueue a dead-letter job with a fresh dedupe key so it can run again."""
    try:
        with psycopg2.connect(_cfg["postgres_dsn"]) as con:
            with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT job_id, queue_name, payload_json, state, max_attempts"
                    " FROM queue_jobs WHERE job_id = %s",
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
    sku = payload.get("sku") or job_id[:8]
    new_dedupe = f"{row['queue_name']}:{sku}:requeue:{int(time.time())}"

    try:
        new_job_id = state_machine.enqueue_job(
            queue_name=row["queue_name"],
            payload=payload,
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
# GET /api/locations — distinct locations from SQLite
# ---------------------------------------------------------------------------

@app.get("/api/locations", dependencies=[AUTH])
def list_locations() -> Dict[str, Any]:
    db_path = _cfg["sqlite_catalog_path"]
    if not db_path.exists():
        raise HTTPException(status_code=503, detail="SQLite catalog not built")

    con = _sqlite_conn()
    try:
        rows = con.execute(
            "SELECT DISTINCT location FROM catalog WHERE location != '' ORDER BY location"
        ).fetchall()
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

    fd, tmp_path = tempfile.mkstemp(suffix='.db', prefix='tgwcatalog_snapshot_')
    os.close(fd)
    src_con = sqlite3.connect(str(db_path))
    try:
        dst_con = sqlite3.connect(tmp_path)
        try:
            src_con.backup(dst_con)
        finally:
            dst_con.close()
    finally:
        src_con.close()

    background_tasks.add_task(os.unlink, tmp_path)
    return FileResponse(
        tmp_path,
        media_type='application/octet-stream',
        filename='tgwcatalog.db',
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
        result.append({
            "key":        key,
            "name":       grp.get("name", key),
            "size_class": grp.get("size_class", ""),
            "ai_hint":    grp.get("ai_hint", ""),
            "floor":      grp.get("pricing", {}).get("floor"),
            "typical_used": grp.get("pricing", {}).get("typical_used"),
        })
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

    doc.update(fields)
    atomic_write_json(json_path, doc, pretty=_cfg.get("pretty", True))

    try:
        state_machine.enqueue_job(
            queue_name="catalog_rebuild",
            payload={"reason": f"set_template:{sku}"},
            dedupe_key="catalog_rebuild:pending",
            not_before=time.time() + 30,
            max_attempts=3,
        )
    except Exception:
        pass

    return {"ok": True, "sku": sku, "template_key": body.template_key,
            "applied": fields, "group_name": grp.get("name", body.template_key)}


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

    doc = load_item_doc(json_path)
    doc["status"] = "deleted"
    doc["deleted_at"] = datetime.now().isoformat()
    atomic_write_json(json_path, doc, pretty=_cfg.get("pretty", True))

    try:
        state_machine.enqueue_job(
            queue_name="catalog_rebuild",
            payload={"reason": f"delete:{sku}"},
            dedupe_key="catalog_rebuild:pending",
            not_before=time.time() + 30,
            max_attempts=3,
        )
    except Exception:
        pass

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
    doc = load_item_doc(json_path)
    doc["photo_order"] = [n for n in body.order if n]
    atomic_write_json(json_path, doc, pretty=_cfg.get("pretty", True))
    try:
        state_machine.enqueue_job(
            queue_name="catalog_rebuild",
            payload={"reason": f"photo_order:{sku}"},
            dedupe_key="catalog_rebuild:pending",
            not_before=time.time() + 30,
            max_attempts=3,
        )
    except Exception:
        pass
    return {"ok": True, "sku": sku, "order": doc["photo_order"]}


# ---------------------------------------------------------------------------
# GET /api/items/{sku}/hint-trail — identification history
# ---------------------------------------------------------------------------

@app.get("/api/items/{sku}/hint-trail", dependencies=[AUTH])
def get_hint_trail(sku: str) -> Dict[str, Any]:
    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail=f"sku not found: {sku}")
    doc = load_item_doc(json_path)
    history = doc.get("identification_history", [])
    return {"ok": True, "sku": sku, "count": len(history), "history": history}


# ---------------------------------------------------------------------------
# GET /form/intake/{sku} — mobile intake form (HTML)
# ---------------------------------------------------------------------------

_STATIC_HEAD = (
    '<link rel="stylesheet" href="/static/tgw.css">'
    '<link rel="stylesheet" href="/static/nav.css">'
)
_STATIC_FOOT = (
    '<script src="/static/tgw.js"></script>'
    '<script src="/static/nav.js"></script>'
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
        api_key_json=json.dumps(_web_key),
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
  <button class="btn-action" id="btn-draft" onclick="triggerAction('ebay_draft')">Re-draft</button>
</div>
<div class="msg" id="action-msg"></div>

<button class="btn" onclick="submitForm()">Save</button>
<div style="margin-top:10px;text-align:center">
  <a class="detail-link" href="/form/items/{sku}">View detail &rarr;</a>
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
  var bd = document.getElementById('btn-draft');
  bi.disabled = true; bd.disabled = true;
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
    bi.disabled = false; bd.disabled = false;
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
    from fastapi.responses import HTMLResponse

    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        return HTMLResponse(f"<h2>SKU not found: {sku}</h2>", status_code=404)

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
        sel = 'selected' if c == cond_val else ''
        cond_opts += f'<option value="{c}" {sel}>{c if c else "— not set —"}</option>'

    weight = doc.get("weight_oz", "")
    barcode = doc.get("barcode", doc.get("upc", ""))
    ai_hint = doc.get("ai_hint", "")
    sku_short = sku[-9:]

    # Photo count from filesystem (server-side; JS keeps it live via polling)
    sku_dir = json_path.parent
    n_photos = sum(
        1 for p in sku_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    )
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
        api_key=_web_key,
        api_key_json=json.dumps(_web_key),
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
        api_key=_web_key,
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
    import re
    return list(dict.fromkeys(re.findall(r'PP-[A-Z0-9]+-\d+', body)))


def _render_todos_html(rows) -> str:
    """Build the todos dashboard HTML from open todo rows (grouped by agent)."""
    import html as _html

    head = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>TGW Todos</title>'
        + _STATIC_HEAD
        + '<style>' + _TODOS_EXTRA_CSS + '</style>'
        '</head><body>'
        '<h2>Open Todos</h2>'
    )
    if not rows:
        return head + '<div class="allclear">✓ All clear — no open todos.</div>' + _STATIC_FOOT + _TODOS_JS + '</body></html>'

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
        '<div class="todos-ctrl">'
        f'<label for="agent-sel">Agent:</label>'
        f'<select id="agent-sel">{filter_opts}</select>'
        '</div>',
        f'<div class="total">{len(rows)} open item(s)</div>',
    ]

    for agent, items in groups:
        agent_esc = _html.escape(str(agent))
        parts.append(
            f'<div class="agent" data-agent="{agent_esc}">'
            f'<h3>{agent_esc}</h3>'
            f'<span class="count">{len(items)} open</span></div>'
        )
        parts.append(
            f'<table data-agent="{agent_esc}">'
            '<tr><th>ID</th><th>P</th><th>Task</th><th>Src</th><th></th></tr>'
        )
        for it in items:
            body_raw = str(it.get("body", ""))
            body_esc = _html.escape(body_raw)
            pp_refs = _extract_pp_refs(body_raw)
            pp_badges = "".join(
                f'<a class="pp-badge" href="/docs/plan/TGW-Master-Plan.md" title="{r}">{r}</a>'
                for r in pp_refs
            )
            body_cell = (
                f'<div class="task-body" data-agent="{agent_esc}">{body_esc}</div>'
                f'<span class="task-expand"></span>'
                + (pp_badges if pp_badges else "")
            )
            parts.append(
                f'<tr data-agent="{agent_esc}">'
                f'<td class="id">#{_html.escape(str(it.get("id", "")))}</td>'
                f'<td class="p">{_html.escape(str(it.get("priority", "")))}</td>'
                f'<td>{body_cell}</td>'
                f'<td class="src">{_html.escape(str(it.get("source", "")))}</td>'
                f'<td><button class="copy-btn" data-body="{body_esc}" title="Copy task text">📋</button></td>'
                '</tr>'
            )
        parts.append('</table>')
    parts.append(_STATIC_FOOT)
    parts.append(_TODOS_JS)
    parts.append('</body></html>')
    return ''.join(parts)


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
            '<title>TGW Todos</title>'
            + _STATIC_HEAD
            + '<style>' + _TODOS_EXTRA_CSS + '</style>'
            '</head><body>'
            '<h2>Open Todos</h2>'
            f'<div class="msg err" style="display:block">todo store unavailable: {exc}</div>'
            + _STATIC_FOOT
            + '</body></html>'
        )
        return HTMLResponse(body, status_code=200)
    return HTMLResponse(_render_todos_html(rows))


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
        '<title>TGW Suggest</title>'
        + _STATIC_HEAD
        + '</head><body>'
        '<h2>Add Suggestion</h2>'
        '<form method="post" action="/form/suggest">'
        '<label>Suggestion — any punctuation is safe here</label>'
        '<textarea name="text" required autofocus placeholder="idea, task, note ..."></textarea>'
        '<button class="btn" type="submit">Add to SUGGESTIONS.md</button>'
        '</form>'
        + banner
        + _STATIC_FOOT
        + '</body></html>'
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

    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
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
    if Path(safe).suffix.lower() not in {
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov", ".mkv", ".webm"
    }:
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
.card-btns{display:flex;gap:4px;padding:5px 6px;background:#151515;border-top:1px solid #222}
.cbtn{flex:1;padding:5px 4px;border:none;border-radius:4px;cursor:pointer;font-size:.74em;
  font-weight:600;font-family:inherit;white-space:nowrap}
.cbtn-a{background:#1a3a1a;color:#7f7}.cbtn-a:hover{background:#1e4a1e}
.cbtn-p{background:#1a2a3a;color:#7af}.cbtn-p:hover{background:#1e3a4e}
.cbtn:disabled{opacity:.45;cursor:default}
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
.jtable{width:100%;border-collapse:collapse;font-size:.78em}
.jtable th{color:#666;text-align:left;padding:4px 6px;border-bottom:1px solid #2a2a2a;
  font-weight:normal;font-size:.75em;text-transform:uppercase}
.jtable td{padding:4px 6px;border-bottom:1px solid #1e1e1e;vertical-align:top}
.js-done{color:#7f7}.js-pending,.js-running{color:#fb7}
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
  <button class="chip" data-s="In Stock">In Stock</button>
  <button class="chip" data-s="Listed">Listed</button>
  <button class="chip" data-s="Staged">Staged</button>
  <button class="chip" data-s="Sold">Sold</button>
</div>
<div class="summary" id="sum"></div>
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
function setView(v){{
  _view=v;
  document.getElementById('vt-card').classList.toggle('active',v==='card');
  document.getElementById('vt-list').classList.toggle('active',v==='list');
  load(0);
}}
function _cardHtml(it){{
  const pf=parseFloat(it.price);const price=isNaN(pf)?'—':'$'+pf.toFixed(2);
  const sku=esc(it.sku);
  return `<div class="card"><a href="/form/items/${{it.sku}}" class="card-inner">
    <img class="thumb" src="/thumb/${{it.sku}}" loading="lazy" alt="" onerror="this.style.visibility='hidden'">
    <div class="card-body">
      <div class="card-sku">${{sku}}</div>
      <div class="card-title">${{esc(it.title||'')}}</div>
      <div class="card-meta">
        <span class="sbadge ${{scls(it.status)}}">${{esc(it.status||'—')}}</span>
        <span>${{esc(it.location||'')}}</span>
        <span class="price">${{price}}</span>
      </div>
    </div></a>
    <div class="card-btns">
      <button class="cbtn cbtn-a" onclick="cact(event,'${{it.sku}}','approve')" title="Approve (set Ready)">✓ Approve</button>
      <button class="cbtn cbtn-p" onclick="cact(event,'${{it.sku}}','ebay_publish')" title="Publish to eBay">↑ List</button>
    </div></div>`;
}}
function _rowHtml(it){{
  const pf=parseFloat(it.price);const price=isNaN(pf)?'—':'$'+pf.toFixed(2);
  return `<tr>
    <td><img class="lt-thumb" src="/thumb/${{it.sku}}" loading="lazy" onerror="this.style.visibility='hidden'" alt=""></td>
    <td><a href="/form/items/${{it.sku}}" class="lt-sku">${{esc(it.sku)}}</a></td>
    <td><a href="/form/items/${{it.sku}}" class="lt-title">${{esc(it.title||'')}}</a></td>
    <td><span class="sbadge ${{scls(it.status)}}">${{esc(it.status||'—')}}</span></td>
    <td style="color:#888;font-size:.8em">${{esc(it.location||'')}}</td>
    <td class="lt-price">${{price}}</td>
    <td>
      <button class="lt-btn" onclick="cact(event,'${{it.sku}}','approve')">✓</button>
      <button class="lt-btn" onclick="cact(event,'${{it.sku}}','ebay_publish')">↑</button>
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
function df(){{clearTimeout(_t);_t=setTimeout(()=>load(0),300);}}
initChips('#status-chips',()=>load(0));
load(0);
</script>
</body>
</html>
"""


def _render_item_detail_html(
    sku: str,
    item: Dict[str, Any],
    images: List[str],
    videos: List[str],
    jobs: List[Dict[str, Any]],
    api_key: str = "",
) -> str:
    import html as _html

    h = _html.escape

    def fv(key: str) -> str:
        v = item.get(key)
        return h(str(v)) if v is not None else '<span style="color:#444">—</span>'

    def fr(label: str, val: str = "", key: str = "", editable: bool = False) -> str:
        display = val if val else fv(key)
        if editable and key:
            raw = item.get(key)
            raw_str = h(str(raw)) if raw is not None else ""
            return (
                f'<div class="frow">'
                f'<span class="fn">{label}</span>'
                f'<span class="fv editable" data-field="{h(key)}" data-raw="{raw_str}"'
                f' title="Double-click to edit">{display}</span>'
                f"</div>"
            )
        return (
            f'<div class="frow">'
            f'<span class="fn">{label}</span>'
            f'<span class="fv">{display}</span>'
            f"</div>"
        )

    # Gallery
    if images:
        main_src = f"/media/{h(sku)}/{h(images[0])}"
        strip_items = []
        for i, img in enumerate(images):
            mv_buttons = ""
            if i > 1:
                mv_buttons += (
                    f'<button class="strip-mv" onclick="mvFront({i})" title="Move to front">⇑</button>'
                )
            if i > 0:
                mv_buttons += (
                    f'<button class="strip-mv" style="top:{"1px" if i <= 1 else "22px"}" '
                    f'onclick="mvPhoto({i})" title="Move earlier">↑</button>'
                )
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
            f'<img class="lb-img" id="lb-img" src="" alt="">'
            f'</div>'
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
            f"function mvPhoto(idx){{"
            f"if(idx<1)return;"
            f"var t=_photos[idx-1];_photos[idx-1]=_photos[idx];_photos[idx]=t;"
            f"savePhotoOrder();"
            f"}}"
            f"function mvFront(idx){{"
            f"if(idx<1)return;"
            f"var item=_photos.splice(idx,1)[0];"
            f"_photos.unshift(item);"
            f"savePhotoOrder();"
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
            f'<div class="strip-item video-item">'
            f'<video src="/media/{h(sku)}/{h(vid)}" class="strip-vid"'
            f' onclick="window.open(this.src,\'_blank\')" preload="none"></video>'
            f'</div>'
            for vid in videos
        )
        video_strip = (
            f'<div class="video-strip">'
            f'<div class="video-strip-hdr">VIDEO</div>'
            f'<div class="strip">{video_items}</div>'
            f'</div>'
        )
        gallery_html += video_strip

    # eBay sub-docs
    eb = item.get("ebay_listing") or {}
    eo = item.get("ebay_offer") or {}
    dl = item.get("draft_listing") or {}

    listing_id = eb.get("listing_id") or item.get("listing_id", "")
    listing_url = eb.get("listing_url") or item.get("listing_url", "")
    listing_status = (eb.get("status") or "").strip()
    offer_status = (eo.get("status") or "").strip()
    offer_price = eo.get("price")
    is_active = listing_status.lower() in ("active",) or offer_status.upper() in ("PUBLISHED",)
    is_staged = offer_status.upper() in ("UNPUBLISHED", "PUBLISHED")

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
        ebay_link_parts.append(
            f'<a class="ebay-btn ebay-btn-primary" href="{h(listing_url)}"'
            f' target="_blank" rel="noopener noreferrer">View on eBay ↗</a>'
        )
    if listing_id:
        sh_url = f"https://www.ebay.com/sh/lst/active?keyword={h(listing_id)}"
        ebay_link_parts.append(
            f'<a class="ebay-btn ebay-btn-sec" href="{sh_url}"'
            f' target="_blank" rel="noopener noreferrer">Seller Hub ↗</a>'
        )
    if is_active:
        ebay_link_parts.append(
            '<a class="ebay-btn ebay-btn-sec" href="https://messages.ebay.com/"'
            ' target="_blank" rel="noopener noreferrer">eBay Messages ↗</a>'
        )
    if listing_id:
        ebay_link_parts.append('<span id="offer-badge-wrap"></span>')
    ebay_links_html = (
        f'<div class="ebay-links">{"".join(ebay_link_parts)}</div>'
        if ebay_link_parts else ""
    )

    # eBay listing confirmed data
    listing_section = (
        fr("Listing ID", h(listing_id) if listing_id else "")
        + fr("eBay Status", h(listing_status) if listing_status else "")
        + fr("Published", h(str(eb.get("published_at", "") or "")[:19]))
        + fr("API", h(str(eb.get("api", "") or "")))
        + ebay_links_html
    )

    # eBay offer data (what we submitted)
    def _fmt_price(v: Any) -> str:
        try:
            return f"${float(v):.2f}" if v is not None else "—"
        except (ValueError, TypeError):
            return "—"

    # Pricing history expandable section (todo 877)
    _comps = eo.get("price_comps") or {} if eo else {}
    _price_source = eo.get("price_source", "") if eo else ""
    _target_price = eo.get("target_price") if eo else None
    _priced_at = (eo.get("priced_at") or "")[:19] if eo else ""
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
    if _comps.get("count"):
        _ph_rows += (
            f'<div class="frow"><span class="fn">Comp count</span><span class="fv">{h(str(_comps.get("count","—")))}</span></div>'
            f'<div class="frow"><span class="fn">Range</span><span class="fv">{_cfmt(_comps.get("min"))} – {_cfmt(_comps.get("max"))}</span></div>'
            f'<div class="frow"><span class="fn">p25 / median</span><span class="fv">{_cfmt(_comps.get("p25"))} / {_cfmt(_comps.get("median"))}</span></div>'
        )
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
    _ph_content = _ph_rows if _ph_rows else '<span style="color:#555;font-size:.82em">No pricing data yet</span>'
    _pricing_history_html = (
        f'<details style="margin-top:4px">'
        f'<summary style="cursor:pointer;color:#4a8ade;font-size:.82em;list-style:none">▶ Pricing History</summary>'
        f'<div style="margin-top:6px;padding:6px;background:#111;border:1px solid #222;border-radius:4px">'
        f'{_ph_content}'
        f'</div>'
        f'</details>'
    ) if eo else ""

    offer_section = (
        fr("Offer ID", h(str(eo.get("offer_id", "") or "")))
        + fr("Offer Status", h(offer_status) if offer_status else "")
        + fr("Offer Price", _fmt_price(offer_price))
        + _pricing_history_html
        + fr("Staged At", h(str(eo.get("staged_at", "") or "")[:19]))
    ) if eo else '<div class="frow"><span class="fv" style="color:#555">No offer yet</span></div>'

    # Draft listing data (what we prepared for eBay)
    if dl:
        _dl_raw_price = dl.get("price")
        if _dl_raw_price is None and offer_price is not None:
            dl_price_str = (
                _fmt_price(offer_price)
                + ' <span style="color:#888;font-size:.78em">(from offer)</span>'
            )
        else:
            dl_price_str = _fmt_price(_dl_raw_price)
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
        draft_section = (
            fr("Draft Title", h(str(dl.get("title", "") or "")))
            + fr("Category Sent", h(f"{dl.get('category_id','')} · {dl.get('category_name','')}"))
            + (fr("Store Category", h(_dl_store_cat)) if _dl_store_cat else "")
            + (fr("Shipping Policy", h(_dl_ship)) if _dl_ship else "")
            + fr("Condition Sent", h(f"{dl.get('condition_label','')} ({dl.get('condition_enum','')})"))
            + fr("Draft Price", dl_price_str)
            + fr("Quality Score", h(str(q_score)))
            + fr("Quality Flags", h(q_flags))
            + fr("Title Flags", h(title_flags))
            + fr("Req. Specifics", h(f"{req_fill}/{req_total} filled"))
            + fr("Rec. Specifics", h(f"{rec_fill}/{rec_total} filled"))
        )
    else:
        draft_section = '<div class="frow"><span class="fv" style="color:#555">No draft yet — run Re-identify then Re-draft</span></div>'

    # Revision draft diff
    revision_draft_html = ""
    revision = item.get("revision_draft")
    if revision:
        delta = revision.get("delta") or {}
        baseline = revision.get("baseline") or {}
        snap = baseline.get("snapshot") or {}
        by = h(str(revision.get("by", "")))
        at = h(str(revision.get("at", "")))[:19]
        bhash = h(str(baseline.get("hash", "")))[:12]
        if delta:
            rev_rows = "".join(
                f'<tr>'
                f'<td class="dfield">{h(f)}</td>'
                f'<td class="dwas">{h(str(snap.get(f, "—")))}</td>'
                f'<td class="dnow">{h(str(v))}</td>'
                f"</tr>"
                for f, v in delta.items()
            )
            revision_draft_html = (
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
            ts = (j.get("updated_at") or j.get("finished_at") or j.get("created_at") or "")[:16]
            err = h(str(j.get("error_detail") or "")[:60])
            qn = j.get("queue_name", "")
            tip = _WORKER_TOOLTIPS.get(qn, "")
            tip_attr = f' title="{h(tip)}"' if tip else ""
            job_rows += (
                f"<tr>"
                f'<td{tip_attr}>{h(qn)}</td>'
                f'<td class="{sc}">{h(state)}</td>'
                f'<td style="color:#666;font-size:.8em">{h(ts)}</td>'
                f'<td style="color:#f99;font-size:.8em">{err}</td>'
                f"</tr>"
            )
        jobs_html = (
            f'<table class="jtable">'
            f"<tr><th>Queue</th><th>State</th><th>Updated</th><th>Error</th></tr>"
            f"{job_rows}</table>"
        )

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
        h(str(_qty_raw)) if _qty_raw is not None
        else (h(str(dl.get("quantity"))) + ' <span style="color:#555;font-size:.78em">(from draft)</span>'
              if dl.get("quantity") is not None else '<span style="color:#444">—</span>')
    )
    # Effective price display with source annotation
    _price_raw = item.get("price")
    if _price_raw is not None:
        _price_display = h(price_str)
    elif offer_price is not None:
        _price_display = h(price_str) + ' <span style="color:#555;font-size:.78em">(from eBay offer)</span>'
    elif dl.get("price") is not None:
        _price_display = h(price_str) + ' <span style="color:#555;font-size:.78em">(from draft)</span>'
    else:
        _price_display = '<span style="color:#444">—</span>'

    fields_html = (
        '<div class="dfields">'
        # — Inventory record (what we track)
        '<div class="dsec">'
        '<h3>Inventory Record <span style="color:#2a4a6a;font-size:.72em;font-weight:normal">dbl-click to edit</span></h3>'
        '<div style="font-size:.73em;color:#556;margin-bottom:6px">Your internal record — drives the pipeline</div>'
        + fr("Title", key="title", editable=True)
        + fr("Condition", key="condition", editable=True)
        + fr("AI hint", key="ai_hint", editable=True)
        + fr("Description", key="description", editable=True)
        + fr("Brand", key="brand", editable=True)
        + fr("Model", key="model", editable=True)
        + fr("Category group", key="category_group")
        + fr("Barcode", key="barcode")
        + fr("Price", _price_display, key="price", editable=True)
        + fr("Qty", _qty_display, key="qty", editable=True)
        + fr("Location", key="location", editable=True)
        + fr("Weight (oz)", key="weight_oz", editable=True)
        + fr("Size class", key="size_class")
        + fr("Verified", key="verified")
        + fr("Alt text", key="alt_text")
        + "</div>"
        # — eBay Listing (confirmed by eBay after publish)
        f'<div class="dsec"><h3>eBay Listing{listing_badge}</h3>'
        '<div style="font-size:.73em;color:#556;margin-bottom:6px">Confirmed eBay data after listing is live</div>'
        + listing_section
        + "</div>"
        # — eBay Submission (what we sent to eBay — not a buyer offer)
        f'<div class="dsec"><h3>eBay Submission{offer_badge} <span style="font-size:.7em;color:#555;font-weight:normal">our offer to eBay</span></h3>'
        '<div style="font-size:.73em;color:#556;margin-bottom:6px">The offer record TGW created on eBay — not a buyer offer</div>'
        + offer_section
        + "</div>"
        # — eBay Draft (what the pipeline prepared, before staging)
        '<div class="dsec"><h3>eBay Draft <span style="font-size:.7em;color:#555;font-weight:normal">pipeline output</span></h3>'
        '<div style="font-size:.73em;color:#556;margin-bottom:6px">Output from ebay_draft — staged to eBay when you click Stage</div>'
        + draft_section
        + "</div>"
        + (
            f'<div class="dsec">'
            f'<h3>Revision Draft <span style="font-size:.7em;color:#555;font-weight:normal">proposed changes</span></h3>'
            f'<div style="font-size:.73em;color:#556;margin-bottom:6px">AI or operator proposed changes — apply to push to eBay</div>'
            f'{revision_draft_html}</div>'
            if revision_draft_html else ""
        )
        + (
            f'<div class="dsec"><h3>Pipeline Jobs</h3>{jobs_html}</div>'
            if jobs_html else ""
        )
        + "</div>"
    )

    # Actions section — context-aware based on eBay status
    import json as _json
    _ak_json2 = _json.dumps(api_key)
    _sku_json2 = _json.dumps(sku)
    _listing_id_json = _json.dumps(listing_id)
    _is_active_js = "true" if is_active else "false"

    publish_disabled = ' disabled title="Already active on eBay — End Listing first to relist"' if is_active else ""
    publish_cls = " act-disabled" if is_active else ""
    stage_disabled = ' disabled title="Already staged/published — use End Listing + relist to update"' if is_staged else ""
    stage_cls = " act-disabled" if is_staged else ""
    end_btn = (
        '<button class="act-btn act-warn" onclick="triggerAction(\'ebay_end_listing\')">'
        "End Listing on eBay</button>"
        if is_active else ""
    )

    actions_html = (
        f'<div class="dsec danger-zone">'
        f'<h3>Actions</h3>'
        f'<div class="act-row">'
        f'<button class="act-btn" onclick="triggerAction(\'ai_identify\')">Re-identify</button>'
        f'<button class="act-btn" onclick="triggerAction(\'ebay_draft\')">Re-draft</button>'
        f'<button class="act-btn" onclick="triggerAction(\'ebay_price\')">Re-price</button>'
        f'<button class="act-btn{stage_cls}"{stage_disabled} onclick="triggerAction(\'ebay_stage\')">Stage</button>'
        f'<button class="act-btn{publish_cls}"{publish_disabled} onclick="triggerAction(\'ebay_publish\')">Publish</button>'
        f'{end_btn}'
        f'</div>'
        f'<div class="act-row" style="margin-top:10px">'
        f'<button class="act-btn act-delete" onclick="deleteItem()">Delete item</button>'
        f'</div>'
        f'</div>'
        f'<script>'
        f'window.TGW_API_KEY={_ak_json2};'
        f'var _SKU={_sku_json2};'
        f'var _LISTING_ID={_listing_id_json};'
        f'var _IS_ACTIVE={_is_active_js};'
        f'function triggerAction(action){{'
        f'  fetch(\'/api/items/\'+_SKU+\'/action\',{{'
        f'    method:\'POST\','
        f'    headers:authHeaders({{\'Content-Type\':\'application/json\'}}),'
        f'    body:JSON.stringify({{action:action}})'
        f'  }}).then(function(r){{return r.json();}}).then(function(d){{'
        f'    alert(d.ok ? \'Action queued: \'+action : \'Error: \'+(d.detail||\'failed\'));'
        f'  }}).catch(function(e){{alert(\'Network error: \'+e);}});'
        f'}}'
        f'function deleteItem(){{'
        f'  var msg=\'Delete \'+_SKU+\'?\\nThis marks the item as deleted.\\nThe ItemData folder is preserved.\';'
        f'  if(_IS_ACTIVE){{msg+=\'\\n\\n⚠️ Active eBay listing (ID: \'+_LISTING_ID+\').\\nEnd Listing first, or use the End Listing button.\';}} '
        f'  if(!confirm(msg))return;'
        f'  fetch(\'/api/items/\'+_SKU,{{'
        f'    method:\'DELETE\','
        f'    headers:authHeaders()'
        f'  }}).then(function(r){{return r.json();}}).then(function(d){{'
        f'    if(d.ok){{window.location.href=\'/form/items\';}}'
        f'    else{{alert(\'Delete failed: \'+(d.detail||\'unknown error\'));}}'
        f'  }}).catch(function(e){{alert(\'Network error: \'+e);}});'
        f'}}'
        f'// Inline field editing — dblclick on .fv.editable to edit in place'
        f'document.querySelectorAll(\'.fv.editable\').forEach(function(span){{'
        f'  span.addEventListener(\'dblclick\',function(){{'
        f'    var field=span.dataset.field;'
        f'    var oldVal=span.dataset.raw!==undefined?span.dataset.raw:span.textContent;'
        f'    if(oldVal===\'—\')oldVal=\'\';'
        f'    var inp=document.createElement(\'input\');'
        f'    inp.type=\'text\';inp.value=oldVal;inp.className=\'fv fv-edit\';'
        f'    span.parentNode.replaceChild(inp,span);'
        f'    inp.focus();inp.select();'
        f'    function commit(){{'
        f'      var newVal=inp.value;'
        f'      inp.disabled=true;'
        f'      fetch(\'/api/items/\'+_SKU,{{'
        f'        method:\'PATCH\','
        f'        headers:authHeaders({{\'Content-Type\':\'application/json\'}}),'
        f'        body:JSON.stringify({{fields:{{[field]:newVal}}}})'
        f'      }}).then(function(r){{return r.json();}}).then(function(d){{'
        f'        if(d.ok){{'
        f'          span.textContent=newVal||\'—\';'
        f'          span.dataset.raw=newVal;'
        f'          inp.parentNode.replaceChild(span,inp);'
        f'          span.classList.add(\'fv-saved\');'
        f'          setTimeout(function(){{span.classList.remove(\'fv-saved\');}},800);'
        f'        }}else{{'
        f'          alert(\'Save failed: \'+(d.detail||\'error\'));'
        f'          inp.parentNode.replaceChild(span,inp);'
        f'        }}'
        f'      }}).catch(function(e){{'
        f'        alert(\'Network error: \'+e);'
        f'        inp.parentNode.replaceChild(span,inp);'
        f'      }});'
        f'    }}'
        f'    function cancel(){{inp.parentNode.replaceChild(span,inp);}}'
        f'    inp.addEventListener(\'keydown\',function(e){{'
        f'      if(e.key===\'Enter\'){{e.preventDefault();commit();}}'
        f'      if(e.key===\'Escape\')cancel();'
        f'    }});'
        f'    inp.addEventListener(\'blur\',function(){{if(!inp.disabled)commit();}});'
        f'  }});'
        f'}});'
        f'</script>\n'
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

    return (
        f"<!DOCTYPE html>\n<html lang='en'>\n<head>\n"
        f"<meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{h(sku)} — TGW</title>"
        + _STATIC_HEAD
        + f"<style>{_ITEMS_EXTRA_CSS}</style>"
        f"</head>\n<body>\n"
        f'<a class="back" href="/form/items">← Inventory</a>\n'
        f'<div class="sku-hdr">'
        f'<span class="slabel">{h(sku)}</span>'
        f'<span class="stitle">{h(title)}</span>'
        f"</div>\n"
        f'<div class="detail-layout">'
        f"{gallery_html}"
        f"{fields_html}"
        f"</div>\n"
        f"{actions_html}"
        + _STATIC_FOOT
        + offer_script
        + "</body></html>"
    )


@app.get("/form/items")
def items_browse_form():
    """Inventory browse — card grid with search/filter. No Bearer auth (network trust)."""
    from fastapi.responses import HTMLResponse

    html = _BROWSE_HTML.format(
        static_head=_STATIC_HEAD,
        static_foot=_STATIC_FOOT,
        extra_css=_ITEMS_EXTRA_CSS,
        api_key=_web_key,
    )
    return HTMLResponse(html, headers={"Cache-Control": "no-store, no-cache"})


@app.get("/form/items/{sku}")
def item_detail_form(sku: str):
    """Item detail page — photos, fields, revision diff. Server-rendered, no auth."""
    from fastapi.responses import HTMLResponse

    if ".." in sku:
        return HTMLResponse("<h2>invalid sku</h2>", status_code=400)

    json_path = _cfg["itemdata_root"] / sku / f"{sku}.json"
    if not json_path.exists():
        return HTMLResponse(f"<h2>SKU not found: {sku}</h2>", status_code=404)

    item = load_item_doc(json_path)

    def _nat_key(p: Path) -> List:
        return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", p.name)]

    all_images: List[str] = []
    videos: List[str] = []
    for p in sorted(json_path.parent.iterdir(), key=_nat_key):
        if not p.is_file():
            continue
        suf = p.suffix.lower()
        if suf in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
            all_images.append(p.name)
        elif suf in {".mp4", ".mov", ".mkv", ".webm"}:
            videos.append(p.name)

    photo_order = item.get("photo_order") or []
    if photo_order:
        img_set = set(all_images)
        ordered = [n for n in photo_order if n in img_set]
        extras = [n for n in all_images if n not in set(ordered)]
        images = ordered + extras
    else:
        images = all_images

    jobs: List[Dict[str, Any]] = []
    try:
        with psycopg2.connect(_cfg["postgres_dsn"]) as con:
            with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT queue_name, state, created_at, updated_at,
                           finished_at, error_code, error_detail
                      FROM queue_jobs
                     WHERE payload_json->>'sku' = %s
                     ORDER BY created_at DESC LIMIT 10
                    """,
                    (sku,),
                )
                jobs = [dict(r) for r in cur.fetchall()]
                for j in jobs:
                    for k in ("created_at", "updated_at", "finished_at"):
                        if j[k] is not None:
                            j[k] = j[k].isoformat()
    except Exception as exc:
        log.warning("queue job fetch failed for %s: %s", sku, exc)

    return HTMLResponse(
        _render_item_detail_html(sku, item, images, videos, jobs, _web_key),
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
                                     THEN 1 END)
                        FROM catalog
                        """
                    ).fetchone()
                    result["needs_review"] = row[0]
                    result["needs_photos"] = row[1]
                    result["has_revision_draft"] = row[2]
                    result["ready_count"] = row[3]
                else:
                    row = con.execute(
                        "SELECT COUNT(*) FROM catalog WHERE image IS NULL OR image = ''"
                    ).fetchone()
                    result["needs_review"] = None
                    result["needs_photos"] = row[0]
                    result["has_revision_draft"] = None
                    result["ready_count"] = None
            finally:
                con.close()
        except Exception as exc:
            log.warning("dashboard: SQLite query failed: %s", exc)
            result.update(needs_review=None, needs_photos=None,
                          has_revision_draft=None, ready_count=None)
    else:
        result.update(needs_review=None, needs_photos=None,
                      has_revision_draft=None, ready_count=None)

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
    if (
        _pending_offers_cache is not None
        and time.time() - _pending_offers_cache_at < _PENDING_OFFERS_TTL
    ):
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
            if '429' in _raw or '21919188' in _raw or 'rate' in _raw.lower():
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
            capture_output=True, text=True, timeout=5,
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

Valid agents: claude, gemini, sokoban (database tasks), admin, operator.
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
                cur.execute(
                    "SELECT queue_name, state, COUNT(*) AS n FROM queue_jobs"
                    " GROUP BY queue_name, state ORDER BY queue_name, state"
                )
                qrows = [dict(r) for r in cur.fetchall()]
        active = {
            r["queue_name"]: r["n"]
            for r in qrows if r["state"] in ("queued", "claimed")
        }
        dead = sum(r["n"] for r in qrows if r["state"] == "dead_letter")
        lines.append(
            "Active queues: " + (", ".join(f"{k}={v}" for k, v in active.items()) or "all idle")
        )
        lines.append(
            f"Dead-letter jobs: {dead}" + (" — NEEDS ATTENTION" if dead else "")
        )
    except Exception as exc:
        lines.append(f"Queue/dead-letter: unavailable ({exc})")

    # Inventory summary from SQLite catalog
    try:
        db_path = _cfg.get("sqlite_catalog_path")
        if db_path and Path(db_path).exists():
            con = _sqlite_conn()
            try:
                row = con.execute(
                    "SELECT COUNT(*),"
                    " COUNT(CASE WHEN LOWER(status) IN ('published','live','listed') THEN 1 END),"
                    " COUNT(CASE WHEN LOWER(status) = 'staged' THEN 1 END)"
                    " FROM catalog"
                ).fetchone()
                if row:
                    lines.append(
                        f"Inventory: {row[0]} total items, {row[1]} live on eBay, {row[2]} staged"
                    )
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
    if provider != "openrouter":
        raise HTTPException(status_code=503, detail="pm_chat only supports openrouter provider")

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
            row = con.execute(
                "SELECT location FROM catalog WHERE sku = ?", (sku,)
            ).fetchone()
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
            "daily_limit":  r["daily_limit"],
            "daily_used":   r["daily_used"],
            "hourly_limit": r["hourly_limit"],
            "hourly_used":  r["hourly_used"],
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
        api_key_json=json.dumps(_web_key),
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

    return cmd_revise_apply(_cfg, sku, dry_run=body.dry_run, by=body.by)


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
    del doc["revision_draft"]
    atomic_write_json(json_path, doc, pretty=_cfg.get("pretty", True))
    try:
        state_machine.enqueue_job(
            queue_name="catalog_rebuild",
            payload={"reason": f"revision_discard:{sku}"},
            dedupe_key="catalog_rebuild:pending",
            not_before=time.time() + 30,
            max_attempts=3,
        )
    except Exception:
        pass
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
        api_key_json=json.dumps(_web_key),
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
  return '<span class="' + cls + '">Q:' + score + '</span>';
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
    var editUrl = '/form/items/' + encodeURIComponent(sku);
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


@app.get("/form/review")
def review_form():
    """Post-draft review queue — approve/re-draft/edit items awaiting human QA.
    No Bearer auth (network trust); JS embeds the key for API calls."""
    from fastapi.responses import HTMLResponse

    html = _REVIEW_HTML.format(
        static_head=_STATIC_HEAD,
        static_foot=_STATIC_FOOT,
        review_css=_REVIEW_EXTRA_CSS,
        api_key_json=json.dumps(_web_key),
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

function renderQueues(data) {{
  var el = document.getElementById('queue-table');
  if (!data || !data.ok) {{
    el.innerHTML = '<div class="err-box">Failed to load queue status.</div>';
    return;
  }}
  var queues = data.queues || {{}};
  var names = Object.keys(queues).sort();
  if (names.length === 0) {{
    el.innerHTML = '<div class="empty-state">No queue activity.</div>';
    return;
  }}
  var html = '<table class="pl-table"><tr>' +
    '<th>Queue</th><th>Pending</th><th>Running</th><th>Done today</th><th>Failed/DL</th></tr>';
  names.forEach(function(q) {{
    var s = queues[q] || {{}};
    var pending = (s.queued || 0) + (s.leased || 0) + (s.retry_wait || 0);
    var running = s.running || 0;
    var done = s.succeeded || 0;
    var dead = (s.dead_letter || 0) + (s.failed || 0);
    html += '<tr>' +
      '<td class="qname">' + escapeHtml(q) + '</td>' +
      numCell(pending || null, 'n-queued') +
      numCell(running || null, 'n-run') +
      numCell(done || null, 'n-done') +
      numCell(dead || null, 'n-dead') +
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
  // Queue depths
  try {{
    var r = await fetch('/api/queue/status', {{headers: authHeaders()}});
    renderQueues(await r.json());
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
        api_key_json=json.dumps(_web_key),
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
                cur.execute(
                    "UPDATE queue_jobs SET state = 'cancelled' WHERE job_id = %s",
                    (job_id,),
                )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"cancel failed: {exc}")

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
        "logs":     Path("/opt/TGW/var/log"),
        "backup":   Path("/opt/TGW/var/local/backups"),
    }
    disk: Dict[str, Any] = {}
    for label, path in disk_paths.items():
        try:
            usage = shutil.disk_usage(str(path))
            disk[label] = {
                "path": str(path),
                "total": usage.total,
                "used":  usage.used,
                "free":  usage.free,
                "pct":   round(usage.used / usage.total * 100, 1) if usage.total else 0,
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
                "exists":           True,
                "mtime":            mtime,
                "expires_at":       expires_at,
                "remaining_seconds": remaining,
                "ok":               remaining is None or remaining > 300,
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
                cur.execute(
                    "SELECT state, COUNT(*) FROM queue_jobs GROUP BY state ORDER BY state"
                )
                for row in cur.fetchall():
                    job_states[str(row[0])] = int(row[1])
    except Exception as exc:
        job_states["_error"] = str(exc)

    return {
        "ok":         True,
        "disk":       disk,
        "ebay_token": token_info,
        "sync":       sync_info,
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
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode != 0:
            return {
                "ok":         False,
                "unit":       unit,
                "returncode": r.returncode,
                "stderr":     r.stderr.strip(),
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
        api_key_json=json.dumps(_web_key),
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
    {{key:'needs_review',      label:'Need Review',    href:'/form/items', cls:function(v){{return v>0?'alert':'';}}}},
    {{key:'pending_offers',    label:'Pending Offers', href:null,          cls:function(v){{return v>0?'info':'';}}}},
    {{key:'needs_photos',      label:'Need Photos',    href:'/form/items', cls:function(v){{return v>0?'alert':'';}}}},
    {{key:'has_revision_draft',label:'Revision Drafts',href:null,          cls:function(v){{return v>0?'info':'';}}}},
    {{key:'dead_letter_count', label:'Dead Letters',   href:null,          cls:function(v){{return v>0?'err':'';}}}},
    {{key:'ready_count',       label:'Ready to List',  href:'/form/items', cls:function(v){{return v>0?'ok':'';}}}},
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
        api_key_json=json.dumps(_web_key),
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

    return HTMLResponse(_LINKS_HTML.format(
        static_head=_STATIC_HEAD,
        static_foot=_STATIC_FOOT,
    ))


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


def _vault_root() -> Path:
    """Return the plan vault root from config, or fall back to repo-relative default."""
    p = _cfg.get("plan_vault_path")
    if p:
        return Path(p)
    return (Path(__file__).parent.parent.parent / "docs" / "TGW-Plan-Vault").resolve()


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
            parts.append(
                f'<a class="docs-link{active}" href="/docs/{rel}"'
                f' title="{_html.escape(rel)}">{_html.escape(display)}</a>'
            )
        parts.append('</div>')
    parts.append('</nav>')
    return "".join(parts)


def _docs_page_html(title: str, body_html: str, sidebar_html: str) -> str:
    import html as _html
    return (
        "<!DOCTYPE html><html lang='en'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_html.escape(title)} — TGW Docs</title>"
        + _STATIC_HEAD
        + f"<style>{_DOCS_EXTRA_CSS}</style>"
        + "</head><body>"
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

    vault = _vault_root()
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
        escape=False,
        plugins=["table", "strikethrough"],
    )
    body_html = md(content)

    sections = _list_docs_sections()
    sidebar_html = _docs_sidebar_html(sections, path)
    title = Path(path).stem.replace("-", " ").replace("_", " ")

    return HTMLResponse(_docs_page_html(title, body_html, sidebar_html))


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
    from .workers.ebay_legacy_sync import _mark_item_sold

    body = await request.body()
    log.debug('ebay_webhook: received %d bytes', len(body))

    if not verify_notification_signature(body, _cfg):
        log.warning('ebay_webhook: invalid signature — rejected')
        raise HTTPException(status_code=400, detail='invalid signature')

    event = parse_sold_notification(body)
    if event is None:
        # Ping/test notification from eBay — just ack it
        log.info('ebay_webhook: non-sold notification (ping or unknown type)')
        return {'ack': 'Success'}

    listing_id = event['listing_id']
    index = _get_listing_index()
    json_path = index.get(listing_id)

    # Cache miss — maybe a newly listed item; do a targeted scan
    if json_path is None or not json_path.exists():
        log.info('ebay_webhook: listing %s not in index — rebuilding', listing_id)
        _listing_index_built_at_reset()
        index = _get_listing_index()
        json_path = index.get(listing_id)

    if json_path is None or not json_path.exists():
        log.warning('ebay_webhook: no local item for listing_id=%s — acking anyway', listing_id)
        return {'ack': 'Success'}

    synced_at = datetime.now(timezone.utc).isoformat()
    try:
        did_mark = _mark_item_sold(
            json_path,
            order_id=event['order_id'],
            buyer=event['buyer'],
            sale_price=event['sale_price'],
            quantity=event['quantity'],
            sale_date=event['sale_date'],
            synced_at=synced_at,
            cfg=_cfg,
        )
        if did_mark:
            log.info('ebay_webhook: marked sold listing_id=%s', listing_id)
            try:
                state_machine.enqueue_job(
                    queue_name='catalog_rebuild',
                    payload={'reason': 'ebay_webhook_sold'},
                    dedupe_key='catalog_rebuild:pending',
                    not_before=time.time() + 30,
                    max_attempts=3,
                )
            except Exception:
                pass
    except Exception as exc:
        log.error('ebay_webhook: mark failed listing_id=%s: %s', listing_id, exc)

    return {'ack': 'Success'}
