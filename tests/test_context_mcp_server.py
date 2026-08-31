from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anyio
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mcp import ClientSession
from mcp.client.sse import sse_client

from tgw import context_mcp_server as context
from tgw.context_source_guard import ContextSourceGuardError, validate_context_source
from tgw.execution_resources import (
    CARD_RESOURCE_NAMES,
    card_resource_receipt,
    content_hash,
    issue_harness_retrieval_attestation,
)

ROOT = Path(__file__).resolve().parents[1]
CURRENT_PLAN_COLD_SOURCES = (
    "plan/execution/AMENDMENT-20260823-MCP-LIVE-CLIENT-CONVERGENCE.yaml",
    "pp/PP-ACTOR-MCP-BOUNDARY-001.md",
    "plan/execution/targets/W19-W21-MCP-ONLY-ACTOR-HARDENING-v1.yaml",
    "plan/execution/ACTIVE-PLAN-AMENDMENT-PROCESS-v1.yaml",
)


def _long_plan_source(title: str, total_lines: int) -> str:
    lines = [f"# {title}", "", "Approved baseline."]
    lines.extend(
        f"Bound evidence line {line_number}."
        for line_number in range(4, total_lines + 1)
    )
    return "\n".join(lines) + "\n"


def test_registration_runbook_binds_every_fail_closed_server_environment() -> None:
    runbook = Path(__file__).parents[1] / "docs/runbooks/tgw-context-mcp-v1-20260815.md"
    text = runbook.read_text(encoding="utf-8")
    for name in (
        "TGW_CONTEXT_PLAN_SOLUTION",
        "TGW_CONTEXT_ENVIRONMENT_CATALOG",
        "TGW_CONTEXT_ENVIRONMENT_CATALOG_HASH",
    ):
        assert text.count(name) >= 2
    assert "unbuilt" in text and "candidate catalog" in text


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(root: Path, message: str) -> str:
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Context Test", "-c", "user.email=context@example.invalid", "commit", "-qm", message)
    return _git(root, "rev-parse", "HEAD")


def _rewrite_startup_binding(bound_context, **updates: str) -> None:
    path = bound_context["startup_binding"]
    assert isinstance(path, Path)
    value = json.loads(path.read_text(encoding="utf-8"))
    value.update(updates)
    path.chmod(0o644)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    path.chmod(0o444)


def _store_fleet_convergence(bound_context, projection) -> None:
    path = bound_context["fleet_convergence"]
    assert isinstance(path, Path)
    projection.pop("projection_sha256", None)
    transaction = projection.get("transaction")
    if isinstance(transaction, dict):
        transaction.pop("projection_sha256", None)
        transaction["obligations_sha256"] = context._sha(
            context._canonical(transaction["obligations"])
        )
        transaction["projection_sha256"] = context._sha(
            context._canonical(transaction)
        )
    projection["projection_sha256"] = context._sha(context._canonical(projection))
    path.chmod(0o644)
    path.write_text(json.dumps(projection, sort_keys=True), encoding="utf-8")
    path.chmod(0o444)


def _rewrite_fleet_convergence(bound_context, **revision_updates: str) -> None:
    path = bound_context["fleet_convergence"]
    assert isinstance(path, Path)
    projection = json.loads(path.read_text(encoding="utf-8"))
    transaction = projection["transaction"]
    transaction["target_revisions"].update(revision_updates)
    _store_fleet_convergence(bound_context, projection)


