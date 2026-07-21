# Review: todo #1608 statemachine-manifest (PP-STATEMACHINE-001, all 4 phases)

status: cleared
reviewer: Claude (main session, 2026-07-20)
branch: todo/1608-statemachine-manifest @ 51f47fb
base: catio-nix-0.0.1-alpha (correct base, diffed directly, not `main`)

## What was checked

- Result manifest: status/files-touched/live-evidence/deviations all present, no
  sanity-check failure. Live evidence explicitly documents PYTHONPATH override to the
  worktree's own `state_machine.py` before every pytest run (per the worktree-testing
  caveat) — verified this claim is stated correctly, not just "tests pass."
- Diff vs `catio-nix-0.0.1-alpha`: 18 files, every one within declared scope (core
  `state_machine.py`, the 8 flagged workers, `ebay_upload.py`, `http_server.py`'s two
  D.2 call sites, `api.py`'s `restart-ebay-token` wiring, `invariants.md`'s new E16,
  the not-yet-deployed priority config, the new test file, and the C12 allowlist
  line-shift refresh). Nothing outside scope.
- Core logic (`state_machine.py`): `MissingManifestFieldError`, the `dedupe_key`/
  `entity_id` enforcement checks run before the `entity_id = entity_id or queue_name`
  fallback (correct ordering — the fallback can't mask a real caller omission),
  `resolve_priority()`'s config lookup + fallback chain is sound, `supersede`'s
  cancel-then-insert runs inside the same connection/cursor context as the insert
  (atomic), `debounce`+`supersede` mutual-exclusion is explicit and tested.
- Deviations: all six are genuine engineering judgment calls surfaced by real
  codebase facts (the packet's guessed operation names `end_listing`/`mark_sold`
  don't exist; real call shape is `'<queue>:run'`; `alt_text_batch` doesn't exist,
  real name is `alt_text`) rather than silent substitutions — each one explained with
  the actual grep evidence behind it. The `tgw-queue-priorities.json` non-deployment
  is a correct, expected worktree-isolation boundary (same category as
  `tgw-models.json` — config/ isn't git-tracked), not a shortcut.
- Phase 4's re-verification: an independent AST-based re-grep (not just trusting the
  #1607 audit) found zero holdout call sites before flipping enforcement on — matches
  the packet's explicit instruction to re-verify rather than trust the prior audit.
- Invariant E16: follows the E9-E15 template (Rule/Why/Enforcement/Known gap)
  precisely. Cites the real incident chain and the #1406/PP-DEADLETTER-001
  historical precedent accurately (cross-checked against this session's own earlier
  master-plan reading — the "~300k-row" `entity_id` breakage claim matches the
  PP-DEADLETTER-001 finding of ~99.997% of that table defaulting to queue_name).
- Test suite: 2744 passed, 1 skipped, confirmed running the worktree's own code at
  every phase checkpoint. C12 allowlist diff verified as a pure +1 line-number shift
  matching the single new `import psycopg2.errors` line — not masking a real
  accessor-routing change.
- One judgment call worth surfacing, not a defect: `token_refresh:run` was seeded at
  `high` (30) rather than `normal` in the priority config — a reasonable call
  (token refresh is infrastructure-critical) but not explicitly specified in the
  packet. Flagging for visibility, not requesting a fix — Dave can adjust the config
  value trivially post-deploy if he disagrees, that's the whole point of it being
  config.

## Trigger check (step 3)

None fired. No out-of-scope files (test-file carve-out correctly applies to the new
test file and the C12 allowlist refresh, both legitimately tied to in-scope changes).
No invariants.md violation — this diff *adds* an invariant, consistent with every
touched path. No live/production write attempted before stitch (the packet correctly
deferred the config file's live deployment and any worker restarts to the post-review
step, exactly per contract). No todo/pp_ref mismatch.

## Outcome

Cleared for stitch. Not merged — stitch is a separate explicit step. Two follow-up
actions needed at stitch time, both already correctly flagged as deferred in the
result manifest, not part of this review's scope:
1. Copy `docs/TGW-Plan-Vault/plan/packets/results/1608-tgw-queue-priorities.json` to
   `/opt/TGW/config/tgw-queue-priorities.json` (live deploy, not git-tracked).
2. Restart workers to pick up the merged code (same deferred-restart pattern as #1604).
