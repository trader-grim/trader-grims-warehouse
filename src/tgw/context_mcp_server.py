"""Authoritative MCP context bound to an approved Plan and exact source tree.

The server deliberately reads committed blobs, never mutable checkout bytes.
It exposes navigation/evidence plus one credential-free confirmation receipt
bound to this exact process and its active live-client obligation.  It has no
approval, queue, deployment, or arbitrary provider-effect operation.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from mcp.server import FastMCP

from tgw.code_graph import CodeGraphService, build_snapshot
from tgw.context_source_guard import (
    ContextSourceGuardError,
    closed_git_environment,
    validate_context_source,
)
from tgw.execution_resources import (
    CARD_RESOURCE_NAMES,
    ResourceVerificationError,
    card_resource_receipt,
    content_hash,
    validate_harness_retrieval_attestation,
)
from tgw.plan_graph import live_plan_graph

SCHEMA = "tgw-context-service/v1"
FULL_COMMIT = re.compile(r"[0-9a-f]{40}")
MAX_TEXT_BYTES = 2_000_000
MAX_QUERY = 1_000
MAX_LINES = 250
MAX_RESULTS = 100
MAX_REVIEW_BUNDLE_BYTES = 64 * 1024 * 1024
PLAN_PREFIXES = ("plan/", "pp/", "reference/")
RUNBOOK_PREFIX = "docs/runbooks/"
PLAN_RUNBOOK_PREFIX = "reference/runbooks/"
CURRENT_PLAN_SOURCE_PATHS = (
    "plan/execution/AMENDMENT-20260823-MCP-LIVE-CLIENT-CONVERGENCE.yaml",
    "pp/PP-ACTOR-MCP-BOUNDARY-001.md",
    "plan/execution/targets/W19-W21-MCP-ONLY-ACTOR-HARDENING-v1.yaml",
    "plan/execution/ACTIVE-PLAN-AMENDMENT-PROCESS-v1.yaml",
)
_INSTRUCTION_ENTRY_POINTS = {
    "claude": "/home/claude/.claude/CLAUDE.md",
    "codex": "/home/codex/.codex/AGENTS.md",
    "deepseek": "/home/deepseek/.dsh/AGENTS.md",
}
SCOPE_SEMANTICS = {
    "default_execution_root": "TGW Master Plan",
    "governed_execution_platform_ref": "plan/execution/GOVERNED-EXECUTION-PLATFORM-v1.yaml",
    "platform_w11_completion_implies_master_plan_completion": False,
    "narrow_plan_pp_or_todo_completion_implies_parent_completion": False,
}


class ContextError(RuntimeError):
    """A binding, source, or bounded-query precondition failed."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _path_env(name: str, default: str) -> Path:
    value = Path(os.environ.get(name, default))
    if not value.is_absolute():
        raise ContextError(f"{name} must be an absolute path")
    return value.resolve(strict=True)


def _git_executable() -> Path:
    path = shutil.which("git", path=os.environ.get("PATH", "/usr/bin:/bin"))
    if path is None:
        raise ContextError("catalog-pinned Git is unavailable")
    try:
        return Path(path).resolve(strict=True)
    except OSError as exc:
        raise ContextError("catalog-pinned Git is unavailable") from exc


def _git_env(git: Path) -> dict[str, str]:
    try:
        return closed_git_environment(git)
    except ContextSourceGuardError as exc:
        raise ContextError(str(exc)) from exc


def _git(root: Path, *args: str, bytes_output: bool = False) -> str | bytes:
    git = _git_executable()
    result = subprocess.run(
        [
            str(git), "-c", f"safe.directory={root}",
            "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
            "-C", str(root), *args,
        ],
        check=False,
        capture_output=True,
        timeout=30,
        env=_git_env(git),
    )
    if result.returncode:
        message = result.stderr.decode(errors="replace").strip()
        raise ContextError(message or f"git {' '.join(args)} failed")
    return result.stdout if bytes_output else result.stdout.decode().strip()


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    git = _git_executable()
    result = subprocess.run(
        [
            str(git), "-c", f"safe.directory={root}",
            "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
            "-C", str(root), "merge-base", "--is-ancestor", ancestor, descendant,
        ],
        check=False,
        capture_output=True,
        timeout=30,
        env=_git_env(git),
    )
    if result.returncode not in {0, 1}:
        raise ContextError(result.stderr.decode(errors="replace").strip() or "Plan ancestry check failed")
    return result.returncode == 0


def _approved_commit() -> str:
    commit = os.environ.get("TGW_CONTEXT_PLAN_COMMIT", "")
    if not FULL_COMMIT.fullmatch(commit):
        raise ContextError("TGW_CONTEXT_PLAN_COMMIT must be a full approved commit")
    return commit


