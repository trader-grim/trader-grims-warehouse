import json
from dataclasses import dataclass

import pytest

from tgw.errors import TreatmentFailure
from tgw.item_mutation import item_generation
from tgw.workers.normalize_condition import (
    NormalizeConditionWorker,
    apply_condition_mutation,
    handle_job,
)


def _job(**payload):
    base = {"sku": "TGW-001", "entity_id": "TGW-001",
            "object_generation": "gen-7", "graph_id": "graph-7",
            "treatment_id": "normalize-condition", "treatment_version": "1"}
    base.update(payload)
    return {"job_id": "job-123", "entity_id": "TGW-001", "payload_json": base}


@dataclass
class Result:
    status: str
    operation_id: str = "operation-1"
    resulting_generation: str | None = "gen-8"
    changed: bool = True


def test_committed_mutation_is_only_success_authority():
    calls = []
    receipt = handle_job(_job(), {"root": "/data"},
                         mutation_fn=lambda **kw: calls.append(kw) or Result("COMMITTED"))
    assert receipt["outcome"] == "satisfied"
    assert receipt["established_conditions"] == ["valid_condition"]
    assert receipt["evidence"]["changed"] is True
    assert receipt["evidence"]["resulting_generation"] == "gen-8"
    assert calls == [{"config": {"root": "/data"}, "sku": "TGW-001",
                      "job_id": "job-123", "graph_id": "graph-7",
                      "expected_generation": "gen-7"}]


def test_noop_mutation_propagates_exact_changed_false():
    receipt = handle_job(
        _job(), {}, mutation_fn=lambda **kw: Result(
            "COMMITTED", resulting_generation="gen-7", changed=False,
        ),
    )
    assert receipt["outcome"] == "satisfied"
    assert receipt["evidence"]["changed"] is False
    assert receipt["evidence"]["resulting_generation"] == "gen-7"


def test_operation_id_is_stable_for_job_identity():
    ids = []
    def mutate(**kw):
        ids.append((kw["job_id"], kw["graph_id"], kw["expected_generation"]))
        return {"status": "COMMITTED", "operation_id": "operation-1"}

    handle_job(_job(), {}, mutation_fn=mutate)
    handle_job(_job(), {}, mutation_fn=mutate)
    assert ids[0] == ids[1]


def test_payload_condition_cannot_spoof_authoritative_input():
    calls = []
    handle_job(_job(condition="New"), {}, mutation_fn=lambda **kw: calls.append(kw) or {"status": "FAILED"})
    assert "condition" not in calls[0]


@pytest.mark.parametrize(
    ("authoritative", "status", "written"),
    [("pre-owned", "COMMITTED", "Used"), ("mystery mint-ish", "FAILED", "mystery mint-ish")],
)
def test_adapter_uses_authoritative_document_and_never_guesses(
    tmp_path, monkeypatch, authoritative, status, written
):
    item_root = tmp_path / "items"
    item_path = item_root / "TGW-001" / "TGW-001.json"
    item_path.parent.mkdir(parents=True)
    document = {"sku": "TGW-001", "condition": authoritative}
    item_path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr("tgw.sqlite_catalog.upsert_catalog_row", lambda cfg, doc: {"ok": True})
    cfg = {"itemdata_root": item_root, "archive_root": tmp_path / "archive",
           "data_root": tmp_path / "data", "sqlite_catalog_path": tmp_path / "catalog.db",
           "item_mutation_journal_root": tmp_path / "journal"}
    result = apply_condition_mutation(
        config=cfg, sku="TGW-001", job_id="job-123", graph_id="graph-7",
        expected_generation=item_generation(document),
    )
    assert result.status == status
    assert json.loads(item_path.read_text(encoding="utf-8"))["condition"] == written


