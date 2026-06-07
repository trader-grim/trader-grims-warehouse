"""PP-INTAKE-001 — tests for the set-template intake feature.

The one-button template intake loop (CLI + mobile web form) is the core
operator-velocity feature, touched daily, and had ZERO test coverage. Covers
_build_template_fields (pure) and cmd_set_template (--list/--camera/--dry-run/
write/unknown-key/CurrentItem fallback).

Reconcile note (session 15 audit): the plan claimed the template writes
fulfillment_policy_id. It does NOT — test_template_does_not_write_fulfillment_policy
pins that. The per-item policy mechanism is PP-HINT-001 shipping_profile +
PP-STORAGE-001 size_class resolver instead.
"""

from __future__ import annotations

import json

import pytest

import tgw.api as api
import tgw.ebay.pricing as pricing

GROUPS = {
    "groups": {
        "books": {
            "name": "Books",
            "size_class": "small",
            "ai_hint": "printed book",
            "ebay_categories": ["261186"],
            "pricing": {"typical_used": 8.0},
        },
        "vinyl": {
            "name": "Vinyl Records",
            "ai_hint": "",
            "ebay_categories": [],
            "pricing": {},
        },
    }
}
BOOKS = GROUPS["groups"]["books"]
VINYL = GROUPS["groups"]["vinyl"]


# ---------------------------------------------------------------------------
# _build_template_fields (pure — cfg is unused)
# ---------------------------------------------------------------------------

def test_build_fields_writes_core_fields_on_empty_item():
    fields = api._build_template_fields({}, BOOKS, "books", {})
    assert fields["category_group"] == "books"
    assert fields["size_class"] == "small"
    assert fields["ai_hint"] == "printed book"
    assert fields["ebay_category_id"] == "261186"


def test_build_fields_prepends_existing_ai_hint():
    fields = api._build_template_fields({}, BOOKS, "books", {"ai_hint": "first edition"})
    assert fields["ai_hint"] == "printed book; first edition"


def test_build_fields_no_duplicate_when_hint_equals_group():
    fields = api._build_template_fields({}, BOOKS, "books", {"ai_hint": "printed book"})
    assert fields["ai_hint"] == "printed book"


def test_build_fields_preserves_existing_category_id():
    fields = api._build_template_fields(
        {}, BOOKS, "books", {"ebay_category_id": "99999"})
    assert "ebay_category_id" not in fields  # not overwritten


def test_build_fields_omits_size_class_when_group_lacks_it():
    fields = api._build_template_fields({}, VINYL, "vinyl", {})
    assert "size_class" not in fields
    assert fields["category_group"] == "vinyl"
    # vinyl has empty ai_hint and no categories -> only category_group written.
    assert "ai_hint" not in fields
    assert "ebay_category_id" not in fields


def test_template_does_not_write_fulfillment_policy():
    # Reconcile: the plan claimed this; the code never writes it.
    fields = api._build_template_fields({}, BOOKS, "books", {})
    assert "fulfillment_policy_id" not in fields


# ---------------------------------------------------------------------------
# cmd_set_template
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg(tmp_path):
    return {
        "itemdata_root": tmp_path,
        "category_groups_path": str(tmp_path / "category-groups.json"),
    }


@pytest.fixture(autouse=True)
def _stub_groups_and_clipboard(monkeypatch):
    # _load_groups is imported lazily from .ebay.pricing; patch the source.
    monkeypatch.setattr(pricing, "_load_groups", lambda cfg: GROUPS)
    # Never shell out to wl-copy/xclip in tests.
    monkeypatch.setattr(api, "_push_clipboard", lambda text: True)


def _write_item(cfg, sku, doc):
    d = cfg["itemdata_root"] / sku
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sku}.json").write_text(json.dumps(doc), encoding="utf-8")
    return d / f"{sku}.json"


def test_list_groups(cfg, capsys):
    out = api.cmd_set_template(cfg, list_groups=True)
    assert out["ok"] is True
    assert out["count"] == 2


def test_camera_only_pushes_clipboard_no_write(cfg):
    out = api.cmd_set_template(cfg, camera_only="books")
    assert out["ok"] is True
    assert out["json_updated"] is False
    assert out["clipboard"] == "SETTEMPLATE:Books"


def test_camera_only_unknown_group(cfg):
    out = api.cmd_set_template(cfg, camera_only="nope")
    assert out["ok"] is False
    assert "unknown group key" in out["error"]


def test_unknown_group_key(cfg):
    out = api.cmd_set_template(cfg, group_key="nope", sku="tgw001")
    assert out["ok"] is False
    assert "available" in out


def test_no_group_key_errors(cfg):
    out = api.cmd_set_template(cfg)
    assert out["ok"] is False
    assert "group_key required" in out["error"]


def test_dry_run_does_not_write(cfg):
    path = _write_item(cfg, "tgw001", {"sku": "tgw001", "title": "X"})
    out = api.cmd_set_template(cfg, group_key="books", sku="tgw001", dry_run=True)
    assert out["ok"] is True
    assert out["dry_run"] is True
    assert out["would_write"]["category_group"] == "books"
    # File untouched.
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert "category_group" not in doc


def test_write_merges_fields_and_preserves_existing(cfg):
    path = _write_item(cfg, "tgw001",
                       {"sku": "tgw001", "title": "Old Book", "ai_hint": "rare"})
    out = api.cmd_set_template(cfg, group_key="books", sku="tgw001")
    assert out["ok"] is True
    assert out["clipboard"] == "SETTEMPLATE:Books"

    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["category_group"] == "books"
    assert doc["size_class"] == "small"
    assert doc["ai_hint"] == "printed book; rare"
    assert doc["ebay_category_id"] == "261186"
    assert doc["title"] == "Old Book"            # preserved
    assert "fulfillment_policy_id" not in doc     # reconcile


def test_sku_not_found(cfg):
    out = api.cmd_set_template(cfg, group_key="books", sku="tgw404")
    assert out["ok"] is False
    assert "item not found" in out["error"]


def test_current_item_fallback(cfg, monkeypatch):
    _write_item(cfg, "tgw777", {"sku": "tgw777"})
    monkeypatch.setattr(api, "_current_item_sku", lambda: "tgw777")
    out = api.cmd_set_template(cfg, group_key="books")  # no sku -> fallback
    assert out["ok"] is True
    assert out["sku"] == "tgw777"


def test_current_item_fallback_missing(cfg, monkeypatch):
    monkeypatch.setattr(api, "_current_item_sku", lambda: None)
    out = api.cmd_set_template(cfg, group_key="books")
    assert out["ok"] is False
    assert "CurrentItem" in out["error"]
