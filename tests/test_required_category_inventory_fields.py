from tgw.inventory_record import (
    ensure_required_category_fields,
    get_inventory_fields,
)


def test_required_category_fields_are_scaffolded_empty_in_inventory_record():
    item = {"item_attributes": {"Color": "Red"}}

    patch = ensure_required_category_fields(
        item,
        [
            {"name": "Brand", "required": True},
            {"name": "Format", "required": True},
            {"name": "Color", "required": False},
        ],
    )

    assert patch["item_attributes"]["fields"] == {
        "Color": "Red",
        "Brand": "",
        "Format": "",
    }
    assert {entry["key"] for entry in patch["item_attributes_history"]} == {
        "Brand", "Format",
    }
    assert {entry["source"] for entry in patch["item_attributes_history"]} == {
        "ebay_category_schema",
    }


def test_required_category_scaffold_preserves_existing_values_and_empty_keys():
    item = {
        "item_attributes": {
            "_set": "inventory_record",
            "version": 1,
            "fields": {"Brand": "Columbia", "Format": ""},
        },
    }

    assert ensure_required_category_fields(
        item,
        [{"name": "Brand", "required": True}, {"name": "Format", "required": True}],
    ) == {}
    assert get_inventory_fields(item) == {"Brand": "Columbia", "Format": ""}
