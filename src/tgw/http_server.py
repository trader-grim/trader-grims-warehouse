"""
tgw.http_server — FastAPI HTTP service (tgw-http).

Exposes inventory and pipeline operations over HTTP on port 7373.
Shared API for MC console copyin operations and the Flutter app.

Auth: Bearer <api_key> — key stored in secrets_root/tgw-api-key.json
"""

from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Security, status
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
# Module-level state (set during lifespan startup)
# ---------------------------------------------------------------------------

_cfg: Dict[str, Any] = {}
_api_key: str = ""

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
}


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _cfg, _api_key
    _cfg = load_config(DEFAULT_CONFIG)
    state_machine.init(_cfg["postgres_dsn"])

    key_path: Path = _cfg["secrets_root"] / "tgw-api-key.json"
    if not key_path.exists():
        raise RuntimeError(f"API key file not found: {key_path}")
    _api_key = json.loads(key_path.read_text(encoding="utf-8"))["api_key"]

    log.info("tgw-http started on port 7373")
    yield
    log.info("tgw-http shutting down")


app = FastAPI(title="tgw-http", version="1.0", lifespan=lifespan)

_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

def _require_auth(credentials: HTTPAuthorizationCredentials = Security(_security)) -> None:
    if credentials.credentials != _api_key:
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


class PMChatBody(BaseModel):
    message: str
    history: List[Dict[str, str]] = Field(default_factory=list)


