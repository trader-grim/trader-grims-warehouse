"""
tgw.listing_quality — Listing quality scorer for eBay drafts.

score_draft(item, photo_count=None) -> QualityResult
  Scores 0–100 based on title strength, brand presence, specifics
  completeness, photo count, description length, and price comp quality.
  Stored in draft_listing.quality at ebay_draft time; re-scored after
  ebay_price writes price_comps.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from tgw.ebay.draft_specifics import get_ebay_aspects

_GENERIC_BRANDS = frozenset({
    'unbranded', 'does not apply', 'n/a', 'na', 'generic', '',
    'does not apply', 'not applicable',
})

# Model/MPN values that explicitly acknowledge there is no model — suppress no_model flag
_ACKNOWLEDGED_NO_MODEL = frozenset({
    'does not apply', 'unknown', 'n/a', 'na', 'none', 'other', 'not applicable',
})

_SCORE_MAX = 100


@dataclass
class QualityResult:
    score: int          # 0–100
    flags: List[str]    # machine-readable warning tags, sorted by severity
    # per-signal breakdown
    title_pts: int
    brand_pts: int
    model_pts: int
    specifics_pts: int
    photo_pts: int
    desc_pts: int
    comp_pts: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            'score': self.score,
            'flags': self.flags,
            'breakdown': {
                'title':       self.title_pts,
                'brand':       self.brand_pts,
                'model':       self.model_pts,
                'specifics':   self.specifics_pts,
                'photos':      self.photo_pts,
                'description': self.desc_pts,
                'comps':       self.comp_pts,
            },
        }


def score_draft(
    item: Dict[str, Any],
    photo_count: Optional[int] = None,
) -> QualityResult:
    """
    Score a draft listing 0–100.

    Weights:
      title length          10 pts
      brand in title        25 pts  (largest single eBay search signal)
      model/MPN in title    10 pts
      specifics fill rate   20 pts  (required: 15, recommended: 5)
      photo count           20 pts
      description length     5 pts
      price comp count      10 pts
    Total                  100 pts

    photo_count: raw image files in SKU dir (caller provides);
    falls back to len(ebay_photos) from item JSON if omitted.
    """
    flags: List[str] = []
    draft = item.get('draft_listing') or {}
    # Prefer draft title (may be SEO-enhanced) over the raw AI title
    title = str(draft.get('title') or item.get('title', '')).strip()
    pl    = item.get('product_lookup') or {}

    # ── Title length (10 pts) ──────────────────────────────────────────────
    tlen = len(title)
    if tlen == 0:
        title_pts = 0
        flags.append('no_title')
    elif tlen < 25:
        title_pts = 0
        flags.append('title_too_short')
    elif tlen < 40:
        title_pts = 5
    elif tlen <= 80:
        title_pts = 10
    else:
        title_pts = 5
        flags.append('title_too_long')

    # ── Brand in title (25 pts) ────────────────────────────────────────────
    # todo #1418: Set B read via tgw.ebay.draft_specifics (the sanctioned accessor)
    specs      = get_ebay_aspects(item)
    spec_brand = str(specs.get('Brand') or specs.get('brand') or '').strip()
    pl_brand   = str(pl.get('brand') or '').strip()

    # Prefer product_lookup brand (ground truth) over AI-filled aspect
    best_brand = pl_brand if pl_brand else spec_brand
    title_lower = title.lower()

    if best_brand and best_brand.lower() not in _GENERIC_BRANDS:
        if best_brand.lower() in title_lower:
            brand_pts = 25
        else:
            brand_pts = 12   # brand known but not injected into title yet
    elif spec_brand and spec_brand.lower() not in _GENERIC_BRANDS:
        # AI placed a brand that might not be in title
        brand_pts = 10
    else:
        brand_pts = 0
        # Suppress false-positive when spec_brand value (even a generic like "Unbranded")
        # already appears in the title — the brand is acknowledged and present
        if not (spec_brand and spec_brand.lower() in title_lower):
            flags.append('no_brand')

    # ── Model/identifier in title (10 pts) ────────────────────────────────
    mpn        = str(specs.get('MPN') or specs.get('mpn') or pl.get('mpn') or '').strip()
    model_val  = str(specs.get('Model') or specs.get('model') or '').strip()
    identifier = mpn or model_val

    if identifier and identifier.lower() not in _GENERIC_BRANDS and len(identifier) > 2:
        model_pts = 10 if identifier.lower() in title_lower else 5
    else:
        model_pts = 0
        # Flag no_model when identifier is absent; suppress if explicitly acknowledged
        # with "Does Not Apply", "Unknown", etc. (set Model aspect to clear this flag)
        if not identifier or identifier.lower() not in _ACKNOWLEDGED_NO_MODEL:
            flags.append('no_model')

    # ── Specifics completeness (20 pts: required 15, recommended 5) ───────
    req_total  = int(draft.get('aspects_required_total', 0))
    req_filled = int(draft.get('aspects_required_filled', 0))
    rec_total  = int(draft.get('aspects_recommended_total', 0))
    rec_filled = int(draft.get('aspects_recommended_filled', 0))

    if req_total > 0:
        req_pts = round((req_filled / req_total) * 15)
    else:
        req_pts = 15   # no required aspects = full credit (e.g. fallback category)

    if rec_total > 0:
        rec_pts = round((rec_filled / rec_total) * 5)
    else:
        rec_pts = 5    # no recommended aspects = full credit

    specifics_pts = req_pts + rec_pts

    # ── Photo count (20 pts) ──────────────────────────────────────────────
    if photo_count is None:
        photo_count = len(item.get('ebay_photos') or [])

    if photo_count == 0:
        photo_pts = 0
        flags.append('no_photos')
    elif photo_count == 1:
        photo_pts = 6
        flags.append('few_photos')
    elif photo_count == 2:
        photo_pts = 12
        flags.append('few_photos')
    else:
        photo_pts = 20

    # ── Description word count (5 pts) ────────────────────────────────────
    desc  = str(item.get('description') or draft.get('description') or '').strip()
    words = len(desc.split()) if desc else 0
    if words >= 150:
        desc_pts = 5
    elif words >= 75:
        desc_pts = 3
    elif words >= 25:
        desc_pts = 1
    else:
        desc_pts = 0
        flags.append('short_description')

    # ── Price comp count (10 pts) ─────────────────────────────────────────
    comps      = (item.get('ebay_offer') or {}).get('price_comps') or {}
    comp_count = int(comps.get('count', 0))
    if comp_count >= 5:
        comp_pts = 10
    elif comp_count >= 3:
        comp_pts = 6
        flags.append('thin_comps')
    elif comp_count >= 1:
        comp_pts = 3
        flags.append('thin_comps')
    else:
        comp_pts = 0
        flags.append('no_price_comps')

    total = title_pts + brand_pts + model_pts + specifics_pts + photo_pts + desc_pts + comp_pts
    total = max(0, min(_SCORE_MAX, total))

    return QualityResult(
        score=total,
        flags=flags,
        title_pts=title_pts,
        brand_pts=brand_pts,
        model_pts=model_pts,
        specifics_pts=specifics_pts,
        photo_pts=photo_pts,
        desc_pts=desc_pts,
        comp_pts=comp_pts,
    )
