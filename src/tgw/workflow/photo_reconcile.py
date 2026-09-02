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
the live listing's ``imageUrls`` — each local file's ``ebay_photos`` row gets
the live URL that sits at that file's position in ``ordered_photos()`` /
``photo_order`` order, exactly the local→URL pairing
``item_snapshot._photo_sync_state`` rebuilds and gates on — WITHOUT reading or
rewriting any listing-content field (title, description, price, aspects,
condition), so it is safe to apply to a sold item.

The acceptance equality for this task also requires
``draft_listing.imageUrls == live``.  This module never rewrites listing
content (the draft's image list is pinned by
:func:`tgw.draft_sync.pin_draft_to_live`, not here), so a draft that has itself
diverged from live is refused rather than papered over with a false
``reconciled`` result — :func:`reconcile_photo_state_to_live` returns ``{}`` and
the caller holds for operator attention.

It reads the SKU photo directory (via ``ordered_photos``) but writes nothing, so
it can back both the ``resync_photos`` action and an offline batch pass over
affected SKUs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from .item_snapshot import inventory_available

__all__ = [
    "live_image_urls",
    "listing_photo_terminal",
    "reconcile_photo_state_to_live",
]

# eBay-side completed-sale state — the listing content must not be touched.
# Only ``Sold`` is ever written to ``ebay_listing.status`` in this tree
# (``mark_item_sold`` / ``_apply_sold_evidence``); ``ended`` / ``completed`` are
# deliberately NOT terminal here because an ended-but-unsold listing is
# relist-eligible (see http_server "Ended listings qualify - they can be
# relisted") and must stay in the listing pipeline.
_TERMINAL_LISTING_STATES = {"sold"}


def _resolve_local_key(local: str, sku_dir: Path) -> str:
    """Absolute string key for a photo ``local`` path — matches ``_photo_sync_state``."""
    local_path = Path(local)
    return str(local_path if local_path.is_absolute() else sku_dir / local_path)


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
    """The item is out of the listing pipeline (sold, or not inventory-available).

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


def reconcile_photo_state_to_live(
    doc: Mapping[str, Any], sku_dir: "Path | str",
) -> Dict[str, Any]:
    """Return the fence-PATCH fields that pin the local photo projections to
    the live listing's ``imageUrls``.

    Each ``ebay_photos`` row is realigned to the live URL at its local file's
    position in ``ordered_photos()`` / ``photo_order`` order — the same
    local→URL pairing ``item_snapshot._photo_sync_state`` rebuilds and requires
    for ``exact`` — while ``ebay_offer.photo_urls`` and
    ``ebay_submitted...product.imageUrls`` are set to the live list verbatim.

    Only the fields that actually diverge are returned.  ``{}`` means the
    reconcile is refused and the caller must hold for operator attention:

    * no live ``imageUrls`` to pin from;
    * ``draft_listing.imageUrls`` is itself not equal to live (this module
      never rewrites listing content, so the draft must already be pinned);
    * the local photo files, the hosted ``ebay_photos`` rows and the live URLs
      do not line up one-to-one (that gap needs a real ``ebay_upload``).

    No listing-content field is read or written.
    """
    sku_dir = Path(sku_dir)

    live_urls = live_image_urls(doc)
    if not live_urls:
        return {}

    # The acceptance equality includes draft_listing.imageUrls == live, but
    # repairing the draft is pin_draft_to_live's job, not ours — a diverged
    # draft is an operator problem, not something to hide behind a "reconciled"
    # result.
    draft = doc.get("draft_listing")
    draft = draft if isinstance(draft, Mapping) else {}
    draft_urls = [
        u for u in (draft.get("imageUrls") or [])
        if isinstance(u, str) and u.strip()
    ]
    if draft_urls != live_urls:
        return {}

    entries = doc.get("ebay_photos")
    entries = entries if isinstance(entries, list) else []
    if not entries or len(entries) != len(live_urls):
        return {}

    from tgw.assets import ordered_photos

    expected_keys = [str(path) for path in ordered_photos(doc, sku_dir)]
    if len(expected_keys) != len(live_urls):
        return {}
    # live URL that belongs to each local file, by that file's display position.
    target_by_key = {key: live_urls[i] for i, key in enumerate(expected_keys)}

    resolved: List[Tuple[str, Mapping[str, Any]]] = []
    seen_keys: set = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            return {}
        local = entry.get("local")
        if not isinstance(local, str) or not local.strip():
            return {}
        key = _resolve_local_key(local, sku_dir)
        if key in seen_keys or key not in target_by_key:
            # duplicate row or a hosted row with no matching local file -> the
            # set is not a clean 1:1, refuse.
            return {}
        seen_keys.add(key)
        resolved.append((key, entry))

    patch: Dict[str, Any] = {}

    new_entries: List[Dict[str, Any]] = []
    photos_changed = False
    for key, entry in resolved:
        url = target_by_key[key]
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
