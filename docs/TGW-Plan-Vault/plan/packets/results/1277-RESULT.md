# Result: 1277 trading-api-xml-escape
Status: done
Todo: #1277   PP: PP-COHESION-001

Files touched:
- src/tgw/apis/ebay/trading.py (import + 4 functions: end_item,
  revise_item_sku, revise_item_pictures, respond_to_best_offer wrapped
  caller-supplied string values with `xml.sax.saxutils.escape`)
- tests/test_trading_xml_escape.py (new — injection + no-regression tests
  for all 4 functions, per packet's Acceptance items 1-3)

Live evidence:
- `revise_item_sku(cfg, listing_id="226700000001", new_sku='tgw123"/><Evil>x</Evil><SKU>')`
  built XML: `<SKU>tgw123"/&gt;&lt;Evil&gt;x&lt;/Evil&gt;&lt;SKU&gt;</SKU>` —
  `&gt;`/`&lt;` present, no literal `<Evil>` element; `ET.fromstring()` on
  the full body parses with no injected sibling element
  (`root.find('.//Evil') is None`), confirmed by
  `test_revise_item_sku_escapes_malicious_new_sku`.
- Same injection check passed for `end_item()` (malicious `reason`) and
  `respond_to_best_offer()` (malicious `offer_id`), and for
  `revise_item_pictures()` (malicious URL) — all in
  tests/test_trading_xml_escape.py.
- Normal-value round trip: `revise_item_sku`, `end_item`,
  `revise_item_pictures`, `respond_to_best_offer` called with ordinary
  SKU/listing_id/HTTPS-URL/action values produce byte-identical XML
  fragments to the pre-fix behavior (`test_normal_values_pass_through_unchanged`).
- Full offline suite: `PYTHONPATH=<worktree>/src pytest -q` →
  `2116 passed, 1 skipped` (confirmed `tgw.apis.ebay.trading.__file__`
  resolved to the worktree copy before running, per contract step 2).
- New xml-escape test file alone: `26 passed` (includes the 4 sibling
  trading tests already present in the repo, unaffected).

Deviations from spec:
- One flagged, non-behavioral: the packet's Acceptance item 1 example
  expected the escaped output to contain `&quot;` for the `"` character.
  `xml.sax.saxutils.escape()` — the exact stdlib call the Spec section
  names verbatim (`from xml.sax.saxutils import escape as _xml_escape`,
  no `entities=` dict) — only escapes `&`, `<`, `>` by default; it does
  not touch `"` or `'`. This is correct XML per se (quote escaping is
  only required inside attribute values, and none of these 4 builders
  ever place caller-supplied data inside an attribute — all insertions
  are element text content, where `"` needs no escaping to remain
  well-formed). Implemented exactly per the Spec section's literal
  instruction (`_xml_escape(value)`, default entity set) rather than
  extending it with a custom `entities=` dict to force `&quot;` output,
  since the Spec text is unambiguous about which stdlib call to use and
  adding entities would be an unstated addition. Test assertion adjusted
  to check `&gt;`/`&lt;` presence + `ET.fromstring` non-injection instead
  of `&quot;`. Flagging per Prime Directive 3 rather than silently
  matching the acceptance-text example.
- Import placed in the top-of-file import block (alphabetically among the
  stdlib imports) rather than "near `_t()`" (line ~55) as the Spec section
  literally says — matches this file's existing import-organization
  convention (all stdlib/third-party imports grouped at top; `_t()` is a
  helper function, not an import site). Purely stylistic, no behavior
  difference; flagging since the packet was explicit about placement.

Out-of-scope findings filed: none — no adjacent issues found in the 4
touched functions or their immediate neighbors beyond what the packet
already scoped.