@pytest.fixture
def bound_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path | str]:
    plan_repository = tmp_path / "plans"
    for path, content in {
        "plan/SPEC-plan-capability-graph-v2.md": "# Plan v2\n",
        "plan/TGW-Master-Plan.md": "# Master Plan\n",
        "plan/execution/GOVERNED-EXECUTION-PLATFORM-v1.yaml": (
            "work_units:\n"
            "  - id: W10\n"
            "    title: Prove the canonical gate\n"
            "  - id: W11\n"
            "    title: Cut over consumers\n"
            "    requires: [W10]\n"
        ),
        "plan/pp/PP-CONTEXT-001.md": "# PP\n",
        CURRENT_PLAN_COLD_SOURCES[0]: _long_plan_source(
            "Live-client convergence", 253
        ),
        CURRENT_PLAN_COLD_SOURCES[1]: _long_plan_source(
            "Actor MCP boundary", 261
        ),
        CURRENT_PLAN_COLD_SOURCES[2]: _long_plan_source(
            "W19-W21 hardening", 269
        ),
        CURRENT_PLAN_COLD_SOURCES[3]: _long_plan_source(
            "Active amendment process", 297
        ),
        "reference/context.md": "# Context\n",
        "reference/runbooks/actor-mcp-onboarding.md": "# Actor MCP onboarding\n\nCanonical current procedure.\n",
        "reference/runbooks/manual-platform-recovery.md": "# Manual recovery\n\nHuman fallback.\n",
    }.items():
        target = plan_repository / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    _git(plan_repository, "init", "-q")
    approved = _commit(plan_repository, "approved")
    materialization = tmp_path / "approved"
    _git(plan_repository, "worktree", "add", "--detach", str(materialization), approved)
    for path in CURRENT_PLAN_COLD_SOURCES:
        target = plan_repository / path
        target.write_text(target.read_text() + "Current evidence-head revision.\n")
    (plan_repository / "evidence.md").write_text("later evidence\n")
    evidence_head = _commit(plan_repository, "evidence")
    evidence_tree = _git(plan_repository, "rev-parse", "HEAD^{tree}")
    current_plan_sources = {
        path: context._sha((plan_repository / path).read_bytes())
        for path in CURRENT_PLAN_COLD_SOURCES
    }

    source = tmp_path / "source"
    (source / "src" / "fixture").mkdir(parents=True)
    (source / "src" / "fixture" / "provider.py").write_text("class ContextProvider:\n    pass\n")
    (source / "src" / "tgw").mkdir()
    shutil.copyfile(
        ROOT / "src/tgw/actor_startup.py",
        source / "src/tgw/actor_startup.py",
    )
    shutil.copyfile(
        ROOT / "src/tgw/context_mcp_server.py",
        source / "src/tgw/context_mcp_server.py",
    )
    (source / "docs" / "runbooks").mkdir(parents=True)
    (source / "docs" / "runbooks" / "context.md").write_text("# Context\n\nCommitted runbook.\n")
    (source / "agent-services" / "actor-bootstrap").mkdir(parents=True)
    (source / "agent-services" / "actor-bootstrap" / "bootstrap-policy-v1.json").write_text(
        json.dumps({"schema": "tgw-actor-bootstrap-policy/v1", "effects": "mcp-only"})
    )
    (source / "scripts").mkdir()
    shutil.copyfile(
        ROOT / "scripts/tgw_actor_startup.py",
        source / "scripts/tgw_actor_startup.py",
    )
    instruction_source = source / "AGENTS.md"
    instruction_source.write_text("# TGW agent entry point\n")
    _git(source, "init", "-q")
    source_commit = _commit(source, "source")
    source_tree = _git(source, "rev-parse", "HEAD^{tree}")
    instruction_source.chmod(0o444)

    git = Path(shutil.which("git") or "").resolve(strict=True)

    catalog = {
        "schema": "tgw-execution-environment-catalog/v3",
        "flake_lock": {"path": "flake.lock", "sha256": "1" * 64},
        "actors": {"codex": {"enabled": True, "permitted_profiles": ["development"]}},
        "profiles": {"development": {
            "state": "ready-for-preflight",
            "tools": [{
                "name": "git",
                "executable_path": str(git),
                "executable_sha256": context._sha(git.read_bytes()),
            }],
        }},
    }
    catalog_path = tmp_path / "execution-environment-catalog.json"
    catalog_path.write_text(json.dumps(catalog))
    catalog_hash = "sha256:" + hashlib.sha256(context._canonical(catalog)).hexdigest()

    home = tmp_path / "home"
    stable_launcher = home / ".local/bin/tgw-actor"
    stable_launcher.parent.mkdir(parents=True)
    stable_launcher.symlink_to(source / "scripts/tgw_actor_startup.py")
    instruction_entry_point = home / ".codex/AGENTS.md"
    instruction_entry_point.parent.mkdir(parents=True)
    instruction_entry_point.symlink_to(instruction_source)
    instruction_sha256 = context._sha(instruction_source.read_bytes())
    monkeypatch.setattr(
        context,
        "_INSTRUCTION_ENTRY_POINTS",
        {
            **context._INSTRUCTION_ENTRY_POINTS,
            "codex": str(instruction_entry_point),
        },
    )
    executable = Path(sys.executable).resolve(strict=True)
    executable_state = executable.stat(follow_symlinks=False)
    runtime_paths = {
        "TGW_CONTEXT_RUNTIME_ENTRYPOINT": source / "scripts/tgw_actor_startup.py",
        "TGW_CONTEXT_RUNTIME_MODULE": source / "src/tgw/actor_startup.py",
        "TGW_CONTEXT_RUNTIME_CONTEXT_MODULE": source / "src/tgw/context_mcp_server.py",
        "TGW_CONTEXT_STABLE_LAUNCHER": stable_launcher,
        "TGW_CONTEXT_RUNTIME_EXECUTABLE": executable,
    }
    generation = "sha256:" + "b" * 64
    solution = "sha256:" + "a" * 64
    convergence_path = tmp_path / "fleet-convergence.json"
    obligations = [{
        "obligation_id": "sha256:" + "1" * 64,
        "actor": "codex",
        "baseline_state": "LIVE",
        "checkpoint_disposition": None,
        "path_identity_hash": "sha256:" + "2" * 64,
        "parent_identity_hash": "sha256:" + "3" * 64,
        "baseline_child_identity_hashes": ["sha256:" + "4" * 64],
        "replacement_policy": "one-successor-per-observed-parent-v1",
        "disposition": "CONFIRMED",
        "pending_reasons": [],
        "client_confirmation_hash": "sha256:" + "5" * 64,
        "parent_transition_hash": None,
        "parent_transition_disposition": None,
    }]
    ledger_evidence = {
        "schema": "tgw-provider-ledger-evidence-link/v1",
        "sequence": 1,
        "record_sha256": "sha256:" + "7" * 64,
        "evidence_sha256": "sha256:" + "c" * 64,
        "review_receipt_sha256": "sha256:" + "3" * 64,
        "admission_receipt_sha256": "sha256:" + "2" * 64,
        "actor_verification_receipt_hashes": {
            "codex": "sha256:" + "b" * 64,
        },
        "client_confirmation_hashes": ["sha256:" + "5" * 64],
        "parent_transition_hashes": [],
        "cold_handoff_receipt_sha256": "sha256:" + "c" * 64,
        "managed_service_action_receipt_sha256": "sha256:" + "d" * 64,
        "terminal_convergence_receipt_sha256": "sha256:" + "e" * 64,
    }
    ledger_evidence["link_sha256"] = context._sha(
        context._canonical(ledger_evidence)
    )
    transaction = {
        "schema": "tgw-fleet-convergence-projection/v1",
        "status": "VERIFIED",
        "transaction_id": "context-fixture-transaction",
        "actors": ["codex"],
        "direction": "successor",
        "predecessor_generation": "sha256:" + "c" * 64,
        "successor_generation": generation,
        "target_generation": generation,
        "target_revisions": {
            "approved_plan": approved,
            "approved_solution": solution,
            "evidence_plan": evidence_head,
            "evidence_tree": evidence_tree,
            "source_commit": source_commit,
            "source_tree": source_tree,
            "current_plan_sources": current_plan_sources,
            "current_plan_sources_sha256": context._sha(
                context._canonical(current_plan_sources)
            ),
            "catalog": catalog_hash,
            "bootstrap": "sha256:" + "0" * 64,
            "broker_policy": "sha256:" + "1" * 64,
            "review": "sha256:" + "3" * 64,
            "admission": "sha256:" + "2" * 64,
        },
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "created_at": "2026-08-23T00:00:00+00:00",
        "updated_at": "2026-08-23T00:01:00+00:00",
        "journal_sha256": "sha256:" + "6" * 64,
        "journal_payload_sha256": "sha256:" + "f" * 64,
        "ledger_sequence": 1,
        "ledger_record_sha256": "sha256:" + "7" * 64,
        "ledger_evidence": ledger_evidence,
        "coordinator_binding_sha256": "sha256:" + "4" * 64,
        "confinement_state": "NON_CONFINING_ACTOR_COMPOSITE_STORES",
        "selected_release": {
            "path": "/opt/TGW/tgw-lib/releases/context-fixture",
            "generation": "context-fixture",
            "commit": source_commit,
            "tree": source_tree,
            "manifest_sha256": "sha256:" + "5" * 64,
        },
        "admission_evidence": {
            "review_receipt_sha256": "sha256:" + "3" * 64,
            "admission_receipt_sha256": "sha256:" + "2" * 64,
            "ledger_sequence": 1,
            "ledger_record_sha256": "sha256:" + "7" * 64,
        },
        "real_store_evidence_sha256": "sha256:" + "6" * 64,
        "cold_handoff_evidence_sha256": "sha256:" + "7" * 64,
        "cold_handoff_receipt_sha256": "sha256:" + "c" * 64,
        "managed_service_action_receipt_sha256": "sha256:" + "d" * 64,
        "terminal_convergence_receipt_sha256": "sha256:" + "e" * 64,
        "actor_verifications": [{
            "actor": "codex",
            "actor_proof_hash": "sha256:" + "8" * 64,
            "context_mcp_proof_hash": "sha256:" + "9" * 64,
            "verification_receipt_sha256": "sha256:" + "b" * 64,
            "primary_real_store_semantic_sha256": "sha256:" + "a" * 64,
            "instruction_entry_point_path": str(instruction_entry_point),
            "instruction_entry_point_sha256": instruction_sha256,
            "live_context_state": "CURRENT",
            "verified_at": "2026-08-23T00:01:00Z",
        }],
        "last_verified_at": "2026-08-23T00:01:00Z",
        "obligations": obligations,
        "obligations_sha256": context._sha(context._canonical(obligations)),
        "global_pending": [],
    }
    transaction["projection_sha256"] = context._sha(context._canonical(transaction))
    convergence = {
        "schema": "tgw-fleet-convergence-set/v1",
        "state": "TERMINAL",
        "generation_status": "CURRENT",
        "active_transaction_ids": [],
        "active_pointer_sha256": "sha256:" + "d" * 64,
        "supersessions_sha256": "sha256:" + "e" * 64,
        "transaction": transaction,
    }
    convergence["projection_sha256"] = context._sha(context._canonical(convergence))
    convergence_path.write_text(json.dumps(convergence, sort_keys=True))
    convergence_path.chmod(0o444)
    binding_path = tmp_path / "codex-startup.json"
    binding_path.write_text(json.dumps({
        "schema": "tgw-actor-startup-binding/v3",
        "actor": "codex",
        "trusted_public_key": "fixture-public-key",
        "expected_generation": generation,
        "expected_plan_commit": approved,
        "expected_solution_hash": solution,
        "expected_source_commit": source_commit,
        "expected_source_tree": source_tree,
        "context_source_root": str(source),
        "expected_catalog_hash": catalog_hash,
        "fleet_convergence_path": str(convergence_path),
        "stable_launcher_path": str(stable_launcher),
    }, sort_keys=True))
    binding_path.chmod(0o444)
    real_path_stat = Path.stat
    real_path_lstat = Path.lstat

    def root_owned_binding_stat(path, *args, **kwargs):
        observed = real_path_stat(path, *args, **kwargs)
        if path in {binding_path, convergence_path, instruction_source}:
            values = list(observed)
            values[4] = 0
            return os.stat_result(values)
        return observed

    monkeypatch.setattr(Path, "stat", root_owned_binding_stat)

    def root_owned_instruction_lstat(path, *args, **kwargs):
        observed = real_path_lstat(path, *args, **kwargs)
        if path == instruction_entry_point:
            values = list(observed)
            values[4] = 0
            return os.stat_result(values)
        return observed

    monkeypatch.setattr(Path, "lstat", root_owned_instruction_lstat)
    real_fstat = os.fstat

    def root_owned_projection_fstat(descriptor):
        observed = real_fstat(descriptor)
        try:
            target = Path(f"/proc/self/fd/{descriptor}").resolve(strict=True)
        except OSError:
            return observed
        if target == convergence_path:
            values = list(observed)
            values[4] = 0
            return os.stat_result(values)
        return observed

    monkeypatch.setattr(os, "fstat", root_owned_projection_fstat)

    environment = {
        "TGW_CONTEXT_STARTUP_BINDING": str(binding_path),
        "TGW_CONTEXT_ACTOR": "codex",
        "TGW_CONTEXT_GENERATION": generation,
        "TGW_CONTEXT_PLAN_ROOT": str(materialization),
        "TGW_CONTEXT_PLAN_REPOSITORY": str(plan_repository),
        "TGW_CONTEXT_PLAN_COMMIT": approved,
        "TGW_CONTEXT_PLAN_SOLUTION": solution,
        "TGW_CONTEXT_SOURCE_ROOT": str(source),
        "TGW_CONTEXT_SOURCE_COMMIT": source_commit,
        "TGW_CONTEXT_SOURCE_TREE": source_tree,
        "TGW_CONTEXT_RUNTIME_ROOT": str(tmp_path / "runtime"),
        "TGW_CONTEXT_ENVIRONMENT_CATALOG": str(catalog_path),
        "TGW_CONTEXT_ENVIRONMENT_CATALOG_HASH": catalog_hash,
        "TGW_CONTEXT_FLEET_CONVERGENCE": str(convergence_path),
        "TGW_CONTEXT_RUNTIME_EXECUTABLE_DEVICE": str(executable_state.st_dev),
        "TGW_CONTEXT_RUNTIME_EXECUTABLE_INODE": str(executable_state.st_ino),
    }
    for name, path in runtime_paths.items():
        environment[name] = str(path)
        environment[name + "_SHA256"] = context._sha(path.resolve().read_bytes())
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(context, "__file__", str(source / "src/tgw/context_mcp_server.py"))

    def validate_fixture_source(
        candidate, _git_path, *, expected_commit=None, expected_tree=None,
    ):
        candidate = Path(candidate).resolve(strict=True)
        observed_commit = _git(candidate, "rev-parse", "HEAD^{commit}")
        observed_tree = _git(candidate, "rev-parse", "HEAD^{tree}")
        status = _git(candidate, "status", "--porcelain=v1", "--untracked-files=all")
        if (
            candidate != source
            or status
            or observed_commit != expected_commit
            or observed_tree != expected_tree
        ):
            raise ContextSourceGuardError(
                "fixture Context source is stale, dirty, or ambiguously materialized"
            )
        return candidate, observed_commit, observed_tree

    monkeypatch.setattr(context, "validate_context_source", validate_fixture_source)
    context._code_snapshot.cache_clear()
    context._protected_context_source_once.cache_clear()
    try:
        yield {
            "approved": approved,
            "evidence_head": evidence_head,
            "evidence_tree": evidence_tree,
            "current_plan_sources": current_plan_sources,
            "source": source,
            "source_commit": source_commit,
            "source_tree": source_tree,
            "startup_binding": binding_path,
            "catalog": catalog_path,
            "fleet_convergence": convergence_path,
            "instruction_entry_point": instruction_entry_point,
            "instruction_source": instruction_source,
            "instruction_sha256": instruction_sha256,
        }
    finally:
        context._code_snapshot.cache_clear()
        context._protected_context_source_once.cache_clear()


