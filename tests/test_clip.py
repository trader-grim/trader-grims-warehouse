"""PP-CLIP-001 — tests for the clipboard history store + query core.

All operations take an explicit db_path (tmp), so nothing touches the real
~/.local store. The X11 daemon is out of scope (later phase).
"""

import sqlite3
import stat

import pytest

import tgw.clip as clip


def _db(tmp_path):
    return tmp_path / "history.db"


def test_db_created_with_restrictive_permissions(tmp_path):
    db = _db(tmp_path)
    clip.record_clip("test", db_path=db)
    assert stat.S_IMODE(db.stat().st_mode) == 0o600
    assert stat.S_IMODE(db.parent.stat().st_mode) == 0o700


def test_permissive_existing_db_self_heals(tmp_path):
    db = _db(tmp_path)
    # Simulate a pre-fix db created with a permissive mode.
    clip.record_clip("seed", db_path=db)
    db.chmod(0o644)
    assert stat.S_IMODE(db.stat().st_mode) == 0o644
    clip.record_clip("trigger", db_path=db)
    assert stat.S_IMODE(db.stat().st_mode) == 0o600


def test_ttl_prune_removes_old_rows_keeps_recent(tmp_path):
    db = _db(tmp_path)
    clip.record_clip("old one", db_path=db)
    # Backdate the row we just inserted to 20 days ago.
    con = sqlite3.connect(str(db))
    con.execute(
        "UPDATE clip_history SET captured_at = datetime('now', '-20 days') "
        "WHERE content = ?",
        ("old one",),
    )
    con.commit()
    con.close()

    clip.record_clip("trigger", db_path=db)

    rows = clip.list_history(db_path=db)
    contents = [r["content"] for r in rows]
    assert "old one" not in contents
    assert "trigger" in contents


def test_row_count_retention_still_enforced(tmp_path):
    db = _db(tmp_path)
    for i in range(clip._RETENTION + 5):
        clip.record_clip(f"item {i}", db_path=db)
    rows = clip.list_history(limit=clip._RETENTION + 10, db_path=db)
    assert len(rows) == clip._RETENTION


def test_classify_sku():
    assert clip.classify_sku("tgw202601011200000") == "tgw202601011200000"
    assert clip.classify_sku("  tgw202601011200000  ") == "tgw202601011200000"
    assert clip.classify_sku("not a sku") == ""
    assert clip.classify_sku("tgw123") == ""          # too short
    assert clip.classify_sku("tgw2026010112000001") == ""  # too long (16 digits)


# ---------------------------------------------------------------------------
# looks_like_secret (todo #1565/PP-CLIP-001) — best-effort content heuristic
# ---------------------------------------------------------------------------

# One example per documented prefix pattern, plus a synthetic high-entropy
# generic token (no known prefix).
_SECRET_SHAPED = [
    "sk-ant-api03-abCdEfGhIjKlMnOpQrStUvWxYz0123456789",
    "sk-proj-abCdEfGhIjKlMnOpQrStUvWxYz012345",
    "ghp_aB3xQ9zT1kLmN7pR5sV8wY0cD2fH4jK6mZ1",
    "gho_aB3xQ9zT1kLmN7pR5sV8wY0cD2fH4jK6mZ1",
    "github_pat_11ABCDEFG0aB3xQ9zT1kLmN7pR5sV8wY0cD2fH4jK6mZ1abcdefgh",
    "AIzaSyAbCdEfGhIjKlMnOpQrStUvWxYz012345",
    "xoxb-1234567890-abCdEfGhIjKlMnOpQrStUvWx",
    "xoxp-1234567890-abCdEfGhIjKlMnOpQrStUvWx",
    "AKIAIOSFODNN7EXAMPLE1234567890ABCDEF",
    "ASIAIOSFODNN7EXAMPLE1234567890ABCDEF",
    "glpat-aB3xQ9zT1kLmN7pR5sV8wY0cD2fH4jK",
    # generic high-entropy fallback, no known prefix
    "Zk8pQ2vR9mL4tW7xN1cB6jH3sD0yF5gA8uE2",
]


