# Packet: collision_report() actually consumes check_collisions()'s real return shape
Todo: #1294   PP: PP-COHESION-001   Track: framework batch (PP-HERMES-EA-001)

## Context budget (ALL the model may load)
This packet + `src/tgw/sku_migration.py` (`check_collisions()` and
`collision_report()` only, plus the existing test file if one exists) +
the todo brief (`tgw todo brief 1294`). Nothing else.

## Spec
`check_collisions(cfg)` (line ~269) returns a **dict**:
`{ok, raw_a_collisions, auto_resolved, unresolvable, safe_to_migrate,
resolved_pairs, unresolvable_detail}`.

`collision_report(cfg)` (line ~564) currently does
`collisions = check_collisions(cfg)` then `for c in collisions: t =
c['conflict_type']` — iterating a dict yields its string keys, so `c` is
a string like `'ok'`, and `c['conflict_type']` always raises `TypeError`.
There are ZERO callers of `collision_report()` anywhere in `src/` or
`tests/` (verified by grep before writing this packet) — it has never run
successfully.

Fix: rewrite `collision_report()` to consume `check_collisions()`'s
actual dict shape, preserving the same OUTPUT keys `collision_report()`
already promises (`ok`, `total`, `by_type`, `collisions`,
`safe_to_migrate`) so any future caller sees the same contract, populated
correctly:

```python
def collision_report(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Run collision check and return structured report."""
    collisions = check_collisions(cfg)
    return {
        'ok': collisions['ok'],
        'total': collisions['raw_a_collisions'],
        'by_type': {
            'auto_resolved': collisions['auto_resolved'],
            'unresolvable': collisions['unresolvable'],
        },
        'collisions': collisions['resolved_pairs'][:50],
        'safe_to_migrate': collisions['safe_to_migrate'],
    }
```

Do not change `check_collisions()` itself — it is correct and has its own
(working) callers elsewhere; only `collision_report()` is broken.

## Dataset
None — this is a read-only report/summary function over already-computed
collision data, not a data write.

## Out of scope
- `check_collisions()` itself, `build_migration_map()`, or any other
  function in `sku_migration.py`.
- Do not add a caller for `collision_report()` — it stays uncalled; fixing
  it so it WORKS if/when something does call it is the scope, not wiring
  it up to a CLI/API surface.
- Do not remove the function on the grounds that it's dead code — the
  audit finding asked for the bug fixed, not the function deleted.

## Acceptance (live)
1. Call `collision_report(cfg)` against real config/data (or a
   constructed `cfg` that exercises `iter_all_skus`/`build_migration_map`
   with at least one real A-to-A collision case if one exists, else an
   empty-collision case) — must return a dict with the 5 keys above and
   NOT raise `TypeError`.
2. Confirm `total` matches `check_collisions(cfg)['raw_a_collisions']`,
   and `by_type['auto_resolved'] + by_type['unresolvable'] == total`
   (arithmetic invariant already implied by `check_collisions()`'s own
   `auto_resolved = raw_a_collisions - unresolvable`).
3. Confirm `collisions` is a list of the actual pair dicts (each with
   `winner`/`loser`/`natural_target`/`resolved_target`), not strings.

## Quota/risk
None.
