"""
tgw.apis.fence — HTTP client for the ItemData write fence (PP-FENCE-001).

Workers use these functions instead of atomic_write_json. All writes are
routed through the tgw-http fence at http://127.0.0.1:7373.

Machine writes use the distinct Bearer token from cfg["machine_api_key"].
The caller header is retained only for attribution and loop prevention.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger(__name__)

_BASE = "http://127.0.0.1:7373"


def _headers(cfg: Dict[str, Any]) -> Dict[str, str]:
    # X-TGW-Caller identifies the fence client (worker:<queue>, cli:<op>,
    # tgw-http) so the server can distinguish machine writes from operator
    # edits. Session-42 incident: the PATCH endpoint's auto-redraft-on-
    # draft_listing-change fired on WORKER patches too, creating an infinite
    # draft→patch→redraft pipeline loop (one SKU accumulated 287 draft jobs).
    from tgw import quota
    return {"Authorization": f"Bearer {cfg['api_key']}",
            "X-TGW-Caller": f"{quota._context_kind}:{quota._context_name}"}


def _machine_headers(cfg: Dict[str, Any]) -> Dict[str, str]:
    headers = _headers(cfg)
    machine_key = cfg.get("machine_api_key")
    if not machine_key:
        raise RuntimeError("machine_api_key is required for item fence writes")
    headers["Authorization"] = f"Bearer {machine_key}"
    return headers


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
        headers=_machine_headers(cfg),
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
    allow_protected: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    POST /api/items/{sku}/ebay-write — deep-merge eBay blocks.

    Merges the supplied blocks into the item JSON, preserving protected sub-fields
    (price_comps, staged_at, photo_verify) that workers must not overwrite BY
    DEFAULT — a generic resync (e.g. ebay_sync re-saving its own full snapshot
    of ebay_offer/ebay_listing) must never clobber a fresher value it doesn't
    know about. The one or two workers that actually OWN a protected field
    (ebay_price owns price_comps, ebay_repush owns photo_verify) pass that
    field's name explicitly via allow_protected to intentionally refresh/clear
    it — everyone else stays blocked. Logs price divergence when incoming
    price differs from stored price.
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
    if allow_protected:
        body["allow_protected"] = list(allow_protected)
    resp = requests.post(
        f"{_BASE}/api/items/{sku}/ebay-write",
        json=body,
        headers=_headers(cfg),
        timeout=10,
    )
    _raise(resp)
    return resp.json()


def sold_evidence(
    cfg: Dict[str, Any],
    sku: str,
    *,
    ebay_sale: List[Dict[str, Any]],
    sold_out: bool = False,
    remaining_quantity: Optional[int] = None,
) -> Dict[str, Any]:
    """
    POST /api/items/{sku}/sold-evidence — sanctioned machine sold-marking route
    (PP-SOLD-001 / Todo #1966).

    Records the completed-sale evidence mark_item_sold produces: the full
    ``ebay_sale`` order list and, on sellout, the ``status=sold`` /
    ``ebay_listing.status=Sold`` transition plus the ``draft_listing.quantity``
    decrement.  Draft content is never touched.  Uses the distinct machine
    Bearer token — the generic PATCH fence refuses these fields
    (``workflow_evidence_write_required``).

    sold_out: item is fully sold — forces quantity 0 and the status transition.
    remaining_quantity: post-decrement draft quantity for a partial multi-qty
        sale; ignored when sold_out is set.  None (with sold_out False) records
        only the ebay_sale list — the "oversold" case.
    """
    payload: Dict[str, Any] = {
        "ebay_sale": list(ebay_sale),
        "sold_out": bool(sold_out),
    }
    if remaining_quantity is not None:
        payload["remaining_quantity"] = int(remaining_quantity)
    resp = requests.post(
        f"{_BASE}/api/items/{sku}/sold-evidence",
        json=payload,
        headers=_machine_headers(cfg),
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
