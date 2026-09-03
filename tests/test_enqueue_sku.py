"""PP-WM-001 — tests for `tgw enqueue-sku` (the per-SKU enqueue the Qtile chord uses)."""

import json

import pytest

import tgw.api as api
from tgw.queue import state_machine as sm


@pytest.fixture
def cfg(tmp_path):
    return {"itemdata_root": tmp_path, "postgres_dsn": "postgresql://fake/db"}


def _write_item(cfg, sku):
    d = cfg["itemdata_root"] / sku
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sku}.json").write_text(json.dumps({"sku": sku}), encoding="utf-8")


def test_invalid_queue(cfg):
    out = api.cmd_enqueue_sku(cfg, "tgw001", "not_a_queue")
    assert out["ok"] is False
    assert "invalid queue" in out["error"]


def test_item_not_found(cfg):
    out = api.cmd_enqueue_sku(cfg, "tgw404", "ai_identify")
    assert out["ok"] is False
    assert "not found" in out["error"]


def test_success(cfg, monkeypatch):
    _write_item(cfg, "tgw001")
    cfg["ebay_environment"] = "sandbox"
    initialized = []
    monkeypatch.setattr(sm, "init", lambda *a, **k: initialized.append(a))
    captured = {}
    monkeypatch.setattr(sm, "enqueue_job",
                        lambda **kw: captured.update(kw) or "job-9")
    out = api.cmd_enqueue_sku(cfg, "tgw001", "ebay_draft")
    assert out["ok"] is True
    assert out["job_id"] == "job-9"
    assert out["queue"] == "ebay_draft"
    assert captured["dedupe_key"] == "ebay_draft:tgw001"
    assert captured["payload"] == {"sku": "tgw001"}
    assert initialized == [("postgresql://fake/db", "sandbox")]


def test_duplicate_is_ok(cfg, monkeypatch):
    import psycopg2.errors
    _write_item(cfg, "tgw001")
    monkeypatch.setattr(sm, "init", lambda *a, **k: None)

    def _dupe(**kw):
        raise psycopg2.errors.UniqueViolation("dup")

    monkeypatch.setattr(sm, "enqueue_job", _dupe)
    out = api.cmd_enqueue_sku(cfg, "tgw001", "ai_identify")
    assert out["ok"] is True
    assert "already queued" in out["note"]
