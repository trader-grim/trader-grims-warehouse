"""
tgw.workers.ebay_draft — eBay draft listing worker.

Fetches item specifics (aspects) for the item's eBay category, uses
Qwen2.5 (text) to suggest values based on item data, then writes a
draft_listing block to the item JSON ready for human review before upload.

Enqueued by ai_identify after category resolution. Safe to re-run —
will overwrite draft_listing but never touch title/description/condition.
"""

from __future__ import annotations

import csv
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2.errors
import requests

import tgw.logging as tgw_logging
from tgw.apis.ebay.client import ebay_get
from tgw.apis.ebay.conditions import best_condition
from tgw.apis.ebay.specifics import get_aspects
from tgw.apis.fence import patch_item as fence_patch_item
from tgw.apis.llm import call_model, get_task_model
from tgw.apis.ollama import extract_json
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.ebay.description import build_listing_description
from tgw.queue import state_machine
from tgw.queue.worker_base import HardFailure, QueueWorker

log = logging.getLogger(__name__)

QUEUE_NAME  = 'ebay_draft'

_SYSTEM = """\
You are an eBay listing assistant. Given item details and a list of eBay item
specifics (aspects), suggest the best value for each aspect.
For SELECTION_ONLY aspects, you MUST choose from the allowed values listed.
For FREE_TEXT aspects, suggest a concise, accurate value.
If an aspect does not apply, use null.
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
    """Query getCategorySuggestions with the drafted title and compute agreement.

    Fail-soft: any API error returns ``{'category_agreement': 'unavailable'}``.

    Returns::

        {
            'category_suggestions': [{'category_id': str, 'category_name': str}, ...],
            'category_agreement': 'agreed' | 'mismatch' | 'unavailable',
        }

    Agreement is ``'agreed'`` if the resolved category is in the top-3
    suggestions; ``'mismatch'`` otherwise.  No category_choice change is made.
    """
    try:
        from tgw.apis.ebay.taxonomy import get_category_suggestions
        raw_suggestions = get_category_suggestions(cfg, title)
    except Exception as exc:
        log.debug('category validation unavailable for %r: %s', title, exc)
        return {'category_suggestions': [], 'category_agreement': 'unavailable'}

    simplified = [
        {
            'category_id':   s.get('category', {}).get('categoryId'),
            'category_name': s.get('category', {}).get('categoryName'),
        }
        for s in raw_suggestions[:top_n]
        if s.get('category', {}).get('categoryId')
    ]

    top_ids = {s['category_id'] for s in simplified[:3]}
    agreement = 'agreed' if str(resolved_category_id) in top_ids else 'mismatch'

    return {
        'category_suggestions': simplified,
        'category_agreement':   agreement,
    }


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
                lines.append(f'  {a["name"]}{req}: free text')
        lines.append('')
    lines.append('Respond with JSON: {"Brand": "...", "Theme": "...", ...}')
    return '\n'.join(lines)


class EbayDraftWorker(QueueWorker):

    def handle(self, job: Dict[str, Any]) -> None:
        payload = job.get('payload_json') or {}
        sku     = payload.get('sku', '')
        if not sku:
            raise HardFailure('ebay_draft job missing sku in payload')

        json_path = self.config['itemdata_root'] / sku / f'{sku}.json'
        if not json_path.exists():
            raise HardFailure(f'item JSON not found for {sku}')

        item = json.loads(json_path.read_text(encoding='utf-8'))

        title = item.get('title', '')
        if not title or title == sku:
            raise HardFailure(f'no title on {sku} — run ai_identify first')

        category_id   = item.get('ebay_category_id')
        category_name = item.get('ebay_category_name', '')

        # If taxonomy lookup failed during ai_identify, retry it here
        if not category_id:
            log.info('no ebay_category_id for %s — retrying taxonomy lookup', sku)
            try:
                from tgw.apis.ebay.taxonomy import best_category
                category_id, category_name = best_category(
                    self.config, title, item.get('category', ''))
                if category_id:
                    item['ebay_category_id']   = category_id
                    item['ebay_category_name'] = category_name
                    log.info('taxonomy retry succeeded for %s: %s %s',
                             sku, category_id, category_name)
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
                    _write_offline_csv_row(self.config, sku, item)
                    item['offline_draft'] = True
                    fence_patch_item(self.config, sku, {'offline_draft': True})
                    log.warning('eBay unreachable for %s (%s) — wrote offline CSV row', sku, exc)
                    tgw_logging.log_event('ebay_draft_offline', sku=sku,
                                          reason=type(exc).__name__)
                    return
                raise
            log.info('fetched %d aspects for category %s', len(aspects), category_id)

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
        ia = item.get('item_attributes') or {}
        ia_filled: List[str] = []
        for attr_name, attr_val in ia.items():
            if not attr_val or attr_name in prefilled:
                continue
            if attr_name not in aspect_names:
                continue
            val = str(attr_val).strip()
            if not val:
                continue
            aspect_def = next((a for a in aspects if a['name'] == attr_name), None)
            if aspect_def and aspect_def['mode'] == 'SELECTION_ONLY' and aspect_def['allowed_values']:
                if val not in aspect_def['allowed_values']:
                    log.debug('item_attr prefill: %r not in allowed values for %r — skipping',
                              val, attr_name)
                    continue
            prefilled[attr_name] = val
            ia_filled.append(attr_name)

        if ia_filled:
            log.info('%s: pre-filled %d specifics from item_attributes: %s',
                     sku, len(ia_filled), ia_filled)

        # Use text model to fill aspect values
        prompt  = _build_prompt(item, aspects, prefilled=prefilled, browse_hints=browse_hints)
        _, _draft_model = get_task_model(self.config, 'ebay_draft')
        log.info('asking %s to fill %d aspects for %s', _draft_model, len(aspects), sku)
        tgw_logging.log_event('ebay_draft_aspects_call', sku=sku,
                              category_id=category_id, aspect_count=len(aspects))

        # Phase 5 — description enrichment: if product_lookup has a substantive
        # description, ask the model to produce a 200+ word eBay description that
        # weaves in the product data, brand/MPN, and the AI's visual observation.
        pl_description = (pl.get('description') or '').strip()
        enrich_description = bool(pl_description and len(pl_description.split()) >= 20)

        raw = call_model('ebay_draft', _SYSTEM, prompt, self.config, sku=sku)

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

        try:
            suggested = extract_json(raw)
        except Exception as exc:
            raise HardFailure(
                f'ebay_draft: model returned non-JSON for {sku}: {raw[:200]}'
            ) from exc

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
            item_specifics[name] = val

        # Prefilled values override AI output (product database is authoritative)
        item_specifics.update(prefilled)

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
            'item_specifics':             item_specifics,
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
        fence_patch_item(self.config, sku, {'draft_listing': draft})

        log.info('ebay_draft complete for %s: %d specifics filled', sku, len(item_specifics))
        tgw_logging.log_event('ebay_draft_complete', sku=sku,
                              specifics_filled=len(item_specifics),
                              item_specifics=item_specifics)

        try:
            state_machine.enqueue_job(
                queue_name='catalog_rebuild',
                payload={'reason': f'ebay_draft:{sku}'},
                dedupe_key='catalog_rebuild:pending',
                not_before=time.time() + 30,
                max_attempts=3,
            )
        except psycopg2.errors.UniqueViolation:
            pass

        try:
            state_machine.enqueue_job(
                queue_name='ebay_price',
                payload={'sku': sku},
                dedupe_key=f'ebay_price:{sku}',
                max_attempts=5,
            )
        except psycopg2.errors.UniqueViolation:
            pass

        try:
            state_machine.enqueue_job(
                queue_name='ebay_upload',
                payload={'sku': sku},
                dedupe_key=f'ebay_upload:{sku}',
                max_attempts=5,
            )
        except psycopg2.errors.UniqueViolation:
            pass


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
