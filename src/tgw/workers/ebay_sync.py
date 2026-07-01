"""
tgw.workers.ebay_sync — sync eBay listing status back to ItemData.

Self-scheduling: on startup enqueues a sync job if the queue is idle, then
reschedules every SYNC_INTERVAL_S after each run.

Each run fetches all offers from the eBay Inventory API and updates the
ebay_listing.status field in any matching local item JSON.  If any items
changed, a coalesced catalog_rebuild job is enqueued.

Queue name: ebay_sync
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import psycopg2.errors
import requests

import tgw.logging as tgw_logging
from tgw.apis.ebay.client import ebay_get
from tgw.apis.fence import ebay_write as fence_ebay_write
from tgw.apis.fence import patch_item as fence_patch_item
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.ebay.pull import backfill_canonical_from_live
from tgw.ebay.sync import fetch_all_offers
from tgw.queue import state_machine
from tgw.queue.worker_base import QueueWorker

log = logging.getLogger(__name__)

QUEUE_NAME = "ebay_sync"
SYNC_INTERVAL_S = 6 * 3600  # check eBay every 6 hours


class EbaySyncWorker(QueueWorker):
    def run(self) -> None:
        self.install_signal_handlers()
        tgw_logging.log_event("worker_start", queue=QUEUE_NAME, owner=self.owner)
        log.info("ebay_sync worker started: owner=%s", self.owner)

        # Enqueue a startup sync job only if the queue is completely idle
        try:
            depths = state_machine.queue_depths()
            if depths.get(QUEUE_NAME, 0) == 0:
                state_machine.enqueue_job(
                    queue_name=QUEUE_NAME,
                    payload={"reason": "startup"},
                    max_attempts=3,
                )
                log.info("ebay_sync: enqueued startup sync job")
        except Exception as exc:
            log.warning("ebay_sync: startup enqueue skipped: %s", exc)

        while not self._stop:
            self._maybe_recover()
            job = self._claim_one()
            if job is None:
                time.sleep(self.poll_interval)
                continue
            self._process(job)

        tgw_logging.log_event("worker_stop", queue=QUEUE_NAME, owner=self.owner)
        log.info("ebay_sync worker stopped")

    def handle(self, job: Dict[str, Any]) -> None:
        target_sku = (job.get("payload_json") or {}).get("sku")

        if target_sku:
            # Per-SKU sync — fetch just this item's offer from eBay
            log.info("ebay_sync: targeted sync for %s", target_sku)
            tgw_logging.log_event("ebay_sync_start", sku=target_sku)
            from tgw.ebay.sync import _find_offer

            try:
                offer = _find_offer(self.config, target_sku)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                log.warning("ebay_sync: eBay unreachable for %s (%s)", target_sku, exc)
                return
            if offer is None:
                log.info("ebay_sync: no eBay offer found for %s", target_sku)
                return
            try:
                updated = self._sync_one(offer, target_sku)
            except Exception:
                log.exception("ebay_sync: error syncing %s", target_sku)
                updated = 0
            if updated:
                try:
                    import time as _time

                    state_machine.enqueue_job(
                        queue_name="catalog_rebuild",
                        payload={"reason": "ebay_sync_targeted"},
                        dedupe_key="catalog_rebuild:pending",
                        not_before=_time.time() + 5,
                        max_attempts=3,
                    )
                except Exception:
                    pass
            log.info("ebay_sync: targeted sync %s → %s", target_sku, "updated" if updated else "no change")
            return

        log.info("ebay_sync: fetching all eBay offers")
        tgw_logging.log_event("ebay_sync_start")

        try:
            offers = fetch_all_offers(self.config)
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status == 400:
                # eBay error 25707: an orphaned offer in our account has a non-alphanumeric
                # SKU that causes the bulk list to fail globally.  Fall back to individual
                # per-SKU lookups for all locally-tracked items with offer_ids.
                try:
                    errs = exc.response.json().get('errors', [])
                    eids = {int(e.get('errorId', 0)) for e in errs}
                except Exception:
                    eids = set()
                if 25707 in eids:
                    log.warning(
                        "ebay_sync: bulk offer list blocked by bad SKU (eBay 25707) — "
                        "falling back to per-SKU individual lookups"
                    )
                    offers = self._fetch_offers_by_local_skus()
                else:
                    raise
            else:
                raise
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            log.warning("ebay_sync: eBay unreachable (%s) — will retry next cycle", exc)
            tgw_logging.log_event("ebay_sync_offline", reason=type(exc).__name__)
            self._reschedule()
            return

        log.info("ebay_sync: received %d offer(s) from eBay", len(offers))
        updated = 0

        for offer in offers:
            sku = offer.get("sku", "")
            if not sku:
                continue
            try:
                updated += self._sync_one(offer, sku)
            except Exception:
                log.exception("ebay_sync: error syncing %s", sku)

        log.info("ebay_sync: updated %d item(s)", updated)
        tgw_logging.log_event("ebay_sync_complete", offers_fetched=len(offers), items_updated=updated)

        if updated:
            try:
                state_machine.enqueue_job(
                    queue_name="catalog_rebuild",
                    payload={"reason": "ebay_sync"},
                    dedupe_key="catalog_rebuild:pending",
                    not_before=time.time() + 30,
                    max_attempts=3,
                )
            except psycopg2.errors.UniqueViolation:
                pass

        self._reschedule()

    def _fetch_offers_by_local_skus(self) -> List[Dict[str, Any]]:
        """Fallback: fetch offers one SKU at a time from local items with offer_ids.

        Used when the bulk GET /offer list is broken by a phantom offer with a
        non-alphanumeric SKU on eBay's side (error 25707).  Slower but always works.
        """
        import time as _time
        itemdata = self.config["itemdata_root"]
        offers: List[Dict[str, Any]] = []
        skus_checked = 0
        for jf in sorted(itemdata.glob("*/tgw*.json")):
            try:
                item = json.loads(jf.read_text(encoding="utf-8"))
                if not item.get("ebay_offer", {}).get("offer_id"):
                    continue
                sku = jf.parent.name
                data = ebay_get(self.config, "/sell/inventory/v1/offer",
                                params={"sku": sku})
                offers.extend(data.get("offers", []))
                skus_checked += 1
                if skus_checked % 100 == 0:
                    log.info("ebay_sync fallback: checked %d SKUs, %d offers so far",
                             skus_checked, len(offers))
                _time.sleep(0.05)
            except Exception as exc:
                log.warning("ebay_sync fallback: error fetching offer for %s: %s",
                            jf.parent.name, exc)
        log.info("ebay_sync fallback: fetched %d offers from %d local SKUs",
                 len(offers), skus_checked)
        return offers

    def _sync_one(self, offer: Dict[str, Any], sku: str) -> int:
        """Update local item JSON from one eBay offer. Returns 1 if item was changed."""
        json_path = self.config["itemdata_root"] / sku / f"{sku}.json"
        if not json_path.exists():
            return 0

        item = json.loads(json_path.read_text(encoding="utf-8"))

        offer_id = offer.get("offerId", "")
        ebay_status = offer.get("status", "")
        listing_info = offer.get("listing", {})
        listing_id = listing_info.get("listingId", "")
        listing_status = listing_info.get("listingStatus", "")
        price_val = offer.get("pricingSummary", {}).get("price", {}).get("value")
        category_id = str(offer.get("categoryId", ""))
        quantity = offer.get("availableQuantity")

        changed = False

        # --- ebay_listing: write all durable eBay-side identifiers ---
        ebay_listing = item.get("ebay_listing") or {}
        listing_updates: Dict[str, Any] = {}
        if offer_id and ebay_listing.get("offer_id") != offer_id:
            listing_updates["offer_id"] = offer_id
        if ebay_status and ebay_listing.get("status") != ebay_status:
            listing_updates["status"] = ebay_status
        if listing_id and ebay_listing.get("listing_id") != listing_id:
            listing_updates["listing_id"] = listing_id
            listing_updates["listing_url"] = f"https://www.ebay.com/itm/{listing_id}"
        if listing_status and ebay_listing.get("listing_status") != listing_status:
            listing_updates["listing_status"] = listing_status
        if listing_updates:
            ebay_listing.update(listing_updates)
            item["ebay_listing"] = ebay_listing
            changed = True

        # --- ebay_offer: write current eBay state; preserve price_comps / staged_at ---
        ebay_offer = item.get("ebay_offer") or {}
        offer_updates: Dict[str, Any] = {}
        if offer_id and ebay_offer.get("offer_id") != offer_id:
            offer_updates["offer_id"] = offer_id
        if ebay_status and ebay_offer.get("status") != ebay_status:
            offer_updates["status"] = ebay_status
        if price_val is not None:
            try:
                price_f = float(price_val)
                if ebay_offer.get("price") != price_f:
                    offer_updates["price"] = price_f
                # Mirror live price into ebay_listing so the UI can show divergence
                # between what we submitted and what eBay currently shows buyers.
                if ebay_listing.get("live_price") != price_f:
                    ebay_listing["live_price"] = price_f
                    item["ebay_listing"] = ebay_listing
                    changed = True
            except (TypeError, ValueError):
                pass
        if category_id and ebay_offer.get("category_id") != category_id:
            offer_updates["category_id"] = category_id
        if quantity is not None and ebay_offer.get("quantity") != quantity:
            offer_updates["quantity"] = quantity
        if offer_updates:
            ebay_offer.update(offer_updates)
            item["ebay_offer"] = ebay_offer
            changed = True

        # PP-EBAY-MIRROR-001 P2: propagate cached ebay_live imageUrls → ebay_offer.photo_urls
        live_block = item.get("ebay_live") or {}
        live_image_urls = live_block.get("inventory_item", {}).get("product", {}).get("imageUrls")
        if live_image_urls and not ebay_offer.get("photo_urls"):
            ebay_offer["photo_urls"] = live_image_urls
            item["ebay_offer"] = ebay_offer
            changed = True

        # PP-EBAY-SNAPSHOT-001 Phase 3: periodic photo integrity check for active listings.
        if listing_status == "ACTIVE" or ebay_listing.get("status") == "Active":
            item["ebay_listing"] = ebay_listing  # anchor before mutation
            changed |= self._check_photo_integrity(sku, item, ebay_listing)

        # Refresh ebay_live with current offer + inventory item snapshot
        from datetime import datetime, timezone

        _now_iso = datetime.now(timezone.utc).isoformat()
        existing_live = item.get("ebay_live") or {}
        new_live = dict(existing_live)
        new_live["offer"] = dict(offer)
        new_live["synced_at"] = _now_iso
        # Also refresh inventory_item (photos, aspects, title) from eBay
        try:
            live_inv = ebay_get(self.config, f"/sell/inventory/v1/inventory_item/{sku}")
            new_live["inventory_item"] = live_inv
            new_live["pulled_at"] = _now_iso
        except Exception as _exc:
            log.warning("ebay_sync: inventory_item GET failed for %s: %s", sku, _exc)
        item["ebay_live"] = new_live
        changed = True

        fence_ebay_write(self.config, sku, ebay_listing=item.get("ebay_listing"), ebay_offer=item.get("ebay_offer"), ebay_live=new_live)

        # Promote eBay live data into canonical inventory fields (brand, model,
        # description, condition enum, weight_oz, etc.) for any field currently empty.
        item["ebay_live"] = new_live  # make the fresh live data visible to backfill
        _promoted = backfill_canonical_from_live(item)
        if _promoted:
            fence_patch_item(self.config, sku, _promoted)
            log.info("ebay_sync: %s backfilled canonical fields: %s", sku, list(_promoted.keys()))

        log.info("ebay_sync: %s → status=%s listing_id=%s price=%s", sku, ebay_status, listing_id, price_val)
        tgw_logging.log_event("ebay_listing_synced", sku=sku, status=ebay_status, listing_id=listing_id, offer_id=offer_id, price=price_val)
        return 1

    def _check_photo_integrity(self, sku: str, item: Dict[str, Any], ebay_listing: Dict[str, Any]) -> bool:
        """GET inventory_item and enqueue ebay_repush if photo count dropped.

        Returns True if ebay_listing was mutated (so caller writes the file).
        Only runs when the item is due for a check (every ebay_verify_interval_days).
        """
        interval_days = int(self.config.get("ebay_verify_interval_days", 7))
        last_checked = ebay_listing.get("photo_verify", {}).get("verified_at")
        if last_checked:
            try:
                age_days = (datetime.now(timezone.utc) - datetime.fromisoformat(last_checked)).days
                if age_days < interval_days:
                    return False
            except (ValueError, TypeError):
                pass

        try:
            live = ebay_get(self.config, f"/sell/inventory/v1/inventory_item/{sku}")
        except Exception as exc:
            log.warning("ebay_sync: photo check GET failed for %s: %s", sku, exc)
            return False

        confirmed = live.get("product", {}).get("imageUrls", [])
        now_iso = datetime.now(timezone.utc).isoformat()

        # PP-EBAY-MIRROR-001 P2: refresh ebay_live + photo_urls from live eBay GET
        stored_photo_urls = item.get("ebay_offer", {}).get("photo_urls") or []
        if confirmed and confirmed != stored_photo_urls:
            fence_ebay_write(
                self.config,
                sku,
                ebay_live={"inventory_item": live, "pulled_at": now_iso},
                ebay_offer={"photo_urls": confirmed},
            )

        submitted = item.get("ebay_submitted", {}).get("inventory_item", {}).get("product", {}).get("imageUrls") or item.get("draft_listing", {}).get("imageUrls", [])
        ebay_listing["photo_verify"] = {
            "submitted_count": len(submitted),
            "confirmed_count": len(confirmed),
            "verified_at": now_iso,
        }

        if submitted and len(confirmed) < len(submitted):
            log.error("ebay_sync: %s photo count dropped — submitted=%d confirmed=%d — enqueueing repush", sku, len(submitted), len(confirmed))
            tgw_logging.log_event("ebay_photo_count_dropped", sku=sku, submitted=len(submitted), confirmed=len(confirmed))
            try:
                state_machine.enqueue_job(
                    queue_name="ebay_repush",
                    payload={"sku": sku},
                    dedupe_key=f"ebay_repush:{sku}",
                    max_attempts=3,
                )
            except psycopg2.errors.UniqueViolation:
                pass
        else:
            log.debug("ebay_sync: %s photo verify OK — %d/%d confirmed", sku, len(confirmed), len(submitted))
            tgw_logging.log_event("ebay_photo_verify_ok", sku=sku, confirmed=len(confirmed))

        return True

    def _reschedule(self) -> None:
        next_run = time.time() + SYNC_INTERVAL_S
        jid = state_machine.enqueue_job(
            queue_name=QUEUE_NAME,
            payload={"reason": "scheduled"},
            not_before=next_run,
            max_attempts=3,
        )
        log.info("ebay_sync: next sync in %dh (job %s)", SYNC_INTERVAL_S // 3600, jid)
        tgw_logging.log_event("ebay_sync_rescheduled", next_run_in_hours=SYNC_INTERVAL_S // 3600, next_job_id=jid)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="tgw-ebay-sync-worker")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = EbaySyncWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
