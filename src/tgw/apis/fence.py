"""
tgw.apis.fence — HTTP client for the ItemData write fence (PP-FENCE-001).

Workers use these functions instead of atomic_write_json. All writes are
routed through the tgw-http fence at http://127.0.0.1:7373.

Auth: Bearer token from cfg["api_key"] — same key used by Flutter / MC.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests

log = logging.getLogger(__name__)

_BASE = "http://127.0.0.1:7373"


def _headers(cfg: Dict[str, Any]) -> Dict[str, str]:
    return {"Authorization": f"Bearer {cfg['api_key']}"}


def _raise(resp: requests.Response) -> None:
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        try:
            detail = resp.json().get("detail", resp.text[:200])
        except Exception:
            detail = resp.text[:200]
        raise requests.HTTPError(f"{e} — {detail}", response=resp) from e


def get_item(cfg: Dict[str, Any], sku: str) -> Dict[str, Any]:
    """GET /api/items/{sku} — returns full item dict."""
    resp = requests.get(f"{_BASE}/api/items/{sku}", headers=_headers(cfg), timeout=10)
    _raise(resp)
    return resp.json()["item"]


def patch_item(cfg: Dict[str, Any], sku: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    """PATCH /api/items/{sku} — update top-level fields."""
    resp = requests.patch(
        f"{_BASE}/api/items/{sku}",
        json={"fields": fields},
        headers=_headers(cfg),
        timeout=10,
    )
    _raise(resp)
    return resp.json()


def append_item(
    cfg: Dict[str, Any],
    sku: str,
    op: str,
    data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    POST /api/items/{sku}/append — typed list append.

    op must be one of:
      "vision_result"     → item["vision_results"]
      "photo"             → item["photos"]
      "price_event"       → item["price_history"]
      "history_event"     → item["{data['type']}_history"]  (title/description/location)
    """
    resp = requests.post(
        f"{_BASE}/api/items/{sku}/append",
        json={"op": op, "data": data},
        headers=_headers(cfg),
        timeout=10,
    )
    _raise(resp)
    return resp.json()


def ebay_write(
    cfg: Dict[str, Any],
    sku: str,
    *,
    ebay_offer: Optional[Dict[str, Any]] = None,
    ebay_listing: Optional[Dict[str, Any]] = None,
    ebay_submitted: Optional[Dict[str, Any]] = None,
    ebay_live: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    POST /api/items/{sku}/ebay-write — deep-merge eBay blocks.

    Merges the supplied blocks into the item JSON, preserving protected sub-fields
    (price_comps, staged_at, photo_verify) that workers must not overwrite.
    Logs price divergence when incoming price differs from stored price.
    """
    body: Dict[str, Any] = {}
    if ebay_offer is not None:
        body["ebay_offer"] = ebay_offer
    if ebay_listing is not None:
        body["ebay_listing"] = ebay_listing
    if ebay_submitted is not None:
        body["ebay_submitted"] = ebay_submitted
    if ebay_live is not None:
        body["ebay_live"] = ebay_live
    resp = requests.post(
        f"{_BASE}/api/items/{sku}/ebay-write",
        json=body,
        headers=_headers(cfg),
        timeout=10,
    )
    _raise(resp)
    return resp.json()


def create_item(cfg: Dict[str, Any], sku: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    POST /api/items — create new item through the fence.

    Raises requests.HTTPError with 409 if sku already exists.
    """
    resp = requests.post(
        f"{_BASE}/api/items",
        json={"sku": sku, "data": data},
        headers=_headers(cfg),
        timeout=10,
    )
    _raise(resp)
    return resp.json()
