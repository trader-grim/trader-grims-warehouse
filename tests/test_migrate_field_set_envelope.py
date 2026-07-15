"""Tests for scripts/migrate_field_set_envelope.py (todo #1418).

Exercises plan_item()/run() as pure functions — no real ItemData I/O
(the live dry-run + sample verification against real data is the
Acceptance step, run separately and recorded in the result manifest).
"""

import importlib.util
import sys
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "migrate_field_set_envelope.py"
_spec = importlib.util.spec_from_file_location("migrate_field_set_envelope", _SCRIPT_PATH)
migrate_field_set_envelope = importlib.util.module_from_spec(_spec)
sys.modules["migrate_field_set_envelope"] = migrate_field_set_envelope
_spec.loader.exec_module(migrate_field_set_envelope)

plan_item = migrate_field_set_envelope.plan_item
_round_trip_ok = migrate_field_set_envelope._round_trip_ok
_best_known_timestamp = migrate_field_set_envelope._best_known_timestamp


def test_plan_item_bare_dict_both_sets():
    doc = {
        "sku": "tgw202607150000001",
        "item_attributes": {"Type": "Brooch", "Brand": "Unbranded"},
        "draft_listing": {"item_specifics": {"Type": "Brooch"}},
    }
    plan = plan_item(doc)
    assert plan["needs_a"] is True
    assert plan["needs_b"] is True
    assert plan["patch"]["item_attributes"]["fields"] == doc["item_attributes"]
    assert plan["patch"]["draft_listing"]["item_specifics"]["fields"] == {"Type": "Brooch"}
    assert plan["patch"]["item_attributes"]["updated_at_backfilled"] is True
    assert plan["patch"]["draft_listing"]["item_specifics"]["updated_at_backfilled"] is True
    assert _round_trip_ok(plan)


def test_plan_item_already_enveloped_is_noop():
    doc = {
        "sku": "tgw202607150000002",
        "item_attributes": {
            "_set": "inventory_record", "version": 1,
            "updated_at": "2026-07-15T00:00:00+00:00",
            "fields": {"Type": "Brooch"},
        },
        "draft_listing": {
            "item_specifics": {
                "_set": "ebay_draft", "version": 1,
                "updated_at": "2026-07-15T00:00:00+00:00",
                "fields": {"Type": "Brooch"},
            }
        },
    }
    plan = plan_item(doc)
    assert plan["needs_a"] is False
    assert plan["needs_b"] is False
    assert plan["patch"] == {}


def test_plan_item_no_data_present():
    doc = {"sku": "tgw202607150000003"}
    plan = plan_item(doc)
    assert plan["needs_a"] is False
    assert plan["needs_b"] is False


def test_plan_item_preserves_other_draft_listing_keys():
    doc = {
        "sku": "tgw202607150000004",
        "draft_listing": {
            "title": "A vintage brooch",
            "price": 12.5,
            "item_specifics": {"Type": "Brooch"},
        },
    }
    plan = plan_item(doc)
    assert plan["patch"]["draft_listing"]["title"] == "A vintage brooch"
    assert plan["patch"]["draft_listing"]["price"] == 12.5


def test_plan_item_history_starts_empty_no_fabrication():
    doc = {"sku": "tgw202607150000005", "item_attributes": {"Type": "Brooch"}}
    plan = plan_item(doc)
    assert plan["patch"]["item_attributes_history"] == []


def test_best_known_timestamp_priority_order():
    assert _best_known_timestamp({"baseline_at": "T1",
                                   "ebay_listing": {"synced_at": "T2"}}) == "T1"
    assert _best_known_timestamp({"ebay_listing": {"synced_at": "T2"},
                                   "ebay_offer": {"staged_at": "T3"}}) == "T2"
    assert _best_known_timestamp({"ebay_offer": {"staged_at": "T3"}}) == "T3"
    assert _best_known_timestamp(
        {"price_history": [{"ts": "T4"}, {"ts": "T5"}]}) == "T5"
    assert _best_known_timestamp({}) is None


def test_round_trip_ok_detects_corruption():
    doc = {"item_attributes": {"Type": "Brooch"}}
    plan = plan_item(doc)
    # Simulate corruption: mutate the planned fields after the fact
    plan["patch"]["item_attributes"]["fields"]["Type"] = "CORRUPTED"
    assert _round_trip_ok(plan) is False
