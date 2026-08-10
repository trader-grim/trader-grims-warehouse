"""Tests for tgw.workflow.item_snapshot — pure read-only ObjectSnapshot builder."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from tgw.workflow.contracts import (  # noqa: E402
    FingerprintResult,
    GoalProfile,
    ObjectSnapshot,
)
from tgw.workflow.item_snapshot import build_item_snapshot  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(**overrides):
    """Build a minimal item dict with safe defaults, overridden by kwargs."""
    item = {
        "sku": "TEST-001",
        "title": "Widget",
        "condition": "Used",
        "ebay_category_id": "12345",
        "draft_listing": {
            "title": "Widget in good condition",
            "category_id": "12345",
            "price": 10.0,
            "imageUrls": ["https://img.example/1.jpg"],
        },
        "ebay_offer": {"offer_id": "55123456789"},
        "ebay_listing": {"status": "Active"},
        "ebay_photos": ["https://uploaded.example/1.jpg"],
        "image": "photos/TEST-001/001.jpg",
    }
    item.update(overrides)
    return item


def _snapshot(item, goal=None, tmp_path=None) -> ObjectSnapshot:
    """Write item to a temp JSON and build a snapshot."""
    if tmp_path is None:
        import atexit
        from tempfile import mkdtemp

        tmp_dir = Path(mkdtemp(prefix="test_item_snapshot_"))
        atexit.register(lambda d=tmp_dir: _rmtree(d))
        json_path = tmp_dir / "item.json"
    else:
        json_path = tmp_path / "item.json"
        json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(item), encoding="utf-8")
    if goal is None:
        goal = GoalProfile(
            identity="ready",
            version="1",
            required=(
                "item_has_photos",
                "photos_uploaded",
                "ai_identified",
                "draft_generated",
                "priced",
                "staged",
                "published",
                "valid_condition",
                "valid_category",
                "title_ok",
            ),
        )
    return build_item_snapshot(str(json_path), goal)


def _rmtree(path: Path) -> None:
    """Best-effort recursive delete of a temp directory."""
    if not path.exists():
        return
    for child in sorted(path.iterdir(), reverse=True):
        try:
            if child.is_dir():
                _rmtree(child)
            else:
                child.unlink()
        except OSError:
            pass
    try:
        path.rmdir()
    except OSError:
        pass


def _result(snapshot: ObjectSnapshot, condition_id: str) -> FingerprintResult:
    """Return the FingerprintResult for a given condition_id, or None."""
    for a in snapshot.assertions:
        if a.condition_id == condition_id:
            return a.result
    return None


# ---------------------------------------------------------------------------
# Object identity and generation
# ---------------------------------------------------------------------------


def test_snapshot_object_id_is_sku_from_item():
    item = _make_item(sku="SKU-123")
    snap = _snapshot(item)
    assert snap.object_id == "SKU-123"


def test_snapshot_generation_is_sha256_of_canonical_json():
    item_a = _make_item(sku="A")
    item_b = _make_item(sku="B")

    snap_a = _snapshot(item_a)
    snap_b = _snapshot(item_b)

    assert len(snap_a.generation) == 64
    assert len(snap_b.generation) == 64
    assert snap_a.generation != snap_b.generation, "different items must have different generations"


def test_snapshot_generation_is_stable():
    """Same item JSON produces the same generation hash twice."""
    import tempfile

    item = _make_item()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "item.json"
        p.write_text(json.dumps(item), encoding="utf-8")
        goal = GoalProfile("test", "1", ())
        a = build_item_snapshot(str(p), goal)
        b = build_item_snapshot(str(p), goal)
        assert a.generation == b.generation


# ---------------------------------------------------------------------------
# item_has_photos
# ---------------------------------------------------------------------------


def test_has_photos_true_via_image_field():
    item = _make_item(image="photos/TEST/001.jpg", _images=[])
    snap = _snapshot(item)
    assert _result(snap, "item_has_photos") == FingerprintResult.TRUE


def test_has_photos_true_via_images_list():
    item = _make_item(image="", _images=["/path/to/001.jpg"])
    snap = _snapshot(item)
    assert _result(snap, "item_has_photos") == FingerprintResult.TRUE


def test_has_photos_false():
    item = _make_item(image="", _images=[])
    snap = _snapshot(item)
    assert _result(snap, "item_has_photos") == FingerprintResult.FALSE


# ---------------------------------------------------------------------------
# photos_uploaded
# ---------------------------------------------------------------------------


def test_photos_uploaded_true_via_ebay_photos():
    item = _make_item(ebay_photos=["https://img.ebay.com/001.jpg"], draft_listing={})
    snap = _snapshot(item)
    assert _result(snap, "photos_uploaded") == FingerprintResult.TRUE


def test_photos_uploaded_true_via_draft_image_urls():
    item = _make_item(
        ebay_photos=[],
        draft_listing={"imageUrls": ["https://img.example/1.jpg"]},
    )
    snap = _snapshot(item)
    assert _result(snap, "photos_uploaded") == FingerprintResult.TRUE


def test_photos_uploaded_false():
    item = _make_item(ebay_photos=[], draft_listing={"imageUrls": []})
    snap = _snapshot(item)
    assert _result(snap, "photos_uploaded") == FingerprintResult.FALSE


# ---------------------------------------------------------------------------
# ai_identified
# ---------------------------------------------------------------------------


def test_ai_identified_true_via_ebay_category_id():
    item = _make_item(ebay_category_id="45678", product_lookup=None)
    snap = _snapshot(item)
    assert _result(snap, "ai_identified") == FingerprintResult.TRUE


def test_ai_identified_true_via_product_lookup():
    item = _make_item(ebay_category_id="", product_lookup={"epid": "123"})
    snap = _snapshot(item)
    assert _result(snap, "ai_identified") == FingerprintResult.TRUE


def test_ai_identified_false():
    item = _make_item(ebay_category_id="", product_lookup={})
    snap = _snapshot(item)
    assert _result(snap, "ai_identified") == FingerprintResult.FALSE


# ---------------------------------------------------------------------------
# draft_generated
# ---------------------------------------------------------------------------


def test_draft_generated_true():
    snap = _snapshot(_make_item())
    assert _result(snap, "draft_generated") == FingerprintResult.TRUE


def test_draft_generated_false_no_draft():
    item = _make_item(draft_listing=None)
    snap = _snapshot(item)
    assert _result(snap, "draft_generated") == FingerprintResult.FALSE


def test_draft_generated_false_no_title():
    item = _make_item(draft_listing={"title": "", "category_id": "12345"})
    snap = _snapshot(item)
    assert _result(snap, "draft_generated") == FingerprintResult.FALSE


def test_draft_generated_false_category_99():
    item = _make_item(draft_listing={"title": "Widget", "category_id": "99"})
    snap = _snapshot(item)
    assert _result(snap, "draft_generated") == FingerprintResult.FALSE


# ---------------------------------------------------------------------------
# priced
# ---------------------------------------------------------------------------


def test_priced_true():
    snap = _snapshot(_make_item(draft_listing={"price": 10.0}))
    assert _result(snap, "priced") == FingerprintResult.TRUE


def test_priced_false_null():
    item = _make_item(draft_listing={"price": None})
    snap = _snapshot(item)
    assert _result(snap, "priced") == FingerprintResult.FALSE


def test_priced_false_zero():
    item = _make_item(draft_listing={"price": 0})
    snap = _snapshot(item)
    assert _result(snap, "priced") == FingerprintResult.FALSE


def test_priced_false_negative():
    item = _make_item(draft_listing={"price": -5.0})
    snap = _snapshot(item)
    assert _result(snap, "priced") == FingerprintResult.FALSE


def test_priced_false_missing_draft():
    item = _make_item(draft_listing=None)
    snap = _snapshot(item)
    assert _result(snap, "priced") == FingerprintResult.FALSE


# ---------------------------------------------------------------------------
# staged
# ---------------------------------------------------------------------------


def test_staged_true():
    snap = _snapshot(_make_item(ebay_offer={"offer_id": "55123456789"}))
    assert _result(snap, "staged") == FingerprintResult.TRUE


def test_staged_false_no_offer_id():
    item = _make_item(ebay_offer={"other": "data"})
    snap = _snapshot(item)
    assert _result(snap, "staged") == FingerprintResult.FALSE


def test_staged_false_no_offer():
    item = _make_item(ebay_offer=None)
    snap = _snapshot(item)
    assert _result(snap, "staged") == FingerprintResult.FALSE


# ---------------------------------------------------------------------------
# published
# ---------------------------------------------------------------------------


def test_published_true():
    snap = _snapshot(_make_item(ebay_listing={"status": "Active"}))
    assert _result(snap, "published") == FingerprintResult.TRUE


def test_published_false_other_status():
    item = _make_item(ebay_listing={"status": "Draft"})
    snap = _snapshot(item)
    assert _result(snap, "published") == FingerprintResult.FALSE


def test_published_false_no_listing():
    item = _make_item(ebay_listing=None)
    snap = _snapshot(item)
    assert _result(snap, "published") == FingerprintResult.FALSE


# ---------------------------------------------------------------------------
# valid_condition
# ---------------------------------------------------------------------------


def test_valid_condition_true():
    for cond in ("New", "Used", "For parts or not working", "Seller refurbished"):
        item = _make_item(condition=cond)
        snap = _snapshot(item)
        assert _result(snap, "valid_condition") == FingerprintResult.TRUE, f"condition={cond!r}"


def test_valid_condition_false_unknown():
    snap = _snapshot(_make_item(condition="Excellent"))
    assert _result(snap, "valid_condition") == FingerprintResult.FALSE


def test_valid_condition_false_missing():
    item = _make_item()
    del item["condition"]
    snap = _snapshot(item)
    assert _result(snap, "valid_condition") == FingerprintResult.FALSE


# ---------------------------------------------------------------------------
# valid_category
# ---------------------------------------------------------------------------


def test_valid_category_true():
    snap = _snapshot(_make_item(ebay_category_id="45678"))
    assert _result(snap, "valid_category") == FingerprintResult.TRUE


def test_valid_category_false_99():
    item = _make_item(ebay_category_id="99")
    snap = _snapshot(item)
    assert _result(snap, "valid_category") == FingerprintResult.FALSE


def test_valid_category_false_empty():
    item = _make_item(ebay_category_id="")
    snap = _snapshot(item)
    assert _result(snap, "valid_category") == FingerprintResult.FALSE


# ---------------------------------------------------------------------------
# title_ok
# ---------------------------------------------------------------------------


def test_title_ok_true_short():
    item = _make_item(draft_listing={"title": "Short title"})
    snap = _snapshot(item)
    assert _result(snap, "title_ok") == FingerprintResult.TRUE


def test_title_ok_true_exactly_80():
    item = _make_item(draft_listing={"title": "A" * 80})
    snap = _snapshot(item)
    assert _result(snap, "title_ok") == FingerprintResult.TRUE


def test_title_ok_false_over_80():
    item = _make_item(draft_listing={"title": "A" * 81})
    snap = _snapshot(item)
    assert _result(snap, "title_ok") == FingerprintResult.FALSE


def test_title_ok_false_empty():
    item = _make_item(draft_listing={"title": ""})
    snap = _snapshot(item)
    assert _result(snap, "title_ok") == FingerprintResult.FALSE


def test_title_ok_false_missing():
    item = _make_item(draft_listing={})
    snap = _snapshot(item)
    assert _result(snap, "title_ok") == FingerprintResult.FALSE


# ---------------------------------------------------------------------------
# pipeline_error
# ---------------------------------------------------------------------------


def test_pipeline_error_present():
    item = _make_item(pipeline_error="Draft generation failed: timeout")
    snap = _snapshot(item)
    assert _result(snap, "pipeline_error") == FingerprintResult.TRUE


def test_pipeline_error_absent():
    item = _make_item()
    item.pop("pipeline_error", None)
    snap = _snapshot(item)
    assert _result(snap, "pipeline_error") is None


# ---------------------------------------------------------------------------
# All conditions present
# ---------------------------------------------------------------------------


def test_snapshot_has_all_expected_conditions():
    """Happy-path item produces exactly the 10 core assertions (no pipeline_error)."""
    item = _make_item()
    item.pop("pipeline_error", None)
    snap = _snapshot(item)

    condition_ids = sorted(a.condition_id for a in snap.assertions)
    expected = sorted([
        "item_has_photos",
        "photos_uploaded",
        "ai_identified",
        "draft_generated",
        "priced",
        "staged",
        "published",
        "valid_condition",
        "valid_category",
        "title_ok",
    ])
    assert condition_ids == expected


def test_snapshot_includes_pipeline_error_when_present():
    item = _make_item(pipeline_error="some error")
    snap = _snapshot(item)
    ids = {a.condition_id for a in snap.assertions}
    assert "pipeline_error" in ids
    assert len(ids) == 11  # 10 core + pipeline_error


# ---------------------------------------------------------------------------
# External effect ambiguities
# ---------------------------------------------------------------------------


def test_no_external_effect_ambiguities():
    """Snapshot builder never sets external_effect_ambiguities (items are local)."""
    snap = _snapshot(_make_item())
    assert snap.external_effect_ambiguities == ()


def test_item_data_cannot_manufacture_external_effect_ambiguity(tmp_path):
    item = _make_item(external_effect_ambiguities=["listing.publish"])
    snap = _snapshot(item, tmp_path=tmp_path)

    assert snap.external_effect_ambiguities == ()


def test_authoritative_external_effect_ambiguity_is_separate_input(tmp_path):
    path = tmp_path / "item.json"
    path.write_text(json.dumps(_make_item()), encoding="utf-8")
    goal = GoalProfile("ready", "1", ("published",))

    snap = build_item_snapshot(
        path,
        goal,
        external_effect_ambiguities=("listing.publish", "listing.publish"),
    )

    assert snap.external_effect_ambiguities == ("listing.publish",)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_item_is_handled_gracefully():
    """Snapshot still builds with a nearly-empty item — all conditions FALSE."""
    item = {"sku": "BARE"}
    snap = _snapshot(item)
    assert snap.object_id == "BARE"
    assert len(snap.generation) == 64
    # All core conditions should be FALSE
    for a in snap.assertions:
        if a.condition_id != "pipeline_error":
            assert a.result == FingerprintResult.FALSE, f"{a.condition_id} should be FALSE"


def test_missing_sku_uses_dir_name():
    """When SKU is absent from the item, fall back to the parent dir name."""
    import tempfile

    item = {"title": "No SKU"}
    with tempfile.TemporaryDirectory() as td:
        sku_dir = Path(td) / "FALLBACK-SKU"
        sku_dir.mkdir()
        json_path = sku_dir / "item.json"
        json_path.write_text(json.dumps(item), encoding="utf-8")
        goal = GoalProfile("test", "1", ())
        snap = build_item_snapshot(str(json_path), goal)
        assert snap.object_id == "FALLBACK-SKU"