def test_status_binds_approved_plan_evidence_and_committed_source(bound_context):
    status = context.context_status()
    assert status["plan"]["approved_commit"] == bound_context["approved"]
    assert status["plan"]["approved_solution_hash"] == "sha256:" + "a" * 64
    assert status["plan"]["evidence_head"] == bound_context["evidence_head"]
    assert status["source"]["commit"] == bound_context["source_commit"]
    assert status["code_graph"]["commit"] == bound_context["source_commit"]
    assert status["environment"]["catalog_hash"].startswith("sha256:")
    assert status["environment"]["catalog"]["profiles"]["development"]["state"] == "ready-for-preflight"
    convergence = status["fleet_convergence"]
    assert convergence["state"] == "TERMINAL"
    assert convergence["generation_status"] == "CURRENT"
    assert convergence["active_transaction_ids"] == []
    assert convergence["active_pointer_sha256"] == "sha256:" + "d" * 64
    assert convergence["supersessions_sha256"] == "sha256:" + "e" * 64
    transaction = convergence["transaction"]
    assert transaction["status"] == "VERIFIED"
    assert transaction["actors"] == ["codex"]
    assert transaction["target_generation"] == status["startup"]["generation"]
    assert transaction["target_revisions"] == {
        "approved_plan": status["plan"]["approved_commit"],
        "approved_solution": status["plan"]["approved_solution_hash"],
        "evidence_plan": bound_context["evidence_head"],
        "evidence_tree": bound_context["evidence_tree"],
        "source_commit": status["source"]["commit"],
        "source_tree": status["source"]["tree"],
        "current_plan_sources": bound_context["current_plan_sources"],
        "current_plan_sources_sha256": context._sha(
            context._canonical(bound_context["current_plan_sources"])
        ),
        "catalog": status["environment"]["catalog_hash"],
        "bootstrap": "sha256:" + "0" * 64,
        "broker_policy": "sha256:" + "1" * 64,
        "review": "sha256:" + "3" * 64,
        "admission": "sha256:" + "2" * 64,
    }
    assert transaction["ledger_sequence"] == 1
    assert transaction["last_verified_at"] == "2026-08-23T00:01:00Z"
    assert transaction["actor_verifications"][0]["actor"] == "codex"
    assert transaction["actor_verifications"][0]["live_context_state"] == "CURRENT"
    assert transaction["actor_verifications"][0][
        "instruction_entry_point_path"
    ] == str(bound_context["instruction_entry_point"])
    assert transaction["actor_verifications"][0][
        "instruction_entry_point_sha256"
    ] == bound_context["instruction_sha256"]
    assert transaction["obligations"][0]["disposition"] == "CONFIRMED"
    assert transaction["obligations"][0]["checkpoint_disposition"] is None
    unsigned_transaction = dict(transaction)
    transaction_hash = unsigned_transaction.pop("projection_sha256")
    assert transaction_hash == context._sha(context._canonical(unsigned_transaction))
    unsigned_convergence = dict(convergence)
    convergence_hash = unsigned_convergence.pop("projection_sha256")
    assert convergence_hash == context._sha(context._canonical(unsigned_convergence))
    assert status["generation_status"]["state"] == "CURRENT"
    assert status["generation_status"]["client_state"] == "CURRENT"
    assert status["generation_status"]["fleet_state"] == "CURRENT"
    assert status["startup"]["instruction_entry_point_path"] == str(
        bound_context["instruction_entry_point"]
    )
    assert status["startup"]["instruction_entry_point_sha256"] == bound_context[
        "instruction_sha256"
    ]
    assert status["startup"]["instruction_entry_point_state"] == "CURRENT"
    assert (
        f"instructions={str(bound_context['instruction_sha256']).removeprefix('sha256:')[:12]}"
        in status["generation_status"]["line"]
    )
    assert status["generation_status"]["line"].startswith(
        "TGW Context generation: client=CURRENT fleet=CURRENT aggregate=CURRENT "
    )
    assert status["scope_semantics"]["platform_w11_completion_implies_master_plan_completion"] is False


