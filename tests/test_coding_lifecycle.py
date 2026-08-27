import json
import os
import stat
from pathlib import Path

import pytest

from tgw import coding_cli
from tgw.development import coding_lifecycle
from tgw.development.coding_lifecycle import (
    STAGES,
    LifecycleError,
    LifecycleStore,
    advance,
    build_binding,
    create,
    job_binding,
    record_operator_readback,
    report_stale_source,
    request_resume,
    stage_result,
    validate_job_binding_payload,
)
from tgw.development.local_workflow import LocalCodingWorkflowError, load_config
from tgw.development.plan_binding import execution_root_hash


def plan_binding(worktree: Path, *, source: str = "c" * 40) -> dict:
    root = {
        "schema": "tgw-execution-root/v1",
        "kind": "todo",
        "todo_id": 1915,
    }
    root["identity_hash"] = execution_root_hash(root)
    return {
        "schema": "tgw-plan-coding-todo/v1",
        "plan_commit": "a" * 40,
        "solution_hash": "sha256:" + "b" * 64,
        "closure_hash": "sha256:" + "1" * 64,
        "capability": "workflow.condition-derived-convergence",
        "treatment_id": "establish:workflow.condition-derived-convergence@1",
        "source_commit": source,
        "idempotency_key": "sha256:" + "2" * 64,
        "worktree": str(worktree),
        "worktree_identity": {
            "worktree": str(worktree),
            "branch": "todo/1915",
            "head": source,
            "actor": "codex",
            "created": True,
        },
        "execution_root": root,
    }


def store_at(path: Path) -> LifecycleStore:
    return LifecycleStore(path, group_gid=os.getegid())


def new(store: LifecycleStore, worktree: Path | None = None) -> dict:
    selected = worktree or store.root.parent / "worktree"
    selected.mkdir(exist_ok=True)
    binding = build_binding(
        target=1915,
        plan_binding=plan_binding(selected),
        source_tree="d" * 40,
    )
    return create(store, target=1915, binding=binding)


def result_handler(stage: str, *, outcome: str = "satisfied", receipt=None):
    def run(record):
        return stage_result(
            record,
            stage,
            outcome,
            receipt=receipt or {"stage": stage},
            reason="exact reason" if outcome != "satisfied" else None,
        )

    return run


def test_create_is_immediate_group_shared_and_duplicate_replay_is_one_record(
    tmp_path: Path,
):
    store = store_at(tmp_path / "journal")
    first = new(store)
    assert create(store, target=1915, binding=first["binding"]) == first
    assert first["state"] == "QUEUED"
    assert len(list(store.root.glob("*.json"))) == 1
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o2770
    assert stat.S_IMODE(store.path(first["root_id"]).stat().st_mode) == 0o660
    assert all(value is False for value in first["boundaries"].values())


def test_repeated_start_retains_one_root_and_reports_stale_source(tmp_path: Path):
    store = store_at(tmp_path / "journal")
    record = new(store)
    stale = report_stale_source(
        store,
        record["root_id"],
        source_commit="e" * 40,
        source_tree="f" * 40,
    )
    assert stale["root_id"] == record["root_id"]
    assert stale["state"] == "REMEDIATION_REQUIRED"
    assert stale["failure"]["bound_source_commit"] == "c" * 40
    assert len(list(store.root.glob("*.json"))) == 1
    assert store.find(1915) == stale


def test_restart_at_each_stage_replays_no_satisfied_effect(tmp_path: Path):
    store = store_at(tmp_path / "journal")
    record = new(store)
    calls = {stage: 0 for stage in STAGES}

    def handler(stage):
        def run(current):
            calls[stage] += 1
            if calls[stage] == 1:
                return stage_result(
                    current, stage, "waiting", reason=f"{stage} pending"
                )
            if stage == "operator_readback":
                readback = current["operator"]["readback"]
                if readback is None:
                    return stage_result(
                        current, stage, "waiting", reason="operator pending"
                    )
                return stage_result(current, stage, "satisfied", receipt=readback)
            return stage_result(
                current, stage, "satisfied", receipt={"stage": stage}
            )

        return run

    handlers = {stage: handler(stage) for stage in STAGES}
    while True:
        record = advance(store, record["root_id"], handlers)
        if (
            record["stage"] == "operator_readback"
            and record["operator"]["notification"] is not None
        ):
            break
    record_operator_readback(
        store, record["root_id"], actor="operator", decision="accept"
    )
    record = advance(store, record["root_id"], handlers)
    assert record["state"] == "SUCCEEDED"
    assert record["operator_acceptance"] == "ACCEPTED"
    assert set(record["effects"]) == set(STAGES)
    before = dict(calls)
    assert advance(store, record["root_id"], handlers) == record
    assert calls == before


