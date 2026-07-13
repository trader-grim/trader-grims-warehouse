# Result: 1304 multi-intake-collision-finding
Status: done
Todo: #1304   PP: PP-COHESION-001 (invariant C11)

Files touched:
- `src/tgw/workers/multi_intake.py`
- `src/tgw/api.py`
- `tests/test_multi_intake.py`
- `docs/TGW-Plan-Vault/reference/invariants.md`

What was wrong: `multi_intake.py`'s derived-child-SKU-collision guard
(`_extract_items`, when a deterministically-derived child SKU already has
an ItemData record) only ever called `log_event('multi_intake_sku_collision',
...)` and a deduped `notify()` — both transient (journald rots; notify is
fire-and-forget). Nothing was written to the colliding item's own JSON, so
there was no way to later query "which items currently have an unresolved
SKU collision from intake" — the exact gap invariant C11 exists to close.

Exact fix: mirrored `ebay_stage.py`'s `legacy_listing_blocked` pattern.
- `multi_intake.py`: on every collision hit, calls
  `fence_patch_item(self.config, sku, {'sku_collision_blocked': {
  'colliding_sku': sku, 'base_sku': base_sku, 'detected_at': <iso ts>}})`
  (imported as `from tgw.apis.fence import patch_item as fence_patch_item`
  — the fenced write path, never a direct JSON write). This is additive:
  the existing `log_event`/`notify` calls are unchanged, still run first.
  A fence-write failure is caught and logged as a warning rather than
  aborting the whole intake split (transient log/notify already ran; the
  normal newitems_dir path is unaffected either way).
- Field name chosen: `sku_collision_blocked` — matches the
  `legacy_listing_blocked` naming convention exactly (`<condition>_blocked`),
  and a future resolve flow can set a matching `sku_collision_resolved`
  boolean the same way `legacy_listing_resolved` works.
- `src/tgw/api.py` (`_verify_item`): added catalog-verify rule
  `sku_collision_unrepaired` (severity `warning`) immediately after the
  existing `legacy_listing_unrepaired` block — fires when
  `sku_collision_blocked` is set and `sku_collision_resolved` is not truthy.

Test added: `tests/test_multi_intake.py::test_extract_items_skips_existing_sku_without_touching_it`
updated (was asserting the colliding item's JSON was *completely*
unchanged — now asserts original fields are preserved AND the new
`sku_collision_blocked` field is set with `colliding_sku`/`base_sku`/
`detected_at`, then calls `tgw.api._verify_item()` directly and asserts
`sku_collision_unrepaired` is in the returned violation rules). The
re-drop-dedup test (`test_collision_notify_is_deduped_across_batch_redrop`)
was updated to patch `fence_patch_item` (via `tests/conftest.py`'s existing
`make_fake_patch_item` helper, same helper `ebay_stage`'s tests use) so it
doesn't attempt a real HTTP fence call.

Live evidence (offline test run, PYTHONPATH pinned to worktree, confirmed
`tgw.workers.multi_intake.__file__` and `tgw.api.__file__` resolve under
the worktree path before running):
```
PYTHONPATH=/opt/TGW/var/worktrees/1304-multi-intake-collision-finding/src pytest -q
...
1 failed, 2150 passed, 1 skipped, 1 warning in 51.84s
FAILED tests/test_llm_google_direct.py::TestCallModelGoogleDirectDispatch::test_success_does_not_touch_openrouter
```
That is exactly the known pre-existing flake tracked as todo #1370
(shared quota-state pollution in full-suite runs, passes in isolation) —
not caused by this change. `tests/test_multi_intake.py` alone: `3 passed`.

Deviations from spec: none. Field name `sku_collision_blocked` was a
naming choice explicitly left to me by the packet, justified above
(matches `legacy_listing_blocked` convention).

Out-of-scope findings filed: none — no adjacent broken thing was found
during this task; the packet's described gap was the only issue in this
guard.
