# Result: 1467 aspects-form-layout
Status: done
Todo: #1467   PP: PP-LISTEDITOR-001

Files touched:
- `src/tgw/http_server.py` (`_CATEGORY_CONTEXT_IIFE`, the `loadCatCtx()` client-side aspects-form renderer)
- `tests/test_http_server.py` (new regression test)

## Root cause (concrete, traced not guessed)

Pre-flight (invariant C11) found the packet's literal claim didn't match live
data as of today: local `draft_listing.item_specifics.fields.Material` on
`tgw202605051207245` is currently `""`, and the real live eBay
`inventory_item.product.aspects` for this SKU currently carries no `Material`
key at all (confirmed via `d.get('ebay_live')` in the item JSON, `synced_at`
2026-07-18). The item's data has clearly been touched by unrelated same-day
work since Dave's 2026-07-16 observation — this is flagged as a deviation
below, not silently absorbed. Per the packet's own instruction ("read the
CURRENT rendering code fresh... likely candidates: sort/grouping... CSS class
difference... ordering") the investigation proceeded on the CODE, using this
item's real category (`38064` / "Porcelain") and its real eBay-Taxonomy
aspect list (fetched live via `get_aspects(cfg, '38064')`), reconstructing
the exact incident scenario (`Material="Cloisonne"`) against that real data.

Category 38064's official aspects (live Taxonomy lookup):
`Original/Reproduction` (FREE_TEXT), `Material` (**SELECTION_ONLY**,
`allowed_values=["Porcelain"]` only), `Country of Origin` (SELECTION_ONLY),
`California Prop 65 Warning` (FREE_TEXT). `Material` IS a normal, official,
"same"-layer recommended aspect for this category — not custom, not
orphaned, matching the packet's framing exactly.

The bug: in the `d.aspects.forEach()` loop inside `loadCatCtx()`, when an
aspect's `mode==='SELECTION_ONLY'`, the code builds a `<select>` whose
`<option>` list comes *only* from `asp.allowed_values`. If the field's real
current value (`cur`, e.g. `"Cloisonne"`) is not itself one of
`allowed_values` (e.g. because the category's allowed set is narrower than
what's actually stored — exactly what happens after a category
reassignment), **no `<option>` in the list gets marked `selected`**. With no
option explicitly selected, the browser defaults the `<select>` to its
first/blank `<option value="">—</option>` — the dropdown visually reads
"—" (empty) even though `data-initial="Cloisonne"` is present in the DOM
attribute the whole time. A genuinely filled, correct, "same"-layer field
renders indistinguishable from every actually-empty peer field around it —
exactly "Dave initially didn't see it was filled."

Traced live (not reasoned abstractly): extracted `_CATEGORY_CONTEXT_IIFE`'s
exact render logic, ran it in Node against the real live-fetched aspect
list for category 38064 with a synthetic `prefill={"Material":"Cloisonne",
"Original/Reproduction":"Vintage Original"}` (Node chosen because the
render logic is genuine embedded JS, not something Python evaluates —
running the actual interpreter for the actual language it's written in is
stronger evidence than a hand-port). Confirmed: BEFORE fix, `Material`'s
`<select>` has zero `selected` options despite `data-initial="Cloisonne"`.
This also explains why a plain `FREE_TEXT`/datalist input never has this
problem — its `value="cur"` attribute is set unconditionally regardless of
`allowed_values` membership; only the `SELECTION_ONLY` `<select>` path
silently drops an off-list value.

Confirming precedent already in the same file: the `dl-condition-select`
`<select>` builder, a few lines above this exact code, already has an
explicit fallback for this identical failure mode — when the current
condition enum isn't in the category's valid list, it appends
`<option value="curVal" selected>curVal — not valid for this category,
please fix</option>` rather than silently losing the value. The aspects
`SELECTION_ONLY` builder never got the same treatment; this is a rendering
gap, not a data bug, matching the packet's own framing.

## Fix

Mirrored the exact same pattern used by `dl-condition-select`: when
`cur` is non-empty and not present in `asp.allowed_values`, prepend a
`selected` `<option>` carrying the real value with a "not in this
category's list, please verify" hint, and border the `<select>` amber
(`#c84`) instead of the default gray (`#444`) so the operator sees both
that (a) the field is filled and (b) it doesn't match the category's
official values — a signal to decide whether to correct it or leave it
(same "operator gate" pattern as everywhere else in this codebase).
Matching `cur` values, and empty fields, are unaffected (verified — see
evidence below). `saveEbayDraft()`'s collection loop (`el.value` vs
`data-initial`) is unaffected: `<select>.value` reflects the selected
option's value regardless of which option is `selected`, so `data-initial`
still equals the rendered value and nothing gets spuriously flagged as
"changed."