def _approved_solution() -> str:
    solution = os.environ.get("TGW_CONTEXT_PLAN_SOLUTION", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", solution):
        raise ContextError("TGW_CONTEXT_PLAN_SOLUTION must be an exact approved solution hash")
    return solution


def _current_actor_startup_binding() -> dict[str, str]:
    """Fail closed when this long-lived MCP child predates fleet cutover."""
    raw_path = os.environ.get("TGW_CONTEXT_STARTUP_BINDING", "")
    if not raw_path:
        raise ContextError("TGW_CONTEXT_STARTUP_BINDING is required")
    path = Path(raw_path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ContextError("actor startup binding is unavailable")
    observed = path.stat(follow_symlinks=False)
    if observed.st_uid != 0 or observed.st_mode & 0o022 or not stat.S_ISREG(observed.st_mode):
        raise ContextError("actor startup binding is not root-owned and immutable")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContextError("actor startup binding is invalid") from exc
    required = {
        "schema", "actor", "trusted_public_key", "expected_generation",
        "expected_plan_commit", "expected_solution_hash",
        "expected_source_commit", "expected_source_tree",
        "context_source_root", "expected_catalog_hash",
        "fleet_convergence_path",
        "stable_launcher_path",
    }
    expected = {
        "schema": "tgw-actor-startup-binding/v3",
        "actor": os.environ.get("TGW_CONTEXT_ACTOR", ""),
        "expected_generation": os.environ.get("TGW_CONTEXT_GENERATION", ""),
        "expected_plan_commit": os.environ.get("TGW_CONTEXT_PLAN_COMMIT", ""),
        "expected_solution_hash": os.environ.get("TGW_CONTEXT_PLAN_SOLUTION", ""),
        "expected_source_commit": os.environ.get("TGW_CONTEXT_SOURCE_COMMIT", ""),
        "expected_source_tree": os.environ.get("TGW_CONTEXT_SOURCE_TREE", ""),
        "context_source_root": os.environ.get("TGW_CONTEXT_SOURCE_ROOT", ""),
        "expected_catalog_hash": os.environ.get("TGW_CONTEXT_ENVIRONMENT_CATALOG_HASH", ""),
        "fleet_convergence_path": os.environ.get(
            "TGW_CONTEXT_FLEET_CONVERGENCE", ""
        ),
        "stable_launcher_path": os.environ.get(
            "TGW_CONTEXT_STABLE_LAUNCHER", ""
        ),
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value.get("schema") != "tgw-actor-startup-binding/v3"
        or any(value.get(name) != expected_value for name, expected_value in expected.items())
    ):
        raise ContextError("actor Context MCP process is stale after fleet cutover")
    return {name: str(raw) for name, raw in value.items()}


def _fleet_convergence(startup_binding: Mapping[str, str]) -> dict[str, Any]:
    """Read the exact non-secret provider projection for cold handoff."""
    raw_path = os.environ.get("TGW_CONTEXT_FLEET_CONVERGENCE", "")
    if raw_path != startup_binding.get("fleet_convergence_path"):
        raise ContextError("fleet convergence path differs from startup binding")
    path = Path(raw_path)
    if (
        not path.is_absolute()
        or path == Path("/tmp")
        or Path("/tmp") in path.parents
        or path.is_symlink()
    ):
        raise ContextError("fleet convergence projection is unavailable")
    try:
        descriptor = os.open(
            path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
        )
        try:
            before = os.fstat(descriptor)
            raw = os.read(descriptor, 2_000_001)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if (
            before.st_uid != 0
            or before.st_mode & 0o022
            or before.st_nlink != 1
            or not stat.S_ISREG(before.st_mode)
            or len(raw) > 2_000_000
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
        ):
            raise ContextError("fleet convergence projection is not root protected")
        projection = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise ContextError("fleet convergence projection is invalid") from exc
    if not isinstance(projection, dict):
        raise ContextError("fleet convergence projection is invalid")
    outer = dict(projection)
    claimed_outer = outer.pop("projection_sha256", None)
    if (
        set(projection) != {
            "schema", "state", "generation_status", "active_transaction_ids",
            "active_pointer_sha256", "supersessions_sha256", "transaction",
            "projection_sha256",
        }
        or projection.get("schema") != "tgw-fleet-convergence-set/v1"
        or claimed_outer != _sha(_canonical(outer))
        or projection.get("state") not in {"ACTIVE", "TERMINAL"}
        or projection.get("generation_status") not in {
            "CURRENT", "UPDATE_PENDING", "RESTART_REQUIRED", "MIXED", "HOLD"
        }
        or not isinstance(projection.get("active_transaction_ids"), list)
        or not re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(projection.get("active_pointer_sha256", "")),
        )
        or not re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(projection.get("supersessions_sha256", "")),
        )
    ):
        # AMBIGUOUS and NO_TRANSACTION are deliberately not usable as a
        # positive context binding.
        raise ContextError("fleet convergence projection is ambiguous or absent")
    transaction = projection.get("transaction")
    if not isinstance(transaction, dict):
        raise ContextError("fleet convergence transaction is unavailable")
    inner = dict(transaction)
    claimed_inner = inner.pop("projection_sha256", None)
    required = {
        "schema", "status", "transaction_id", "actors", "direction",
        "predecessor_generation", "successor_generation", "target_generation",
        "target_revisions", "boot_id", "created_at", "updated_at",
        "journal_sha256", "journal_payload_sha256", "ledger_sequence",
        "ledger_record_sha256", "ledger_evidence",
        "coordinator_binding_sha256",
        "confinement_state", "selected_release", "admission_evidence",
        "real_store_evidence_sha256", "cold_handoff_evidence_sha256",
        "cold_handoff_receipt_sha256",
        "managed_service_action_receipt_sha256",
        "terminal_convergence_receipt_sha256",
        "actor_verifications", "last_verified_at",
        "obligations", "obligations_sha256",
        "global_pending", "projection_sha256",
    }
    revisions = transaction.get("target_revisions")
    obligations = transaction.get("obligations")
    actors = transaction.get("actors")
    current_boot = Path("/proc/sys/kernel/random/boot_id").read_text(
        encoding="utf-8"
    ).strip()
    if (
        set(transaction) != required
        or transaction.get("schema") != "tgw-fleet-convergence-projection/v1"
        or claimed_inner != _sha(_canonical(inner))
        or not re.fullmatch(
            r"[a-z0-9][a-z0-9._-]{0,127}",
            str(transaction.get("transaction_id", "")),
        )
        or transaction.get("direction") not in {"successor", "rollback"}
        or transaction.get("target_generation")
        != startup_binding["expected_generation"]
        or not isinstance(revisions, dict)
        or set(revisions) != {
            "approved_plan", "approved_solution", "evidence_plan",
            "evidence_tree", "source_commit", "source_tree",
            "current_plan_sources", "current_plan_sources_sha256", "catalog",
            "bootstrap", "broker_policy", "review", "admission",
        }
        or revisions.get("approved_plan")
        != startup_binding["expected_plan_commit"]
        or revisions.get("approved_solution")
        != startup_binding["expected_solution_hash"]
        or revisions.get("source_commit")
        != startup_binding["expected_source_commit"]
        or revisions.get("source_tree")
        != startup_binding["expected_source_tree"]
        or revisions.get("catalog") != startup_binding["expected_catalog_hash"]
        or not FULL_COMMIT.fullmatch(str(revisions.get("evidence_plan", "")))
        or not FULL_COMMIT.fullmatch(str(revisions.get("evidence_tree", "")))
        or not isinstance(revisions.get("current_plan_sources"), dict)
        or revisions.get("current_plan_sources_sha256")
        != _sha(_canonical(revisions.get("current_plan_sources")))
        or transaction.get("boot_id") != current_boot
        or not isinstance(actors, list)
        or not actors
        or actors != sorted(set(actors))
        or startup_binding["actor"] not in actors
        or not isinstance(obligations, list)
        or len(obligations) < len(actors)
        or len(obligations) > 256
        or not isinstance(transaction.get("global_pending"), list)
        or not isinstance(transaction.get("actor_verifications"), list)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(transaction.get("journal_sha256", "")))
        or not re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(transaction.get("journal_payload_sha256", "")),
        )
        or not isinstance(transaction.get("ledger_sequence"), int)
        or transaction.get("ledger_sequence") < 1
        or not re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(transaction.get("ledger_record_sha256", "")),
        )
        or not re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(transaction.get("coordinator_binding_sha256", "")),
        )
        or transaction.get("confinement_state")
        != "NON_CONFINING_ACTOR_COMPOSITE_STORES"
        or any(
            not re.fullmatch(
                r"sha256:[0-9a-f]{64}", str(transaction.get(name, ""))
            )
            for name in (
                "real_store_evidence_sha256", "cold_handoff_evidence_sha256"
            )
        )
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(transaction.get("obligations_sha256", "")))
    ):
        raise ContextError("fleet convergence transaction binding differs")
    selected_release = transaction.get("selected_release")
    admission_evidence = transaction.get("admission_evidence")
    ledger_evidence = transaction.get("ledger_evidence")
    ledger_unsigned = (
        dict(ledger_evidence) if isinstance(ledger_evidence, dict) else {}
    )
    ledger_link_hash = ledger_unsigned.pop("link_sha256", None)
    if (
        not isinstance(selected_release, dict)
        or set(selected_release)
        != {"path", "generation", "commit", "tree", "manifest_sha256"}
        or not isinstance(selected_release.get("path"), str)
        or not selected_release["path"].startswith("/opt/TGW/")
        or ".." in Path(selected_release["path"]).parts
        or not isinstance(selected_release.get("generation"), str)
        or not selected_release["generation"]
        or not FULL_COMMIT.fullmatch(str(selected_release.get("commit", "")))
        or not FULL_COMMIT.fullmatch(str(selected_release.get("tree", "")))
        or not re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(selected_release.get("manifest_sha256", "")),
        )
        or not isinstance(admission_evidence, dict)
        or set(admission_evidence)
        != {
            "review_receipt_sha256", "admission_receipt_sha256",
            "ledger_sequence", "ledger_record_sha256",
        }
        or admission_evidence.get("review_receipt_sha256")
        != revisions["review"]
        or admission_evidence.get("admission_receipt_sha256")
        != revisions["admission"]
        or admission_evidence.get("ledger_sequence")
        != transaction["ledger_sequence"]
        or admission_evidence.get("ledger_record_sha256")
        != transaction["ledger_record_sha256"]
        or not isinstance(ledger_evidence, dict)
        or set(ledger_evidence) != {
            "schema", "sequence", "record_sha256", "evidence_sha256",
            "review_receipt_sha256", "admission_receipt_sha256",
            "actor_verification_receipt_hashes",
            "client_confirmation_hashes", "parent_transition_hashes",
            "cold_handoff_receipt_sha256",
            "managed_service_action_receipt_sha256",
            "terminal_convergence_receipt_sha256", "link_sha256",
        }
        or ledger_evidence.get("schema")
        != "tgw-provider-ledger-evidence-link/v1"
        or ledger_evidence.get("sequence") != transaction["ledger_sequence"]
        or ledger_evidence.get("record_sha256")
        != transaction["ledger_record_sha256"]
        or ledger_evidence.get("review_receipt_sha256") != revisions["review"]
        or ledger_evidence.get("admission_receipt_sha256")
        != revisions["admission"]
        or not isinstance(
            ledger_evidence.get("actor_verification_receipt_hashes"), dict
        )
        or not isinstance(ledger_evidence.get("client_confirmation_hashes"), list)
        or not isinstance(ledger_evidence.get("parent_transition_hashes"), list)
        or any(
            not re.fullmatch(r"sha256:[0-9a-f]{64}", str(value))
            for value in (
                ledger_evidence.get("evidence_sha256"),
                *ledger_evidence["actor_verification_receipt_hashes"].values(),
                *ledger_evidence["client_confirmation_hashes"],
                *ledger_evidence["parent_transition_hashes"],
            )
        )
        or any(
            value is not None
            and not re.fullmatch(r"sha256:[0-9a-f]{64}", str(value))
            for value in (
                ledger_evidence.get("cold_handoff_receipt_sha256"),
                ledger_evidence.get("managed_service_action_receipt_sha256"),
                ledger_evidence.get("terminal_convergence_receipt_sha256"),
            )
        )
        or transaction.get("cold_handoff_receipt_sha256")
        != ledger_evidence.get("cold_handoff_receipt_sha256")
        or transaction.get("managed_service_action_receipt_sha256")
        != ledger_evidence.get("managed_service_action_receipt_sha256")
        or transaction.get("terminal_convergence_receipt_sha256")
        != ledger_evidence.get("terminal_convergence_receipt_sha256")
        or ledger_link_hash != _sha(_canonical(ledger_unsigned))
    ):
        raise ContextError("fleet convergence release evidence differs")
    seen: set[str] = set()
    for obligation in obligations:
        if (
            not isinstance(obligation, dict)
            or set(obligation) != {
                "obligation_id", "actor", "baseline_state",
                "checkpoint_disposition",
                "path_identity_hash", "parent_identity_hash",
                "baseline_child_identity_hashes", "replacement_policy",
                "disposition", "pending_reasons", "client_confirmation_hash",
                "parent_transition_hash", "parent_transition_disposition",
            }
            or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", str(obligation.get("obligation_id", ""))
            )
            or obligation["obligation_id"] in seen
            or obligation.get("actor") not in actors
            or obligation.get("baseline_state") not in {
                "IDLE", "LIVE", "LATE_CURRENT", "LATE_STALE"
            }
            or obligation.get("checkpoint_disposition") not in {
                None, "LATE_ARRIVAL"
            }
            or not isinstance(obligation.get("baseline_child_identity_hashes"), list)
            or not isinstance(obligation.get("pending_reasons"), list)
            or obligation.get("parent_transition_disposition") not in {
                None, "OPERATOR_HARNESS_RESTART",
                "DECLARED_USER_SERVICE_RESTART",
            }
            or (
                obligation.get("parent_transition_hash") is not None
                and not re.fullmatch(
                    r"sha256:[0-9a-f]{64}",
                    str(obligation.get("parent_transition_hash")),
                )
            )
        ):
            raise ContextError("fleet convergence obligation is invalid")
        seen.add(obligation["obligation_id"])
    verified_actors: set[str] = set()
    for verification in transaction["actor_verifications"]:
        if (
            not isinstance(verification, dict)
            or set(verification) != {
                "actor", "actor_proof_hash", "context_mcp_proof_hash",
                "verification_receipt_sha256",
                "primary_real_store_semantic_sha256", "live_context_state",
                "instruction_entry_point_path",
                "instruction_entry_point_sha256", "verified_at",
            }
            or verification.get("actor") not in actors
            or verification["actor"] in verified_actors
            or any(
                not re.fullmatch(r"sha256:[0-9a-f]{64}", str(verification.get(name, "")))
                for name in (
                    "actor_proof_hash", "context_mcp_proof_hash",
                    "verification_receipt_sha256",
                    "primary_real_store_semantic_sha256",
                    "instruction_entry_point_sha256",
                )
            )
            or verification.get("instruction_entry_point_path")
            != _INSTRUCTION_ENTRY_POINTS.get(str(verification.get("actor")))
        ):
            raise ContextError("fleet convergence actor verification is invalid")
        if (
            ledger_evidence["actor_verification_receipt_hashes"].get(
                verification["actor"]
            )
            != verification["verification_receipt_sha256"]
        ):
            raise ContextError("fleet convergence actor receipt differs")
        verified_actors.add(verification["actor"])
    active_ids = projection["active_transaction_ids"]
    if (
        (projection["state"] == "ACTIVE" and active_ids != [transaction["transaction_id"]])
        or (projection["state"] == "TERMINAL" and active_ids != [])
        or (
            projection["state"] == "TERMINAL"
            and transaction.get("status") not in {"VERIFIED", "ROLLED_BACK"}
        )
        or (
            projection["state"] == "ACTIVE"
            and transaction.get("status") in {"VERIFIED", "ROLLED_BACK"}
        )
        or (
            projection["state"] == "TERMINAL"
            and transaction.get("terminal_convergence_receipt_sha256") is None
        )
    ):
        raise ContextError("fleet convergence lifecycle state differs")
    if transaction.get("status") == "VERIFIED" and verified_actors != set(actors):
        raise ContextError("fleet convergence terminal actor proof is incomplete")
    return projection


