"""Invariant A4: no new inline SKU path construction outside the platform layer.

A4 states that nothing outside config.py / items.py should construct
ItemData paths directly (itemdata_root / sku / f'{sku}.json').
Use config.sku_json(cfg, sku) instead.

This test is a "don't make it worse" gate: the files in _A4_ALLOWLIST
pre-date the invariant and are grandfathered. The test fails if any NEW
file starts building the pattern inline.

To fix a violation: replace `cfg['itemdata_root'] / sku / f'{sku}.json'`
with `config.sku_json(cfg, sku)` and remove the file from the allowlist.
"""
import re
from pathlib import Path

_SRC = Path(__file__).parent.parent / "src" / "tgw"

# Grandfathered violators — DO NOT add new entries; fix the code instead.
_A4_ALLOWLIST = frozenset({
    "api.py",
    "http_server.py",
    "mcp_server.py",
    "ebay/pull.py",
    "workers/ebay_draft.py",
    "workers/ebay_price.py",
    "workers/ebay_publish.py",
    "workers/ebay_stage.py",
    "workers/ebay_sync.py",
    "workers/ebay_sku_migrate.py",
    "workers/ebay_upload.py",
    "workers/itemdata_scrub.py",
})

# Matches: itemdata_root ... / <var> / f'...'  (inline SKU path construction)
_PATTERN = re.compile(r"itemdata_root\b[^\n]*/\s*\w+\s*/\s*f['\"]")


def test_a4_no_new_inline_sku_paths():
    """Fail if any file outside the allowlist builds itemdata_root paths inline."""
    violators = set()
    for py_file in _SRC.rglob("*.py"):
        rel = str(py_file.relative_to(_SRC))
        if rel in ("config.py", "items.py"):
            continue
        if _PATTERN.search(py_file.read_text()):
            violators.add(rel)

    new_violations = violators - _A4_ALLOWLIST
    assert not new_violations, (
        "A4 invariant: new files building itemdata_root paths inline "
        "(use config.sku_json(cfg, sku) instead): "
        + str(sorted(new_violations))
    )
