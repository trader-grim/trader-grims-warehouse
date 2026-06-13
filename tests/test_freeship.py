"""Tests for PP-FREESHIP-001 — free shipping pricing mode.

Covers:
  - freeship_price() rounding behaviour (nearest .99)
  - config loading of free_shipping_enabled / default_shipping_cost / fulfillment_policy_free_shipping
  - cmd_price_freeship() dry-run and --apply
  - ebay_price worker applies freeship when free_shipping_enabled
  - sync._resolve_fulfillment_id uses freeship policy when free_shipping=True
"""

from __future__ import annotations

import json

import pytest

import tgw.workers.ebay_price as ebay_price_mod
from tgw.config import load_config
from tgw.ebay.pricing import freeship_price

# ---------------------------------------------------------------------------
# freeship_price() — nearest .99 rounding
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("item_price,shipping_cost,expected", [
    # Exactly at a .99 point already
    (12.99, 5.00, 17.99),
    # Rounds up: 12.99 + 5.51 = 18.50 → tipping point is 18.49 so goes to 18.99
    (12.99, 5.51, 18.99),
    # Rounds down: 12.99 + 5.49 = 18.48 → 17.99
    (12.99, 5.49, 17.99),
    # Exact midpoint: 12.99 + 5.50 = 18.49; combined > 18.49 is False → lower = 17.99
    (12.99, 5.50, 17.99),
    # Zero shipping — price unchanged except rounding
    (9.99,  0.00, 9.99),
    (10.00, 0.00, 10.99),  # 9.99 would be below item_price=10.00 → snaps up to 10.99
    # Small values floored at 0.99
    (0.10, 0.50, 0.99),
])
def test_freeship_price_nearest_99(item_price, shipping_cost, expected):
    assert freeship_price(item_price, shipping_cost) == expected


def test_freeship_never_below_99():
    assert freeship_price(0.01, 0.01) == 0.99


def test_freeship_large_shipping_cost():
    # $5.00 item + $15.00 shipping → $20.00 → base=20, mid=20.49, 20.00 > 20.49 F → 19.99
    assert freeship_price(5.00, 15.00) == 19.99


def test_freeship_zero_base_price():
    # 0 + 5.50 = 5.50 → base=5, mid=5.49, 5.50 > 5.49 → upper=5.99
    assert freeship_price(0.0, 5.50) == 5.99


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def test_config_freeship_defaults(tmp_path):
    cfg_path = tmp_path / "tgw-api-config.json"
    cfg_path.write_text("{}", encoding="utf-8")
    cfg = load_config(cfg_path)
    assert cfg["free_shipping_enabled"] is False
    assert cfg["default_shipping_cost"] == 0.0
    assert cfg["fulfillment_policy_free_shipping"] is None


def test_config_freeship_from_json(tmp_path):
    cfg_path = tmp_path / "tgw-api-config.json"
    cfg_path.write_text(json.dumps({
        "free_shipping_enabled": True,
        "default_shipping_cost": 6.95,
        "fulfillment_policy_free_shipping": "POLICY-FREE-123",
    }), encoding="utf-8")
    cfg = load_config(cfg_path)
    assert cfg["free_shipping_enabled"] is True
    assert cfg["default_shipping_cost"] == 6.95
    assert cfg["fulfillment_policy_free_shipping"] == "POLICY-FREE-123"


# ---------------------------------------------------------------------------
# cmd_price_freeship — dry-run and --apply
# ---------------------------------------------------------------------------

@pytest.fixture
def item_dir(tmp_path):
    sku = "tgw20260612120000001"
    d = tmp_path / sku
    d.mkdir()
    item = {
        "title": "Test Gadget",
        "condition": "good",
        "ebay_offer": {"price": 14.99},
        "draft_listing": {"price": 14.99, "title": "Test Gadget"},
    }
    (d / f"{sku}.json").write_text(json.dumps(item), encoding="utf-8")
    return tmp_path, sku


def _cfg(tmp_path):
    return {
        "itemdata_root": tmp_path,
        "pretty": False,
        "free_shipping_enabled": False,
        "default_shipping_cost": 0.0,
        "fulfillment_policy_free_shipping": None,
    }


