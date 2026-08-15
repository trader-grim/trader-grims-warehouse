"""Dependency-free local coding-treatment execution primitives.

This module intentionally imports neither the queue worker nor service-side
evaluation.  A caller supplies an already-authorized envelope.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from tgw.errors import HardFailure, TreatmentFailure
from tgw.workflow.contracts import OUTCOME_CONFLICT, OUTCOME_FAILED, OUTCOME_PARTIAL, OUTCOME_SATISFIED
from tgw.workflow.treatments import CODING_TREATMENTS

DEFAULT_WORKTREE_ROOT = Path("/opt/TGW/var/worktrees")
DEFAULT_REPOSITORY_ROOT = Path("/opt/TGW/tgw-lib/src/trader-grims-warehouse")
_RECEIPT_FILES = {
    "codex-implement": "implementation-receipt.json",
    "claude-review": "review-receipt.json",
    "controller-verify": "controller-harness-receipt.json",
    "hermes-stitch": "stitch-receipt.json",
}
_VALID_OUTCOMES = frozenset({OUTCOME_SATISFIED, OUTCOME_FAILED, OUTCOME_PARTIAL, OUTCOME_CONFLICT})


def execution_envelope(document: dict[str, Any]) -> dict[str, Any]:
    """Validate the bounded service authorization without evaluating it."""
    execution = document.get("execution")
    if not isinstance(execution, dict):
        raise HardFailure("canonical coding execution envelope is unavailable")
    required = ("todo_id", "treatment_id", "treatment_version", "graph_id", "object_generation", "evaluator_version", "evidence_set_hash", "treatment_registry_hash", "task_spec_hash")
    if any(not isinstance(execution.get(field), str) or not execution[field] for field in required if field != "todo_id"):
        raise HardFailure("canonical coding execution envelope is incomplete")
    if execution.get("todo_id") != document.get("todo_id") or execution.get("object_generation") != document.get("object_generation"):
        raise HardFailure("canonical coding execution envelope does not match request")
    task_spec = execution.get("task_spec")
    if (
        not isinstance(task_spec, dict)
        or task_spec.get("schema") != "coding-task/v1"
        or task_spec.get("todo_id") != execution.get("todo_id")
        or not isinstance(task_spec.get("agent"), str)
        or not task_spec["agent"]
        or not isinstance(task_spec.get("body"), str)
        or not task_spec["body"]
    ):
        raise HardFailure("canonical coding execution task specification is invalid")
    expected_task_hash = hashlib.sha256(
        json.dumps(task_spec, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if execution.get("task_spec_hash") != expected_task_hash:
        raise HardFailure("canonical coding execution task specification hash is invalid")
    if not any(item.identity == execution["treatment_id"] and item.version == execution["treatment_version"] for item in CODING_TREATMENTS):
        raise HardFailure("canonical coding execution envelope has no registered treatment")
    return dict(execution)


def _git_identity(path: Path) -> tuple[Path | None, Path | None]:
    try:
        probe = subprocess.run(["git", "rev-parse", "--show-toplevel", "--git-common-dir"], cwd=path, check=False, text=True, capture_output=True)
    except OSError:
        return None, None
    if probe.returncode or len(probe.stdout.splitlines()) != 2:
        return None, None
    top_text, common_text = probe.stdout.splitlines()
    common = Path(common_text)
    return Path(top_text).resolve(), (common if common.is_absolute() else path / common).resolve()


def validated_coding_worktree(worktree_value: str | Path, object_id: str | Path, coding_config: dict[str, Any] | None = None) -> Path:
    config = coding_config or {}
    if not isinstance(config, dict) or not isinstance(worktree_value, (str, Path)) or not str(worktree_value) or not isinstance(object_id, (str, Path)) or not str(object_id):
        raise HardFailure("coding job has no worktree or object_id")
    root = Path(config.get("worktree_root", DEFAULT_WORKTREE_ROOT)).resolve()
    worktree = Path(worktree_value).resolve()
    try:
        worktree.relative_to(root)
    except ValueError as exc:
        raise HardFailure(f"coding worktree escapes configured root: {worktree}") from exc
    if worktree.parent != root or worktree != Path(object_id).resolve() or not worktree.is_dir():
        raise HardFailure("coding worktree is invalid")
    repository = Path(config.get("repository_root", DEFAULT_REPOSITORY_ROOT)).resolve()
    worktree_top, worktree_common = _git_identity(worktree)
    repository_top, repository_common = _git_identity(repository)
    if worktree_top != worktree or repository_top != repository or not worktree_common or worktree_common != repository_common:
        raise HardFailure("coding worktree does not belong to configured TGW repository")
    return worktree


def receipt_path_for_treatment(worktree: Path, treatment_id: str) -> Path:
    try:
        return worktree / _RECEIPT_FILES[treatment_id]
    except KeyError as exc:
        raise HardFailure(f"unsupported coding treatment: {treatment_id}") from exc


def _write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def execute_authorized_treatment(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Run one registered local treatment, with no QueueWorker construction."""
    treatment_id = payload.get("treatment_id")
    treatment = next((item for item in CODING_TREATMENTS if item.identity == treatment_id and item.version == payload.get("treatment_version")), None)
    if treatment is None or not isinstance(payload.get("graph_id"), str) or not isinstance(payload.get("object_generation"), str):
        raise HardFailure("coding execution envelope has no registered treatment")
    coding = config.get("coding", {})
    if not isinstance(coding, dict):
        raise HardFailure("coding configuration must be an object")
    worktree = validated_coding_worktree(payload.get("worktree"), payload.get("object_id"), coding)
    command = coding.get("commands", {}).get(treatment_id) if isinstance(coding.get("commands"), dict) else None
    if not isinstance(command, list) or not command or not all(isinstance(arg, str) and arg for arg in command):
        raise HardFailure(f"coding.commands.{treatment_id} must be a non-empty argv list")
    if Path(command[0]).name.lower() in {"ssh", "sudo", "sh", "bash"}:
        raise HardFailure("coding runner must use the configured local argv protocol")
    allowed = coding.get("allowed_runners")
    if allowed is not None and (not isinstance(allowed, list) or command[0] not in allowed):
        raise HardFailure("coding command is not an allowed local runner")
    runner_env = dict(os.environ)
    # Bootstrap the allowlisted runner from the trusted worker environment.
    # The runner passes this separately to child checks so an attempt cannot
    # shadow the runner implementation itself with worktree Python modules.
    runner_env["TGW_CODING_WORKTREE_SRC"] = str(worktree / "src")
    runner_env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        completed = subprocess.run(
            command, cwd=worktree, check=False, text=True, capture_output=True,
            timeout=int(coding.get("timeout_s", 1800)),
            env={**runner_env, "TGW_CODING_JOB": json.dumps(payload)},
        )
        if completed.returncode:
            raise RuntimeError(f"coding launcher exited {completed.returncode}: {completed.stderr[-500:]}")
        outcome_data = json.loads(completed.stdout)
        if not isinstance(outcome_data, dict):
            raise HardFailure("coding launcher outcome must be a JSON object")
        outcome, established, artifacts = outcome_data.get("outcome"), outcome_data.get("established_conditions", []), outcome_data.get("artifacts", [])
        if (
            outcome not in _VALID_OUTCOMES
            or not isinstance(established, list)
            or not all(isinstance(item, str) for item in established)
            or not isinstance(artifacts, list)
            or not set(established).issubset(treatment.may_establish)
            or (outcome != OUTCOME_SATISFIED and established)
            or (outcome == OUTCOME_SATISFIED and not established)
        ):
            raise HardFailure("coding launcher returned invalid or unauthorized outcome")
    except (HardFailure, RuntimeError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        receipt = {
            "status": "FAIL",
            "treatment_id": treatment_id,
            "treatment_version": treatment.version,
            "graph_id": payload["graph_id"],
            "object_id": str(worktree),
            "object_generation": payload["object_generation"],
            "outcome": OUTCOME_FAILED,
            "established_conditions": [],
            "artifacts": [{"kind": "mechanical_failure", "detail": str(exc)}],
            "receipt_schema_id": treatment.receipt_schema_id,
        }
        _write_receipt(receipt_path_for_treatment(worktree, treatment_id), receipt)
        raise TreatmentFailure(f"coding treatment mechanical failure: {exc}", receipt) from exc
    receipt = {
        "status": "PASS" if outcome == OUTCOME_SATISFIED else "FAIL",
        "treatment_id": treatment_id,
        "treatment_version": treatment.version,
        "graph_id": payload["graph_id"],
        "object_id": str(worktree),
        "object_generation": payload["object_generation"],
        "outcome": outcome,
        "established_conditions": established,
        "artifacts": artifacts,
        "receipt_schema_id": treatment.receipt_schema_id,
    }
    _write_receipt(receipt_path_for_treatment(worktree, treatment_id), receipt)
    if outcome != OUTCOME_SATISFIED:
        raise TreatmentFailure(f"coding treatment reported {outcome}", receipt)
    return receipt
