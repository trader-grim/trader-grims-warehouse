# Review: todo #1562 — condition-enum-flagging

Status: cleared
Reviewer: Claude (main session)
Branch: `todo/1562-condition-enum-flagging` @ `5c7a1c9` (1 commit ahead of `catio-nix-0.0.1-alpha`)

Checked against PP-CONDITION-ENUM-001's master-plan spec (3 numbered requirements:
shared `flagFieldInvalid()`, save-error field contract, server-side enum validation)
and the 1562-RESULT.md manifest.

- Spec item 1 (shared red-border flag fn): `flagFieldInvalid()` added once in
  `http_server.py`, used by both `updateCharCount()` (refactored, not duplicated)
  and both condition-select render paths (initial SSR + `loadCatCtx()`). Confirmed.
- Spec item 2 (save-error field contract): `extract_ebay_error_field()` in
  `ebay/sync.py`, wired identically into `ebay_stage.py`/`ebay_publish.py`'s
  `pipeline_error` dict via the fence (`fence_patch_item`); item-detail page reads
  `pipeline_error.field` and calls `flagFieldInvalid()` on load. PATCH rejection also
  returns `{"field": "condition_enum", ...}`. Confirmed.
- Spec item 3 (server-side enum validation before persist): `patch_item()` checks
  `is_known_condition_enum()` before `_apply_patch()`, returns 422 without touching
  disk on failure. Confirmed — closes the exact C14-class silent-corruption path from
  the live incident.
- Scope: all touched files (`conditions.py`, `sync.py`, `http_server.py`,
  `ebay_stage.py`, `ebay_publish.py`, 5 test files) are within the packet's declared
  area. The `test_invariant_c12_field_set_accessors.py` line-number allowlist refresh
  is the detector's own documented position-pinned behavior, not scope creep.
- Deviation noted in manifest (PATCH validates against the *global* enum vocabulary,
  not per-category subset) is a deliberate, well-reasoned narrowing — flagged
  correctly rather than silently present in the diff. No objection; leaving as-is.
- Invariants: C11 (durable finding via fence-persisted `pipeline_error.field`), C12
  (field-set accessor discipline unchanged, only line positions shifted), C14 (this
  IS the fix for the C14-class incident — operator-facing PATCH now rejects rather
  than silently corrupts) all satisfied.
- Live evidence: manifest's TestClient-based evidence (422 rejection, red-border SSR,
  live `pipeline_error.raw` → `condition_enum` extraction against the real
  tgw202605051124483 record) reviewed and accepted. Independently re-ran the targeted
  test files from the worktree (`tests/test_condition_options.py`,
  `test_extract_ebay_error_field.py`, `test_http_server.py`,
  `test_invariant_c12_field_set_accessors.py`) — 340 passed, 0 failed.

Operational friction filed: pytest run via `sudo -u tgw` inside this worktree fails
collection (`PermissionError` on `flake.lock`, a db-owned symlink into
`/home/db/tgw-flake` that `tgw` can't traverse) — worked around by running as `db`
instead. Not a defect in this branch; filing as a todo per the skill's standing
"any operational friction gets a todo" rule.

No trigger fired. Cleared for stitch.