def test_resumable_partial_reopens_same_root_and_remains_recoverable(tmp_path: Path):
    store = store_at(tmp_path / "journal")
    record = new(store)
    partial = advance(
        store,
        record["root_id"],
        {"implementation": result_handler("implementation", outcome="resumable_partial")},
    )
    assert partial["state"] == "RESUMABLE_PARTIAL"
    assert partial["state"] not in coding_lifecycle.TERMINAL
    assert [item["root_id"] for item in store.records()] == [record["root_id"]]
    reopened = request_resume(
        store,
        record["root_id"],
        receipt={
            "root_id": record["root_id"],
            "binding_hash": record["binding"]["binding_hash"],
            "resume_of": "sha256:" + "3" * 64,
        },
    )
    assert reopened["root_id"] == record["root_id"]
    assert reopened["state"] == "WAITING"
    resumed = advance(
        store,
        record["root_id"],
        {
            "implementation": result_handler("implementation"),
            "controller": result_handler("controller", outcome="waiting"),
        },
    )
    assert resumed["stage"] == "controller"


def test_context_outage_never_waives_retry_and_later_publication_completes(
    tmp_path: Path,
):
    store = store_at(tmp_path / "journal")
    record = new(store)
    online = False

    def handler(stage):
        def run(current):
            if stage == "terminal_publication" and not online:
                return stage_result(
                    current,
                    stage,
                    "publication_unavailable",
                    reason="Context offline",
                )
            if stage == "operator_readback":
                readback = current["operator"]["readback"]
                if readback is None:
                    return stage_result(
                        current, stage, "waiting", reason="operator pending"
                    )
                return stage_result(current, stage, "satisfied", receipt=readback)
            return stage_result(
                current, stage, "satisfied", receipt={"stage": stage}
            )

        return run

    handlers = {stage: handler(stage) for stage in STAGES}
    first = advance(store, record["root_id"], handlers)
    assert first["publication"]["pending"] is True
    record_operator_readback(
        store, record["root_id"], actor="operator", decision="accept"
    )
    for expected_attempt in range(2, 7):
        first = advance(store, record["root_id"], handlers)
        assert first["publication"]["attempts"] == expected_attempt
        assert first["publication"]["retry_available"] is True
        assert first["state"] == "AWAITING_CONTEXT_PUBLICATION"
    online = True
    completed = advance(store, record["root_id"], handlers)
    assert completed["state"] == "SUCCEEDED"
    assert completed["publication"]["published"] is True


def test_operator_readback_is_not_acceptance_and_rejection_is_explicit(
    tmp_path: Path,
):
    store = store_at(tmp_path / "journal")
    record = new(store)
    handlers = {
        stage: result_handler(stage)
        for stage in STAGES
        if stage != "operator_readback"
    }
    handlers["operator_readback"] = result_handler(
        "operator_readback", outcome="waiting"
    )
    waiting = advance(store, record["root_id"], handlers)
    read = record_operator_readback(
        store, waiting["root_id"], actor="operator", decision=None
    )
    assert read["operator_acceptance"] == "PENDING"
    rejected = record_operator_readback(
        store, waiting["root_id"], actor="operator", decision="reject"
    )
    assert rejected["operator_acceptance"] == "REJECTED"


def test_tampered_or_private_journal_fails_closed(tmp_path: Path):
    store = store_at(tmp_path / "journal")
    record = new(store)
    path = store.path(record["root_id"])
    path.write_text(path.read_text().replace("QUEUED", "SUCCEEDED"))
    path.chmod(0o660)
    with pytest.raises(LifecycleError, match="hash/schema"):
        store.get(record["root_id"])
    path.chmod(0o600)
    with pytest.raises(LifecycleError, match="ownership/mode"):
        store.get(record["root_id"])