def test_price_freeship_dry_run(item_dir):
    from tgw.api import cmd_price_freeship
    tmp_path, sku = item_dir
    cfg = _cfg(tmp_path)
    result = cmd_price_freeship(cfg, sku, shipping_cost=5.00, apply=False)
    assert result["ok"] is True
    assert result["base_price"] == 14.99
    assert result["shipping_cost"] == 5.00
    assert result["freeship_price"] == freeship_price(14.99, 5.00)
    assert result["applied"] is False
    # Item JSON must not have changed
    item = json.loads((tmp_path / sku / f"{sku}.json").read_text())
    assert item["ebay_offer"]["price"] == 14.99
    assert "free_shipping" not in item


def test_price_freeship_apply(item_dir):
    from tgw.api import cmd_price_freeship
    tmp_path, sku = item_dir
    cfg = _cfg(tmp_path)
    result = cmd_price_freeship(cfg, sku, shipping_cost=5.00, apply=True)
    assert result["ok"] is True
    assert result["applied"] is True
    item = json.loads((tmp_path / sku / f"{sku}.json").read_text())
    expected = freeship_price(14.99, 5.00)
    assert item["ebay_offer"]["price"] == expected
    assert item["draft_listing"]["price"] == expected
    assert item["free_shipping"] is True
    assert "freeship_applied_at" in item["ebay_offer"]


def test_price_freeship_uses_item_shipping_cost(item_dir):
    from tgw.api import cmd_price_freeship
    tmp_path, sku = item_dir
    # Write shipping_cost into item JSON
    p = tmp_path / sku / f"{sku}.json"
    item = json.loads(p.read_text())
    item["shipping_cost"] = 7.50
    p.write_text(json.dumps(item))
    cfg = _cfg(tmp_path)
    result = cmd_price_freeship(cfg, sku)   # no --shipping-cost arg
    assert result["shipping_cost"] == 7.50
    assert result["shipping_cost_source"] == "item"


def test_price_freeship_uses_config_default(item_dir):
    from tgw.api import cmd_price_freeship
    tmp_path, sku = item_dir
    cfg = _cfg(tmp_path)
    cfg["default_shipping_cost"] = 4.99
    result = cmd_price_freeship(cfg, sku)
    assert result["shipping_cost"] == 4.99
    assert result["shipping_cost_source"] == "config_default"


def test_price_freeship_arg_overrides_item_cost(item_dir):
    from tgw.api import cmd_price_freeship
    tmp_path, sku = item_dir
    p = tmp_path / sku / f"{sku}.json"
    item = json.loads(p.read_text())
    item["shipping_cost"] = 7.50
    p.write_text(json.dumps(item))
    cfg = _cfg(tmp_path)
    result = cmd_price_freeship(cfg, sku, shipping_cost=3.00)
    assert result["shipping_cost"] == 3.00
    assert result["shipping_cost_source"] == "arg"


def test_price_freeship_missing_sku(tmp_path):
    from tgw.api import cmd_price_freeship
    cfg = _cfg(tmp_path)
    result = cmd_price_freeship(cfg, "tgw_nonexistent_sku")
    assert result["ok"] is False


def test_price_freeship_no_price(tmp_path):
    from tgw.api import cmd_price_freeship
    sku = "tgw20260612120000002"
    d = tmp_path / sku
    d.mkdir()
    item = {"title": "No Price Yet", "draft_listing": {}}
    (d / f"{sku}.json").write_text(json.dumps(item))
    cfg = _cfg(tmp_path)
    result = cmd_price_freeship(cfg, sku, shipping_cost=5.00)
    assert result["ok"] is False
    assert "no price" in result["error"]


# ---------------------------------------------------------------------------
# ebay_price worker — auto freeship when free_shipping_enabled
# ---------------------------------------------------------------------------


@pytest.fixture
def price_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(ebay_price_mod.tgw_logging, "log_event", lambda *a, **k: None)
    enqueued = []
    monkeypatch.setattr(ebay_price_mod.state_machine, "enqueue_job",
                        lambda **kw: enqueued.append(kw))
    import tgw.listing_quality as lq

    class _Q:
        def to_dict(self):
            return {"stub": True}

    monkeypatch.setattr(lq, "score_draft", lambda item: _Q())
    worker = object.__new__(ebay_price_mod.EbayPriceWorker)
    worker.config = {
        "itemdata_root": tmp_path,
        "pretty": False,
        "free_shipping_enabled": True,
        "default_shipping_cost": 6.00,
    }
    worker._enqueued = enqueued
    return worker


def _write_item_for_worker(tmp_path, sku):
    item = {
        "title": "Acme Thing",
        "condition": "good",
        "draft_listing": {"title": "Acme Thing", "category_id": "12345",
                          "category_name": "Widgets"},
    }
    d = tmp_path / sku
    d.mkdir(parents=True)
    (d / f"{sku}.json").write_text(json.dumps(item), encoding="utf-8")


