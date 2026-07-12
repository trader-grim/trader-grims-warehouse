# DONE — todo #1167 (audit#1143)

`src/tgw/workers/ai_identify.py`'s `handle()` did `item.pop("ai_reidentify",
None)` at line 288 to clear the force-reidentify flag after a successful
scan — but that only mutated the in-memory `item` dict. The actual
persistence happens via `fence_patch_item(self.config, sku, fence_fields)`,
where `fence_fields` is a curated allow-list dict (line ~373) that never
included `ai_reidentify` at all. The clear never reached disk: every
subsequent `ai_identify` run for that SKU still saw `ai_reidentify=True`
on disk and re-triggered a billed vision-AI call, forever.

## Fix
`http_server.py`'s `_apply_patch()` deletes a field from the document when
its patched value is `None` (confirmed in its own docstring: "Fields with
value None are deleted from the document"). Added
`fence_fields["ai_reidentify"] = None` — guarded by the already-computed
`force_reidentify` local (only sent when there was actually something to
clear, matching the file's existing style of conditional fence-field
inclusion).

## Tests
New `tests/test_ai_identify_reidentify_flag.py` (this file had zero prior
test coverage — a large worker with many external dependencies, all mocked
here: LLM call, product lookup, taxonomy category lookup, image hashing,
job enqueue):
- a force-reidentify run persists `ai_reidentify: None` in the
  `fence_patch_item` call (the regression case for #1167)
- a normal (non-reidentify) run does NOT send a no-op `ai_reidentify` key

`pytest -q tests/test_ai_identify_reidentify_flag.py`: 2/2 pass. Full suite:
1974 passed, 1 skipped, 2 failed (both pre-existing/unrelated in
`test_invariants_pricing.py`).

## Live verification (read-only, no billed AI calls made)
Scanned real `/opt/TGW/data/ItemData` for the bug's exact footprint
(`ai_reidentify=true` AND `ai_identified=true` still persisted together):
found **4 real affected items** — `tgw202605051936445`, `tgw202605052242107`,
`tgw202605060201087`, `tgw202606021107459`. Each would have re-triggered a
billed vision-AI call on its next `ai_identify` run, confirming this is a
live, real-world bug, not just a theoretical one. These 4 pre-date this
fix, so they're not automatically corrected by it (the fix only prevents
*new* occurrences).

Filed **todo #1257** for the follow-up: clearing the stale flag on these 4
items is a no-cost metadata deletion (no re-identify needed), but I did not
do it as part of this fix — mutating existing production item data beyond
the code fix itself is a separate decision that should get an explicit nod
rather than being folded silently into a bug-fix commit (feedback:
fix-the-tool-not-the-list — the tool fix is the deliverable here; data
repair rides behind, separately reviewed). Also noted as a secondary
observation in that todo: 3 of the 4 affected items show 3
`ai_identify` rounds in `identification_history` but only 1 entry in
`vision_results` — an inconsistency worth checking, but unrelated to this
bug and left for that follow-up.

No deviations from the todo brief. No config/secrets/OAuth scopes touched;
no billed AI/vision calls made during this fix or its verification.
