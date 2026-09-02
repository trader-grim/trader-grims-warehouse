import hashlib
import json
import os
import stat
import subprocess
import tarfile
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

import pytest

from tgw import coding_cli
from tgw.development import coding_lifecycle, coding_root_effect
from tgw.development.coding_lifecycle import (
    STAGES,
    LifecycleError,
    LifecycleStore,
    advance,
    build_binding,
    candidate_job_binding,
    create,
    job_binding,
    record_operator_readback,
    request_resume,
    stage_result,
    validate_implementation_intent_payload,
    validate_job_binding,
    validate_job_binding_payload,
)
from tgw.development.coding_review import (
    run_local_review,
    validate_failed_review_artifact,
    validate_review_artifact,
)
from tgw.development.coding_root_effect import (
    RootEffectError,
    RootEffectPaths,
    build_projection_request,
    build_request,
    ensure_projection_request,
    process_projection,
    process_request,
    validate_request,
)
from tgw.development.local_workflow import LocalCodingWorkflowError, load_config
from tgw.development.plan_binding import execution_root_hash
from tgw.protected_git import read_exact_tree_file, write_exact_tree_archive
from tgw.review_contract import ReviewRunnerError
from tgw.workers.coding import _write_receipt


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def plan_binding(
    worktree: Path,
    *,
    source: str = "c" * 40,
    source_tree: str = "d" * 40,
) -> dict:
    root = {"schema": "tgw-execution-root/v1", "kind": "todo", "todo_id": 1915}
    root["identity_hash"] = execution_root_hash(root)
    return {
        "schema": "tgw-plan-coding-todo/v1",
        "plan_commit": "a" * 40,
        "solution_hash": "sha256:" + "b" * 64,
        "closure_hash": "sha256:" + "1" * 64,
        "capability": "workflow.condition-derived-convergence@1",
        "treatment_id": coding_cli.DEFAULT_TREATMENT,
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
        "source_tree": source_tree,
    }


def store_at(path: Path) -> LifecycleStore:
    return LifecycleStore(path, group_gid=os.getegid())


def new(
    store: LifecycleStore,
    worktree: Path | None = None,
    *,
    source: str = "c" * 40,
    source_tree: str = "d" * 40,
) -> dict:
    selected = worktree or store.root.parent / "worktree"
    selected.mkdir(exist_ok=True)
    binding = build_binding(
        target=1915,
        plan_binding=plan_binding(selected, source=source, source_tree=source_tree),
        source_tree=source_tree,
    )
    return create(store, target=1915, binding=binding)


def review_result(
    record: dict,
    *,
    commit: str,
    tree: str,
    job_id: str = "review-job",
) -> dict:
    fence = job_binding(record)
    candidate = candidate_job_binding(fence, commit=commit, tree=tree)
    task = {
        "schema": "coding-task/v1",
        "todo_id": 1915,
        "agent": "codex",
        "body": "Implement the exact bounded headless lifecycle card.",
    }
    snapshot = digest(
        {"schema": "tgw-local-review-snapshot/v1", "commit": commit, "tree": tree}
    )
    report = {
        "schema": "tgw-code-review/v1",
        "verdict": "PASS",
        "snapshot_hash": snapshot,
        "summary": "independent diagnostic review passed",
        "findings": [],
    }
    context_unsigned = {
        "schema": "tgw-local-independent-review-context/v1",
        "mode": "exact-clean-candidate-semantic-review",
        "snapshot_hash": snapshot,
        "worktree": str(Path(record["binding"]["worktree"]).resolve()),
        "plan_commit": record["binding"]["plan_commit"],
        "source_commit": record["binding"]["source_commit"],
        "card_idempotency_key": fence["card_idempotency_key"],
        "candidate_binding_hash": candidate["candidate_binding_hash"],
        "task_spec_hash": digest(task),
    }
    execution_unsigned = {
        "schema": "tgw-local-independent-review-execution/v1",
        "actor": "codex",
        "uid": 1001,
        "pid": 1234,
        "service": "tgw-claude-review-worker.service",
        "queue": "claude-review",
        "network": True,
        "provider": "codex-ephemeral-read-only",
        "independence": {
            "separate_queue_job": True,
            "ephemeral_provider_session": True,
            "candidate_sandbox": "read-only",
            "authority": False,
        },
        "context": {
            **context_unsigned,
            "context_hash": digest(context_unsigned),
        },
    }
    artifact = {
        "kind": "tgw_review_report",
        "diagnostic_verdict": "PASS_NON_ADMITTING",
        "execution": {
            **execution_unsigned,
            "execution_hash": digest(execution_unsigned),
        },
        "root_id": record["root_id"],
        "binding_hash": record["binding"]["binding_hash"],
        "job_binding_hash": fence["job_binding_hash"],
        "job_id": job_id,
        "card_idempotency_key": fence["card_idempotency_key"],
        "candidate_binding_hash": candidate["candidate_binding_hash"],
        "candidate_commit": commit,
        "candidate_tree": tree,
        "report": report,
        "report_bytes": canonical(report).decode(),
        "report_sha256": "sha256:" + hashlib.sha256(canonical(report)).hexdigest(),
        "checks": [
            {
                "name": name,
                "returncode": 0,
                "output_sha256": "sha256:" + "7" * 64,
            }
            for name in ("git-diff-check",)
        ],
    }
    return {
        "status": "PASS",
        "todo_id": 1915,
        "outcome": "satisfied",
        "treatment_id": "claude-review",
        "established_conditions": ["reviewed"],
        "artifacts": [artifact],
        "plan_binding": record["binding"]["plan_todo_binding"],
        "coding_lifecycle": fence,
        "coding_candidate": candidate,
        "task_spec": task,
    }


def record_at_integration(
    store: LifecycleStore,
    worktree: Path,
    *,
    source: str = "c" * 40,
    source_tree: str = "d" * 40,
    commit: str = "e" * 40,
    tree: str = "f" * 40,
) -> dict:
    record = new(store, worktree, source=source, source_tree=source_tree)
    candidate = {
        "schema": "tgw-local-coding-candidate-evidence/v1",
        "root_id": record["root_id"],
        "binding_hash": record["binding"]["binding_hash"],
        "worktree": str(worktree),
        "commit": commit,
        "tree": tree,
        "classification": "CLOSED_CANDIDATE",
    }
    reviewed = review_result(record, commit=commit, tree=tree)
    review_evidence = {
        "schema": "tgw-local-coding-queue-evidence/v1",
        "root_id": record["root_id"],
        "binding_hash": record["binding"]["binding_hash"],
        "job_id": "review-job",
        "result": reviewed,
    }
    receipts = {
        "controller": {"schema": "controller", "status": "PASS"},
        "candidate": candidate,
        "review": review_evidence,
        "integration": {
            "schema": "tgw-local-coding-integration/v1",
            "candidate_commit": commit,
            "candidate_tree": tree,
        },
    }
    record["effects"] = {
        stage: {
            "receipt": receipt,
            "receipt_hash": digest(receipt),
            "idempotency_key": coding_lifecycle.stage_idempotency_key(record, stage),
        }
        for stage, receipt in receipts.items()
    }
    record["stage"] = "materialization"
    record["state"] = "WAITING"
    return store.put(record)


def root_paths(tmp_path: Path, store: LifecycleStore, worktree: Path) -> RootEffectPaths:
    effects = tmp_path / "root-effects"
    effects.mkdir(mode=0o2750)
    effects.chmod(0o2750)
    return RootEffectPaths(
        request_root=effects,
        lifecycle_root=store.root,
        repository=worktree,
        runtime_root=tmp_path / "runtime",
        coding_config=tmp_path / "coding.json",
        restart_ack=tmp_path / "restart-ack",
        group_gid=os.getegid(),
        root_uid=os.geteuid(),
    )


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


def test_foreign_root_owned_exact_lifecycle_directory_is_validated_not_mutated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "root-owned-lifecycle"
    root.mkdir(mode=0o2770)
    root.chmod(0o2770)
    store = LifecycleStore(root, group_gid=os.getegid())
    monkeypatch.setattr(
        coding_lifecycle.os, "geteuid", lambda: os.getuid() + 1000
    )
    monkeypatch.setattr(
        coding_lifecycle.os,
        "fchown",
        lambda *_args: pytest.fail("foreign exact root must not be mutated"),
    )
    monkeypatch.setattr(
        coding_lifecycle.os,
        "fchmod",
        lambda *_args: pytest.fail("foreign exact root must not be mutated"),
    )
    store._prepare_root()


def test_managed_service_reconstructs_persisted_nonterminal_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    unit = Path("systemd/tgw-coding-lifecycle-supervisor.service").read_text()
    assert "Type=simple" in unit
    assert "Restart=always" in unit
    assert "--managed" in unit
    assert "KillMode=" not in unit
    assert "sudo" not in unit

    store = store_at(tmp_path / "journal")
    record = new(store)
    local = __import__("tgw.development.local_workflow", fromlist=["load_config"])
    monkeypatch.setattr(
        local,
        "load_config",
        lambda _path: {"coding": {"lifecycle_root": str(store.root)}},
    )
    monkeypatch.setattr(
        coding_lifecycle,
        "LifecycleStore",
        lambda _root: LifecycleStore(store.root, group_gid=os.getegid()),
    )
    calls = []

    def supervise(identity, *, config_path):
        calls.append((identity, Path(config_path)))
        current = store.get(identity)
        current["state"] = "TECHNICALLY_COMPLETE"
        return store.put(current)

    monkeypatch.setattr(coding_cli, "supervise", supervise)
    observed = coding_lifecycle.run_managed_supervisor(
        config_path=tmp_path / "coding.json", once=True
    )
    assert observed == [
        {"root_id": record["root_id"], "state": "TECHNICALLY_COMPLETE"}
    ]
    assert calls == [(record["root_id"], tmp_path / "coding.json")]


