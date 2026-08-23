from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import tgw.context_update_coordinator as coordinator
from tgw.plan_solver import solve

H = "sha256:" + "a" * 64
C = "b" * 40
T = "c" * 40


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def _transaction_fixture(root: Path, transaction_id: str) -> coordinator.CoordinatorPaths:
    private = root / "transactions"
    private.mkdir(mode=0o700)
    transaction = private / transaction_id
    transaction.mkdir(mode=0o700)
    request = {
        "schema": "tgw-context-root-derived-update-request/v2",
        "transaction_id": transaction_id,
        "candidate": {"commit": C, "tree": T},
        "plan": {
            "approved_commit": C,
            "approved_solution": H,
            "evidence_commit": C,
            "evidence_tree": T,
        },
    }
    actor_request = {
        "transaction_id": transaction_id,
        "successor_generation": H,
    }
    prepared = {
        "schema": "tgw-context-update-prepared-evidence/v1",
        "transaction_id": transaction_id,
        "request": request,
        "request_sha256": coordinator._hash(request),
        "actor_request": actor_request,
    }
    effect_body = {
        "schema": "tgw-context-update-effect-plan/v1",
        "transaction_id": transaction_id,
        "effects": [
            {"sequence": 1, "action": "FINALIZE_TRANSACTION", "targets": []}
        ],
    }
    effect_plan = {
        **effect_body,
        "effect_plan_sha256": coordinator._hash(effect_body),
    }
    journal = {
        "schema": "tgw-context-update-private-journal/v1",
        "transaction_id": transaction_id,
        "request_sha256": coordinator._hash(request),
        "candidate": {
            "prepared_evidence_sha256": coordinator._hash(prepared),
        },
        "effect_plan": effect_plan,
    }
    binding = coordinator.coordinator_binding(
        actor_request=actor_request,
        journal_sha256=coordinator._hash(journal),
        ledger_opening={"record_sha256": H},
        effect_plan_sha256=effect_plan["effect_plan_sha256"],
    )
    progress = coordinator._progress(
        transaction_id=transaction_id,
        journal_sha256=coordinator._hash(journal),
        binding=binding,
    )
    _write_json(transaction / "prepared-evidence.json", prepared)
    _write_json(transaction / "private-journal.json", journal)
    _write_json(transaction / "coordinator-binding.json", binding)
    _write_json(transaction / "progress.json", progress)
    return coordinator.CoordinatorPaths(private_root=private)


def _snapshot(path: Path) -> dict[str, tuple[Any, ...]]:
    result: dict[str, tuple[Any, ...]] = {}
    for item in [path, *sorted(path.rglob("*"))]:
        state = item.lstat()
        relative = item.relative_to(path).as_posix() or "."
        result[relative] = (
            state.st_dev,
            state.st_ino,
            state.st_mode,
            state.st_uid,
            state.st_gid,
            state.st_nlink,
            state.st_size,
            state.st_mtime_ns,
            item.read_bytes() if item.is_file() else None,
        )
    return result


def test_read_transaction_status_is_pure_and_verified(tmp_path: Path) -> None:
    transaction_id = "sync-20260823"
    paths = _transaction_fixture(tmp_path, transaction_id)
    before = _snapshot(paths.private_root)

    result = coordinator.read_transaction_status(
        transaction_id,
        paths=paths,
        trusted_uid=os.getuid(),
        trusted_gid=os.getgid(),
    )

    assert result == {
        "schema": "tgw-context-update-status/v1",
        "transaction_id": transaction_id,
        "status": "PREPARED",
        "candidate_commit": C,
        "candidate_tree": T,
        "approved_plan": C,
        "evidence_plan": C,
        "actor_generation": H,
        "completed_effects": 0,
        "total_effects": 1,
        "binding_sha256": result["binding_sha256"],
        "hold": None,
    }
    assert before == _snapshot(paths.private_root)