def _active_instruction_entry_point(
    startup_binding: Mapping[str, str],
    fleet_convergence: Mapping[str, Any],
) -> dict[str, Any]:
    actor = str(startup_binding["actor"])
    expected_path = _INSTRUCTION_ENTRY_POINTS.get(actor)
    transaction = fleet_convergence.get("transaction")
    verifications = (
        transaction.get("actor_verifications", [])
        if isinstance(transaction, Mapping) else []
    )
    verification = next(
        (
            item
            for item in verifications
            if isinstance(item, Mapping) and item.get("actor") == actor
        ),
        None,
    )
    if fleet_convergence.get("generation_status") != "CURRENT":
        return {
            "path": expected_path,
            "sha256": None,
            "state": "PENDING_VERIFICATION",
        }
    if (
        expected_path is None
        or not isinstance(verification, Mapping)
        or verification.get("instruction_entry_point_path") != expected_path
        or re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(verification.get("instruction_entry_point_sha256", "")),
        )
        is None
    ):
        raise ContextError("actor instruction entry point is absent from fleet proof")
    path = Path(expected_path)
    try:
        link_state = path.lstat()
        target = path.resolve(strict=True)
        target_state = target.stat(follow_symlinks=False)
        raw = target.read_bytes()
    except OSError as exc:
        raise ContextError("actor instruction entry point is unavailable") from exc
    observed_hash = _sha(raw)
    if (
        not path.is_symlink()
        or link_state.st_uid != 0
        or target.is_symlink()
        or not target.is_file()
        or target_state.st_uid != 0
        or target_state.st_mode & 0o022
        or observed_hash != verification["instruction_entry_point_sha256"]
    ):
        raise ContextError("actor instruction entry point differs from fleet proof")
    return {"path": expected_path, "sha256": observed_hash, "state": "CURRENT"}


def _catalog_git(catalog: Mapping[str, Any], catalog_path: Path) -> Path:
    actor = os.environ.get("TGW_CONTEXT_ACTOR", "")
    declaration = catalog.get("actors", {}).get(actor)
    profiles = declaration.get("permitted_profiles") if isinstance(declaration, Mapping) else None
    if (
        not actor
        or not isinstance(declaration, Mapping)
        or declaration.get("enabled") is not True
        or not isinstance(profiles, list)
        or not profiles
    ):
        raise ContextError("actor is not enabled in the exact environment catalog")
    identities: set[tuple[str, str]] = set()
    for profile_name in profiles:
        profile = catalog.get("profiles", {}).get(profile_name)
        tools = profile.get("tools") if isinstance(profile, Mapping) else None
        matches = [
            item for item in tools or []
            if isinstance(item, Mapping) and item.get("name") == "git"
        ]
        if len(matches) != 1:
            raise ContextError("catalog-pinned Git is unavailable")
        path, digest = matches[0].get("executable_path"), matches[0].get("executable_sha256")
        if not isinstance(path, str) or not isinstance(digest, str):
            raise ContextError("catalog-pinned Git identity is invalid")
        identities.add((path, digest))
    if len(identities) != 1:
        raise ContextError("actor profiles do not share one catalog-pinned Git")
    raw_path, expected_digest = next(iter(identities))
    try:
        executable = Path(raw_path).resolve(strict=True)
    except OSError as exc:
        raise ContextError("catalog-pinned Git is unavailable") from exc
    if (
        not executable.is_file()
        or executable.is_symlink()
        or _sha(executable.read_bytes()) != expected_digest
        or executable != _git_executable()
    ):
        raise ContextError(f"catalog-pinned Git identity differs: {catalog_path}")
    return executable


