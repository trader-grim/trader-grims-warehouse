"""PP-WORKFLOW-001 — condition-derived photo-state convergence (Todo #1967).

The published eBay listing's ``product.imageUrls`` is the authoritative photo
set for an item whose draft has been pinned to the live baseline
(:func:`tgw.draft_sync.pin_draft_to_live`) or whose listing has already sold.
A local re-upload (``ebay_upload`` → ``ebay_photos``) mints a *different* EPS
URL family for the same source files, so once the draft carries the live URLs
the exact ``ebay_photos``/``draft_listing.imageUrls`` equality check in
``item_snapshot._photo_sync_state`` can never converge — ``resync_photos`` then
re-dispatches ``ebay_upload`` on every call, each pass rewriting a divergent
``ebay_photos``/``ebay_submitted`` that never reaches the (already sold,
unpublishable) listing.

Root cause of the Aug 31 divergence on ``tgw201809090907247``:

* the listing had sold (``ebay_listing.status == "Sold"``, offer
  ``UNPUBLISHED``, ``availableQuantity == 0``) but the local ``status`` and
  ``draft_listing.quantity`` were never reconciled, so
  :func:`tgw.workflow.item_snapshot.inventory_available` still read ``True``;
* ``resync_photos`` therefore authorised a ``tgw.ebay_staged`` goal with
  ``("upload", "stage")`` scope and dispatched ``ebay_upload``;
* ``ebay_upload`` re-uploaded the seven 2022 originals to a fresh EPS family
  (``IdkAAeSwOiBqR2tI`` …) into ``ebay_photos`` and the follow-on stage wrote
  ``ebay_submitted.inventory_item.product.imageUrls`` from that set, while
  ``draft_listing.imageUrls`` / ``ebay_offer.photo_urls`` / ``ebay_live``
  still (correctly) held the live family (``LqUAAOSwwNNnu5PL`` …).

:func:`reconcile_photo_state_to_live` computes the exact patch that brings
``ebay_photos[].url``, ``ebay_offer.photo_urls`` and
``ebay_submitted.inventory_item.product.imageUrls`` back into agreement with
the live listing's ``imageUrls``, position for position, WITHOUT reading or
rewriting any listing-content field (title, description, price, aspects,
condition) — safe to apply to a sold item.  It is pure/no-I/O so it can be
used both by the ``resync_photos`` action and by an offline batch pass over
affected SKUs.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

from .item_snapshot import inventory_available

__all__ = [
    "live_image_urls",
    "listing_photo_terminal",
    "reconcile_photo_state_to_live",
]

# eBay-side completed-sale states — the listing content must not be touched.
_TERMINAL_LISTING_STATES = {"sold", "ended", "completed"}


def live_image_urls(doc: Mapping[str, Any]) -> List[str]:
    """The live listing mirror's ordered ``product.imageUrls`` (``[]`` if none)."""
    live = doc.get("ebay_live")
    if not isinstance(live, Mapping):
        return []
    inv = live.get("inventory_item")
    product = inv.get("product") if isinstance(inv, Mapping) else None
    urls = product.get("imageUrls") if isinstance(product, Mapping) else None
    if not isinstance(urls, list):
        return []
    return [u for u in urls if isinstance(u, str) and u.strip()]


def listing_photo_terminal(doc: Mapping[str, Any]) -> bool:
    """The item is out of the listing pipeline (sold/ended or not available).

    ``resync_photos`` must never dispatch an upload/stage effect for such an
    item; the only legal move is to reconcile the local photo projections to
    whatever the live listing already shows.
    """
    listing = doc.get("ebay_listing")
    if isinstance(listing, Mapping):
        status = str(listing.get("status") or "").strip().lower()
        if status in _TERMINAL_LISTING_STATES:
            return True
    return not inventory_available(doc)


def reconcile_photo_state_to_live(doc: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the fence-PATCH fields that pin the local photo projections to
    the live listing's ``imageUrls`` — position for position.

    Only the fields that actually diverge are returned; ``{}`` means either
    there is no live photo set to pin from or the local photo count does not
    match it one-to-one (that gap needs a real ``ebay_upload`` and is out of
    scope here).  No listing-content field is read or written.
    """
    live_urls = live_image_urls(doc)
    if not live_urls:
        return {}

    entries = doc.get("ebay_photos")
    entries = entries if isinstance(entries, list) else []
    mapped = [
        entry
        for entry in entries
        if isinstance(entry, Mapping)
        and isinstance(entry.get("local"), str)
        and entry["local"].strip()
    ]
    # A 1:1 realignment is only honest when every current local photo lines up
    # with exactly one live URL and there are no stray hosted rows.
    if not mapped or len(mapped) != len(live_urls) or len(mapped) != len(entries):
        return {}

    patch: Dict[str, Any] = {}

    new_entries: List[Dict[str, Any]] = []
    photos_changed = False
    for entry, url in zip(mapped, live_urls):
        new_entry = dict(entry)
        if new_entry.get("url") != url:
            photos_changed = True
        new_entry["url"] = url
        new_entries.append(new_entry)
    if photos_changed:
        patch["ebay_photos"] = new_entries

    offer = doc.get("ebay_offer")
    if isinstance(offer, Mapping) and list(offer.get("photo_urls") or []) != live_urls:
        patch["ebay_offer"] = {**offer, "photo_urls": list(live_urls)}

    submitted = doc.get("ebay_submitted")
    if isinstance(submitted, Mapping):
        inv = submitted.get("inventory_item")
        product = inv.get("product") if isinstance(inv, Mapping) else None
        if (
            isinstance(product, Mapping)
            and list(product.get("imageUrls") or []) != live_urls
        ):
            new_product = {**product, "imageUrls": list(live_urls)}
            new_inv = {**inv, "product": new_product}
            patch["ebay_submitted"] = {**submitted, "inventory_item": new_inv}

    return patch
