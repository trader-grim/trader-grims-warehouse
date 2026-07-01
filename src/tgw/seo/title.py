"""
tgw.seo.title — eBay title enhancement using product lookup data.

Applies rule-based brand/MPN injection and flags quality issues.
Called by ebay_draft after the draft title is established.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_MAX_TITLE = 80
_MIN_TITLE = 40

# Words that burn high-value title real estate when they lead the title.
# Buyers search "antique mirror" not "antique something" — the noun should lead.
# These are moved back, not removed; they still add search signal later in the title.
_LEADING_FILLER = {
    'vintage', 'antique', 'collectible', 'collectable', 'rare',
    'beautiful', 'stunning', 'gorgeous', 'lovely',
}

# 4-digit year, 1600-2099
_YEAR_RE = re.compile(r'^(1[6-9]\d\d|20\d\d)$')


def _demote_leading_filler(title: str) -> tuple[str, list[str]]:
    """Move leading filler (and year+filler clusters) away from positions 1-4.

    Two cases:
      A. Pure filler lead — "Vintage Ceramic Bowl" → "Ceramic Bowl Vintage"
         Moves up to 3 consecutive filler words to the end.

      B. Year + filler lead — "1983 Vintage Christmas Enamel Spoon" →
         "Christmas Enamel Spoon 1983 Vintage"
         Moves the year + any immediately-following filler words to positions 4-5,
         letting content words lead. A year with NO filler after it is left alone.

    Returns (reordered_title, list_of_moved_tokens).
    """
    words = title.split()
    if not words:
        return title, []

    def _is_filler(w: str) -> bool:
        return w.lower().rstrip('.,;:') in _LEADING_FILLER

    # Case A: one or more leading filler words (no year first)
    moved: list[str] = []
    while words and len(moved) < 3 and _is_filler(words[0]):
        moved.append(words.pop(0))
    if moved:
        if not words:
            return title, []
        return ' '.join(words + moved), moved

    # Case B: year immediately followed by at least one filler word
    if _YEAR_RE.match(words[0]) and len(words) > 1 and _is_filler(words[1]):
        cluster = [words.pop(0)]  # the year
        while words and len(cluster) < 4 and _is_filler(words[0]):
            cluster.append(words.pop(0))
        # Insert cluster after the first 3 content words
        front, tail = words[:3], words[3:]
        if not front:
            return title, []
        return ' '.join(front + cluster + tail), cluster

    return title, []


def enhance_title(
    title: str,
    product_lookup: Optional[Dict[str, Any]] = None,
    item_specifics: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Enhance a draft title using product lookup data.

    Returns dict:
      title     — enhanced title (may equal original if no changes)
      title_ai  — original AI title (only present if title was changed)
      flags     — list of SEO flag strings (title_too_short, title_too_long,
                  all_caps:<words>, no_brand, no_model)
    """
    pl    = product_lookup or {}
    specs = item_specifics or {}

    enhanced = title.strip()
    original = enhanced
    flags: List[str] = []

    # Demote generic lead words — e.g. "Vintage Ceramic Bowl" → "Ceramic Bowl Vintage"
    enhanced, _moved = _demote_leading_filler(enhanced)
    if _moved:
        flags.append('leading_filler:' + ','.join(_moved))

    brand = (pl.get('brand') or specs.get('Brand') or '').strip()
    mpn   = (pl.get('mpn')   or specs.get('MPN')   or specs.get('Model') or '').strip()

    # Inject brand if absent
    if brand and brand.lower() not in enhanced.lower():
        candidate = f'{brand} {enhanced}'
        if len(candidate) <= _MAX_TITLE:
            enhanced = candidate
        else:
            available = _MAX_TITLE - len(brand) - 1
            if available > 15:
                enhanced = f'{brand} {enhanced[:available].rstrip()}'

    # Append MPN/model if absent and space allows
    if mpn and mpn.lower() not in enhanced.lower():
        candidate = f'{enhanced} {mpn}'
        if len(candidate) <= _MAX_TITLE:
            enhanced = candidate

    # Length flags
    if len(enhanced) < _MIN_TITLE:
        flags.append('title_too_short')
    if len(enhanced) > _MAX_TITLE:
        flags.append('title_too_long')

    # ALL CAPS words (eBay penalises these; exclude model numbers with digits/hyphens)
    caps_words = [
        w for w in enhanced.split()
        if len(w) > 2 and w.isupper() and w.isalpha()
    ]
    if caps_words:
        flags.append('all_caps:' + ','.join(caps_words[:3]))

    # Missing brand / model signals (informational, not blocking)
    if not brand:
        # Suppress false-positive: if the raw Brand spec has a value in the title,
        # it's already reflected — only flag when Brand field is truly unfilled
        raw_spec_brand = (specs.get('Brand') or specs.get('brand') or '').strip()
        if not (raw_spec_brand and raw_spec_brand.lower() in enhanced.lower()):
            flags.append('no_brand')
    if not mpn:
        flags.append('no_model')

    result: Dict[str, Any] = {'title': enhanced, 'flags': flags}
    if enhanced != original:
        result['title_ai'] = original
    return result
