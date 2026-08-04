# Result: #1485 incremental-id-trigger
Status: done
Todo: #1485   PP: PP-INTAKE-004

Files touched:
- `src/tgw/http_server.py` — `AppendBody` gained `session_complete: Optional[bool]`;
  `append_item()` now calls two new helpers after a successful photo append:
  `_maybe_early_identify()` and `_maybe_session_complete_identify()`.
- `tests/test_http_server.py` — 6 new tests covering below-threshold no-op,
  crossing-threshold single enqueue, no-refire-once-identified, session-complete
  refinement (sets `ai_reidentify`), and session-complete fallback (enqueues
  `ai_identify` directly when it never ran).
- `docs/TGW-Plan-Vault/inbox/claude/INPROGRESS-1485-incremental-id-trigger.md` —
  breadcrumb (worktree-local, will be swept by session-start processing).
- `docs/TGW-Plan-Vault/plan/packets/results/1485-RESULT.md` — this file.

## What was built

`POST /api/items/{sku}/append` with `op: "photo"`, after appending the photo,
now:

1. **Early-fire trigger** (`_maybe_early_identify`): if the running photo
   count on the item has just reached/crossed `ai_identify.py`'s
   `_MAX_PHOTOS_CLOUD` (verified live = **6**, not assumed from the design
   doc) AND `ai_identified` is not yet true on the item, enqueues
   `ai_identify` with `dedupe_key=f"ai_identify:{sku}"` — same dedupe
   convention `bundle_intake.py` already uses, so later machinery (dead
   letter, re-enqueue) treats it identically. Guarded so it only fires once:
   subsequent photos landing after the item is already identified are a
   silent no-op here (refinement is the separate mechanism below, not a
   repeat of this path).
2. **Session-completion signal** (new `AppendBody.session_complete: bool`
   field, since no existing session-completion signal was found anywhere in
   `http_server.py`/workers — verified by grep, none existed): when set true
   on any append call —
   - if `ai_identified` is already true, sets `ai_reidentify: true` via the
     existing `_apply_patch()` — reuses the pre-existing re-scan mechanism
     `workers/ai_identify.py` already reads (verified live: `handle()` reads
     `item.get("ai_reidentify")` as `force_reidentify` and reruns even when
     `already_identified`).
   - if `ai_identified` was never set (fallback — a quick item that never
     crossed 6 photos), enqueues `ai_identify` directly with whatever
     smaller photo set exists, rather than waiting indefinitely.

## Live evidence (verified against REAL production Postgres + REAL ItemData root)

Ran the worktree's own `http_server.append_item()` Python function directly
(same code path the endpoint calls) against the real `state_machine`
Postgres DB and real `itemdata_root`, using two throwaway test SKUs
(`tgw20260717999999001`, `tgw20260717999999002`, titled "THROWAWAY TEST
ITEM ... safe to delete"). Output:

```
[setup] creating throwaway test item tgw20260717999999001
[phase A] photos=6 ai_identified=None
[phase A] queue_jobs rows for ai_identify:tgw20260717999999001 after 6th photo: [('c596cfed-1022-4658-8e11-df8e3eb362a7', 'ai_identify', 'queued', 'ai_identify:tgw20260717999999001', {'sku': 'tgw20260717999999001'})]
[phase B] queue_jobs rows after 7th photo (pre-identify): [('c596cfed-1022-4658-8e11-df8e3eb362a7', 'ai_identify', 'queued', 'ai_identify:tgw20260717999999001', {'sku': 'tgw20260717999999001'})]
[phase C] ai_reidentify=True after session_complete on identified item
[setup] creating second throwaway test item tgw20260717999999002 (fallback path)
[phase D] queue_jobs rows before session_complete (2 photos, below threshold): []
[phase D] queue_jobs rows after session_complete fallback: [('1617b0d2-9bb3-49dd-a763-01c48b862431', 'ai_identify', 'queued', 'ai_identify:tgw20260717999999002', {'sku': 'tgw20260717999999002'})]

ALL LIVE ACCEPTANCE CHECKS PASSED
[cleanup] deleting queue_jobs rows + throwaway item dirs
[cleanup] done
```

Confirmed by direct psql afterward: `select count(*) from queue_jobs where
dedupe_key like 'ai_identify:tgw20260717999999%'` → `0`, and
`ItemData/` has no leftover `999999` dirs — cleanup verified, no test
artifacts left in the real dataset. The queued `ai_identify` jobs were
deleted before any worker could pick them up (no LLM/quota calls made by
this test).

Offline suite: `PYTHONPATH=<worktree>/src pytest -q tests/test_http_server.py`
→ **305 passed** (299 pre-existing + 6 new), confirmed importing from the
worktree path (`tgw.http_server.__file__` resolved under
`/opt/TGW/var/worktrees/1485-incremental-id-trigger`, not the shared
checkout).

## Pre-flight verification (invariant C11 — live, not assumed)

- `_MAX_PHOTOS_CLOUD` in `workers/ai_identify.py`: confirmed **6** by direct
  grep/read, not taken from the design doc's "currently 6" claim alone.
- Result field ai_identify actually writes: confirmed `item["ai_identified"]
  = True` (boolean flag, not e.g. a separate `ai_identify_result` object) —
  the design doc's own phrasing ("e.g. no `ai_identify_result` yet") was
  wrong/aspirational; the real field name is `ai_identified`.
- `ai_reidentify` mechanism: confirmed live in `ai_identify.py::handle()` —
  `force_reidentify = bool(item.get("ai_reidentify"))`; skip is bypassed
  when `force_reidentify` is true even if `already_identified`; cleared via
  `item.pop("ai_reidentify", None)` after a successful run. Also confirmed
  the same pattern already used elsewhere in `http_server.py` (bulk
  `ai_identify` action, lines ~1407/1690) sets `ai_reidentify: True` via
  `_apply_patch` — my new code follows that exact existing convention
  rather than inventing a new one.
- Session-completion signal: confirmed **no existing mechanism** anywhere
  in `http_server.py` or the workers (grepped `session_complete`,
  `capture_session`, `SessionComplete` — zero hits) — added the simple
  explicit flag the packet said to add if none existed.

## Deviations from spec

None. Threshold, field names, and reuse of `ai_reidentify` all matched the
packet's stated spec once verified live — no substitution.

## Out-of-scope findings filed

- **#1497** (filed during this task, `pp_ref=PP-INTAKE-004`): the branch tip
  (`catio-nix-0.0.1-alpha`, since commit `928ca63`) has `http_server.py`
  importing `tgw.ebay.category_aspect_migration`, but that module file (and
  a couple of its dependent edits: `draft_specifics.py`'s
  `remove_ebay_aspects`, `repush.py`, `sync.py`, `workers/ebay_publish.py`,
  `workers/ebay_stage.py`, `workers/pm_intake.py`) were **never
  git-committed** — still sitting as uncommitted/untracked changes in the
  shared checkout as of 2026-07-17. Any fresh clone or isolated worktree of
  this branch fails to even import `http_server.py`. This blocked my own
  isolated-worktree test run until I temporarily copied those files in
  (never committed to my branch, reverted before finalizing) — a real
  workaround, not a substitute for fixing the underlying gap. Flagged as
  its own todo per the "operational friction always gets filed" rule.

No Kotlin app, barcode scanner, or event-bus work touched — out of scope
per the packet, untouched.