@pytest.mark.parametrize("secret", _SECRET_SHAPED)
def test_looks_like_secret_flags_known_shapes(secret):
    assert clip.looks_like_secret(secret) is True


_SAFE_CONTENT = [
    "tgw202601011200000",                                    # TGW SKU
    "The quick brown fox jumps over the lazy dog today.",     # normal sentence
    "https://example.com/some/path?query=value&other=thing",  # URL
    "abababababababababababababab",                          # low-entropy repeated pattern
    "",                                                        # empty
    "short",                                                  # too short to be a token
]


@pytest.mark.parametrize("content", _SAFE_CONTENT)
def test_looks_like_secret_does_not_flag_safe_content(content):
    assert clip.looks_like_secret(content) is False


def test_looks_like_secret_never_flags_a_classified_sku_even_if_long():
    sku = "tgw202601011200000"
    assert clip.classify_sku(sku) == sku
    assert clip.looks_like_secret(sku) is False


def test_looks_like_secret_ignores_embedded_token_in_prose():
    # A prefix-looking substring embedded in a normal multi-word sentence is
    # NOT flagged — this heuristic only targets a bare token copied verbatim.
    assert clip.looks_like_secret("my key is sk-abc123 don't share it") is False


def test_shannon_entropy_high_for_random_token():
    assert clip._shannon_entropy("Zk8pQ2vR9mL4tW7xN1cB6jH3sD0yF5gA8uE2") > clip._ENTROPY_THRESHOLD


def test_shannon_entropy_low_for_repeated_pattern():
    assert clip._shannon_entropy("abababababababababababababab") < clip._ENTROPY_THRESHOLD


def test_secret_persistence_end_to_end_not_recorded_via_process_change():
    """Sanity check that looks_like_secret's classification lines up with
    what clipd.process_change would actually skip (full integration is
    covered in test_clipd.py)."""
    assert clip.looks_like_secret("ghp_aB3xQ9zT1kLmN7pR5sV8wY0cD2fH4jK6mZ1") is True


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


def test_pp_clip_authority_projection_uses_http_client_not_history_db(tmp_path, monkeypatch, capsys):
    calls = []

    class Client:
        def __init__(self, endpoint, token):
            calls.append((endpoint, token))

        def list_requests(self, *, limit):
            return {"requests": [{"request_id": "request:1", "status": "pending"}], "limit": limit}

    monkeypatch.setattr(clip, "PlanAuthorityHttpClient", Client)
    result = clip.cmd_clip(
        "authority-list", authority_url="https://authority.example", authority_token="token",
        limit=7, db_path=_db(tmp_path),
    )
    assert result["ok"] is True
    assert calls == [("https://authority.example", "token")]
    assert clip.list_history(db_path=_db(tmp_path)) == []
    assert "request:1" in capsys.readouterr().out


def test_pp_clip_authority_decision_requires_explicit_operator_inputs(tmp_path):
    result = clip.cmd_clip("authority-decide", authority_url="https://authority.example", authority_token="token", db_path=_db(tmp_path))
    assert result == {"ok": False, "error": "authority-decide requires --request-id, --decision and --reason"}


# ---------------------------------------------------------------------------
# origin/label columns + deliver_clip (todo #1563/PP-CLIP-001
# clipboard-agent-delivery Phase 0)
# ---------------------------------------------------------------------------

def test_deliver_clip_inserts_with_origin_agent(tmp_path):
    db = _db(tmp_path)
    out = clip.deliver_clip("prepared content", label="a label", db_path=db)
    assert out["ok"] is True
    assert out["origin"] == "agent"
    assert out["label"] == "a label"

    rows = clip.list_history(db_path=db)
    assert len(rows) == 1
    assert rows[0]["origin"] == "agent"
    assert rows[0]["label"] == "a label"
    assert rows[0]["content"] == "prepared content"


def test_deliver_clip_classifies_sku_same_as_record_clip(tmp_path):
    db = _db(tmp_path)
    out = clip.deliver_clip("tgw202601011200000", db_path=db)
    assert out["is_sku"] is True
    assert out["sku"] == "tgw202601011200000"


