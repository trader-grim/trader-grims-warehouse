Status: cleared
Reviewer: Claude (runner-review)
Todo: #1309   PP: PP-COHESION-001
Checked: diff (`git diff 714de85 todo/1309-ready-fence-bypass`) against the
todo brief's stated bug (ready_pool() inline path construction + raw JSON
read bypassing the fence), scope (ready.py + new test only), result
manifest completeness. Verified `from tgw.items import load_item_doc`
correctly resolves (items.py:24 re-exports it from resolver.py — not a
broken import as it first appeared); confirmed `find_item_jsons` +
`load_item_doc` is the exact same pattern used correctly in catalog.py.
Summary: minimal fence-compliance fix — raw `root.iterdir()` + hand-built
path + `json.loads()` replaced with `find_item_jsons(cfg)` +
`load_item_doc(json_path)`. Beneficial side effect correctly surfaced, not
suppressed: pool now reports an item's canonical `sku` field (rename-aware)
instead of always trusting the directory name. New test demonstrates
exactly that case. Full suite green modulo the known #1370 flake. No
triggers fired. Cleared for stitch.
