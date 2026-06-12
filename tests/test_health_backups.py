"""Tests for check_backups() — PP-BACKUP-001 A4."""

from __future__ import annotations

import time
from pathlib import Path

from tgw.health import check_backups  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(tmp_path: Path) -> dict:
    db_dir = tmp_path / "db"
    snap_root = tmp_path / "snapshot"
    secrets_dir = tmp_path / "secrets"
    stamp = tmp_path / "rclone-last-success"
    db_dir.mkdir()
    snap_root.mkdir()
    secrets_dir.mkdir()
    return {
        "backup_db_dir": db_dir,
        "backup_snapshot_root": snap_root,
        "backup_secrets_dir": secrets_dir,
        "backup_rclone_stamp": stamp,
    }


def _touch(path: Path, age_seconds: float = 0.0) -> None:
    path.touch()
    t = time.time() - age_seconds
    import os
    os.utime(path, (t, t))


# ---------------------------------------------------------------------------
# Green path — everything fresh
# ---------------------------------------------------------------------------

def test_all_fresh(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _touch(cfg["backup_db_dir"] / "state_machine-20260611.dump", age_seconds=3600)
    _touch(cfg["backup_rclone_stamp"], age_seconds=3600)
    (cfg["backup_snapshot_root"] / "bin").mkdir(exist_ok=True)
    _touch(cfg["backup_snapshot_root"] / "bin" / "tgw", age_seconds=300)
    _touch(cfg["backup_secrets_dir"] / "secrets-20260601.tar.gz.gpg", age_seconds=86400 * 10)

    result = check_backups(cfg)

    assert result["ok"] is True
    assert result.get("warn") is None
    assert result["issues"] == []
    assert result["warnings"] == []


# ---------------------------------------------------------------------------
# Red conditions (ok=False)
# ---------------------------------------------------------------------------

def test_no_dump_is_red(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _touch(cfg["backup_rclone_stamp"], age_seconds=3600)
    _touch(cfg["backup_secrets_dir"] / "secrets-20260601.tar.gz.gpg", age_seconds=86400)

    result = check_backups(cfg)

    assert result["ok"] is False
    assert any("no db dump" in i for i in result["issues"])


def test_stale_dump_is_red(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _touch(cfg["backup_db_dir"] / "state_machine-20260610.dump", age_seconds=30 * 3600)
    _touch(cfg["backup_rclone_stamp"], age_seconds=3600)
    _touch(cfg["backup_secrets_dir"] / "secrets-20260601.tar.gz.gpg", age_seconds=86400)

    result = check_backups(cfg)

    assert result["ok"] is False
    assert any("dump stale" in i for i in result["issues"])


def test_missing_rclone_stamp_is_red(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _touch(cfg["backup_db_dir"] / "state_machine-20260611.dump", age_seconds=3600)
    # stamp file intentionally absent
    _touch(cfg["backup_secrets_dir"] / "secrets-20260601.tar.gz.gpg", age_seconds=86400)

    result = check_backups(cfg)

    assert result["ok"] is False
    assert any("stamp absent" in i or "never completed" in i for i in result["issues"])


def test_stale_rclone_stamp_is_red(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _touch(cfg["backup_db_dir"] / "state_machine-20260610.dump", age_seconds=3600)
    _touch(cfg["backup_rclone_stamp"], age_seconds=30 * 3600)
    _touch(cfg["backup_secrets_dir"] / "secrets-20260601.tar.gz.gpg", age_seconds=86400)

    result = check_backups(cfg)

    assert result["ok"] is False
    assert any("sync stale" in i for i in result["issues"])


# ---------------------------------------------------------------------------
# Yellow conditions (ok=True, warn=True)
# ---------------------------------------------------------------------------

def test_stale_snapshot_tree_is_yellow(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _touch(cfg["backup_db_dir"] / "state_machine-20260611.dump", age_seconds=3600)
    _touch(cfg["backup_rclone_stamp"], age_seconds=3600)
    snap_sub = cfg["backup_snapshot_root"] / "bin"
    snap_sub.mkdir()
    _touch(snap_sub / "tgw", age_seconds=7200)  # 2 h > 1 h limit
    _touch(cfg["backup_secrets_dir"] / "secrets-20260601.tar.gz.gpg", age_seconds=86400)

    result = check_backups(cfg)

    assert result["ok"] is True
    assert result.get("warn") is True
    assert any("snapshot tree stale" in w for w in result["warnings"])


def test_stale_secrets_bundle_is_yellow(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _touch(cfg["backup_db_dir"] / "state_machine-20260611.dump", age_seconds=3600)
    _touch(cfg["backup_rclone_stamp"], age_seconds=3600)
    _touch(cfg["backup_secrets_dir"] / "secrets-20260401.tar.gz.gpg", age_seconds=86400 * 50)  # 50d

    result = check_backups(cfg)

    assert result["ok"] is True
    assert result.get("warn") is True
    assert any("secrets bundle stale" in w for w in result["warnings"])


def test_no_secrets_bundle_is_yellow(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _touch(cfg["backup_db_dir"] / "state_machine-20260611.dump", age_seconds=3600)
    _touch(cfg["backup_rclone_stamp"], age_seconds=3600)
    # no bundle files

    result = check_backups(cfg)

    assert result["ok"] is True
    assert result.get("warn") is True
    assert any("no encrypted secrets bundle" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# Both red and yellow simultaneously
# ---------------------------------------------------------------------------

def test_red_and_yellow_together(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    # red: stale dump
    _touch(cfg["backup_db_dir"] / "state_machine-20260610.dump", age_seconds=30 * 3600)
    _touch(cfg["backup_rclone_stamp"], age_seconds=3600)
    # yellow: stale secrets bundle
    _touch(cfg["backup_secrets_dir"] / "secrets-20260401.tar.gz.gpg", age_seconds=86400 * 50)

    result = check_backups(cfg)

    assert result["ok"] is False
    assert result.get("warn") is True
    assert result["issues"]
    assert result["warnings"]


# ---------------------------------------------------------------------------
# Detail string sanity
# ---------------------------------------------------------------------------

def test_detail_all_ok(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _touch(cfg["backup_db_dir"] / "state_machine-20260611.dump", age_seconds=3600)
    _touch(cfg["backup_rclone_stamp"], age_seconds=3600)
    snap_sub = cfg["backup_snapshot_root"] / "data"
    snap_sub.mkdir()
    _touch(snap_sub / "file.json", age_seconds=300)
    _touch(cfg["backup_secrets_dir"] / "secrets-20260601.tar.gz.gpg", age_seconds=86400 * 5)

    result = check_backups(cfg)

    assert result["ok"] is True
    assert "fresh" in result["detail"]