def test_active_restart_required_projection_cannot_look_terminal(bound_context):
    path = bound_context["fleet_convergence"]
    assert isinstance(path, Path)
    projection = json.loads(path.read_text(encoding="utf-8"))
    transaction = projection["transaction"]
    transaction["status"] = "RESTART_REQUIRED"
    transaction["actor_verifications"] = []
    transaction["last_verified_at"] = None
    transaction["obligations"][0]["disposition"] = "PENDING"
    transaction["obligations"][0]["pending_reasons"] = [
        "ORDINARY_HARNESS_CONFIRMATION_PENDING",
    ]
    projection["state"] = "ACTIVE"
    projection["generation_status"] = "RESTART_REQUIRED"
    projection["active_transaction_ids"] = [transaction["transaction_id"]]
    _store_fleet_convergence(bound_context, projection)

    observed = context.context_status()["fleet_convergence"]
    assert observed["state"] == "ACTIVE"
    assert observed["generation_status"] == "RESTART_REQUIRED"
    assert observed["active_transaction_ids"] == [transaction["transaction_id"]]
    assert observed["transaction"]["status"] == "RESTART_REQUIRED"
    assert observed["transaction"]["status"] not in {"VERIFIED", "ROLLED_BACK"}
    assert observed["transaction"]["obligations"][0]["pending_reasons"] == [
        "ORDINARY_HARNESS_CONFIRMATION_PENDING",
    ]


