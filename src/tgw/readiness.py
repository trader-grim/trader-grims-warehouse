"""
tgw.readiness — Marketplace-agnostic listing readiness checker.

Usage:
    from tgw.readiness import check_ebay, readiness_html
    fields = check_ebay(item)
    html   = readiness_html(fields)
"""
from __future__ import annotations

import html as _html
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple


def _condition_valid_for_category(
    catalog_root: Optional[Path], category_id: str, condition_enum: str
) -> Tuple[bool, str]:
    """
    Return (is_valid, note) by checking condition_enum against the cached policy.
    Falls back to (True, '') when the cache is unavailable (never blocks publish
    on a lookup failure).
    """
    if not catalog_root or not category_id or not condition_enum:
        return True, ""
    try:
        import json as _json
        cache = catalog_root / "ebay-condition-policies.json"
        if not cache.exists():
            return True, ""
        raw = _json.loads(cache.read_text(encoding="utf-8"))
        allowed_list = (raw.get("policies") or {}).get(str(category_id), [])
        if not allowed_list:
            return True, ""  # no policy entry — can't validate
        # allowed_list is [[conditionId, desc], ...]
        from tgw.apis.ebay.conditions import CONDITION_ID_TO_ENUM
        allowed_enums = {
            CONDITION_ID_TO_ENUM.get(str(entry[0] if isinstance(entry, (list, tuple)) else entry), "")
            for entry in allowed_list
        }
        # Multi-enum conditionIds: e.g. cid 3000 maps to USED_EXCELLENT in the map,
        # but some categories only accept 4000/5000/6000 (Very Good/Good/Acceptable).
        # Build the reverse so we can check all enums for allowed cids.
        all_allowed_enums = {e for e in allowed_enums if e}
        if condition_enum in all_allowed_enums:
            return True, ""
        return False, f"not allowed in category {category_id} (re-draft or change condition)"
    except Exception:
        return True, ""  # never hard-fail on cache lookup

@dataclass
class ReadinessField:
    name:     str   # machine key
    label:    str   # display label
    status:   str   # 'ok' | 'missing' | 'warning'
    severity: str   # 'required' | 'recommended' | 'info'
    value:    Any   # current value or None
    jump_to:  str   # CSS anchor id in the editor


class ReadinessChecker:
    marketplace: str = "unknown"

    def check(self, item: dict) -> List[ReadinessField]:
        raise NotImplementedError


