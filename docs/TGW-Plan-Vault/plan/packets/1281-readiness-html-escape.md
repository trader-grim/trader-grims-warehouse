# Packet: readiness_html() HTML-escapes item-derived field values
Todo: #1281   PP: PP-COHESION-001   Track: SECURITY (graduated to concurrent — run alongside #1278/#1279/#1283)

## Context budget (ALL the model may load)
This packet + `src/tgw/readiness.py` (the whole file, 253 lines) + this
todo's existing test file if one exists. Nothing else.

## Verified live before this packet was written
- `readiness_html()` (line 228) builds HTML by interpolating
  `f.value` and `f.label` from each `ReadinessField` with zero escaping
  (lines 240, 247-249).
- `f.label` is ALWAYS a hardcoded string literal passed at each `_f(...)`
  call site in `EbayReadinessChecker.check()` (e.g. `"eBay title"`,
  `"Category"`) — never derived from item data. Do NOT escape `label`;
  wrapping a constant in `html.escape()` is a no-op but adds noise not
  asked for by the finding, which is specifically about item-derived
  values.
- `f.value` IS item-derived in multiple fields — confirmed at line 86:
  `title[:60] + ...` where `title = str(dl.get("title") or "").strip()`
  (an AI-generated/operator-edited draft title), and line 96:
  `f"{cat_id} · {dl.get('category_name','')}"`. This is exactly the class
  of untrusted/item-derived string the codebase's own convention already
  escapes elsewhere (`html.escape()`, used identically in `http_server.py`
  and in this session's #1276 fix to `description.py`).
- `val_html`'s interpolation point is HTML element text content (inside a
  `<span>`), not an attribute value — `html.escape()`'s default behavior
  (escaping `&`, `<`, `>`, `"`, `'`) is correct and sufficient here, no
  custom entity set needed.

## Spec
At the top of `readiness.py`, add:
```python
import html as _html
```

In `readiness_html()`, change the `val_html` line to escape the
stringified value:
```python
val_html = (
    f'<span style="color:#667;font-size:.82em;margin-left:8px">{_html.escape(str(f.value))}</span>'
    if f.value else ""
)
```
Do not change `f.label`'s interpolation, the icon, the background/border
colors, or anything else in this function or file.

## Dataset
None — this only changes how the readiness checklist widget renders;
`ReadinessField.value` itself and the item JSON it's derived from are
untouched.

## Out of scope
- `f.label` interpolation (see Verified-live — it's a hardcoded literal,
  not item-derived; do not touch).
- `EbayReadinessChecker.check()` or `_condition_valid_for_category()` —
  read-only for context, not part of this fix.
- Any other file.

## Acceptance (live)
1. Build a `ReadinessField` with `value='<script>alert(1)</script>'` and
   call `readiness_html([field])` — output must NOT contain a literal
   `<script>` tag; must contain the escaped `&lt;script&gt;...` form.
2. A field with a normal short value (e.g. `'123 · Cell Phones'`, no
   special characters) — output byte-identical to the pre-fix output for
   that input (escaping a string with no special chars is a no-op).
3. A field with `value=None` — `val_html` must still be `""` (unchanged
   from current behavior; `str(f.value)` is never reached in that branch
   since the `if f.value else ""` guard short-circuits first).
4. Confirm `f.label`'s output is unchanged (still raw, since it's always
   a hardcoded literal) — this is a deliberate non-fix, not an oversight,
   and the acceptance check should assert it explicitly so a future
   change to how `label` is populated doesn't silently reopen this class
   of bug without anyone noticing the assumption changed.
5. Run the full offline suite — zero regressions.

## Quota/risk
None — no new API calls, pure string-escaping fix for an operator-facing
internal HTML widget.