def test_ambiguous_fleet_projection_is_never_positive_context(bound_context):
    ambiguous = {
        "schema": "tgw-fleet-convergence-set/v1",
        "state": "AMBIGUOUS",
        "generation_status": "HOLD",
        "active_transaction_ids": ["transaction-a", "transaction-b"],
    }
    _store_fleet_convergence(bound_context, ambiguous)

    with pytest.raises(context.ContextError, match="ambiguous or absent"):
        context.context_status()


def test_fleet_projection_requires_root_protection(bound_context, monkeypatch):
    projection_path = bound_context["fleet_convergence"]
    assert isinstance(projection_path, Path)
    fixture_fstat = os.fstat

    def actor_owned_projection(descriptor):
        observed = fixture_fstat(descriptor)
        try:
            target = Path(f"/proc/self/fd/{descriptor}").resolve(strict=True)
        except OSError:
            return observed
        if target == projection_path:
            values = list(observed)
            values[4] = 1000
            return os.stat_result(values)
        return observed

    monkeypatch.setattr(os, "fstat", actor_owned_projection)
    with pytest.raises(context.ContextError, match="not root protected"):
        context.context_status()


def test_fleet_projection_refuses_hash_mismatch(bound_context):
    path = bound_context["fleet_convergence"]
    assert isinstance(path, Path)
    projection = json.loads(path.read_text(encoding="utf-8"))
    projection["generation_status"] = "HOLD"
    path.chmod(0o644)
    path.write_text(json.dumps(projection, sort_keys=True), encoding="utf-8")
    path.chmod(0o444)

    with pytest.raises(context.ContextError, match="ambiguous or absent"):
        context.context_status()


def test_fleet_projection_refuses_target_generation_mismatch(bound_context):
    path = bound_context["fleet_convergence"]
    assert isinstance(path, Path)
    projection = json.loads(path.read_text(encoding="utf-8"))
    projection["transaction"]["target_generation"] = "sha256:" + "9" * 64
    _store_fleet_convergence(bound_context, projection)

    with pytest.raises(context.ContextError, match="transaction binding differs"):
        context.context_status()


@pytest.mark.parametrize("invalid_field", ["path", "hash"])
def test_context_status_rejects_invalid_instruction_projection(
    bound_context, invalid_field,
):
    path = bound_context["fleet_convergence"]
    assert isinstance(path, Path)
    projection = json.loads(path.read_text(encoding="utf-8"))
    verification = projection["transaction"]["actor_verifications"][0]
    if invalid_field == "path":
        verification["instruction_entry_point_path"] = (
            "/home/deepseek/.dsh/AGENTS.md"
        )
        expected_error = "actor verification is invalid"
    else:
        verification["instruction_entry_point_sha256"] = "sha256:" + "f" * 64
        expected_error = "differs from fleet proof"
    _store_fleet_convergence(bound_context, projection)

    with pytest.raises(context.ContextError, match=expected_error):
        context.context_status()


def test_context_status_rejects_instruction_content_drift(bound_context):
    instruction_source = bound_context["instruction_source"]
    assert isinstance(instruction_source, Path)
    instruction_source.chmod(0o644)
    instruction_source.write_text("# drifted instructions\n")
    instruction_source.chmod(0o444)

    with pytest.raises(context.ContextError, match="differs from fleet proof"):
        context.context_status()


def test_context_plan_graph_indexes_approved_execution_work_unit(bound_context):
    result = context.plan_graph("W11")
    assert result["status"] == "matched"
    assert result["plan_commit"] == bound_context["approved"]
    candidate = result["candidates"][0]
    assert candidate["node_id"] == "work-unit:W11"
    assert candidate["citation"]["path"] == "plan/execution/GOVERNED-EXECUTION-PLATFORM-v1.yaml"


def test_current_plan_authority_reads_every_cold_convergence_source(bound_context):
    status = context.context_status()
    for path in CURRENT_PLAN_COLD_SOURCES:
        for authority, commit, tree in (
            (
                "current-plan",
                bound_context["evidence_head"],
                status["plan"]["evidence_tree"],
            ),
            (
                "approved-plan",
                bound_context["approved"],
                status["plan"]["approved_tree"],
            ),
        ):
            pages = []
            start_line = 1
            source_sha256 = None
            total_lines = None
            while total_lines is None or start_line <= total_lines:
                page = context.source_chunk(
                    path,
                    authority=authority,
                    start_line=start_line,
                    max_lines=250,
                )
                pages.append(page["content"])
                assert page["authority"] == authority
                assert page["commit"] == commit
                assert page["tree"] == tree
                assert page["confined_path"] == path
                assert page["blob_sha256"] == page["sha256"]
                assert page["content_sha256"] == context._sha(
                    page["content"].encode()
                )
                assert page["start_line"] == start_line
                assert page["end_line"] == min(
                    start_line + 249, page["total_lines"]
                )
                source_sha256 = source_sha256 or page["sha256"]
                total_lines = total_lines or page["total_lines"]
                assert page["sha256"] == source_sha256
                assert page["total_lines"] == total_lines
                start_line = page["end_line"] + 1
            assert len(pages) == 2
            assert context._sha(("\n".join(pages) + "\n").encode()) == (
                source_sha256
            )
            if authority == "current-plan":
                assert source_sha256 == bound_context["current_plan_sources"][path]
                assert "Current evidence-head revision." in pages[-1]
            else:
                assert "Current evidence-head revision." not in pages[-1]


def test_dirty_application_source_holds_committed_runbook_and_codegraph_reads(
    bound_context,
):
    source = bound_context["source"]
    assert isinstance(source, Path)
    assert context.code_graph("symbols", "ContextProvider")["result"][0]["name"] == "ContextProvider"
    assert "Committed runbook" in context.runbooks(
        path="docs/runbooks/context.md",
    )["content"]
    (source / "src" / "fixture" / "provider.py").write_text("broken python !!!\n")
    (source / "docs" / "runbooks" / "context.md").write_text("dirty bytes\n")
    with pytest.raises(context.ContextError, match="stale|dirty|protected"):
        context.code_graph("symbols", "ContextProvider")
    with pytest.raises(context.ContextError, match="stale|dirty|protected"):
        context.runbooks(path="docs/runbooks/context.md")


