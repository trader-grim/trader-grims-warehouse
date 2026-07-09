"""
tgw.apis.lookup.base — shared dataclass and item-field helpers for product lookup.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass
class LookupResult:
    source:     str
    fetched_at: str
    title:      str = ''
    brand:      str = ''
    description: str = ''
    mpn:        str = ''   # manufacturer part number
    ean:        str = ''
    upc:        str = ''
    isbn:       str = ''
    msrp:       Optional[float] = None
    category:   str = ''
    image_url:  str = ''
    extra:      Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialisable dict for storage in item JSON under product_lookup.
        extra is stored as product_lookup.raw — full API response preserved."""
        d = asdict(self)
        raw = d.pop('extra', None) or {}
        result = {k: v for k, v in d.items() if v is not None and v != ''}
        if raw:
            result['raw'] = raw
        return result

    def prompt_context(self) -> str:
        """Compact one-line string for injection into AI prompts."""
        parts = []
        if self.brand:
            parts.append(self.brand)
        if self.title:
            parts.append(self.title)
        if self.mpn:
            parts.append(f'MPN: {self.mpn}')
        if self.category:
            parts.append(f'({self.category})')
        return ', '.join(parts) if parts else ''


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def barcode_from_item(item: Dict[str, Any]) -> tuple[str, str]:
    """
    Return (barcode_value, barcode_type) from item JSON.
    Type is 'upc', 'ean', or 'isbn'.  Returns ('', '') if none found.
    """
    _SKIP = {'', '0', 'none', 'n/a', 'does not apply', 'na'}

    for key, btype in (
        ('upc', 'upc'), ('UPC', 'upc'),
        ('ean', 'ean'), ('EAN', 'ean'),
        ('isbn', 'isbn'), ('ISBN', 'isbn'),
        ('P:UPC', 'upc'), ('P:EAN', 'ean'), ('P:ISBN', 'isbn'),
    ):
        val = str(item.get(key, '')).strip()
        if val.lower() not in _SKIP:
            return val, btype

    # Fall back to item_specifics in draft_listing
    specs = item.get('draft_listing', {}).get('item_specifics', {})
    for spec_key, btype in (('UPC', 'upc'), ('EAN', 'ean'), ('ISBN', 'isbn')):
        val = str(specs.get(spec_key, '')).strip()
        if val.lower() not in _SKIP:
            return val, btype

    return '', ''
