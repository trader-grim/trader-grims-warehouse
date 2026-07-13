# In progress: #1277 trading-api-xml-escape

Working in isolated worktree `/opt/TGW/var/worktrees/1277-trading-api-xml-escape`
on branch `todo/1277-trading-api-xml-escape`, base `catio-nix-0.0.1-alpha`.

Task: add `xml.sax.saxutils.escape` wrapping of caller-supplied string values
in 4 Trading API XML-body builders in `src/tgw/apis/ebay/trading.py`
(`end_item`, `revise_item_sku`, `revise_item_pictures`,
`respond_to_best_offer`) per packet
`docs/TGW-Plan-Vault/plan/packets/1277-trading-api-xml-escape.md`. Pure
string-escaping fix, no call-site/validation changes. Runs alongside
sibling task #1276 (html-escape, separate worktree) — packet says stitch
only once both pass clean, but this branch is executed independently.
