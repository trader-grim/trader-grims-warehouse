# Packet: build_listing_description() HTML-escapes the picklist line before embedding

Todo: #1367   PP: PP-COHESION-001   Track: SECURITY (continues the graduated
html.escape() sequence — #1276/#1277/#1281/#1282 already merged clean; no
pairing gate needed, stitch immediately after this clears)

## Context budget (ALL the model may load)
This packet + `src/tgw/ebay/description.py` (the whole file, 78 lines) +
this todo's existing test file if one exists (`tests/test_ebay_description.py`
or similar — check, don't assume). Nothing else.

## Verified live before this packet was written
- `src/tgw/ebay/description.py:76-78` —
  `build_listing_description()` already escapes `ai_desc` (fixed in #1276,
  commit 6368d18) but interpolates `pl = picklist_line(item)` into
  `f'<p>{pl}</p>'` completely unescaped.
- `picklist_line()` (line 38) builds `pl` from `item.get('title', '')` (or
  `draft_listing.title`) — an item field, same trust boundary as `ai_desc`:
  not attacker-input in the security-scanner sense, but LLM/user-editable
  text that can legitimately contain `<`, `>`, `&` and is rendered into
  eBay's buyer-facing listing HTML. This is the exact same HTML-injection
  class #1276 fixed, explicitly flagged as out-of-scope-for-later in that
  packet: "the picklist line has its own separate unescaped-interpolation
  issue (`title` from `picklist_line()`) but that is a distinct,
  not-yet-filed finding" — #1367 is that finding.
- `picklist_line()` itself is also used standalone (its docstring: "used
  for warehouse picking and future QR code generation" / "machine-parseable
  ... matches tgw.source convention") — **do not change `picklist_line()`'s
  own return value**, other consumers depend on its exact plain-text
  `tgw-pl::=::...` format unescaped. Only the HTML-embedding call site in
  `build_listing_description()` needs escaping.

## Spec
In `build_listing_description()`, escape `pl` at the point it's embedded
into HTML, not inside `picklist_line()` itself:

```python
pl = picklist_line(item)

return f'<p>{_html.escape(ai_desc)}</p>{bp_html}<p>{_html.escape(pl)}</p>'
```

`_html` is already imported (`import html as _html`, line 21). No new
imports needed.

## Out of scope
- `picklist_line()`'s own return value / plain-text format — unchanged,
  other consumers (warehouse picking, Google Sheet sync, tgw.source) need
  the raw unescaped text.
- `bp_html` (boilerplate) — operator config, not re-touched here (already
  correctly left unescaped by #1276's packet reasoning).
- Any other file. Only `src/tgw/ebay/description.py`.

## Dataset
None — rendering-only change, stored item fields (title, etc.) are
untouched; only the HTML string sent to eBay changes.

## Acceptance (live)
1. Call `build_listing_description({'title': 'Nice <b>item</b>', 'sku': 'tgw123', 'draft_listing': {}}, cfg)`
   — the picklist `<p>` must contain the literal escaped text
   `Nice &lt;b&gt;item&lt;/b&gt;`, not a real `<b>` element.
2. Call with a title containing `<script>alert(1)</script>` — the returned
   string must NOT contain a literal `<script>` tag anywhere.
3. Call `picklist_line()` directly (not through `build_listing_description`)
   with the same unsafe title — its return value must be byte-identical to
   current (pre-fix) output: still unescaped raw text. Confirms the other
   consumers' contract is untouched.
4. Call with a normal plain-ASCII title/description (no special chars) —
   `build_listing_description()` output must be byte-identical to current
   (pre-fix) output for that input, confirming no regression to the common
   case.
5. Run the full offline suite — zero regressions.

## Quota/risk
None — no new API calls, pure string-escaping fix for a stored-HTML-
injection vector into live eBay listings (buyer-facing surface), same
risk class and fix pattern as #1276 which already merged clean.
