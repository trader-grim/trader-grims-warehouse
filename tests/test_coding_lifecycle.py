import hashlib
import json
import os
import stat
import subprocess
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

import pytest

from tgw import coding_cli
from tgw.development import coding_lifecycle, coding_root_effect, local_workflow
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
    validate_job_binding_payload,
)
from tgw.development.coding_review import run_local_review, validate_review_artifact
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
from tgw.review_contract import ReviewRunnerError


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
    source_protection = {
        "trusted_uid": 0,
        "trusted_gid": 0,
        "root_identity": {"device": 1, "inode": 2},
        "held_through_use": True,
    }
    protected = {
        "schema": "tgw-local-governed-review-projection/v1",
        "provider_neutral": True,
        "privileged_authority": False,
        "candidate_commit": commit,
        "candidate_tree": tree,
        "plan_commit": record["binding"]["plan_commit"],
        "execution_hash": "sha256:" + "3" * 64,
        "role_receipt_hash": "sha256:" + "4" * 64,
        "candidate_receipt_hash": "sha256:" + "5" * 64,
        "governed_bundle_hash": "sha256:" + "6" * 64,
        "result_hash": "sha256:" + "7" * 64,
        "source_protection": source_protection,
        "source_protection_hash": digest(source_protection),
    }
    artifact = {
        "kind": "tgw_governed_review_projection",
        "diagnostic_verdict": "PASS_NON_ADMITTING",
        "root_id": record["root_id"],
        "binding_hash": record["binding"]["binding_hash"],
        "job_binding_hash": fence["job_binding_hash"],
        "job_id": job_id,
        "card_idempotency_key": fence["card_idempotency_key"],
        "candidate_binding_hash": candidate["candidate_binding_hash"],
        "task_spec_hash": digest(task),
        "protected_review": protected,
        "projection_hash": digest(protected),
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
    effects.mkdir(mode=0o3770)
    effects.chmod(0o3770)
    return RootEffectPaths(
        request_root=effects,
        lifecycle_root=store.root,
        repository=worktree,
        runtime_root=tmp_path / "runtime",
        coding_config=tmp_path / "coding.json",
        group_gid=os.getegid(),
        root_uid=os.geteuid(),
    )


def protected_review_evidence(request: dict) -> dict:
    unsigned = {
        "schema": "tgw-local-coding-protected-review-evidence/v1",
        "role": "independent-review",
        "candidate_commit": request["candidate_commit"],
        "candidate_tree": request["candidate_tree"],
        "plan_commit": request["plan_commit"],
        "governed_bundle_hash": "sha256:" + "6" * 64,
        "candidate_receipt_hash": "sha256:" + "5" * 64,
        "role_receipt_hash": "sha256:" + "4" * 64,
        "execution_hash": "sha256:" + "3" * 64,
    }
    return {**unsigned, "protected_review_hash": digest(unsigned)}


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
    assert new_fence != old_fence
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
                "plan_binding": record["binding"]["plan_todo_binding"],
            },
        },
    ]
    monkeypatch.setattr(coding_cli, "_jobs", lambda *_args, **_kwargs: rows)
    assert [
        row["job_id"]
        for row in coding_cli._bound_jobs(reopened, "codex-implement")
    ] == ["new"]


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
        lambda _args: {"binding": record["binding"]["plan_todo_binding"]},
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


