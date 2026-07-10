# DONE — todo #1213: photo_repair_iss013.py ITEMDATA_ROOT hardcoded

`ITEMDATA_ROOT` was a hardcoded `Path('/opt/TGW/data/ItemData')` literal
instead of reading `itemdata_root` from `tgw.config`, unlike sibling
`photosync_canary_probe.py`. Now derived from `load_config(DEFAULT_CONFIG)`
at import time; existing tests already monkeypatch `pr.ITEMDATA_ROOT`
directly so needed no changes.

## Live evidence

- Direct import confirms live resolution: `ITEMDATA_ROOT =
  /opt/TGW/data/ItemData` (same real value as before — proves the fix
  didn't regress the live path, just stopped hardcoding it).
- New regression test `test_itemdata_root_follows_config_value` — reloads
  the module with a monkeypatched `load_config` returning a distinct
  `itemdata_root`, confirms `ITEMDATA_ROOT` picks up the new value. This
  directly proves the config wiring, not just that today's value matches.
- `pytest -q tests/test_photo_repair_iss013.py` — 5 passed (was 4).
- `pytest -q` (full suite) — 2044 passed (was 2043), same 2 pre-existing
  unrelated failures in `test_invariants_pricing.py`.
- `ruff check` — clean.
