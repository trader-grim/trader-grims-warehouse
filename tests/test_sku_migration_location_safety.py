"""todo #1284 / PP-COHESION-001 — rename_sku()'s location-symlink update
must route link_dir construction through the hardened config.location_dir()
(todo #1274) instead of a raw location_tree_root / location join, matching
the same fix shape already applied to catalog.build_location_tree() (#1275).

A malformed/malicious `location` value must not let rename_sku() create a
symlink outside location_tree_root, and must not abort the rest of the
rename (the SKU/JSON rewrite already completed by that point in the
function) — it should log a warning and skip the symlink update only.
"""

import json
from contextlib import contextmanager

import tgw.sku_migration as sku_migration


class _FakeCursor:
    def execute(self, *a, **k):
        pass


class _FakeConn:
    def cursor(self):
        return contextlib_cm(_FakeCursor())

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def contextlib_cm(obj):
    @contextmanager
    def _cm():
        yield obj
    return _cm()


def _cfg(tmp_path):
    itemdata_root = tmp_path / "ItemData"
    location_tree_root = tmp_path / "LocationTree"
    itemdata_root.mkdir(parents=True, exist_ok=True)
    return {
        "itemdata_root": itemdata_root,
        "location_tree_root": location_tree_root,
        "postgres_dsn": "postgresql://unused/for-test",
        "pretty": True,
    }


def _make_item(cfg, sku, location):
    d = cfg["itemdata_root"] / sku
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sku}.json").write_text(json.dumps({"sku": sku, "location": location}))
    return d


def _patched(tmp_path, monkeypatch):
    # Never touch the real /opt/TGW/var/migrate-archive during tests.
    monkeypatch.setattr(sku_migration, "_MIGRATE_ARCHIVE", tmp_path / "migrate-archive")
    monkeypatch.setattr(sku_migration.psycopg2, "connect", lambda dsn: _FakeConn())
    monkeypatch.setattr(sku_migration.state_machine, "enqueue_job", lambda **k: None)


def test_valid_location_symlink_updated_no_warning(tmp_path, monkeypatch, caplog):
    cfg = _cfg(tmp_path)
    _patched(tmp_path, monkeypatch)
    old_sku, new_sku = "tgw000000000000001", "tgw000000000000002"
    _make_item(cfg, old_sku, "SAT013")
    (cfg["location_tree_root"] / "SAT013").mkdir(parents=True, exist_ok=True)

    with caplog.at_level("WARNING"):
        out = sku_migration.rename_sku(cfg, old_sku, new_sku, cls="A", dry_run=False)

    assert out["ok"] is True
    link = cfg["location_tree_root"] / "SAT013" / new_sku
    assert link.is_symlink()
    assert link.resolve() == (cfg["itemdata_root"] / new_sku).resolve()
    assert "unsafe location" not in caplog.text


def test_malicious_location_rejected_not_escaped(tmp_path, monkeypatch, caplog):
    cfg = _cfg(tmp_path)
    _patched(tmp_path, monkeypatch)
    old_sku, new_sku = "tgw000000000000003", "tgw000000000000004"
    _make_item(cfg, old_sku, "../../../tmp/evil")
    evil_target = tmp_path / "tmp" / "evil"

    with caplog.at_level("WARNING"):
        out = sku_migration.rename_sku(cfg, old_sku, new_sku, cls="A", dry_run=False)

    # SKU rename itself still succeeds — not rolled back over a bad location.
    assert out["ok"] is True
    new_dir = cfg["itemdata_root"] / new_sku
    assert new_dir.exists()
    assert (new_dir / f"{new_sku}.json").exists()

    # No symlink escaped location_tree_root.
    assert not evil_target.exists()

    # Warning logged, not a raised/unhandled exception.
    assert "unsafe location" in caplog.text
    assert "rename_sku" in caplog.text
