# Result: 1274 config-path-safety-validation
Status: done
Todo: #1274   PP: PP-COHESION-001

Files touched:
- src/tgw/config.py (added `_safe_segment()` shared validator; `sku_dir()`
  and `location_dir()` now route through it instead of raw `pathlib` joins)
- tests/test_config_path_safety.py (new — dedicated regression tests for
  this fix)
- docs/TGW-Plan-Vault/inbox/INPROGRESS-1274-config-path-safety-validation.md
  (breadcrumb, to be processed/deleted by the stitch step)

Live evidence:
- Confirmed `tgw.config.__file__` resolved under this worktree's
  `src/tgw/config.py` (not the shared checkout) before running any
  acceptance check, via `PYTHONPATH=.../1274-config-path-safety-validation/src`.
- Verified live against real production data at `/opt/TGW/data/ItemData`
  and `/opt/TGW/data/ItemCatalog/by-location` (read-only, no writes):
  - `sku_dir(cfg, "tgw20260713120000000")` → `/opt/TGW/data/ItemData/tgw20260713120000000` (unchanged, no exception)
  - `sku_dir(cfg, "../../../etc/passwd")` → `ValueError: unsafe sku value: '../../../etc/passwd'`
  - `sku_dir(cfg, "/etc/passwd")` → `ValueError: unsafe sku value: '/etc/passwd'`
  - `location_dir(cfg, "SAT013")` → `/opt/TGW/data/ItemCatalog/by-location/SAT013` (unchanged, no exception)
  - `location_dir(cfg, "../outside")` → `ValueError: unsafe location value: '../outside'`
  - `location_dir(cfg, "/tmp/x")` → `ValueError: unsafe location value: '/tmp/x'`
  - Round-tripped the first 5 real SKU dirs under `/opt/TGW/data/ItemData`
    and first 10 real location dirs under
    `/opt/TGW/data/ItemCatalog/by-location` through `sku_dir()`/`location_dir()`
    — all resolved to the identical real paths, zero rejections (confirms
    the allow-list regex does not break any real existing data, per the
    packet's live-verification claim).
- Full offline suite, `PYTHONPATH` pinned to this worktree:
  `2055 passed, 1 skipped` (2046 pre-existing + 9 new tests in
  `tests/test_config_path_safety.py`) — zero regressions against any
  existing caller.

Deviations from spec: none — implementation matches the packet's
`_safe_segment()` code block verbatim; `sku_json()`/`sku_exists()` left
untouched (they inherit validation via `sku_dir()`, as the packet states).

Out-of-scope findings filed: none — no new issues surfaced. Confirmed the
existing test files that reference `sku_dir`/`location_dir`
(`test_audit1143_workers_cohesion.py`) only pass well-formed SKU values and
were unaffected.
