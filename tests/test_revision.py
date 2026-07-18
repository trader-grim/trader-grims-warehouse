"""Tests for tgw.revision — PP-REVISION-001 slices 1 and 2."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tgw.revision import (
    baseline_hash,
    cmd_revise,
    cmd_revise_apply,
    compose_revised_state,
    detect_drift,
    format_apply_diff,
    format_diff,
    live_mirror,
    parse_assignments,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(tmp_path: Path) -> dict:
    return {"itemdata_root": tmp_path / "ItemData", "pretty": False}


def _make_item(cfg: dict, sku: str, extra: dict | None = None) -> Path:
    sku_dir = Path(cfg["itemdata_root"]) / sku
    sku_dir.mkdir(parents=True, exist_ok=True)
    doc = {"sku": sku, **(extra or {})}
    p = sku_dir / f"{sku}.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _read_item(cfg: dict, sku: str) -> dict:
    p = Path(cfg["itemdata_root"]) / sku / f"{sku}.json"
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# parse_assignments
# ---------------------------------------------------------------------------

class TestParseAssignments:
    def test_string_value(self):
        assert parse_assignments(["title=Silver watch"]) == {"title": "Silver watch"}

    def test_int_value(self):
        assert parse_assignments(["qty=3"]) == {"qty": 3}

    def test_float_value(self):
        assert parse_assignments(["price=29.99"]) == {"price": 29.99}

    def test_bool_value(self):
        assert parse_assignments(["active=true"]) == {"active": True}

    def test_null_value(self):
        assert parse_assignments(["note=null"]) == {"note": None}

    def test_dotted_path(self):
        assert parse_assignments(["draft_listing.price=19.99"]) == {"draft_listing.price": 19.99}

    def test_value_with_equals(self):
        result = parse_assignments(["title=a=b"])
        assert result == {"title": "a=b"}

    def test_multiple_assignments(self):
        result = parse_assignments(["title=Foo", "price=9.99"])
        assert result == {"title": "Foo", "price": 9.99}

    def test_no_key_raises(self):
        with pytest.raises(ValueError, match="empty field name"):
            parse_assignments(["=value"])

    def test_no_equals_raises(self):
        with pytest.raises(ValueError, match="FIELD=VALUE"):
            parse_assignments(["noequals"])


# ---------------------------------------------------------------------------
# baseline_hash
# ---------------------------------------------------------------------------

class TestBaselineHash:
    def test_hash_is_16_hex_chars(self):
        h = baseline_hash({"listing_id": "123", "status": "Active"})
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_same_input_same_hash(self):
        snap = {"a": 1, "b": "two"}
        assert baseline_hash(snap) == baseline_hash(snap)

    def test_key_order_irrelevant(self):
        h1 = baseline_hash({"a": 1, "b": 2})
        h2 = baseline_hash({"b": 2, "a": 1})
        assert h1 == h2

    def test_different_input_different_hash(self):
        h1 = baseline_hash({"price": 29.99})
        h2 = baseline_hash({"price": 30.00})
        assert h1 != h2

    def test_empty_snapshot_has_hash(self):
        h = baseline_hash({})
        assert len(h) == 16


# ---------------------------------------------------------------------------
# live_mirror
# ---------------------------------------------------------------------------

class TestLiveMirror:
    def test_returns_ebay_listing_block(self):
        item = {"sku": "x", "ebay_listing": {"listing_id": "123", "status": "Active"}}
        assert live_mirror(item) == {"listing_id": "123", "status": "Active"}

    def test_returns_empty_when_absent(self):
        assert live_mirror({"sku": "x"}) == {}

    def test_returns_copy_not_reference(self):
        orig = {"listing_id": "abc"}
        item = {"ebay_listing": orig}
        m = live_mirror(item)
        m["injected"] = True
        assert "injected" not in item["ebay_listing"]


# ---------------------------------------------------------------------------
# detect_drift
# ---------------------------------------------------------------------------

class TestDetectDrift:
    def test_no_drift(self):
        snap = {"listing_id": "123", "status": "Active"}
        assert detect_drift(snap, snap) == []

    def test_detects_changed_field(self):
        pinned = {"status": "Active", "live_price": 29.99}
        current = {"status": "Ended", "live_price": 29.99}
        drift = detect_drift(current, pinned)
        assert len(drift) == 1
        assert drift[0]["field"] == "status"
        assert drift[0]["baseline"] == "Active"
        assert drift[0]["current"] == "Ended"

    def test_detects_added_field(self):
        pinned = {"listing_id": "123"}
        current = {"listing_id": "123", "live_price": 25.0}
        drift = detect_drift(current, pinned)
        assert any(d["field"] == "live_price" for d in drift)

    def test_detects_removed_field(self):
        pinned = {"listing_id": "123", "live_price": 25.0}
        current = {"listing_id": "123"}
        drift = detect_drift(current, pinned)
        assert any(d["field"] == "live_price" for d in drift)

    def test_empty_snapshots_no_drift(self):
        assert detect_drift({}, {}) == []


# ---------------------------------------------------------------------------
# cmd_revise
# ---------------------------------------------------------------------------

class TestCmdRevise:
    def test_missing_item_json_returns_error(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = cmd_revise(cfg, "tgw001", ["title=New title"])
        assert result["ok"] is False
        assert "not found" in result["error"]

    def test_no_assignments_returns_error(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001")
        result = cmd_revise(cfg, "tgw001", [])
        assert result["ok"] is False
        assert "no --set" in result["error"]

    def test_bad_assignment_returns_error(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001")
        result = cmd_revise(cfg, "tgw001", ["notanequalsign"])
        assert result["ok"] is False
        assert "FIELD=VALUE" in result["error"]

    def test_writes_revision_draft(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001", {"title": "Old title", "price": 20.0})
        result = cmd_revise(cfg, "tgw001", ["title=New title", "price=24.99"])

        assert result["ok"] is True
        item = _read_item(cfg, "tgw001")
        assert "revision_draft" in item
        rd = item["revision_draft"]
        assert rd["delta"] == {"title": "New title", "price": 24.99}
        assert "baseline" in rd
        assert "hash" in rd["baseline"]
        assert "snapshot" in rd["baseline"]
        assert "created_at" in rd
        assert rd["by"] == "claude"

    def test_only_revision_draft_modified(self, tmp_path):
        """cmd_revise must not modify any field other than revision_draft."""
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001", {"title": "Old", "price": 20.0, "condition": "Used"})
        cmd_revise(cfg, "tgw001", ["title=New", "price=25.0"])
        item = _read_item(cfg, "tgw001")
        assert item["title"] == "Old"
        assert item["price"] == 20.0
        assert item["condition"] == "Used"

    def test_baseline_snapshot_matches_ebay_listing(self, tmp_path):
        mirror = {"listing_id": "999", "status": "Active", "live_price": 29.99}
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001", {"ebay_listing": mirror})
        cmd_revise(cfg, "tgw001", ["price=24.99"])
        item = _read_item(cfg, "tgw001")
        snapshot = item["revision_draft"]["baseline"]["snapshot"]
        assert snapshot == mirror

    def test_baseline_hash_matches_snapshot(self, tmp_path):
        mirror = {"listing_id": "abc", "status": "Active"}
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001", {"ebay_listing": mirror})
        cmd_revise(cfg, "tgw001", ["title=X"])
        item = _read_item(cfg, "tgw001")
        stored_hash = item["revision_draft"]["baseline"]["hash"]
        assert stored_hash == baseline_hash(mirror)

    def test_baseline_empty_when_no_ebay_listing(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001", {"title": "Draft item"})
        result = cmd_revise(cfg, "tgw001", ["title=Updated"])
        assert result["ok"] is True
        item = _read_item(cfg, "tgw001")
        assert item["revision_draft"]["baseline"]["snapshot"] == {}

    def test_returns_delta_in_result(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001")
        result = cmd_revise(cfg, "tgw001", ["title=New", "qty=2"])
        assert result["delta"] == {"title": "New", "qty": 2}

    def test_returns_diff_lines(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001", {"title": "Old"})
        result = cmd_revise(cfg, "tgw001", ["title=New"])
        assert isinstance(result["diff_lines"], list)
        assert len(result["diff_lines"]) > 0

    def test_by_default_is_claude(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001")
        result = cmd_revise(cfg, "tgw001", ["title=X"])
        assert result["by"] == "claude"

    def test_by_overridable(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001")
        cmd_revise(cfg, "tgw001", ["title=X"], by="dave")
        item = _read_item(cfg, "tgw001")
        assert item["revision_draft"]["by"] == "dave"

    def test_overwrite_existing_draft(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001", {"title": "Old"})
        cmd_revise(cfg, "tgw001", ["title=First"])
        result = cmd_revise(cfg, "tgw001", ["title=Second"])
        assert result["ok"] is True
        assert result["had_existing_draft"] is True
        item = _read_item(cfg, "tgw001")
        assert item["revision_draft"]["delta"] == {"title": "Second"}

    def test_dotted_field_in_delta(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001", {"draft_listing": {"price": 20.0}})
        result = cmd_revise(cfg, "tgw001", ["draft_listing.price=15.0"])
        assert result["delta"] == {"draft_listing.price": 15.0}
        # Item's actual draft_listing.price must NOT be changed
        item = _read_item(cfg, "tgw001")
        assert item["draft_listing"]["price"] == 20.0

    def test_numeric_value_parsed(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001")
        result = cmd_revise(cfg, "tgw001", ["price=19.99"])
        assert result["delta"]["price"] == pytest.approx(19.99)


# ---------------------------------------------------------------------------
# fence-compliant read/write (#1313/#1316): sku_old fallback + E5 archiving
# ---------------------------------------------------------------------------

class TestCmdReviseFenceCompliance:
    def _reset_resolver_cache(self):
        import tgw.resolver as resolver_mod
        resolver_mod._sku_old_index = None

    def test_resolves_old_sku_via_sku_old_fallback(self, tmp_path):
        """A request using a renamed item's OLD sku must resolve via
        find_current_sku, matching items.get_item()'s established idiom."""
        self._reset_resolver_cache()
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw002", {"title": "Renamed item", "sku_old": "tgw001"})

        result = cmd_revise(cfg, "tgw001", ["title=New title"])

        assert result["ok"] is True
        assert result["sku"] == "tgw001"
        item = _read_item(cfg, "tgw002")
        assert item["revision_draft"]["delta"] == {"title": "New title"}

    def test_archives_pre_overwrite_content_on_write(self, tmp_path):
        """atomic_write_json must receive archive_root so invariant E5
        (archive-before-overwrite) fires on the revision_draft write."""
        cfg = _make_cfg(tmp_path)
        archive_root = tmp_path / "archive"
        cfg["archive_root"] = archive_root
        _make_item(cfg, "tgw001", {"title": "Old title"})

        # First write: item JSON doesn't exist yet at write-time... it does
        # (created by _make_item), so this write is itself an overwrite.
        result = cmd_revise(cfg, "tgw001", ["title=New title"])

        assert result["ok"] is True
        assert archive_root.exists()
        archived = list(archive_root.rglob("*.zip"))
        assert archived, f"expected an archive zip under {archive_root}, found none"