class EbayReadinessChecker(ReadinessChecker):
    marketplace = "ebay"

    def check(self, item: dict) -> List[ReadinessField]:  # noqa: C901
        fields: List[ReadinessField] = []
        dl = item.get("draft_listing") or {}
        ep = item.get("ebay_photos") or []
        pl = item.get("product_lookup") or {}

        def _f(name, label, status, severity, value, jump_to):
            fields.append(ReadinessField(name, label, status, severity, value, jump_to))

        # ── REQUIRED ──────────────────────────────────────────────────────

        title = str(dl.get("title") or "").strip()
        tlen  = len(title)
        if tlen >= 1 and tlen <= 80:
            _f("ebay_title", "eBay title", "ok", "required",
               title[:60] + ("…" if tlen > 60 else ""), "dl-title")
        elif tlen > 80:
            _f("ebay_title", "eBay title", "warning", "required",
               f"{tlen} chars (max 80)", "dl-title")
        else:
            _f("ebay_title", "eBay title", "missing", "required", None, "dl-title")

        cat_id = str(dl.get("category_id") or "").strip()
        if cat_id and cat_id != "99":
            _f("ebay_category", "Category", "ok", "required",
               f"{cat_id} · {dl.get('category_name','')}", "dl-category")
        else:
            _f("ebay_category", "Category", "missing", "required", None, "dl-category")

        cond_enum = str(dl.get("condition_enum") or dl.get("condition") or "").strip()
        cat_id_for_cond = str(dl.get("category_id") or "").strip()
        _catalog_root = item.get("_catalog_root")  # injected by http_server if available
        if cond_enum:
            _cond_valid, _cond_note = _condition_valid_for_category(
                _catalog_root, cat_id_for_cond, cond_enum
            )
            _cond_display = dl.get("condition_label") or dl.get("condition_description") or cond_enum
            if _cond_valid:
                _f("ebay_condition", "Condition", "ok", "required",
                   _cond_display, "dl-condition")
            else:
                _f("ebay_condition", "Condition", "warning", "required",
                   f"{_cond_display} · {_cond_note}", "dl-condition")
        else:
            _f("ebay_condition", "Condition", "missing", "required", None, "dl-condition")

        price = dl.get("price")
        eo = item.get("ebay_offer") or {}
        price_conf = (
            eo.get("price_comps", {}).get("confidence")
            or eo.get("price_confidence")
            or dl.get("price_confidence")
            or ""
        )
        try:
            pf = float(price)
            if pf > 0:
                _conf_note = f" · comp confidence: {price_conf}" if price_conf else ""
                _status = "warning" if price_conf == "low" else "ok"
                _f("ebay_price", "Price", _status, "required",
                   f"${pf:.2f}{_conf_note}", "dl-price")
            else:
                _f("ebay_price", "Price", "missing", "required", "0 or negative", "dl-price")
        except (TypeError, ValueError):
            _f("ebay_price", "Price", "missing", "required", None, "dl-price")

        eps_urls = ep or dl.get("imageUrls") or dl.get("image_urls") or []
        if eps_urls:
            _f("ebay_photos", "Photos on eBay (EPS)", "ok", "required",
               f"{len(eps_urls)} uploaded", "eps-photos")
        else:
            _f("ebay_photos", "Photos on eBay (EPS)", "missing", "required", None, "eps-photos")

        req_total  = int(dl.get("aspects_required_total") or 0)
        req_filled = int(dl.get("aspects_required_filled") or 0)
        _aspects_cat = str(dl.get("aspects_category_id") or "").strip()
        _draft_cat   = str(dl.get("category_id") or "").strip()
        _aspects_stale = bool(_aspects_cat and _draft_cat and _aspects_cat != _draft_cat)
        if _aspects_stale:
            _f("ebay_required_aspects", "Required aspects", "warning", "required",
               f"stale — computed for category {_aspects_cat}, now {_draft_cat} · re-draft needed",
               "dl-aspects")
        elif req_total == 0:
            _f("ebay_required_aspects", "Required aspects", "ok", "required",
               "none for this category", "dl-aspects")
        elif req_filled >= req_total:
            _f("ebay_required_aspects", "Required aspects", "ok", "required",
               f"all {req_total} filled", "dl-aspects")
        else:
            _f("ebay_required_aspects", "Required aspects", "missing", "required",
               f"{req_total - req_filled} of {req_total} missing", "dl-aspects")

        # ── RECOMMENDED ───────────────────────────────────────────────────

        desc = str(dl.get("description") or item.get("description") or "").strip()
        if len(desc) > 50:
            _f("ebay_description", "Description", "ok", "recommended",
               f"{len(desc.split())} words", "dl-description")
        else:
            _f("ebay_description", "Description", "warning", "recommended",
               "too short or missing", "dl-description")

        if len(eps_urls) >= 3:
            _f("ebay_photos_count", "Photo count (≥3 recommended)", "ok", "recommended",
               str(len(eps_urls)), "eps-photos")
        else:
            _f("ebay_photos_count", "Photo count (≥3 recommended)", "warning", "recommended",
               str(len(eps_urls)), "eps-photos")

        rec_total  = int(dl.get("aspects_recommended_total") or 0)
        rec_filled = int(dl.get("aspects_recommended_filled") or 0)
        if _aspects_stale:
            _f("ebay_recommended_aspects", "Recommended aspects", "warning", "recommended",
               "stale — re-draft needed", "dl-aspects")
        elif rec_total == 0:
            _f("ebay_recommended_aspects", "Recommended aspects", "ok", "recommended",
               "none for this category", "dl-aspects")
        elif rec_filled >= rec_total:
            _f("ebay_recommended_aspects", "Recommended aspects", "ok", "recommended",
               f"all {rec_total} filled", "dl-aspects")
        else:
            _f("ebay_recommended_aspects", "Recommended aspects", "warning", "recommended",
               f"{rec_total - rec_filled} of {rec_total} empty", "dl-aspects")

        upc = item.get("upc") or pl.get("ean") or pl.get("upc")
        if upc:
            _f("ebay_upc", "UPC / GTIN", "ok", "recommended", str(upc), "catalog-upc")
        else:
            _f("ebay_upc", "UPC / GTIN", "warning", "recommended", None, "catalog-upc")

        # ── INFO ──────────────────────────────────────────────────────────

        ship = dl.get("shipping_profile") or dl.get("fulfillment_policy_id") or "auto-resolved"
        _f("ebay_fulfillment", "Fulfillment policy", "ok", "info", str(ship), "dl-shipping")

        floor     = item.get("floor_price")
        floor_val = f"${float(floor):.2f}" if floor is not None else "not set"
        _f("ebay_floor_price", "Floor price", "ok", "info", floor_val, "catalog-floor")

        return fields


def check_ebay(item: dict) -> List[ReadinessField]:
    return EbayReadinessChecker().check(item)


_STATUS_STYLE = {
    ("missing", "required"):    ("#3a1a1a", "#c44", "❌"),
    ("warning", "required"):    ("#3a1a1a", "#c44", "⚠️"),
    ("warning", "recommended"): ("#2a2a0a", "#aa0", "⚠️"),
    ("ok",      "required"):    ("#1a2a1a", "#4a4", "✅"),
    ("ok",      "recommended"): ("#1a2a1a", "#4a4", "✅"),
    ("ok",      "info"):        ("#1a1a2a", "#44a", "ℹ️"),
}
_DEFAULT_STYLE = ("#1e1e1e", "#444", "•")


def readiness_html(fields: List[ReadinessField]) -> str:
    if not fields:
        return ""
    parts = [
        '<div id="readiness-checklist" style="margin:0 0 14px 0;border:1px solid #333;'
        'border-radius:6px;overflow:hidden">'
        '<div style="background:#111;padding:6px 10px;font-size:.78em;color:#778;'
        'border-bottom:1px solid #333;font-weight:600">Listing readiness</div>'
    ]
    for f in fields:
        bg, bl, icon = _STATUS_STYLE.get((f.status, f.severity), _DEFAULT_STYLE)
        val_html = (
            f'<span style="color:#667;font-size:.82em;margin-left:8px">{_html.escape(str(f.value))}</span>'
            if f.value else ""
        )
        parts.append(
            f'<a href="#{f.jump_to}" style="display:flex;align-items:center;padding:5px 10px;'
            f'background:{bg};border-left:3px solid {bl};text-decoration:none;'
            f'border-bottom:1px solid #1a1a1a">'
            f'<span style="margin-right:6px;font-size:.85em;min-width:18px">{icon}</span>'
            f'<span style="color:#ccc;font-size:.85em;flex:1">{f.label}</span>'
            f'{val_html}'
            f"</a>"
        )
    parts.append("</div>")
    return "".join(parts)