def test_status_cli_never_constructs_mutating_coordinator(monkeypatch) -> None:
    expected = {
        "schema": "tgw-context-update-status/v1",
        "transaction_id": "sync-20260823",
        "status": "PREPARED",
        "candidate_commit": C,
        "candidate_tree": T,
        "approved_plan": C,
        "evidence_plan": C,
        "actor_generation": H,
        "completed_effects": 0,
        "total_effects": 17,
        "binding_sha256": H,
        "hold": None,
    }
    monkeypatch.setattr(
        coordinator, "read_transaction_status", lambda transaction_id: expected
    )

    def forbidden_constructor():
        raise AssertionError("status constructed the mutating coordinator")

    monkeypatch.setattr(
        coordinator, "RootContextUpdateCoordinator", forbidden_constructor
    )
    output = io.StringIO()
    assert (
        coordinator.main(
            ["status", "--transaction-id", "sync-20260823", "--json"],
            output_stream=output,
        )
        == 0
    )
    assert json.loads(output.getvalue()) == expected


def test_prepare_cli_builds_only_bounded_owner_direct_request(monkeypatch) -> None:
    observed: list[dict[str, Any]] = []

    class FakeCoordinator:
        def prepare(self, request):
            observed.append(request)
            return {"status": "PREPARED"}

    monkeypatch.setattr(
        coordinator, "RootContextUpdateCoordinator", FakeCoordinator
    )
    output = io.StringIO()
    assert (
        coordinator.main(
            [
                "prepare",
                "--transaction-id",
                "sync-20260823",
                "--source-label",
                "operator-conversation",
            ],
            input_stream=io.BytesIO(b"Synchronize the installed actor generation."),
            output_stream=output,
        )
        == 0
    )
    assert observed == [
        {
            "schema": "tgw-context-root-update-request/v2",
            "transaction_id": "sync-20260823",
            "authority": {
                "schema": "tgw-context-update-authority/v1",
                "mode": "OWNER_DIRECT",
                "instruction_utf8": "Synchronize the installed actor generation.",
                "source_label": "operator-conversation",
            },
        }
    ]
    assert json.loads(output.getvalue()) == {"status": "PREPARED"}


@pytest.mark.parametrize(
    ("command", "status", "exit_code"),
    [
        ("resume", "WAIT_EXTERNAL", 3),
        ("rollback", "ROLLED_BACK", 0),
    ],
)
def test_cli_dispatches_only_transaction_identity(
    monkeypatch, command: str, status: str, exit_code: int
) -> None:
    calls: list[tuple[str, str]] = []

    class FakeCoordinator:
        def resume(self, transaction_id):
            calls.append(("resume", transaction_id))
            return {"status": "WAIT_EXTERNAL"}

        def rollback(self, transaction_id):
            calls.append(("rollback", transaction_id))
            return {"status": "ROLLED_BACK"}

    monkeypatch.setattr(
        coordinator, "RootContextUpdateCoordinator", FakeCoordinator
    )
    output = io.StringIO()
    assert (
        coordinator.main(
            [command, "--transaction-id", "sync-20260823"],
            output_stream=output,
        )
        == exit_code
    )
    assert calls == [(command, "sync-20260823")]
    assert json.loads(output.getvalue()) == {"status": status}


def test_prepare_rejects_oversized_stdin_before_coordinator_construction(
    monkeypatch,
) -> None:
    def forbidden_constructor():
        raise AssertionError("invalid input constructed the mutating coordinator")

    monkeypatch.setattr(
        coordinator, "RootContextUpdateCoordinator", forbidden_constructor
    )
    errors = io.StringIO()
    assert (
        coordinator.main(
            [
                "prepare",
                "--transaction-id",
                "sync-20260823",
                "--source-label",
                "operator-console",
            ],
            input_stream=io.BytesIO(
                b"x" * (coordinator._MAX_OWNER_DIRECTIVE + 1)
            ),
            error_stream=errors,
        )
        == 2
    )
    assert json.loads(errors.getvalue())["status"] == "HOLD"