# ---------------------------------------------------------------------------
# detect_drift in context of existing draft
# ---------------------------------------------------------------------------

class TestDriftDetectionInRevise:
    def test_no_drift_when_mirror_unchanged(self, tmp_path):
        mirror = {"listing_id": "123", "status": "Active"}
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001", {"ebay_listing": mirror})
        # First revision (pins baseline)
        cmd_revise(cfg, "tgw001", ["title=X"])
        # Second revision (checks baseline vs current mirror — same, no drift)
        result = cmd_revise(cfg, "tgw001", ["title=Y"])
        assert result["drift"] == []

    def test_drift_reported_when_mirror_changes(self, tmp_path):
        mirror_v1 = {"listing_id": "123", "status": "Active", "live_price": 29.99}
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001", {"ebay_listing": mirror_v1})
        # First revision pins baseline at v1
        cmd_revise(cfg, "tgw001", ["title=X"])

        # Simulate ebay_sync updating the mirror
        item = _read_item(cfg, "tgw001")
        item["ebay_listing"]["live_price"] = 24.99
        Path(cfg["itemdata_root"]).joinpath("tgw001/tgw001.json").write_text(
            json.dumps(item), encoding="utf-8"
        )

        # Second revision should detect drift
        result = cmd_revise(cfg, "tgw001", ["title=Y"])
        assert len(result["drift"]) >= 1
        drifted_fields = [d["field"] for d in result["drift"]]
        assert "live_price" in drifted_fields


