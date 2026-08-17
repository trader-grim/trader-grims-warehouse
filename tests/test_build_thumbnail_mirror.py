from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from PIL import Image


def _script_module():
    path = Path(__file__).parents[1] / "scripts" / "build_thumbnail_mirror.py"
    spec = importlib.util.spec_from_file_location("build_thumbnail_mirror", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _image(path: Path, color: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1600, 800), color=color).save(path)


def test_mirror_keeps_sku_and_full_name_for_same_stem_extensions(tmp_path: Path) -> None:
    mod = _script_module()
    source, destination = tmp_path / "ItemData", tmp_path / "ItemCatalog" / "media"
    _image(source / "tgw001" / "photo.jpg", "red")
    _image(source / "tgw001" / "photo.png", "blue")
    _image(source / "tgw001" / "detail" / "photo.jpg", "green")
    records = mod.build_mirror(source, destination, max_size=(256, 256), quality=85, force=False, dry_run=False, limit=None)
    generated = [record for record in records if record.action == "generated"]
    assert [Path(record.destination).relative_to(destination).as_posix() for record in generated] == ["tgw001/detail/photo.jpg.jpg", "tgw001/photo.jpg.jpg", "tgw001/photo.png.jpg"]
    for record in generated:
        with Image.open(record.destination) as thumbnail:
            assert thumbnail.format == "JPEG"
            assert max(thumbnail.size) == 256


def test_cli_dry_run_creates_no_mirror_and_writes_manifest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mod = _script_module()
    source, destination, manifest = tmp_path / "ItemData", tmp_path / "mirror", tmp_path / "report.jsonl"
    _image(source / "tgw002" / "image.jpg", "red")
    assert mod.main(["--source", str(source), "--destination", str(destination), "--manifest", str(manifest), "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["records"] == 1
    assert not destination.exists()
    assert json.loads(manifest.read_text())["action"] == "would_generate"