@pytest.mark.parametrize(
    "arguments",
    [
        ["apply"],
        ["resume", "--transaction-id", "sync", "--effect", "RESTART_PROVIDER"],
        ["prepare", "--transaction-id", "sync", "--request", "request.json"],
        ["status", "--transaction-id", "sync", "--path", "/etc/tgw"],
    ],
)
def test_cli_has_no_arbitrary_effect_or_path_surface(arguments) -> None:
    with pytest.raises(SystemExit) as raised:
        coordinator._cli_parser().parse_args(arguments)
    assert raised.value.code == 2


def _instruction_binding() -> dict[str, Any]:
    body = {
        "schema": "tgw-context-cold-instruction-binding/v1",
        "actor": "claude",
        "path": "/home/claude/.claude/CLAUDE.md",
        "sha256": "sha256:" + "1" * 64,
        "bootstrap_receipt_hash": "sha256:" + "2" * 64,
        "contract_receipt_hash": "sha256:" + "3" * 64,
    }
    return {**body, "binding_sha256": coordinator._hash(body)}


def _cold_fixture(*, include_review: bool = True) -> tuple[bytes, dict[str, Any]]:
    source_content = "current"
    source_hash = "sha256:" + hashlib.sha256(source_content.encode()).hexdigest()
    current_sources = {
        path: source_hash for path in coordinator._CURRENT_PLAN_SOURCES
    }
    request = {
        "transaction_id": "sync-20260823",
        "successor_generation": "sha256:" + "4" * 64,
        "revisions": {
            "plan": "a" * 40,
            "solution": "sha256:" + "5" * 64,
            "evidence_plan": "b" * 40,
            "evidence_tree": "c" * 40,
            "source": "d" * 40,
            "source_tree": "e" * 40,
            "catalog": "sha256:" + "6" * 64,
            "current_plan_sources": current_sources,
        },
    }
    status = {
        "plan": {
            "approved_commit": request["revisions"]["plan"],
            "approved_solution_hash": request["revisions"]["solution"],
            "evidence_head": request["revisions"]["evidence_plan"],
            "evidence_tree": request["revisions"]["evidence_tree"],
        },
        "source": {
            "commit": request["revisions"]["source"],
            "tree": request["revisions"]["source_tree"],
        },
        "environment": {"catalog_hash": request["revisions"]["catalog"]},
        "startup": {
            "actor": "claude",
            "generation": request["successor_generation"],
        },
        "fleet_convergence": {
            "transaction": {"transaction_id": request["transaction_id"]}
        },
        "generation_status": {"line": "TGW Context generation: CURRENT"},
    }
    calls: list[tuple[str, dict[str, Any], Any]] = [
        ("Skill", {"skill": "tgw-plan"}, "loaded tgw-plan"),
    ]
    if include_review:
        calls.append(("Skill", {"skill": "tgw-review"}, "loaded tgw-review"))
    calls.extend(
        [
            ("mcp__tgw-context__tgw_context_status", {}, status),
            (
                "mcp__tgw-context__tgw_context_bundle",
                {"receiver": "claude"},
                {
                    "receiver": "claude",
                    "status": {
                        "source": {"commit": request["revisions"]["source"]}
                    },
                },
            ),
            (
                "mcp__tgw-context__tgw_context_plan_graph",
                {"receiver": "claude", "operation": "resolve"},
                {"plan_commit": request["revisions"]["plan"]},
            ),
        ]
    )
    for path in coordinator._CURRENT_PLAN_SOURCES:
        calls.append(
            (
                "mcp__tgw-context__tgw_context_plan_source",
                {"path": path, "authority": "current-plan"},
                {
                    "authority": "current-plan",
                    "confined_path": path,
                    "commit": request["revisions"]["evidence_plan"],
                    "tree": request["revisions"]["evidence_tree"],
                    "blob_sha256": source_hash,
                    "content": source_content,
                    "content_sha256": source_hash,
                    "start_line": 1,
                    "end_line": 1,
                    "total_lines": 1,
                    "bytes": len(source_content.encode()),
                },
            )
        )
    events: list[dict[str, Any]] = []
    for index, (name, arguments, result) in enumerate(calls):
        identity = f"tool-{index}"
        events.extend(
            [
                {
                    "type": "tool_use",
                    "id": identity,
                    "name": name,
                    "input": arguments,
                },
                {
                    "type": "tool_result",
                    "tool_use_id": identity,
                    "content": (
                        json.dumps(result, sort_keys=True)
                        if isinstance(result, dict)
                        else result
                    ),
                },
            ]
        )
    raw = b"\n".join(
        json.dumps(event, sort_keys=True).encode() for event in events
    )
    return raw, request


