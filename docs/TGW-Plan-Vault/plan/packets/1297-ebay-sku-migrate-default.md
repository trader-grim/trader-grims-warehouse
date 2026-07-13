# Packet: ebay_sku_migrate worker's code default matches its documented safe default
Todo: #1297   PP: PP-COHESION-001   Track: concurrent batch 1 of 3 (PP-HERMES-EA-001)

## Context budget (ALL the model may load)
This packet + `src/tgw/workers/ebay_sku_migrate.py` (the module docstring
lines ~20-30 and the `run()` method's enabled-check around line ~783
only) + the todo brief (`tgw todo brief 1297`). Nothing else.

## Verified live before this packet was written
`/opt/TGW/config/tgw-api-config.json` currently has
`"ebay_sku_migrate": {"enabled": true, ...}` — the key is EXPLICITLY set,
not absent. This fix does NOT change current live behavior (the explicit
`true` wins either way); it only closes a latent trap for any future
config that omits the key (fresh install, key accidentally removed,
etc.), where the code's current wrong default would silently enable a
migration worker the documentation promises stays off. **Confirm this is
still true live before making the change** — re-check the config file
yourself; do not just trust this note if time has passed.

## Spec
Module docstring (lines ~24, ~27) documents:
```
Worker is disabled by default — set ebay_sku_migrate.enabled=true to start.
...
  ebay_sku_migrate.enabled        — false by default; set true to activate
```
But the actual guard at line ~783:
```python
if not migrate_cfg.get('enabled', True):
```
uses `True` as the default when the `enabled` key is absent — the
opposite of documented. Fix: change the default to `False`:
```python
if not migrate_cfg.get('enabled', False):
```
No other change. Do not touch the docstring (it already says the correct
thing) or any other logic in this file.

## Dataset
None — this is a worker-startup guard, not a data write.

## Out of scope
- Any other config key or logic in `ebay_sku_migrate.py`.
- The live `tgw-api-config.json` file — do NOT edit it; it already has
  the key explicitly set and doesn't need to change. This packet fixes
  only the code's fallback default for when the key is absent.
- The worker's systemd unit / enablement status — unrelated to this bug.

## Acceptance (live)
1. Re-verify (live, right before making the change) that
   `/opt/TGW/config/tgw-api-config.json`'s `ebay_sku_migrate.enabled` key
   is still explicitly set (either `true` or `false` — just not absent).
   If it's now absent, STOP and flag this as blocked — the live-behavior
   assumption this packet was written under no longer holds and needs a
   fresh decision, not a silent code change.
2. Construct `migrate_cfg = {}` (key absent) and confirm
   `migrate_cfg.get('enabled', False)` (the new default) evaluates falsy
   — worker would exit early, matching the documented safe default.
3. Construct `migrate_cfg = {'enabled': True}` and confirm the guard still
   correctly allows the worker to proceed — explicit `true` unaffected.
4. Confirm the live config's actual current value (`true`) still produces
   the same runtime behavior as before this fix (no regression to
   current, intentional operation).

## Quota/risk
None. Explicitly NOT an authorization to start/stop this worker — that
stays whatever it currently is; this only fixes what happens if the
config key is someday absent.
