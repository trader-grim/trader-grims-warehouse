"""Tests for tgw.ebay.inventory_diff — the reverse-flow (eBay Draft ->
Inventory Record) diff engine and gated apply function (todo #1417,
PP-LISTEDITOR-001, built on #1418's accessor modules).
"""

from tgw import inventory_record
from tgw.ebay import draft_specifics
from tgw.ebay.inventory_diff import apply_inventory_diff, diff_ebay_draft_to_inventory


def _envelope_item(inv_fields, ebay_fields, history=None):
    return {
        "item_attributes": inventory_record.wrap_inventory_attributes(inv_fields),
        "draft_listing": {
            "item_specifics": draft_specifics.wrap_ebay_specifics(ebay_fields),
            "item_specifics_history": history or [],
        },
    }


# ---------------------------------------------------------------------------
# diff_ebay_draft_to_inventory
# ---------------------------------------------------------------------------

def test_diff_flags_differing_key():
    item = _envelope_item({"Type": "Lapel Pin"}, {"Type": "Brooch"})
    diffs = diff_ebay_draft_to_inventory(item)
    assert len(diffs) == 1
    d = diffs[0]
    assert d["key"] == "Type"
    assert d["inventory_value"] == "Lapel Pin"
    assert d["ebay_value"] == "Brooch"


def test_diff_agreeing_key_not_flagged():
    item = _envelope_item({"Type": "Brooch"}, {"Type": "Brooch"})
    assert diff_ebay_draft_to_inventory(item) == []


def test_diff_key_only_in_set_b_has_none_inventory_value():
    """A new fact Set A never had (not just a correction) — spec point 1."""
    item = _envelope_item({}, {"Metal": "Silver"})
    diffs = diff_ebay_draft_to_inventory(item)
    assert len(diffs) == 1
    assert diffs[0]["key"] == "Metal"
    assert diffs[0]["inventory_value"] is None
    assert diffs[0]["ebay_value"] == "Silver"


def test_diff_key_only_in_set_a_not_part_of_diff():
    """Set A can legitimately hold universal facts no marketplace needs —
    not a discrepancy to resolve (spec point 1)."""
    item = _envelope_item({"Cost": "5.00"}, {})
    assert diff_ebay_draft_to_inventory(item) == []


def test_diff_source_from_history_entry():
    history = [{"ts": "2026-07-01T00:00:00+00:00", "key": "Type",
                "value": "Brooch", "previous_value": "Lapel Pin",
                "source": "accept_proposals", "applied_by": "operator"}]
    item = _envelope_item({"Type": "Lapel Pin"}, {"Type": "Brooch"}, history)
    diffs = diff_ebay_draft_to_inventory(item)
    assert diffs[0]["source"] == "accept_proposals"
    assert diffs[0]["detected_at"] == "2026-07-01T00:00:00+00:00"


def test_diff_source_defaults_to_ebay_draft_without_history():
    item = _envelope_item({"Type": "Lapel Pin"}, {"Type": "Brooch"})
    diffs = diff_ebay_draft_to_inventory(item)
    assert diffs[0]["source"] == "ebay_draft"


def test_diff_detected_at_none_for_legacy_bare_dict_no_timestamp():
    """Legacy bare-dict item — no envelope, no history — Prime Directive 1:
    never fabricate a detection timestamp."""
    item = {"item_attributes": {"Type": "Lapel Pin"},
            "draft_listing": {"item_specifics": {"Type": "Brooch"}}}
    diffs = diff_ebay_draft_to_inventory(item)
    assert diffs[0]["detected_at"] is None


def test_diff_pure_no_mutation():
    item = _envelope_item({"Type": "Lapel Pin"}, {"Type": "Brooch"})
    before = str(item)
    diff_ebay_draft_to_inventory(item)
    assert str(item) == before


# ---------------------------------------------------------------------------
# apply_inventory_diff
# ---------------------------------------------------------------------------