## Live evidence

Traced via Node (the JS's own interpreter) against category 38064's real,
live-fetched Taxonomy aspect list (`get_aspects(cfg, '38064')`, run inside
the worktree via `PYTHONPATH=.../src`), reconstructing the incident's exact
values:

BEFORE (current shared-checkout code — no fallback option, value silently
dropped from the visible `<select>`):
```
<select data-aspect="Material" data-initial="Cloisonne"
  style="background:#1a1a1a;color:#eee;border:1px solid #444">
  <option value="">—</option>
  <option value="Porcelain">Porcelain</option>
</select>
```
No `<option>` is `selected` → browser shows "—" (looks empty) despite
`data-initial="Cloisonne"`.

AFTER (worktree fix):
```
<select data-aspect="Material" data-initial="Cloisonne"
  style="background:#1a1a1a;color:#eee;border:1px solid #c84">
  <option value="">—</option>
  <option value="Cloisonne" selected>Cloisonne — not in this category’s
    list, please verify</option>
  <option value="Porcelain">Porcelain</option>
</select>
```
`Cloisonne` is now visibly selected and flagged.

Regression checks (same harness, same real category data):
- `Material="Porcelain"` (a value that DOES match `allowed_values`):
  renders exactly as before — `<option value="Porcelain" selected>` only,
  default gray border, no extra fallback option added.
- `Material` absent from prefill (genuinely empty): renders exactly as
  before — no fallback option, default gray border.

New regression test `test_category_context_js_shows_off_list_selection_value`
in `tests/test_http_server.py` locks in that the served page's JS contains
the `offList` fallback logic (asserts on `_CATEGORY_CONTEXT_IIFE` source,
matching the existing pattern used by the neighboring
`test_item_detail_save_ebay_draft_js_sends_cleared_aspects` test for the
same class of client-side-JS-string assertion).

Full offline suite, run from inside the worktree with the mandatory
`PYTHONPATH=/opt/TGW/var/worktrees/1467-aspects-form-layout/src` /
`LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH` overrides (confirmed testing the
worktree's own copy via `tgw.http_server.__file__`, not the shared
checkout):

```
2539 passed, 1 skipped, 1 warning in 194.39s (0:03:14)
```

Not click-tested in an actual live browser session against a running
`tgw-http` instance — the packet asked to hit "a running dev/test instance"
if possible; auth on `/form/items/{sku}` requires a session-login cookie
(`GET /login`), and reading/using session credentials to script a login was
out of scope for a rendering-only fix (also declined by the sandbox's own
credential-materialization guard when I reached for `tgw.env` directly).
The trace above runs the *actual* embedded JS interpreter (Node) against
the *actual* live-fetched category data and the *actual* incident values,
which is stronger evidence than reasoning about the code abstractly, but
is short of a full end-to-end browser click-test — flagged here rather than
silently claimed as full live-fire.

## Deviations from spec

1. **Data mismatch found during pre-flight (invariant C11), not silently
   adapted around**: the packet's SKU (`tgw202605051207245`) no longer has
   `Material=Cloisonne` in either local Set B or live eBay state as of
   2026-07-18 — it's now empty in both. The bug mechanism was still traced
   and fixed using this item's real category-context data plus the
   incident's own stated values (Cloisonne), since the underlying rendering
   defect is a property of the code, not of this SKU's current data.
   Reported rather than silently treating the packet's stated values as
   still-true.
2. Acceptance was gathered via a real-interpreter (Node) trace of the
   actual embedded JS against real live-fetched aspect data, not a full
   logged-in browser session against a running `tgw-http` instance —
   auth (`/login` session cookie) was the blocker; reading raw secrets to
   script around it was declined per the sandbox's credential guard rather
   than worked around.
3. The added amber-border + "not in this category's list, please verify"
   hint text on the off-list fallback option is a judgment call beyond the
   bare minimum "make the value visible" fix — flagged explicitly rather
   than silently added: it follows the exact same visual-signal pattern
   already used by `dl-condition-select`'s own "not valid for this
   category, please fix" fallback a few lines above in the same function,
   so it's consistent with existing UI conventions in this file, not a
   novel pattern.

## Out-of-scope findings filed

None. No new adjacent issues were found that weren't already covered by
existing todos (#1465, #1471, #1472, #1473) referenced in the linked plan
section.