def _run_worker(worker, tmp_path, sku):
    _write_item_for_worker(tmp_path, sku)
    worker.handle({"payload_json": {"sku": sku}})
    return json.loads((tmp_path / sku / f"{sku}.json").read_text(encoding="utf-8"))


def _suggestion(price, comps):
    return {"price": price, "source": "browse:full_title", "comps": comps,
            "price_confidence": "medium", "velocity_hint": None,
            "queried_at": "2026-06-12T00:00:00Z"}


def test_worker_freeship_price_applied(price_worker, tmp_path, monkeypatch):
    comps = {"count": 3, "min": 10.0, "p25": 14.99, "median": 15.0,
             "p75": 16.0, "max": 16.0}
    monkeypatch.setattr(ebay_price_mod, "suggest_price",
                        lambda *a, **k: _suggestion(14.99, comps))
    sku = "tgw_fs001"
    result = _run_worker(price_worker, tmp_path, sku)
    # launch = to_99(16.0 * 1.10) = to_99(17.60) = 17.99
    # freeship = freeship_price(17.99, 6.00) = freeship_price(17.99, 6.00)
    from tgw.ebay.pricing import to_99
    expected_launch = to_99(16.0 * 1.10)
    expected_freeship = freeship_price(expected_launch, 6.00)
    assert result["ebay_offer"]["price"] == expected_freeship
    assert result["free_shipping"] is True


def test_worker_freeship_uses_item_shipping_cost(price_worker, tmp_path, monkeypatch):
    """Item-level shipping_cost takes priority over config default."""
    comps = {"count": 3, "min": 10.0, "p25": 12.99, "median": 14.0,
             "p75": 15.0, "max": 15.0}
    monkeypatch.setattr(ebay_price_mod, "suggest_price",
                        lambda *a, **k: _suggestion(12.99, comps))
    sku = "tgw_fs002"
    _write_item_for_worker(tmp_path, sku)
    # Inject item-level shipping_cost
    p = tmp_path / sku / f"{sku}.json"
    item = json.loads(p.read_text())
    item["shipping_cost"] = 3.50
    p.write_text(json.dumps(item))
    price_worker.handle({"payload_json": {"sku": sku}})
    result = json.loads(p.read_text())
    from tgw.ebay.pricing import to_99
    expected_launch = to_99(15.0 * 1.10)
    expected_freeship = freeship_price(expected_launch, 3.50)
    assert result["ebay_offer"]["price"] == expected_freeship
    assert result["free_shipping"] is True


def test_worker_no_freeship_when_disabled(tmp_path, monkeypatch):
    """When free_shipping_enabled is False, worker does not apply freeship."""
    monkeypatch.setattr(ebay_price_mod.tgw_logging, "log_event", lambda *a, **k: None)
    enqueued = []
    monkeypatch.setattr(ebay_price_mod.state_machine, "enqueue_job",
                        lambda **kw: enqueued.append(kw))
    import tgw.listing_quality as lq

    class _Q:
        def to_dict(self):
            return {"stub": True}

    monkeypatch.setattr(lq, "score_draft", lambda item: _Q())
    worker = object.__new__(ebay_price_mod.EbayPriceWorker)
    worker.config = {
        "itemdata_root": tmp_path,
        "pretty": False,
        "free_shipping_enabled": False,
        "default_shipping_cost": 6.00,
    }
    comps = {"count": 3, "min": 10.0, "p25": 12.99, "median": 14.0,
             "p75": 15.0, "max": 15.0}
    monkeypatch.setattr(ebay_price_mod, "suggest_price",
                        lambda *a, **k: _suggestion(12.99, comps))
    sku = "tgw_fs003"
    result = _run_worker(worker, tmp_path, sku)
    assert "free_shipping" not in result


# ---------------------------------------------------------------------------
# sync._resolve_fulfillment_id — free_shipping policy priority
# ---------------------------------------------------------------------------

def test_resolve_uses_freeship_policy_when_set():
    from tgw.ebay.sync import _resolve_fulfillment_id
    cfg = {
        "fulfillment_policy_free_shipping": "FREESHIP-POLICY-ID",
        "fulfillment_policy_id": "NORMAL-POLICY-ID",
        "fulfillment_policy_by_category": {},
        "fulfillment_policy_by_size_class": {},
    }
    fid = _resolve_fulfillment_id(cfg, "12345", free_shipping=True)
    assert fid == "FREESHIP-POLICY-ID"