def _runtime_identity() -> dict[str, Any]:
    required_paths = {
        "entrypoint": "TGW_CONTEXT_RUNTIME_ENTRYPOINT",
        "startup_module": "TGW_CONTEXT_RUNTIME_MODULE",
        "context_module": "TGW_CONTEXT_RUNTIME_CONTEXT_MODULE",
        "stable_launcher": "TGW_CONTEXT_STABLE_LAUNCHER",
        "executable": "TGW_CONTEXT_RUNTIME_EXECUTABLE",
    }
    resolved: dict[str, Path] = {}
    for label, name in required_paths.items():
        raw = os.environ.get(name, "")
        if not raw or not Path(raw).is_absolute():
            raise ContextError(f"{name} is required")
        try:
            path = Path(raw)
            target = path.resolve(strict=True)
        except OSError as exc:
            raise ContextError(f"{name} is unavailable") from exc
        if not target.is_file():
            raise ContextError(f"{name} is unavailable")
        expected_hash = os.environ.get(name + "_SHA256", "")
        if _sha(target.read_bytes()) != expected_hash:
            raise ContextError(f"{name} runtime hash differs")
        resolved[label] = path
    loaded_context = Path(__file__).resolve(strict=True)
    source_root = Path(os.environ.get("TGW_CONTEXT_SOURCE_ROOT", ""))
    expected_runtime_paths = {
        "entrypoint": source_root / "scripts" / "tgw_actor_startup.py",
        "startup_module": source_root / "src" / "tgw" / "actor_startup.py",
        "context_module": source_root / "src" / "tgw" / "context_mcp_server.py",
    }
    if any(
        resolved[name].resolve(strict=True) != expected.resolve(strict=True)
        for name, expected in expected_runtime_paths.items()
    ):
        raise ContextError("actor Context runtime paths differ from retained source")
    if loaded_context != resolved["context_module"].resolve(strict=True):
        raise ContextError("loaded Context MCP module differs from exact candidate")
    executable = Path(sys.executable).resolve(strict=True)
    executable_state = executable.stat(follow_symlinks=False)
    if (
        executable != resolved["executable"].resolve(strict=True)
        or str(executable_state.st_dev) != os.environ.get("TGW_CONTEXT_RUNTIME_EXECUTABLE_DEVICE")
        or str(executable_state.st_ino) != os.environ.get("TGW_CONTEXT_RUNTIME_EXECUTABLE_INODE")
    ):
        raise ContextError("loaded Context MCP executable identity differs")
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="utf-8"
        ).strip()
        status_rows = Path("/proc/self/status").read_text(encoding="utf-8").splitlines()
        status = {
            row.split(":", 1)[0]: row.split(":", 1)[1].strip()
            for row in status_rows if ":" in row
        }
        raw_stat = Path("/proc/self/stat").read_text(encoding="utf-8")
        arguments = [
            raw.decode("utf-8", errors="replace")
            for raw in Path("/proc/self/cmdline").read_bytes().split(b"\0") if raw
        ]
        cmdline_shape = [Path(arguments[0]).name if arguments else ""]
        cmdline_shape.extend(
            item for item in arguments[1:]
            if item.startswith("--") or item in {"-m", "tgw.context_mcp_server"}
        )
        process = {
            "boot_id": boot_id,
            "pid": os.getpid(),
            "start_ticks": int(raw_stat.rsplit(") ", 1)[1].split()[19]),
            "uid": int(status["Uid"].split()[0]),
            "ppid": int(status.get("PPid", "0")),
            "executable_path": str(executable),
            "executable_device": executable_state.st_dev,
            "executable_inode": executable_state.st_ino,
            "executable_sha256": os.environ["TGW_CONTEXT_RUNTIME_EXECUTABLE_SHA256"],
            "cmdline_shape": cmdline_shape,
            "cmdline_sha256": _sha(_canonical(arguments)),
        }
    except (OSError, KeyError, ValueError) as exc:
        raise ContextError("loaded Context MCP process identity is unavailable") from exc
    process["identity_hash"] = _sha(_canonical(process))
    return {
        "entrypoint": str(resolved["entrypoint"]),
        "entrypoint_sha256": os.environ["TGW_CONTEXT_RUNTIME_ENTRYPOINT_SHA256"],
        "startup_module": str(resolved["startup_module"]),
        "startup_module_sha256": os.environ["TGW_CONTEXT_RUNTIME_MODULE_SHA256"],
        "context_module": str(resolved["context_module"]),
        "context_module_sha256": os.environ["TGW_CONTEXT_RUNTIME_CONTEXT_MODULE_SHA256"],
        "stable_launcher": str(resolved["stable_launcher"]),
        "stable_launcher_sha256": os.environ["TGW_CONTEXT_STABLE_LAUNCHER_SHA256"],
        "executable": str(executable),
        "executable_sha256": os.environ["TGW_CONTEXT_RUNTIME_EXECUTABLE_SHA256"],
        "executable_device": executable_state.st_dev,
        "executable_inode": executable_state.st_ino,
        "process": process,
    }


@lru_cache(maxsize=4)
def _protected_context_source_once(
    source_root: str,
    git: str,
    expected_commit: str,
    expected_tree: str,
) -> Path:
    try:
        root, _commit, _tree = validate_context_source(
            Path(source_root), Path(git),
            expected_commit=expected_commit,
            expected_tree=expected_tree,
        )
    except ContextSourceGuardError as exc:
        raise ContextError(f"actor Context MCP source is not protected: {exc}") from exc
    return root


