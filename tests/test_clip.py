"""PP-CLIP-001 — tests for the clipboard history store + query core.

All operations take an explicit db_path (tmp), so nothing touches the real
~/.local store. The X11 daemon is out of scope (later phase).
"""

import tgw.clip as clip


def _db(tmp_path):
    return tmp_path / "history.db"


def test_classify_sku():
    assert clip.classify_sku("tgw202601011200000") == "tgw202601011200000"
    assert clip.classify_sku("  tgw202601011200000  ") == "tgw202601011200000"
    assert clip.classify_sku("not a sku") == ""
    assert clip.classify_sku("tgw123") == ""          # too short
    assert clip.classify_sku("tgw2026010112000001") == ""  # too long (16 digits)


def test_record_and_list(tmp_path):
    db = _db(tmp_path)
    clip.record_clip("hello world", db_path=db)
    clip.record_clip("tgw202601011200000", db_path=db)
    rows = clip.list_history(db_path=db)
    assert len(rows) == 2
    # Most recent first.
    assert rows[0]["content"] == "tgw202601011200000"
    assert rows[0]["is_sku"] == 1
    assert rows[1]["is_sku"] == 0


def test_last_sku_survives_later_nonsku(tmp_path):
    db = _db(tmp_path)
    clip.record_clip("tgw202601011200000", db_path=db)   # the SKU
    clip.record_clip("some copied text", db_path=db)      # later, non-SKU
    clip.record_clip("more text", db_path=db)
    # last_sku must still return the SKU even though it's no longer the latest clip.
    assert clip.last_sku(db_path=db) == "tgw202601011200000"


def test_last_sku_returns_latest_of_multiple(tmp_path):
    db = _db(tmp_path)
    clip.record_clip("tgw202601011200000", db_path=db)
    clip.record_clip("tgw202602021300000", db_path=db)
    assert clip.last_sku(db_path=db) == "tgw202602021300000"


def test_last_sku_none_when_empty(tmp_path):
    assert clip.last_sku(db_path=_db(tmp_path)) is None


def test_list_sku_only_filter(tmp_path):
    db = _db(tmp_path)
    clip.record_clip("plain text", db_path=db)
    clip.record_clip("tgw202601011200000", db_path=db)
    sku_rows = clip.list_history(sku_only=True, db_path=db)
    assert len(sku_rows) == 1
    assert sku_rows[0]["sku"] == "tgw202601011200000"


def test_search(tmp_path):
    db = _db(tmp_path)
    clip.record_clip("blue ceramic vase", db_path=db)
    clip.record_clip("red plastic bin", db_path=db)
    hits = clip.search("ceramic", db_path=db)
    assert len(hits) == 1
    assert "ceramic" in hits[0]["content"]


def test_wipe_nonsku_preserves_skus(tmp_path):
    db = _db(tmp_path)
    clip.record_clip("tgw202601011200000", db_path=db)
    clip.record_clip("junk one", db_path=db)
    clip.record_clip("junk two", db_path=db)
    deleted = clip.wipe_nonsku(db_path=db)
    assert deleted == 2
    remaining = clip.list_history(db_path=db)
    assert len(remaining) == 1
    assert remaining[0]["is_sku"] == 1


def test_cmd_clip_last_sku(tmp_path):
    db = _db(tmp_path)
    clip.record_clip("tgw202601011200000", db_path=db)
    out = clip.cmd_clip("last-sku", db_path=db)
    assert out["ok"] is True
    assert out["sku"] == "tgw202601011200000"


def test_cmd_clip_unknown_action(tmp_path):
    out = clip.cmd_clip("bogus", db_path=_db(tmp_path))
    assert out["ok"] is False
    assert "unknown clip action" in out["error"]
