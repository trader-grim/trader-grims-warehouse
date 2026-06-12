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

_INTAKE_FORM_CSS = """
body{font-family:system-ui,sans-serif;margin:0;padding:8px;background:#111;color:#eee}
h2{font-size:1.1em;margin:4px 0 10px}
.sku{font-size:.75em;color:#888;margin-bottom:8px}
label{display:block;font-size:.85em;color:#aaa;margin:10px 0 3px}
input,select,textarea{width:100%;box-sizing:border-box;padding:10px;font-size:1em;
  background:#222;color:#eee;border:1px solid #444;border-radius:6px}
textarea{height:60px;resize:vertical}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0}
.chip{padding:8px 12px;border-radius:20px;background:#2a2a2a;border:2px solid #444;
  font-size:.82em;cursor:pointer;transition:background .15s,border-color .15s}
.chip:hover{background:#333;border-color:#666}
.chip.active{background:#1a4a8a;border-color:#4a8ade;color:#fff}
.btn{display:block;width:100%;padding:14px;margin-top:14px;font-size:1.1em;
  background:#1a6030;color:#fff;border:none;border-radius:8px;cursor:pointer}
.btn:active{background:#155028}
.msg{margin-top:10px;padding:8px;border-radius:6px;font-size:.9em;display:none}
.msg.ok{background:#1a4a1a;color:#7f7;display:block}
.msg.err{background:#4a1a1a;color:#f77;display:block}
.field-row{display:flex;gap:8px}
.field-row>*{flex:1}
"""

_INTAKE_FORM_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Intake: {sku_short}</title>
<style>{css}</style>
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

<script>
const SKU = {sku_json};
const API = '/api/items/' + SKU;
const AUTH = 'Bearer {api_key}';

document.querySelectorAll('.chip').forEach(c => {{
  c.addEventListener('click', () => {{
    document.querySelectorAll('.chip').forEach(x => x.classList.remove('active'));
    c.classList.add('active');
    document.getElementById('tpl_key').value = c.dataset.key;
  }});
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
        css=_INTAKE_FORM_CSS,
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

_BULK_FORM_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TGW Bulk Edit</title>
<style>{css}</style>
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

<script>
const AUTH = 'Bearer {api_key}';
document.querySelectorAll('#fields .chip').forEach(c => {{
  c.addEventListener('click', () => {{
    document.querySelectorAll('#fields .chip').forEach(x => x.classList.remove('active'));
    c.classList.add('active');
    document.getElementById('field').value = c.dataset.f;
  }});
}});

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

function escapeHtml(s) {{
  return s.replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
}}
</script>
</body>
</html>
"""

_BULK_FORM_CSS = _INTAKE_FORM_CSS + """
table{width:100%;border-collapse:collapse;margin-top:10px;font-size:.8em}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #333}
th{color:#aaa}
"""


@app.get("/form/bulk")
def bulk_form(request: Request):
    """Tablet-first bulk editor — no Bearer auth on the page (network trust);
    the embedded JS calls the authenticated /api/bulk/* endpoints."""
    from fastapi.responses import HTMLResponse
    html = _BULK_FORM_HTML.format(css=_BULK_FORM_CSS, api_key=_api_key)
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# GET /form/todos — tablet-first open-todo dashboard (PP-TODO-001, Round 4 #34)
# ---------------------------------------------------------------------------

_TODOS_FORM_CSS = _INTAKE_FORM_CSS + """
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
        '<title>TGW Todos</title><style>' + _TODOS_FORM_CSS + '</style></head><body>'
        '<h2>Open Todos</h2>'
    )
    if not rows:
        return head + '<div class="allclear">✓ All clear — no open todos.</div></body></html>'

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
            '<title>TGW Todos</title><style>' + _TODOS_FORM_CSS + '</style></head><body>'
            '<h2>Open Todos</h2>'
            f'<div class="msg err" style="display:block">todo store unavailable: {exc}</div>'
            '</body></html>'
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
        '<title>TGW Suggest</title><style>' + _INTAKE_FORM_CSS + '</style></head><body>'
        '<h2>Add Suggestion</h2>'
        '<form method="post" action="/form/suggest">'
        '<label>Suggestion — any punctuation is safe here</label>'
        '<textarea name="text" required autofocus placeholder="idea, task, note ..."></textarea>'
        '<button class="btn" type="submit">Add to SUGGESTIONS.md</button>'
        '</form>'
        + banner +
        '</body></html>'
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