def _bindings() -> dict[str, Any]:
    startup_binding = _current_actor_startup_binding()
    fleet_convergence = _fleet_convergence(startup_binding)
    instruction_entry_point = _active_instruction_entry_point(
        startup_binding, fleet_convergence
    )
    runtime_identity = _runtime_identity()
    plan_root = _path_env("TGW_CONTEXT_PLAN_ROOT", "/opt/TGW/tgw-lib/runtime/approved-plan")
    plan_repository = _path_env("TGW_CONTEXT_PLAN_REPOSITORY", "/opt/TGW/library/plans")
    source_root = _path_env(
        "TGW_CONTEXT_SOURCE_ROOT", "/opt/TGW/tgw-lib/src/trader-grims-warehouse"
    )
    runtime_root = Path(os.environ.get("TGW_CONTEXT_RUNTIME_ROOT", "/opt/TGW/tgw-lib/var/context"))
    if not runtime_root.is_absolute():
        raise ContextError("TGW_CONTEXT_RUNTIME_ROOT must be an absolute path")
    approved = _approved_commit()
    solution = _approved_solution()
    catalog_path = Path(os.environ.get(
        "TGW_CONTEXT_ENVIRONMENT_CATALOG",
        "/etc/tgw/execution-environment-catalog.json",
    ))
    if not catalog_path.is_absolute():
        raise ContextError("TGW_CONTEXT_ENVIRONMENT_CATALOG must be an absolute path")
    catalog_resolved_path = catalog_path.resolve(strict=True)
    try:
        catalog = json.loads(catalog_resolved_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContextError("execution environment catalog is invalid") from exc
    if (
        not isinstance(catalog, dict)
        or catalog.get("schema") != "tgw-execution-environment-catalog/v3"
        or not isinstance(catalog.get("actors"), dict)
        or not isinstance(catalog.get("profiles"), dict)
    ):
        raise ContextError("execution environment catalog is invalid")
    catalog_hash = _sha(_canonical(catalog))
    expected_catalog_hash = os.environ.get("TGW_CONTEXT_ENVIRONMENT_CATALOG_HASH", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_catalog_hash):
        raise ContextError("TGW_CONTEXT_ENVIRONMENT_CATALOG_HASH must be exact")
    if catalog_hash != expected_catalog_hash:
        raise ContextError("execution environment catalog hash does not match configured revision")
    catalog_git = _catalog_git(catalog, catalog_resolved_path)
    if _git(plan_root, "rev-parse", "HEAD^{commit}") != approved:
        raise ContextError("approved Plan materialization does not match configured commit")
    if _git(plan_root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ContextError("approved Plan materialization is not clean")
    if _git(plan_repository, "cat-file", "-t", approved) != "commit":
        raise ContextError("approved Plan commit is absent from the canonical repository")
    evidence_head = _git(plan_repository, "rev-parse", "HEAD^{commit}")
    assert isinstance(evidence_head, str)
    if _git(plan_repository, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ContextError("canonical Plan repository is not clean")
    if not _is_ancestor(plan_repository, approved, evidence_head):
        raise ContextError("canonical Plan evidence HEAD does not descend from the approved Plan")
    evidence_tree = _git(
        plan_repository, "rev-parse", f"{evidence_head}^{{tree}}"
    )
    projected_revisions = fleet_convergence["transaction"]["target_revisions"]
    projected_current_sources = projected_revisions.get("current_plan_sources")
    observed_current_sources = {
        path: _sha(
            _git(
                plan_repository, "show", f"{evidence_head}:{path}",
                bytes_output=True,
            )
        )
        for path in CURRENT_PLAN_SOURCE_PATHS
    }
    if (
        projected_revisions.get("evidence_plan") != evidence_head
        or projected_revisions.get("evidence_tree") != evidence_tree
        or projected_current_sources != observed_current_sources
    ):
        raise ContextError("fleet projection differs from current Plan evidence")
    source_root = _protected_context_source_once(
        str(source_root), str(catalog_git),
        startup_binding["expected_source_commit"], startup_binding["expected_source_tree"],
    )
    source_commit = _git(source_root, "rev-parse", "HEAD^{commit}")
    source_tree = _git(source_root, "rev-parse", f"{source_commit}^{{tree}}")
    source_status = _git(source_root, "status", "--porcelain=v1", "--untracked-files=all")
    assert isinstance(source_commit, str) and isinstance(source_tree, str) and isinstance(source_status, str)
    if (
        source_commit != startup_binding["expected_source_commit"]
        or source_tree != startup_binding["expected_source_tree"]
        or source_status
    ):
        raise ContextError("actor Context MCP source is stale or dirty after fleet cutover")
    if str(source_root) != startup_binding["context_source_root"]:
        raise ContextError("actor Context MCP source root differs from startup binding")
    return {
        "plan_root": plan_root,
        "plan_repository": plan_repository,
        "plan_commit": approved,
        "plan_solution_hash": solution,
        "plan_tree": _git(plan_root, "rev-parse", "HEAD^{tree}"),
        "plan_repository_head": evidence_head,
        "plan_repository_tree": evidence_tree,
        "source_root": source_root,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "source_worktree_clean": not bool(source_status),
        "source_status_sha256": _sha(str(source_status).encode()),
        "runtime_root": runtime_root,
        "environment_catalog": catalog,
        "environment_catalog_path": catalog_path,
        "environment_catalog_resolved_path": catalog_resolved_path,
        "environment_catalog_hash": catalog_hash,
        "runtime_identity": runtime_identity,
        "startup_binding": startup_binding,
        "instruction_entry_point": instruction_entry_point,
        "fleet_convergence": fleet_convergence,
    }


def _bytes_at(root: Path, commit: str, path: str) -> bytes:
    raw = _git(root, "show", f"{commit}:{path}", bytes_output=True)
    assert isinstance(raw, bytes)
    if len(raw) > MAX_TEXT_BYTES:
        raise ContextError(f"source exceeds {MAX_TEXT_BYTES} byte retrieval bound")
    return raw


def _safe_path(path: str, prefixes: tuple[str, ...]) -> str:
    if not isinstance(path, str) or not path or len(path) > 500:
        raise ContextError("path must be a non-empty bounded string")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or ".." in parsed.parts or parsed.as_posix() != path:
        raise ContextError("path must be canonical and repository-relative")
    if not path.startswith(prefixes):
        raise ContextError("path is outside the admitted context roots")
    return path


def _chunk(root: Path, commit: str, path: str, start_line: int, max_lines: int) -> dict[str, Any]:
    if type(start_line) is not int or start_line < 1:
        raise ContextError("start_line must be a positive integer")
    if type(max_lines) is not int or not 1 <= max_lines <= MAX_LINES:
        raise ContextError(f"max_lines must be between 1 and {MAX_LINES}")
    raw = _bytes_at(root, commit, path)
    lines = raw.decode("utf-8").splitlines()
    selected = lines[start_line - 1:start_line - 1 + max_lines]
    return {
        "schema": "tgw-context-source-chunk/v1", "commit": commit, "path": path,
        "sha256": _sha(raw), "bytes": len(raw), "total_lines": len(lines),
        "start_line": start_line,
        "end_line": start_line + len(selected) - 1 if selected else start_line - 1,
        "content": "\n".join(selected),
    }


@lru_cache(maxsize=4)
def _code_snapshot(root_text: str, commit: str) -> dict[str, Any]:
    return build_snapshot(Path(root_text), commit)


def context_status() -> dict[str, Any]:
    binding = _bindings()
    plan_root = binding["plan_repository"]
    plan_commit = binding["plan_commit"]
    assert isinstance(plan_root, Path) and isinstance(plan_commit, str)
    source_root = binding["source_root"]
    source_commit = binding["source_commit"]
    assert isinstance(source_root, Path) and isinstance(source_commit, str)
    graph = _code_snapshot(str(source_root), source_commit)
    fleet = binding["fleet_convergence"]
    fleet_transaction = fleet["transaction"]
    startup_actor = binding["startup_binding"]["actor"]
    startup_verification = next(
        (
            item
            for item in fleet_transaction["actor_verifications"]
            if item.get("actor") == startup_actor
        ),
        None,
    )
    startup_instruction_hash = (
        str(startup_verification.get("instruction_entry_point_sha256", ""))
        if isinstance(startup_verification, Mapping) else ""
    )
    fleet_state = str(fleet["generation_status"])
    pending_count = sum(
        len(item["pending_reasons"])
        for item in fleet_transaction["obligations"]
    ) + len(fleet_transaction["global_pending"])
    generation_line = (
        f"TGW Context generation: client=CURRENT fleet={fleet_state} "
        f"aggregate={fleet_state} "
        f"gen={str(fleet_transaction['target_generation']).removeprefix('sha256:')[:12]} "
        f"approved={str(fleet_transaction['target_revisions']['approved_plan'])[:12]} "
        f"evidence={str(fleet_transaction['target_revisions']['evidence_plan'])[:12]} "
        f"source={str(fleet_transaction['target_revisions']['source_commit'])[:12]} "
        f"instructions={startup_instruction_hash.removeprefix('sha256:')[:12] or 'pending'} "
        f"tx={fleet_transaction['transaction_id']} pending={pending_count}"
    )
    identities = {}
    for path in (
        "plan/SPEC-plan-capability-graph-v2.md",
        "plan/TGW-Master-Plan.md",
        "plan/execution/GOVERNED-EXECUTION-PLATFORM-v1.yaml",
    ):
        raw = _bytes_at(plan_root, plan_commit, path)
        identities[path] = {"sha256": _sha(raw), "bytes": len(raw)}
    result = {
        "schema": SCHEMA, "ok": True, "host_role": "tgw-lib-authoritative-context",
        "plan": {
            "repository": str(plan_root), "approved_materialization": str(binding["plan_root"]),
            "approved_commit": plan_commit, "approved_tree": binding["plan_tree"],
            "approved_solution_hash": binding["plan_solution_hash"],
            "evidence_head": binding["plan_repository_head"],
            "evidence_tree": binding["plan_repository_tree"],
            "evidence_descends_from_approved": True,
            "sources": identities,
        },
        "source": {
            "repository": str(source_root), "commit": source_commit, "tree": binding["source_tree"],
            "working_tree_clean": binding["source_worktree_clean"],
            "status_sha256": binding["source_status_sha256"],
        },
        "code_graph": {key: graph[key] for key in ("commit", "tree", "freshness_hash", "capabilities")},
        "environment": {
            "catalog": binding["environment_catalog"],
            "catalog_path": str(binding["environment_catalog_path"]),
            "catalog_resolved_path": str(binding["environment_catalog_resolved_path"]),
            "catalog_hash": binding["environment_catalog_hash"],
        },
        "runtime": binding["runtime_identity"],
        "startup": {
            "actor": startup_actor,
            "generation": binding["startup_binding"]["expected_generation"],
            "binding_path": os.environ["TGW_CONTEXT_STARTUP_BINDING"],
            "instruction_entry_point_path": binding[
                "instruction_entry_point"
            ]["path"],
            "instruction_entry_point_sha256": binding[
                "instruction_entry_point"
            ]["sha256"],
            "instruction_entry_point_state": binding[
                "instruction_entry_point"
            ]["state"],
        },
        "fleet_convergence": fleet,
        "generation_status": {
            "state": fleet_state,
            "client_state": "CURRENT",
            "fleet_state": fleet_state,
            "line": generation_line,
        },
        "scope_semantics": dict(SCOPE_SEMANTICS),
    }
    result["context_sha256"] = _sha(_canonical(result))
    return result


def confirm_rebind(
    transaction_id: str,
    direction: str,
    obligation_id: str,
) -> dict[str, Any]:
    """Relay this exact MCP process status without exposing provider secrets."""

    status = context_status()
    fleet = status["fleet_convergence"]
    transaction = fleet.get("transaction")
    actor = status.get("startup", {}).get("actor")
    obligations = (
        transaction.get("obligations", [])
        if isinstance(transaction, Mapping) else []
    )
    obligation = next(
        (
            item for item in obligations
            if isinstance(item, Mapping)
            and item.get("obligation_id") == obligation_id
        ),
        None,
    )
    if (
        not isinstance(transaction_id, str)
        or not isinstance(obligation_id, str)
        or direction not in {"successor", "rollback"}
        or not isinstance(transaction, Mapping)
        or transaction.get("transaction_id") != transaction_id
        or transaction.get("direction") != direction
        or not isinstance(obligation, Mapping)
        or obligation.get("actor") != actor
        or fleet.get("state") != "ACTIVE"
    ):
        raise ContextError(
            "Context rebind confirmation must name this active actor obligation"
        )
    def envelope(current_status: Mapping[str, Any], current_obligation: str) -> dict[str, Any]:
        return {
            "schema": "tgw-context-client-confirmation/v1",
            "transaction_id": transaction_id,
            "direction": direction,
            "obligation_id": current_obligation,
            "status": dict(current_status),
        }
    # The relay owns the provider credential and validates AF_UNIX SO_PEERCRED
    # against status.runtime.process.  No secret enters this actor process.
    try:
        from tgw.context_confirmation_relay import submit_confirmation

        expected_obligation_id = obligation_id
        response = submit_confirmation(envelope(status, obligation_id))
        if (
            isinstance(response, Mapping)
            and response.get("status") == "RETRY_REQUIRED"
            and response.get("transaction_id") == transaction_id
            and response.get("previous_obligation_id") == obligation_id
            and re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                str(response.get("obligation_id", "")),
            )
        ):
            retry_obligation = str(response["obligation_id"])
            expected_obligation_id = retry_obligation
            retry_status = context_status()
            retry_transaction = retry_status["fleet_convergence"].get("transaction")
            retry_obligations = (
                retry_transaction.get("obligations", [])
                if isinstance(retry_transaction, Mapping) else []
            )
            if (
                not isinstance(retry_transaction, Mapping)
                or retry_transaction.get("transaction_id") != transaction_id
                or retry_transaction.get("direction") != direction
                or not any(
                    isinstance(item, Mapping)
                    and item.get("obligation_id") == retry_obligation
                    and item.get("actor") == actor
                    and item.get("checkpoint_disposition") == "LATE_ARRIVAL"
                    for item in retry_obligations
                )
            ):
                raise ContextError("root Context confirmation retry differs")
            response = submit_confirmation(
                envelope(retry_status, retry_obligation)
            )
    except (ImportError, OSError, TypeError, ValueError) as exc:
        raise ContextError("root Context confirmation relay is unavailable") from exc
    if (
        not isinstance(response, Mapping)
        or response.get("status") != "CONFIRMED"
        or response.get("transaction_id") != transaction_id
        or response.get("obligation_id") != expected_obligation_id
        or not re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(response.get("confirmation_hash", "")),
        )
    ):
        raise ContextError("root Context confirmation relay response differs")
    return dict(response)


def plan_graph(task: str, receiver: str = "codex", operation: str = "brief", limit: int = 12) -> dict[str, Any]:
    if not isinstance(task, str) or not task.strip() or len(task) > MAX_QUERY:
        raise ContextError("task must be a non-empty bounded string")
    binding = _bindings()
    result = live_plan_graph(
        binding["plan_root"], task, receiver=receiver, operation=operation, limit=limit,
        runtime_root=binding["runtime_root"],
        approved_plan_commit=binding["plan_commit"],
        approved_solution_hash=binding["plan_solution_hash"],
    )
    if result["plan_commit"] != binding["plan_commit"]:
        raise ContextError("Plan Graph did not bind the approved Plan commit")
    result["scope_semantics"] = dict(SCOPE_SEMANTICS)
    return result


def source_chunk(
    path: str,
    start_line: int = 1,
    max_lines: int = 200,
    authority: str = "approved-plan",
) -> dict[str, Any]:
    binding = _bindings()
    path = _safe_path(path, PLAN_PREFIXES)
    if authority == "approved-plan":
        commit, tree = binding["plan_commit"], binding["plan_tree"]
    elif authority == "current-plan":
        commit, tree = binding["plan_repository_head"], binding["plan_repository_tree"]
    else:
        raise ContextError("Plan source authority must be approved-plan or current-plan")
    result = _chunk(binding["plan_repository"], commit, path, start_line, max_lines)
    result.update(
        {
            "authority": authority,
            "tree": tree,
            "confined_path": path,
            "blob_sha256": result["sha256"],
            "content_sha256": _sha(result["content"].encode()),
        }
    )
    return result


def _runbook_sources(binding: Mapping[str, Any], authority: str) -> list[tuple[str, Path, str, str, str]]:
    sources = {
        "current-plan": (
            "canonical-plan-runbook", binding["plan_repository"],
            binding["plan_repository_head"], binding["plan_repository_tree"], PLAN_RUNBOOK_PREFIX,
        ),
        "approved-plan": (
            "approved-plan-runbook", binding["plan_repository"],
            binding["plan_commit"], binding["plan_tree"], PLAN_RUNBOOK_PREFIX,
        ),
        "application": (
            "committed-application-runbook", binding["source_root"],
            binding["source_commit"], binding["source_tree"], RUNBOOK_PREFIX,
        ),
    }
    if authority == "all":
        return [sources["current-plan"], sources["application"]]
    if authority not in sources:
        raise ContextError("runbook authority must be all, current-plan, approved-plan, or application")
    return [sources[authority]]


def runbooks(
    query: str = "", path: str = "", start_line: int = 1, max_lines: int = 200,
    limit: int = 20, authority: str = "all",
) -> dict[str, Any]:
    binding = _bindings()
    if path:
        if authority == "all":
            authority = "current-plan" if path.startswith(PLAN_RUNBOOK_PREFIX) else "application"
        selected = _runbook_sources(binding, authority)
        source_authority, root, commit, tree, prefix = selected[0]
        result = _chunk(root, commit, _safe_path(path, (prefix,)), start_line, max_lines)
        result.update({"authority": source_authority, "tree": tree})
        return result
    if not isinstance(query, str) or len(query) > MAX_QUERY:
        raise ContextError("query must be a bounded string")
    if type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
        raise ContextError(f"limit must be between 1 and {MAX_RESULTS}")
    tokens = sorted(set(re.findall(r"[a-z0-9_-]{3,}", query.casefold())))
    matches = []
    revisions = []
    for source_authority, root, commit, tree, prefix in _runbook_sources(binding, authority):
        paths = _git(root, "ls-tree", "-r", "--name-only", commit, "--", prefix)
        assert isinstance(paths, str)
        revisions.append({"authority": source_authority, "commit": commit, "tree": tree, "prefix": prefix})
        for candidate in paths.splitlines():
            if not candidate.endswith(".md"):
                continue
            raw = _bytes_at(root, commit, candidate)
            haystack = f"{candidate}\n{raw.decode('utf-8')}".casefold()
            score = sum(haystack.count(token) for token in tokens)
            if query.casefold().strip() and score == 0:
                continue
            matches.append({
                "authority": source_authority, "commit": commit, "tree": tree,
                "path": candidate, "sha256": _sha(raw), "bytes": len(raw), "score": score,
            })
    matches.sort(key=lambda item: (-item["score"], item["authority"], item["path"]))
    return {"schema": "tgw-context-runbook-index/v2", "revisions": revisions, "query": query, "matches": matches[:limit]}


def onboarding_bundle(actor: str) -> dict[str, Any]:
    """Return every immutable input a seed launcher needs before actor enrollment."""
    if not isinstance(actor, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", actor):
        raise ContextError("actor must be a canonical account identity")
    binding = _bindings()
    catalog = binding["environment_catalog"]
    declaration = catalog.get("actors", {}).get(actor)
    if not isinstance(declaration, Mapping) or declaration.get("enabled") is not True:
        raise ContextError("actor is not enabled in the exact environment catalog")
    profiles = declaration.get("permitted_profiles")
    if not isinstance(profiles, list) or not profiles:
        raise ContextError("actor has no permitted catalog profile")
    missing_profiles = [
        name for name in profiles
        if not isinstance(name, str) or not isinstance(catalog["profiles"].get(name), Mapping)
    ]
    if missing_profiles:
        raise ContextError("actor references a missing catalog profile")
    plan_runbook = runbooks(path="reference/runbooks/actor-mcp-onboarding.md", authority="current-plan")
    manual_recovery = runbooks(path="reference/runbooks/manual-platform-recovery.md", authority="current-plan")
    policy_path = "agent-services/actor-bootstrap/bootstrap-policy-v1.json"
    policy_raw = _bytes_at(binding["source_root"], binding["source_commit"], policy_path)
    launcher_path = "scripts/tgw_actor_startup.py"
    launcher_raw = _bytes_at(binding["source_root"], binding["source_commit"], launcher_path)
    policy = json.loads(policy_raw)
    stable_launcher = Path("/opt/TGW/tgw-lib/bin/tgw-actor")
    required_context_environment = {
        "TGW_ACTOR_CONTEXT_ENDPOINT": "tgw-context",
        "TGW_ACTOR_CONTEXT_REGISTRATION": "stable-launcher-v1",
    }
    result = {
        "schema": "tgw-actor-onboarding-context-bundle/v1",
        "status": "SEED_REQUIRED",
        "actor": actor,
        "catalog": {
            "path": str(binding["environment_catalog_path"]),
            "resolved_path": str(binding["environment_catalog_resolved_path"]),
            "hash": binding["environment_catalog_hash"],
            "declaration": declaration,
            "profiles": {name: catalog["profiles"].get(name) for name in profiles},
        },
        "plan": {
            "approved_commit": binding["plan_commit"],
            "approved_solution_hash": binding["plan_solution_hash"],
            "evidence_head": binding["plan_repository_head"],
            "evidence_tree": binding["plan_repository_tree"],
            "evidence_descends_from_approved": True,
        },
        "source": {"commit": binding["source_commit"], "tree": binding["source_tree"]},
        "runbooks": {"onboarding": plan_runbook, "manual_recovery": manual_recovery},
        "bootstrap_policy": {
            "path": policy_path, "commit": binding["source_commit"],
            "sha256": _sha(policy_raw), "value": policy,
        },
        "launcher": {"path": launcher_path, "commit": binding["source_commit"], "sha256": _sha(launcher_raw)},
        "context_mcp_registration": {
            "transport": "stdio",
            "command": str(stable_launcher),
            "args": ["--context-mcp"],
            "environment": required_context_environment,
        },
        "required_context_environment": required_context_environment,
        "next_authority": "one unexpired actor-onboarding-seed/v1 issued by the orchestrator",
        "fallback": "FORBIDDEN",
    }
    result["bundle_sha256"] = _sha(_canonical(result))
    return result


def code_graph(operation: str = "status", query: str = "", limit: int = 20) -> dict[str, Any]:
    if not isinstance(query, str) or len(query) > MAX_QUERY:
        raise ContextError("query must be a bounded string")
    binding = _bindings()
    snapshot = _code_snapshot(str(binding["source_root"]), str(binding["source_commit"]))
    result = CodeGraphService(snapshot).query(operation, query, limit)
    result["binding"] = {key: snapshot[key] for key in ("commit", "tree", "freshness_hash")}
    return result


def context_bundle(task: str, receiver: str = "codex", limit: int = 12) -> dict[str, Any]:
    if not isinstance(task, str) or not task.strip() or len(task) > MAX_QUERY:
        raise ContextError("task must be a non-empty bounded string")
    result = {
        "schema": "tgw-context-task-bundle/v1", "task": task, "receiver": receiver,
        "status": context_status(), "plan_graph": plan_graph(task, receiver, limit=limit),
        "runbooks": runbooks(query=task, limit=min(limit, 20)), "code_graph": code_graph(),
        "instructions": [
            "Repair a stale Plan or MCP projection before changing code; never work around it.",
            "Retrieve cited Plan and canonical/application runbook chunks before changing code.",
            "Use CodeGraph queries against the bound source commit; do not infer from a worktree.",
            "Report Plan, PP, Todo, implementation, deployment, and live status separately.",
            "Never describe platform W11 completion as completion of the TGW Master Plan.",
        ],
    }
    result["bundle_sha256"] = _sha(_canonical(result))
    return result


class HTTPReviewContextBrokerClient:
    """Call the privileged broker from the separately operated context service."""

    def __init__(self, endpoint: str) -> None:
        parsed = urlsplit(endpoint) if isinstance(endpoint, str) else None
        if (
            parsed is None
            or parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "::1"}
            or parsed.port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ContextError("governed review context broker endpoint is invalid")
        credential = os.environ.get("TGW_CONTEXT_BROKER_REQUEST_CREDENTIAL", "")
        if not credential or any(character.isspace() for character in credential):
            raise ContextError("governed review context broker credential is unavailable")
        self.endpoint = endpoint.rstrip("/")
        self.authorization = "Bearer " + credential

    def execute(self, request_value: Mapping[str, Any]) -> dict[str, Any]:
        raw = _canonical(request_value)
        request = Request(
            self.endpoint + "/v1/review-context", data=raw, method="POST",
            headers={
                "Accept": "application/json", "Content-Type": "application/json",
                "Authorization": self.authorization,
            },
        )
        try:
            with urlopen(request, timeout=30) as response:  # nosec: protected configured endpoint
                body = response.read(MAX_REVIEW_BUNDLE_BYTES + 1)
        except (HTTPError, URLError, OSError) as exc:
            raise ContextError("governed review context broker failed") from exc
        if len(body) > MAX_REVIEW_BUNDLE_BYTES:
            raise ContextError("governed review context broker response exceeds its bound")
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContextError("governed review context broker response is invalid") from exc
        if not isinstance(value, dict):
            raise ContextError("governed review context broker response is invalid")
        return value


def _review_context_run(
    *, challenge: str, card_json: str, handoff_hash: str,
    resource_receipt_hash: str, skill_contract_hash: str,
    grant_json: str,
    broker_factory: Any = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch every review-card resource inside one authenticated service run."""

    _per_call_guard()
    if re.fullmatch(r"[0-9a-f]{64}", challenge) is None:
        raise ContextError("governed review challenge is invalid")
    if not isinstance(card_json, str) or not card_json or len(card_json.encode()) > MAX_TEXT_BYTES:
        raise ContextError("governed review card framing is invalid")
    if (
        not isinstance(grant_json, str)
        or not grant_json
        or len(grant_json.encode()) > MAX_TEXT_BYTES
    ):
        raise ContextError("governed review grant framing is invalid")
    try:
        card = json.loads(card_json)
        grant = json.loads(grant_json)
        receipt = card_resource_receipt(card)
    except (json.JSONDecodeError, ResourceVerificationError) as exc:
        raise ContextError("governed review card is invalid") from exc
    if (
        card.get("role") != "independent-review"
        or not isinstance(grant, Mapping)
        or set(grant) != {"schema", "request", "request_hash"}
        or grant.get("schema") != "tgw-governed-review-context-grant/v1"
        or not isinstance(grant.get("request"), Mapping)
        or grant.get("request_hash") != _sha(_canonical(grant.get("request")))
        or receipt["receipt_hash"] != resource_receipt_hash
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", handoff_hash)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", skill_contract_hash)
        or skill_contract_hash != os.environ.get("TGW_CONTEXT_REVIEW_SKILL_CONTRACT_HASH")
    ):
        raise ContextError("governed review context binding is invalid")
    try:
        expected_uid = int(os.environ.get("TGW_CONTEXT_REVIEW_UID", ""))
        expected_gid = int(os.environ.get("TGW_CONTEXT_REVIEW_GID", ""))
    except ValueError as exc:
        raise ContextError("governed review runtime identity is invalid") from exc
    service_id = os.environ.get("TGW_CONTEXT_RESOURCE_SERVICE_ID", "")
    client_id = os.environ.get("TGW_CONTEXT_RESOURCE_SERVICE_CLIENT_ID", "")
    try:
        broker_factory = broker_factory or HTTPReviewContextBrokerClient
        broker = broker_factory(os.environ.get("TGW_CONTEXT_REVIEW_BROKER_ENDPOINT", ""))
        expected_request = {
            "schema": "tgw-context-review-broker-request/v2",
            "client_id": client_id,
            "challenge": challenge,
            "skill_contract_hash": skill_contract_hash,
            "card_hash": card["card_hash"], "role": "independent-review",
            "execution_identity": (
                f"governed-review:{challenge}:uid={expected_uid}:gid={expected_gid}"
            ),
            "handoff_hash": handoff_hash,
            "resource_receipt_hash": resource_receipt_hash,
            "resource_service_catalog_ref": os.environ.get(
                "TGW_CONTEXT_RESOURCE_SERVICE_CATALOG_REF", "",
            ),
            "resource_service_catalog_hash": os.environ.get(
                "TGW_CONTEXT_RESOURCE_SERVICE_CATALOG_HASH", "",
            ),
            "resources": {
                name: card["bindings"][name] for name in sorted(CARD_RESOURCE_NAMES)
            },
            "issued_at": grant["request"].get("issued_at"),
            "not_before": grant["request"].get("not_before"),
            "expires_at": grant["request"].get("expires_at"),
        }
        if grant["request"] != expected_request:
            raise ResourceVerificationError("review context grant binding mismatch")
        service_bundle = broker.execute(expected_request)
        if (
            not isinstance(service_bundle, Mapping)
            or service_bundle.get("schema") != "tgw-context-review-resource-bundle/v1"
            or service_bundle.get("client_id") != client_id
            or service_bundle.get("challenge") != challenge
            or service_bundle.get("skill_contract_hash") != skill_contract_hash
        ):
            raise ResourceVerificationError("review context service bundle binding mismatch")
        unsigned_bundle = dict(service_bundle)
        claimed_bundle_hash = unsigned_bundle.pop("bundle_hash", None)
        if claimed_bundle_hash != _sha(_canonical(unsigned_bundle)):
            raise ResourceVerificationError("review context service bundle hash mismatch")
        attestation = service_bundle.get("retrieval_attestation")
        attestation = validate_harness_retrieval_attestation(
            attestation,
            expected={
                "service_id": service_id, "client_id": client_id,
                "card_hash": card["card_hash"], "role": "independent-review",
                "execution_identity": (
                    f"governed-review:{challenge}:uid={expected_uid}:gid={expected_gid}"
                ),
                "handoff_hash": handoff_hash,
                "resource_receipt_hash": resource_receipt_hash,
                "resources": {
                    name: card["bindings"][name] for name in sorted(CARD_RESOURCE_NAMES)
                },
            },
            attestation_key_id=os.environ.get("TGW_CONTEXT_ATTESTATION_KEY_ID"),
            attestation_public_key=os.environ.get("TGW_CONTEXT_ATTESTATION_PUBLIC_KEY"),
        )
        encoded_resources = service_bundle.get("resources")
        if not isinstance(encoded_resources, Mapping) or set(encoded_resources) != CARD_RESOURCE_NAMES:
            raise ResourceVerificationError("review context service resources are invalid")
        visible_resources = {}
        for name in sorted(CARD_RESOURCE_NAMES):
            encoded = encoded_resources[name]
            binding = card["bindings"][name]
            if (
                not isinstance(encoded, Mapping)
                or set(encoded) != {
                    "ref", "hash", "content_sha256", "content_base64",
                }
                or encoded.get("ref") != binding["ref"]
                or encoded.get("hash") != binding["hash"]
                or not isinstance(encoded.get("content_base64"), str)
            ):
                raise ResourceVerificationError("review context service resource binding mismatch")
            try:
                content = base64.b64decode(encoded["content_base64"], validate=True)
            except (TypeError, ValueError) as exc:
                raise ResourceVerificationError("review context service resource encoding is invalid") from exc
            if (
                content_hash(content) != encoded["content_sha256"]
                or content_hash(content) != binding["hash"]
            ):
                raise ResourceVerificationError("review context service resource content differs")
            visible_resources[name] = {
                "ref": binding["ref"], "hash": binding["hash"],
                "content_sha256": encoded["content_sha256"],
                "content": content.decode("utf-8", errors="replace"),
            }
    except (KeyError, ResourceVerificationError, ValueError) as exc:
        raise ContextError("governed review registered context retrieval failed") from exc
    receipt = {
        "schema": "tgw-context-review-run/v1", "status": "PASS",
        "context_run_id": attestation["run_id"], "challenge": challenge,
        "card_hash": card["card_hash"], "resource_receipt_hash": resource_receipt_hash,
        "skill_contract_hash": skill_contract_hash,
        "runtime_uid": expected_uid, "runtime_gid": expected_gid,
        "retrieval_attestation": attestation,
        "resource_bundle_hash": service_bundle["bundle_hash"],
    }
    return receipt, visible_resources

mcp = FastMCP(
    name="tgw-context",
    instructions=(
        "Authoritative TGW planning, coding, and root-provider fleet "
        "convergence context hosted on tgw-lib. Plan source reads name either "
        "the approved Plan or the current evidence-head authority. On every "
        "ordinary harness session start, call tgw_context_status once and "
        "present generation_status.line verbatim to the model/operator; this "
        "visibility note is informational and is not an operator gate. The "
        "only mutation is tgw_context_confirm_rebind: a credential-free, "
        "self-process and active-obligation-bound confirmation receipt."
    ),
)
governed_review_mcp = FastMCP(
    name="tgw-context-governed-review",
    instructions="Governed-review-only registered TGW context retrieval.",
)


def _json_call(function: Any, *args: Any, **kwargs: Any) -> str:
    try:
        _per_call_guard()
        return json.dumps(function(*args, **kwargs), ensure_ascii=False)
    except Exception as exc:  # MCP errors are a bounded, serializable response.
        return json.dumps({"ok": False, "error": str(exc), "error_type": type(exc).__name__})


def _per_call_guard() -> dict[str, Any]:
    """Revalidate root binding and retained source for every exported tool call."""

    return _bindings()


@mcp.tool()
def tgw_context_status() -> str:
    """Return exact Plan/source/runtime bindings and cold fleet convergence."""
    return _json_call(context_status)


@mcp.tool()
def tgw_context_confirm_rebind(
    transaction_id: str,
    direction: str,
    obligation_id: str,
) -> str:
    """Confirm only this process's active, projected live-client obligation."""

    return _json_call(
        confirm_rebind, transaction_id, direction, obligation_id
    )


def _tgw_context_bundle(
    task: str, receiver: str = "codex", limit: int = 12, challenge: str = "",
    card_json: str = "", handoff_hash: str = "", resource_receipt_hash: str = "",
    skill_contract_hash: str = "", grant_json: str = "", *, governed_only: bool = False,
) -> str:
    """Return context; a governed review must also open and complete its bound retrieval run."""

    def result() -> dict[str, Any]:
        supplied = (
            challenge, card_json, handoff_hash, resource_receipt_hash,
            skill_contract_hash, grant_json,
        )
        if not any(supplied) and not governed_only:
            return context_bundle(task, receiver, limit)
        if not all(supplied):
            raise ContextError("governed review context arguments must be complete")
        review_receipt, visible_resources = _review_context_run(
            challenge=challenge, card_json=card_json, handoff_hash=handoff_hash,
            resource_receipt_hash=resource_receipt_hash,
            skill_contract_hash=skill_contract_hash, grant_json=grant_json,
        )
        bundle = {
            "task": task, "receiver": receiver,
            "registered_resources": visible_resources,
            "registered_resource_retrieval": review_receipt,
        }
        bundle["bundle_sha256"] = _sha(_canonical(bundle))
        return bundle

    return _json_call(result)


@mcp.tool()
def tgw_context_bundle(
    task: str, receiver: str = "codex", limit: int = 12, challenge: str = "",
    card_json: str = "", handoff_hash: str = "", resource_receipt_hash: str = "",
    skill_contract_hash: str = "", grant_json: str = "",
) -> str:
    """Return ordinary context or complete an explicitly bound governed retrieval."""

    return _tgw_context_bundle(
        task, receiver, limit, challenge, card_json, handoff_hash,
        resource_receipt_hash, skill_contract_hash, grant_json,
    )


@governed_review_mcp.tool(name="tgw_context_bundle")
def governed_review_context_bundle(
    task: str, receiver: str = "codex", limit: int = 12, challenge: str = "",
    card_json: str = "", handoff_hash: str = "", resource_receipt_hash: str = "",
    skill_contract_hash: str = "", grant_json: str = "",
) -> str:
    """Complete one fully bound governed-review registered-resource retrieval."""

    return _tgw_context_bundle(
        task, receiver, limit, challenge, card_json, handoff_hash,
        resource_receipt_hash, skill_contract_hash, grant_json,
        governed_only=True,
    )


@mcp.tool()
def tgw_context_plan_graph(task: str, receiver: str = "codex", operation: str = "brief", limit: int = 12) -> str:
    """Query the Plan Graph derived from the exact approved Plan materialization."""
    return _json_call(plan_graph, task, receiver, operation, limit)


@mcp.tool()
def tgw_context_plan_source(
    path: str,
    start_line: int = 1,
    max_lines: int = 200,
    authority: str = "approved-plan",
) -> str:
    """Read bounded exact approved-Plan or current evidence-head source."""
    return _json_call(source_chunk, path, start_line, max_lines, authority)


@mcp.tool()
def tgw_context_runbooks(
    query: str = "", path: str = "", start_line: int = 1, max_lines: int = 200,
    limit: int = 20, authority: str = "all",
) -> str:
    """Search/read current canonical Plan, approved Plan, or application runbooks."""
    return _json_call(runbooks, query, path, start_line, max_lines, limit, authority)


@mcp.tool()
def tgw_context_onboarding(actor: str) -> str:
    """Return the exact Plan/catalog/source/runbook inputs required to enroll an actor."""
    return _json_call(onboarding_bundle, actor)


@mcp.tool()
def tgw_context_code_graph(operation: str = "status", query: str = "", limit: int = 20) -> str:
    """Query the CodeGraph snapshot bound to committed application source."""
    return _json_call(code_graph, operation, query, limit)


def _sse_binding(*, governed: bool = False) -> tuple[str, int]:
    host = os.environ.get("TGW_CONTEXT_MCP_HOST", "127.0.0.1").strip()
    if not host:
        raise ValueError("TGW_CONTEXT_MCP_HOST must not be empty")
    if governed and host not in {"127.0.0.1", "::1"}:
        raise ValueError("governed review MCP host must be loopback")
    try:
        port = int(os.environ.get("TGW_CONTEXT_MCP_PORT", "8766"))
    except ValueError as exc:
        raise ValueError("TGW_CONTEXT_MCP_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("TGW_CONTEXT_MCP_PORT must be between 1 and 65535")
    return host, port


def main() -> None:
    import sys

    governed = "--governed-review-sse" in sys.argv
    if "--sse" not in sys.argv and not governed:
        mcp.run(transport="stdio")
        return
    from mcp.server.transport_security import TransportSecuritySettings

    host, port = _sse_binding(governed=governed)
    server = governed_review_mcp if governed else mcp
    server.settings.host = host
    server.settings.port = port
    authority = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    server.settings.transport_security = TransportSecuritySettings(
        allowed_hosts=[authority],
        allowed_origins=[f"http://{authority}"],
    )
    server.run(transport="sse")


if __name__ == "__main__":
    main()
