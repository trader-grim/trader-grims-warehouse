# Result: 1301 resolver-silent-except
Status: done
Todo: #1301   PP: PP-COHESION-001
Files touched:
- src/tgw/resolver.py
- tests/test_resolver.py
- docs/TGW-Plan-Vault/inbox/INPROGRESS-1301-resolver-silent-except.md (breadcrumb, worktree-local)

Live evidence:
- `resolve()`'s JSON-loading selector loop (status/ebay_item_id/upc/search/empty_field,
  `src/tgw/resolver.py` lines ~261-269) previously had:
  ```python
  try:
      doc = load_item_doc_by_sku(cfg, sku)
  except Exception:
      continue
  ```
  Now:
  ```python
  try:
      doc = load_item_doc_by_sku(cfg, sku)
  except Exception as exc:
      log.warning(
          'resolve(): skipping sku %s — failed to load item JSON: %s',
          sku, exc,
      )
      continue
  ```
  using `log = logging.getLogger(__name__)` added at module top (matches the
  existing project-wide pattern seen in health.py/promo.py/scrub.py/todo.py/
  velocity.py/clipd.py — grepped first to confirm before adopting).
- Added `tests/test_resolver.py::test_resolve_corrupt_item_json_skipped_but_logged`:
  writes a valid item + a second item with deliberately corrupt JSON
  (`{not valid json`), calls `resolve(cfg, status='ACTIVE')`, and asserts
  (a) the valid SKU is still returned (no crash, no total wipeout), and
  (b) a WARNING-level log record naming the corrupt SKU was emitted
  (via `caplog.at_level('WARNING', logger='tgw.resolver')`).
- Full offline suite, PYTHONPATH pinned to this worktree's `src/`
  (confirmed `tgw.resolver.__file__` resolves under the worktree path
  before running):
  `PYTHONPATH=/opt/TGW/var/worktrees/1301-resolver-silent-except/src:$PYTHONPATH python -m pytest -q`
  → `2138 passed, 1 skipped, 1 warning in 52.74s` (the 1 skip and the
  fastapi/httpx deprecation warning are pre-existing, unrelated to this change).

Deviations from spec: none. Kept the `except Exception` broad (not narrowed
to specific exception types) — the packet said narrowing was a nice-to-have
only "if the existing code structure allows a narrow substitution without
behavior change," and the core ask ("the skip must leave a trace") didn't
require it; narrowing risks missing exception types this loop currently
protects against (e.g. arbitrary `KeyError`/`TypeError` from malformed but
JSON-valid docs) for no functional gain, so left as-is per "keep the change
minimal and targeted."

Out-of-scope findings filed: none — no new adjacent issues surfaced during
this fix; `_build_sku_old_index()` and `_location_skus_from_itemdata()` have
similar bare `except Exception: pass` patterns nearby (lines ~93-94, ~174-175)
but are outside this packet's named function/loop and out of scope per
"don't over-engineer... not a redesign of the selector search path" — noting
here for visibility rather than filing a new todo, since it's the same
audit batch (PP-COHESION-001) and likely already covered by an adjacent
todo in that batch; left to the PP owner to confirm/dedupe rather than risk
a duplicate.