# ---------------------------------------------------------------------------
# format_diff smoke tests
# ---------------------------------------------------------------------------

class TestFormatDiff:
    def test_includes_field_names(self):
        item = {"title": "Old title"}
        delta = {"title": "New title"}
        lines = format_diff(item, delta, {}, [])
        full = "\n".join(lines)
        assert "title" in full
        assert "Old title" in full
        assert "New title" in full

    def test_includes_drift_when_present(self):
        item = {}
        delta = {"price": 20.0}
        drift = [{"field": "live_price", "baseline": 29.99, "current": 24.99}]
        lines = format_diff(item, delta, {"live_price": 24.99}, drift)
        full = "\n".join(lines)
        assert "drift" in full.lower()
        assert "live_price" in full

    def test_no_mirror_message(self):
        lines = format_diff({"title": "X"}, {"title": "Y"}, {}, [])
        full = "\n".join(lines)
        assert "not available" in full.lower() or "no drift" in full.lower()

    def test_new_field_label(self):
        item = {}
        delta = {"brand_new_field": "value"}
        lines = format_diff(item, delta, {}, [])
        full = "\n".join(lines)
        assert "new field" in full.lower()


# ---------------------------------------------------------------------------
# compose_revised_state
# ---------------------------------------------------------------------------

class TestComposeRevisedState:
    def test_delta_overwrites_mirror(self):
        mirror = {"title": "Old", "price": 20.0, "qty": 1}
        delta = {"title": "New", "price": 25.0}
        composed = compose_revised_state(mirror, delta)
        assert composed["title"] == "New"
        assert composed["price"] == 25.0
        assert composed["qty"] == 1  # preserved

    def test_mirror_keys_preserved(self):
        mirror = {"listing_id": "abc", "status": "Active"}
        delta = {"price": 9.99}
        composed = compose_revised_state(mirror, delta)
        assert composed["listing_id"] == "abc"
        assert composed["status"] == "Active"
        assert composed["price"] == 9.99

    def test_empty_delta_returns_mirror_copy(self):
        mirror = {"listing_id": "abc"}
        composed = compose_revised_state(mirror, {})
        assert composed == mirror
        assert composed is not mirror  # must be a copy

    def test_new_key_in_delta_added(self):
        mirror = {"listing_id": "abc"}
        delta = {"brand_new": "value"}
        composed = compose_revised_state(mirror, delta)
        assert composed["brand_new"] == "value"
        assert composed["listing_id"] == "abc"