def test_cold_proof_binds_both_skills_and_signed_instruction() -> None:
    raw, request = _cold_fixture()
    instruction = _instruction_binding()

    proof = coordinator.verify_cold_continuity_transcript(
        raw, request, instruction
    )

    assert proof["status"] == "PASS"
    assert proof["instruction_entry_point"] == instruction
    assert {"Skill", "tgw_context_status"} <= set(proof["tool_names"])


def test_cold_proof_rejects_missing_review_skill() -> None:
    raw, request = _cold_fixture(include_review=False)
    with pytest.raises(
        coordinator.ContextUpdateCoordinatorError,
        match="tgw-plan and tgw-review",
    ):
        coordinator.verify_cold_continuity_transcript(
            raw, request, _instruction_binding()
        )


def test_cold_proof_rejects_unsigned_instruction_drift() -> None:
    raw, request = _cold_fixture()
    instruction = _instruction_binding()
    instruction["sha256"] = "sha256:" + "9" * 64
    with pytest.raises(
        coordinator.ContextUpdateCoordinatorError,
        match="instruction binding differs",
    ):
        coordinator.verify_cold_continuity_transcript(raw, request, instruction)


@pytest.mark.parametrize("materialization", ["symlink", "copy"])
def test_instruction_readback_accepts_signed_symlink_or_regular_file(
    tmp_path: Path, materialization: str
) -> None:
    source = tmp_path / "AGENTS.md"
    destination = tmp_path / "CLAUDE.md"
    source_raw = b"# TGW agent entry point\n"
    source.write_bytes(source_raw)
    desired_sha256 = "sha256:" + hashlib.sha256(source_raw).hexdigest()
    effect: dict[str, Any] = {
        "materialization": materialization,
        "desired_sha256": desired_sha256,
    }
    if materialization == "symlink":
        destination.symlink_to(source)
    else:
        destination.write_bytes(source_raw)
        destination.chmod(0o640)
        state = destination.stat(follow_symlinks=False)
        effect.update(
            {
                "desired_uid": state.st_uid,
                "desired_gid": state.st_gid,
                "desired_mode": 0o640,
            }
        )

    assert coordinator._instruction_destination_is_exact(
        destination, source, source_raw, effect
    )

    destination.unlink()
    destination.write_bytes(b"drift")
    assert not coordinator._instruction_destination_is_exact(
        destination, source, source_raw, effect
    )


def test_resume_persists_inflight_intent_before_effect(monkeypatch) -> None:
    instance = object.__new__(coordinator.RootContextUpdateCoordinator)
    instance.event_hook = None
    instance.paths = coordinator.CoordinatorPaths()
    instance.trusted_uid = os.getuid()
    instance.trusted_gid = os.getgid()
    instance.now = lambda: datetime(2026, 8, 23, tzinfo=timezone.utc)
    progress = {
        "status": "PREPARED",
        "completed_effects": [],
        "inflight_sequence": None,
        "postimages": {},
        "hold": None,
    }
    journal = {
        "effect_plan": {
            "effects": [
                {
                    "sequence": 1,
                    "action": "INSTALL_PLATFORM_TRUST",
                    "targets": [],
                }
            ]
        }
    }
    events: list[str] = []
    instance._load_transaction = lambda transaction_id: (
        Path("/durable/transaction"),
        {},
        journal,
        {"binding_sha256": H},
        progress,
    )

    def write_progress(_path, value):
        events.append(f"write:{value['status']}:{value['inflight_sequence']}")
        return dict(value)

    def fail_effect(**_kwargs):
        events.append("effect")
        raise coordinator.ContextUpdateCoordinatorError("injected crash")

    instance._apply_effect = fail_effect
    monkeypatch.setattr(coordinator, "_write_progress", write_progress)
    monkeypatch.setattr(coordinator, "append_coordinator_event", lambda **_kwargs: {})

    with pytest.raises(
        coordinator.ContextUpdateCoordinatorError, match="injected crash"
    ):
        instance.resume("sync-20260823")

    assert events == [
        "write:RUNNING:1",
        "effect",
        "write:HOLD:1",
    ]