def test_apply_writes_only_checked_keys():
    history = [
        {"ts": "2026-07-01T00:00:00+00:00", "key": "Type", "value": "Brooch",
         "previous_value": "Lapel Pin", "source": "ebay_draft", "applied_by": "system"},
        {"ts": "2026-07-01T00:00:00+00:00", "key": "Metal", "value": "Silver",
         "previous_value": None, "source": "ebay_draft", "applied_by": "system"},
    ]
    item = _envelope_item({"Type": "Lapel Pin"}, {"Type": "Brooch", "Metal": "Silver"}, history)
    patch = apply_inventory_diff(item, ["Type"])
    assert patch["item_attributes"]["fields"]["Type"] == "Brooch"
    assert "Metal" not in patch["item_attributes"]["fields"]
    assert patch["applied_keys"] == ["Type"]


def test_apply_records_provenance():
    history = [{"ts": "2026-07-01T00:00:00+00:00", "key": "Type", "value": "Brooch",
                "previous_value": "Lapel Pin", "source": "ebay_draft", "applied_by": "system"}]
    item = _envelope_item({"Type": "Lapel Pin"}, {"Type": "Brooch"}, history)
    patch = apply_inventory_diff(item, ["Type"])
    entry = patch["item_attributes_history"][-1]
    assert entry["key"] == "Type"
    assert entry["value"] == "Brooch"
    assert entry["previous_value"] == "Lapel Pin"
    assert entry["source"] == "ebay_draft"
    assert entry["applied_by"] == "operator"
    assert entry["detected_at"] == "2026-07-01T00:00:00+00:00"
    assert "ts" in entry  # applied_at, from set_inventory_fields


def test_apply_idempotent_no_op_when_key_no_longer_diverges():
    """Idempotency (spec point 5): a stale UI checkbox for an already-
    resolved key is a silent no-op, not an error."""
    item = _envelope_item({"Type": "Brooch"}, {"Type": "Brooch"})
    patch = apply_inventory_diff(item, ["Type"])
    assert patch == {}


def test_apply_reresurfaces_unchecked_diff_next_call():
    """spec point 5's chosen behavior: an unapplied/unchecked diff is not
    sticky-dismissed — it simply reappears on the next diff call because
    Set A/Set B still genuinely disagree. See this packet's result
    manifest for the explicit design confirmation."""
    item = _envelope_item({"Type": "Lapel Pin"}, {"Type": "Brooch", "Metal": "Silver"})
    patch = apply_inventory_diff(item, ["Type"])
    item2 = dict(item)
    item2["item_attributes"] = patch["item_attributes"]
    item2["item_attributes_history"] = patch["item_attributes_history"]
    remaining = diff_ebay_draft_to_inventory(item2)
    assert {d["key"] for d in remaining} == {"Metal"}


def test_apply_ignores_requested_key_not_in_diff():
    item = _envelope_item({"Brand": "Unbranded"}, {"Brand": "Unbranded"})
    patch = apply_inventory_diff(item, ["Brand"])
    assert patch == {}


def test_apply_groups_by_differing_source():
    history = [
        {"ts": "2026-07-01T00:00:00+00:00", "key": "Type", "value": "Brooch",
         "previous_value": "Lapel Pin", "source": "ebay_draft", "applied_by": "system"},
        {"ts": "2026-07-02T00:00:00+00:00", "key": "Metal", "value": "Silver",
         "previous_value": None, "source": "accept_proposals", "applied_by": "operator"},
    ]
    item = _envelope_item({"Type": "Lapel Pin"}, {"Type": "Brooch", "Metal": "Silver"}, history)
    patch = apply_inventory_diff(item, ["Type", "Metal"])
    by_key = {e["key"]: e for e in patch["item_attributes_history"]}
    assert by_key["Type"]["source"] == "ebay_draft"
    assert by_key["Metal"]["source"] == "accept_proposals"
    assert patch["item_attributes"]["fields"] == {"Type": "Brooch", "Metal": "Silver"}


def test_apply_pure_no_mutation():
    item = _envelope_item({"Type": "Lapel Pin"}, {"Type": "Brooch"})
    before = str(item)
    apply_inventory_diff(item, ["Type"])
    assert str(item) == before
