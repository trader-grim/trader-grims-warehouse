# Packet: sku_dir()/sku_json()/location_dir() reject path-traversal input
Todo: #1274   PP: PP-COHESION-001   Track: SECURITY batch, foundational fix (run alone, not concurrent — #1273/#1275/#1284 all depend on this)

## Context budget (ALL the model may load)
This packet + `src/tgw/config.py` (lines ~258-276: `sku_dir()`,
`sku_json()`, `sku_exists()`, `location_dir()` only, plus its existing
test file if one exists) + the todo brief (`tgw todo brief 1274`).
Nothing else.

## Verified live before this packet was written
`find /opt/TGW/data/ItemCatalog/by-location -maxdepth 1 -mindepth 1` shows
every real location value in production is a simple alphanumeric code
(`SAT013`, `EA3035`, `unknown`, `sold`, `disposed`, `MUG01`, etc.) — no
spaces, no slashes, no nested paths. SKU format is documented and fixed:
`tgwYYYYMMDDHHMMSSmmm` (pure alphanumeric). A strict single-segment
allow-list validator will not break any real existing data.

## Spec
`sku_dir(cfg, sku)` returns `cfg["itemdata_root"] / sku` and
`location_dir(cfg, location)` returns `cfg["location_tree_root"] / location`
— raw `pathlib` joins with zero validation. Two distinct escape vectors:
1. **Absolute-path override**: `Path("/opt/TGW/data") / "/etc/passwd"`
   evaluates to `Path("/etc/passwd")` — pathlib's `/` operator discards
   the left side entirely when the right side is itself absolute.
2. **Traversal**: a `sku`/`location` value containing `../../..` walks
   out of the intended root even without being absolute.

Every ItemData writer in `items.py`/`resolver.py`/`catalog.py` calls
these helpers with no containment guarantee.

Fix — add a shared validator and use it in both functions:
```python
import re

_SAFE_SEGMENT_RE = re.compile(r'^[A-Za-z0-9_.-]+$')

def _safe_segment(root: Path, name: str, kind: str) -> Path:
    """Join *name* under *root* as a single path segment, raising
    ValueError if it isn't a safe, contained segment."""
    if not name or name in ('.', '..') or not _SAFE_SEGMENT_RE.match(name):
        raise ValueError(f"unsafe {kind} value: {name!r}")
    candidate = (root / name).resolve()
    root_resolved = root.resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise ValueError(f"{kind} {name!r} escapes {root}")
    return candidate


def sku_dir(cfg: Dict[str, Any], sku: str) -> Path:
    """Canonical directory for a SKU."""
    return _safe_segment(cfg["itemdata_root"], sku, "sku")


def location_dir(cfg: Dict[str, Any], location: str) -> Path:
    """Canonical location directory in the symlink tree."""
    return _safe_segment(cfg["location_tree_root"], location, "location")
```
`sku_json()` and `sku_exists()` call `sku_dir()` already — no change
needed there, they inherit the validation automatically.

**Do NOT loosen `_SAFE_SEGMENT_RE` to allow spaces or slashes** — verified
live that no real data needs it (see above). If a legitimate future value
needs a different charset, that's a new decision for Dave, not something
to silently widen here.

## Dataset
None — this only rejects malformed/malicious input earlier; legitimate
SKU/location values are unaffected (verified against real production
data above).

## Out of scope
- `#1273`, `#1275`, `#1284` — do NOT touch `http_server.py`, `catalog.py`,
  or `sku_migration.py` in this packet. Those are separate, deliberately
  held pending this fix landing — see the todo tracker.
- Any other function in `config.py`.
- Do not change `cfg["itemdata_root"]`/`cfg["location_tree_root"]`
  themselves or how they're configured.

## Acceptance (live)
1. Call `sku_dir(cfg, "tgw20260713120000000")` (a normal, valid SKU
   format) — must return the same path as before this fix, no exception.
2. Call `sku_dir(cfg, "../../../etc/passwd")` — must raise `ValueError`,
   never return a path outside `itemdata_root`.
3. Call `sku_dir(cfg, "/etc/passwd")` (absolute-path override attempt) —
   must raise `ValueError`, never silently return `/etc/passwd`.
4. Call `location_dir(cfg, "SAT013")` (a real, verified-live location
   value) — must return the same path as before, no exception.
5. Call `location_dir(cfg, "../outside")` and `location_dir(cfg, "/tmp/x")`
   — both must raise `ValueError`.
6. Run the full offline suite — confirm zero regressions against any
   existing caller that passes a real SKU or location value (if any
   existing test passes a value that would now be rejected, that is
   itself a finding to report, not something to silently work around by
   loosening the regex).

## Quota/risk
None — no new API calls. This is the root-cause fix for a genuine path-
traversal vulnerability class; treat any acceptance failure as a stop
condition, not something to route around.
