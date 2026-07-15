"""Tests for tgw.inventory_record — Set A ("Inventory Record") accessor
module (todo #1418, PP-LISTEDITOR-001, foundation for #1416/#1417).
"""

from tgw import inventory_record


def test_get_inventory_fields_legacy_bare_dict():
    """Pre-migration items still carry item_attributes as a bare dict —
    the accessor must read it transparently (back-compat during the long
    transition before the full-catalog migration runs)."""
    item = {"item_attributes": {"Type": "Brooch", "Brand": "Unbranded"}}
    assert inventory_record.get_inventory_fields(item) == {
        "Type": "Brooch", "Brand": "Unbranded",
    }


def test_get_inventory_fields_envelope_shape():
    item = {
        "item_attributes": {
            "_set": "inventory_record",
            "version": 1,
            "updated_at": "2026-07-15T00:00:00+00:00",
            "fields": {"Type": "Brooch"},
        }
    }
    assert inventory_record.get_inventory_fields(item) == {"Type": "Brooch"}


def test_get_inventory_fields_missing_or_none():
    assert inventory_record.get_inventory_fields({}) == {}
    assert inventory_record.get_inventory_fields({"item_attributes": None}) == {}


def test_get_inventory_field_single_key():
    item = {"item_attributes": {"Type": "Brooch"}}
    assert inventory_record.get_inventory_field(item, "Type") == "Brooch"
    assert inventory_record.get_inventory_field(item, "Brand", "default") == "default"


def test_wrap_inventory_attributes_shape():
    env = inventory_record.wrap_inventory_attributes({"Type": "Brooch"})
    assert env["_set"] == "inventory_record"
    assert env["version"] == 1
    assert env["fields"] == {"Type": "Brooch"}
    assert env["updated_at_backfilled"] is False
    assert "updated_at" in env


def test_wrap_inventory_attributes_backfilled_flag():
    env = inventory_record.wrap_inventory_attributes(
        {"Type": "Brooch"}, updated_at="2020-01-01T00:00:00+00:00", backfilled=True)
    assert env["updated_at"] == "2020-01-01T00:00:00+00:00"
    assert env["updated_at_backfilled"] is True


def test_is_envelope():
    assert inventory_record.is_envelope({"_set": "inventory_record", "fields": {}})
    assert not inventory_record.is_envelope({"Type": "Brooch"})
    assert not inventory_record.is_envelope(None)
    assert not inventory_record.is_envelope("not a dict")


def test_set_inventory_fields_new_item_no_prior_history():
    item = {}
    patch = inventory_record.set_inventory_fields(
        item, {"Type": "Brooch"}, source="ai_identify", applied_by="system")
    assert patch["item_attributes"]["fields"] == {"Type": "Brooch"}
    assert len(patch["item_attributes_history"]) == 1
    entry = patch["item_attributes_history"][0]
    assert entry["key"] == "Type"
    assert entry["value"] == "Brooch"
    assert entry["previous_value"] is None
    assert entry["source"] == "ai_identify"
    assert entry["applied_by"] == "system"


def test_set_inventory_fields_only_appends_history_on_real_change():
    item = {"item_attributes": {"Type": "Brooch"}}
    patch = inventory_record.set_inventory_fields(
        item, {"Type": "Brooch", "Brand": "Unbranded"}, source="test")
    # Type unchanged -> no history entry; Brand is new -> one entry
    assert len(patch["item_attributes_history"]) == 1
    assert patch["item_attributes_history"][0]["key"] == "Brand"
    assert patch["item_attributes"]["fields"] == {"Type": "Brooch", "Brand": "Unbranded"}


def test_set_inventory_fields_preserves_prior_history_append_only():
    item = {
        "item_attributes": {"Type": "Lapel Pin"},
        "item_attributes_history": [
            {"ts": "2026-01-01T00:00:00+00:00", "key": "Type", "value": "Lapel Pin",
             "previous_value": None, "source": "ai_identify", "applied_by": "system"},
        ],
    }
    patch = inventory_record.set_inventory_fields(
        item, {"Type": "Brooch"}, source="operator_patch", applied_by="operator")
    assert len(patch["item_attributes_history"]) == 2
    assert patch["item_attributes_history"][0]["value"] == "Lapel Pin"
    assert patch["item_attributes_history"][1]["value"] == "Brooch"
    assert patch["item_attributes_history"][1]["previous_value"] == "Lapel Pin"


def test_set_inventory_fields_none_value_is_noop_not_delete():
    item = {"item_attributes": {"Type": "Brooch"}}
    patch = inventory_record.set_inventory_fields(item, {"Type": None, "Brand": "X"}, source="test")
    assert patch["item_attributes"]["fields"] == {"Type": "Brooch", "Brand": "X"}
    assert len(patch["item_attributes_history"]) == 1
    assert patch["item_attributes_history"][0]["key"] == "Brand"


def test_set_inventory_fields_from_existing_envelope():
    item = {
        "item_attributes": {
            "_set": "inventory_record", "version": 1,
            "updated_at": "2026-01-01T00:00:00+00:00",
            "updated_at_backfilled": False,
            "fields": {"Type": "Lapel Pin"},
        }
    }
    patch = inventory_record.set_inventory_fields(item, {"Type": "Brooch"}, source="test")
    assert patch["item_attributes"]["fields"] == {"Type": "Brooch"}
    assert patch["item_attributes"]["_set"] == "inventory_record"
