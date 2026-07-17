"""Tests for tgw.ebay.category_aspect_migration (todo #1471,
PP-LISTEDITOR-001, invariant C14 lineage).
"""

from tgw.ebay import category_aspect_migration as cam


def _item(category_id="12345", item_specifics=None):
    return {
        "draft_listing": {
            "category_id": category_id,
            "item_specifics": item_specifics or {},
        },
    }


def test_detect_orphaned_aspects_finds_fields_outside_category_list(monkeypatch):
    monkeypatch.setattr(
        cam, "get_aspects",
        lambda cfg, category_id: [{"name": "Material"}, {"name": "Original/Reproduction"}],
    )
    item = _item(item_specifics={
        "Material": "Porcelain", "Original/Reproduction": "Vintage Original",
        "Color": "Orange", "Type": "Figurine",
    })
    orphaned = cam.detect_category_orphaned_aspects({}, item)
    assert {o["key"] for o in orphaned} == {"Color", "Type"}
    assert {o["key"]: o["value"] for o in orphaned} == {"Color": "Orange", "Type": "Figurine"}


def test_detect_orphaned_aspects_empty_when_all_covered(monkeypatch):
    monkeypatch.setattr(
        cam, "get_aspects",
        lambda cfg, category_id: [{"name": "Material"}],
    )
    item = _item(item_specifics={"Material": "Porcelain"})
    assert cam.detect_category_orphaned_aspects({}, item) == []


def test_detect_orphaned_aspects_no_category_returns_empty():
    item = _item(category_id="", item_specifics={"Color": "Orange"})
    assert cam.detect_category_orphaned_aspects({}, item) == []


def test_detect_orphaned_aspects_lookup_failure_fails_safe_to_empty(monkeypatch):
    """Prime Directive 1: never propose discarding real data because a
    category-aspect lookup failed (rate limit, network) — fail toward
    "nothing orphaned," not toward "everything orphaned."""
    def _raise(cfg, category_id):
        raise RuntimeError("rate limited")
    monkeypatch.setattr(cam, "get_aspects", _raise)
    item = _item(item_specifics={"Color": "Orange"})
    assert cam.detect_category_orphaned_aspects({}, item) == []


def test_apply_migration_moves_checked_keys_to_set_a_and_removes_from_set_b(monkeypatch):
    monkeypatch.setattr(
        cam, "get_aspects",
        lambda cfg, category_id: [{"name": "Material"}],
    )
    item = _item(item_specifics={"Material": "Porcelain", "Color": "Orange", "Type": "Figurine"})
    patch = cam.apply_category_aspect_migration(item, ["Color", "Type"], cfg={})

    assert patch["item_attributes"]["fields"] == {"Color": "Orange", "Type": "Figurine"}
    assert patch["draft_listing"]["item_specifics"]["fields"] == {"Material": "Porcelain"}
    assert patch["migrated_keys"] == ["Color", "Type"]
    assert len(patch["item_attributes_history"]) == 2
    assert len(patch["draft_listing"]["item_specifics_history"]) == 2
    for entry in patch["draft_listing"]["item_specifics_history"]:
        assert entry["value"] is None
        assert entry["source"] == "category_aspect_migration"


def test_apply_migration_only_moves_the_checked_subset(monkeypatch):
    monkeypatch.setattr(
        cam, "get_aspects",
        lambda cfg, category_id: [{"name": "Material"}],
    )
    item = _item(item_specifics={"Material": "Porcelain", "Color": "Orange", "Type": "Figurine"})
    patch = cam.apply_category_aspect_migration(item, ["Color"], cfg={})

    assert patch["migrated_keys"] == ["Color"]
    assert patch["item_attributes"]["fields"] == {"Color": "Orange"}
    # Type stays on eBay — not requested, must survive untouched.
    assert patch["draft_listing"]["item_specifics"]["fields"] == {
        "Material": "Porcelain", "Type": "Figurine",
    }


def test_apply_migration_reredetects_live_ignores_stale_requested_key(monkeypatch):
    """A key requested that is NO LONGER orphaned at call time (e.g. the
    category changed again, or it was already removed) is silently
    skipped — same re-diffing discipline as apply_inventory_diff."""
    monkeypatch.setattr(
        cam, "get_aspects",
        lambda cfg, category_id: [{"name": "Material"}, {"name": "Color"}],
    )
    item = _item(item_specifics={"Material": "Porcelain", "Color": "Orange"})
    patch = cam.apply_category_aspect_migration(item, ["Color"], cfg={})
    assert patch == {}


def test_apply_migration_empty_keys_returns_empty_patch(monkeypatch):
    monkeypatch.setattr(cam, "get_aspects", lambda cfg, category_id: [])
    item = _item(item_specifics={"Color": "Orange"})
    assert cam.apply_category_aspect_migration(item, [], cfg={}) == {}
