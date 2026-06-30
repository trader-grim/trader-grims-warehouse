"""PP-PORTABLE-CATALOG-001 Phase 1+2 — tests for the portable catalog export.

Pure: a tiny tmp sqlite db + a tmp thumbnails dir are built per test, exported
to a tmp dest, and assertions run on the returned dict and the filesystem.
Nothing touches the real catalog or thumbnail store.

Phase 2 additions: Syncthing push trigger + syncthing_pushed/syncthing_error
fields in the result dict.
"""

import sqlite3

import tgw.catalog_export as ce


def _make_db(path):
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE catalog (sku TEXT PRIMARY KEY, title TEXT)")
        con.executemany("INSERT INTO catalog (sku, title) VALUES (?, ?)",
                        [("tgw001", "Alpha"), ("tgw002", "Beta")])
        con.commit()
    finally:
        con.close()


def _make_thumbs(thumb_root, n=5):
    thumb_root.mkdir(parents=True, exist_ok=True)
    skus = []
    for i in range(n):
        sku = f"tgw{i:03d}"
        skus.append(sku)
        (thumb_root / f"{sku}.jpg").write_bytes(b"\xff\xd8\xff" + bytes([i]) * 16)
    # a non-jpg that must never be copied
    (thumb_root / "notes.txt").write_text("ignore me")
    return skus


def _cfg(tmp_path):
    db = tmp_path / "src" / "tgwcatalog.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    thumbs = tmp_path / "thumbs"
    return {"sqlite_catalog_path": db, "thumbnail_root": thumbs}, db, thumbs


def test_export_full(tmp_path):
    cfg, db, thumbs = _cfg(tmp_path)
    _make_db(db)
    _make_thumbs(thumbs, 5)
    dest = tmp_path / "dest"

    out = ce.export_catalog(cfg, dest)
    assert out["ok"] is True
    assert out["artifact"] == "catalog_export"
    assert out["db_copied"] is True
    assert out["thumbnails_copied"] == 5
    assert out["bytes_total"] > 0
    assert "elapsed_seconds" in out

    assert (dest / "tgwcatalog.db").exists()
    copied_jpgs = sorted(p.name for p in (dest / "thumbnails").glob("*.jpg"))
    assert copied_jpgs == ["tgw000.jpg", "tgw001.jpg", "tgw002.jpg",
                           "tgw003.jpg", "tgw004.jpg"]
    # the non-jpg must not be carried over
    assert not (dest / "thumbnails" / "notes.txt").exists()

    # the exported db is a valid copy
    con = sqlite3.connect(dest / "tgwcatalog.db")
    try:
        rows = con.execute("SELECT count(*) FROM catalog").fetchone()[0]
    finally:
        con.close()
    assert rows == 2


def test_export_limit(tmp_path):
    cfg, db, thumbs = _cfg(tmp_path)
    _make_db(db)
    _make_thumbs(thumbs, 5)
    dest = tmp_path / "dest"

    out = ce.export_catalog(cfg, dest, limit=2)
    assert out["ok"] is True
    assert out["thumbnails_copied"] == 2
    copied = sorted(p.name for p in (dest / "thumbnails").glob("*.jpg"))
    assert copied == ["tgw000.jpg", "tgw001.jpg"]  # sorted by name, first 2


def test_export_no_thumbnails(tmp_path):
    cfg, db, thumbs = _cfg(tmp_path)
    _make_db(db)
    _make_thumbs(thumbs, 5)
    dest = tmp_path / "dest"

    out = ce.export_catalog(cfg, dest, with_thumbnails=False)
    assert out["ok"] is True
    assert out["db_copied"] is True
    assert out["thumbnails_copied"] == 0
    assert (dest / "tgwcatalog.db").exists()
    # thumbnails dir should not have been populated
    assert not (dest / "thumbnails").exists() or \
        list((dest / "thumbnails").glob("*.jpg")) == []


