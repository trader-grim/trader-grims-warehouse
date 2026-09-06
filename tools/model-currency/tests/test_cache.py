from __future__ import annotations

import json
import time

from model_currency.cache import Cache, default_root


def test_roundtrip(tmp_path):
    c = Cache(root=tmp_path, ttl=100)
    assert c.put("openrouter", {"data": [1, 2, 3]}) is True
    data, meta = c.get("openrouter")
    assert data == {"data": [1, 2, 3]}
    assert meta["fresh"] is True
    assert meta["age"] >= 0


def test_missing_entry_returns_none(tmp_path):
    data, meta = Cache(root=tmp_path).get("nope")
    assert data is None and meta is None


def test_staleness(tmp_path):
    c = Cache(root=tmp_path, ttl=1)
    c.put("groq", {"x": 1})
    path = c._path("groq")
    payload = json.loads(path.read_text())
    payload["fetched_at"] = time.time() - 3600
    path.write_text(json.dumps(payload))

    data, meta = c.get("groq")
    assert data == {"x": 1}
    assert meta["fresh"] is False

    # explicit max_age can still accept it
    _, meta2 = c.get("groq", max_age=float("inf"))
    assert meta2["fresh"] is True


def test_corrupt_entry_is_ignored(tmp_path):
    c = Cache(root=tmp_path)
    c._path("bad").parent.mkdir(parents=True, exist_ok=True)
    c._path("bad").write_text("{ not json")
    assert c.get("bad") == (None, None)


def test_unwritable_root_does_not_raise(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a dir")
    c = Cache(root=blocker / "sub")
    assert c.put("k", {"a": 1}) is False
    assert c.get("k") == (None, None)


def test_key_is_filesystem_safe(tmp_path):
    c = Cache(root=tmp_path)
    c.put("weird/../key name", {"ok": 1})
    data, _ = c.get("weird/../key name")
    assert data == {"ok": 1}
    assert list(tmp_path.iterdir())  # something was written, inside tmp_path


def test_default_root_respects_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_CURRENCY_CACHE", str(tmp_path / "mc"))
    assert default_root() == tmp_path / "mc"
    monkeypatch.delenv("MODEL_CURRENCY_CACHE")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert default_root() == tmp_path / "xdg" / "model-currency"
