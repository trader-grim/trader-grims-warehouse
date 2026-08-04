# INPROGRESS: todo #1276 — ebay/description.py HTML-escape ai_desc

Working in isolated worktree
`/opt/TGW/var/worktrees/1276-ebay-description-html-escape` on branch
`todo/1276-ebay-description-html-escape` (base: `catio-nix-0.0.1-alpha`).

Task: `build_listing_description()` in `src/tgw/ebay/description.py`
embeds `ai_desc` (LLM-generated / product-lookup text) unescaped into
listing HTML. Fix: `html.escape()` around `ai_desc` only, per packet
`docs/TGW-Plan-Vault/plan/packets/1276-ebay-description-html-escape.md`.
`bp_html` (operator config) and `pl` (picklist line — separate,
out-of-scope finding) stay untouched. Adding an offline test file
(none existed previously). Will file a new todo for `picklist_line()`'s
unescaped `title` interpolation as an out-of-scope finding, then write
the result manifest and stop.
