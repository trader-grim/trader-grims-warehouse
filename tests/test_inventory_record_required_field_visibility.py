from pathlib import Path


def test_inventory_record_renders_empty_required_schema_fields_without_blank_add_action():
    source = (Path(__file__).parents[1] / "src" / "tgw" / "http_server.py").read_text()
    inventory_panel = source[
        source.index("Inventory Record specifics"):
        source.index("# ── eBay -> Inventory Record sync panel")
    ]
    assert "for k, v in sorted(ia.items())\n" in inventory_panel
    assert 'if v and k not in isp and k != "Title"' in inventory_panel