def test_resolve_falls_through_when_no_freeship_policy():
    from tgw.ebay.sync import _resolve_fulfillment_id
    cfg = {
        "fulfillment_policy_free_shipping": None,
        "fulfillment_policy_id": "NORMAL-POLICY-ID",
        "fulfillment_policy_by_category": {},
        "fulfillment_policy_by_size_class": {},
    }
    fid = _resolve_fulfillment_id(cfg, "12345", free_shipping=True)
    # Falls through to global default
    assert fid == "NORMAL-POLICY-ID"


def test_resolve_ignores_freeship_flag_when_false():
    from tgw.ebay.sync import _resolve_fulfillment_id
    cfg = {
        "fulfillment_policy_free_shipping": "FREESHIP-POLICY-ID",
        "fulfillment_policy_id": "NORMAL-POLICY-ID",
        "fulfillment_policy_by_category": {},
        "fulfillment_policy_by_size_class": {},
    }
    fid = _resolve_fulfillment_id(cfg, "12345", free_shipping=False)
    assert fid == "NORMAL-POLICY-ID"


def test_resolve_shipping_profile_beats_freeship():
    """Bug #7: per-item shipping_profile must override free_shipping flag."""
    from tgw.ebay.sync import _resolve_fulfillment_id
    cfg = {
        "fulfillment_policy_free_shipping": "FREESHIP-POLICY-ID",
        "fulfillment_policy_by_profile": {"bulky": "BULKY-POLICY-ID"},
        "fulfillment_policy_id": "NORMAL-POLICY-ID",
        "fulfillment_policy_by_category": {},
        "fulfillment_policy_by_size_class": {},
    }
    fid = _resolve_fulfillment_id(cfg, "12345", shipping_profile="bulky", free_shipping=True)
    assert fid == "BULKY-POLICY-ID"


def test_resolve_unmapped_shipping_profile_falls_through_to_freeship():
    """Bug (review 2): unmapped shipping_profile must fall through, not return raw string."""
    from tgw.ebay.sync import _resolve_fulfillment_id
    cfg = {
        "fulfillment_policy_free_shipping": "FREESHIP-POLICY-ID",
        "fulfillment_policy_by_profile": {},   # 'standard' not mapped
        "fulfillment_policy_id": "NORMAL-POLICY-ID",
        "fulfillment_policy_by_category": {},
        "fulfillment_policy_by_size_class": {},
    }
    fid = _resolve_fulfillment_id(cfg, "12345", shipping_profile="standard", free_shipping=True)
    # Must use freeship policy, NOT return the raw string 'standard' as an ID
    assert fid == "FREESHIP-POLICY-ID"
    assert fid != "standard"


# ---------------------------------------------------------------------------
# Bug fixes — regression tests
# ---------------------------------------------------------------------------

def test_freeship_price_floor_prevents_undercut():
    """Bug #1: freeship_price must never return below item_price."""
    assert freeship_price(5.00, 0.10) == 5.99   # was 4.99 before fix
    assert freeship_price(10.00, 0.00) == 10.99  # was 9.99 before fix
    assert freeship_price(3.00, 0.05) == 3.99    # 3.05 → nearest=2.99 < 3.00 → snap up


def test_price_freeship_apply_idempotency(item_dir):
    """Bug #4: --apply twice must fail on second call rather than stacking shipping."""
    from tgw.api import cmd_price_freeship
    tmp_path, sku = item_dir
    cfg = _cfg(tmp_path)
    first = cmd_price_freeship(cfg, sku, shipping_cost=5.00, apply=True)
    assert first["ok"] is True
    second = cmd_price_freeship(cfg, sku, shipping_cost=5.00, apply=True)
    assert second["ok"] is False
    assert "freeship_applied_at" in second["error"]
    # Price must not have changed from the first apply
    item = json.loads((tmp_path / sku / f"{sku}.json").read_text())
    assert item["ebay_offer"]["price"] == first["freeship_price"]


def test_worker_target_price_includes_shipping(price_worker, tmp_path, monkeypatch):
    """Bug #5: target_price must be freeship-adjusted so the repricer doesn't underprice."""
    comps = {"count": 3, "min": 10.0, "p25": 14.99, "median": 15.0,
             "p75": 16.0, "max": 16.0}
    monkeypatch.setattr(ebay_price_mod, "suggest_price",
                        lambda *a, **k: _suggestion(14.99, comps))
    sku = "tgw_fs_target"
    result = _run_worker(price_worker, tmp_path, sku)
    # target_price must be >= p25 (shipping absorbed), not the bare p25
    ship_cost = 6.00  # price_worker fixture has default_shipping_cost=6.00
    assert result["ebay_offer"]["target_price"] == freeship_price(14.99, ship_cost)
    assert result["ebay_offer"]["target_price"] > 14.99