# ---------------------------------------------------------------------------
# _overlapping_drift (via format_apply_diff indirectly, but also directly)
# ---------------------------------------------------------------------------

class TestOverlappingDrift:
    def test_overlapping_field_is_blocking(self):
        from tgw.revision import _overlapping_drift
        delta = {"price": 25.0, "title": "New"}
        drift = [
            {"field": "price", "baseline": 20.0, "current": 22.0},
            {"field": "status", "baseline": "Active", "current": "Ended"},
        ]
        blocking = _overlapping_drift(delta, drift)
        assert len(blocking) == 1
        assert blocking[0]["field"] == "price"

    def test_non_overlapping_is_empty(self):
        from tgw.revision import _overlapping_drift
        delta = {"title": "New"}
        drift = [{"field": "status", "baseline": "Active", "current": "Ended"}]
        assert _overlapping_drift(delta, drift) == []

    def test_empty_drift_returns_empty(self):
        from tgw.revision import _overlapping_drift
        assert _overlapping_drift({"price": 1}, []) == []


# ---------------------------------------------------------------------------
# format_apply_diff
# ---------------------------------------------------------------------------

class TestFormatApplyDiff:
    def test_shows_field_change(self):
        delta = {"price": 25.0}
        mirror = {"price": 20.0}
        composed = {"price": 25.0}
        lines = format_apply_diff(delta, mirror, composed, [], [])
        full = "\n".join(lines)
        assert "price" in full
        assert "20.0" in full
        assert "25.0" in full

    def test_shows_blocking_drift_section(self):
        blocking = [{"field": "price", "baseline": 20.0, "current": 22.0}]
        lines = format_apply_diff({"price": 25.0}, {}, {}, blocking, [])
        full = "\n".join(lines)
        assert "BLOCKING" in full
        assert "price" in full

    def test_shows_non_blocking_drift_section(self):
        non_blocking = [{"field": "status", "baseline": "Active", "current": "Ended"}]
        lines = format_apply_diff({"price": 25.0}, {}, {}, [], non_blocking)
        full = "\n".join(lines)
        assert "non-blocking" in full.lower()
        assert "status" in full

    def test_no_change_skipped_in_apply_diff(self):
        delta = {"price": 20.0}
        mirror = {"price": 20.0}  # same value — no change
        lines = format_apply_diff(delta, mirror, {"price": 20.0}, [], [])
        full = "\n".join(lines)
        assert "was:" not in full  # no diff line emitted when value is the same