def test_export_missing_db(tmp_path):
    cfg, db, thumbs = _cfg(tmp_path)
    # deliberately do NOT create the db
    _make_thumbs(thumbs, 3)
    dest = tmp_path / "dest"

    out = ce.export_catalog(cfg, dest)
    assert out["ok"] is False
    assert "build-sqlite" in out["error"]
    assert not (dest / "tgwcatalog.db").exists()


def test_check_only_writes_nothing(tmp_path):
    cfg, db, thumbs = _cfg(tmp_path)
    _make_db(db)
    _make_thumbs(thumbs, 5)
    dest = tmp_path / "dest"

    out = ce.export_catalog(cfg, dest, check_only=True)
    assert out["ok"] is True
    assert out["check_only"] is True
    assert out["db_copied"] is True
    assert out["thumbnails_copied"] == 5
    assert out["bytes_total"] > 0
    # nothing written
    assert not dest.exists()


def test_check_only_respects_limit(tmp_path):
    cfg, db, thumbs = _cfg(tmp_path)
    _make_db(db)
    _make_thumbs(thumbs, 5)
    dest = tmp_path / "dest"

    out = ce.export_catalog(cfg, dest, limit=3, check_only=True)
    assert out["thumbnails_copied"] == 3
    assert not dest.exists()


# ---------------------------------------------------------------------------
# Phase 2 — Syncthing push trigger
# ---------------------------------------------------------------------------

def test_syncthing_pushed_false_by_default(tmp_path):
    cfg, db, thumbs = _cfg(tmp_path)
    _make_db(db)
    dest = tmp_path / "dest"
    out = ce.export_catalog(cfg, dest, with_thumbnails=False)
    assert out["syncthing_pushed"] is False
    assert "syncthing_error" not in out


def test_syncthing_scan_called_when_push_folder_id_set(tmp_path, monkeypatch):
    cfg, db, thumbs = _cfg(tmp_path)
    _make_db(db)
    dest = tmp_path / "dest"

    calls = []
    monkeypatch.setattr('tgw.apis.syncthing.scan_folder',
                        lambda c, fid: calls.append(fid))

    out = ce.export_catalog(cfg, dest, with_thumbnails=False, push_folder_id='catalog-export')
    assert out["syncthing_pushed"] is True
    assert calls == ['catalog-export']
    assert "syncthing_error" not in out


def test_syncthing_error_captured_does_not_raise(tmp_path, monkeypatch):
    cfg, db, thumbs = _cfg(tmp_path)
    _make_db(db)
    dest = tmp_path / "dest"

    def _fail(c, fid):
        raise RuntimeError("Syncthing unreachable")

    monkeypatch.setattr('tgw.apis.syncthing.scan_folder', _fail)

    out = ce.export_catalog(cfg, dest, with_thumbnails=False, push_folder_id='catalog-export')
    assert out["ok"] is True        # export still succeeded
    assert out["syncthing_pushed"] is False
    assert "Syncthing unreachable" in out["syncthing_error"]


def test_check_only_includes_syncthing_pushed_false(tmp_path):
    cfg, db, thumbs = _cfg(tmp_path)
    _make_db(db)
    dest = tmp_path / "dest"
    out = ce.export_catalog(cfg, dest, check_only=True)
    assert out["syncthing_pushed"] is False


def test_negative_limit_does_not_truncate(tmp_path):
    cfg, db, thumbs = _cfg(tmp_path)
    _make_db(db)
    _make_thumbs(thumbs, 5)
    dest = tmp_path / "dest"

    # A negative limit must NOT slice from the end — all thumbnails are copied.
    out = ce.export_catalog(cfg, dest, limit=-2)
    assert out["ok"] is True
    assert out["thumbnails_copied"] == 5
    copied = sorted(p.name for p in (dest / "thumbnails").glob("*.jpg"))
    assert len(copied) == 5


def test_dest_accepts_str(tmp_path):
    cfg, db, thumbs = _cfg(tmp_path)
    _make_db(db)
    _make_thumbs(thumbs, 1)
    dest = tmp_path / "dest_str"

    out = ce.export_catalog(cfg, str(dest))
    assert out["ok"] is True
    assert out["dest"] == str(dest)
    assert (dest / "tgwcatalog.db").exists()