def test_canonical_plan_runbooks_and_onboarding_are_first_class_context(bound_context):
    current = context.runbooks(path="reference/runbooks/actor-mcp-onboarding.md")
    assert current["authority"] == "canonical-plan-runbook"
    assert current["commit"] == context.context_status()["plan"]["evidence_head"]
    assert "Canonical current procedure" in current["content"]
    indexed = context.runbooks(query="onboarding")
    assert {item["authority"] for item in indexed["matches"]} >= {"canonical-plan-runbook"}
    bundle = context.onboarding_bundle("codex")
    assert bundle["status"] == "SEED_REQUIRED"
    assert bundle["fallback"] == "FORBIDDEN"
    assert bundle["plan"]["evidence_descends_from_approved"] is True
    assert bundle["runbooks"]["onboarding"]["authority"] == "canonical-plan-runbook"
    registration = bundle["context_mcp_registration"]
    assert registration["command"] == "/opt/TGW/tgw-lib/bin/tgw-actor"
    assert registration["args"] == ["--context-mcp"]
    assert registration["environment"] == bundle["required_context_environment"]
    assert registration["environment"] == {
        "TGW_ACTOR_CONTEXT_ENDPOINT": "tgw-context",
        "TGW_ACTOR_CONTEXT_REGISTRATION": "stable-launcher-v1",
    }


def test_onboarding_rejects_unknown_actor_and_unbound_catalog_profile(
    bound_context, monkeypatch,
):
    with pytest.raises(context.ContextError, match="not enabled"):
        context.onboarding_bundle("unknown")
    catalog_path = Path(os.environ["TGW_CONTEXT_ENVIRONMENT_CATALOG"])
    catalog = json.loads(catalog_path.read_text())
    catalog["actors"]["codex"]["permitted_profiles"] = ["absent"]
    catalog_path.write_text(json.dumps(catalog))
    catalog_hash = "sha256:" + hashlib.sha256(context._canonical(catalog)).hexdigest()
    monkeypatch.setenv(
        "TGW_CONTEXT_ENVIRONMENT_CATALOG_HASH",
        catalog_hash,
    )
    _rewrite_startup_binding(bound_context, expected_catalog_hash=catalog_hash)
    _rewrite_fleet_convergence(bound_context, catalog=catalog_hash)
    with pytest.raises(context.ContextError, match="catalog-pinned Git is unavailable"):
        context.onboarding_bundle("codex")


def test_onboarding_rejects_missing_committed_bootstrap_input(
    bound_context, monkeypatch,
):
    source = bound_context["source"]
    assert isinstance(source, Path)
    _git(source, "rm", "agent-services/actor-bootstrap/bootstrap-policy-v1.json")
    source_commit = _commit(source, "remove required bootstrap policy")
    source_tree = _git(source, "rev-parse", "HEAD^{tree}")
    monkeypatch.setenv("TGW_CONTEXT_SOURCE_COMMIT", source_commit)
    monkeypatch.setenv("TGW_CONTEXT_SOURCE_TREE", source_tree)
    _rewrite_startup_binding(
        bound_context,
        expected_source_commit=source_commit,
        expected_source_tree=source_tree,
    )
    _rewrite_fleet_convergence(
        bound_context,
        source_commit=source_commit,
        source_tree=source_tree,
    )
    context._protected_context_source_once.cache_clear()
    with pytest.raises(context.ContextError, match="does not exist"):
        context.onboarding_bundle("codex")


def test_runbook_authority_mismatch_and_dirty_plan_fail_closed(bound_context):
    with pytest.raises(context.ContextError, match="outside"):
        context.runbooks(
            path="docs/runbooks/context.md",
            authority="current-plan",
        )
    repository = Path(os.environ["TGW_CONTEXT_PLAN_REPOSITORY"])
    (repository / "untracked.md").write_text("drift\n")
    with pytest.raises(context.ContextError, match="not clean"):
        context.context_status()


def test_fail_closed_path_and_materialization_checks_and_bounded_surface(bound_context, monkeypatch):
    with pytest.raises(context.ContextError, match="outside"):
        context.source_chunk("docs/runbooks/context.md")
    monkeypatch.setenv("TGW_CONTEXT_PLAN_COMMIT", str(bound_context["evidence_head"]))
    with pytest.raises(context.ContextError, match="stale after fleet cutover"):
        context.context_status()
    tools = set(context.mcp._tool_manager._tools)
    assert tools == {
        "tgw_context_status", "tgw_context_confirm_rebind", "tgw_context_bundle",
        "tgw_context_plan_graph", "tgw_context_plan_source",
        "tgw_context_runbooks", "tgw_context_code_graph", "tgw_context_onboarding",
        "tgw_context_todo_exact", "tgw_context_todo_current",
        "tgw_context_todo_dependencies", "tgw_context_todo_inventory",
    }
    payload = json.loads(context.tgw_context_status())
    assert payload["ok"] is False


def test_every_exported_tool_revalidates_fail_closed_bindings(monkeypatch):
    ordinary = {
        "tgw_context_status": lambda: context.tgw_context_status(),
        "tgw_context_confirm_rebind": lambda: context.tgw_context_confirm_rebind(
            "fixture-transaction",
            "successor",
            "sha256:" + "1" * 64,
        ),
        "tgw_context_bundle": lambda: context.tgw_context_bundle("fixture task"),
        "tgw_context_plan_graph": lambda: context.tgw_context_plan_graph("W11"),
        "tgw_context_plan_source": lambda: context.tgw_context_plan_source(
            "plan/TGW-Master-Plan.md",
        ),
        "tgw_context_runbooks": lambda: context.tgw_context_runbooks(),
        "tgw_context_onboarding": lambda: context.tgw_context_onboarding("codex"),
        "tgw_context_code_graph": lambda: context.tgw_context_code_graph(),
        "tgw_context_todo_exact": lambda: context.tgw_context_todo_exact(
            1936, "doctor",
        ),
        "tgw_context_todo_current": lambda: context.tgw_context_todo_current(
            "doctor",
        ),
        "tgw_context_todo_dependencies": lambda: context.tgw_context_todo_dependencies(
            1936, "doctor", [1935],
        ),
        "tgw_context_todo_inventory": lambda: context.tgw_context_todo_inventory(
            "planning-inventory",
        ),
    }
    assert set(context.mcp._tool_manager._tools) == set(ordinary)
    assert set(context.governed_review_mcp._tool_manager._tools) == {
        "tgw_context_bundle",
    }
    calls = 0

    def refuse_stale_process():
        nonlocal calls
        calls += 1
        raise context.ContextError("per-call fixture rejected stale process")

    monkeypatch.setattr(context, "_per_call_guard", refuse_stale_process)
    for invoke in ordinary.values():
        payload = json.loads(invoke())
        assert payload == {
            "ok": False,
            "error": "per-call fixture rejected stale process",
            "error_type": "ContextError",
        }
    governed = json.loads(context.governed_review_context_bundle("fixture task"))
    assert governed == {
        "ok": False,
        "error": "per-call fixture rejected stale process",
        "error_type": "ContextError",
    }
    assert calls == len(ordinary) + 1


