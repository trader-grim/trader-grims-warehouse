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
from fastapi import Depends, FastAPI, HTTPException, Request, Security, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

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
