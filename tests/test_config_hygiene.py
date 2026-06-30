"""Tests for ISS-003 + ISS-004 config hygiene fixes.

ISS-003: full_catalog_path code default must match JSON value (master-catalog.json).
ISS-004: ebay_sku_migrate block must be surfaced in the normalised config dict,
         not require callers to reach into cfg['raw'].
"""

from __future__ import annotations

import json
from pathlib import Path

from tgw.config import load_config


def _write_cfg(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "tgw-api-config.json"
    p.write_text(json.dumps(data))
    return p


# ---------------------------------------------------------------------------
# ISS-003 — full_catalog_path default
# ---------------------------------------------------------------------------


def test_full_catalog_path_default_matches_json_canonical(tmp_path):
    """When full_catalog_path is absent from JSON, default must be master-catalog.json."""
    cfg_path = _write_cfg(tmp_path, {"catalog_root": str(tmp_path)})
    cfg = load_config(cfg_path)
    assert cfg["full_catalog_path"] == tmp_path / "master-catalog.json"


def test_full_catalog_path_explicit_override(tmp_path):
    """An explicit full_catalog_path in JSON must still be honoured."""
    override = str(tmp_path / "custom-catalog.json")
    cfg_path = _write_cfg(tmp_path, {"full_catalog_path": override})
    cfg = load_config(cfg_path)
    assert cfg["full_catalog_path"] == Path(override)


# ---------------------------------------------------------------------------
# ISS-004 — ebay_sku_migrate in normalised config
# ---------------------------------------------------------------------------


def test_ebay_sku_migrate_present_in_normalised_config(tmp_path):
    """ebay_sku_migrate must be a top-level key in the normalised config dict."""
    cfg_path = _write_cfg(tmp_path, {})
    cfg = load_config(cfg_path)
    assert "ebay_sku_migrate" in cfg


def test_ebay_sku_migrate_defaults_to_empty_dict(tmp_path):
    """When ebay_sku_migrate is absent from JSON, the normalised value is {}."""
    cfg_path = _write_cfg(tmp_path, {})
    cfg = load_config(cfg_path)
    assert cfg["ebay_sku_migrate"] == {}


def test_ebay_sku_migrate_block_surfaced_without_raw(tmp_path):
    """ebay_sku_migrate values are accessible via cfg key, not cfg['raw']."""
    migrate_block = {"enabled": True, "batch_size": 10}
    cfg_path = _write_cfg(tmp_path, {"ebay_sku_migrate": migrate_block})
    cfg = load_config(cfg_path)
    assert cfg["ebay_sku_migrate"] == migrate_block
    assert cfg["ebay_sku_migrate"]["enabled"] is True
    assert cfg["ebay_sku_migrate"]["batch_size"] == 10
