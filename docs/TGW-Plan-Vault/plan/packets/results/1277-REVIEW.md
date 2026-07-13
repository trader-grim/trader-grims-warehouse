# Review: 1277 trading-api-xml-escape
Status: cleared — run 2 of 2 for the resumed SECURITY sequence (paired
with #1276). 2-in-a-row clean, cadence rule satisfied — stitching both
now, sequence graduates to concurrent execution for #1278/#1279/#1281/#1283.
Reviewer: Claude (main session, tgw-runner-review)

Checked: Spec — all 4 named functions (`end_item`, `revise_item_sku`,
`revise_item_pictures`, `respond_to_best_offer`) wrap exactly the
caller-supplied string values the packet named with `_xml_escape(...)`;
no other function in the file touched; `counter_price` (already-safe
float) correctly left alone. Out-of-scope — only `trading.py` + its new
test file touched; `offers.py` and every other caller untouched.
Invariants — n/a (outbound XML body construction only, no ItemData write
path, no fence bypass). Live evidence — re-verified independently: new
test file's 5 cases (4 injection-attempt + 1 no-regression, all 4
functions covered) use real `ET.fromstring` structural parsing to confirm
no injected sibling element survives, not just substring checks — a
stronger acceptance bar than the packet's own literal examples asked for.
Confirmed `tgw.apis.ebay.trading.__file__` resolves under the worktree
path, full offline suite 2116 passed/1 skipped/0 failed — matches
executor's reported numbers.

Deviation reviewed and accepted: packet's acceptance text expected
`&quot;` in escaped output; `xml.sax.saxutils.escape()`'s default entity
set only covers `&`/`<`/`>`, which is correct and sufficient since every
insertion point here is XML element text, not an attribute value — quote
escaping is irrelevant there. Adding a custom `entities=` map to force
`&quot;` output would have been an unrequested addition beyond the
packet's literal Spec text. Test assertions were correctly adjusted to
check `&lt;`/`&gt;` plus structural non-injection instead. No out-of-
control triggers fired.

Stitching #1276 + #1277 together now.
