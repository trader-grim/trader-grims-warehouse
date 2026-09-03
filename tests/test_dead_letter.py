"""Round 4 #29 — tests for `tgw dead-letter`, focused on --requeue-transient.

cmd_dead_letter lazily imports state_machine + classify_dead_letter inside the
function, so we patch those module attributes. All state is mocked — no DB.
"""

import tgw.api as api
from tgw.queue import state_machine, worker_base


def _jobs():
    # Two transient (token expired, read timeout), two permanent (hard failures).
    return [
        {"job_id": "j1", "queue_name": "ebay_stage", "error_detail": "token is expired",
         "payload_json": {"sku": "tgw1"}, "finished_at": "2026-06-03 06:05"},
        {"job_id": "j2", "queue_name": "ebay_stage", "error_detail": "no eBay photo URLs yet — waiting",
         "payload_json": {"sku": "tgw2"}, "finished_at": "2026-06-03 06:05"},
        {"job_id": "j3", "queue_name": "ebay_stage", "error_detail": "HardFailure: eBay rejected (25709)",
         "payload_json": {"sku": "tgw3"}, "finished_at": "2026-06-03 06:05"},
        {"job_id": "j4", "queue_name": "ebay_draft", "error_detail": "HardFailure: no ebay_category_id",
         "payload_json": {"sku": "tgw4"}, "finished_at": "2026-06-03 06:05"},
    ]


def _patch_common(monkeypatch, requeued):
    def _init(dsn, ebay_environment):
        assert dsn == "x"
        assert ebay_environment == "production"

    monkeypatch.setattr(state_machine, "init", _init, raising=False)
    # classify by the real transient substrings used in worker_base
    monkeypatch.setattr(state_machine, "requeue_dead_letter_job",
                        lambda jid: requeued.append(jid) or f"new-{jid}", raising=False)


def test_requeue_transient_only_requeues_transient(monkeypatch, capsys):
    requeued = []
    _patch_common(monkeypatch, requeued)
    monkeypatch.setattr(state_machine, "dead_letter_jobs",
                        lambda queue_name='', limit=100: _jobs(), raising=False)

    out = api.cmd_dead_letter({"postgres_dsn": "x"}, requeue_transient=True)

    assert out["ok"] is True
    assert out["action"] == "requeue_transient"
    # j1 (token expired) + j2 (no photo urls) are transient; j3/j4 are permanent.
    assert requeued == ["j1", "j2"]
    assert out["requeued_count"] == 2
    assert out["skipped_permanent"] == 2
    summary = capsys.readouterr().out
    assert "Re-enqueued 2 transient" in summary


def test_requeue_transient_honors_queue_filter(monkeypatch):
    requeued = []
    _patch_common(monkeypatch, requeued)
    seen = {}

    def fake_jobs(queue_name='', limit=100):
        seen["queue"] = queue_name
        return [j for j in _jobs() if not queue_name or j["queue_name"] == queue_name]

    monkeypatch.setattr(state_machine, "dead_letter_jobs", fake_jobs, raising=False)

    out = api.cmd_dead_letter({"postgres_dsn": "x"}, queue="ebay_draft", requeue_transient=True)

    # Only ebay_draft jobs were considered; that queue's single job is permanent.
    assert seen["queue"] == "ebay_draft"
    assert out["requeued_count"] == 0
    assert out["skipped_permanent"] == 1
    assert requeued == []


def test_requeue_transient_classification_uses_real_worker_base(monkeypatch):
    """Sanity-check the real classify_dead_letter agrees with our fixture intent."""
    assert worker_base.classify_dead_letter("token is expired")[0] == "requeue"
    assert worker_base.classify_dead_letter("HardFailure: eBay rejected (25709)")[0] == "dead_letter"


def test_single_requeue_id_path_unaffected(monkeypatch):
    requeued = []
    _patch_common(monkeypatch, requeued)
    monkeypatch.setattr(state_machine, "dead_letter_jobs",
                        lambda queue_name='', limit=100: _jobs(), raising=False)

    out = api.cmd_dead_letter({"postgres_dsn": "x"}, requeue_id="j9")
    assert out["ok"] is True
    assert out["action"] == "requeue"
    assert out["new_job_id"] == "new-j9"
    assert requeued == ["j9"]


def test_listing_path_still_lists(monkeypatch, capsys):
    requeued = []
    _patch_common(monkeypatch, requeued)
    monkeypatch.setattr(state_machine, "dead_letter_jobs",
                        lambda queue_name='', limit=100: _jobs(), raising=False)

    out = api.cmd_dead_letter({"postgres_dsn": "x"})
    assert out["ok"] is True
    assert out["count"] == 4
    assert requeued == []  # listing must not mutate anything
