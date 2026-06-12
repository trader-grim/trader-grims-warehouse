"""
tgw.ebay.market_data — PP-REPRICER-001 read-only market-data provider layer.

The *read-only* foundation for the eventual automatic repricer.  It abstracts
"where do comparable prices come from" behind a small provider interface so the
repricer (and ``tgw reprice-suggest``) can blend independent signals:

  * OwnSalesProvider    — our own sold history, aggregated per eBay category by
                          the velocity_stats worker (velocity-stats.json).
  * BrowseCompsProvider — active eBay listings via the Browse API (read-only
                          GET; reuses ebay.pricing.suggest_price).
  * StubProvider        — placeholder for the sold-price provider that needs the
                          ``buy.marketplace_insights`` scope (NOT granted — see
                          CLAUDE.md / PP-REPRICER-001).  Always reports no data.

NOTHING here writes to eBay.  ``reprice_suggest`` only *reports* a suggested
price and a recommendation; every result carries ``applied: False``.  Applying
suggestions is deliberately out of scope until the sold-data provider unblocks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Protocol

from ..velocity import _category, load_velocity_stats
from .pricing import _apply_floor, suggest_price, to_99

# Minimum sample count before a provider is considered to have usable data.
MIN_SAMPLES = 3


@dataclass
class Comps:
    """One provider's view of comparable prices for an item."""
    source: str
    available: bool = False
    n: int = 0
    p25: Optional[float] = None
    median: Optional[float] = None
    p75: Optional[float] = None
    note: str = ''

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MarketDataProvider(Protocol):
    name: str

    def comps(self, item: Dict[str, Any]) -> Comps: ...


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

class OwnSalesProvider:
    """Comps from our own per-category sold history (velocity-stats.json)."""
    name = 'own_sales'

    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self._stats: Optional[Dict[str, Any]] = None

    def _load(self) -> Dict[str, Any]:
        if self._stats is None:
            self._stats = load_velocity_stats(self.cfg['catalog_root']) or {}
        return self._stats

    def comps(self, item: Dict[str, Any]) -> Comps:
        cat_id, _ = _category(item)
        if not cat_id:
            return Comps(self.name, note='no category on item')
        c = self._load().get('categories', {}).get(str(cat_id))
        if not c:
            return Comps(self.name, note=f'no velocity data for category {cat_id}')
        n = int(c.get('sold_count', 0) or 0)
        median = c.get('median_sale_price')
        p25 = c.get('p25_sale_price')
        has = (median is not None or p25 is not None)
        return Comps(self.name, available=(n >= MIN_SAMPLES and has),
                     n=n, p25=p25, median=median, p75=None,
                     note=f'category {cat_id}')


class BrowseCompsProvider:
    """Comps from active eBay listings via the Browse API (read-only)."""
    name = 'browse'

    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg

    def comps(self, item: Dict[str, Any]) -> Comps:
        dl = item.get('draft_listing') or {}
        title = str(item.get('title') or dl.get('title') or '').strip()
        if not title:
            return Comps(self.name, note='no title to search')
        cat_id, cat_name = _category(item)
        condition = str(item.get('condition') or dl.get('condition') or '')
        res = suggest_price(self.cfg, title, category_name=cat_name,
                            category_id=str(cat_id), item_condition=condition,
                            product_lookup=item.get('product_lookup'))
        comps = res.get('comps') or {}
        n = int(comps.get('count', 0) or 0)
        return Comps(self.name, available=(n >= MIN_SAMPLES), n=n,
                     p25=comps.get('p25'), median=comps.get('median'),
                     p75=comps.get('p75'), note=str(res.get('source', '')))


class StubProvider:
    """Placeholder for the sold-price provider (buy.marketplace_insights)."""
    name = 'sold_marketplace_insights'

    def __init__(self, cfg: Optional[Dict[str, Any]] = None):
        self.cfg = cfg

    def comps(self, item: Dict[str, Any]) -> Comps:
        return Comps(self.name, available=False,
                     note='requires buy.marketplace_insights scope (not granted)')


def default_providers(cfg: Dict[str, Any]) -> List[MarketDataProvider]:
    """The standard provider stack, in blend-priority order."""
    return [OwnSalesProvider(cfg), BrowseCompsProvider(cfg), StubProvider(cfg)]


# ---------------------------------------------------------------------------
# Suggestion engine (read-only)
# ---------------------------------------------------------------------------

def current_price(item: Dict[str, Any]) -> Optional[float]:
    """Best-effort current listed price for an item, or None."""
    for block, key in (('ebay_offer', 'price'), ('draft_listing', 'price'),
                       ('ebay_listing', 'price')):
        try:
            v = float((item.get(block) or {}).get(key))
        except (TypeError, ValueError):
            continue
        if v > 0:
            return round(v, 2)
    return None


def reprice_suggest(cfg: Dict[str, Any], item: Dict[str, Any],
                    providers: Optional[List[MarketDataProvider]] = None) -> Dict[str, Any]:
    """
    Produce a read-only price suggestion for one item by blending providers.

    Basis preference: own-sales (what actually sold) before browse (what's
    listed now).  The suggested price uses the p25 basis — matching the
    pipeline's launch-pricing convention — floored and rounded to .99.
    Never writes to eBay; the result always carries ``applied: False``.
    """
    providers = providers if providers is not None else default_providers(cfg)
    results = [p.comps(item) for p in providers]
    cat_id, _ = _category(item)
    current = current_price(item)

    basis: Optional[Comps] = next(
        (r for r in results if r.available and r.source == 'own_sales'), None)
    if basis is None:
        basis = next((r for r in results if r.available), None)

    suggested: Optional[float] = None
    rationale = 'no market data available from any provider'
    if basis is not None:
        raw = basis.p25 if basis.p25 is not None else basis.median
        if raw is not None:
            floored, was_floored = _apply_floor(float(raw), cfg, str(cat_id))
            suggested = to_99(floored)
            rationale = (f'{basis.source}: p25={basis.p25} median={basis.median} '
                         f'n={basis.n}' + (' (floored)' if was_floored else ''))

    recommendation = 'unknown'
    delta: Optional[float] = None
    delta_pct: Optional[float] = None
    if suggested is not None and current is not None:
        delta = round(suggested - current, 2)
        delta_pct = round((delta / current) * 100, 1) if current else None
        if suggested < current * 0.95:
            recommendation = 'reduce'
        elif suggested > current * 1.10:
            recommendation = 'raise'
        else:
            recommendation = 'hold'
    elif suggested is not None and current is None:
        recommendation = 'set'

    return {
        'ok': True,
        'sku': item.get('sku'),
        'category_id': str(cat_id),
        'current_price': current,
        'suggested_price': suggested,
        'delta': delta,
        'delta_pct': delta_pct,
        'recommendation': recommendation,
        'basis': basis.source if basis else None,
        'rationale': rationale,
        'providers': [r.to_dict() for r in results],
        'applied': False,  # read-only — this layer never writes to eBay
    }
