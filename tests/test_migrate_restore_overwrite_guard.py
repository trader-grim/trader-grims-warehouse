"""todo #1300 / PP-COHESION-001 — `tgw migrate-restore` must refuse to
overwrite a live item JSON that already exists at the restore destination
unless --force is passed, and its write must go through atomic_write_json's
archive_root fence like every other item-JSON write path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

from tgw.api import main


def _cfg(tmp_path):
    itemdata_root = tmp_path / "ItemData"
    archive_root = tmp_path / "archive"
    itemdata_root.mkdir(parents=True, exist_ok=True)
    return {
        "itemdata_root": itemdata_root,
        "archive_root": archive_root,
        "postgres_dsn": "postgresql://unused/for-test",
        "pretty": True,
    }


def _make_archive_snapshot(archive_dir: Path, old_sku: str, **fields):
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / f"{old_sku}.json").write_text(
        json.dumps({"sku": old_sku, **fields}), encoding="utf-8"
    )


def _run_migrate_restore(monkeypatch, tmp_path, cfg, args_tail, sku_history_row=None):
    monkeypatch.setattr(sys, "argv", ["tgw", "migrate-restore", *args_tail])

    class _FakeCursor:
        def execute(self, *a, **k):
            pass

        def fetchone(self):
            return sku_history_row

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _FakeConn:
        def cursor(self):
            return _FakeCursor()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch("tgw.api.load_config", return_value=cfg), \
         patch("tgw.queue.state_machine.init"), \
         patch("psycopg2.connect", return_value=_FakeConn()):
        return main()


def test_migrate_restore_refuses_existing_destination_without_force(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path)
    old_sku = "tgw000000000000010"
    _make_archive_snapshot(cfg["archive_root"] / "migrate", old_sku, title="Archived Item")

    with patch("tgw.sku_migration._MIGRATE_ARCHIVE", cfg["archive_root"] / "migrate"):
        # Live item already exists at the (no-rename) destination path.
        live_dir = cfg["itemdata_root"] / old_sku
        live_dir.mkdir(parents=True, exist_ok=True)
        live_json = live_dir / f"{old_sku}.json"
        live_json.write_text(json.dumps({"sku": old_sku, "title": "Live item — created after snapshot"}))

        rc = _run_migrate_restore(monkeypatch, tmp_path, cfg, [old_sku])

    assert rc == 1
    out = capsys.readouterr().out
    assert "refusing to overwrite" in out.lower()
    # The live item must be untouched.
    assert json.loads(live_json.read_text())["title"] == "Live item — created after snapshot"


def test_migrate_restore_force_overwrites_and_uses_archive_root(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path)
    old_sku = "tgw000000000000011"
    _make_archive_snapshot(cfg["archive_root"] / "migrate", old_sku, title="Archived Item")

    with patch("tgw.sku_migration._MIGRATE_ARCHIVE", cfg["archive_root"] / "migrate"), \
         patch("tgw.items.atomic_write_json") as mock_awj:
        live_dir = cfg["itemdata_root"] / old_sku
        live_dir.mkdir(parents=True, exist_ok=True)
        live_json = live_dir / f"{old_sku}.json"
        live_json.write_text(json.dumps({"sku": old_sku, "title": "stale live item"}))

        rc = _run_migrate_restore(monkeypatch, tmp_path, cfg, [old_sku, "--force"])

    assert rc == 0
    assert mock_awj.called
    _, kwargs = mock_awj.call_args
    assert kwargs.get("archive_root") == cfg["archive_root"]


def test_migrate_restore_succeeds_when_destination_absent(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path)
    old_sku = "tgw000000000000012"
    _make_archive_snapshot(cfg["archive_root"] / "migrate", old_sku, title="Archived Item")

    with patch("tgw.sku_migration._MIGRATE_ARCHIVE", cfg["archive_root"] / "migrate"):
        (cfg["itemdata_root"] / old_sku).mkdir(parents=True, exist_ok=True)
        rc = _run_migrate_restore(monkeypatch, tmp_path, cfg, [old_sku])

    assert rc == 0
    restored = json.loads((cfg["itemdata_root"] / old_sku / f"{old_sku}.json").read_text())
    assert restored["title"] == "Archived Item"
