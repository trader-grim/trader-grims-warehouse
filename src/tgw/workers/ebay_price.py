"""
tgw.workers.ebay_price — Suggest and auto-fill a price for a draft listing.

Enqueued by ebay_draft after the draft_listing block is written.  Queries
eBay Browse API for active listing comps, computes the 25th-percentile price,
and writes:
  - ebay_offer.price        (the suggested price)
  - ebay_offer.price_source (how it was derived)
  - ebay_offer.price_comps  (count, min, p25, median, max)
  - ebay_offer.priced_at    (ISO timestamp)
  - draft_listing.price     (same value, so ebay_publish can read it)

Skips items that already have ebay_offer.price set (idempotent).
If comps are insufficient (< 3 results) price is left null and flagged.

Queue name: ebay_price
Payload:    {sku: "<SKU>"}
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import psycopg2.errors

import tgw.logging as tgw_logging
from tgw.apis.fence import ebay_write as fence_ebay_write
from tgw.apis.fence import patch_item as fence_patch_item
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.ebay.pricing import freeship_price, suggest_price, to_99
from tgw.errors import TreatmentFailure
from tgw.item_mutation import (
    item_generation,
    mutate_item,
    operation_identity,
    reconcile_mutation,
)
from tgw.queue import state_machine
from tgw.queue.worker_base import HardFailure, QueueWorker
from tgw.sqlite_catalog import upsert_catalog_row
from tgw.workflow.item_snapshot import inventory_available

log = logging.getLogger(__name__)

QUEUE_NAME = 'ebay_price'


class EbayPriceWorker(QueueWorker):

    def _receipt(
        self, payload: Dict[str, Any], sku: str, *, outcome: str,
        changed: bool, resulting_generation: str | None,
        operation_id: str = "", mutation_status: str = "",
    ) -> Dict[str, Any]:
        return {
            "receipt_schema_id": "treatment-receipt/v1",
            "treatment_id": "ebay-price",
            "treatment_version": "1",
            "graph_id": payload["graph_id"],
            "goal_profile_id": payload["goal_profile_id"],
            "goal_profile_version": payload["goal_profile_version"],
            "object_generation": payload["object_generation"],
            "condition_hash": payload["condition_hash"],
            "entity_id": sku,
            "outcome": outcome,
            "established_conditions": (["priced"] if outcome == "satisfied" else []),
            "evidence": {
                "changed": changed,
                "resulting_generation": resulting_generation,
                **({"operation_id": operation_id} if operation_id else {}),
                **({"mutation_status": mutation_status} if mutation_status else {}),
            },
        }

    def _commit_governed_price(
        self, *, job: Dict[str, Any], payload: Dict[str, Any], sku: str,
        json_path: Path, checkpoint: Dict[str, Any],
    ) -> Dict[str, Any]:
        expected = {
            "schema": "ebay-price-observation/v1",
            "sku": sku,
            "expected_generation": payload["object_generation"],
        }
        if any(checkpoint.get(key) != value for key, value in expected.items()):
            raise HardFailure("ebay_price checkpoint identity mismatch")
        fields = checkpoint.get("fields")
        if not isinstance(fields, dict):
            raise HardFailure("ebay_price checkpoint fields missing")
        mutation_payload = {
            "schema": checkpoint["schema"],
            "job_id": job["job_id"],
            "graph_id": payload["graph_id"],
            "fields": fields,
        }
        operation_id = operation_identity(
            sku=sku, kind="ebay-price",
            expected_generation=payload["object_generation"], payload=mutation_payload,
        )
        if checkpoint.get("operation_id") != operation_id:
            raise HardFailure("ebay_price checkpoint operation identity mismatch")

        def mutate(document: Dict[str, Any]) -> Dict[str, Any]:
            if document.get("sku") != sku:
                raise ValueError("authoritative document SKU mismatch")
            updated = dict(document)
            updated.update(fields)
            return updated

        def project(_sku: str, document: Dict[str, Any]) -> Dict[str, Any]:
            result = upsert_catalog_row(self.config, document)
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise RuntimeError("SQLite projection did not report success")
            return result

        data_root = Path(self.config.get("data_root", "/opt/TGW/data"))
        journal_root = Path(self.config.get(
            "item_mutation_journal_root", data_root.parent / "var/item-mutations",
        ))
        result = mutate_item(
            item_path=json_path,
            archive_root=Path(self.config.get("archive_root", data_root / "ItemArchive")),
            journal_root=journal_root,
            sku=sku, kind="ebay-price",
            expected_generation=payload["object_generation"],
            payload=mutation_payload, mutate=mutate, project=project,
            operation_id=operation_id,
        )
        status = str(result.status).upper()
        if status == "REPAIR_REQUIRED":
            result = reconcile_mutation(
                item_path=json_path, journal_root=journal_root,
                operation_id=operation_id, project=project,
            )
            status = str(result.status).upper()
        if status == "CONFLICT":
            # A later canonical mutation won.  Let workflow evaluation decide
            # whether a current price, draft, or photo resync is now needed;
            # this attempt remains durable history but is not a dead letter.
            receipt = self._receipt(
                payload, sku, outcome="satisfied", changed=False,
                resulting_generation=result.resulting_generation,
                operation_id=operation_id, mutation_status=status,
            )
            receipt["evidence"].update({
                "detail": result.detail,
                "reason_code": "MUTATION_CONFLICT_REEVALUATE",
            })
            return receipt
        if status != "COMMITTED":
            outcome = {
                "CONFLICT": "conflict", "REPAIR_REQUIRED": "repair_required",
            }.get(status, "failed")
            receipt = self._receipt(
                payload, sku, outcome=outcome, changed=bool(result.changed),
                resulting_generation=result.resulting_generation,
                operation_id=operation_id, mutation_status=status,
            )
            receipt["evidence"]["detail"] = result.detail
            raise TreatmentFailure(
                f"ebay-price mutation did not commit: {status}", receipt,
            )
        draft = fields.get("draft_listing")
        price = draft.get("price") if isinstance(draft, dict) else None
        priced = isinstance(price, (int, float)) and not isinstance(price, bool) and price > 0
        if not priced:
            receipt = self._receipt(
                payload, sku, outcome="partial", changed=bool(result.changed),
                resulting_generation=result.resulting_generation,
                operation_id=operation_id, mutation_status=status,
            )
            receipt["evidence"]["reason_code"] = "PRICE_REQUIRES_OPERATOR_INPUT"
            raise TreatmentFailure("ebay-price produced no positive price", receipt)
        return self._receipt(
            payload, sku, outcome="satisfied", changed=bool(result.changed),
            resulting_generation=result.resulting_generation,
            operation_id=operation_id, mutation_status=status,
        )

    def _checkpoint_and_commit(
        self, *, job: Dict[str, Any], payload: Dict[str, Any], sku: str,
        json_path: Path, fields: Dict[str, Any],
    ) -> Dict[str, Any]:
        mutation_payload = {
            "schema": "ebay-price-observation/v1",
            "job_id": job["job_id"], "graph_id": payload["graph_id"],
            "fields": fields,
        }
        checkpoint = {
            "schema": "ebay-price-observation/v1", "sku": sku,
            "expected_generation": payload["object_generation"], "fields": fields,
            "operation_id": operation_identity(
                sku=sku, kind="ebay-price",
                expected_generation=payload["object_generation"],
                payload=mutation_payload,
            ),
        }
        checkpoint = state_machine.checkpoint_running_job(
            job["job_id"], self.owner, job["lease_token"], checkpoint,
        )
        return self._commit_governed_price(
            job=job, payload=payload, sku=sku, json_path=json_path,
            checkpoint=checkpoint,
        )

    def handle(self, job: Dict[str, Any]) -> None:
        payload = job.get('payload_json') or {}
        sku = payload.get('sku', '')
        if not sku:
            raise HardFailure('ebay_price job missing sku in payload')

        json_path = self.config['itemdata_root'] / sku / f'{sku}.json'
        if not json_path.exists():
            raise HardFailure(f'item JSON not found for {sku}')

        item = json.loads(json_path.read_text(encoding='utf-8'))
        if not inventory_available(item):
            raise HardFailure(
                f'{sku}: inventory is sold, terminal, or zero quantity; '
                'explicitly restore inventory before pricing an eBay listing'
            )

        governed_keys = {
            "treatment_id", "treatment_version", "graph_id",
            "goal_profile_id", "goal_profile_version", "object_generation",
            "condition_hash",
        }
        governed = any(key in payload for key in governed_keys)
        if governed:
            required = governed_keys | {"entity_id"}
            if any(not isinstance(payload.get(key), str) or not payload[key].strip()
                   for key in required):
                raise HardFailure("ebay_price governed job has incomplete identity")
            if payload["treatment_id"] != "ebay-price" or payload["treatment_version"] != "1":
                raise HardFailure("ebay_price governed treatment identity mismatch")
            if job.get("entity_type") != "item" or job.get("entity_id") != sku:
                raise HardFailure("ebay_price governed entity envelope mismatch")
            if payload["entity_id"] != sku or payload.get("object_id", sku) != sku:
                raise HardFailure("ebay_price governed payload entity mismatch")
            if not isinstance(job.get("job_id"), str) or not job["job_id"].strip():
                raise HardFailure("ebay_price governed job_id missing")
            if not isinstance(job.get("lease_token"), str) or not job["lease_token"].strip():
                raise HardFailure("ebay_price governed lease token missing")
            if (item_generation(item) != payload["object_generation"]
                    and "observation_checkpoint" not in payload):
                raise HardFailure("ebay_price governed object generation mismatch")
            if "observation_checkpoint" in payload:
                checkpoint = state_machine.checkpoint_running_job(
                    job["job_id"], self.owner, job["lease_token"],
                    payload["observation_checkpoint"],
                )
                return self._commit_governed_price(
                    job=job, payload=payload, sku=sku, json_path=json_path,
                    checkpoint=checkpoint,
                )

        draft = item.get('draft_listing')
        if not draft:
            raise HardFailure(f'{sku}: no draft_listing — run ebay_draft first')

        # PP-PHOTOSYNC-001 P5 (todo #1120): an operator-set price is consent-
        # gated. The draft→price auto-chain (no origin stamp) must never
        # overwrite a price the operator explicitly typed in — even if
        # ebay_offer.price has since gone missing (e.g. a redraft cleared
        # other offer fields). Only a job carrying origin='operator' — the
        # Re-price button, which clears ebay_offer.price/draft.price itself
        # as its consent signal (invariant C10) — may compute a fresh price
        # over an operator's last price_history entry.
        price_history = item.get('price_history') or []
        suggest_only = False
        if (payload.get('origin') != 'operator' and price_history
                and price_history[-1].get('source') == 'operator'):
            op_price = price_history[-1].get('price')
            log.info('ebay_price: %s — last price_history entry is operator-sourced '
                     '($%s); suggest-only, not overwriting', sku, op_price)
            tgw_logging.log_event('ebay_price_skipped_operator_override', sku=sku,
                                  operator_price=op_price)
            if governed:
                guarded_offer = dict(item.get('ebay_offer') or {})
                guarded_offer['price_guard_skipped'] = {
                    'ts': datetime.now(timezone.utc).isoformat(),
                    'reason': 'operator_price_history',
                    'operator_price': op_price,
                }
                return self._checkpoint_and_commit(
                    job=job, payload=payload, sku=sku, json_path=json_path,
                    fields={
                        'ebay_offer': guarded_offer,
                        'draft_listing': dict(draft),
                    },
                )
            fence_ebay_write(self.config, sku, ebay_offer={
                'price_guard_skipped': {
                    'ts': datetime.now(timezone.utc).isoformat(),
                    'reason': 'operator_price_history',
                    'operator_price': op_price,
                },
            })
            # audit#1143 #1240 code-review follow-up: the write above is a
            # real item mutation (invariant A7 — every mutation enqueues a
            # coalesced catalog_rebuild). The pre-#1240 code used to reach
            # this same enqueue further down (after wastefully calling
            # suggest_price() first); the #1240 early return skipped it
            # entirely, leaving the catalog stale until an unrelated write.
            try:
                state_machine.enqueue_catalog_rebuild(f'ebay_price_guard_skipped:{sku}')
            except psycopg2.errors.UniqueViolation:
                pass
            # audit#1143 #1240: this used to fall through and still call
            # suggest_price() unconditionally below — an operator's last
            # price_history entry means "leave this alone," full stop, not
            # "leave the price alone but still burn a comps query." Return
            # here so the guard is a hard skip, matching its own log message
            # ("skip is early" per test_chain_enqueued_price_skips_when_operator_set_last).
            return

        # Dave (s46): the auto-pricer sets a price only on the initial
        # identification. Any later run still refreshes comps and records a
        # suggestion (ebay_offer.suggested_price) — it powers the comp
        # engine but never touches draft/offer price or the repricer floor.
        existing = item.get('ebay_offer', {})
        if existing.get('price') is not None or draft.get('price') is not None:
            if not suggest_only:
                log.info('ebay_price: %s already priced — suggest-only re-run', sku)
                tgw_logging.log_event('ebay_price_suggest_only', sku=sku,
                                      price=existing.get('price') or draft.get('price'))
            suggest_only = True

        title          = draft.get('title') or item.get('title', '')
        category_name  = draft.get('category_name') or item.get('ebay_category_name', '')
        category_id    = str(draft.get('category_id') or item.get('ebay_category_id', ''))
        item_condition = str(item.get('condition', '')).strip()
        product_lookup = item.get('product_lookup') or {}
        search_terms   = str(item.get('search_terms') or '').strip()

        if not title or title == sku:
            raise HardFailure(f'{sku}: no title — run ai_identify first')

        if search_terms:
            log.info('ebay_price: using operator search_terms %r for %s', search_terms, sku)
        log.info('ebay_price: querying comps for %r (condition=%r)', title[:60], item_condition)
        tgw_logging.log_event('ebay_price_start', sku=sku, title=title[:60],
                               search_terms=search_terms or None)

        result = suggest_price(
            self.config, title, category_name, category_id,
            item_condition=item_condition,
            product_lookup=product_lookup,
            search_terms=search_terms,
        )

        ebay_offer = dict(existing)
        ebay_offer['price_source'] = result['source']
        # Only overwrite price_comps when we have real data — preserve existing
        # comps if the new search returned nothing (avoids wiping on re-price)
        new_comps = result['comps']
        if new_comps and (new_comps.get('count') or 0) > 0:
            if result.get('comp_items'):
                new_comps['items'] = result['comp_items']
            ebay_offer['price_comps'] = new_comps
        ebay_offer['priced_at']    = result['queried_at']

        suggested = result['price']
        if suggest_only:
            # Comp refresh + suggestion only: price_comps/priced_at were set
            # above; record the suggestion and stop. No draft_listing write —
            # a suggest run must not flip the item's lifecycle state.
            if suggested is not None:
                ebay_offer['suggested_price'] = suggested
                ebay_offer['suggested_at'] = result['queried_at']
            if governed:
                return self._checkpoint_and_commit(
                    job=job, payload=payload, sku=sku, json_path=json_path,
                    fields={
                        'ebay_offer': ebay_offer,
                        'draft_listing': dict(draft),
                    },
                )
            fence_ebay_write(self.config, sku, ebay_offer=ebay_offer, allow_protected=['price_comps'])
            log.info('ebay_price: %s suggest-only → suggested=$%s (%d comps)',
                     sku, suggested, (result['comps'] or {}).get('count', 0))
            tgw_logging.log_event('ebay_price_suggested', sku=sku,
                                  suggested_price=suggested,
                                  source=result['source'])
            try:
                state_machine.enqueue_catalog_rebuild(f'ebay_price_suggest:{sku}')
            except psycopg2.errors.UniqueViolation:
                pass
            return

        if suggested is not None:
            # Launch price: max comp rounded up to next .99 — this is the initial
            # listed price, creating a visible "discount" when the repricer lowers
            # it to target (p25) after the configured period.
            comps = result['comps']
            launch = to_99(comps['max'] * 1.10) if comps.get('max') else suggested
            if launch < suggested:
                # The floor can push the target (p25) above a launch derived from
                # raw junk comps — never launch below the markdown target.
                launch = to_99(suggested)

            # PP-FREESHIP-001: when free_shipping_enabled, absorb shipping cost
            # into the listing price and mark the item for a free-shipping policy.
            _ship_cost_used = 0.0
            if self.config.get('free_shipping_enabled'):
                _item_ship = item.get('shipping_cost')
                ship_cost = float(
                    _item_ship if _item_ship not in (None, '')
                    else self.config.get('default_shipping_cost', 0.0)
                )
                if ship_cost > 0:
                    _ship_cost_used = ship_cost
                    base_launch = launch
                    launch = freeship_price(launch, ship_cost)
                    item['free_shipping'] = True
                    log.info('ebay_price: %s freeship → $%.2f (base=$%.2f + ship=$%.2f)',
                             sku, launch, base_launch, ship_cost)

            ebay_offer['price']        = launch
            # target_price (repricer floor) must absorb the same shipping cost so the
            # repricer never marks down to a price that leaves shipping uncovered.
            ebay_offer['target_price'] = (
                freeship_price(suggested, _ship_cost_used) if _ship_cost_used > 0
                else suggested
            )
            draft['price']             = launch      # staged at launch price

            # PP-STRIKE-001: record MSRP as originalRetailPrice when it exceeds
            # the launch price, so the offer body gets a strikethrough display.
            msrp_raw = product_lookup.get('msrp')
            if msrp_raw:
                try:
                    msrp_float = float(msrp_raw)
                    if msrp_float > launch:
                        draft['original_retail_price'] = round(msrp_float, 2)
                        log.info('%s: original_retail_price=%.2f from product_lookup.msrp',
                                 sku, msrp_float)
                except (TypeError, ValueError):
                    pass

            log.info('ebay_price: %s → launch=$%.2f target=$%.2f (%d comps, %s, conf=%s)',
                     sku, launch, suggested,
                     comps.get('count', 0), result['source'],
                     result.get('price_confidence', '?'))
            tgw_logging.log_event('ebay_price_set', sku=sku,
                                  price=launch,
                                  target_price=suggested,
                                  source=result['source'],
                                  price_confidence=result.get('price_confidence'),
                                  comps=comps)
        else:
            ebay_offer['price'] = None
            log.warning('ebay_price: %s — insufficient comps, price left null', sku)
            tgw_logging.log_event('ebay_price_no_comps', sku=sku, title=title[:60])

        draft['price_confidence'] = result.get('price_confidence', 'low')

        item['ebay_offer']    = ebay_offer
        item['draft_listing'] = draft

        # Re-score quality now that price_comps are present (comp_pts were 0 at draft time)
        try:
            from tgw.listing_quality import score_draft
            draft['quality'] = score_draft(item).to_dict()
        except Exception as exc:
            log.warning('ebay_price: quality rescore failed for %s: %s', sku, exc)

        top_level_patch = {'draft_listing': draft}
        if item.get('free_shipping'):
            top_level_patch['free_shipping'] = True
        if governed:
            return self._checkpoint_and_commit(
                job=job, payload=payload, sku=sku, json_path=json_path,
                fields={'ebay_offer': ebay_offer, **top_level_patch},
            )
        fence_ebay_write(self.config, sku, ebay_offer=ebay_offer, allow_protected=['price_comps'])
        fence_patch_item(self.config, sku, top_level_patch)



        # Only stage when we have a price — no point creating an offer with no price

        return {"ok": True, "sku": sku}


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-ebay-price-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = EbayPriceWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
