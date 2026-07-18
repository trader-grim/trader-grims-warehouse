# Result: 1468 c14-clear-value-detector

Status: partial
Todo: #1468   PP: PP-LISTEDITOR-001

## Summary

Built the invariant-C14 fleet-wide "clear value round-trip" detector: every
operator-facing save path in `src/tgw/http_server.py` was inventoried, and
each got a round-trip test (set → save → clear → save → re-read → assert
actually empty) or an explicit, stated reason for exclusion. While building
the detector, it found two live, previously-undiscovered instances of the
same bug class the Material incident (2026-07-16) was root-caused for. Per
the packet's explicit instruction, neither was fixed inline — both are
captured as permanent `xfail` regression tests (will flip green the moment
someone fixes them) and filed as new todos with full repro detail.

## Save-path inventory (`src/tgw/http_server.py`, cross-checked against
`src/tgw/revision.py` for the revision-apply path named in the packet)

| Path | Endpoint | Covered? | Why |
|---|---|---|---|
| Item-detail direct field edit | `PATCH /api/items/{sku}` (bare top-level field) | ✅ tested, GREEN | `test_c14_patch_item_top_level_field_clear_roundtrip` |
| Aspects form (Set B) | `PATCH /api/items/{sku}` (`draft_listing.item_specifics`) | ✅ tested, GREEN | `test_c14_patch_item_aspects_form_clear_roundtrip` — HTTP-level companion to the existing accessor-level test in `test_draft_specifics.py` |
| Bulk edit | `POST /api/bulk/apply` | ✅ tested, GREEN | `test_c14_bulk_apply_title_clear_roundtrip` (BulkBody.value is a plain str, "" is a legitimate clear-request) |
| Revision proposal accept | `POST /api/items/{sku}/action` `action=accept_proposals` | ✅ tested, GREEN | `test_c14_accept_proposals_clear_roundtrip` — prior tests here only ever asserted non-empty accepted values |
| Base-data padlock auto-sync | `_apply_patch`'s `draft_listing` branch (fires on every draft_listing save) | ⚠️ tested, **FAILS today** (xfail, todo #1522) | `test_c14_unlocked_description_clear_reverted_by_unrelated_draft_save` + control `test_c14_locked_top_level_field_clear_survives_unrelated_draft_save` (confirms locking IS an effective, non-default mitigation) |
| Revision-apply live push (aspects) | `tgw/revision.py: _place_delta_in_bodies`, behind `POST /api/items/{sku}/revision/apply` | ⚠️ tested, **FAILS today** (xfail, todo #1523) | `TestLiveApply::test_c14_aspects_delta_clear_omits_key_not_blank_value` in `tests/test_revision.py` |
| Set A (`item_attributes`) bare-dict clear | `PATCH /api/items/{sku}` (`item_attributes`, non-machine caller) | Not covered — no operator UI path exists | The "Inventory Record specifics" panel is read-only + lock-toggle only (`+ Add to listing` is additive, moves Set A → Set B, never edits/clears Set A). `inventory_record.set_inventory_fields()` already treats an explicit `""` as a real clear and `None` as a deliberate no-op (both by design, unit-tested in `test_inventory_record.py`) — nothing to regression-guard at the HTTP layer until an operator-facing Set A edit/clear UI exists (see memory `feedback-set-a-destination-not-settled` / todo #1473, Set A's destination is itself unsettled). |
| `inventory-diff/apply` | `POST /api/items/{sku}/inventory-diff/apply` | Excluded, by design | Moves a value from Set B → Set A (checked keys), never discards it — not a "clear" in the C14 sense. |
| `category-aspect-migration/apply` | `POST /api/items/{sku}/category-aspect-migration/apply` | Excluded, by design | Moves unchecked keys Set B → Set A, same reasoning as above. |
| `set-template` | `POST /api/items/{sku}/set-template` | Excluded | Additive-only (fills unset fields), never clears an existing operator value. |
| `photo-order`, `inventory-lock`, `remove-comp` | various | Excluded | Not field-value corrections (ordering metadata, lock-toggle metadata, list-item removal by identity). |
| `revision/apply`'s price/quantity/title fields | `tgw/revision.py: _place_delta_in_bodies` | Not separately covered | Same function as the aspects finding above; price/quantity/title clearing to a falsy-but-valid value (e.g. price=0) isn't the same semantic as "clear a text field" and the existing `TestLiveApply` suite already exercises each field's PUT-body construction individually. |

## Files touched

- `tests/test_http_server.py` — new C14 section (6 tests: 5 passing round-trip
  tests + 1 xfail regression for the padlock finding)
- `tests/test_revision.py` — 1 new xfail regression test in `TestLiveApply`
  for the revision-apply aspect-clear finding
- `docs/TGW-Plan-Vault/reference/invariants.md` — C14 entry updated with
  detector status, coverage summary, and both new findings
- (todos filed via `tgw todo --add`, not a file in this repo)

## Live evidence

Full offline suite, run against the worktree copy (confirmed via
`PYTHONPATH` pointing at the worktree `src/`, not the shared checkout):

```
LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH \
PYTHONPATH=/opt/TGW/var/worktrees/1468-c14-clear-value-detector/src:$PYTHONPATH \
python3 -m pytest -q
...
2543 passed, 1 skipped, 2 xfailed, 1 warning in 219.51s (0:03:39)
```

The 2 xfailed are the two new C14 findings (todos #1522, #1523) — both
`strict=True`, so they will fail the suite (flip to an unexpected pass) the
moment either underlying bug is fixed, forcing the xfail marker to be
removed rather than silently staying green after a fix.

Targeted run isolating just the new C14 tests, confirming the 5 GREEN paths
pass and the 2 findings fail as documented:

```
tests/test_http_server.py::test_c14_patch_item_top_level_field_clear_roundtrip PASSED
tests/test_http_server.py::test_c14_patch_item_aspects_form_clear_roundtrip PASSED
tests/test_http_server.py::test_c14_bulk_apply_title_clear_roundtrip PASSED
tests/test_http_server.py::test_c14_accept_proposals_clear_roundtrip PASSED
tests/test_http_server.py::test_c14_locked_top_level_field_clear_survives_unrelated_draft_save PASSED
tests/test_http_server.py::test_c14_unlocked_description_clear_reverted_by_unrelated_draft_save XFAIL
tests/test_revision.py::TestLiveApply::test_c14_aspects_delta_clear_omits_key_not_blank_value XFAIL
```

## Deviations from spec

- Status is `partial`, not `done`: the detector itself is complete and
  green for every path it covers, but two real bugs it found are
  deliberately left unfixed per the packet's own instruction ("do NOT
  silently fix it as a drive-by... write the failing test... file a new
  todo"). Flagging `partial` rather than `done` since the packet's stated
  acceptance bar ("the new test suite runs and passes for every save path
  that's currently correct, and clearly documents/fails for any path that
  isn't") is met, but two `xfail`s remain open work, not a clean bill of
  health.
- Set A (`item_attributes`) bare-dict clearing is not covered by an HTTP-
  level round-trip test — deliberate, not an oversight: no operator UI path
  currently writes/clears it (see inventory table above). If/when #1473's
  "Set A destination" design work adds an operator-facing edit surface for
  Set A, this detector should gain a matching round-trip test at that time.
- Did not attempt to fix either of the two findings (#1522, #1523) inline —
  explicit packet instruction, mirrors how #1250 and other packets this
  session handled adjacent-but-out-of-scope findings.

## Out-of-scope findings filed

- **Todo #1522** (PP-LISTEDITOR-001): base-data padlock auto-sync silently
  reverts an unlocked top-level `title`/`description` clear on the next
  unrelated `draft_listing` save. Full repro in the todo body and in
  `test_c14_unlocked_description_clear_reverted_by_unrelated_draft_save`.
- **Todo #1523** (PP-LISTEDITOR-001): `revision.py`'s live-push body
  builder (`_place_delta_in_bodies`) never received the #1462 "omit a
  cleared aspect key instead of sending a blank value" fix — a second,
  unpatched instance of the exact eBay-rejects-empty-aspect-value bug.
  Full repro in the todo body and in
  `TestLiveApply::test_c14_aspects_delta_clear_omits_key_not_blank_value`.