def test_regular_record_clip_defaults_origin_clipboard(tmp_path):
    db = _db(tmp_path)
    clip.record_clip("plain clipboard content", db_path=db)
    rows = clip.list_history(db_path=db)
    assert rows[0]["origin"] == "clipboard"
    assert rows[0]["label"] is None


def test_schema_migration_is_idempotent_on_preexisting_old_schema_db(tmp_path):
    """Simulate a real pre-migration DB (no origin/label columns) and confirm
    _connect() adds the columns additively, with no data loss and correct
    defaults on the old rows — and that running it twice is safe."""
    db = _db(tmp_path)
    # Build the OLD schema directly (pre-#1563), seed some real rows.
    con = sqlite3.connect(str(db))
    con.execute(
        """
        CREATE TABLE clip_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            content     TEXT NOT NULL,
            selection   TEXT NOT NULL DEFAULT 'clipboard',
            is_sku      INTEGER NOT NULL DEFAULT 0,
            sku         TEXT,
            captured_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    con.execute(
        "INSERT INTO clip_history (content, selection, is_sku, sku) VALUES (?, ?, ?, ?)",
        ("pre-existing old-schema row", "clipboard", 0, None),
    )
    con.execute(
        "INSERT INTO clip_history (content, selection, is_sku, sku) VALUES (?, ?, ?, ?)",
        ("tgw202601011200000", "clipboard", 1, "tgw202601011200000"),
    )
    con.commit()
    con.close()

    # First _connect() call runs the migration.
    con1 = clip._connect(db)
    con1.close()
    # Second call must be a no-op (idempotent, no error, no duplicate columns).
    con2 = clip._connect(db)
    con2.close()

    rows = clip.list_history(limit=10, db_path=db)
    assert len(rows) == 2
    contents = {r["content"] for r in rows}
    assert contents == {"pre-existing old-schema row", "tgw202601011200000"}
    for r in rows:
        assert r["origin"] == "clipboard"
        assert r["label"] is None

    # New writes onto the migrated DB still work correctly.
    clip.deliver_clip("new agent content", label="new", db_path=db)
    rows = clip.list_history(limit=10, db_path=db)
    assert len(rows) == 3
    agent_rows = [r for r in rows if r["origin"] == "agent"]
    assert len(agent_rows) == 1
    assert agent_rows[0]["label"] == "new"


def test_list_history_surfaces_origin_and_label(tmp_path):
    db = _db(tmp_path)
    clip.record_clip("regular clip", db_path=db)
    clip.deliver_clip("delivered content", label="the label", db_path=db)
    rows = clip.list_history(db_path=db)
    by_content = {r["content"]: r for r in rows}
    assert by_content["regular clip"]["origin"] == "clipboard"
    assert by_content["regular clip"]["label"] is None
    assert by_content["delivered content"]["origin"] == "agent"
    assert by_content["delivered content"]["label"] == "the label"


def test_search_surfaces_origin_and_label(tmp_path):
    db = _db(tmp_path)
    clip.deliver_clip("blue ceramic vase delivered", label="vase note", db_path=db)
    hits = clip.search("ceramic", db_path=db)
    assert len(hits) == 1
    assert hits[0]["origin"] == "agent"
    assert hits[0]["label"] == "vase note"


def test_cmd_clip_deliver_returns_ok_with_id(tmp_path):
    db = _db(tmp_path)
    out = clip.cmd_clip("deliver", pattern="test content", label="test", db_path=db)
    assert out["ok"] is True
    assert isinstance(out["id"], int)
    assert out["origin"] == "agent"
    assert out["label"] == "test"


def test_cmd_clip_deliver_requires_content(tmp_path):
    db = _db(tmp_path)
    out = clip.cmd_clip("deliver", pattern="", db_path=db)
    assert out["ok"] is False
    assert "content" in out["error"]


def test_cmd_clip_list_shows_agent_tag(tmp_path, capsys):
    db = _db(tmp_path)
    clip.deliver_clip("delivered content", label="a label", db_path=db)
    clip.cmd_clip("list", db_path=db)
    out = capsys.readouterr().out
    assert "[AGENT]" in out
    assert "a label" in out