def test_review_worker_projects_only_exact_provider_neutral_governed_pass(
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

    request_root = tmp_path / "protected-requests"
    request_root.mkdir()
    source_protection = {
        "trusted_uid": 0,
        "trusted_gid": 0,
        "root_identity": {"device": 1, "inode": 2},
        "held_through_use": True,
    }
    normalized_execution = {
        "source": {
            "commit": commit,
            "tree": tree,
            "snapshot_hash": "sha256:" + "8" * 64,
        },
        "plan_commit": payload["plan_binding"]["plan_commit"],
        "review": {"verdict": "PASS", "findings": []},
        "source_protection": source_protection,
        "execution_hash": "sha256:" + "3" * 64,
    }
    finalized = {
        "execution": {"opaque": "validated by governed adapter"},
        "governed_review_receipt": {
            "role": "independent-review",
            "status": "PASS",
            "established_conditions": ["reviewed"],
            "receipt_hash": "sha256:" + "4" * 64,
        },
        "governed_execution_bundle": {
            "source_commit": commit,
            "source_tree": tree,
            "plan_commit": payload["plan_binding"]["plan_commit"],
            "role": "independent-review",
            "candidate_receipt": {
                "ref": "candidate:receipt",
                "content_sha256": "sha256:" + "5" * 64,
            },
            "bundle_hash": "sha256:" + "6" * 64,
        },
        "result": {"overall": "PASS", "result_hash": "sha256:" + "7" * 64},
        "validation": {"status": "PASS"},
    }
    observed_requests = []

    result = run_local_review(
        payload,
        worktree,
        governed_runner=lambda path: observed_requests.append(path) or finalized,
        execution_validator=lambda _value: normalized_execution,
        config_loader=lambda *_args, **_kwargs: {
            "request_root": request_root,
        },
    )
    artifact = validate_review_artifact(
        result,
        payload=payload,
        worktree=worktree,
        expected_job_id="review-job",
    )
    assert observed_requests == [request_root / f"{commit}.request.json"]
    assert artifact["diagnostic_verdict"] == "PASS_NON_ADMITTING"
    assert artifact["protected_review"]["provider_neutral"] is True
    assert artifact["protected_review"]["privileged_authority"] is False
    assert artifact["protected_review"]["source_protection"][
        "held_through_use"
    ] is True
    failed = {**result, "outcome": "failed", "established_conditions": []}
    with pytest.raises(ReviewRunnerError, match="success conditions"):
        validate_review_artifact(
            failed,
            payload=payload,
            worktree=worktree,
            expected_job_id="review-job",
        )
    mismatched = {
        **finalized,
        "governed_execution_bundle": {
            **finalized["governed_execution_bundle"],
            "source_tree": "9" * 40,
        },
    }
    with pytest.raises(ReviewRunnerError, match="exact bindings"):
        run_local_review(
            payload,
            worktree,
            governed_runner=lambda _path: mismatched,
            execution_validator=lambda _value: normalized_execution,
            config_loader=lambda *_args, **_kwargs: {
                "request_root": request_root,
            },
        )
    unheld_execution = {
        **normalized_execution,
        "source_protection": {
            **source_protection,
            "held_through_use": False,
        },
    }
    with pytest.raises(ReviewRunnerError, match="exact bindings"):
        run_local_review(
            payload,
            worktree,
            governed_runner=lambda _path: finalized,
            execution_validator=lambda _value: unheld_execution,
            config_loader=lambda *_args, **_kwargs: {
                "request_root": request_root,
            },
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
    with pytest.raises(RootEffectError, match="protected review refused"):
        process_request(
            paths,
            request,
            store=store,
            effects=lambda *_args: pytest.fail(
                "untrusted request reached privileged effects"
            ),
            review_verifier=lambda *_args: (_ for _ in ()).throw(
                RootEffectError("protected review refused untrusted trigger")
            ),
        )

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

    from tgw import doctor_cli

    monkeypatch.setattr(
        doctor_cli,
        "repair_workers",
        lambda _paths, *, desired_commit: {
            "schema": "doctor-workers",
            "desired_commit": desired_commit,
            "status": "PASS",
        },
    )
    monkeypatch.setattr(
        coding_root_effect,
        "_runtime_canary",
        lambda _paths, _root_id: {
            "schema": "tgw-local-coding-disconnect-restart-canary/v1",
            "disposable": True,
            "canary_hash": "sha256:" + "8" * 64,
        },
    )
    protected = protected_review_evidence(request)
    first_effects = coding_root_effect._default_effects(
        paths, {**request, "_protected_review": protected}
    )
    assert first_effects["selection"]["state"] == "completed"

    def verifier(*_args):
        return protected

    recovered = process_request(
        paths, request, store=store, review_verifier=verifier
    )
    replay = process_request(
        paths,
        request,
        store=store,
        review_verifier=lambda *_args: pytest.fail(
            "trusted response replay must not rerun review"
        ),
    )
    assert recovered == replay
    assert recovered["status"] == "PASS"
    assert recovered["candidate_commit"] == commit
    assert recovered["governed_review_bundle_hash"] == "sha256:" + "6" * 64
    assert recovered["technical_result_hash"].startswith("sha256:")
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


def test_root_consumer_requires_exact_protected_governed_receipt_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", repository], check=True)
    subprocess.run(
        ["git", "config", "user.email", "protected@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Protected Review"],
        cwd=repository,
        check=True,
    )
    (repository / "source.py").write_text("RESULT = 1915\n")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "candidate"], cwd=repository, check=True
    )
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
    request = build_request(record)
    pins = tmp_path / "pins.json"
    sink = tmp_path / "sink.json"
    monkeypatch.setattr(
        coding_root_effect,
        "load_protected_review_config",
        lambda *_args, **_kwargs: {
            "candidate_evidence_descriptor_config": pins,
            "execution_evidence_sink_config": sink,
        },
    )
    from tgw import context_generation_status

    monkeypatch.setattr(
        context_generation_status, "_protected_directory", lambda *_args: None
    )
    monkeypatch.setattr(
        context_generation_status, "_protected_json", lambda *_args: {}
    )
    monkeypatch.setattr(
        coding_root_effect,
        "PinnedCandidateEvidenceDescriptor",
        lambda *_args, **_kwargs: type(
            "Descriptor",
            (),
            {
                "w06_plan_materialization_pin": {
                    "plan_source": {"commit": request["plan_commit"]}
                }
            },
        )(),
    )
    monkeypatch.setattr(
        coding_root_effect,
        "PinnedGitReceiptSink",
        lambda *_args, **_kwargs: object(),
    )

    def governed_bundle(*_args, **_kwargs):
        return {
            "receipt": {"receipt_hash": "sha256:" + "5" * 64},
            "role_receipt": {
                "receipt_hash": "sha256:" + "4" * 64,
                "artifacts": [
                    {
                        "kind": "governed_review_execution",
                        "execution_hash": "sha256:" + "3" * 64,
                    }
                ],
            },
            "card": {
                "role": "independent-review",
                "plan_commit": request["plan_commit"],
                "solution_id": request["solution_hash"],
                "bindings": {
                    "plan_graph": {"hash": request["solution_hash"]},
                    "authority_conditions": {"hash": request["closure_hash"]},
                },
            },
            "bundle_hash": "sha256:" + "6" * 64,
        }

    monkeypatch.setattr(
        coding_root_effect, "verify_governed_execution_bundle", governed_bundle
    )
    verified = coding_root_effect.verify_protected_review_evidence(
        paths, request, record
    )
    assert verified["role"] == "independent-review"
    assert verified["governed_bundle_hash"] == "sha256:" + "6" * 64

    def substituted_bundle(*_args, **_kwargs):
        value = governed_bundle()
        value["bundle_hash"] = "sha256:" + "9" * 64
        return value

    monkeypatch.setattr(
        coding_root_effect,
        "verify_governed_execution_bundle",
        substituted_bundle,
    )
    with pytest.raises(
        coding_root_effect.ProtectedReviewEvidenceError,
        match="differs from protected",
    ):
        coding_root_effect.verify_protected_review_evidence(
            paths, request, record
        )


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
    assert (
        implementation["coding_workflow"]["commit"]
        == request["candidate_commit"]
    )
    terminal = implementation["coding_lifecycle_result"]
    assert terminal["result_hash"] == request["result_hash"]
    assert terminal["operator_acceptance"] == "PENDING"


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
        "/opt/TGW/.venvs/controller/bin/tgw-local-independent-review-runner"
    ]
    parsed = local_workflow.parser().parse_args(
        ["worker", "--queue", "claude-review"]
    )
    assert parsed.operation == "worker"
    assert parsed.queue == "claude-review"
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
            assert "tgw_codex_review_bin" not in body
            assert "tgw_codex_review_auth" not in body
            assert "privatenetwork=true" not in body
        for forbidden in ("ssh ", "tgw-prod", "approval", "admission", "remote"):
            assert forbidden not in body


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
