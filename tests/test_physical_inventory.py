"""todo #1482 / PP-INVENTORY-001 manual leg — manifest-vs-physical checklist
workflow. Covers manifest generation (location -> expected-contents query)
and the operator adjudication write path (present / missing / misfiled).
"""

import json

import tgw.physical_inventory as physical_inventory


def _cfg(tmp_path):
    itemdata_root = tmp_path / "ItemData"
    location_tree_root = tmp_path / "LocationTree"
    itemdata_root.mkdir(parents=True, exist_ok=True)
    return {
        "itemdata_root": itemdata_root,
        "location_tree_root": location_tree_root,
        "archive_root": tmp_path / "Archive",
    }


def _make_item(cfg, sku, location, **extra):
    d = cfg["itemdata_root"] / sku
    d.mkdir(parents=True, exist_ok=True)
    doc = {"sku": sku, "location": location, "title": f"Widget {sku}"}
    doc.update(extra)
    (d / f"{sku}.json").write_text(json.dumps(doc))


def _load(cfg, sku):
    return json.loads((cfg["itemdata_root"] / sku / f"{sku}.json").read_text())


def test_build_manifest_scopes_to_one_location(tmp_path):
    cfg = _cfg(tmp_path)
    _make_item(cfg, "tgw000000000000001", "SAT013")
    _make_item(cfg, "tgw000000000000002", "SAT013")
    _make_item(cfg, "tgw000000000000003", "SAT099")

    manifest = physical_inventory.build_manifest(cfg, "SAT013")

    skus = {m["sku"] for m in manifest}
    assert skus == {"tgw000000000000001", "tgw000000000000002"}
    assert all(m["last_result"] is None for m in manifest)


def test_build_manifest_empty_location(tmp_path):
    cfg = _cfg(tmp_path)
    _make_item(cfg, "tgw000000000000001", "SAT013")

    manifest = physical_inventory.build_manifest(cfg, "NOWHERE")

    assert manifest == []


def test_inventory_sweep_checklist_writes_markdown(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    _make_item(cfg, "tgw000000000000001", "SAT013")

    out_path = tmp_path / "checklist.md"
    result = physical_inventory.inventory_sweep_checklist(cfg, "SAT013", output=out_path)

    assert result["ok"] is True
    assert result["count"] == 1
    assert out_path.exists()
    content = out_path.read_text()
    assert "SAT013" in content
    assert "tgw000000000000001" in content


def test_inventory_sweep_checklist_stdout_when_no_output(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    _make_item(cfg, "tgw000000000000001", "SAT013")

    result = physical_inventory.inventory_sweep_checklist(cfg, "SAT013")

    assert result["ok"] is True
    captured = capsys.readouterr()
    assert "tgw000000000000001" in captured.out


def test_inventory_record_present_persists_finding(tmp_path):
    cfg = _cfg(tmp_path)
    _make_item(cfg, "tgw000000000000001", "SAT013")

    result = physical_inventory.inventory_record(
        cfg, "tgw000000000000001", "present", location="SAT013"
    )

    assert result["ok"] is True
    doc = _load(cfg, "tgw000000000000001")
    assert doc["inventory_sweep"]["result"] == "present"
    assert doc["inventory_sweep"]["location_at_check"] == "SAT013"
    # location field itself is untouched on a present result
    assert doc["location"] == "SAT013"


def test_inventory_record_missing_does_not_touch_status(tmp_path):
    cfg = _cfg(tmp_path)
    _make_item(cfg, "tgw000000000000001", "SAT013", status="in stock")

    result = physical_inventory.inventory_record(
        cfg, "tgw000000000000001", "missing", location="SAT013", note="not on shelf"
    )

    assert result["ok"] is True
    doc = _load(cfg, "tgw000000000000001")
    assert doc["inventory_sweep"]["result"] == "missing"
    assert doc["inventory_sweep"]["note"] == "not on shelf"
    # missing is a durable finding, not an automatic status change (Prime
    # Directive 3 — no silent substitution of "missing" for "sold")
    assert doc["status"] == "in stock"


def test_inventory_record_misfiled_requires_to_location(tmp_path):
    cfg = _cfg(tmp_path)
    _make_item(cfg, "tgw000000000000001", "SAT013")

    result = physical_inventory.inventory_record(
        cfg, "tgw000000000000001", "misfiled", location="SAT013"
    )

    assert result["ok"] is False
    assert "to-location" in result["error"] or "to_location" in result["error"]


def test_inventory_record_misfiled_corrects_location(tmp_path):
    cfg = _cfg(tmp_path)
    _make_item(cfg, "tgw000000000000001", "SAT013")

    result = physical_inventory.inventory_record(
        cfg,
        "tgw000000000000001",
        "misfiled",
        location="SAT013",
        to_location="SAT099",
        note="found in the wrong bin",
    )

    assert result["ok"] is True
    doc = _load(cfg, "tgw000000000000001")
    assert doc["location"] == "SAT099"
    assert doc["inventory_sweep"]["result"] == "misfiled"
    assert doc["inventory_sweep"]["corrected_to"] == "SAT099"

    # symlink tree updated to match — old link gone, new link present
    assert not (cfg["location_tree_root"] / "SAT013" / "tgw000000000000001").exists()
    assert (cfg["location_tree_root"] / "SAT099" / "tgw000000000000001").exists()


def test_inventory_record_unknown_sku(tmp_path):
    cfg = _cfg(tmp_path)

    result = physical_inventory.inventory_record(cfg, "tgw000000000000099", "present")

    assert result["ok"] is False
    assert "not found" in result["error"]


def test_inventory_record_invalid_result(tmp_path):
    cfg = _cfg(tmp_path)
    _make_item(cfg, "tgw000000000000001", "SAT013")

    result = physical_inventory.inventory_record(cfg, "tgw000000000000001", "bogus")

    assert result["ok"] is False