def test_pp_start_aliases_exactly_one_smallest_todo_root_and_refuses_ambiguity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    binding = plan_binding(worktree)
    row = {
        "id": 1915,
        "pp_ref": coding_cli.PP_REF,
        "done_at": None,
        "status_note": json.dumps(binding),
    }
    solution = {
        "plan_commit": binding["plan_commit"],
        "solution_hash": binding["solution_hash"],
        "closure_hash": binding["closure_hash"],
        "work_units": [
            {
                "id": coding_cli.DEFAULT_TREATMENT,
                "capability": binding["capability"],
            }
        ],
    }
    projection = {
        "pp_ref": coding_cli.PP_REF,
        "resolver_binding": {"agreement": "verified"},
        "solution": {
            "conformance_verified": True,
            "solution_hash": binding["solution_hash"],
        },
    }
    config = {
        "postgres_dsn": "test",
        "coding": {"lifecycle_root": str(tmp_path / "journal")},
    }
    monkeypatch.setattr(coding_cli, "_initialize", lambda _path: config)
    monkeypatch.setattr(
        coding_cli,
        "_pp_runtime_binding",
        lambda *_args: {
            "selected_commit": binding["source_commit"],
            "selected_tree": "d" * 40,
        },
    )
    monkeypatch.setattr(coding_cli.todo, "todo_list", lambda **_kwargs: [row])
    monkeypatch.setattr(coding_cli.todo, "todo_get", lambda _identifier: row)
    monkeypatch.setattr(coding_cli, "reconcile_pp_workflow", lambda **_kwargs: projection)
    monkeypatch.setattr(
        coding_cli, "_plan_binding_for_todo", lambda _identifier: (row, binding)
    )
    monkeypatch.setattr(
        coding_cli,
        "start",
        lambda *_args, **kwargs: {
            "ok": True,
            "dispatch_jobs": kwargs["dispatch_jobs"],
        },
    )
    monkeypatch.setattr(
        coding_cli,
        "LifecycleStore",
        lambda root: LifecycleStore(root, group_gid=os.getegid()),
    )
    local = __import__("tgw.development.local_workflow", fromlist=["load_solution"])
    monkeypatch.setattr(local, "load_solution", lambda _path: solution)

    pp = coding_cli.lifecycle_start(coding_cli.PP_REF, config_path=tmp_path / "x")
    todo = coding_cli.lifecycle_start(1915, config_path=tmp_path / "x")
    assert pp["root_id"] == todo["root_id"]
    assert pp["target"] == "1915"
    assert pp["pp_alias"]["todo_id"] == 1915
    assert pp["supervisor"] == "tgw-coding-lifecycle-supervisor.service"
    assert pp["returns_immediately"] is True
    monkeypatch.setattr(coding_cli.todo, "todo_list", lambda **_kwargs: [row, row])
    with pytest.raises(coding_cli.CodingCLIError, match="ambiguous"):
        coding_cli.lifecycle_start(coding_cli.PP_REF, config_path=tmp_path / "x")


def test_todo_lifecycle_start_creates_missing_unix_worktree_binding_and_returns_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    worktree = tmp_path / "todo-1921-plan-bound"
    worktree.mkdir()
    binding = plan_binding(worktree)
    row = {"id": 1915, "done_at": None, "status_note": None}
    solution = {
        "plan_commit": binding["plan_commit"],
        "solution_hash": binding["solution_hash"],
        "closure_hash": binding["closure_hash"],
    }
    config = {
        "postgres_dsn": "test",
        "coding": {"lifecycle_root": str(tmp_path / "journal")},
    }
    calls = []
    bound = False

    def exact_binding(_identifier):
        if not bound:
            raise coding_cli.CodingCLIError("Todo 1915 has no exact Plan/Todo binding")
        return row, binding

    def prepare(*_args, **kwargs):
        nonlocal bound
        calls.append(kwargs)
        bound = True
        return {"ok": True, "session": {"cwd": str(worktree)}}

    monkeypatch.setattr(coding_cli, "_initialize", lambda _path: config)
    monkeypatch.setattr(
        coding_cli,
        "_pp_runtime_binding",
        lambda *_args: {
            "selected_commit": binding["source_commit"],
            "selected_tree": "d" * 40,
        },
    )
    monkeypatch.setattr(coding_cli, "_plan_binding_for_todo", exact_binding)
    monkeypatch.setattr(coding_cli, "start", prepare)
    monkeypatch.setattr(
        coding_cli,
        "LifecycleStore",
        lambda root: LifecycleStore(root, group_gid=os.getegid()),
    )
    local = __import__("tgw.development.local_workflow", fromlist=["load_solution"])
    monkeypatch.setattr(local, "load_solution", lambda _path: solution)

    result = coding_cli.lifecycle_start(1915, config_path=tmp_path / "x")

    assert calls == [
        {
            "config_path": tmp_path / "x",
            "solution_path": coding_cli.DEFAULT_SOLUTION,
            "source_commit": binding["source_commit"],
            "dispatch_jobs": False,
        }
    ]
    assert result["binding_created"] is True
    assert result["session"]["cwd"] == str(worktree)
    assert result["session"]["argv"] == ["codex", "-C", str(worktree)]
    assert result["session"]["observer"] == ["tgw", "coding", "status", "1915"]


def test_resume_intent_is_durable_before_dispatch_and_retries_use_new_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store = store_at(tmp_path / "journal")
    record = new(store)
    partial = advance(
        store,
        record["root_id"],
        {
            "implementation": lambda current: stage_result(
                current,
                "implementation",
                "resumable_partial",
                reason="candidate interrupted",
            )
        },
    )
    old_fence = job_binding(partial)
    intent = {
        "schema": "tgw-local-coding-lifecycle-resume-intent/v1",
        "root_id": record["root_id"],
        "binding_hash": record["binding"]["binding_hash"],
        "todo_id": 1915,
        "resume_of": "sha256:" + "3" * 64,
        "resume_fingerprint": "sha256:" + "4" * 64,
        "worktree": record["binding"]["worktree"],
        "source_commit": record["binding"]["source_commit"],
        "source_tree": record["binding"]["source_tree"],
    }
    reopened = request_resume(store, record["root_id"], receipt=intent)
    new_fence = job_binding(reopened)
    assert reopened["state"] == "WAITING"
    assert new_fence == old_fence
    assert request_resume(store, record["root_id"], receipt=intent) == reopened

    rows = [
        {
            "job_id": "old",
            "queue_name": "codex-implement",
            "payload_json": {
                "coding_lifecycle": old_fence,
                "plan_binding": record["binding"]["plan_todo_binding"],
            },
        },
        {
            "job_id": "new",
            "queue_name": "codex-implement",
            "payload_json": {
                "coding_lifecycle": new_fence,
                "implementation_intent_hash": coding_lifecycle.implementation_intent_hash(reopened),
                "plan_binding": record["binding"]["plan_todo_binding"],
            },
        },
    ]
    monkeypatch.setattr(coding_cli, "_jobs", lambda *_args, **_kwargs: rows)
    assert [
        row["job_id"]
        for row in coding_cli._bound_jobs(reopened, "codex-implement")
    ] == ["new"]


def test_fast_terminal_resume_job_consumes_intent_before_next_partial(
    tmp_path: Path,
) -> None:
    store = store_at(tmp_path / "journal")
    record = new(store)
    partial = advance(
        store,
        record["root_id"],
        {
            "implementation": lambda current: stage_result(
                current,
                "implementation",
                "resumable_partial",
                reason="first partial",
            )
        },
    )
    intent = {
        "schema": "tgw-local-coding-lifecycle-resume-intent/v1",
        "root_id": record["root_id"],
        "binding_hash": record["binding"]["binding_hash"],
        "todo_id": 1915,
        "resume_of": "sha256:" + "3" * 64,
        "resume_fingerprint": "sha256:" + "4" * 64,
        "worktree": record["binding"]["worktree"],
        "source_commit": record["binding"]["source_commit"],
        "source_tree": record["binding"]["source_tree"],
    }
    reopened = request_resume(store, partial["root_id"], receipt=intent)
    first_resume_fence = job_binding(reopened)
    terminal = advance(
        store,
        record["root_id"],
        {
            "implementation": lambda current: stage_result(
                current,
                "implementation",
                "resumable_partial",
                reason="worker completed before queue read",
                job_ids=["fast-terminal-job"],
            )
        },
    )
    assert terminal["state"] == "RESUMABLE_PARTIAL"
    assert terminal.get("resume_intent") is None
    assert terminal["active_implementation_generation"]["intent_hash"] == (
        coding_lifecycle.implementation_intent_hash(reopened)
    )
    next_resume = request_resume(store, terminal["root_id"], receipt=intent)
    assert job_binding(next_resume) == first_resume_fence
    assert coding_lifecycle.implementation_intent_hash(next_resume) != (
        coding_lifecycle.implementation_intent_hash(reopened)
    )