def test_adapter_rejects_document_sku_mismatch_before_publication(tmp_path, monkeypatch):
    item_root = tmp_path / "items"
    item_path = item_root / "TGW-001" / "TGW-001.json"
    item_path.parent.mkdir(parents=True)
    document = {"sku": "OTHER", "condition": "pre-owned"}
    item_path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr("tgw.sqlite_catalog.upsert_catalog_row", lambda cfg, doc: {"ok": True})
    cfg = {"itemdata_root": item_root, "archive_root": tmp_path / "archive",
           "data_root": tmp_path / "data", "sqlite_catalog_path": tmp_path / "catalog.db",
           "item_mutation_journal_root": tmp_path / "journal"}
    result = apply_condition_mutation(
        config=cfg, sku="TGW-001", job_id="job-123", graph_id="graph-7",
        expected_generation=item_generation(document),
    )
    assert result.status == "FAILED"
    assert json.loads(item_path.read_text()) == document


def test_adapter_requires_truthful_sqlite_success(tmp_path, monkeypatch):
    item_root = tmp_path / "items"
    item_path = item_root / "TGW-001" / "TGW-001.json"
    item_path.parent.mkdir(parents=True)
    document = {"sku": "TGW-001", "condition": "pre-owned"}
    item_path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr("tgw.sqlite_catalog.upsert_catalog_row", lambda cfg, doc: {"ok": False})
    cfg = {"itemdata_root": item_root, "archive_root": tmp_path / "archive",
           "data_root": tmp_path / "data", "sqlite_catalog_path": tmp_path / "catalog.db",
           "item_mutation_journal_root": tmp_path / "journal"}
    result = apply_condition_mutation(
        config=cfg, sku="TGW-001", job_id="job-123", graph_id="graph-7",
        expected_generation=item_generation(document),
    )
    assert result.status == "REPAIR_REQUIRED"


@pytest.mark.parametrize("change", [{"graph_id": ""}, {"object_generation": ""},
                                      {"treatment_id": "ebay-publish"},
                                      {"treatment_version": "2"}, {"sku": "OTHER"}])
def test_bad_identity_never_mutates(change):
    calls = []
    receipt = handle_job(_job(**change), {}, mutation_fn=lambda **kw: calls.append(kw))
    assert receipt["outcome"] == "failed" and not receipt["established_conditions"] and not calls


@pytest.mark.parametrize("result", [{"status": "CONFLICT"}, Result("ABORTED"), None])
def test_noncommitted_result_never_establishes_condition(result):
    receipt = handle_job(_job(), {}, mutation_fn=lambda **kw: result)
    assert receipt["outcome"] == "failed" and receipt["established_conditions"] == []
    assert receipt["evidence"]["reason_code"] == "MUTATION_NOT_COMMITTED"


def test_exception_is_structured_without_message_leak():
    def fail(**kw): raise RuntimeError("secret detail")
    receipt = handle_job(_job(), {}, mutation_fn=fail)
    assert receipt["evidence"]["error_type"] == "RuntimeError"
    assert "secret detail" not in str(receipt)


def test_worker_raises_hard_failure_instead_of_returning_failed_receipt(monkeypatch):
    worker = object.__new__(NormalizeConditionWorker)
    worker.config = {}
    monkeypatch.setattr(
        "tgw.workers.normalize_condition.handle_job",
        lambda *args, **kwargs: {"outcome": "failed", "evidence": {"reason_code": "CONFLICT"}},
    )
    with pytest.raises(TreatmentFailure, match="CONFLICT") as raised:
        worker.handle(_job())
    assert raised.value.result["evidence"]["reason_code"] == "CONFLICT"


def test_worker_success_receipt_carries_complete_queue_identity(monkeypatch):
    worker = object.__new__(NormalizeConditionWorker)
    worker.config = {}
    job = _job(
        goal_profile_id="tgw.ebay_listable", goal_profile_version="1",
        condition_hash="condition-1",
    )
    monkeypatch.setattr(
        "tgw.workers.normalize_condition.handle_job",
        lambda *args, **kwargs: {
            "outcome": "satisfied", "treatment_id": "normalize-condition",
            "treatment_version": "1", "graph_id": "graph-7",
            "receipt_schema_id": "treatment-receipt/v1",
        },
    )
    receipt = worker.handle(job)
    assert receipt["goal_profile_id"] == "tgw.ebay_listable"
    assert receipt["goal_profile_version"] == "1"
    assert receipt["object_generation"] == "gen-7"
    assert receipt["condition_hash"] == "condition-1"
    assert receipt["entity_id"] == "TGW-001"
