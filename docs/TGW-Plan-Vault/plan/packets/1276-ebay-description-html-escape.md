# Packet: build_listing_description() HTML-escapes untrusted text before embedding
Todo: #1276   PP: PP-COHESION-001   Track: SECURITY (resumed, cadence reset — first task of new sequence, hold for a second clean run before stitching)

## Context budget (ALL the model may load)
This packet + `src/tgw/ebay/description.py` (the whole file, 77 lines) +
`src/tgw/workers/ebay_draft.py` lines ~440-470 (description-enrichment
call, read-only — confirms description content is meant to be plain
prose, not pre-formatted HTML) + this todo's existing test file if one
exists. Nothing else.

## Verified live before this packet was written
- `src/tgw/ebay/description.py:77` — `build_listing_description()` embeds
  `ai_desc` (from `draft.get('description')` or `item.get('description')`)
  directly into `f'<p>{ai_desc}</p>...'` with zero escaping.
- `ai_desc`'s source is confirmed LLM-generated prose (`ebay_draft.py`
  prompt: "Write in natural prose sentences... Write the eBay listing
  description") — it is never meant to contain real HTML tags, so
  escaping cannot break legitimate formatting.
- The codebase's own established convention for this exact situation is
  `html.escape()` — used identically in `http_server.py` (lines 302, 327,
  3021, 3045-3047) for other untrusted/item-derived strings interpolated
  into HTML responses. This packet applies the same existing convention,
  not a new pattern.
- `bp_html` (the boilerplate paragraphs) is operator-configured via
  `cfg['description_footer']`, not attacker-influenced — do not escape it,
  escaping operator config text would be a scope/behavior change not
  asked for.

## Spec
In `build_listing_description()`, escape `ai_desc` before interpolation:

```python
import html as _html
...
return f'<p>{_html.escape(ai_desc)}</p>{bp_html}<p>{pl}</p>'
```

Only `ai_desc` changes. `bp_html` and `pl` (the picklist line) are
untouched by this packet — the picklist line has its own separate
unescaped-interpolation issue (`title` from `picklist_line()`) but that is
a distinct, not-yet-filed finding; do not fix it here, note it as an
out-of-scope finding in the result manifest so it can be filed as its own
todo.

## Dataset
None — this only changes how the description string is rendered into
eBay listing HTML; the stored `description` field itself is untouched
(Prime Directive 1: raw AI description text keeps being written
unmodified — only its escaped form goes out over the wire to eBay).

## Out of scope
- `picklist_line()`'s own unescaped `title` interpolation — flag as a new
  finding, do not fix here.
- `bp_html` (boilerplate) — operator config, not attacker-influenced,
  leave unescaped.
- Any other file. Only `src/tgw/ebay/description.py`.

## Acceptance (live)
1. Call `build_listing_description({'description': 'Nice <b>item</b>, buy now!'}, cfg)`
   — the returned HTML must contain the literal text
   `Nice &lt;b&gt;item&lt;/b&gt;, buy now!` inside the first `<p>` tag, not
   a real `<b>` element.
2. Call `build_listing_description({'description': '<script>alert(1)</script>'}, cfg)`
   — the returned string must NOT contain a literal `<script>` tag; it
   must contain the escaped `&lt;script&gt;...` form.
3. Call with a normal plain-prose description (no special characters) —
   output must be byte-identical to the current (pre-fix) output for that
   input, confirming no regression to the common case.
4. Run the full offline suite — zero regressions.

## Quota/risk
None — no new API calls, pure string-escaping fix for a stored-HTML-
injection vector into live eBay listings (buyer-facing surface).
