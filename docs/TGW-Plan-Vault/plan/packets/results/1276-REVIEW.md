# Review: 1276 ebay-description-html-escape
Status: cleared — run 1 of 2 for the resumed SECURITY sequence (paired
with #1277). 2-in-a-row clean — stitched together with #1277.
Reviewer: Claude (main session, tgw-runner-review)

Checked: Spec — `build_listing_description()` diff is exactly the one
line the packet specified (`html.escape()` wraps `ai_desc` only; `bp_html`
and `pl` untouched). Out-of-scope — only `description.py` +
its new test file touched; the adjacent `picklist_line()` title-escaping
gap was correctly NOT fixed inline, filed as its own todo (#1367)
instead. Invariants — n/a (pure output-escaping change, no ItemData
write path, no fence bypass). Live evidence — re-verified independently
in this review (not just trusting the executor's report): confirmed
`tgw.ebay.description.__file__` resolves under the worktree path with
`PYTHONPATH` set, new test file 4/4 passed, full offline suite 2115
passed/1 skipped/0 failed — matches the executor's reported numbers
exactly. No deviations from spec, no out-of-control triggers fired.

Stitched.