def test_price_freeship_null_ebay_offer(tmp_path):
    """Bug #1: item JSON with 'ebay_offer': null must not crash (or {}'' is not None)."""
    from tgw.api import cmd_price_freeship
    sku = "tgw20260612120000003"
    d = tmp_path / sku
    d.mkdir()
    item = {
        "title": "Null Offer Item",
        "ebay_offer": None,  # JSON null — must not AttributeError
        "draft_listing": {"price": 12.99},
    }
    (d / f"{sku}.json").write_text(json.dumps(item), encoding="utf-8")
    cfg = _cfg(tmp_path)
    result = cmd_price_freeship(cfg, sku, shipping_cost=5.00)
    assert result["ok"] is True
    assert result["base_price"] == 12.99


def test_worker_empty_string_shipping_cost_falls_through(tmp_path, monkeypatch):
    """Bug (review 2): item.shipping_cost='' must fall through to config default, not crash."""
    monkeypatch.setattr(ebay_price_mod.tgw_logging, "log_event", lambda *a, **k: None)
    enqueued = []
    monkeypatch.setattr(ebay_price_mod.state_machine, "enqueue_job",
                        lambda **kw: enqueued.append(kw))
    import tgw.listing_quality as lq

    class _Q:
        def to_dict(self):
            return {"stub": True}

    monkeypatch.setattr(lq, "score_draft", lambda item: _Q())
    worker = object.__new__(ebay_price_mod.EbayPriceWorker)
    worker.config = {
        "itemdata_root": tmp_path,
        "pretty": False,
        "free_shipping_enabled": True,
        "default_shipping_cost": 5.00,
    }
    comps = {"count": 3, "min": 10.0, "p25": 12.99, "median": 14.0,
             "p75": 15.0, "max": 15.0}
    monkeypatch.setattr(ebay_price_mod, "suggest_price",
                        lambda *a, **k: _suggestion(12.99, comps))
    sku = "tgw_fs_emptystr"
    _write_item_for_worker(tmp_path, sku)
    p = tmp_path / sku / f"{sku}.json"
    item = json.loads(p.read_text())
    item["shipping_cost"] = ""   # empty string — must use config default, not crash
    p.write_text(json.dumps(item))
    worker.handle({"payload_json": {"sku": sku}})   # must not raise
    result = json.loads(p.read_text())
    # Empty string treated as missing → config default (5.00) used → freeship applied
    assert result.get("free_shipping") is True


def test_worker_zero_shipping_cost_not_overridden(tmp_path, monkeypatch):
    """Bug #2: item.shipping_cost=0 must not fall through to config default_shipping_cost."""
    monkeypatch.setattr(ebay_price_mod.tgw_logging, "log_event", lambda *a, **k: None)
    enqueued = []
    monkeypatch.setattr(ebay_price_mod.state_machine, "enqueue_job",
                        lambda **kw: enqueued.append(kw))
    import tgw.listing_quality as lq

    class _Q:
        def to_dict(self):
            return {"stub": True}

    monkeypatch.setattr(lq, "score_draft", lambda item: _Q())
    worker = object.__new__(ebay_price_mod.EbayPriceWorker)
    worker.config = {
        "itemdata_root": tmp_path,
        "pretty": False,
        "free_shipping_enabled": True,
        "default_shipping_cost": 9.99,  # must NOT be used when item has shipping_cost=0
    }
    comps = {"count": 3, "min": 10.0, "p25": 12.99, "median": 14.0,
             "p75": 15.0, "max": 15.0}
    monkeypatch.setattr(ebay_price_mod, "suggest_price",
                        lambda *a, **k: _suggestion(12.99, comps))
    sku = "tgw_fs_zero"
    _write_item_for_worker(tmp_path, sku)
    p = tmp_path / sku / f"{sku}.json"
    item = json.loads(p.read_text())
    item["shipping_cost"] = 0   # explicit zero — must be respected
    p.write_text(json.dumps(item))
    worker.handle({"payload_json": {"sku": sku}})
    result = json.loads(p.read_text())
    # ship_cost=0 → freeship block skips, free_shipping not set
    assert result.get("free_shipping") is not True
