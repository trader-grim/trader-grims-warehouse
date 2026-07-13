"""
tgw.offers — eBay Best Offer management (PP-OFFER-001).

Phase 1: list and respond to incoming Best Offers via Trading API.

Commands (see tgw/api.py for CLI wiring):
  tgw offers list [--pending] [--sku SKU] [--auto-accept] [--live]
      List incoming Best Offers; --pending limits to Pending status only.
      --auto-accept: apply auto_accept_min_pct config rule to eligible offers.

  tgw offers respond OFFER_ID --listing-id LISTING_ID
      --accept | --counter PRICE | --decline [--live] [--by AGENT]
      Default: dry-run. --live submits to eBay and logs to offer_history.

Config keys (optional):
  auto_accept_min_pct: float  (default None = off)
      Fraction of current listing price above which to auto-accept offers.
      Never auto-declines; accept-only.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tgw.apis.ebay.trading import get_best_offers, respond_to_best_offer
from tgw.config import sku_json
from tgw.items import atomic_write_json

log = logging.getLogger(__name__)

# C11 (invariants.md): a durable registry for Best Offer responses that
# succeeded against eBay's live API but whose local SKU could not be
# resolved from the SQLite catalog. Without this, a successful eBay-side
# accept/counter/decline had no local record at all, no queryable finding,
# and no way to retry resolution later. Mirrors the
# `ebay_sku_migrate._BLOCKED_REGISTRY` pattern (plain JSON registry keyed
# by an identifier that IS known, atomic tmp-file write). Keyed by
# offer_id since that (plus listing_id) is what's known when SKU
# resolution fails -- there is no resolved item to attach a field to.
_UNRESOLVED_REGISTRY = Path('/opt/TGW/var/offers-unresolved.json')


def _record_unresolved_offer(
    offer_id: str,
    listing_id: str,
    action: str,
    counter_price: Optional[float],
    by: str,
    at: str,
) -> None:
    """Persist a Best-Offer outcome that succeeded on eBay but could not be
    resolved to a local SKU, so a future repair pass can retry resolution.

    Registry entries are keyed by offer_id and never silently dropped:
    each retry attempt (e.g. by a future repair worker) bumps `attempts`
    and `last_attempt_at` rather than overwriting history. Entries are
    only removed once resolution succeeds (see `_resolve_unresolved_offer`).
    """
    try:
        registry: Dict[str, Any] = {}
        if _UNRESOLVED_REGISTRY.exists():
            registry = json.loads(_UNRESOLVED_REGISTRY.read_text(encoding='utf-8'))

        existing = registry.get(offer_id)
        if existing is None:
            registry[offer_id] = {
                "offer_id": offer_id,
                "listing_id": listing_id,
                "action": action,
                "counter_price": counter_price,
                "by": by,
                "first_seen_at": at,
                "last_attempt_at": at,
                "attempts": 1,
                "resolved": False,
            }
        else:
            existing["last_attempt_at"] = at
            existing["attempts"] = existing.get("attempts", 1) + 1
            existing["resolved"] = False

        _UNRESOLVED_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        tmp = _UNRESOLVED_REGISTRY.with_suffix('.tmp')
        tmp.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding='utf-8')
        tmp.replace(_UNRESOLVED_REGISTRY)
    except Exception as exc:
        # Persisting the finding must never itself crash the caller; a
        # failure here still leaves the log line as a last-resort trail.
        log.error(
            "offer_history: FAILED to persist unresolved-SKU finding for offer %s "
            "(listing_id=%s): %s", offer_id, listing_id, exc,
        )


def _resolve_unresolved_offer(offer_id: str) -> None:
    """Remove a previously-recorded unresolved offer once it has been
    resolved (e.g. by a future repair pass re-running SKU resolution).
    Not called from the current handling path -- provided so a later
    repair worker has a symmetric, tested way to clear resolved entries.
    """
    try:
        if not _UNRESOLVED_REGISTRY.exists():
            return
        registry: Dict[str, Any] = json.loads(_UNRESOLVED_REGISTRY.read_text(encoding='utf-8'))
        if offer_id not in registry:
            return
        registry.pop(offer_id, None)
        tmp = _UNRESOLVED_REGISTRY.with_suffix('.tmp')
        tmp.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding='utf-8')
        tmp.replace(_UNRESOLVED_REGISTRY)
    except Exception as exc:
        log.warning("offer_history: could not clear resolved offer %s: %s", offer_id, exc)


# ---------------------------------------------------------------------------
# Item lookup helpers
# ---------------------------------------------------------------------------

def _find_item_by_listing_id(cfg: Dict[str, Any], listing_id: str) -> Optional[Path]:
    """Return item JSON path for a listing_id using the SQLite catalog."""
    db_path = Path(cfg.get("sqlite_catalog_path", ""))
    if db_path.exists():
        try:
            with sqlite3.connect(str(db_path)) as conn:
                row = conn.execute(
                    "SELECT sku FROM catalog"
                    " WHERE json_extract(data, '$.ebay_listing.listing_id') = ?",
                    (listing_id,),
                ).fetchone()
            if row:
                return sku_json(cfg, row[0])
        except Exception as exc:
            log.warning("offer_history: catalog lookup failed for listing_id=%s: %s", listing_id, exc)
    return None


def _log_offer_history(
    cfg: Dict[str, Any],
    listing_id: str,
    offer_id: str,
    action: str,
    counter_price: Optional[float],
    by: str,
    at: str,
) -> None:
    """Append a response record to offer_history in the item JSON."""
    path = _find_item_by_listing_id(cfg, listing_id)
    if path is None:
        # C11: this offer response already SUCCEEDED against eBay's live
        # API (this function is only called after that). If we can't
        # resolve which local SKU it belongs to, that must not just be
        # logged and dropped -- persist it durably so an operator/repair
        # pass can find and retry it later.
        log.warning("offer_history: item with listing_id=%s not found", listing_id)
        _record_unresolved_offer(offer_id, listing_id, action, counter_price, by, at)
        return
    try:
        item = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("offer_history: could not read %s: %s", path, exc)
        return

    entry: Dict[str, Any] = {
        "offer_id": offer_id,
        "listing_id": listing_id,
        "action": action,
        "by": by,
        "at": at,
    }
    if counter_price is not None:
        entry["counter_price"] = counter_price

    history: List[Dict[str, Any]] = item.setdefault("offer_history", [])
    history.append(entry)
    atomic_write_json(path, item, pretty=cfg.get("pretty", True))
    log.info("offer_history: logged %s for offer %s on listing %s", action, offer_id, listing_id)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_offers_list(
    cfg: Dict[str, Any],
    *,
    sku: str = "",
    pending_only: bool = False,
    auto_accept: bool = False,
    dry_run: bool = True,
    by: str = "claude",
) -> Dict[str, Any]:
    """List incoming Best Offers from eBay GetBestOffers.

    Returns {ok, offers, auto_accepted, count}.
    auto_accept applies auto_accept_min_pct rule; dry_run gates the API call.
    """
    status_filter = "Pending" if pending_only else "All"
    try:
        offers: List[Dict[str, Any]] = list(get_best_offers(cfg, status=status_filter))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    if sku:
        offers = [o for o in offers if o.get("sku") == sku]

    auto_accepted: List[str] = []
    min_pct = cfg.get("auto_accept_min_pct")
    if auto_accept and min_pct is not None:
        for offer in offers:
            if offer.get("status") != "Pending":
                continue
            offer_price = offer.get("offer_price")
            listing_price = offer.get("listing_price")
            if offer_price is None or not listing_price:
                continue
            if offer_price >= min_pct * listing_price:
                res = cmd_offers_respond(
                    cfg,
                    offer_id=offer["offer_id"],
                    listing_id=offer["listing_id"],
                    action="Accept",
                    dry_run=dry_run,
                    by=by,
                )
                if res.get("ok"):
                    auto_accepted.append(offer["offer_id"])

    return {
        "ok": True,
        "offers": offers,
        "auto_accepted": auto_accepted,
        "count": len(offers),
    }


def cmd_offers_respond(
    cfg: Dict[str, Any],
    offer_id: str,
    listing_id: str,
    action: str,
    counter_price: Optional[float] = None,
    *,
    dry_run: bool = True,
    by: str = "claude",
) -> Dict[str, Any]:
    """Respond to a Best Offer via RespondToBestOffer.

    action: 'Accept' | 'Decline' | 'Counter'
    counter_price: required when action='Counter'.
    dry_run=True (default): show what would be sent; no eBay API call.
    On success (live), response is appended to offer_history in item JSON.
    """
    if action not in ("Accept", "Decline", "Counter"):
        return {
            "ok": False,
            "error": f"invalid action {action!r}: must be Accept, Decline, or Counter",
        }
    if action == "Counter" and counter_price is None:
        return {"ok": False, "error": "action=Counter requires counter_price"}

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "offer_id": offer_id,
            "listing_id": listing_id,
            "action": action,
            "counter_price": counter_price,
            "by": by,
            "at": now_iso,
            "note": "dry-run: no eBay API call made; add --live to submit",
        }

    try:
        respond_to_best_offer(
            cfg,
            offer_id=offer_id,
            listing_id=listing_id,
            action=action,
            counter_price=counter_price,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc), "offer_id": offer_id}

    _log_offer_history(cfg, listing_id, offer_id, action, counter_price, by, now_iso)

    return {
        "ok": True,
        "dry_run": False,
        "offer_id": offer_id,
        "listing_id": listing_id,
        "action": action,
        "counter_price": counter_price,
        "by": by,
        "at": now_iso,
    }