def test_stage_claim_requires_exact_idempotency_and_receipt(tmp_path: Path):
    store = store_at(tmp_path / "journal")
    record = new(store)
    missing = advance(
        store,
        record["root_id"],
        {
            "implementation": lambda current: {
                "outcome": "satisfied",
                "idempotency_key": coding_lifecycle.stage_idempotency_key(
                    current, "implementation"
                ),
            }
        },
    )
    assert missing["state"] == "REMEDIATION_REQUIRED"
    assert "without evidence" in missing["failure"]["reason"]


def test_worker_job_binding_rejects_substitution(tmp_path: Path):
    store = store_at(tmp_path / "journal")
    record = new(store)
    fence = job_binding(record)
    assert validate_job_binding_payload(
        fence, plan_binding=record["binding"]["plan_todo_binding"]
    ) == fence
    stale = {**fence, "closure_hash": "sha256:" + "9" * 64}
    with pytest.raises(LifecycleError, match="malformed or stale"):
        validate_job_binding_payload(
            stale, plan_binding=record["binding"]["plan_todo_binding"]
        )


def test_queue_stage_ignores_historical_todo_success_and_binds_exact_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    store = store_at(tmp_path / "journal")
    record = new(store, worktree)
    fence = job_binding(record)
    receipt = {
        "status": "PASS",
        "outcome": "satisfied",
        "treatment_id": "codex-implement",
        "plan_binding": record["binding"]["plan_todo_binding"],
        "coding_lifecycle": fence,
    }
    (worktree / "implementation-receipt.json").write_text(json.dumps(receipt))
    rows = [
        {
            "job_id": "historical",
            "queue_name": "codex-implement",
            "state": "succeeded",
            "payload_json": {"todo_id": 1915, "result": {"status": "PASS"}},
        },
        {
            "job_id": "exact",
            "queue_name": "codex-implement",
            "state": "succeeded",
            "attempt_count": 1,
            "payload_json": {
                "todo_id": 1915,
                "plan_binding": record["binding"]["plan_todo_binding"],
                "coding_lifecycle": fence,
                "result": receipt,
            },
        },
    ]
    monkeypatch.setattr(coding_cli, "_jobs", lambda *_args, **_kwargs: rows)
    result = coding_cli._queue_evidence(
        record,
        stage="implementation",
        queue_name="codex-implement",
        receipt_name="implementation-receipt.json",
        dispatch=lambda: pytest.fail("exact row must be reused"),
    )
    assert result["outcome"] == "satisfied"
    assert result["job_ids"] == ["exact"]
    assert result["receipt"]["job_id"] == "exact"


def test_exact_independent_review_failure_stops_without_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store = store_at(tmp_path / "journal")
    record = new(store)
    fence = job_binding(record)
    rows = [
        {
            "job_id": "review-failed",
            "queue_name": "claude-review",
            "state": "dead_letter",
            "payload_json": {
                "todo_id": 1915,
                "plan_binding": record["binding"]["plan_todo_binding"],
                "coding_lifecycle": fence,
            },
        }
    ]
    monkeypatch.setattr(coding_cli, "_jobs", lambda *_args, **_kwargs: rows)
    result = coding_cli._queue_evidence(
        record,
        stage="review",
        queue_name="claude-review",
        receipt_name="review-receipt.json",
        dispatch=lambda: pytest.fail("terminal exact review must not redispatch"),
    )
    assert result["outcome"] == "failed"
    assert result["job_ids"] == ["review-failed"]


