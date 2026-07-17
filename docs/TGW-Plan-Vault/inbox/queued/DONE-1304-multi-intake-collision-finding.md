Todo #1304 (PP-COHESION-001, invariant C11) — DONE, stitched. multi_intake.py's
derived-child-SKU-collision guard now persists `sku_collision_blocked` on
the colliding item, additive alongside the existing log_event/notify; new
`sku_collision_unrepaired` catalog-verify rule (warning). Reviewed clean,
full suite green. api.py's _verify_item had a merge conflict against #1303's
same-region addition — resolved by keeping both detector blocks sequentially
(non-overlapping rules, purely additive).