def test_runtime_identity_hash_drift_holds_before_context_read(
    bound_context, monkeypatch,
):
    monkeypatch.setenv("TGW_CONTEXT_RUNTIME_MODULE_SHA256", "sha256:" + "0" * 64)
    with pytest.raises(context.ContextError, match="runtime hash differs"):
        context.context_status()


def test_production_source_guard_refuses_nonretained_fixture(bound_context):
    source = bound_context["source"]
    assert isinstance(source, Path)
    git = Path(shutil.which("git") or "").resolve(strict=True)
    with pytest.raises(
        ContextSourceGuardError,
        match="below the canonical retained-source root",
    ):
        validate_context_source(
            source,
            git,
            expected_commit=str(bound_context["source_commit"]),
            expected_tree=str(bound_context["source_tree"]),
        )


def test_long_lived_actor_context_process_holds_when_root_binding_changes(tmp_path, monkeypatch):
    actor = "codex"
    generation = "sha256:" + "1" * 64
    plan = "f" * 40
    solution = "sha256:" + "2" * 64
    source_commit = "e" * 40
    source_tree = "d" * 40
    catalog = "sha256:" + "3" * 64
    source_root = str(tmp_path / "source")
    convergence_path = str(tmp_path / "fleet-convergence.json")
    binding_path = tmp_path / "codex-startup.json"
    stable_launcher = "/home/codex/.local/bin/tgw-actor"
    binding = {
        "schema": "tgw-actor-startup-binding/v3",
        "actor": actor,
        "trusted_public_key": "fixture",
        "expected_generation": generation,
        "expected_plan_commit": plan,
        "expected_solution_hash": solution,
        "expected_source_commit": source_commit,
        "expected_source_tree": source_tree,
        "context_source_root": source_root,
        "expected_catalog_hash": catalog,
        "fleet_convergence_path": convergence_path,
        "stable_launcher_path": stable_launcher,
    }
    binding_path.write_text(json.dumps(binding), encoding="utf-8")
    binding_path.chmod(0o444)
    real_stat = Path.stat

    def root_owned_stat(path, *args, **kwargs):
        observed = real_stat(path, *args, **kwargs)
        if path == binding_path:
            values = list(observed)
            values[4] = 0
            return os.stat_result(values)
        return observed

    monkeypatch.setattr(Path, "stat", root_owned_stat)
    for name, value in {
        "TGW_CONTEXT_STARTUP_BINDING": str(binding_path),
        "TGW_CONTEXT_ACTOR": actor,
        "TGW_CONTEXT_GENERATION": generation,
        "TGW_CONTEXT_PLAN_COMMIT": plan,
        "TGW_CONTEXT_PLAN_SOLUTION": solution,
        "TGW_CONTEXT_SOURCE_COMMIT": source_commit,
        "TGW_CONTEXT_SOURCE_TREE": source_tree,
        "TGW_CONTEXT_SOURCE_ROOT": source_root,
        "TGW_CONTEXT_ENVIRONMENT_CATALOG_HASH": catalog,
            "TGW_CONTEXT_FLEET_CONVERGENCE": convergence_path,
            "TGW_CONTEXT_STABLE_LAUNCHER": stable_launcher,
    }.items():
        monkeypatch.setenv(name, value)

    assert context._current_actor_startup_binding()["expected_generation"] == generation

    binding_path.chmod(0o644)
    binding["expected_generation"] = "sha256:" + "9" * 64
    binding_path.write_text(json.dumps(binding), encoding="utf-8")
    binding_path.chmod(0o444)
    with pytest.raises(context.ContextError, match="stale after fleet cutover"):
        context._current_actor_startup_binding()


def test_status_rejects_a_stale_environment_catalog_binding(bound_context, monkeypatch):
    monkeypatch.setenv("TGW_CONTEXT_ENVIRONMENT_CATALOG_HASH", "sha256:" + "0" * 64)
    _rewrite_startup_binding(
        bound_context, expected_catalog_hash="sha256:" + "0" * 64,
    )
    _rewrite_fleet_convergence(bound_context, catalog="sha256:" + "0" * 64)
    with pytest.raises(context.ContextError, match="catalog hash"):
        context.context_status()


def test_status_rejects_plan_evidence_head_that_does_not_descend_from_approval(bound_context):
    repository = Path(os.environ["TGW_CONTEXT_PLAN_REPOSITORY"])
    approved = str(bound_context["approved"])
    _git(repository, "checkout", "--orphan", "divergent-evidence")
    for item in repository.iterdir():
        if item.name != ".git":
            if item.is_dir():
                import shutil

                shutil.rmtree(item)
            else:
                item.unlink()
    (repository / "unrelated.md").write_text("unrelated\n")
    _commit(repository, "divergent")
    with pytest.raises(context.ContextError, match="does not descend"):
        context.context_status()
    _git(repository, "checkout", "-q", approved)


