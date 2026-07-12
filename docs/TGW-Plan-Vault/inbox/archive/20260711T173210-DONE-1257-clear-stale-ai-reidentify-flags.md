# DONE — todo #1257

## Primary task: clear stale ai_reidentify flags
Re-verified live (read-only) that all 4 items still carried the stale
`ai_reidentify: true` flag from before the #1167 fix. Cleared it through
the **tgw-api fence** (`PATCH /api/items/{sku}` with `ai_reidentify: None`
→ `_apply_patch()`'s established None-means-delete convention), matching
the settled-architecture rule ("tgw-api is the fence — all ItemData
reads/writes go through it") — no direct file write, no re-identify call,
no billed vision-AI call made.

Verified per-item before/after via the fence's own GET and a direct
read of the on-disk JSON:

| SKU | before | after |
|---|---|---|
| tgw202605051936445 | `ai_reidentify: true` | field absent |
| tgw202605052242107 | `ai_reidentify: true` | field absent |
| tgw202605060201087 | `ai_reidentify: true` | field absent |
| tgw202606021107459 | `ai_reidentify: true` | field absent |

`ai_identified` and `title` confirmed unchanged on all 4 (nothing else
touched).

## Secondary observation — resolved, not a bug
The todo flagged 3 of the 4 items showing 3 rounds in
`identification_history` but only 1 entry in `vision_results`, "worth
checking separately." Investigated: for all 3 affected items, rounds 1 and
2 occurred on 2026-06-11/2026-06-16 and the morning of 2026-06-17 — before
commit `35b34d8` ("PP-DERIVED-001 Phase 1+3 — capture full vision raw and
product lookup raw", 2026-06-17 10:15:17 -0700) added `vision_results`
capture to `ai_identify.py` at all. Round 3 (2026-07-02, well after) is the
first round that had the capability to write a `vision_results` entry.
This is a benign timeline artifact of when the feature was added — not
data loss, corruption, or a bug. No repair needed.

## Live verification
- Re-confirmed live (read-only) before touching anything that all 4 items
  still had the stale flag (data hadn't changed since the #1167
  investigation).
- Verified the fence PATCH calls succeeded (`{"ok": true, "updated":
  ["ai_reidentify"]}` for each) and independently re-read the real on-disk
  JSON afterward to confirm the field is fully gone, not just nulled.
- Ran `tgw health` after the change: overall `ok: false`, but all 3
  failing checks (`backups` — db dump stale 123h; `nats` — missing Python
  module; `ebay_sync_fallback` — 424 consecutive runs, already tracked as
  todo #1077) are pre-existing, unrelated conditions, not caused by this
  cleanup. Flagging per Prime Directive 2 rather than silently passing
  over them, but not expanding this todo's scope to fix them — worth a
  look separately, especially the stale backup.

No deviations from the todo brief. No config/secrets/OAuth scopes touched;
no code changes in this todo (data-only cleanup, explicitly authorized).
