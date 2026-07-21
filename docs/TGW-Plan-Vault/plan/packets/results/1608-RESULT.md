# Result: 1608 statemachine-manifest (PP-STATEMACHINE-001, all 4 phases)

Status: done
Todo: #1608   PP: PP-STATEMACHINE-001

## Files touched

- `src/tgw/queue/state_machine.py` — `MissingManifestFieldError`, `resolve_priority()` +
  `_load_queue_priorities()`/`_reset_queue_priorities_cache()`, `enqueue_job()` extended
  with `priority: Optional[int] = None` (config-lookup default), `supersede: bool = False`,
  `dedupe_key_exempt: bool = False`, and the Phase 4 enforcement raise.
- `src/tgw/workers/ebay_legacy_sync.py`, `ebay_sync.py`, `token_refresh.py`,
  `velocity_stats.py`, `ebay_price_reducer.py`, `ebay_sku_migrate.py`, `ebay_dole.py`,
  `sync_conflict.py` — shared `'<queue_name>:pending'` debounce dedupe_key on both the
  startup-enqueue and `_reschedule()` call sites (Phase 1).
- `src/tgw/workers/ebay_upload.py` — `dedupe_key=f'ebay_upload:{sku}'` on the quota-retry
  reschedule (Phase 1, item B).
- `src/tgw/http_server.py` — `sync_from_ebay` action (line ~2005) and the
  `revision/apply` follow-up sync (line ~8309) both got `entity_type='item'`,
  `entity_id=sku`, `dedupe_key=f'ebay_sync:sku:{sku}'` (Phase 1, item D.2) — deliberately
  distinct from `ebay_sync`'s own `'ebay_sync:pending'` singleton key. Also added
  `import psycopg2.errors` near the top.
- `src/tgw/api.py` — `restart-ebay-token` CLI now passes
  `dedupe_key="token_refresh:pending"`, `supersede=True` (Phase 3).
- `docs/TGW-Plan-Vault/reference/invariants.md` — new **E16** section (Phase 4).
- `docs/TGW-Plan-Vault/plan/packets/results/1608-tgw-queue-priorities.json` — the reviewed
  Phase 2 config content, **not yet deployed** to `/opt/TGW/config/` (see Deviations).
- `tests/test_statemachine_manifest.py` — new, 15 tests covering all 4 phases (offline,
  mocked DB connection, same convention as `tests/test_agent_trace.py`).
- `tests/test_invariant_c12_field_set_accessors.py` — refreshed the C12 allowlist's
  line-number pins in `http_server.py` (shifted +1 by the new `import psycopg2.errors`
  line) — pre-existing known-fragile detector design, not a real accessor-routing change,
  per the file's own documented convention (see prior refresh comments already in that
  file).

## Live evidence

Offline test evidence is the acceptance bar for this packet (per its own spec — the
touched workers are stopped or need a restart to pick up code changes, a separate
Dave/Claude deploy step after review):

```
PYTHONPATH=/opt/TGW/var/worktrees/1608-statemachine-manifest/src \
LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH \
python -m pytest -q
```
→ **2744 passed, 1 skipped** at every phase checkpoint I stopped to re-run the suite
(after Phase 1's 8-worker + ebay_upload + http_server fixes; again after Phase 2/3 landed
together; again after Phase 4's enforcement flip). No checkpoint ever left the suite red.

Confirmed `tgw.queue.state_machine.__file__` resolves under the worktree path (not the
shared checkout) before each run, per the tgw-coder contract's PYTHONPATH-override caveat.

Full-repo re-audit before flipping Phase 4's enforcer on (AST-based, not the original
#1607 audit's grep, per the packet's own "don't just trust the prior audit" instruction):
every real `enqueue_job(...)` call site in `src/tgw/` (62, function-definition and
`state_machine.py`'s one internal self-call excluded) plus all 3 `scripts/requeue_*.py`
call sites already supplied a non-empty `dedupe_key`, and every `entity_type='item'` call
already supplied a non-empty `entity_id` — **zero holdouts found**, `dedupe_key_exempt`
shipped but unused as of this packet landing.

## Deviations from spec

1. **`tgw-queue-priorities.json` could not be written directly to
   `/opt/TGW/config/`** — the worktree-isolation contract's PreToolUse classifier
   correctly blocked writes to that live, non-git-tracked path (same category as
   `tgw-models.json`: config/ is intentionally not part of this repo). The fully
   reviewed content is instead at
   `docs/TGW-Plan-Vault/plan/packets/results/1608-tgw-queue-priorities.json`, ready for
   a live-deploy copy as a post-review step. `resolve_priority()` tolerates the file
   being absent (falls back to `normal`/100 for every lookup), so nothing breaks in the
   interim — this matches the packet's own "falls back to normal if no config entry
   exists" spec, just extended to "or no config file at all."
2. **The packet's proposed `ebay_publish:end_listing`/`ebay_publish:mark_sold` operation
   names don't exist in this codebase.** Grepped: no `enqueue_job()` call site anywhere
   in `src/tgw/` passes a non-default `operation=` kwarg — every call implicitly uses
   `enqueue_job()`'s own default, `'run'`. `ebay_end_listing`/`mark_sold` are
   `http_server.py` operator-action strings that write item JSON directly and end/mark
   the listing synchronously — they never enqueue an `ebay_publish` job with those
   literal operation values. Seeded `tgw-queue-priorities.json` with `'<queue>:run'`
   keys instead (matching today's real call shape), noted in the config file's own
   `_comment`. `ebay_publish:run` itself got `urgent` (the actual live-listing-publish
   action, closest real analog to the packet's intent).
3. **`alt_text_batch`** (named in the packet as a bulk/background queue) doesn't exist
   either — the real queue name is `alt_text`. Used the real name.
4. **Priority default changed from `priority: int = 100` to `priority: Optional[int] =
   None`** so `enqueue_job()` can distinguish "caller passed nothing (use config lookup)"
   from "caller explicitly passed 100" — not stated explicitly in the packet but required
   to implement "an explicit priority= argument always overrides the config lookup"
   correctly (an explicit `priority=100` and an absent `priority=` are otherwise
   indistinguishable). No caller passes `priority=` today, so this is a no-op change to
   existing behavior.
5. **`supersede`'s cancel scope** — the packet didn't specify which prior states are
   eligible to be cancelled-and-replaced. Chose `('queued', 'retry_wait', 'failed',
   'dead_letter')` — i.e. everything genuinely pending or stalled — and deliberately
   excluded `'leased'`/`'running'` (a job actively being worked right now isn't
   preempted by a fresh enqueue under the same key; it's left to finish or fail on its
   own). Documented in the docstring and inline comment.
6. **`debounce=True` and `supersede=True` together raise `ValueError`** (not specified
   in the packet) — they're contradictory collision-handling modes (extend-later vs.
   cancel-and-replace-now) and allowing both silently would make the actual behavior
   depend on undocumented code-path ordering. Flagging as a deliberate choice, not a
   silent substitution.
7. **Queue-priority tier seeding is not exhaustive** — left unmapped (falls back to
   `normal`): `ebay_repush` seeded explicitly at `normal` (not exhaustive-critical, but
   included since it's a direct photo-repush path); anything not listed in the
   config's key set (e.g. any future queue) falls back to `normal` by design, not an
   omission needing a todo.

## Out-of-scope findings filed

None — the #1607 audit's D.1/D.2/D.3 decision points were all resolved within this
packet's own Phase 3/1 scope (that was the point of dispatching all 4 phases together).
No adjacent broken thing was noticed that fell outside this packet's declared scope.