# ---------------------------------------------------------------------------
# cmd_revise_apply
# ---------------------------------------------------------------------------

def _make_item_with_draft(cfg, sku, delta, mirror=None, extra=None):
    """Helper: create an item JSON with a pre-written revision_draft."""
    import json as _json

    from tgw.revision import baseline_hash as _bh
    mirror = mirror or {}
    snapshot = dict(mirror)
    b_hash = _bh(snapshot)
    doc = {
        "sku": sku,
        **(extra or {}),
        "ebay_listing": mirror,
        "revision_draft": {
            "delta": delta,
            "baseline": {"hash": b_hash, "snapshot": snapshot},
            "created_at": "2026-06-14T00:00:00Z",
            "by": "claude",
        },
    }
    sku_dir = Path(cfg["itemdata_root"]) / sku
    sku_dir.mkdir(parents=True, exist_ok=True)
    p = sku_dir / f"{sku}.json"
    p.write_text(_json.dumps(doc), encoding="utf-8")
    return p


class TestCmdReviseApply:
    def test_missing_item_json_returns_error(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = cmd_revise_apply(cfg, "tgw999")
        assert result["ok"] is False
        assert "not found" in result["error"]

    def test_no_revision_draft_returns_error(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001")
        result = cmd_revise_apply(cfg, "tgw001")
        assert result["ok"] is False
        assert "revision_draft" in result["error"]

    def test_empty_delta_returns_error(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        # Write item with draft that has no delta
        sku_dir = Path(cfg["itemdata_root"]) / "tgw001"
        sku_dir.mkdir(parents=True, exist_ok=True)
        doc = {
            "sku": "tgw001",
            "revision_draft": {
                "delta": {},
                "baseline": {"hash": "abc", "snapshot": {}},
                "created_at": "2026-06-14T00:00:00Z",
                "by": "claude",
            },
        }
        (sku_dir / "tgw001.json").write_text(json.dumps(doc))
        result = cmd_revise_apply(cfg, "tgw001")
        assert result["ok"] is False
        assert "empty" in result["error"]

    def test_dry_run_success_no_drift(self, tmp_path):
        mirror = {"listing_id": "abc", "status": "Active", "price": 20.0}
        cfg = _make_cfg(tmp_path)
        _make_item_with_draft(cfg, "tgw001", delta={"price": 25.0}, mirror=mirror)
        result = cmd_revise_apply(cfg, "tgw001", dry_run=True)
        assert result["ok"] is True
        assert result["dry_run"] is True
        assert result["applied"] is False
        assert result["delta"] == {"price": 25.0}
        assert result["composed"]["price"] == 25.0
        assert result["composed"]["listing_id"] == "abc"  # mirror preserved
        assert result["blocking_drift"] == []
        assert result["hash_match"] is True

    def test_blocking_drift_refuses_apply(self, tmp_path):
        mirror_v1 = {"listing_id": "abc", "price": 20.0}
        cfg = _make_cfg(tmp_path)
        path = _make_item_with_draft(cfg, "tgw001", delta={"price": 25.0}, mirror=mirror_v1)

        # Simulate mirror drift on the same field as delta
        item = json.loads(path.read_text())
        item["ebay_listing"]["price"] = 22.0  # drifted!
        path.write_text(json.dumps(item))

        result = cmd_revise_apply(cfg, "tgw001", dry_run=True)
        assert result["ok"] is False
        assert "drifted" in result["error"]
        assert len(result["blocking_drift"]) == 1
        assert result["blocking_drift"][0]["field"] == "price"

    def test_non_blocking_drift_allowed(self, tmp_path):
        mirror_v1 = {"listing_id": "abc", "price": 20.0, "status": "Active"}
        cfg = _make_cfg(tmp_path)
        path = _make_item_with_draft(cfg, "tgw001", delta={"price": 25.0}, mirror=mirror_v1)

        # Drift on status (not in delta → non-blocking)
        item = json.loads(path.read_text())
        item["ebay_listing"]["status"] = "Ended"
        path.write_text(json.dumps(item))

        result = cmd_revise_apply(cfg, "tgw001", dry_run=True)
        assert result["ok"] is True
        assert result["blocking_drift"] == []
        assert len(result["non_blocking_drift"]) == 1
        assert result["non_blocking_drift"][0]["field"] == "status"

    def test_live_write_gated_by_apply_enabled(self, tmp_path):
        mirror = {"listing_id": "abc", "price": 20.0}
        cfg = _make_cfg(tmp_path)
        _make_item_with_draft(cfg, "tgw001", delta={"price": 25.0}, mirror=mirror)
        with patch("tgw.revision._APPLY_ENABLED", False):
            result = cmd_revise_apply(cfg, "tgw001", dry_run=False)
        assert result["ok"] is False
        assert "disabled" in result["error"].lower()

    def test_returns_diff_lines(self, tmp_path):
        mirror = {"listing_id": "abc", "price": 20.0}
        cfg = _make_cfg(tmp_path)
        _make_item_with_draft(cfg, "tgw001", delta={"price": 25.0}, mirror=mirror)
        result = cmd_revise_apply(cfg, "tgw001", dry_run=True)
        assert result["ok"] is True
        assert isinstance(result["diff_lines"], list)
        assert len(result["diff_lines"]) > 0

    def test_returns_sku_in_result(self, tmp_path):
        mirror = {"listing_id": "abc"}
        cfg = _make_cfg(tmp_path)
        _make_item_with_draft(cfg, "tgw001", delta={"title": "X"}, mirror=mirror)
        result = cmd_revise_apply(cfg, "tgw001", dry_run=True)
        assert result["sku"] == "tgw001"


# ---------------------------------------------------------------------------
# Live apply (PP-LISTEDITOR-001 Phase 2) — mocked eBay client
# ---------------------------------------------------------------------------

_LIVE_MIRROR = {
    "listing_id": "326000000001",
    "offer_id": "262000000001",
    "api": "inventory",
    "status": "Active",
    "live_price": 20.0,
}


def _fresh_bodies():
    """Fresh GET responses as eBay would return them (incl. read-only keys)."""
    inv = {
        "sku": "tgw001",
        "locale": "en_US",
        "condition": "USED_EXCELLENT",
        "product": {"title": "Old Title", "description": "Old desc",
                    "imageUrls": ["https://i.example/1.jpg"],
                    "aspects": {"Brand": ["Acme"]}},
        "availability": {"shipToLocationAvailability": {
            "quantity": 1,
            "availabilityDistributions": [
                {"merchantLocationKey": "LOC1", "quantity": 1}],
        }},
    }
    offer = {
        "offerId": "262000000001",
        "sku": "tgw001",
        "status": "PUBLISHED",
        "marketplaceId": "EBAY_US",
        "listingDescription": "Old listing desc",
        "availableQuantity": 1,
        "pricingSummary": {"price": {"currency": "USD", "value": "20.00"}},
    }
    return inv, offer


class TestLiveApply:
    def _run(self, tmp_path, delta, mirror=None):
        cfg = _make_cfg(tmp_path)
        _make_item_with_draft(cfg, "tgw001", delta=delta,
                              mirror=dict(mirror or _LIVE_MIRROR))
        inv, offer = _fresh_bodies()
        puts = []

        def fake_get(cfg_, path, **kw):
            return dict(inv) if "inventory_item" in path else dict(offer)

        def fake_put(cfg_, path, body, **kw):
            puts.append((path, body))
            return {}

        with patch("tgw.apis.ebay.client.ebay_get", side_effect=fake_get), \
             patch("tgw.apis.ebay.client.ebay_put", side_effect=fake_put):
            result = cmd_revise_apply(cfg, "tgw001", dry_run=False, by="test")
        return cfg, result, puts

    def test_price_delta_puts_offer_only(self, tmp_path):
        cfg, result, puts = self._run(tmp_path, {"price": 25.0})
        assert result["ok"] is True
        assert result["applied"] is True
        assert len(puts) == 1
        path, body = puts[0]
        assert "offer/262000000001" in path
        assert body["pricingSummary"]["price"]["value"] == "25.00"
        # read-only keys stripped before PUT
        assert "offerId" not in body and "status" not in body

    def test_title_delta_puts_inventory_only(self, tmp_path):
        cfg, result, puts = self._run(tmp_path, {"title": "New Title"})
        assert result["ok"] is True
        assert len(puts) == 1
        path, body = puts[0]
        assert "inventory_item/tgw001" in path
        assert body["product"]["title"] == "New Title"
        assert "sku" not in body and "locale" not in body

    def test_quantity_delta_puts_both(self, tmp_path):
        cfg, result, puts = self._run(tmp_path, {"quantity": 3})
        assert result["ok"] is True
        assert len(puts) == 2
        inv_body = next(b for p, b in puts if "inventory_item" in p)
        offer_body = next(b for p, b in puts if "offer/" in p)
        avail = inv_body["availability"]["shipToLocationAvailability"]
        assert avail["quantity"] == 3
        assert avail["availabilityDistributions"][0]["quantity"] == 3
        assert offer_body["availableQuantity"] == 3

    def test_aspects_delta_normalizes_values(self, tmp_path):
        cfg, result, puts = self._run(
            tmp_path, {"item_specifics": {"Brand": "NewCo", "Color": ["Red"]}})
        assert result["ok"] is True
        inv_body = puts[0][1]
        assert inv_body["product"]["aspects"] == {
            "Brand": ["NewCo"], "Color": ["Red"]}

    def test_unsupported_field_refuses(self, tmp_path):
        cfg, result, puts = self._run(tmp_path, {"bogus_field": 1})
        assert result["ok"] is False
        assert "unsupported" in result["error"]
        assert puts == []

    def test_trading_api_item_refuses(self, tmp_path):
        mirror = {"listing_id": "1", "api": "trading", "offer_id": ""}
        cfg, result, puts = self._run(tmp_path, {"price": 25.0}, mirror=mirror)
        assert result["ok"] is False
        assert "Inventory API" in result["error"]
        assert puts == []

    def test_archives_pre_overwrite_content_on_live_apply(self, tmp_path):
        """#1316: the live-apply write must also pass archive_root so
        invariant E5 fires (item JSON already exists at write-time)."""
        cfg = _make_cfg(tmp_path)
        archive_root = tmp_path / "archive"
        cfg["archive_root"] = archive_root
        _make_item_with_draft(cfg, "tgw001", delta={"price": 25.0},
                              mirror=dict(_LIVE_MIRROR))
        inv, offer = _fresh_bodies()

        def fake_get(cfg_, path, **kw):
            return dict(inv) if "inventory_item" in path else dict(offer)

        def fake_put(cfg_, path, body, **kw):
            return {}

        with patch("tgw.apis.ebay.client.ebay_get", side_effect=fake_get), \
             patch("tgw.apis.ebay.client.ebay_put", side_effect=fake_put):
            result = cmd_revise_apply(cfg, "tgw001", dry_run=False, by="test")

        assert result["ok"] is True
        assert archive_root.exists()
        archived = list(archive_root.rglob("*.zip"))
        assert archived, f"expected an archive zip under {archive_root}, found none"

    def test_no_offer_id_refuses(self, tmp_path):
        mirror = {"listing_id": "1", "api": "inventory"}
        cfg, result, puts = self._run(tmp_path, {"price": 25.0}, mirror=mirror)
        assert result["ok"] is False
        assert puts == []

    def test_apply_clears_draft_and_writes_history(self, tmp_path):
        cfg, result, puts = self._run(tmp_path, {"price": 25.0})
        assert result["ok"] is True
        doc = _read_item(cfg, "tgw001")
        assert "revision_draft" not in doc
        history = doc["revision_history"]
        assert len(history) == 1
        assert history[0]["delta"] == {"price": 25.0}
        assert history[0]["by"] == "test"
        assert history[0]["calls"] == result["calls"]

    def test_c14_aspects_delta_clear_omits_key_not_blank_value(self, tmp_path):
        """An operator's revision-apply delta clearing 'Brand' (an accepted
        proposal setting it to '') must omit the key from the PUT body sent
        to eBay's Inventory API, not send Brand: [''] — eBay rejects an
        explicit empty aspect value outright (invariant C14 incident,
        #1462), and this push path never got that fix applied."""
        cfg, result, puts = self._run(
            tmp_path, {"item_specifics": {"Brand": "", "Color": ["Red"]}})
        assert result["ok"] is True
        inv_body = puts[0][1]
        assert "Brand" not in inv_body["product"]["aspects"], (
            "cleared aspect 'Brand' was sent to eBay as an explicit blank "
            "value instead of being omitted — eBay rejects this outright, "
            "invariant C14"
        )
        assert inv_body["product"]["aspects"]["Color"] == ["Red"]

    def test_blocking_drift_still_refuses_live(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        path = _make_item_with_draft(cfg, "tgw001", delta={"live_price": 25.0},
                                     mirror=dict(_LIVE_MIRROR))
        item = json.loads(path.read_text())
        item["ebay_listing"]["live_price"] = 22.0  # drifted on delta field
        path.write_text(json.dumps(item))
        with patch("tgw.apis.ebay.client.ebay_get") as g, \
             patch("tgw.apis.ebay.client.ebay_put") as p:
            result = cmd_revise_apply(cfg, "tgw001", dry_run=False)
        assert result["ok"] is False
        assert "drifted" in result["error"]
        g.assert_not_called()
        p.assert_not_called()

    def test_dry_run_never_calls_ebay(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_item_with_draft(cfg, "tgw001", delta={"price": 25.0},
                              mirror=dict(_LIVE_MIRROR))
        with patch("tgw.apis.ebay.client.ebay_get") as g, \
             patch("tgw.apis.ebay.client.ebay_put") as p:
            result = cmd_revise_apply(cfg, "tgw001", dry_run=True)
        assert result["ok"] is True
        assert result["applied"] is False
        g.assert_not_called()
        p.assert_not_called()
