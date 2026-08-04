Status: cleared
Reviewer: Claude (runner-review)
Todo: #1304   PP: PP-COHESION-001
Checked: diff (`git diff 714de85 todo/1304-multi-intake-collision-finding`)
against the todo brief's stated bug, result manifest completeness. Touched
3 files beyond the test (multi_intake.py, api.py, invariants.md) — reviewed
as in-scope, not a trigger: the packet explicitly asked for "a catalog-verify
detector rule mirroring legacy_listing_unrepaired," which necessarily
requires api.py's _verify_item; the invariants.md addition is a "second
instance" note appended to the existing C11 entry, matching this project's
own established documentation practice for this exact invariant (the
existing entry documents ebay_stage.py's implementation the same way).
Verified the target of `sku` in the fence_patch_item call is the EXISTING
colliding item (not the derived-but-uncreated child) — confirmed by reading
the surrounding context (`existing_json = itemdata_root/sku/sku.json`,
`if existing_json.exists()`) — so the finding lands on the correct item:
the one whose data may have silently absorbed a later intake batch's
photos via newitems_dir, which is exactly what an operator needs to verify.
Summary: additive `sku_collision_blocked` field (colliding_sku, base_sku,
detected_at) persisted via fence_patch_item alongside the existing
log_event/notify (not replacing them), wrapped in try/except so a
persistence failure can't abort the intake split. New `sku_collision_unrepaired`
catalog-verify rule mirrors `legacy_listing_unrepaired` exactly. Test
updated to assert original fields untouched, the new field's shape, and
that catalog-verify's _verify_item() actually flags it end-to-end. Full
suite green modulo the known #1370 flake. No triggers fired. Cleared for
stitch.
