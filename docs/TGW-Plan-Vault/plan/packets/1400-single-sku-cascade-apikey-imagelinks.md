# Packet: single-SKU cascade — KeyError('api_key') + malformed ImageLinks rejection, both on tgw202605051933258

Todo: #1400 (covers #1396 too — same SKU, folded into one investigation
per note added to #1396)   PP: PP-DEADLETTER-001   Track: dead-letter
triage (batch, see PP-DEADLETTER-001.md — dispatched alongside 7 other
packets this round)

## Context budget (ALL the model may load)
This packet + `src/tgw/apis/fence.py` (whole file, ~40 lines — the
`_headers()`/`cfg['api_key']` auth site) + `src/tgw/workers/ebay_stage.py`
(whole file) + `src/tgw/workers/ebay_upload.py` (whole file) + item JSON
for `tgw202605051933258` (`ItemData/tgw202605051933258/tgw202605051933258.json`,
read-only via the fence). Nothing else until you understand the real cause.

## Verified live before this packet was written
- **All 4 of the following dead-letters are the SAME single SKU,
  `tgw202605051933258`** — confirmed via `queue_jobs.payload_json`:
  1. `ebay_stage`: `KeyError('api_key')`, `payload_json={"sku":
     "tgw202605051933258", "force": true}`
  2. `ebay_upload`: `KeyError('api_key')`, `payload_json={"sku":
     "tgw202605051933258"}`
  3. `ebay_stage` (×2, same job retried): `HardFailure('...eBay rejected
     staging: The size for ImageLinks cannot exceed . is an invalid
     attribute')` — note the malformed message itself: "cannot exceed ."
     with nothing after "exceed" and before the period. This looks like
     eBay's own error text got garbled by an empty/missing value being
     interpolated into their error template — plausibly related to
     something unusual about this item's photo count/size, not a generic
     ImageLinks bug affecting other items.
- **Interesting anomaly, worth investigating directly**: all 4 dead-letter
  rows have `entity_id` set to the **queue name itself** (`'ebay_stage'`,
  `'ebay_upload'`) rather than the SKU — every other dead-letter in this
  triage batch has `entity_id` = the real SKU. This strongly suggests
  these 4 jobs were enqueued through a different path than the normal
  per-item enqueue (e.g. a manual/maintenance/bulk-operator action rather
  than the standard pipeline flow) — check `tgw_enqueue`/CLI code paths
  and anything that constructs a `queue_jobs` insert with
  `entity_id=queue_name` instead of `entity_id=sku`, since that's a
  distinct anomaly from the `KeyError`/`ImageLinks` symptoms and may be
  the actual root cause thread to pull.
- `cfg['api_key']` is read in exactly one place, `fence.py:_headers()`
  (line 28) — used on **every** fence call project-wide. If this were a
  genuinely missing/misconfigured secret, every fence call from every
  worker would fail, not just this one item — confirm current fence
  health is otherwise fine (`tgw health`) before spending time on a
  secrets-facility theory. The much more likely explanation is a code
  path specific to whatever enqueued this item's jobs building/passing a
  `cfg`-like dict that's missing `api_key` (e.g. a hand-built dict for a
  one-off/manual reprocessing action, not the normal `self.config`).

## Spec
1. Trace how this SKU's `ebay_stage`(force=true)/`ebay_upload` jobs got
   enqueued with `entity_id` set to the queue name — find that code path
   first, since it's the common thread across all 4 symptoms.
2. Once found, determine why it constructs/passes a config dict missing
   `api_key` — fix at the source (don't patch around it by making
   `_headers()` tolerate a missing key; a missing fence auth key should
   fail loudly, just from a code path that actually has the real cfg).
3. Investigate the malformed "ImageLinks cannot exceed . is an invalid
   attribute" eBay rejection for this same item — check its photo
   count/sizes against eBay's actual PictureURL/ImageLinks limits (search
   eBay API docs reference in `docs/TGW-Plan-Vault/reference/` if a
   relevant note exists) to see whether it's oversized photo count (not
   dimension — that's #1398's separate finding) triggering this.
4. If, after investigation, this turns out to be a single corrupted/
   unusual item rather than a systemic bug (e.g. this SKU was manually
   force-processed via some ad-hoc script that's the real culprit), say so
   plainly in the result manifest — don't manufacture a bigger fix than
   the evidence supports. A one-off data/operational fix for this SKU is
   an acceptable outcome if that's what the evidence shows.

## Out of scope
- Any other SKU or the other 7 packets in this batch.
- Don't change `_headers()`'s auth behavior defensively (e.g. `cfg.get('api_key')`
  with a fallback) — that would hide the real bug rather than fix it.

## Dataset
None expected, unless your investigation finds this item's stored data is
itself the problem (e.g. a bad photo) — if so, follow Prime Directive 1
(never discard/overwrite; if a photo needs remediation, that's
PP-DATAINTEGRITY-001 territory, flag it, don't fix it here).

## Acceptance (live)
1. Identify and fix the actual code path causing the missing `api_key` —
   add a regression test covering that path with a normal, complete cfg.
2. Report the ImageLinks finding (root cause or "isolated to this item,
   no systemic fix needed") in the result manifest with evidence.
3. Run the full offline suite — zero regressions.
4. `tgw health` still clean after your fix.

## Quota/risk
Low — investigation may need one live read of this item's eBay-side state
if useful; no bulk operations.
