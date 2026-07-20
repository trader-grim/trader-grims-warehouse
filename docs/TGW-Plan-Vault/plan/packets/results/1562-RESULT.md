# Result: 1562 condition-enum-flagging
Status: done
Todo: #1562   PP: PP-CONDITION-ENUM-001

Files touched:
- src/tgw/apis/ebay/conditions.py — added `ALL_CONDITION_ENUMS` (global vocabulary
  derived from `CONDITION_ID_TO_ENUM`) and `is_known_condition_enum()`.
- src/tgw/http_server.py —
  - `_build_condition_options()` now returns `(html, is_invalid)` instead of a bare
    string; the initial server-rendered `<select id="dl-condition-select">` sets its
    border to `#c44` inline when `is_invalid` is true (same treatment the dynamic
    `loadCatCtx()` re-render already had).
  - New shared JS `flagFieldInvalid(elOrId, isInvalid)`; `updateCharCount()` refactored
    to call it instead of duplicating the border-color logic; `loadCatCtx()`'s condition
    re-render now also calls it.
  - `patch_item()` (PATCH `/api/items/{sku}`) now validates
    `draft_listing.condition_enum` against `is_known_condition_enum()` before calling
    `_apply_patch()`; rejects with `422 {"ok": false, "error": ..., "field":
    "condition_enum"}` instead of silently merging a corrupt value.
  - `_pe_norm` (pipeline_error rendering) now carries a `field` key; a
    `DOMContentLoaded` snippet calls `flagFieldInvalid()` on the mapped element
    (`condition_enum` → `dl-condition-select`, `title` → `dl-title-input`) so a
    dead-lettered item opens already flagged, not just after the operator touches it.
- src/tgw/ebay/sync.py — new `extract_ebay_error_field(body)`: best-effort field
  extraction from an eBay error JSON body (structured `parameters[].name in
  {fieldName,field}`, then a `\[(\w+)\]` regex fallback against
  `parameters[].value`/`longMessage`/`message`), plus `_EBAY_FIELD_TO_DRAFT_FIELD`
  mapping eBay's own field name ("condition") to our draft_listing key
  ("condition_enum"). Returns `None` rather than guessing when nothing matches.
- src/tgw/workers/ebay_stage.py, src/tgw/workers/ebay_publish.py — both now add
  `'field': _extract_ebay_error_field(raw/body_text)` to the persisted
  `pipeline_error` dict alongside the existing `code`/`detail`/`raw`/`ts`/`source` keys.
- tests/test_condition_options.py — updated for the new `(html, invalid)` return
  shape; added a live-incident-shaped regression case (`"Very Good"` label).
- tests/test_http_server.py — added `test_patch_rejects_invalid_condition_enum` and
  `test_patch_accepts_valid_condition_enum`.
- tests/test_extract_ebay_error_field.py — new file; covers the live incident's
  verbatim raw body plus structured/fallback/malformed/no-match cases.
- tests/test_invariant_c12_field_set_accessors.py — refreshed the C12 line-number
  allowlist (this detector is documented as position-pinned/fragile-by-design; no
  accessor-routing behavior changed, only line positions shifted).

Live evidence:
1. Title-length red-border: `updateCharCount()` now calls
   `flagFieldInvalid(inp, n>max)`, which sets `el.style.borderColor=isInvalid?'#c44':'#444'`
   — byte-identical border-color logic to the pre-refactor inline code, confirmed by
   reading the generated JS output directly (see http_server.py ~line 7024-7037) and by
   `pytest tests/test_http_server.py` passing in full (324/324, includes existing
   char-count-adjacent coverage).
2. Rendered a real item via `fastapi.testclient.TestClient` against
   `http_server.app` with `draft_listing.condition_enum = "Very Good"` (a value not in
   its category's allowed set) and GET `/form/items/<sku>`: the initial server-rendered
   HTML contains
   `<select id="dl-condition-select" style="background:#1a1a1a;color:#eee;border:1px solid #c44;...">`
   — red border on first paint, no operator interaction required.
3. `PATCH /api/items/<sku>` with `{"fields":{"draft_listing":{"condition_enum":"Very Good"}}}`
   via the same TestClient returns `422` with body
   `{"ok": false, "error": "condition_enum 'Very Good' is not a valid eBay Inventory API condition enum — rejected, not saved", "field": "condition_enum"}`,
   and the on-disk item JSON's `draft_listing.condition_enum` is confirmed unchanged
   (test: `test_patch_rejects_invalid_condition_enum`, passing). A valid enum
   (`USED_VERY_GOOD`) is accepted and persisted normally (`test_patch_accepts_valid_condition_enum`).
4. Ran `extract_ebay_error_field()` directly against tgw202605051124483's actual
   persisted `pipeline_error.raw` (read live from
   `/opt/TGW/data/ItemData/tgw202605051124483/tgw202605051124483.json`):
   ```
   raw: {"errors":[{"errorId":2004,"domain":"ACCESS","category":"REQUEST","message":"Invalid request","longMessage":"The request has errors. For help, see the documentation for this API.","parameters":[{"name":"reason","value":"Could not serialize field [condition]"}]}]}
   extracted field -> condition_enum
   ```
   Also verified via TestClient that a pipeline_error with `"field": "condition_enum"`
   renders a `DOMContentLoaded` script calling
   `flagFieldInvalid('dl-condition-select', true)` on page load.
5. Full offline test suite: `2588 passed, 1 skipped` (`pytest -q`, run with
   `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH PYTHONPATH=<worktree>/src:$PYTHONPATH` from
   inside the worktree — confirmed `tgw.http_server.__file__` resolves under the
   worktree path, not the shared checkout).

Deviations from spec:
- The PATCH-side validation checks `condition_enum` against the GLOBAL Inventory API
  enum vocabulary (`ALL_CONDITION_ENUMS`, 17 known enum strings), not the tighter
  per-category allowed subset. Rationale: the actual corruption bug is a value that
  isn't even a real enum at all (a human label) — the global check catches that
  unconditionally and doesn't depend on `cfg`/category lookup succeeding inside the
  PATCH handler. A real-but-category-mismatched enum (e.g. valid on some category, not
  this one) is left to the existing dropdown-surfacing behavior
  (`_build_condition_options`'s "not valid for this category, please fix" flow), which
  already handles that softer case by design (category can legitimately change
  post-set, per the existing `condition_remap` logic). Flagging this as a deliberate
  narrowing, not an oversight — happy to tighten to per-category if Dave wants stricter
  PATCH-time rejection.
- `_EBAY_FIELD_TO_DRAFT_FIELD` currently maps only `condition` → `condition_enum` (the
  one confirmed live shape). Per the packet's own instruction ("it's fine if `field` is
  null/absent when it can't be determined — don't force a bad guess"), an unmapped eBay
  field name is returned verbatim rather than dropped, but the client-side
  `_peFieldEls` id-map in `http_server.py` currently only wires `condition_enum` and
  `title` to real elements — any other returned field name is a safe no-op today (no
  flagging happens, no error), ready to extend when another mapped field's incident
  shows up.

Out-of-scope findings filed: none — no new adjacent breakage found; the only
maintenance touch outside the immediate feature (test_invariant_c12_field_set_accessors.py's
line-number allowlist refresh) is explicitly anticipated by that detector's own
docstring/comments, not a new finding.
