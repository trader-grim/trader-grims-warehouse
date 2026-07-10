"""
tgw.apis.ebay.catalog — eBay Commerce Catalog API: EPID lookup by barcode.

Scope required: commerce.catalog.readonly
Returns None gracefully if the scope is not yet granted (401/403).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests

from tgw import quota
from tgw.apis.ebay.client import ebay_get

log = logging.getLogger(__name__)

_SEARCH_PATH = '/commerce/catalog/v1_beta/product_summary/search'


def lookup_epid(cfg: Dict[str, Any], barcode: str) -> Optional[str]:
    """
    Search the eBay Catalog by UPC/EAN barcode and return the EPID if found.

    EPID association at staging time causes eBay to auto-fill verified item
    specifics from its own product record — the biggest single SEO lever for
    items with a scannable barcode.

    Returns the EPID string, or None if not found or scope not granted.
    Scope: commerce.catalog.readonly
    """
    try:
        data = ebay_get(cfg, _SEARCH_PATH, params={
            'q':           barcode,
            'fieldgroups': 'PRODUCT',
            'limit':       1,
        })
        summaries = data.get('productSummaries', [])
        if summaries:
            epid = str(summaries[0].get('epid', '')).strip()
            if epid:
                log.info('catalog: %s → EPID %s', barcode, epid)
                return epid
        log.debug('catalog: no EPID found for barcode %s', barcode)
        return None
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status in (400, 401, 403):
            # eBay returns 400 (not 401/403) when the app has never been
            # granted commerce.catalog.readonly at all, as opposed to an
            # expired/invalid token for a scope it does have — found live
            # 2026-07-04 (PP-PHOTOSYNC-001 P10): a legacy-listing repair job
            # retried forever on this exact call for any item with a barcode,
            # since the module's own documented "scope not granted" handling
            # only covered 401/403.
            log.debug('catalog: commerce.catalog.readonly not granted — EPID skipped')
            return None
        if status == 404:
            return None
        raise
    except quota.QuotaBudgetExceeded:
        # audit#1143 #1173: the bare except below used to swallow this too,
        # defeating the quota-halt/requeue pattern (worker_base.py's
        # 'quota budget exhausted' transient-requeue classification) — the
        # item would silently ship without EPID enrichment during quota
        # exhaustion instead of the job requeuing until the pool resets.
        raise
    except RuntimeError:
        # code-review follow-up to #1173: client.py's load_token() raises a
        # plain RuntimeError ('eBay access token is expired...') proactively,
        # before any request goes out — same swallow bug as QuotaBudgetExceeded
        # above, just a different exception type. worker_base.py has its own
        # dedicated 'token is expired' transient-requeue pattern (900s delay,
        # waits for token_refresh) that this must reach instead of silently
        # skipping EPID enrichment.
        raise
    except Exception as exc:
        log.warning('catalog: EPID lookup failed for %s: %s', barcode, exc)
        return None
