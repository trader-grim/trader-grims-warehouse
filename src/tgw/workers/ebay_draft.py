"""
tgw.workers.ebay_draft — eBay draft listing worker.

Fetches item specifics (aspects) for the item's eBay category, uses
Qwen2.5 (text) to suggest values based on item data, then writes a
draft_listing block to the item JSON ready for human review before upload.

Enqueued by ai_identify after category resolution. Safe to re-run —
will overwrite draft_listing but never touch title/description/condition.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

import tgw.logging as tgw_logging
from tgw import inventory_record, quota
from tgw.apis.ebay.client import ebay_get
from tgw.apis.ebay.conditions import best_condition
from tgw.apis.ebay.specifics import get_aspects
from tgw.apis.fence import patch_item as fence_patch_item
from tgw.apis.llm import CLOUD_PROVIDERS, call_model, get_task_model
from tgw.apis.ollama import extract_json
from tgw.assets import ordered_photos as _asset_ordered_photos
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.config import sku_json as _cfg_sku_json
from tgw.ebay.aspect_translation import translate_inventory_to_ebay_draft
from tgw.ebay.description import build_listing_description
from tgw.ebay.draft_specifics import set_ebay_aspects, wrap_ebay_specifics
from tgw.errors import TreatmentFailure
from tgw.item_mutation import (
    item_generation,
    mutate_item,
    operation_identity,
    reconcile_mutation,
)
from tgw.queue import state_machine
from tgw.queue.worker_base import HardFailure, QueueWorker
from tgw.sqlite_catalog import upsert_catalog_row
from tgw.workflow.item_snapshot import inventory_available

log = logging.getLogger(__name__)

QUEUE_NAME  = 'ebay_draft'

# Session 41 — aspect-fill now looks at the item's photos instead of asking a
# text-only model to guess from the title. "Without good starting data all of
# our guesses will be poor" — this is the same batch limit as ai_identify's
# candidate selection (exclude -alt./cropped- derivatives), just routed through
# the cheap bulk_classify task since this is high-volume, structured extraction,
# not prose generation (ebay_draft's own task stays on gemini-2.5-flash for the
# description-enrichment call below, which needs the stronger model).
_MAX_PHOTOS_ASPECTS = 10
_VISION_MAX_PX_ASPECTS = 1024

_SYSTEM = """\
You are an eBay listing assistant. You will be shown photos of the item and a
list of eBay item specifics (aspects) to fill in. Examine ALL the photos — a
detail visible in only one photo (a barcode, a tag, an engraving, packaging
text) still counts. For SELECTION_ONLY aspects, you MUST choose from the
allowed values listed. For FREE_TEXT aspects, suggest a concise, accurate
value grounded in what you can actually see. If an aspect does not apply or
isn't visible in any photo, use null — never guess.
Respond with valid JSON only — an object mapping aspect name to suggested value.
"""

_SYSTEM_DESC = """\
You are writing an eBay listing description. Write in natural prose sentences.
No bullet points, headers, or ALL CAPS. No markdown. Plain text only.
Target length: 200+ words.
"""


_OFFLINE_CSV_FIELDS = ['sku', 'title', 'category_id', 'category_name',
                       'condition', 'format', 'quantity', 'price', 'description']


_BROWSE_HINT_SKIP = frozenset({'Does Not Apply', 'Unbranded', 'N/A', 'Unknown', 'Other'})
_groups_cache: Dict[str, Any] = {}


def _get_store_category_id(item: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[int]:
    """
    Return the store_category_id for this item's category group, or None.
    category-groups.json is cached per path — reloaded only on process restart.
    """
    cat_group_key = item.get('category_group', '')
    if not cat_group_key:
        return None
    try:
        cg_path_str = cfg['category_groups_path']
        if cg_path_str not in _groups_cache:
            _groups_cache[cg_path_str] = json.loads(
                Path(cg_path_str).read_text(encoding='utf-8')
            )
        grp_data = _groups_cache[cg_path_str].get('groups', {}).get(cat_group_key, {})
        sc_id = grp_data.get('store_category_id')
        return int(sc_id) if sc_id is not None else None
    except Exception:
        return None


def _fetch_browse_aspect_hints(
    cfg: Dict[str, Any],
    title: str,
    category_id: str,
) -> Dict[str, str]:
    """
    Search Browse API for active listings similar to *title* in *category_id*.
    Returns the most common aspect value for each aspect from the ASPECT_REFINEMENTS
    fieldgroup — a lightweight signal about what fields sellers commonly fill in.
    Returns {} on any failure (best-effort; never blocks drafting).
    """
    try:
        data = ebay_get(cfg, '/buy/browse/v1/item_summary/search', params={
            'q':            title[:100],
            'category_ids': category_id,
            'fieldgroups':  'ASPECT_REFINEMENTS',
            'limit':        5,
        })
    except Exception as exc:
        log.debug('browse aspect hints unavailable for %r (%s): %s', title, category_id, exc)
        return {}

    hints: Dict[str, str] = {}
    for dist in data.get('refinement', {}).get('aspectDistributions', []):
        field_name = dist.get('fieldName', '').strip()
        if not field_name:
            continue
        for entry in dist.get('aspectValueDistributions', []):
            val = entry.get('localizedAspectValue', '').strip()
            if val and val not in _BROWSE_HINT_SKIP:
                hints[field_name] = val
                break  # first entry = highest matchCount

    return hints


def _category_confidence(pl_category: str, ebay_category: str) -> str:
    """Jaccard token overlap between product_lookup category and eBay category name."""
    _stop = {'a', 'an', 'the', 'and', 'or', 'of', 'in', 'for', 'by', 'to', '&'}
    a = {w.lower() for w in pl_category.split() if w.lower() not in _stop}
    b = {w.lower() for w in ebay_category.split() if w.lower() not in _stop}
    if not a or not b:
        return 'low'
    ratio = len(a & b) / len(a | b)
    if ratio >= 0.30:
        return 'high'
    if ratio >= 0.10:
        return 'medium'
    return 'low'


def _validate_category_suggestion(
    cfg: Dict[str, Any],
    title: str,
    resolved_category_id: str,
    top_n: int = 5,
) -> Dict[str, Any]:
    """Disabled 2026-07-02 (session 41): this used to fire a live
    getCategorySuggestions call on every drafted item purely for QA telemetry,
    duplicating ai_identify's category call on the same title moments earlier —
    confirmed as a live contributor to repeated Taxonomy API 429 exhaustion
    (session-39 audit finding #3). Always returns 'unavailable' now. Re-enable only
    once PP-CATPICK-001 makes this call provably unnecessary, or behind a disk cache /
    explicit opt-in — never as an unconditional per-item live call again.
    """
    return {'category_suggestions': [], 'category_agreement': 'unavailable'}


def _is_ebay_offline(exc: Exception) -> bool:
    """True if exc indicates eBay is unreachable (not an auth or client error)."""
    if isinstance(exc, (requests.exceptions.ConnectionError,
                        requests.exceptions.Timeout)):
        return True
    if isinstance(exc, requests.exceptions.HTTPError):
        status = exc.response.status_code if exc.response is not None else 0
        return status >= 500
    return False


def _write_offline_csv_row(cfg: Dict[str, Any], sku: str,
                           item: Dict[str, Any]) -> None:
    """Append a row to the offline draft CSV for later manual upload."""
    csv_path: Path = cfg['ebay_draft_csv_path']
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not csv_path.exists()
    with csv_path.open('a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=_OFFLINE_CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow({
            'sku':           sku,
            'title':         item.get('title', ''),
            'category_id':   item.get('ebay_category_id', ''),
            'category_name': item.get('ebay_category_name', ''),
            'condition':     item.get('condition', ''),
            'format':        'FixedPrice',
            'quantity':      1,
            'price':         '',
            'description':   item.get('description', ''),
        })


def _build_prompt(item: Dict[str, Any], aspects: List[Dict[str, Any]],
                  prefilled: Optional[Dict[str, str]] = None,
                  browse_hints: Optional[Dict[str, str]] = None) -> str:
    prefilled = prefilled or {}
    aspect_names = {a['name'] for a in aspects}
    lines = [
        f'Title: {item.get("title", "")}',
        f'Category: {item.get("ebay_category_name", "")}',
        f'Description: {item.get("description", "")}',
        f'Condition: {item.get("condition", "")}',
        '',
    ]
    if prefilled:
        lines.append('Known values from product database (include these verbatim in your JSON):')
        for k, v in prefilled.items():
            lines.append(f'  {k}: {v}')
        lines.append('')

    if browse_hints:
        applicable = {k: v for k, v in browse_hints.items()
                      if k in aspect_names and k not in prefilled}
        if applicable:
            lines.append('Common values from similar active eBay listings (use as context):')
            for k, v in applicable.items():
                lines.append(f'  {k}: "{v}"')
            lines.append('')

    remaining = [a for a in aspects if a['name'] not in prefilled]
    if remaining:
        lines.append('Aspects to fill:')
        for a in remaining:
            req = ' (REQUIRED)' if a['required'] else ''
            if a['allowed_values']:
                av = a['allowed_values']
                if len(av) <= 30:
                    vals = ', '.join(av)
                else:
                    vals = ', '.join(av[:30]) + f' ... ({len(av)} total)'
                lines.append(f'  {a["name"]}{req}: choose from [{vals}]')
            else:
                max_length = a.get('max_length')
                limit = f' (maximum {max_length} characters)' if max_length else ''
                lines.append(f'  {a["name"]}{req}: free text{limit}')
        lines.append('')
    lines.append('Photos of the item are attached — examine all of them before answering.')
    lines.append('Respond with JSON: {"Brand": "...", "Theme": "...", ...}')
    return '\n'.join(lines)


def _encode_resized(img_path: Path, max_px: int = _VISION_MAX_PX_ASPECTS) -> Optional[str]:
    """Return base64 JPEG, resized to max_px on the longest edge.

    Returns None (rather than raising) if *img_path* is truncated/corrupt and
    can't be decoded -- the same corruption class PP-DATAINTEGRITY-001 leg 1's
    photo_files_readable catalog-verify rule already detects project-wide
    (#1154, 206 bad files/149 SKUs). Before this fix the OSError propagated
    uncaught all the way to a bare dead_letter with no durable finding (todo
    #1403; confirmed live: 7-8 ebay_draft dead-letters, 'image file is
    truncated'/'broken data stream'). Callers are responsible for skipping a
    None result and recording a finding -- see _aspect_fill_photos, which
    already screens for this before a photo ever reaches here, so this catch
    is a second line of defense (e.g. a file that changes between selection
    and encode).
    """
    try:
        from PIL import Image
    except ImportError:
        return base64.b64encode(img_path.read_bytes()).decode()

    try:
        with Image.open(img_path) as img:
            img.thumbnail((max_px, max_px), Image.LANCZOS)
            buf = io.BytesIO()
            img.convert('RGB').save(buf, format='JPEG', quality=85)
    except OSError:
        return None
    return base64.b64encode(buf.getvalue()).decode()


def _aspect_fill_photos(
    item: Dict[str, Any],
    sku_dir: Path,
    provider: str,
    *,
    sku: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    finding_sink: Optional[List[Dict[str, Any]]] = None,
) -> List[Path]:
    """Select up to _MAX_PHOTOS_ASPECTS photos for the vision aspect-fill call.

    Cloud providers get the item's real photo set (excluding -alt./cropped-
    derivatives); anything else gets none (falls back to the text-only prompt).

    Truncated/corrupt photos are screened out here rather than left to raise
    OSError deep inside the vision call and dead-letter the whole job (todo
    #1403) -- this is the same corruption class PP-DATAINTEGRITY-001 leg 1's
    photo_files_readable catalog-verify rule already detects project-wide
    (#1154). When *sku*/*config* are supplied, each skip is recorded as a
    durable pipeline_error finding (invariant C11 -- a guard's skip is a
    finding, not a log line), reusing the existing generic pipeline_error
    mechanism (see api.py's _verify_item, which surfaces any pipeline_error
    as a catalog-verify violation) rather than inventing a second "corrupt
    photo" tracking scheme. Callers that don't pass sku/config (e.g. the
    existing pre-#1403 test suite) get filtering only, no persistence --
    matches every pre-existing call site's positional-args-only signature.
    """
    if provider not in CLOUD_PROVIDERS:
        return []
    all_photos = _asset_ordered_photos(item, sku_dir)
    candidates = [
        p for p in all_photos
        if '-alt.' not in p.name and not p.name.startswith('cropped-')
    ][:_MAX_PHOTOS_ASPECTS]

    readable: List[Path] = []
    unreadable: List[str] = []
    for p in candidates:
        try:
            from PIL import Image
            with Image.open(p) as im:
                im.load()
        except ImportError:
            # No PIL available -- can't pre-screen; trust the file the same
            # way _encode_resized falls back to a raw base64 read.
            readable.append(p)
            continue
        except OSError as exc:
            unreadable.append(f'{p.name}: {exc}')
            continue
        readable.append(p)

    if unreadable and sku and config is not None:
        log.warning('%s: %d unreadable/corrupt photo(s) skipped for vision '
                    'aspect-fill: %s', sku, len(unreadable), unreadable)
        finding = {
            'code':   'photo_files_readable',
            'detail': (f'{len(unreadable)} photo(s) unreadable/corrupt, skipped '
                       f'for vision aspect-fill (other readable photos/fields '
                       f'still used): {"; ".join(unreadable)}'),
            'ts':     datetime.now(timezone.utc).isoformat(),
            'source': 'ebay_draft',
        }
        if finding_sink is not None:
            finding_sink.append(finding)
        else:
            fence_patch_item(config, sku, {'pipeline_error': finding})
        tgw_logging.log_event('ebay_draft_unreadable_photo', sku=sku,
                              unreadable_count=len(unreadable))

    return readable


class EbayDraftWorker(QueueWorker):

    def _governed_receipt(
        self, payload: Dict[str, Any], sku: str, *, outcome: str,
        changed: bool, resulting_generation: str | None,
        operation_id: str = "", mutation_status: str = "",
    ) -> Dict[str, Any]:
        return {
            "receipt_schema_id": "treatment-receipt/v1",
            "treatment_id": "ebay-draft",
            "treatment_version": "1",
            "graph_id": payload["graph_id"],
            "goal_profile_id": payload["goal_profile_id"],
            "goal_profile_version": payload["goal_profile_version"],
            "object_generation": payload["object_generation"],
            "condition_hash": payload["condition_hash"],
            "entity_id": sku,
            "outcome": outcome,
            "established_conditions": (["draft_generated"]
                                       if outcome == "satisfied" else []),
            "evidence": {
                "changed": changed,
                "resulting_generation": resulting_generation,
                **({"operation_id": operation_id} if operation_id else {}),
                **({"mutation_status": mutation_status} if mutation_status else {}),
            },
        }

    def _commit_governed_draft(
        self, *, job: Dict[str, Any], payload: Dict[str, Any], sku: str,
        json_path: Path, checkpoint: Dict[str, Any],
    ) -> Dict[str, Any]:
        expected = {
            "schema": "ebay-draft-observation/v1",
            "sku": sku,
            "expected_generation": payload["object_generation"],
        }
        if any(checkpoint.get(key) != value for key, value in expected.items()):
            raise HardFailure("ebay_draft checkpoint identity mismatch")
        fields = checkpoint.get("fields")
        if not isinstance(fields, dict):
            raise HardFailure("ebay_draft checkpoint fields missing")
        mutation_payload = {
            "schema": checkpoint["schema"],
            "job_id": job["job_id"],
            "graph_id": payload["graph_id"],
            "fields": fields,
        }
        operation_id = operation_identity(
            sku=sku, kind="ebay-draft",
            expected_generation=payload["object_generation"],
            payload=mutation_payload,
        )
        if checkpoint.get("operation_id") != operation_id:
            raise HardFailure("ebay_draft checkpoint operation identity mismatch")

        def mutate(document: Dict[str, Any]) -> Dict[str, Any]:
            if document.get("sku") != sku:
                raise ValueError("authoritative document SKU mismatch")
            updated = dict(document)
            updated.update(fields)
            return updated

        def project(_sku: str, document: Dict[str, Any]) -> Dict[str, Any]:
            result = upsert_catalog_row(self.config, document)
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise RuntimeError("SQLite projection did not report success")
            return result

        data_root = Path(self.config.get("data_root", "/opt/TGW/data"))
        journal_root = Path(self.config.get(
            "item_mutation_journal_root", data_root.parent / "var/item-mutations",
        ))
        result = mutate_item(
            item_path=json_path,
            archive_root=Path(self.config.get(
                "archive_root", data_root / "ItemArchive",
            )),
            journal_root=journal_root,
            sku=sku,
            kind="ebay-draft",
            expected_generation=payload["object_generation"],
            payload=mutation_payload,
            mutate=mutate,
            project=project,
            operation_id=operation_id,
        )
        status = str(result.status).upper()
        if status == "REPAIR_REQUIRED":
            result = reconcile_mutation(
                item_path=json_path, journal_root=journal_root,
                operation_id=operation_id, project=project,
            )
            status = str(result.status).upper()
        if status == "CONFLICT":
            # The authoritative item moved while this draft was being built.
            # Finish this attempt with no established condition so the common
            # evaluator selects the next action from the winning generation.
            # Do not strand the item behind a dead letter that Retry cannot
            # repair.
            receipt = self._governed_receipt(
                payload, sku, outcome="satisfied", changed=False,
                resulting_generation=result.resulting_generation,
                operation_id=operation_id, mutation_status=status,
            )
            receipt["evidence"].update({
                "detail": result.detail,
                "reason_code": "MUTATION_CONFLICT_REEVALUATE",
            })
            return receipt
        if status != "COMMITTED":
            outcome = {
                "CONFLICT": "conflict", "REPAIR_REQUIRED": "repair_required",
            }.get(status, "failed")
            receipt = self._governed_receipt(
                payload, sku, outcome=outcome, changed=bool(result.changed),
                resulting_generation=result.resulting_generation,
                operation_id=operation_id, mutation_status=status,
            )
            receipt["evidence"]["detail"] = result.detail
            raise TreatmentFailure(
                f"ebay-draft mutation did not commit: {status}", receipt,
            )
        draft = fields.get("draft_listing")
        valid_draft = (
            isinstance(draft, dict)
            and isinstance(draft.get("title"), str)
            and bool(draft["title"].strip())
            and str(draft.get("category_id", "99")) != "99"
        )
        if not valid_draft:
            receipt = self._governed_receipt(
                payload, sku, outcome="partial", changed=bool(result.changed),
                resulting_generation=result.resulting_generation,
                operation_id=operation_id, mutation_status=status,
            )
            receipt["evidence"]["reason_code"] = "DRAFT_REQUIRES_OPERATOR_CATEGORY"
            raise TreatmentFailure(
                "ebay-draft committed fallback requiring operator category", receipt,
            )
        return self._governed_receipt(
            payload, sku, outcome="satisfied", changed=bool(result.changed),
            resulting_generation=result.resulting_generation,
            operation_id=operation_id, mutation_status=status,
        )

    def handle(self, job: Dict[str, Any]) -> None:
        payload = job.get('payload_json') or {}
        sku     = payload.get('sku', '')
        if not sku:
            raise HardFailure('ebay_draft job missing sku in payload')

        json_path = _cfg_sku_json(self.config, sku)
        if not json_path.exists():
            raise HardFailure(f'item JSON not found for {sku}')

        item = json.loads(json_path.read_text(encoding='utf-8'))
        if not inventory_available(item):
            raise HardFailure(
                f'{sku}: inventory is sold, terminal, or zero quantity; '
                'explicitly restore inventory before generating an eBay draft'
            )

        governed_keys = {
            "treatment_id", "treatment_version", "graph_id",
            "goal_profile_id", "goal_profile_version", "object_generation",
            "condition_hash",
        }
        governed = any(key in payload for key in governed_keys)
        if governed:
            required = governed_keys | {"entity_id"}
            if any(not isinstance(payload.get(key), str) or not payload[key].strip()
                   for key in required):
                raise HardFailure("ebay_draft governed job has incomplete identity")
            if payload["treatment_id"] != "ebay-draft" or payload["treatment_version"] != "1":
                raise HardFailure("ebay_draft governed treatment identity mismatch")
            if job.get("entity_type") != "item" or job.get("entity_id") != sku:
                raise HardFailure("ebay_draft governed entity envelope mismatch")
            if payload["entity_id"] != sku or payload.get("object_id", sku) != sku:
                raise HardFailure("ebay_draft governed payload entity mismatch")
            if not isinstance(job.get("job_id"), str) or not job["job_id"].strip():
                raise HardFailure("ebay_draft governed job_id missing")
            if not isinstance(job.get("lease_token"), str) or not job["lease_token"].strip():
                raise HardFailure("ebay_draft governed lease token missing")
            if (item_generation(item) != payload["object_generation"]
                    and "observation_checkpoint" not in payload):
                raise HardFailure("ebay_draft governed object generation mismatch")
            if "observation_checkpoint" in payload:
                checkpoint = state_machine.checkpoint_running_job(
                    job["job_id"], self.owner, job["lease_token"],
                    payload["observation_checkpoint"],
                )
                return self._commit_governed_draft(
                    job=job, payload=payload, sku=sku, json_path=json_path,
                    checkpoint=checkpoint,
                )

        title = item.get('title', '')
        if not title or title == sku:
            raise HardFailure(f'no title on {sku} — run ai_identify first')

        category_id   = item.get('ebay_category_id')
        category_name = item.get('ebay_category_name', '')

        # If taxonomy lookup failed during ai_identify, retry it here
        category_resolved_here = False
        if not category_id:
            log.info('no ebay_category_id for %s — retrying taxonomy lookup', sku)
            try:
                from tgw.apis.ebay.taxonomy import best_category
                category_id, category_name = best_category(
                    self.config, title, item.get('category', ''))
                if category_id:
                    item['ebay_category_id']   = category_id
                    item['ebay_category_name'] = category_name
                    category_resolved_here = True
                    log.info('taxonomy retry succeeded for %s: %s %s',
                             sku, category_id, category_name)
            except quota.QuotaBudgetExceeded:
                # code-review follow-up (#1181): best_category() deliberately
                # re-raises this so the job requeues transiently instead of
                # silently falling through to the '99 Everything Else'
                # fallback below — must not catch it here.
                raise
            except Exception as exc:
                log.warning('taxonomy retry failed for %s: %s', sku, exc)

        if not category_id:
            # No category at all — use a broad fallback so eBay prompts the
            # operator to select the correct leaf category when they open the draft
            category_id   = '99'   # eBay "Everything Else" — non-leaf, eBay will prompt
            category_name = 'Everything Else'
            log.warning('%s: no category found — staging with fallback category 99', sku)

        # Fetch aspects — category 99 is a non-leaf catch-all; eBay returns 400
        # for it so skip the call and let the operator set specifics in Seller Hub.
        if category_id == '99':
            aspects: List[Dict[str, Any]] = []
            log.warning('%s: fallback category 99 — skipping aspects (set in Seller Hub)', sku)
        else:
            try:
                aspects = get_aspects(self.config, category_id)
            except Exception as exc:
                if _is_ebay_offline(exc):
                    if governed:
                        receipt = self._governed_receipt(
                            payload, sku, outcome="failed", changed=False,
                            resulting_generation=payload["object_generation"],
                        )
                        receipt["evidence"]["reason_code"] = "EBAY_OBSERVATION_UNAVAILABLE"
                        raise TreatmentFailure(
                            "ebay-draft provider observation unavailable", receipt,
                        )
                    _write_offline_csv_row(self.config, sku, item)
                    item['offline_draft'] = True
                    fence_patch_item(self.config, sku, {'offline_draft': True})
                    log.warning('eBay unreachable for %s (%s) — wrote offline CSV row', sku, exc)
                    tgw_logging.log_event('ebay_draft_offline', sku=sku,
                                          reason=type(exc).__name__)
                    return
                raise
            log.info('fetched %d aspects for category %s', len(aspects), category_id)

        # Preserve the exact required category schema in Set A, including
        # intentionally empty fields.  The model/draft path may not be able to
        # ground every value, but the operator must see the real controls.
        _required_schema_patch = inventory_record.ensure_required_category_fields(
            item, aspects,
        )
        if _required_schema_patch:
            item['item_attributes'] = _required_schema_patch['item_attributes']
            item['item_attributes_history'] = _required_schema_patch['item_attributes_history']

        # Phase 2a — Browse API aspect hints (best-effort; supplements AI with market signal)
        browse_hints: Dict[str, str] = {}
        if category_id != '99' and aspects:
            browse_hints = _fetch_browse_aspect_hints(self.config, title, category_id)
            if browse_hints:
                log.info('%s: browse hints for %d aspects: %s',
                         sku, len(browse_hints), list(browse_hints.keys()))

        # Phase 2 — pre-fill known specifics from product_lookup (authoritative over AI)
        pl = item.get('product_lookup') or {}
        aspect_names = {a['name'] for a in aspects}
        _PL_ASPECT_MAP = [
            ('brand', 'Brand'), ('mpn', 'MPN'), ('mpn', 'Model'),
            ('ean', 'EAN'), ('upc', 'UPC'), ('isbn', 'ISBN'),
        ]
        prefilled: Dict[str, str] = {}
        for pl_key, aspect_name in _PL_ASPECT_MAP:
            val = (pl.get(pl_key) or '').strip()
            if not val or aspect_name in prefilled:
                continue
            if aspect_name not in aspect_names:
                continue
            # Validate against SELECTION_ONLY allowed values
            aspect_def = next((a for a in aspects if a['name'] == aspect_name), None)
            if aspect_def and aspect_def['mode'] == 'SELECTION_ONLY' and aspect_def['allowed_values']:
                if val not in aspect_def['allowed_values']:
                    log.debug('prefill: %r not in allowed values for %r — skipping', val, aspect_name)
                    continue
            prefilled[aspect_name] = val

        if prefilled:
            log.info('%s: pre-filled %d specifics from product_lookup: %s',
                     sku, len(prefilled), list(prefilled.keys()))

        # Phase 2b — pre-fill from item_attributes (AI-identified attributes, lower
        # priority than product_lookup so they only fill what's not already set)
        # todo #1416: routed through the named Set A -> Set B translation
        # function (tgw.ebay.aspect_translation) — the ONE legitimate
        # cross-set translation point in the codebase, extracted from what
        # used to be inline logic here (no behavior change).
        ia = inventory_record.get_inventory_fields(item)
        ia_translated = translate_inventory_to_ebay_draft(
            ia, category_id, self.config, aspects=aspects, already_filled=prefilled)
        ia_filled: List[str] = list(ia_translated.keys())
        prefilled.update(ia_translated)

        if ia_filled:
            log.info('%s: pre-filled %d specifics from item_attributes: %s',
                     sku, len(ia_filled), ia_filled)

        # Use a vision model to fill aspect values — looks at the item's actual
        # photos (up to _MAX_PHOTOS_ASPECTS) instead of guessing from title text
        # alone, so details only visible in one photo (barcode, tag, engraving)
        # aren't silently missed. Routed through bulk_classify (cheap, free-tier
        # Gemini) since this is high-volume structured extraction, not prose.
        remaining_aspects = [a for a in aspects if a['name'] not in prefilled]
        photo_findings: List[Dict[str, Any]] = []
        if not remaining_aspects:
            suggested: Dict[str, Any] = {}
            log.info('%s: all %d aspects already prefilled — skipping vision call',
                     sku, len(aspects))
        else:
            prompt = _build_prompt(item, aspects, prefilled=prefilled, browse_hints=browse_hints)
            _classify_provider, _classify_model = get_task_model(self.config, 'bulk_classify')
            sku_dir = json_path.parent
            photos = _aspect_fill_photos(
                item, sku_dir, _classify_provider, sku=sku, config=self.config,
                finding_sink=(photo_findings if governed else None),
            )
            img_b64_list = []
            for p in photos:
                b64 = _encode_resized(p)
                if b64 is None:
                    # Second-line defense: became unreadable between
                    # selection and encode (see _encode_resized docstring).
                    log.warning('%s: %s unreadable at encode time -- skipping', sku, p.name)
                    continue
                img_b64_list.append(b64)
            log.info('asking %s/%s to fill %d aspects for %s (%d photos)',
                     _classify_provider, _classify_model, len(remaining_aspects), sku, len(photos))
            tgw_logging.log_event('ebay_draft_aspects_call', sku=sku,
                                  category_id=category_id, aspect_count=len(remaining_aspects),
                                  photo_count=len(photos))

            raw = call_model('bulk_classify', _SYSTEM, prompt, self.config,
                             img_b64_list=img_b64_list, sku=sku)
            try:
                suggested = extract_json(raw)
            except Exception as exc:
                # audit#1143 #1249: 200 chars was too short to see whether a
                # failure was a genuinely malformed response or just missing
                # its closing ```fence beyond the cutoff -- every one of the
                # 95 dead-lettered jobs of this class was undiagnosable after
                # the fact because the truncated text always looked
                # identically "cut off mid-JSON." 2000 chars covers a full
                # aspect-fill response in the overwhelming majority of cases.
                raise HardFailure(
                    f'ebay_draft: model returned non-JSON for {sku}: {raw[:2000]}'
                ) from exc

        # Phase 5 — description enrichment: if product_lookup has a substantive
        # description, ask the model to produce a 200+ word eBay description that
        # weaves in the product data, brand/MPN, and the AI's visual observation.
        pl_description = (pl.get('description') or '').strip()
        enrich_description = bool(pl_description and len(pl_description.split()) >= 20)

        if enrich_description:
            brand = pl.get('brand', '') or prefilled.get('Brand', '')
            mpn   = pl.get('mpn', '')   or prefilled.get('MPN', '') \
                                         or prefilled.get('Model', '')
            desc_prompt = (
                f'Item: {title}\n'
                f'Condition: {item.get("condition", "used")}\n'
                + (f'Brand: {brand}\n' if brand else '')
                + (f'Model/MPN: {mpn}\n' if mpn else '')
                + f'\nProduct information:\n{pl_description}\n'
                + f'\nWhat the photos show:\n{item.get("description", "")}\n'
                + '\nWrite the eBay listing description.'
            )
            raw_desc = call_model('ebay_draft', _SYSTEM_DESC, desc_prompt, self.config, sku=sku)
            enriched_description = raw_desc.strip()
            log.info('%s: description enriched to %d words',
                     sku, len(enriched_description.split()))
        else:
            enriched_description = None

        # Filter nulls and validate SELECTION_ONLY values; merge prefilled on top
        item_specifics: Dict[str, str] = {}
        for aspect in aspects:
            name = aspect['name']
            val  = suggested.get(name)
            if not val:
                continue
            val = str(val).strip()
            if aspect['mode'] == 'SELECTION_ONLY' and aspect['allowed_values']:
                if val not in aspect['allowed_values']:
                    log.warning('invalid value %r for %r — skipping', val, name)
                    continue
            max_length = aspect.get('max_length')
            if max_length and len(val) > max_length:
                log.warning('over-length value for %r (%d > %d) — skipping',
                            name, len(val), max_length)
                continue
            item_specifics[name] = val

        # Prefilled values override AI output (product database is authoritative)
        item_specifics.update(prefilled)

        # Product lookup values are authoritative about identity, but not
        # exempt from category limits.  Do not silently truncate facts: leave
        # an over-limit value absent for operator correction instead of making
        # a publish that eBay will certainly reject.
        for aspect in aspects:
            max_length = aspect.get('max_length')
            name = aspect['name']
            value = item_specifics.get(name)
            if max_length and isinstance(value, str) and len(value) > max_length:
                log.warning('over-length prefilled value for %r (%d > %d) — removing',
                            name, len(value), max_length)
                item_specifics.pop(name, None)

        # Backfill required aspects the AI left blank — eBay rejects at staging
        # if any required aspect is missing.
        _UNBRANDED_FALLBACKS = ('Unbranded', 'Does Not Apply', 'N/A')
        for aspect in aspects:
            if not aspect['required'] or aspect['name'] in item_specifics:
                continue
            av = aspect['allowed_values']
            fallback: Optional[str] = None
            for candidate in _UNBRANDED_FALLBACKS:
                if not av or candidate in av:
                    fallback = candidate
                    break
            if fallback:
                item_specifics[aspect['name']] = fallback
                log.info('required aspect %r not filled by AI — defaulting to %r',
                         aspect['name'], fallback)

        # Collect aspect stats for quality scoring
        req_aspects = [a for a in aspects if a['required']]
        rec_aspects = [a for a in aspects if not a['required'] and a.get('allowed_values')]
        req_filled_count = sum(1 for a in req_aspects if a['name'] in item_specifics)
        rec_filled_count = sum(1 for a in rec_aspects if a['name'] in item_specifics)

        # Resolve condition — look up the best allowed conditionId for this category.
        # Never upgrades condition; falls back same-or-worse. Stores both the
        # conditionId (what eBay validates) and the buyer-facing label.
        raw_condition = item.get('condition', '')
        cond_result = None
        if category_id != '99':
            try:
                cond_result = best_condition(self.config, category_id, raw_condition)
            except Exception as exc:
                log.warning('%s: condition lookup failed (%s) — will use enum fallback',
                            sku, exc)
        if cond_result:
            log.info('%s: condition %r → %s (%s)',
                     sku, raw_condition,
                     cond_result['condition_id'], cond_result['condition_label'])
        else:
            log.warning('%s: no valid condition found for %r in category %s — '
                        'needs manual review', sku, raw_condition, category_id)

        # Count raw image files — photo score input (photos present before upload)
        sku_dir = json_path.parent
        _IMG_SFXS = {'.jpg', '.jpeg', '.png', '.JPG', '.JPEG', '.PNG'}
        photo_count = sum(1 for p in sku_dir.iterdir()
                          if p.is_file() and p.suffix in _IMG_SFXS)

        # Phase 4 — category confidence: compare product_lookup category hint
        # against the eBay taxonomy category we resolved.
        cat_confidence = None
        pl_cat = (pl.get('category') or '').strip()
        if pl_cat and category_name:
            cat_confidence = _category_confidence(pl_cat, category_name)
            if cat_confidence == 'low':
                log.info('%s: category confidence LOW — product_lookup=%r ebay=%r',
                         sku, pl_cat, category_name)

        # Build draft listing block
        effective_description = enriched_description or item.get('description', '')
        _prev_dl = item.get('draft_listing') or {}
        # todo #1418: item_specifics is Set B's envelope, written ONLY through
        # tgw.ebay.draft_specifics. ebay_draft rebuilds the full aspect set from
        # scratch each run (not an incremental merge), so the envelope's `fields`
        # is a full replace — but history still records genuinely-changed keys,
        # via the accessor's diff-against-existing logic, matching price_history's
        # "only append on a real change" discipline.
        _specifics_hist_patch = set_ebay_aspects(item, item_specifics, source='ebay_draft')
        item_specifics_envelope = wrap_ebay_specifics(item_specifics)
        draft: Dict[str, Any] = {
            'title':                      title,
            'category_id':                category_id,
            'category_name':              item.get('ebay_category_name', ''),
            'condition':                  raw_condition,
            'condition_id':               cond_result['condition_id']    if cond_result else None,
            'condition_label':            cond_result['condition_label'] if cond_result else None,
            'condition_enum':             cond_result['condition_enum']  if cond_result else None,
            'format':                     'FixedPrice',
            'quantity':                   1,
            'price':                      _prev_dl.get('price'),
            'shipping_profile':           _prev_dl.get('shipping_profile'),
            'item_specifics':             item_specifics_envelope,
            'item_specifics_history':     _specifics_hist_patch['item_specifics_history'],
            'description':                effective_description,
            'aspects_category_id':        category_id,
            'aspects_required_total':     len(req_aspects),
            'aspects_required_filled':    req_filled_count,
            'aspects_recommended_total':  len(rec_aspects),
            'aspects_recommended_filled': rec_filled_count,
        }
        if cat_confidence:
            draft['category_confidence'] = cat_confidence
        sc_id = _get_store_category_id(item, self.config)
        if sc_id is not None:
            draft['store_category_id'] = sc_id
        if enriched_description:
            draft['description_source'] = 'enriched'
        if browse_hints:
            aspect_names_set = {a['name'] for a in aspects}
            applicable_hints = {k for k in browse_hints
                                if k in aspect_names_set and k not in prefilled}
            draft['browse_hint_count'] = len(applicable_hints)

        # Build full eBay listing description: AI text + boilerplate footer + picklist line
        item['draft_listing'] = draft   # temporary — needed by build_listing_description
        draft['listing_description'] = build_listing_description(item, self.config)

        # Phase 1 — enhance title using product_lookup (brand/MPN injection + flags)
        from tgw.seo.title import enhance_title
        seo = enhance_title(title, pl, item_specifics)
        draft['title'] = seo['title']
        if 'title_ai' in seo:
            draft['title_ai'] = seo['title_ai']
            log.info('%s: title enhanced: %r → %r', sku, seo['title_ai'], seo['title'])
        if seo['flags']:
            draft['title_flags'] = seo['flags']
            log.info('%s: title flags: %s', sku, seo['flags'])

        # Category validation via Taxonomy getCategorySuggestions (PP-VERIFY-001 signal)
        # Uses the finalised SEO title for the query; never changes category_id.
        if category_id != '99':
            cat_val = _validate_category_suggestion(self.config, draft['title'], category_id)
            draft['category_suggestions'] = cat_val['category_suggestions']
            draft['category_agreement']   = cat_val['category_agreement']
            if cat_val['category_agreement'] == 'mismatch':
                top_name = (
                    cat_val['category_suggestions'][0]['category_name']
                    if cat_val['category_suggestions'] else '(none)'
                )
                log.info('%s: category agreement MISMATCH — taxonomy top=%r, resolved=%r',
                         sku, top_name, category_name)

        # Compute listing quality score — stored in draft; re-scored after pricing adds comps
        from tgw.listing_quality import score_draft
        draft['quality'] = score_draft(item, photo_count=photo_count).to_dict()

        item['draft_listing'] = draft
        patch_fields: Dict[str, Any] = {
            'draft_listing': draft,
            # AI Identify/Reidentify requests a fresh draft without deleting
            # the operator's existing draft before replacement is ready.
            'ai_redraft_requested': None,
        }
        if _required_schema_patch:
            patch_fields['item_attributes'] = _required_schema_patch['item_attributes']
            patch_fields['item_attributes_history'] = _required_schema_patch['item_attributes_history']
        if governed and photo_findings:
            patch_fields['pipeline_error'] = photo_findings[-1]
        if category_resolved_here:
            # Session 41 fix: this used to be mutated in memory only — never
            # persisted — so every subsequent re-draft of an item ai_identify
            # failed to categorize burned another live Taxonomy API call
            # re-resolving the exact same category, on top of everything else
            # already straining that quota today.
            patch_fields['ebay_category_id']   = item['ebay_category_id']
            patch_fields['ebay_category_name'] = item['ebay_category_name']
        if governed:
            mutation_payload = {
                "schema": "ebay-draft-observation/v1",
                "job_id": job["job_id"],
                "graph_id": payload["graph_id"],
                "fields": patch_fields,
            }
            checkpoint = {
                "schema": "ebay-draft-observation/v1",
                "sku": sku,
                "expected_generation": payload["object_generation"],
                "fields": patch_fields,
                "operation_id": operation_identity(
                    sku=sku, kind="ebay-draft",
                    expected_generation=payload["object_generation"],
                    payload=mutation_payload,
                ),
            }
            checkpoint = state_machine.checkpoint_running_job(
                job["job_id"], self.owner, job["lease_token"], checkpoint,
            )
            return self._commit_governed_draft(
                job=job, payload=payload, sku=sku, json_path=json_path,
                checkpoint=checkpoint,
            )
        fence_patch_item(self.config, sku, patch_fields)

        log.info('ebay_draft complete for %s: %d specifics filled', sku, len(item_specifics))
        tgw_logging.log_event('ebay_draft_complete', sku=sku,
                              specifics_filled=len(item_specifics),
                              item_specifics=item_specifics)


        # If the item is already staged/live on eBay, the regenerated draft is
        # NOT pushed automatically. Dave's rule (session 42): "we cannot have
        # uninspected AI changes going live automatically — they are rarely
        # correct so far." The draft waits as a pending update; the operator
        # inspects it in the item editor and pushes via the UI (Update
        # Listing), which enqueues ebay_stage with origin='operator'.
        if item.get('ebay_offer', {}).get('offer_id'):
            log.info('%s: re-draft complete — pending operator inspection '
                     '(NOT auto-pushed to the existing offer)', sku)
            tgw_logging.log_event('ebay_draft_live_update_pending', sku=sku,
                                  offer_id=item['ebay_offer']['offer_id'])

        return {"ok": True, "sku": sku}




def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-ebay-draft-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = EbayDraftWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