class PMActionBody(BaseModel):
    type: str
    agent: Optional[str] = None
    body: Optional[str] = None
    priority: int = 50
    text: Optional[str] = None


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
    sql = f"SELECT sku, title, location, status, price, qty, image FROM catalog {where} ORDER BY sku DESC LIMIT ? OFFSET ?"
    params += [limit, offset]

    con = _sqlite_conn()
    try:
        rows = [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()

    return {"ok": True, "count": len(rows), "items": rows}


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
        if action == "catalog_rebuild":
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

_INTAKE_FORM_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Intake: {sku_short}</title>
{static_head}
</head>
<body>
<h2>Intake Form</h2>
<div class="sku">{sku}</div>

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

<button class="btn" onclick="submitForm()">Save</button>
<div class="msg" id="msg"></div>

{static_foot}
<script>
const SKU = {sku_json};
const API = '/api/items/' + SKU;
const AUTH = 'Bearer {api_key}';

initChips('#chips', c => {{ document.getElementById('tpl_key').value = c.dataset.key; }});

async function submitForm() {{
  const msg = document.getElementById('msg');
  msg.className = 'msg';
  msg.textContent = '';

  const tpl = document.getElementById('tpl_key').value;
  const w   = document.getElementById('weight_oz').value;
  const bc  = document.getElementById('barcode').value.trim();
  const hnt = document.getElementById('ai_hint').value.trim();
  const cnd = document.getElementById('condition').value;

  // Apply template first if changed
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

  // Patch remaining fields
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

    html = _INTAKE_FORM_HTML.format(
        sku=sku,
        sku_short=sku_short,
        sku_json=json.dumps(sku),
        static_head=_STATIC_HEAD,
        static_foot=_STATIC_FOOT,
        chips_html=chips_html,
        current_template=current_template,
        current_template_json=json.dumps(current_template),
        weight_oz=weight,
        barcode=barcode,
        ai_hint=ai_hint,
        condition_options=cond_opts,
        api_key=_api_key,
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
        api_key=_api_key,
    )
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# GET /form/todos — tablet-first open-todo dashboard (PP-TODO-001, Round 4 #34)
# ---------------------------------------------------------------------------

_TODOS_EXTRA_CSS = """
table{width:100%;border-collapse:collapse;margin:2px 0 16px}
th,td{text-align:left;padding:8px 6px;border-bottom:1px solid #333;font-size:.9em;vertical-align:top}
th{color:#888;font-size:.72em;text-transform:uppercase;letter-spacing:.04em}
td.id{color:#4a8ade;font-variant-numeric:tabular-nums;white-space:nowrap}
td.p{color:#caa;font-variant-numeric:tabular-nums;white-space:nowrap}
td.src{color:#777;font-size:.8em;white-space:nowrap}
.agent{display:flex;align-items:baseline;gap:8px;margin:18px 0 2px}
.agent h3{margin:0;font-size:1em;color:#7fbfff;text-transform:capitalize}
.agent .count{font-size:.8em;color:#888}
.allclear{margin-top:24px;padding:16px;border-radius:8px;background:#1a4a1a;color:#7f7;text-align:center;font-size:1.05em}
.total{font-size:.82em;color:#999;margin-bottom:6px}
"""


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
        return head + '<div class="allclear">✓ All clear — no open todos.</div>' + _STATIC_FOOT + '</body></html>'

    # Preserve todo_list ordering (agent, priority, id); group consecutively.
    groups: "list[tuple[str, list]]" = []
    for r in rows:
        agent = r.get("agent", "?")
        if not groups or groups[-1][0] != agent:
            groups.append((agent, []))
        groups[-1][1].append(r)

    parts = [head, f'<div class="total">{len(rows)} open item(s)</div>']
    for agent, items in groups:
        parts.append(
            f'<div class="agent"><h3>{_html.escape(str(agent))}</h3>'
            f'<span class="count">{len(items)} open</span></div>'
        )
        parts.append('<table><tr><th>ID</th><th>P</th><th>Task</th><th>Src</th></tr>')
        for it in items:
            parts.append(
                '<tr>'
                f'<td class="id">#{_html.escape(str(it.get("id", "")))}</td>'
                f'<td class="p">{_html.escape(str(it.get("priority", "")))}</td>'
                f'<td>{_html.escape(str(it.get("body", "")))}</td>'
                f'<td class="src">{_html.escape(str(it.get("source", "")))}</td>'
                '</tr>'
            )
        parts.append('</table>')
    parts.append(_STATIC_FOOT)
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
    p = _cfg["itemdata_root"] / sku / safe
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
    sku_dir = _cfg["itemdata_root"] / sku
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
.summary{font-size:.8em;color:#888;margin-bottom:8px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:10px}
.card{display:flex;flex-direction:column;background:#1a1a1a;border:1px solid #333;
  border-radius:8px;text-decoration:none;color:inherit;overflow:hidden;transition:border-color .15s}
.card:hover{border-color:#4a8ade}
.card .thumb{width:100%;aspect-ratio:4/3;object-fit:cover;background:#111}
.card-body{padding:8px}
.card-sku{font-size:.7em;color:#888;font-family:monospace}
.card-title{font-size:.85em;margin:4px 0;color:#ddd;line-height:1.3;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.card-meta{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:4px;font-size:.75em;color:#888}
.price{color:#7fbfff;font-weight:bold}
.sbadge{padding:2px 6px;border-radius:10px;font-size:.72em}
.s-in-stock{background:#1a3a1a;color:#7f7}.s-listed{background:#1a2a4a;color:#7af}
.s-staged{background:#3a2a0a;color:#fb7}.s-sold{background:#2a1a1a;color:#f77}
.pager{text-align:center;margin-top:14px;font-size:.9em;color:#888}
.pager button{padding:6px 14px;background:#222;color:#eee;border:1px solid #444;
  border-radius:6px;cursor:pointer;margin:0 4px}
.pager button:hover{background:#333}
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
</div>
<div class="chips" id="status-chips" style="margin-bottom:10px">
  <button class="chip active" data-s="">All</button>
  <button class="chip" data-s="In Stock">In Stock</button>
  <button class="chip" data-s="Listed">Listed</button>
  <button class="chip" data-s="Staged">Staged</button>
  <button class="chip" data-s="Sold">Sold</button>
</div>
<div class="summary" id="sum"></div>
<div class="grid" id="grid"><div class="loading">Loading…</div></div>
<div class="pager" id="pager"></div>
{static_foot}
<script>
const AUTH='Bearer {api_key}';
const esc=escapeHtml;
const LIM=60;
let off=0;
const scls=s=>({{
  'in stock':'s-in-stock','listed':'s-listed','staged':'s-staged','sold':'s-sold'
}})[(s||'').toLowerCase()]||'';
async function load(o){{
  off=o??0;
  const search=document.getElementById('sq').value;
  const loc=document.getElementById('loc').value;
  const status=document.querySelector('#status-chips .chip.active')?.dataset.s??'';
  const p=new URLSearchParams({{limit:LIM,offset:off}});
  if(search)p.set('search',search);
  if(loc)p.set('location',loc);
  if(status)p.set('status_filter',status);
  document.getElementById('grid').innerHTML='<div class="loading">Loading…</div>';
  let r,d;
  try{{r=await fetch('/api/items?'+p,{{headers:{{Authorization:AUTH}}}});d=await r.json();}}
  catch(e){{document.getElementById('grid').innerHTML='<div class="loading">Network error</div>';return;}}
  if(!r.ok||!d.ok){{
    document.getElementById('grid').innerHTML='<div class="loading">Error: '+esc(d.detail||d.error||r.status)+'</div>';
    return;
  }}
  document.getElementById('sum').textContent=d.count+' item'+(d.count===1?'':'s');
  if(!d.items.length){{
    document.getElementById('grid').innerHTML='<div class="no-results">No items found.</div>';
    document.getElementById('pager').innerHTML='';
    return;
  }}
  let html='';
  for(const it of d.items){{
    const price=it.price!=null?'$'+parseFloat(it.price).toFixed(2):'—';
    html+=`<a href="/form/items/${{it.sku}}" class="card">
      <img class="thumb" src="/thumb/${{it.sku}}" loading="lazy" alt="" onerror="this.style.visibility='hidden'">
      <div class="card-body">
        <div class="card-sku">${{it.sku}}</div>
        <div class="card-title">${{esc(it.title||'')}}</div>
        <div class="card-meta">
          <span class="sbadge ${{scls(it.status)}}">${{esc(it.status||'—')}}</span>
          <span>${{esc(it.location||'')}}</span>
          <span class="price">${{price}}</span>
        </div>
      </div></a>`;
  }}
  document.getElementById('grid').innerHTML=html;
  const pages=Math.ceil(d.count/LIM);
  const cur=Math.floor(off/LIM)+1;
  let pg='';
  if(off>0)pg+=`<button onclick="load(${{off-LIM}})">← Prev</button>`;
  if(pages>1)pg+=` Page ${{cur}} of ${{pages}} `;
  if(off+LIM<d.count)pg+=`<button onclick="load(${{off+LIM}})">Next →</button>`;
  document.getElementById('pager').innerHTML=pg;
}}
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
) -> str:
    import html as _html

    h = _html.escape

    def fv(key: str) -> str:
        v = item.get(key)
        return h(str(v)) if v is not None else '<span style="color:#444">—</span>'

    def fr(label: str, val: str = "", key: str = "") -> str:
        display = val if val else fv(key)
        return (
            f'<div class="frow">'
            f'<span class="fn">{label}</span>'
            f'<span class="fv">{display}</span>'
            f"</div>"
        )

    # Gallery
    if images:
        main_src = f"/media/{h(sku)}/{h(images[0])}"
        strip = "".join(
            f'<img src="/media/{h(sku)}/{h(img)}" class="{"active" if i == 0 else ""}"'
            f' onclick="sm(this)" loading="lazy" alt="">'
            for i, img in enumerate(images)
        )
        gallery_html = (
            f'<div class="gallery">'
            f'<img class="main-photo" id="mp" src="{main_src}" alt="">'
            f'<div class="strip">{strip}</div>'
            f"</div>"
            f"<script>function sm(el){{"
            f"document.getElementById('mp').src=el.src;"
            f"document.querySelectorAll('.strip img').forEach(i=>i.classList.remove('active'));"
            f"el.classList.add('active');}}</script>"
        )
    else:
        gallery_html = '<div style="color:#555;padding:30px;text-align:center">No photos</div>'

    # eBay fields (prefer ebay_listing sub-doc)
    eb = item.get("ebay_listing") or {}
    listing_id = eb.get("listing_id") or item.get("listing_id", "")
    listing_url = eb.get("listing_url") or item.get("listing_url", "")
    listing_status = eb.get("status") or item.get("status", "")
    price = eb.get("live_price") if eb.get("live_price") is not None else item.get("price")
    try:
        price_str = f"${float(price):.2f}" if price is not None else "—"
    except (ValueError, TypeError):
        price_str = "—"
    url_html = (
        f'<a href="{h(listing_url)}" target="_blank">{h(listing_url[:60])}…</a>'
        if listing_url
        else '<span style="color:#444">—</span>'
    )

    # Revision draft diff
    draft_html = ""
    draft = item.get("revision_draft")
    if draft:
        delta = draft.get("delta") or {}
        baseline = draft.get("baseline") or {}
        snap = baseline.get("snapshot") or {}
        by = h(str(draft.get("by", "")))
        at = h(str(draft.get("at", "")))[:19]
        bhash = h(str(baseline.get("hash", "")))[:12]
        if delta:
            rows = "".join(
                f'<tr>'
                f'<td class="dfield">{h(f)}</td>'
                f'<td class="dwas">{h(str(snap.get(f, "—")))}</td>'
                f'<td class="dnow">{h(str(v))}</td>'
                f"</tr>"
                for f, v in delta.items()
            )
            draft_html = (
                f'<h3 class="diff-hdr">Revision Draft</h3>'
                f'<div class="diff-meta">by {by} · {at} · baseline {bhash}…</div>'
                f'<table class="dtable">'
                f"<tr><th>Field</th><th>Current</th><th>Proposed</th></tr>"
                f"{rows}"
                f"</table>"
            )

    # Pipeline jobs
    jobs_html = ""
    if jobs:
        rows = ""
        for j in jobs[:10]:
            state = j.get("state", "")
            sc = "js-" + state.replace("_", "-").lower()
            ts = (j.get("updated_at") or j.get("finished_at") or j.get("created_at") or "")[:16]
            err = h(str(j.get("error_detail") or "")[:60])
            rows += (
                f"<tr>"
                f'<td>{h(j.get("queue_name",""))}</td>'
                f'<td class="{sc}">{h(state)}</td>'
                f'<td style="color:#666;font-size:.8em">{h(ts)}</td>'
                f'<td style="color:#f99;font-size:.8em">{err}</td>'
                f"</tr>"
            )
        jobs_html = (
            f'<table class="jtable">'
            f"<tr><th>Queue</th><th>State</th><th>Updated</th><th>Error</th></tr>"
            f"{rows}</table>"
        )

    title = item.get("title", "")

    fields_html = (
        '<div class="dfields">'
        '<div class="dsec"><h3>Identity</h3>'
        + fr("Title", key="title")
        + fr("Category group", key="category_group")
        + fr("Condition", key="condition")
        + fr("AI hint", key="ai_hint")
        + fr("Barcode", key="barcode")
        + "</div>"
        '<div class="dsec"><h3>eBay</h3>'
        + fr("Listing ID", h(listing_id) if listing_id else "")
        + fr("Status", h(listing_status) if listing_status else "")
        + fr("Price", price_str)
        + fr("URL", url_html)
        + fr("Qty", key="qty")
        + fr("Qty sold", key="quantity_sold")
        + "</div>"
        '<div class="dsec"><h3>Physical</h3>'
        + fr("Location", key="location")
        + fr("Weight (oz)", key="weight_oz")
        + fr("Size class", key="size_class")
        + "</div>"
        + (
            f'<div class="dsec">{draft_html}</div>'
            if draft_html else ""
        )
        + (
            f'<div class="dsec"><h3>Pipeline Jobs</h3>{jobs_html}</div>'
            if jobs_html else ""
        )
        + "</div>"
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
        + _STATIC_FOOT
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
        api_key=_api_key,
    )
    return HTMLResponse(html)


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

    images: List[str] = []
    videos: List[str] = []
    for p in sorted(json_path.parent.iterdir()):
        if not p.is_file():
            continue
        suf = p.suffix.lower()
        if suf in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
            images.append(p.name)
        elif suf in {".mp4", ".mov", ".mkv", ".webm"}:
            videos.append(p.name)

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

    return HTMLResponse(_render_item_detail_html(sku, item, images, videos, jobs))


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
            log.warning("dashboard: GetBestOffers failed: %s", exc)
            _pending_offers_cache_at = time.time()  # back-off: don't retry until TTL expires
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
    +'<button class="btn-no" onclick="this.closest(\'.pm-toast\').remove()">Dismiss</button>'
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
        api_key_json=json.dumps(_api_key),
    )
    return HTMLResponse(html)


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