def test_disposable_start_partial_resume_closes_exact_successor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Exercise the real worker→journal→CLI resume seam without a provider."""
    from tgw.development.partial_resume import candidate_changed_paths, history
    from tgw.workers.coding import CodingWorker

    repository = tmp_path / "repository"
    worktree_root = tmp_path / "worktrees"
    worktree = worktree_root / "todo-1915-disposable-canary"
    repository.mkdir()
    worktree_root.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "canary@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Disposable Coding Canary"],
        cwd=repository,
        check=True,
    )
    (repository / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "base.txt"], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-m", "canary base"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    source = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    source_tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=repository,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    subprocess.run(
        [
            "git",
            "worktree",
            "add",
            "-b",
            "coding/canary/start-partial-resume",
            str(worktree),
            source,
        ],
        cwd=repository,
        check=True,
        capture_output=True,
    )

    binding = plan_binding(worktree, source=source, source_tree=source_tree)
    row = {
        "id": 1915,
        "agent": "codex",
        "done_at": None,
        "status_note": json.dumps(binding),
    }
    lifecycle_root = tmp_path / "lifecycles"
    config = {
        "postgres_dsn": "disposable-canary",
        "coding": {
            "lifecycle_root": str(lifecycle_root),
            "repository_root": str(repository),
            "worktree_root": str(worktree_root),
        },
    }
    store = store_at(lifecycle_root)
    solution = {
        "plan_commit": binding["plan_commit"],
        "solution_hash": binding["solution_hash"],
        "closure_hash": binding["closure_hash"],
    }
    local = __import__("tgw.development.local_workflow", fromlist=["load_solution"])
    monkeypatch.setattr(coding_cli, "_initialize", lambda _path: config)
    monkeypatch.setattr(
        coding_cli,
        "_pp_runtime_binding",
        lambda *_args: {
            "selected_commit": source,
            "selected_tree": source_tree,
        },
    )
    monkeypatch.setattr(
        coding_cli, "_plan_binding_for_todo", lambda _identifier: (row, binding)
    )
    monkeypatch.setattr(coding_cli.todo, "todo_get", lambda _identifier: row)
    monkeypatch.setattr(local, "load_solution", lambda _path: solution)
    monkeypatch.setattr(
        coding_cli,
        "LifecycleStore",
        lambda _root: LifecycleStore(lifecycle_root, group_gid=os.getegid()),
    )

    rows = []

    def dispatched_start(todo_id, **kwargs):
        record = store.find(todo_id)
        assert record is not None
        stage = kwargs["lifecycle_stage"]
        queue = {
            "implementation": "codex-implement",
            "controller": "controller-verify",
        }[stage]
        payload = {
            "treatment_id": queue,
            "treatment_version": "1",
            "graph_id": f"canary-{stage}-{len(rows) + 1}",
            "object_id": str(worktree),
            "object_generation": f"canary-generation-{len(rows) + 1}",
            "todo_id": todo_id,
            "todo_agent": "codex",
            "worktree": str(worktree),
                "plan_binding": binding,
                "coding_lifecycle": kwargs["lifecycle_job_binding"],
                **(
                    {"implementation_intent_hash": kwargs["lifecycle_intent_hash"]}
                    if kwargs.get("lifecycle_intent_hash") is not None
                    else {}
                ),
                **(
                    {
                        "implementation_intent": dict(
                            kwargs["lifecycle_implementation_intent"]
                        )
                    }
                    if kwargs.get("lifecycle_implementation_intent") is not None
                    else {}
                ),
            }
        if kwargs.get("resume_only"):
            observed = coding_cli.classify(
                worktree,
                {
                    "todo_id": todo_id,
                    "plan_commit": binding["plan_commit"],
                    "solution_hash": binding["solution_hash"],
                    "source_commit": source,
                    "source_tree": source_tree,
                    "actor": "codex",
                    "worktree": str(worktree),
                    "treatment_id": "codex-implement",
                    "treatment_version": "1",
                },
            )
            payload.update(
                {
                    "resume_of": observed["resume_of"],
                    "resume_fingerprint": observed["fingerprint"],
                }
            )
        rows.append(
            {
                "job_id": f"canary-{queue}-{len(rows) + 1}",
                "queue_name": queue,
                "state": "queued",
                "attempt_count": 1,
                "lease_token": (
                    "00000000-0000-4000-8000-"
                    f"{len(rows) + 1:012d}"
                ),
                "payload_json": payload,
            }
        )
        return {"ok": True}

    monkeypatch.setattr(coding_cli, "start", dispatched_start)
    monkeypatch.setattr(coding_cli, "_jobs", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr("tgw.queue.worker_base.state_machine.init", lambda _dsn: None)
    monkeypatch.setattr("tgw.apis.nats_client.init_nats", lambda _config: None)

    def queue_row(job_id):
        return next(row for row in rows if row["job_id"] == job_id)

    def mark_running(job_id, owner, lease_token):
        queue_row(job_id).update(
            state="running", lease_owner=owner, lease_token=lease_token
        )

    def mark_dead_letter(job_id, _owner, _lease_token, detail, *, result=None):
        job = queue_row(job_id)
        job.update(
            state="dead_letter",
            error_code="HARD_FAILURE",
            error_detail=detail,
            finished_at="2026-08-28T00:00:00+00:00",
            lease_owner=None,
            lease_token=None,
        )
        job["payload_json"]["result"] = result

    def close_local_success(job_id, _owner, _lease_token, result, publish):
        compensators = []
        publish(compensators.append)
        assert len(compensators) == 1
        job = queue_row(job_id)
        job.update(
            state="succeeded",
            finished_at="2026-08-28T00:01:00+00:00",
            lease_owner=None,
            lease_token=None,
            error_code=None,
            error_detail=None,
        )
        job["payload_json"]["result"] = result
        return True

    monkeypatch.setattr(
        "tgw.queue.worker_base.state_machine.mark_running", mark_running
    )
    monkeypatch.setattr(
        "tgw.queue.worker_base.state_machine.mark_dead_letter", mark_dead_letter
    )
    monkeypatch.setattr(
        "tgw.queue.worker_base.state_machine.close_local_success",
        close_local_success,
    )
    monkeypatch.setattr(
        "tgw.queue.worker_base.state_machine.get_job", queue_row
    )
    monkeypatch.setattr("tgw.notify.notify", lambda *_args, **_kwargs: None)

    started = coding_cli.lifecycle_start(1915, config_path=tmp_path / "coding.json")
    assert started["state"] == "QUEUED"
    first_fence = job_binding(store.find(1915))
    waiting = coding_cli.supervise(started["root_id"], config_path=tmp_path / "coding.json")
    assert (waiting["state"], waiting["stage"]) == ("WAITING", "implementation")
    first_job = rows[0]

    def preserve_partial(_treatment, _payload, selected_worktree):
        (selected_worktree / "partial.txt").write_text(
            "preserved partial bytes\n", encoding="utf-8"
        )
        return {
            "outcome": "partial",
            "established_conditions": [],
            "artifacts": [{"kind": "canary", "detail": "intentional partial"}],
        }

    worker = CodingWorker("codex-implement", config, launcher=preserve_partial)
    worker._process(first_job)
    first_result = first_job["payload_json"]["result"]
    assert first_job["error_detail"] == (
        "TreatmentFailure('coding treatment reported partial')"
    )
    rows[0] = {**first_job, "job_id": "forged-canary-job"}
    rejected = coding_cli._queue_evidence(
        waiting,
        stage="implementation",
        queue_name="codex-implement",
        receipt_name="implementation-receipt.json",
        dispatch=lambda: pytest.fail("terminal partial must not redispatch"),
    )
    assert rejected["outcome"] == "failed"
    assert "no exact resumable lineage" in rejected["reason"]
    rows[0] = first_job

    receipt_path = worktree / "implementation-receipt.json"
    exact_receipt_bytes = receipt_path.read_bytes()
    forged_receipt = json.loads(exact_receipt_bytes)
    forged_receipt["artifacts"] = [
        {"kind": "forged", "detail": "not the queue-persisted result"}
    ]
    receipt_path.write_text(
        json.dumps(forged_receipt, sort_keys=True) + "\n", encoding="utf-8"
    )
    rejected = coding_cli._queue_evidence(
        waiting,
        stage="implementation",
        queue_name="codex-implement",
        receipt_name="implementation-receipt.json",
        dispatch=lambda: pytest.fail("terminal partial must not redispatch"),
    )
    assert rejected["outcome"] == "failed"
    assert "durable terminal queue provenance" in rejected["reason"]
    receipt_path.write_bytes(exact_receipt_bytes)

    first_job["state"] = "cancelled"
    rejected = coding_cli._queue_evidence(
        waiting,
        stage="implementation",
        queue_name="codex-implement",
        receipt_name="implementation-receipt.json",
        dispatch=lambda: pytest.fail("cancelled partial must not redispatch"),
    )
    assert rejected["outcome"] == "failed"
    assert "durable terminal queue provenance" in rejected["reason"]
    first_job["state"] = "dead_letter"

    partial = coding_cli.supervise(
        started["root_id"], config_path=tmp_path / "coding.json"
    )
    assert (partial["state"], partial["stage"]) == (
        "RESUMABLE_PARTIAL",
        "implementation",
    ), (partial.get("failure"), partial.get("stages", {}).get("implementation"))
    assert partial["stages"]["implementation"]["receipt"]["state"] == (
        "RESUMABLE_PARTIAL"
    )

    resumed = coding_cli.resume(1915, config_path=tmp_path / "coding.json")
    assert resumed["schema"] == "tgw-local-coding-resume/v2"
    assert resumed["ok"] is True
    assert resumed["lifecycle_state"] == "WAITING"
    reopened = store.find(1915)
    assert reopened is not None
    assert job_binding(reopened) == first_fence

    waiting = coding_cli.supervise(
        started["root_id"], config_path=tmp_path / "coding.json"
    )
    assert (waiting["state"], waiting["stage"]) == ("WAITING", "implementation")
    second_job = rows[1]
    assert second_job["payload_json"]["resume_of"] == resumed["coding_state"]["resume_of"]
    second_fence = second_job["payload_json"]["coding_lifecycle"]
    assert waiting.get("resume_intent") is None
    assert waiting["active_implementation_generation"]["kind"] == "resume"
    assert job_binding(waiting) == second_fence

    worker = CodingWorker("codex-implement", config, launcher=preserve_partial)
    worker._process(second_job)
    second_result = second_job["payload_json"]["result"]
    attempts = history(worktree)
    assert [attempt["outcome"] for attempt in attempts] == ["partial", "partial"]
    current_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert current_receipt == second_result
    assert current_receipt["coding_lifecycle"] == second_fence
    assert current_receipt["implementation_attempt_hash"] == attempts[-1][
        "attempt_hash"
    ]
    archived_receipt = (
        worktree
        / ".tgw-coding-history"
        / "implementation"
        / "receipts"
        / (attempts[0]["attempt_hash"].removeprefix("sha256:") + ".json")
    )
    assert json.loads(archived_receipt.read_text(encoding="utf-8")) == (
        first_result
    )

    partial_again = coding_cli.supervise(
        started["root_id"], config_path=tmp_path / "coding.json"
    )
    assert (partial_again["state"], partial_again["stage"]) == (
        "RESUMABLE_PARTIAL",
        "implementation",
    )
    # Old journals can have reached the second partial before one-shot intent
    # consumption existed. The CLI must rotate that stale live intent rather
    # than returning a false resume_reused no-op forever.
    stale_generation = partial_again.pop("active_implementation_generation")
    partial_again["resume_intent"] = stale_generation["intent"]
    store.put(partial_again)
    resumed_again = coding_cli.resume(1915, config_path=tmp_path / "coding.json")
    assert "resume_reused" not in resumed_again
    assert resumed_again["resume_intent_hash"] != resumed["resume_intent_hash"]
    assert job_binding(store.find(1915)) == second_fence
    waiting = coding_cli.supervise(
        started["root_id"], config_path=tmp_path / "coding.json"
    )
    assert (waiting["state"], waiting["stage"]) == ("WAITING", "implementation")
    third_job = rows[2]
    assert third_job["payload_json"]["coding_lifecycle"] == second_fence
    assert third_job["payload_json"]["implementation_intent_hash"] != (
        second_job["payload_json"]["implementation_intent_hash"]
    )

    def close_successor(_treatment, _payload, selected_worktree):
        subprocess.run(
            ["git", "add", "partial.txt"], cwd=selected_worktree, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "close disposable canary"],
            cwd=selected_worktree,
            check=True,
            capture_output=True,
        )
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=selected_worktree,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=selected_worktree,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        return {
            "outcome": "satisfied",
            "established_conditions": ["implemented"],
            "artifacts": [
                {
                    "kind": "closed_candidate",
                    "commit": head,
                    "tree": tree,
                    "base_commit": source,
                    "changed_paths": candidate_changed_paths(
                        selected_worktree, source, head
                    ),
                }
            ],
        }

    worker = CodingWorker("codex-implement", config, launcher=close_successor)
    worker._process(third_job)
    advanced = coding_cli.supervise(
        started["root_id"], config_path=tmp_path / "coding.json"
    )
    assert (advanced["state"], advanced["stage"]) == ("WAITING", "controller")
    assert [attempt["outcome"] for attempt in history(worktree)] == [
        "partial",
        "partial",
        "satisfied",
    ]
    assert rows[3]["queue_name"] == "controller-verify"


def test_controller_finding_automatically_starts_one_fenced_remediation_generation(
    tmp_path: Path,
) -> None:
    store = store_at(tmp_path / "journal")
    record = new(store)
    resume_intent = {
        "schema": "tgw-local-coding-lifecycle-resume-intent/v1",
        "resume_intent_hash": "sha256:" + "8" * 64,
    }
    record["resume_intent"] = resume_intent
    record = store.put(record)
    original_fence = job_binding(record)
    implementation_calls = 0

    def implementation(current):
        nonlocal implementation_calls
        implementation_calls += 1
        if implementation_calls == 1:
            return stage_result(
                current,
                "implementation",
                "satisfied",
                receipt={"generation": 1},
            )
        return stage_result(
            current,
            "implementation",
            "waiting",
            reason="remediation worker pending",
        )

    def controller(current):
        return stage_result(
            current,
            "controller",
            "remediation",
            receipt={
                "schema": "tgw-local-coding-negative-queue-evidence/v1",
                "candidate": {"head": "e" * 40, "tree": "f" * 40},
            },
            reason="missing source-bound test",
            job_ids=["controller-job-1"],
        )

    observed = advance(
        store,
        record["root_id"],
        {"implementation": implementation, "controller": controller},
    )

    assert observed["state"] == "WAITING"
    assert observed["stage"] == "implementation"
    assert implementation_calls == 2
    assert len(observed["remediation_history"]) == 1
    assert observed["remediation_intent"]["failed_stage"] == "controller"
    assert observed["remediation_intent"]["candidate_commit"] == "e" * 40
    assert "resume_intent" not in observed
    assert observed["remediation_history"][0]["resume_intent"] == resume_intent
    assert job_binding(observed) == original_fence


def test_implementation_failure_auto_rebinds_bounded(tmp_path: Path) -> None:
    store = store_at(tmp_path / "journal")
    record = new(store)
    implementation_calls = 0

    def implementation(current):
        nonlocal implementation_calls
        implementation_calls += 1
        return stage_result(
            current,
            "implementation",
            "failed",
            reason="worker dead-lettered with no candidate",
        )

    observed = advance(
        store,
        record["root_id"],
        {"implementation": implementation},
    )

    # Third failed implementation exhausts the budget and goes FAILED.
    assert observed["state"] == "FAILED"
    assert observed["stage"] == "implementation"
    assert observed["auto_rebind_count"] == 2
    assert len(observed["remediation_history"]) == 2
    assert observed["remediation_history"][0]["kind"] == "auto_rebind"
    assert observed["remediation_history"][0]["generation"] == 1
    assert observed["remediation_history"][1]["generation"] == 2
    assert "budget" in observed["failure"]["reason"] or observed["failure"]["reason"] == "worker dead-lettered with no candidate"
    # The implementation handler ran three times: initial + two auto-rebinds.
    assert implementation_calls == 3


def test_remediation_outcome_never_auto_rebinds(tmp_path: Path) -> None:
    store = store_at(tmp_path / "journal")
    record = new(store)

    def implementation(current):
        return stage_result(
            current,
            "implementation",
            "remediation",
            reason="resumable partial requires exact operator resume",
        )

    observed = advance(
        store,
        record["root_id"],
        {"implementation": implementation},
    )

    assert observed["state"] == "REMEDIATION_REQUIRED"
    assert observed.get("auto_rebind_count") in (None, 0)


@pytest.mark.parametrize("remediation_rounds", [1, 2, 3])
def test_review_failures_auto_remediate_with_stable_generation_binding(
    tmp_path: Path, remediation_rounds: int
) -> None:
    store = store_at(tmp_path / "journal")
    record = new(store)
    review_calls = 0
    implementation_calls = 0
    generation_fences: list[dict] = []
    carried_findings: list[list[dict]] = []

    def implementation(current):
        nonlocal implementation_calls
        implementation_calls += 1
        result = stage_result(
            current,
            "implementation",
            "satisfied",
            receipt={"generation": implementation_calls},
            job_ids=[f"implementation-{implementation_calls}"],
        )
        if current.get("remediation_intent") is not None:
            carried_findings.append(
                current["remediation_intent"]["diagnostic_findings"]
            )
            carried = job_binding(current)
            consumed = dict(current)
            coding_lifecycle._bind_active_implementation_generation(consumed, result)
            validate_job_binding(consumed, carried)
            generation_fences.append(carried)
        return result

    def satisfied(stage):
        return lambda current: stage_result(
            current, stage, "satisfied", receipt={"stage": stage}
        )

    def review(current):
        nonlocal review_calls
        review_calls += 1
        if review_calls > remediation_rounds:
            return stage_result(
                current, "review", "satisfied", receipt={"verdict": "PASS"}
            )
        finding = {
            "severity": "high",
            "message": f"review finding round {review_calls}",
        }
        receipt_path = tmp_path / "review-receipt.json"
        failed_receipt = {
            "status": "FAIL",
            "outcome": "failed",
            "treatment_id": "claude-review",
            "object_id": current["binding"]["worktree"],
            "plan_binding": current["binding"]["plan_todo_binding"],
            "coding_lifecycle": job_binding(current),
            "artifacts": [{"findings": [finding]}],
        }
        _write_receipt(receipt_path, failed_receipt)
        validate_job_binding(
            current, json.loads(receipt_path.read_text())["coding_lifecycle"]
        )
        return stage_result(
            current,
            "review",
            "remediation",
            receipt={
                "candidate": {"head": "e" * 40, "tree": "f" * 40},
                "findings": [finding],
            },
            reason=finding["message"],
            job_ids=[f"review-{review_calls}"],
        )

    handlers = {stage: satisfied(stage) for stage in STAGES}
    handlers.update({"implementation": implementation, "review": review})
    observed = advance(store, record["root_id"], handlers)

    assert observed["state"] == "TECHNICALLY_COMPLETE"
    assert len(observed["remediation_history"]) == remediation_rounds
    assert implementation_calls == remediation_rounds + 1
    assert len({item["job_binding_hash"] for item in generation_fences}) == 1
    assert carried_findings == [
        [
            {
                "severity": "high",
                "message": f"review finding round {index}",
            }
        ]
        for index in range(1, remediation_rounds + 1)
    ]
    for index, archived in enumerate(observed["remediation_history"], start=1):
        assert archived["failure_result"]["receipt"]["findings"] == [
            {
                "severity": "high",
                "message": f"review finding round {index}",
            }
        ]


def test_review_remediation_stops_after_maximum_rounds(tmp_path: Path) -> None:
    store = store_at(tmp_path / "journal")
    record = new(store)
    stable_binding = job_binding(record)
    review_calls = 0
    intent_hashes = []

    def satisfied(stage):
        def result(current):
            assert job_binding(current) == stable_binding
            if stage == "implementation" and current.get("remediation_intent"):
                intent_hashes.append(
                    coding_lifecycle.implementation_intent_hash(current)
                )
            return stage_result(
                current,
                stage,
                "satisfied",
                receipt={"stage": stage},
                job_ids=(
                    [f"implementation-{len(current.get('remediation_history', []))}"]
                    if stage == "implementation"
                    else []
                ),
            )
        return result

    def review(current):
        nonlocal review_calls
        review_calls += 1
        return stage_result(
            current,
            "review",
            "remediation",
            receipt={
                "candidate": {"head": "e" * 40, "tree": "f" * 40},
                "findings": [{"message": f"still failing {review_calls}"}],
            },
            reason="diagnostic findings remain",
        )

    handlers = {stage: satisfied(stage) for stage in STAGES}
    handlers["review"] = review
    observed = advance(store, record["root_id"], handlers)

    assert observed["state"] == "REMEDIATION_REQUIRED"
    assert observed["stage"] == "review"
    assert len(observed["remediation_history"]) == 3
    assert job_binding(observed) == stable_binding
    assert len(intent_hashes) == 3
    assert len(set(intent_hashes)) == 3
    assert review_calls == 4
    assert observed["failure"]["reason"] == "diagnostic findings remain"


def test_resume_recovers_worker_completed_crash_boundary_with_new_fenced_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store = store_at(tmp_path / "journal")
    record = new(store)
    partial_receipt = {
        "state": "RESUMABLE_PARTIAL",
        "resume_of": "sha256:" + "3" * 64,
        "fingerprint": "sha256:" + "4" * 64,
    }
    record["state"] = "RESUMABLE_PARTIAL"
    record["stage"] = "candidate"
    record["stages"]["candidate"] = {
        "outcome": "resumable_partial",
        "idempotency_key": coding_lifecycle.stage_idempotency_key(
            record, "candidate"
        ),
        "receipt": partial_receipt,
        "receipt_hash": digest(partial_receipt),
    }
    record["failure"] = {
        "stage": "candidate",
        "reason": "resume worker not yet reconciled",
    }
    record = store.put(record)
    config = {
        "postgres_dsn": "test",
        "coding": {"lifecycle_root": str(store.root)},
    }
    monkeypatch.setattr(coding_cli, "_initialize", lambda _path: config)
    monkeypatch.setattr(
        coding_cli,
        "LifecycleStore",
        lambda _root: LifecycleStore(store.root, group_gid=os.getegid()),
    )
    monkeypatch.setattr(
        coding_cli.todo,
        "todo_get",
        lambda _identifier: {"id": 1915, "agent": "codex"},
    )
    monkeypatch.setattr(
        coding_cli,
        "exclusive_worktree_lease",
        lambda _worktree: nullcontext(),
    )
    closed = {
        "state": "CLOSED_CANDIDATE",
        "source": {"head": "e" * 40, "tree": "f" * 40},
    }
    monkeypatch.setattr(coding_cli, "classify", lambda *_args: closed)
    resumed = coding_cli.resume(1915, config_path=tmp_path / "coding.json")
    reopened = store.get(record["root_id"])
    assert resumed["coding_state"] == closed
    assert reopened["resume_intent"]["resume_of"] == partial_receipt["resume_of"]
    assert reopened["resume_intent"]["resume_fingerprint"] == partial_receipt[
        "fingerprint"
    ]

    captured = []
    item = {
        "id": 1915,
        "agent": "codex",
        "body": "bounded",
        "priority": 1,
        "done_at": None,
    }
    monkeypatch.setattr(coding_cli.todo, "todo_get", lambda _identifier: item)
    monkeypatch.setattr(
        coding_cli,
        "bind_command",
        lambda _args: pytest.fail("managed lifecycle attempted to rebind"),
    )
    monkeypatch.setattr(
        coding_cli,
        "_plan_binding_for_todo",
        lambda _identifier: (
            item,
            record["binding"]["plan_todo_binding"],
        ),
    )
    monkeypatch.setattr(
        coding_cli,
        "source_tree",
        lambda *_args: record["binding"]["source_tree"],
    )
    monkeypatch.setattr(coding_cli, "_jobs", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(coding_cli, "require_coder_account", lambda: "codex")

    def dispatch(config_value, *, todo_ids):
        captured.append((config_value, todo_ids))
        return coding_cli.TickResult(dispatched=1)

    monkeypatch.setattr(coding_cli, "tick", dispatch)
    launched = coding_cli.start(
        1915,
        config_path=tmp_path / "coding.json",
        source_commit=record["binding"]["source_commit"],
        resume_only=True,
        lifecycle_job_binding=job_binding(reopened),
        lifecycle_stage="implementation",
    )
    assert launched["foreman"]["dispatched"] == 1
    assert captured[0][0].lifecycle_rebind == {1915: "codex-implement"}
    assert captured[0][1] == {1915}

    with pytest.raises(
        coding_cli.CodingCLIError,
        match="lifecycle source differs from the exact Todo binding",
    ):
        coding_cli.start(
            1915,
            config_path=tmp_path / "coding.json",
            source_commit="9" * 40,
            lifecycle_job_binding=job_binding(reopened),
            lifecycle_stage="implementation",
        )


def test_review_runner_and_both_receipt_boundaries_require_semantic_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    worktree = tmp_path / "worktree"
    subprocess.run(["git", "init", "-q", worktree], check=True)
    subprocess.run(
        ["git", "config", "user.email", "review@example.invalid"],
        cwd=worktree,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Review Test"], cwd=worktree, check=True
    )
    (worktree / "pyproject.toml").write_text(
        "[project]\nname='review'\nversion='1'\n"
    )
    subprocess.run(["git", "add", "."], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-qm", "candidate"], cwd=worktree, check=True)
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=worktree, text=True
    ).strip()
    tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=worktree, text=True
    ).strip()
    store = store_at(tmp_path / "journal")
    record = new(store, worktree)
    payload = review_result(record, commit=commit, tree=tree)
    payload["job_id"] = "review-job"
    payload["todo_agent"] = "implementation-actor"

    def passing_runner(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 0, "checked", "")

    def semantic_backend(request, cwd):
        assert cwd == worktree
        assert request["output_contract"] == "tgw-code-review/v1"
        assert request["review_context"]["task_spec"] == payload["task_spec"]
        assert request["review_context"]["review_mode"] == "NON_ADMITTING_DIAGNOSTIC"
        return {
            "schema": "tgw-code-review/v1",
            "verdict": "PASS",
            "snapshot_hash": request["snapshot_hash"],
            "summary": "ephemeral semantic review found no defects",
            "findings": [],
        }

    monkeypatch.setattr(
        "tgw.development.coding_review.pwd.getpwuid",
        lambda _uid: type("Identity", (), {"pw_name": "review-actor"})(),
    )
    result = run_local_review(
        payload,
        worktree,
        runner=passing_runner,
        semantic_backend=semantic_backend,
    )
    artifact = validate_review_artifact(
        result,
        payload=payload,
        worktree=worktree,
        expected_job_id="review-job",
    )
    assert artifact["diagnostic_verdict"] == "PASS_NON_ADMITTING"
    assert artifact["report"]["verdict"] == "PASS"
    assert artifact["report"]["findings"] == []
    assert artifact["execution"]["provider"] == "codex-ephemeral-read-only"
    assert artifact["execution"]["independence"]["authority"] is False
    assert artifact["checks"]
    failed = run_local_review(
        payload,
        worktree,
        runner=passing_runner,
        semantic_backend=lambda request, _cwd: {
            "schema": "tgw-code-review/v1",
            "verdict": "FAIL",
            "snapshot_hash": request["snapshot_hash"],
            "summary": "semantic defect remains",
            "findings": [
                {
                    "severity": "high",
                    "path": "pyproject.toml",
                    "line": 1,
                    "message": "bounded task behavior is incomplete",
                }
            ],
        },
    )
    assert failed["outcome"] == "failed"
    assert failed["established_conditions"] == []
    assert failed["artifacts"][0]["diagnostic_verdict"] == "FAIL"
    failed_artifact = validate_failed_review_artifact(
        failed,
        payload=payload,
        worktree=worktree,
        expected_job_id="review-job",
    )
    assert failed_artifact["report"]["findings"][0]["message"] == (
        "bounded task behavior is incomplete"
    )
    fixed_check_failed = run_local_review(
        payload,
        worktree,
        runner=lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 1, "", "fixed check failed"
        ),
        semantic_backend=semantic_backend,
    )
    fixed_check_artifact = validate_failed_review_artifact(
        fixed_check_failed,
        payload=payload,
        worktree=worktree,
        expected_job_id="review-job",
    )
    assert fixed_check_artifact["report"]["findings"] == [
        {
            "severity": "high",
            "path": "pyproject.toml",
            "line": 1,
            "message": "fixed independent check failed: git-diff-check",
        }
    ]
    with pytest.raises(ReviewRunnerError, match="outcome conditions"):
        validate_review_artifact(
            failed,
            payload=payload,
            worktree=worktree,
            expected_job_id="review-job",
        )
    with pytest.raises(ReviewRunnerError, match="one report artifact"):
        validate_review_artifact(
            {**result, "artifacts": []},
            payload=payload,
            worktree=worktree,
            expected_job_id="review-job",
        )

    candidate_receipt = {"commit": commit, "tree": tree}
    record["effects"]["candidate"] = {
        "receipt": candidate_receipt,
        "receipt_hash": digest(candidate_receipt),
    }
    queue_result = review_result(record, commit=commit, tree=tree)
    (worktree / "review-receipt.json").write_text(json.dumps(queue_result))
    rows = [
        {
            "job_id": "review-job",
            "queue_name": "claude-review",
            "state": "succeeded",
            "attempt_count": 1,
            "payload_json": {**queue_result, "result": queue_result},
        }
    ]
    monkeypatch.setattr(coding_cli, "_jobs", lambda *_args, **_kwargs: rows)
    evidence = coding_cli._queue_evidence(
        record,
        stage="review",
        queue_name="claude-review",
        receipt_name="review-receipt.json",
        dispatch=lambda: pytest.fail("exact completed review must be reused"),
    )
    assert evidence["outcome"] == "satisfied"
    queue_result["artifacts"] = []
    (worktree / "review-receipt.json").write_text(json.dumps(queue_result))
    rows[0]["payload_json"]["result"] = queue_result
    refused = coding_cli._queue_evidence(
        record,
        stage="review",
        queue_name="claude-review",
        receipt_name="review-receipt.json",
        dispatch=lambda: None,
    )
    assert refused["outcome"] == "remediation"

    failed_receipt = {
        **failed,
        "status": "FAIL",
        "treatment_id": "claude-review",
        "plan_binding": payload["plan_binding"],
        "coding_lifecycle": payload["coding_lifecycle"],
        "coding_candidate": payload["coding_candidate"],
        "task_spec": payload["task_spec"],
    }
    (worktree / "review-receipt.json").write_text(json.dumps(failed_receipt))
    rows[:] = [
        {
            "job_id": "review-job",
            "queue_name": "claude-review",
            "state": "failed",
            "attempt_count": 1,
            "payload_json": payload,
        }
    ]
    negative = coding_cli._queue_evidence(
        record,
        stage="review",
        queue_name="claude-review",
        receipt_name="review-receipt.json",
        dispatch=lambda: None,
    )
    assert negative["outcome"] == "remediation"
    assert "bounded task behavior is incomplete" in negative["reason"]
    assert negative["receipt"]["findings"] == failed_artifact["report"]["findings"]
    transitioned = dict(record)
    assert coding_lifecycle._begin_bounded_remediation(
        transitioned, stage="review", result=negative
    )
    assert transitioned["remediation_intent"]["diagnostic_findings"] == (
        failed_artifact["report"]["findings"]
    )

    failed_receipt["artifacts"][0]["job_id"] = "forged-review-job"
    (worktree / "review-receipt.json").write_text(json.dumps(failed_receipt))
    rejected = coding_cli._queue_evidence(
        record,
        stage="review",
        queue_name="claude-review",
        receipt_name="review-receipt.json",
        dispatch=lambda: None,
    )
    assert rejected["outcome"] == "failed"
    assert "invalid" in rejected["reason"]


def test_root_request_rejects_forbidden_fields_and_is_idempotent_after_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", repository], check=True)
    subprocess.run(
        ["git", "config", "user.email", "root@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Root Effect Test"],
        cwd=repository,
        check=True,
    )
    (repository / "pyproject.toml").write_text(
        "[project]\nname='root-effect'\nversion='1'\n"
    )
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repository, check=True)
    source = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()
    source_tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=repository, text=True
    ).strip()
    (repository / "source.py").write_text("RESULT = 1915\n")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "candidate"], cwd=repository, check=True)
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()
    tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=repository, text=True
    ).strip()
    store = store_at(tmp_path / "journal")
    record = record_at_integration(
        store,
        repository,
        source=source,
        source_tree=source_tree,
        commit=commit,
        tree=tree,
    )
    paths = root_paths(tmp_path, store, repository)
    request = build_request(record)
    assert validate_request(request, store=store)[0] == request
    with pytest.raises(RootEffectError, match="forbidden field"):
        validate_request({**request, "argv": ["anything"]}, store=store)
    stale = dict(request)
    stale["candidate_tree"] = "9" * 40
    stale_unsigned = {
        key: item for key, item in stale.items() if key != "request_hash"
    }
    stale["request_hash"] = digest(stale_unsigned)
    with pytest.raises(RootEffectError, match="differs from lifecycle"):
        validate_request(stale, store=store)

    request_file = coding_root_effect.request_path(paths, record["root_id"])
    request_file.write_bytes(canonical({"schema": "invalid"}) + b"\n")
    request_file.chmod(0o660)
    assert coding_root_effect.consume_once(paths) == 0
    refusal_file = coding_root_effect._refusal_path(paths, request_file)
    assert coding_root_effect._refusal_applies(
        paths, refusal_file, request_file
    )
    request_file.unlink()
    coding_root_effect.ensure_request(paths, record)
    assert not coding_root_effect._refusal_applies(
        paths, refusal_file, request_file
    )
    observed_requests = []
    monkeypatch.setattr(
        coding_root_effect,
        "process_request",
        lambda _paths, exact, *, store: observed_requests.append(
            (exact["request_hash"], store.root)
        ),
    )
    assert coding_root_effect.consume_once(paths) == 1
    assert observed_requests == [(request["request_hash"], store.root)]

    monkeypatch.setattr(
        coding_root_effect,
        "_runtime_canary",
        lambda _paths, _root_id: {
            "schema": "tgw-local-coding-disconnect-restart-canary/v1",
            "disposable": True,
            "canary_hash": "sha256:" + "8" * 64,
        },
    )
    restart_evidence = {
        "schema": "tgw-local-coding-static-restart-acknowledgement/v1",
        "candidate_commit": commit,
        "services": {"fixed": "active"},
        "acknowledgement_hash": "sha256:" + "7" * 64,
    }
    monkeypatch.setattr(
        coding_root_effect,
        "_restart_acknowledged",
        lambda _paths, _trigger, **_kwargs: restart_evidence,
    )
    monkeypatch.setattr(
        coding_root_effect,
        "_apply_release_ownership_via_bootstrap",
        lambda _paths, _commit: {"status": "stubbed"},
    )
    first_effects = coding_root_effect._default_effects(paths, request)
    assert first_effects["selection"]["state"] == "completed"
    assert first_effects["workers"] == restart_evidence
    recovered = process_request(paths, request, store=store)
    replay = process_request(paths, request, store=store)
    assert recovered == replay
    assert recovered["status"] == "PASS"
    assert recovered["candidate_commit"] == commit
    assert recovered["technical_result_hash"].startswith("sha256:")
    restart = json.loads((paths.request_root / ".restart-request").read_text())
    assert restart == {
        "schema": "tgw-local-coding-static-restart-request/v1",
        "candidate_commit": commit,
    }
    response_file = coding_root_effect.response_path(paths, record["root_id"])
    assert response_file.stat().st_uid == paths.root_uid
    assert stat.S_IMODE(response_file.stat().st_mode) == 0o640
    response_file.chmod(0o660)
    with pytest.raises(RootEffectError, match="ownership/type/mode"):
        coding_root_effect.read_response(paths, request)
    response_file.chmod(0o640)
    assert (
        paths.runtime_root / "releases" / commit / ".release-manifest.json"
    ).is_file()


def test_exact_archive_ignores_repository_config_and_info_attributes(
    tmp_path: Path,
):
    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", repository], check=True)
    subprocess.run(
        ["git", "config", "user.email", "archive@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Archive Test"],
        cwd=repository,
        check=True,
    )
    (repository / "kept.txt").write_text("exact bytes\n")
    executable = repository / "run"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "candidate"], cwd=repository, check=True)
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()
    tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=repository, text=True
    ).strip()
    info_attributes = repository / ".git/info/attributes"
    info_attributes.write_text("kept.txt export-ignore\nrun -export-subst\n")
    subprocess.run(
        ["git", "config", "tar.umask", "0777"], cwd=repository, check=True
    )
    archive = tmp_path / "candidate.tar"
    archive.touch()
    write_exact_tree_archive(
        repository, commit=commit, tree=tree, destination=archive
    )
    mode, exact_bytes = read_exact_tree_file(
        repository, commit=commit, tree=tree, path="kept.txt"
    )
    assert mode == 0o644
    assert exact_bytes == b"exact bytes\n"
    (repository / "kept.txt").write_text("worktree replacement\n")
    assert exact_bytes == b"exact bytes\n"
    with tarfile.open(archive) as stream:
        members = {member.name: member for member in stream.getmembers()}
        assert set(members) == {"kept.txt", "run"}
        assert members["kept.txt"].mode == 0o644
        assert members["run"].mode == 0o755
        assert stream.extractfile(members["kept.txt"]).read() == b"exact bytes\n"


def test_db_bootstrap_materializes_without_context_lifecycle_or_review(
    tmp_path: Path,
):
    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", repository], check=True)
    subprocess.run(
        ["git", "config", "user.email", "bootstrap@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Bootstrap Test"],
        cwd=repository,
        check=True,
    )
    (repository / "source.py").write_text("READY = True\n")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "candidate"], cwd=repository, check=True)
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()
    tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=repository, text=True
    ).strip()
    effects = tmp_path / "effects"
    effects.mkdir(mode=0o2750)
    effects.chmod(0o2750)
    paths = RootEffectPaths(
        request_root=effects,
        lifecycle_root=tmp_path / "absent-lifecycle",
        repository=repository,
        runtime_root=tmp_path / "runtime",
        coding_config=tmp_path / "coding.json",
        group_gid=os.getegid(),
        root_uid=os.geteuid(),
    )
    receipt = coding_root_effect.bootstrap_candidate(
        paths, commit=commit, tree=tree
    )
    assert receipt["actor"] == __import__("pwd").getpwuid(os.geteuid()).pw_name
    assert receipt["commit"] == commit
    assert receipt["tree"] == tree
    assert receipt["receipt_hash"].startswith("sha256:")
    assert (paths.runtime_root / "current").resolve() == (
        paths.runtime_root / "releases" / commit
    ).resolve()


def test_lifecycle_selection_converges_after_bootstrap_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", repository], check=True)
    subprocess.run(
        ["git", "config", "user.email", "bootstrap@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Bootstrap Test"],
        cwd=repository,
        check=True,
    )
    (repository / "source.py").write_text("READY = True\n")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "candidate"], cwd=repository, check=True)
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()
    tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=repository, text=True
    ).strip()
    store = store_at(tmp_path / "journal")
    record = record_at_integration(
        store,
        repository,
        source=commit,
        source_tree=tree,
        commit=commit,
        tree=tree,
    )
    paths = root_paths(tmp_path, store, repository)
    coding_root_effect.bootstrap_candidate(paths, commit=commit, tree=tree)
    request = build_request(record)
    monkeypatch.setattr(
        coding_root_effect,
        "_restart_acknowledged",
        lambda _paths, _trigger, **_kwargs: {
            "schema": "restart",
            "candidate_commit": commit,
        },
    )
    monkeypatch.setattr(
        coding_root_effect,
        "_runtime_canary",
        lambda _paths, _root_id: {"schema": "canary", "status": "PASS"},
    )
    monkeypatch.setattr(
        coding_root_effect,
        "_apply_release_ownership_via_bootstrap",
        lambda _paths, _commit: {"status": "stubbed"},
    )

    effects = coding_root_effect._default_effects(paths, request)

    assert effects["selection"]["state"] == "completed"
    assert effects["selection"]["operation_id"].startswith("coding-")
    assert effects["selection"]["previous_generation"] == commit
    assert effects["selection"]["selected_generation"] == commit


def test_context_projection_is_terminal_bound_and_retry_state_is_cadenced(
    tmp_path: Path,
):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "pyproject.toml").write_text("[project]\n")
    store = store_at(tmp_path / "journal")
    record = record_at_integration(store, worktree)
    materialization = {"schema": "materialization", "status": "PASS"}
    live = {
        "schema": "live",
        "status": "PASS",
        "technical_result_hash": "sha256:" + "9" * 64,
    }
    for stage, receipt in (
        ("materialization", materialization),
        ("live_verification", live),
    ):
        record["effects"][stage] = {
            "receipt": receipt,
            "receipt_hash": digest(receipt),
            "idempotency_key": coding_lifecycle.stage_idempotency_key(record, stage),
        }
    record["stage"] = "terminal_publication"
    record = store.put(record)
    paths = root_paths(tmp_path, store, worktree)
    request = ensure_projection_request(paths, record)
    assert request == build_projection_request(record)
    assert request["root_id"] == record["root_id"]

    calls = []

    def publisher(_paths, exact):
        calls.append(exact["projection_hash"])
        return {
            "path": "/exact/context-receipt.json",
            "file_sha256": "sha256:" + "5" * 64,
            "receipt_sha256": "sha256:" + "6" * 64,
            "task_file_sha256": "sha256:" + "7" * 64,
        }

    response = process_projection(paths, request, publisher=publisher, store=store)
    assert response["status"] == "PUBLISHED"
    assert response["result_hash"] == request["result_hash"]
    assert response["context_task_file_sha256"] == "sha256:" + "7" * 64
    assert process_projection(paths, request, publisher=publisher, store=store) == response
    assert calls == [request["projection_hash"]]

    retry_root = tmp_path / "retry"
    retry_root.mkdir()
    retry_paths = root_paths(retry_root, store, worktree)
    coding_root_effect._defer_projection(retry_paths, request, "Context offline")
    retry = coding_root_effect._projection_retry_path(retry_paths, record["root_id"])
    before = retry.read_bytes()
    before_mtime = retry.stat().st_mtime_ns
    assert not coding_root_effect._projection_is_due(retry_paths, request)
    assert retry.read_bytes() == before
    assert retry.stat().st_mtime_ns == before_mtime

    tampered = dict(request)
    tampered["live_verification_receipt_hash"] = "sha256:" + "0" * 64
    unsigned = {
        key: item for key, item in tampered.items() if key != "projection_hash"
    }
    tampered["projection_hash"] = digest(unsigned)
    with pytest.raises(RootEffectError, match="differs from lifecycle"):
        coding_root_effect.validate_projection_request(tampered, store=store)


def test_terminal_context_task_projection_is_single_cas_and_non_authoritative(
    tmp_path: Path,
):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    store = store_at(tmp_path / "journal")
    record = record_at_integration(store, worktree)
    for stage, receipt in (
        ("materialization", {"schema": "materialization", "status": "PASS"}),
        (
            "live_verification",
            {
                "schema": "live",
                "status": "PASS",
                "technical_result_hash": "sha256:" + "9" * 64,
            },
        ),
    ):
        record["effects"][stage] = {
            "receipt": receipt,
            "receipt_hash": digest(receipt),
            "idempotency_key": coding_lifecycle.stage_idempotency_key(record, stage),
        }
    record["stage"] = "terminal_publication"
    record = store.put(record)
    request = build_projection_request(record)
    task_path = tmp_path / "current-task.json"
    task = {
        "schema": "tgw-current-task/v1",
        "id": "bounded-task",
        "updated_at": "2026-08-24T00:00:00+00:00",
        "plan": {"approved_commit": record["binding"]["plan_commit"]},
        "implementation": {
            "development_source": {
                "commit": record["binding"]["source_commit"],
                "tree": record["binding"]["source_tree"],
                "next_leaf": "workflow.condition-derived-convergence@1",
            },
            "coding_workflow": {
                "commit": record["binding"]["source_commit"]
            },
        },
    }
    task_path.write_text(json.dumps(task, sort_keys=True) + "\n")
    paths = replace(root_paths(tmp_path, store, worktree), context_task=task_path)

    first = coding_root_effect._project_terminal_task(paths, request)
    projected_bytes = task_path.read_bytes()
    projected = json.loads(projected_bytes)
    second = coding_root_effect._project_terminal_task(paths, request)

    assert first == second
    assert task_path.read_bytes() == projected_bytes
    assert projected["plan"] == task["plan"]
    assert projected["id"] == task["id"]
    implementation = projected["implementation"]
    assert (
        implementation["development_source"]["commit"]
        == request["candidate_commit"]
    )
    assert implementation["development_source"]["tree"] == request["candidate_tree"]
    assert (
        implementation["coding_workflow"]["commit"]
        == request["candidate_commit"]
    )
    terminal = implementation["coding_lifecycle_result"]
    assert terminal["result_hash"] == request["result_hash"]
    assert terminal["operator_acceptance"] == "PENDING"

    projected["implementation"]["development_source"]["tree"] = "0" * 40
    task_path.write_text(json.dumps(projected, sort_keys=True) + "\n")
    with pytest.raises(RootEffectError, match="exact lifecycle binding"):
        coding_root_effect._project_terminal_task(paths, request)


def test_context_outage_does_not_block_technical_completion_or_rewrite_early(
    tmp_path: Path,
):
    store = store_at(tmp_path / "journal")
    record = new(store)
    for stage in STAGES[:-2]:
        receipt = {"schema": "prior", "status": "PASS", "stage": stage}
        record["effects"][stage] = {
            "receipt": receipt,
            "receipt_hash": digest(receipt),
            "idempotency_key": coding_lifecycle.stage_idempotency_key(record, stage),
        }
        record["stages"][stage] = stage_result(
            record, stage, "satisfied", receipt=receipt
        )
    record["stage"] = "terminal_publication"
    record["state"] = "WAITING"
    record = store.put(record)
    attempts = []

    def unavailable(current):
        attempts.append(current["root_id"])
        return stage_result(
            current,
            "terminal_publication",
            "publication_unavailable",
            reason="Context unavailable",
        )

    def notify(current):
        return stage_result(
            current,
            "operator_notification",
            "satisfied",
            receipt={
                "schema": "notification",
                "root_id": current["root_id"],
                "operator_acceptance": "PENDING",
            },
        )

    completed = advance(
        store,
        record["root_id"],
        {
            "terminal_publication": unavailable,
            "operator_notification": notify,
        },
    )
    assert completed["state"] == "TECHNICALLY_COMPLETE"
    assert completed["publication"]["pending"] is True
    assert completed["operator_acceptance"] == "PENDING"
    path = store.path(record["root_id"])
    before = path.read_bytes()
    mtime = path.stat().st_mtime_ns
    assert (
        advance(
            store,
            record["root_id"],
            {"terminal_publication": unavailable},
        )
        == completed
    )
    assert attempts == [record["root_id"]]
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == mtime


def test_operator_readback_never_mints_acceptance_and_cli_has_no_decision_tools(
    tmp_path: Path,
):
    store = store_at(tmp_path / "journal")
    record = new(store)
    notification = {"schema": "notification", "root_id": record["root_id"]}
    record["operator"]["notification"] = {
        "receipt": notification,
        "receipt_hash": digest(notification),
    }
    store.put(record)
    read = record_operator_readback(store, record["root_id"], actor="operator")
    assert read["operator_acceptance"] == "PENDING"
    assert read["operator"]["acceptance"] == "PENDING"
    parser = coding_cli.parser()
    for forbidden in ("accept", "reject"):
        with pytest.raises(SystemExit):
            parser.parse_args([forbidden, record["root_id"]])
    from tgw import coding_mcp_server

    assert not hasattr(coding_mcp_server, "tgw_coding_operator_readback")


def test_managed_supervisor_canary_uses_two_real_disposable_subprocesses(
    tmp_path: Path,
):
    store = store_at(tmp_path / "journal")
    record = new(store)
    worktree = Path(record["binding"]["worktree"])
    paths = root_paths(tmp_path, store, worktree)
    runtime = paths.runtime_root / "current"
    runtime.parent.mkdir(parents=True)
    runtime.symlink_to(Path.cwd(), target_is_directory=True)
    config = {
        "schema": "tgw-local-coding-workflow/v1",
        "postgres_dsn": "test",
        "coding": {
            "lifecycle_root": str(store.root),
            "root_effect_root": str(paths.request_root),
            "doctor_receipt_root": str(tmp_path / "doctor"),
            "runtime_root": str(paths.runtime_root),
            "repository_root": str(worktree),
            "worktree_root": str(tmp_path / "worktrees"),
            "commands": {"claude-review": ["/fixed/reviewer"]},
            "allowed_runners": ["/fixed/reviewer"],
            "lifecycle_stages": coding_lifecycle.TYPED_STAGE_IMPLEMENTATIONS,
        },
    }
    paths.coding_config.write_text(json.dumps(config))
    result = coding_root_effect._runtime_canary(paths, record["root_id"])
    assert result["disposable"] is True
    assert [item["phase"] for item in result["phases"]] == [
        "disconnect",
        "restart",
    ]
    assert all(item["returncode"] == 0 for item in result["phases"])
    assert all(
        item["root_id"] == record["root_id"]
        and item["journal_sha256"].startswith("sha256:")
        for item in result["phases"]
    )


def test_installed_config_and_services_are_exact_and_forbid_broad_effects():
    from tgw import doctor_cli

    value = load_config(Path("config/tgw-coding-local.json"))
    assert value["coding"]["lifecycle_stages"] == (
        coding_lifecycle.TYPED_STAGE_IMPLEMENTATIONS
    )
    assert value["coding"]["commands"]["claude-review"] == [
        "/opt/TGW/tgw-lib/coding-runtime/current/bin/tgw-local-independent-review-runner"
    ]
    assert "lifecycle_commands" not in value["coding"]
    managed = (
        "tgw-claude-review-worker.service",
        "tgw-coding-lifecycle-supervisor.service",
        "tgw-coding-root-effect.service",
    )
    assert set(managed) <= set(doctor_cli._ACTIVE_CODING_UNITS)
    assert set(doctor_cli._CODING_SUPPORT_ROOT_KEYS) == {
        "preservation_archive_root",
        "runner_state_root",
        "lifecycle_root",
        "root_effect_root",
    }
    assert doctor_cli._UNIT_ARGV[managed[1]][-1] == "--managed"
    assert doctor_cli._UNIT_ARGV[managed[2]][2:4] == (
        "tgw.development.coding_root_effect",
        "--config",
    )
    for name in managed:
        body = (Path("systemd") / name).read_text().lower()
        assert "[install]" in body
        assert "type=simple" in body
        if name == "tgw-claude-review-worker.service":
            assert "tgw_codex_review_bin=/home/codex/.local/bin/codex" in body
            assert "tgw_codex_review_auth=/home/codex/.codex/auth.json" in body
            assert "privatenetwork=true" not in body
        if name == "tgw-coding-root-effect.service":
            assert "user=db" in body
            assert "group=tgw-coders" in body
            assert "user=root" not in body
        for forbidden in ("ssh ", "tgw-prod", "approval", "admission", "remote"):
            assert forbidden not in body
    restart_path = Path("systemd/tgw-coding-runtime-restart.path").read_text().lower()
    restart_service = Path("systemd/tgw-coding-runtime-restart.service").read_text().lower()
    assert "pathchanged=/opt/tgw/var/tgw-coders/coding-root-effects/.restart-request" in restart_path
    assert "execstart=/bin/systemctl restart" in restart_service
    assert "tgw-plan-render-local.service" in restart_service
    assert "tgw-coding-root-effect.service" in restart_service
    assert "/run/tgw-coding-runtime-restart/complete" in restart_service
    assert "python" not in restart_service
    assert "tgw.development" not in restart_service


def test_generic_lifecycle_command_configuration_is_rejected(tmp_path: Path):
    value = json.loads(Path("config/tgw-coding-local.json").read_text())
    value["coding"]["lifecycle_commands"] = {"review": ["/bin/sh"]}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(value))
    with pytest.raises(LocalCodingWorkflowError, match="generic.*forbidden"):
        load_config(path)


def test_status_and_log_are_consolidated_without_acceptance_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store = store_at(tmp_path / "journal")
    record = new(store)
    config = {"postgres_dsn": "test", "coding": {"lifecycle_root": str(store.root)}}
    monkeypatch.setattr(coding_cli, "_initialize", lambda _path: config)
    monkeypatch.setattr(coding_cli, "load_config", lambda _path: config)
    monkeypatch.setattr(
        coding_cli,
        "LifecycleStore",
        lambda _root: LifecycleStore(store.root, group_gid=os.getegid()),
    )
    monkeypatch.setattr(coding_cli, "_jobs", lambda *_args, **_kwargs: [])
    status = coding_cli.consolidated_status(
        record["root_id"], config_path=tmp_path / "config"
    )
    assert status["root_id"] == record["root_id"]
    assert status["operator_acceptance"] == "PENDING"
    monkeypatch.setattr(
        coding_cli.state_machine,
        "get_job",
        lambda job_id: {
            "job_id": job_id,
            "queue_name": "claude-review",
            "state": "succeeded",
            "payload_json": {"result": {"status": "PASS"}},
        },
    )
    logged = coding_cli.job_log("review-job", config_path=tmp_path / "config")
    assert logged["job_id"] == "review-job"
    assert logged["payload_json"]["result"]["status"] == "PASS"


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


def test_worker_effect_fence_requires_exact_typed_implementation_intent(
    tmp_path: Path,
):
    record = new(store_at(tmp_path / "journal"))
    lifecycle = job_binding(record)
    unsigned = {
        "schema": "tgw-local-coding-remediation-intent/v1",
        "root_id": lifecycle["root_id"],
        "binding_hash": lifecycle["binding_hash"],
        "generation": 2,
        "failed_stage": "review",
        "failure_receipt_hash": "sha256:" + "3" * 64,
        "reason": "diagnostic finding",
        "candidate_commit": "4" * 40,
        "candidate_tree": "5" * 40,
        "requested_at": "2026-08-29T12:00:00+00:00",
        "diagnostic_findings": [{"severity": "high", "message": "finding"}],
    }
    intent_hash = coding_lifecycle._hash(unsigned)
    intent = {**unsigned, "remediation_intent_hash": intent_hash}

    assert validate_implementation_intent_payload(
        intent, claimed_hash=intent_hash, lifecycle_binding=lifecycle
    ) == intent
    with pytest.raises(LifecycleError, match="payload is absent"):
        validate_implementation_intent_payload(None, claimed_hash=intent_hash)
    with pytest.raises(LifecycleError, match="hash mismatch"):
        validate_implementation_intent_payload(
            intent, claimed_hash="sha256:" + "9" * 64
        )
    with pytest.raises(LifecycleError, match="absent or invalid"):
        validate_implementation_intent_payload(intent, claimed_hash=None)

    minimal = {"schema": unsigned["schema"], "generation": 2}
    minimal_hash = coding_lifecycle._hash(minimal)
    with pytest.raises(LifecycleError, match="incomplete or malformed"):
        validate_implementation_intent_payload(
            {**minimal, "remediation_intent_hash": minimal_hash},
            claimed_hash=minimal_hash,
            lifecycle_binding=lifecycle,
        )

    foreign_unsigned = {**unsigned, "root_id": "foreign-root"}
    foreign_hash = coding_lifecycle._hash(foreign_unsigned)
    with pytest.raises(LifecycleError, match="lifecycle identity mismatch"):
        validate_implementation_intent_payload(
            {**foreign_unsigned, "remediation_intent_hash": foreign_hash},
            claimed_hash=foreign_hash,
            lifecycle_binding=lifecycle,
        )

    resume_unsigned = {
        "schema": "tgw-local-coding-lifecycle-resume-intent/v1",
        "root_id": lifecycle["root_id"],
        "binding_hash": lifecycle["binding_hash"],
        "todo_id": int(record["target"]),
        "resume_of": "sha256:" + "6" * 64,
        "resume_fingerprint": "sha256:" + "7" * 64,
        "worktree": record["binding"]["worktree"],
        "source_commit": record["binding"]["source_commit"],
        "source_tree": record["binding"]["source_tree"],
        "generation": 1,
        "requested_at": "2026-08-29T12:00:00+00:00",
    }
    resume_hash = coding_lifecycle._hash(resume_unsigned)
    resume = {**resume_unsigned, "resume_intent_hash": resume_hash}
    assert validate_implementation_intent_payload(
        resume, claimed_hash=resume_hash, lifecycle_binding=lifecycle
    ) == resume


def _bootstrapped_release(tmp_path: Path):
    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", repository], check=True)
    subprocess.run(
        ["git", "config", "user.email", "ownership@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Ownership Test"], cwd=repository, check=True
    )
    (repository / "source.py").write_text("READY = True\n")
    (repository / "pkg").mkdir()
    (repository / "pkg" / "mod.py").write_text("NESTED = True\n")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "candidate"], cwd=repository, check=True)
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()
    tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=repository, text=True
    ).strip()
    store = store_at(tmp_path / "journal")
    paths = root_paths(tmp_path, store, repository)
    coding_root_effect.bootstrap_candidate(paths, commit=commit, tree=tree)
    return paths, commit


def test_selected_release_ownership_flags_a_materializer_owned_tree(tmp_path: Path):
    paths, commit = _bootstrapped_release(tmp_path)

    # Default context_install_uid is 0 (root:root); an ordinary db materialization
    # leaves the tree owned by the running account, so it is flagged.
    observed = coding_root_effect._selected_release_ownership(paths, commit)
    assert observed["root_owned_immutable"] is False
    assert any(entry.endswith(":owner") for entry in observed["unsafe"])

    # When the promotion target is the running account the same tree is accepted.
    local = replace(
        paths,
        context_install_uid=os.geteuid(),
        context_install_gid=os.getegid(),
    )
    accepted = coding_root_effect._selected_release_ownership(local, commit)
    assert accepted["root_owned_immutable"] is True
    assert accepted["unsafe"] == []


def test_ensure_selected_release_root_owned_promotes_inline_when_privileged(
    tmp_path: Path,
):
    paths, commit = _bootstrapped_release(tmp_path)
    local = replace(
        paths,
        context_install_uid=os.geteuid(),
        context_install_gid=os.getegid(),
    )
    request = {"candidate_commit": commit}
    result = coding_root_effect._ensure_selected_release_root_owned(local, request)
    assert result["status"] == "already-root-owned"
    assert result["ownership"]["root_owned_immutable"] is True


def test_ensure_selected_release_root_owned_delegates_bounded_root_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths, commit = _bootstrapped_release(tmp_path)
    seen: list[str] = []
    monkeypatch.setattr(
        coding_root_effect,
        "_apply_release_ownership_via_bootstrap",
        lambda _paths, candidate: seen.append(candidate) or {"status": "invoked"},
    )
    result = coding_root_effect._ensure_selected_release_root_owned(
        paths, {"candidate_commit": commit}
    )
    assert seen == [commit]
    assert result["status"] == "delegated-to-pinned-bootstrap"
    assert result["bootstrap"] == {"status": "invoked"}


def test_apply_release_ownership_via_bootstrap_reports_missing_pin(tmp_path: Path):
    paths, commit = _bootstrapped_release(tmp_path)
    absent = replace(paths, coding_bootstrap=tmp_path / "no-such-bootstrap")
    outcome = coding_root_effect._apply_release_ownership_via_bootstrap(absent, commit)
    assert outcome["status"] == "pinned-bootstrap-unavailable"


def test_default_effects_gates_workers_ack_on_root_owned_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", repository], check=True)
    subprocess.run(
        ["git", "config", "user.email", "gate@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Gate Test"], cwd=repository, check=True
    )
    (repository / "source.py").write_text("READY = True\n")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "candidate"], cwd=repository, check=True)
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()
    tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=repository, text=True
    ).strip()
    store = store_at(tmp_path / "journal")
    record = record_at_integration(
        store, repository, source=commit, source_tree=tree, commit=commit, tree=tree
    )
    paths = root_paths(tmp_path, store, repository)
    coding_root_effect.bootstrap_candidate(paths, commit=commit, tree=tree)
    request = build_request(record)

    calls: list[str] = []
    monkeypatch.setattr(
        coding_root_effect,
        "_apply_release_ownership_via_bootstrap",
        lambda _paths, candidate: calls.append(candidate) or {"status": "invoked"},
    )
    monkeypatch.setattr(
        coding_root_effect,
        "_runtime_canary",
        lambda _paths, _root_id: {"schema": "canary", "status": "PASS"},
    )

    # An ordinary unprivileged materialization emits the bounded promotion
    # request and then stays pending on the fixed root acknowledgement instead
    # of completing against a materializer-owned (Doctor-rejected) release.
    with pytest.raises(coding_root_effect.RestartPending):
        coding_root_effect._default_effects(paths, request)
    assert calls == [commit]
    assert coding_root_effect._selected_release_ownership(
        replace(
            paths,
            context_install_uid=os.geteuid(),
            context_install_gid=os.getegid(),
        ),
        commit,
    )["root_owned_immutable"] is True