def _git(root: Path, *arguments: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
        },
    )
    if check and result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def _runner(command):
    return subprocess.run(
        list(command), check=False, capture_output=True, text=True,
        env={
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
        },
    )


def _plan_fixture(tmp_path: Path) -> tuple[
    coordinator.CoordinatorPaths, str, str, str, str, dict[str, Any]
]:
    repository = tmp_path / "plans"
    approved = tmp_path / "approved"
    config = tmp_path / "control.json"
    repository.mkdir()
    approved.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "test@example.invalid")
    _git(repository, "config", "user.name", "test")
    source = repository / "plan.md"
    source.write_text("historical\n", encoding="utf-8")
    _git(repository, "add", "plan.md")
    _git(repository, "commit", "-m", "historical")
    historical = _git(repository, "rev-parse", "HEAD")
    source.write_text("predecessor\n", encoding="utf-8")
    _git(repository, "commit", "-am", "predecessor")
    predecessor = _git(repository, "rev-parse", "HEAD")
    source.write_text("successor\n", encoding="utf-8")
    _git(repository, "commit", "-am", "successor")
    successor = _git(repository, "rev-parse", "HEAD")
    graph = {
        "schema": "tgw-plan/v2",
        "plan_commit": successor,
        "capabilities": ["fixture@1"],
        "providers": [{"id": "fixture", "provides": ["fixture@1"]}],
        "target": {
            "id": "fixture",
            "profile": "implementation",
            "minimum_state": "admitted",
            "required_capabilities": ["fixture@1"],
        },
    }
    native = solve(graph, expected_plan_commit=successor)
    solution = solve(
        graph,
        expected_plan_commit=successor,
        conformance_result={
            "available": True,
            "closure_hash": native["closure_hash"],
        },
    )
    solution_path = (
        repository
        / coordinator._plan_solution_path(successor)
    )
    solution_path.parent.mkdir(parents=True)
    solution_path.write_text(
        json.dumps(solution, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    _git(repository, "add", str(solution_path.relative_to(repository)))
    _git(repository, "commit", "-m", "successor solution evidence")
    evidence = _git(repository, "rev-parse", "HEAD")
    _git(
        repository,
        "worktree",
        "add",
        "--detach",
        str(approved / predecessor),
        predecessor,
    )
    _git(
        repository,
        "worktree",
        "add",
        "--detach",
        str(approved / successor),
        successor,
    )
    _git(repository, "update-ref", coordinator._APPROVED_PLAN_REF, historical)
    predecessor_solution = "sha256:" + "9" * 64
    _write_json(
        config,
        {
            "actor_fleet_provider": {"schema": "fixture", "preserve": True},
            "unrelated": {"keep": "exact"},
            "plan_approved_commit": predecessor,
            "plan_approved_solution_hash": predecessor_solution,
            "plan_repository_root": str(repository),
            "standalone_plan_root": str(approved / predecessor),
        },
    )
    paths = coordinator.CoordinatorPaths(
        plan_repository=repository,
        approved_plan_root=approved,
        provider_config=config,
        git=Path("/usr/bin/git"),
    )
    amendment = {
        "predecessor": {
            "approved_plan_commit": predecessor,
            "approved_solution_hash": predecessor_solution,
        },
        "successor": {
            "plan_commit": successor,
            "solution_hash": solution["solution_hash"],
            "cutover_receipt": "pending",
        },
    }
    return paths, historical, predecessor, successor, evidence, amendment


def test_plan_activation_derivation_targets_closed_successor_and_rejects_unknown_ref(
    tmp_path: Path, monkeypatch
) -> None:
    paths, historical, predecessor, successor, evidence, amendment = _plan_fixture(
        tmp_path
    )
    monkeypatch.setattr(
        coordinator, "_HISTORICAL_APPROVED_PLAN_REFS", frozenset({historical})
    )

    activation = coordinator._derive_plan_activation(
        paths=paths,
        runner=_runner,
        evidence_commit=evidence,
        amendment=amendment,
    )

    assert activation["predecessor"]["commit"] == predecessor
    assert activation["successor"]["commit"] == successor
    assert activation["successor"]["solution_hash"] == amendment["successor"][
        "solution_hash"
    ]
    assert activation["observed_ref_disposition"]["disposition"] == (
        "HISTORICAL_ONLY_NOT_ROLLBACK_AUTHORITY"
    )
    assert activation["successor"]["materialization"] == str(
        paths.approved_plan_root / successor
    )
    successor_materialization = paths.approved_plan_root / successor
    _git(
        paths.plan_repository,
        "worktree",
        "remove",
        "--force",
        str(successor_materialization),
    )
    assert not successor_materialization.exists()
    materialization = coordinator._prepare_plan_materialization(
        paths, activation, _runner
    )
    assert materialization == {
        "path": str(successor_materialization),
        "commit": successor,
        "tree": activation["successor"]["tree"],
        "pre_effect_disposition": "RETAIN_IMMUTABLE_UNSELECTED",
    }
    bad_solution = json.loads(json.dumps(amendment))
    bad_solution["successor"]["solution_hash"] = "sha256:" + "8" * 64
    with pytest.raises(
        coordinator.ContextUpdateCoordinatorError,
        match="solution hash differs",
    ):
        coordinator._derive_plan_activation(
            paths=paths,
            runner=_runner,
            evidence_commit=evidence,
            amendment=bad_solution,
        )
    with pytest.raises(
        coordinator.ContextUpdateCoordinatorError,
        match="solution observation failed",
    ):
        coordinator._derive_plan_activation(
            paths=paths,
            runner=_runner,
            evidence_commit=successor,
            amendment=amendment,
        )

    unrelated = _git(
        paths.plan_repository, "commit-tree", "HEAD^{tree}", "-m", "unrelated"
    )
    _git(
        paths.plan_repository,
        "update-ref",
        coordinator._APPROVED_PLAN_REF,
        unrelated,
    )
    with pytest.raises(
        coordinator.ContextUpdateCoordinatorError,
        match="neither predecessor nor classified history",
    ):
        coordinator._derive_plan_activation(
            paths=paths,
            runner=_runner,
            evidence_commit=evidence,
            amendment=amendment,
        )


def test_plan_ref_and_preserved_config_activate_together_and_rollback_to_predecessor(
    tmp_path: Path, monkeypatch
) -> None:
    paths, historical, predecessor, successor, evidence, amendment = _plan_fixture(
        tmp_path
    )
    monkeypatch.setattr(
        coordinator, "_HISTORICAL_APPROVED_PLAN_REFS", frozenset({historical})
    )
    activation = coordinator._derive_plan_activation(
        paths=paths,
        runner=_runner,
        evidence_commit=evidence,
        amendment=amendment,
    )
    original = json.loads(paths.provider_config.read_text(encoding="utf-8"))
    candidate = {
        **original,
        "plan_approved_commit": successor,
        "plan_approved_solution_hash": amendment["successor"]["solution_hash"],
        "standalone_plan_root": str(paths.approved_plan_root / successor),
    }
    candidate_path = tmp_path / "candidate-control.json"
    _write_json(candidate_path, candidate)
    projection = {
        "provider_config_path": str(candidate_path),
        "provider_config_sha256": coordinator._hash(candidate),
        "plan_activation_sha256": activation["activation_sha256"],
    }
    preimages = coordinator.snapshot_targets(
        [coordinator.SnapshotTarget("provider-config", paths.provider_config)]
    )
    prepared = {
        "plan_activation": activation,
        "trust_projection": projection,
    }
    journal = {"plan_activation": activation, "preimages": preimages}
    assert journal["plan_activation"]["observed_named_ref"] == historical
    assert journal["plan_activation"]["predecessor"]["commit"] == predecessor
    assert journal["plan_activation"]["observed_ref_disposition"] == {
        "observed_commit": historical,
        "disposition": "HISTORICAL_ONLY_NOT_ROLLBACK_AUTHORITY",
    }
    instance = object.__new__(coordinator.RootContextUpdateCoordinator)
    instance.paths = paths
    instance._runner = _runner
    instance.trusted_uid = os.getuid()
    instance.trusted_gid = os.getgid()
    instance.event_hook = None

    installer = instance._install_candidate_file

    def crash_after_ref(**_kwargs):
        raise coordinator.ContextUpdateCoordinatorError("injected after ref CAS")

    instance._install_candidate_file = crash_after_ref
    with pytest.raises(
        coordinator.ContextUpdateCoordinatorError,
        match="injected after ref CAS",
    ):
        instance._activate_plan_binding("sync-plan", prepared, journal)
    assert _git(
        paths.plan_repository, "rev-parse", coordinator._APPROVED_PLAN_REF
    ) == successor
    assert json.loads(paths.provider_config.read_text(encoding="utf-8")) == original

    instance._install_candidate_file = installer
    receipt = instance._activate_plan_binding("sync-plan", prepared, journal)

    assert receipt["approved_commit"] == successor
    assert _git(paths.plan_repository, "rev-parse", coordinator._APPROVED_PLAN_REF) == successor
    installed = json.loads(paths.provider_config.read_text(encoding="utf-8"))
    assert installed == candidate
    assert installed["unrelated"] == original["unrelated"]
    assert instance._activate_plan_binding(
        "sync-plan", prepared, journal
    )["receipt_sha256"] == receipt["receipt_sha256"]

    restored = instance._restore_approved_plan_ref("sync-plan", activation)
    assert restored["restored_commit"] == predecessor
    assert restored["observed_preimage_not_restored"] == historical
    assert _git(paths.plan_repository, "rev-parse", coordinator._APPROVED_PLAN_REF) == predecessor

    fixed_ids = {
        "actor-public-trust",
        "environment-public-trust",
        "admission-public-trust",
        "provider-config",
        "release-admission",
        "environment-catalog",
        "release-selector",
        "release-selection-receipt",
        "provider-unit",
        "provider-tmpfiles",
        "host-bootstrap-receipt",
        "stable-launcher",
        "stable-bin-parent",
        "status-executable",
        "status-sudoers",
        "relay-unit",
        "relay-python-interpreter",
        "relay-script",
        "provider-attestation-receipt",
        "transaction-scratch-root",
        "cold-continuity-workspace",
        "cold-continuity-transcript",
        "cold-continuity-receipt",
        "deepseek-service-action-receipt",
        "deepseek-service-progress",
        "deepseek-linger-token",
        "deepseek-linger",
        "coordinator-terminal-receipt",
    }
    effect_preimages = [
        {"target_id": identity, "kind": "absent"}
        for identity in sorted(fixed_ids)
    ]
    service_preimages = [
        {"target_id": identity}
        for identity in (
            "provider-service",
            "relay-service",
            "deepseek-user-service",
        )
    ]
    effects = coordinator._effect_plan(
        "sync-plan", effect_preimages, service_preimages
    )["effects"]
    install = next(row for row in effects if row["action"] == "INSTALL_PLATFORM_TRUST")
    targets = {row["target_id"]: row for row in install["targets"]}
    assert targets["approved-plan-ref"]["rollback_disposition"] == (
        "RESTORE_COHERENT_PREDECESSOR"
    )
    assert targets["provider-config"]["rollback_disposition"] == "RESTORE_PREIMAGE"