def test_governed_review_context_run_fetches_every_bound_resource(
    bound_context, monkeypatch,
):
    contents = {
        name: ("a" * 40 if name == "plan_commit" else f"resource:{name}").encode()
        for name in CARD_RESOURCE_NAMES
    }
    bindings = {
        name: {"ref": f"resource:{name}", "hash": content_hash(contents[name])}
        for name in CARD_RESOURCE_NAMES
    }
    unsigned = {
        "schema": "tgw-execution-card/v1", "role": "independent-review",
        "plan_commit": "a" * 40, "bindings": bindings,
    }
    card = {
        **unsigned,
        "card_hash": "sha256:" + hashlib.sha256(context._canonical(unsigned)).hexdigest(),
    }
    receipt = card_resource_receipt(card)
    observed = {}

    signing_key = Ed25519PrivateKey.generate()
    public_key = base64.b64encode(signing_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw,
    )).decode()

    class FakeBroker:
        def __init__(self, endpoint):
            observed["endpoint"] = endpoint

        def execute(self, request):
            observed["request"] = request
            attestation = issue_harness_retrieval_attestation(
                {
                    "schema": "tgw-registered-resource-retrieval-attestation/v3",
                    "service_id": "review-resources", "client_id": "review-client",
                    "run_id": "run-bound-context", "card_hash": request["card_hash"],
                    "role": request["role"],
                    "execution_identity": request["execution_identity"],
                    "handoff_hash": request["handoff_hash"],
                    "resource_receipt_hash": request["resource_receipt_hash"],
                    "resources": request["resources"], "attestation_key_id": "test-key",
                }, signing_private_key=signing_key,
            )
            unsigned_bundle = {
                "schema": "tgw-context-review-resource-bundle/v1",
                "client_id": "review-client", "challenge": request["challenge"],
                "skill_contract_hash": request["skill_contract_hash"],
                "retrieval_attestation": attestation,
                "resources": {
                    name: {
                        **bindings[name],
                        "content_sha256": content_hash(contents[name]),
                        "content_base64": base64.b64encode(contents[name]).decode(),
                    }
                    for name in sorted(bindings)
                },
            }
            return {
                **unsigned_bundle,
                "bundle_hash": context._sha(context._canonical(unsigned_bundle)),
            }

    monkeypatch.setenv("TGW_CONTEXT_REVIEW_BROKER_ENDPOINT", "https://broker.invalid")
    monkeypatch.setenv("TGW_CONTEXT_RESOURCE_SERVICE_ID", "review-resources")
    monkeypatch.setenv("TGW_CONTEXT_RESOURCE_SERVICE_CLIENT_ID", "review-client")
    monkeypatch.setenv("TGW_CONTEXT_RESOURCE_SERVICE_CATALOG_REF", "catalog:test")
    monkeypatch.setenv(
        "TGW_CONTEXT_RESOURCE_SERVICE_CATALOG_HASH", "sha256:" + "f" * 64,
    )
    monkeypatch.setenv(
        "TGW_CONTEXT_REVIEW_SKILL_CONTRACT_HASH", "sha256:" + "e" * 64,
    )
    monkeypatch.setenv("TGW_CONTEXT_REVIEW_UID", str(os.geteuid()))
    monkeypatch.setenv("TGW_CONTEXT_REVIEW_GID", str(os.getegid()))
    monkeypatch.setenv("TGW_CONTEXT_ATTESTATION_KEY_ID", "test-key")
    monkeypatch.setenv("TGW_CONTEXT_ATTESTATION_PUBLIC_KEY", public_key)
    now = datetime.now(timezone.utc)
    grant_request = {
        "schema": "tgw-context-review-broker-request/v2",
        "client_id": "review-client", "challenge": "c" * 64,
        "skill_contract_hash": "sha256:" + "e" * 64,
        "card_hash": card["card_hash"], "role": "independent-review",
        "execution_identity": (
            f"governed-review:{'c' * 64}:uid={os.geteuid()}:gid={os.getegid()}"
        ),
        "handoff_hash": "sha256:" + "d" * 64,
        "resource_receipt_hash": receipt["receipt_hash"],
        "resource_service_catalog_ref": "catalog:test",
        "resource_service_catalog_hash": "sha256:" + "f" * 64,
        "resources": bindings,
        "issued_at": (now - timedelta(seconds=1)).isoformat(),
        "not_before": (now - timedelta(seconds=1)).isoformat(),
        "expires_at": (now + timedelta(minutes=10)).isoformat(),
    }
    grant = {
        "schema": "tgw-governed-review-context-grant/v1",
        "request": grant_request,
        "request_hash": context._sha(context._canonical(grant_request)),
    }
    result, visible = context._review_context_run(
        challenge="c" * 64, card_json=json.dumps(card),
        handoff_hash="sha256:" + "d" * 64,
        resource_receipt_hash=receipt["receipt_hash"],
        skill_contract_hash="sha256:" + "e" * 64,
        grant_json=json.dumps(grant),
        broker_factory=FakeBroker,
    )
    assert result == {
        "schema": "tgw-context-review-run/v1", "status": "PASS",
        "context_run_id": "run-bound-context", "challenge": "c" * 64,
        "card_hash": card["card_hash"],
        "resource_receipt_hash": receipt["receipt_hash"],
        "skill_contract_hash": "sha256:" + "e" * 64,
        "runtime_uid": os.geteuid(), "runtime_gid": os.getegid(),
        "retrieval_attestation": result["retrieval_attestation"],
        "resource_bundle_hash": result["resource_bundle_hash"],
    }
    assert {
        name: visible[name]["content"].encode() for name in sorted(visible)
    } == contents
    assert observed["endpoint"] == "https://broker.invalid"
    assert observed["request"]["resources"] == bindings


def test_governed_review_sse_is_loopback_only_and_exposes_only_bound_tool(monkeypatch):
    monkeypatch.setenv("TGW_CONTEXT_MCP_HOST", "0.0.0.0")
    with pytest.raises(ValueError, match="loopback"):
        context._sse_binding(governed=True)

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    environment = {
        **os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "TGW_CONTEXT_MCP_HOST": "127.0.0.1", "TGW_CONTEXT_MCP_PORT": str(port),
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m", "tgw.context_mcp_server", "--governed-review-sse",
        ],
        env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        for _attempt in range(50):
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.05)
        else:
            raise AssertionError("governed review SSE server did not start")
        mcp_config = {
            "mcpServers": {"tgw-context": {
                "type": "sse", "url": f"http://127.0.0.1:{port}/sse",
            }},
        }

        async def inspect_server() -> None:
            async with sse_client(
                mcp_config["mcpServers"]["tgw-context"]["url"],
                timeout=2, sse_read_timeout=5,
            ) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    assert [tool.name for tool in tools.tools] == ["tgw_context_bundle"]
                    result = await session.call_tool(
                        "tgw_context_bundle", {"task": "must be governed"},
                    )
                    assert "TGW_CONTEXT_STARTUP_BINDING is required" in result.content[0].text

        anyio.run(inspect_server)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
