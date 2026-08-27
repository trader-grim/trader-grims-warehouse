from pathlib import Path

import pytest

from tgw.development.coding_lifecycle import (
    STAGES,
    LifecycleError,
    LifecycleStore,
    advance,
    create,
    stage_idempotency_key,
)


def new(store: LifecycleStore):
    return create(store, target=1915, plan_commit="a" * 40,
                  solution_hash="sha256:" + "b" * 64,
                  source_commit="c" * 40, source_tree="d" * 40)


def test_create_is_immediate_and_duplicate_replay_is_one_record(tmp_path: Path):
    store = LifecycleStore(tmp_path)
    first = new(store)
    assert new(store) == first
    assert first["state"] == "QUEUED"
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert all(value is False for value in first["boundaries"].values())


def test_restart_at_every_stage_and_effect_replay_are_idempotent(tmp_path: Path):
    store = LifecycleStore(tmp_path)
    record = new(store)
    calls = {stage: 0 for stage in STAGES}

    def handler(stage):
        def run(_record):
            calls[stage] += 1
            return {"outcome": "satisfied", "effect_key": stage,
                    "receipt": {"stage": stage}}
        return run

    handlers = {stage: handler(stage) for stage in STAGES}
    result = advance(store, record["root_id"], handlers)
    replay = advance(store, record["root_id"], handlers)
    assert result["state"] == replay["state"] == "SUCCEEDED"
    assert calls == {stage: 1 for stage in STAGES}
    assert set(result["effects"]) == set(STAGES)


@pytest.mark.parametrize("outcome,state", [
    ("failed", "FAILED"), ("resumable_partial", "RESUMABLE_PARTIAL"),
    ("remediation", "REMEDIATION_REQUIRED"),
])
def test_failure_states_close_precisely(tmp_path: Path, outcome: str, state: str):
    store = LifecycleStore(tmp_path)
    record = new(store)
    result = advance(store, record["root_id"], {
        "implementation": lambda _: {"outcome": outcome, "reason": "exact reason"}
    })
    assert result["state"] == state
    assert result.get("failure", {}).get("reason", "exact reason") == "exact reason"


def test_tampered_journal_fails_closed(tmp_path: Path):
    store = LifecycleStore(tmp_path)
    record = new(store)
    path = store.path(record["root_id"])
    path.write_text(path.read_text().replace("QUEUED", "SUCCEEDED"))
    with pytest.raises(LifecycleError, match="hash/schema"):
        store.get(record["root_id"])


def test_records_reconstruct_nonterminal_roots_and_reject_tamper(tmp_path: Path):
    store = LifecycleStore(tmp_path)
    record = new(store)
    assert [item["root_id"] for item in store.records()] == [record["root_id"]]
    store.path(record["root_id"]).write_text("{}")
    with pytest.raises(LifecycleError, match="hash/schema"):
        store.records()


def test_only_one_supervisor_can_own_a_root(tmp_path: Path):
    store = LifecycleStore(tmp_path)
    record = new(store)
    first = store.supervisor_lock(record["root_id"])
    assert first is not None
    try:
        assert store.supervisor_lock(record["root_id"]) is None
    finally:
        first.close()
    second = store.supervisor_lock(record["root_id"])
    assert second is not None
    second.close()


def test_target_lookup_is_exact_and_ambiguous_generations_fail_closed(tmp_path: Path):
    store = LifecycleStore(tmp_path)
    record = new(store)
    assert store.find(1915) == record
    create(store, target=1915, plan_commit="e" * 40,
           solution_hash="sha256:" + "b" * 64,
           source_commit="f" * 40, source_tree="1" * 40)
    with pytest.raises(LifecycleError, match="ambiguous source generations"):
        store.find(1915)


def test_stage_receipt_binding_is_replay_stable_and_mismatch_fails(tmp_path: Path):
    store = LifecycleStore(tmp_path)
    record = new(store)
    expected = stage_idempotency_key(record, "implementation")
    result = advance(store, record["root_id"], {
        "implementation": lambda _: {
            "outcome": "waiting", "idempotency_key": expected,
        }
    })
    assert result["stages"]["implementation"]["idempotency_key"] == expected
    with pytest.raises(LifecycleError, match="receipt binding mismatch"):
        advance(store, record["root_id"], {
            "implementation": lambda _: {
                "outcome": "satisfied", "idempotency_key": "sha256:" + "0" * 64,
            }
        })


def test_context_unavailable_retries_once_then_local_lifecycle_continues(tmp_path: Path):
    store = LifecycleStore(tmp_path)
    record = new(store)
    handlers = {
        stage: (lambda _record, name=stage: {"outcome": "satisfied", "receipt": name})
        for stage in STAGES
    }
    handlers["terminal_publication"] = lambda _: {
        "outcome": "publication_unavailable", "reason": "Context offline",
    }
    first = advance(store, record["root_id"], handlers)
    assert first["state"] == "WAITING"
    assert first["publication"]["retry_available"] is True
    second = advance(store, record["root_id"], handlers)
    assert second["state"] == "SUCCEEDED"
    assert second["publication"] == {
        "attempted": True, "attempts": 2, "last_error": "Context offline",
        "published": False, "retry_available": False,
    }
    assert second["operator_acceptance"] == "PENDING"