def test_lifecycle_start_binds_before_detached_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    journal = tmp_path / "journal"
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    plan = plan_binding(worktree)
    config = {
        "postgres_dsn": "test",
        "coding": {"lifecycle_root": str(journal)},
    }
    monkeypatch.setattr(coding_cli, "_initialize", lambda _path: config)
    monkeypatch.setattr(
        coding_cli,
        "_pp_runtime_binding",
        lambda *_args: {
            "selected_commit": "c" * 40,
            "selected_tree": "d" * 40,
        },
    )
    monkeypatch.setattr(coding_cli.todo, "todo_get", lambda _identifier: {"id": 1915})
    monkeypatch.setattr(coding_cli, "_plan_binding_for_todo", lambda _identifier: ({}, plan))
    calls = []

    def fake_start(*_args, **kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(coding_cli, "start", fake_start)
    monkeypatch.setattr(
        coding_cli,
        "LifecycleStore",
        lambda root: LifecycleStore(root, group_gid=os.getegid()),
    )
    monkeypatch.setattr(coding_lifecycle, "spawn", lambda *_args, **_kwargs: 4321)
    local = __import__("tgw.development.local_workflow", fromlist=["load_solution"])
    monkeypatch.setattr(
        local,
        "load_solution",
        lambda _path: {
            "plan_commit": "a" * 40,
            "solution_hash": "sha256:" + "b" * 64,
        },
    )
    result = coding_cli.lifecycle_start(1915, config_path=tmp_path / "config")
    assert result["returns_immediately"] is True
    assert result["supervisor_pid"] == 4321
    assert calls[0]["dispatch_jobs"] is False
    assert calls[0]["source_commit"] == "c" * 40


def test_run_supervisor_reconstructs_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store = store_at(tmp_path / "journal")
    record = new(store)
    states = ["WAITING", "SUCCEEDED"]

    def fake_supervise(identity, *, config_path):
        assert identity == record["root_id"]
        value = store.get(identity)
        value["state"] = states.pop(0)
        return store.put(value)

    monkeypatch.setattr(coding_cli, "supervise", fake_supervise)
    local = __import__("tgw.development.local_workflow", fromlist=["load_config"])
    monkeypatch.setattr(
        local,
        "load_config",
        lambda _path: {"coding": {"lifecycle_root": str(store.root)}},
    )
    monkeypatch.setattr(
        coding_lifecycle,
        "LifecycleStore",
        lambda root: LifecycleStore(root, group_gid=os.getegid()),
    )
    completed = coding_lifecycle.run_supervisor(
        record["root_id"], config_path=tmp_path / "config", poll_interval=0.01
    )
    assert completed["state"] == "SUCCEEDED"
    assert states == []


def test_spawn_is_detached_and_needs_no_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    observed = {}

    class Process:
        pid = 8123

    def fake_popen(argv, **kwargs):
        observed["argv"] = argv
        observed.update(kwargs)
        return Process()

    monkeypatch.setattr(coding_lifecycle.subprocess, "Popen", fake_popen)
    pid = coding_lifecycle.spawn(
        "coding:" + "1" * 64, config_path=tmp_path / "config.json"
    )
    assert pid == 8123
    assert observed["start_new_session"] is True
    assert observed["close_fds"] is True
    assert observed["stdin"] is coding_lifecycle.subprocess.DEVNULL
    assert observed["stdout"] is coding_lifecycle.subprocess.DEVNULL
    assert observed["stderr"] is coding_lifecycle.subprocess.DEVNULL


def test_installed_config_has_only_complete_typed_lifecycle_registry():
    value = load_config(Path("config/tgw-coding-local.json"))
    assert value["coding"]["lifecycle_stages"] == (
        coding_lifecycle.TYPED_STAGE_IMPLEMENTATIONS
    )
    assert "lifecycle_commands" not in value["coding"]


def test_generic_lifecycle_command_configuration_is_rejected(tmp_path: Path):
    value = json.loads(Path("config/tgw-coding-local.json").read_text())
    value["coding"]["lifecycle_commands"] = {"review": ["/bin/sh"]}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(value))
    with pytest.raises(LocalCodingWorkflowError, match="generic.*forbidden"):
        load_config(path)


def test_cli_lifecycle_error_is_consolidated_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(
        coding_cli,
        "consolidated_status",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            LifecycleError("journal stale")
        ),
    )
    args = coding_cli.parser().parse_args(
        ["status", "coding:" + "1" * 64]
    )
    assert coding_cli.run(args) == 1
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "schema": "tgw-local-coding-error/v1",
        "ok": False,
        "operation": "status",
        "target": "coding:" + "1" * 64,
        "error": "journal stale",
        "error_type": "LifecycleError",
    }


