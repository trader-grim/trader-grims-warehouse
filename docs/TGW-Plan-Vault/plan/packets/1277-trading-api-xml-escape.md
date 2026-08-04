# Packet: Trading API XML builders escape caller-supplied string values
Todo: #1277   PP: PP-COHESION-001   Track: SECURITY (resumed) — run this alone after #1276 clears; only stitch together once both pass clean (cadence rule, 2-in-a-row for the resumed sequence)

## Context budget (ALL the model may load)
This packet + `src/tgw/apis/ebay/trading.py` (the whole file, 624 lines,
but only 4 functions need edits — see Spec) + this todo's existing test
file if one exists. Nothing else. Do NOT touch `src/tgw/offers.py` or any
other caller — this packet only changes how the 4 named functions build
their XML bodies, not their call sites or validation logic upstream.

## Verified live before this packet was written
- Confirmed via read: 4 functions in `trading.py` build XML via raw
  f-string interpolation of caller-supplied string parameters with zero
  XML-escaping:
  - `end_item()` (line 363): `listing_id`, `reason`
  - `revise_item_sku()` (line 386): `listing_id`, `new_sku`
  - `revise_item_pictures()` (line 416): `listing_id`, each URL in
    `image_urls`
  - `respond_to_best_offer()` (line 613): `listing_id`, `offer_id`,
    `action`
- Other XML-building functions in this file (`get_orders`,
  `get_my_ebay_selling`, `get_store_categories`,
  `_trading_call_retrying`'s callers for `GetAPIAccessRulesRequest`,
  `get_best_offers`) only interpolate internally-generated ints/dates
  (page numbers, page sizes, formatted timestamps) — not caller-supplied
  strings. Out of scope; do not touch them.
- `counter_price` in `respond_to_best_offer()` is a Python `float`
  formatted with `:.2f` — already safe (no string content possible), do
  not wrap it in escaping (would break the numeric format).
- `action` is validated against an enum (`Accept`/`Decline`/`Counter`) one
  layer up in `src/tgw/offers.py:170` before this call — still escape it
  here anyway, defense-in-depth for a function whose contract doesn't
  itself enforce the enum (a future caller could bypass `offers.py`).
- No existing XML-escaping convention exists anywhere in this codebase
  (`grep -rn saxutils src/tgw/` → no hits) — this introduces the stdlib
  `xml.sax.saxutils.escape` as the first and canonical pattern for this
  file. `html.escape()` (the codebase's existing convention for HTML
  contexts, used in #1276) is the wrong tool here — it doesn't escape the
  same character set XML requires and this is XML, not HTML.

## Spec
Add the import and a local text-escaping helper at the top of the file
(near `_t()`):

```python
from xml.sax.saxutils import escape as _xml_escape
```

Wrap every caller-supplied string value interpolated as XML element text
in these 4 functions with `_xml_escape(str(value))`:

- `end_item()`: `_xml_escape(listing_id)`, `_xml_escape(reason)`
- `revise_item_sku()`: `_xml_escape(listing_id)`, `_xml_escape(new_sku)`
- `revise_item_pictures()`: `_xml_escape(listing_id)`, and inside the
  `pics = ''.join(...)` comprehension, `_xml_escape(u)` for each URL
- `respond_to_best_offer()`: `_xml_escape(listing_id)`,
  `_xml_escape(offer_id)`, `_xml_escape(action)`

Do not change anything else — timeouts, function signatures, docstrings,
call-site behavior, retry logic, and every other function in the file
stay untouched.

## Dataset
None — this only changes the outbound XML request body sent to eBay for
these 4 operations; no ItemData/queue/local storage is touched.

## Out of scope
- Every other function in this file (see Verified-live section above for
  why they don't need it).
- `src/tgw/offers.py` or any other caller.
- Do not add new validation/enum-enforcement to `action` — that already
  exists one layer up; this packet is only about escaping, not validation.

## Acceptance (live)
1. Call `revise_item_sku(cfg, listing_id="123", new_sku='tgw123"/><Evil>x</Evil><SKU>')`
   in a way that only inspects the built XML string (not a real eBay
   call) — confirm the resulting `xml_body` contains the escaped form
   (`&quot;` / `&gt;` / `&lt;`) and does NOT contain a literal unescaped
   `<Evil>` element that `ET.fromstring` would parse as real XML
   structure.
2. Same check for `end_item()` with a malicious `reason` value, and for
   `respond_to_best_offer()` with a malicious `offer_id`.
3. Call each of the 4 functions with normal, real-shaped values (a real
   SKU format, a real numeric-string listing_id, a real HTTPS EPS image
   URL) — confirm the built XML is byte-identical to the current (pre-fix)
   output for those inputs (ordinary characters pass through
   `xml.sax.saxutils.escape` unchanged), confirming no behavior regression
   to the common case.
4. Run the full offline suite — zero regressions.

## Quota/risk
None — no new live eBay API calls needed for acceptance (string-level XML
body inspection is sufficient); this is a pure XML-injection defense fix
for outbound requests that mutate/end live eBay listings.
