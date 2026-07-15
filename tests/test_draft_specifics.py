"""Tests for tgw.ebay.draft_specifics — Set B ("eBay Draft") accessor
module (todo #1418, PP-LISTEDITOR-001, foundation for #1416/#1417).
"""

from tgw.ebay import draft_specifics


def test_get_ebay_aspects_legacy_bare_dict():
    item = {"draft_listing": {"item_specifics": {"Type": "Brooch"}}}
    assert draft_specifics.get_ebay_aspects(item) == {"Type": "Brooch"}


def test_get_ebay_aspects_envelope_shape():
    item = {
        "draft_listing": {
            "item_specifics": {
                "_set": "ebay_draft", "version": 1,
                "updated_at": "2026-07-15T00:00:00+00:00",
                "fields": {"Type": "Brooch"},
            }
        }
    }
    assert draft_specifics.get_ebay_aspects(item) == {"Type": "Brooch"}


def test_get_ebay_aspects_missing_draft_listing():
    assert draft_specifics.get_ebay_aspects({}) == {}
    assert draft_specifics.get_ebay_aspects({"draft_listing": None}) == {}
    assert draft_specifics.get_ebay_aspects({"draft_listing": {}}) == {}


def test_get_ebay_aspect_single_key():
    item = {"draft_listing": {"item_specifics": {"Type": "Brooch"}}}
    assert draft_specifics.get_ebay_aspect(item, "Type") == "Brooch"
    assert draft_specifics.get_ebay_aspect(item, "Brand", "x") == "x"


def test_wrap_ebay_specifics_shape():
    env = draft_specifics.wrap_ebay_specifics({"Type": "Brooch"})
    assert env["_set"] == "ebay_draft"
    assert env["version"] == 1
    assert env["fields"] == {"Type": "Brooch"}
    assert env["updated_at_backfilled"] is False


def test_is_envelope_distinguishes_from_inventory_record():
    """Set A and Set B envelopes must be distinguishable by _set alone —
    this is the whole point of the tag (grep-discoverable, per-set)."""
    b_env = draft_specifics.wrap_ebay_specifics({"Type": "Brooch"})
    assert draft_specifics.is_envelope(b_env)
    a_shaped = {"_set": "inventory_record", "fields": {}}
    assert not draft_specifics.is_envelope(a_shaped)


def test_set_ebay_aspects_records_history():
    item = {"draft_listing": {"item_specifics": {"Type": "Lapel Pin"}}}
    patch = draft_specifics.set_ebay_aspects(item, {"Type": "Brooch"}, source="ebay_draft")
    assert patch["item_specifics"]["fields"] == {"Type": "Brooch"}
    assert len(patch["item_specifics_history"]) == 1
    entry = patch["item_specifics_history"][0]
    assert entry["previous_value"] == "Lapel Pin"
    assert entry["value"] == "Brooch"
    assert entry["source"] == "ebay_draft"


def test_set_ebay_aspects_preserves_prior_history_append_only():
    item = {
        "draft_listing": {
            "item_specifics": {"Type": "Lapel Pin"},
            "item_specifics_history": [
                {"ts": "2026-01-01T00:00:00+00:00", "key": "Type",
                 "value": "Lapel Pin", "previous_value": None,
                 "source": "ebay_draft", "applied_by": "system"},
            ],
        }
    }
    patch = draft_specifics.set_ebay_aspects(item, {"Type": "Brooch"}, source="ebay_draft")
    assert len(patch["item_specifics_history"]) == 2
    assert patch["item_specifics_history"][0]["value"] == "Lapel Pin"
    assert patch["item_specifics_history"][1]["value"] == "Brooch"


def test_set_ebay_aspects_no_change_no_history_entry():
    item = {"draft_listing": {"item_specifics": {"Type": "Brooch"}}}
    patch = draft_specifics.set_ebay_aspects(item, {"Type": "Brooch"}, source="ebay_draft")
    assert patch["item_specifics_history"] == []
    assert patch["item_specifics"]["fields"] == {"Type": "Brooch"}