def test_real_supervise_typed_handlers_complete_exact_mocked_surfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    worktree = tmp_path / "worktree"
    repository = tmp_path / "repository"
    receipts = tmp_path / "doctor-receipts"
    for path in (worktree, repository, receipts):
        path.mkdir()
    store = store_at(tmp_path / "journal")
    record = new(store, worktree)
    candidate_commit = "e" * 40
    candidate_tree = "f" * 40
    rows = []
    config = {
        "postgres_dsn": "test",
        "coding": {
            "lifecycle_root": str(store.root),
            "repository_root": str(repository),
            "doctor_receipt_root": str(receipts),
            "commands": {"claude-review": ["/typed/review"]},
            "allowed_runners": ["/typed/review"],
        },
    }
    monkeypatch.setattr(coding_cli, "_initialize", lambda _path: config)
    monkeypatch.setattr(
        coding_cli,
        "LifecycleStore",
        lambda root: LifecycleStore(root, group_gid=os.getegid()),
    )
    monkeypatch.setattr(coding_cli, "_jobs", lambda *_args, **_kwargs: list(rows))
    monkeypatch.setattr(
        coding_cli,
        "_plan_binding_for_todo",
        lambda _identifier: ({"agent": "codex"}, record["binding"]["plan_todo_binding"]),
    )
    monkeypatch.setattr(
        coding_cli,
        "classify",
        lambda *_args, **_kwargs: {
            "state": "CLOSED_CANDIDATE",
            "source": {"head": candidate_commit, "tree": candidate_tree},
        },
    )
    monkeypatch.setattr(
        coding_cli,
        "validate_implementation_lineage",
        lambda *_args, **_kwargs: {"attempt_hash": "sha256:" + "4" * 64},
    )

    def add_job(queue_name, receipt_name, extra=None):
        current = store.get(record["root_id"])
        fence = job_binding(current)
        value = {
            "status": "PASS",
            "outcome": "satisfied",
            "treatment_id": queue_name,
            "plan_binding": current["binding"]["plan_todo_binding"],
            "coding_lifecycle": fence,
            "artifacts": [],
            **(extra or {}),
        }
        (worktree / receipt_name).write_text(json.dumps(value))
        rows.append(
            {
                "job_id": f"job-{queue_name}",
                "queue_name": queue_name,
                "state": "succeeded",
                "attempt_count": 1,
                "payload_json": {
                    "todo_id": 1915,
                    "plan_binding": current["binding"]["plan_todo_binding"],
                    "coding_lifecycle": fence,
                    "result": value,
                },
            }
        )

    def fake_start(*_args, **kwargs):
        if kwargs["lifecycle_stage"] == "implementation":
            add_job("codex-implement", "implementation-receipt.json")
        else:
            add_job(
                "controller-verify",
                "controller-harness-receipt.json",
                {"implementation_attempt_hash": "sha256:" + "4" * 64},
            )
        return {"ok": True}

    monkeypatch.setattr(coding_cli, "start", fake_start)

    def fake_tick(*_args, **_kwargs):
        current = store.get(record["root_id"])
        candidate = current["effects"]["candidate"]["receipt"]
        candidate_fence = coding_lifecycle.candidate_job_binding(
            job_binding(current),
            commit=candidate["commit"],
            tree=candidate["tree"],
        )
        add_job(
            "claude-review",
            "review-receipt.json",
            {"coding_candidate": candidate_fence},
        )
        return coding_cli.TickResult(dispatched=1)

    monkeypatch.setattr(coding_cli, "tick", fake_tick)
    local = __import__("tgw.development.local_workflow", fromlist=["_git"])

    def fake_git(_repository, *args):
        if args == ("rev-parse", "HEAD"):
            return candidate_commit
        if args == ("rev-parse", "HEAD^{tree}"):
            return candidate_tree
        if args == ("status", "--porcelain=v1"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(local, "_git", fake_git)
    monkeypatch.setattr(
        coding_cli,
        "_doctor_receipt",
        lambda _root, *, operation, predicate: {
            "path": f"/{operation}.json",
            "file_sha256": "sha256:" + "5" * 64,
            "receipt_sha256": "sha256:" + "6" * 64,
            "receipt": {"operation": operation},
        },
    )

    waiting = coding_cli.supervise(
        record["root_id"], config_path=tmp_path / "config"
    )
    assert waiting["stage"] == "operator_readback"
    assert waiting["state"] == "WAITING"
    assert set(waiting["effects"]) == set(STAGES) - {"operator_readback"}
    record_operator_readback(
        store, record["root_id"], actor="operator", decision="accept"
    )
    completed = coding_cli.supervise(
        record["root_id"], config_path=tmp_path / "config"
    )
    assert completed["state"] == "SUCCEEDED"
    assert set(completed["effects"]) == set(STAGES)
    assert completed["effects"]["review"]["receipt"]["job_id"] == "job-claude-review"
