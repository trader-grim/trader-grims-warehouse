"""Lifecycle projection for the existing provider-neutral governed review.

The coding queue does not mint review authority. It invokes the established
``tgw-governed-review`` implementation with one root-protected request and
projects the resulting diagnostic evidence into the ordinary-user lifecycle.
The privileged consumer independently verifies the protected, pinned receipt
bundle before it may materialize or select any candidate bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Mapping

from tgw.development.coding_review_protection import PROTECTED_REVIEW_CONFIG
from tgw.development.partial_resume import source_fingerprint
from tgw.governed_review_adapter import execute_request, validate_execution
from tgw.review_contract import ReviewRunnerError

PROTECTED_REVIEW_CONFIG_SCHEMA = "tgw-local-coding-protected-review/v2"
DEFAULT_PROTECTED_REVIEW_CONFIG = PROTECTED_REVIEW_CONFIG

_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ROOT = re.compile(r"coding:[0-9a-f]{64}\Z")
_VERDICT = "PASS_NON_ADMITTING"


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def load_protected_review_config(
    path: Path,
    *,
    candidate_repository: Path,
    trusted_uid: int = 0,
) -> dict[str, Path]:
    """Load fixed review wiring only from a protected external file."""

    from tgw.context_generation_status import (
        ContextGenerationStatusError,
        _protected_directory,
        _protected_json,
    )

    try:
        _protected_directory(
            path.parent, "coding protected-review config parent", trusted_uid
        )
        value = _protected_json(
            path, "coding protected-review config", trusted_uid
        )
    except ContextGenerationStatusError as exc:
        raise ReviewRunnerError(
            "protected governed-review configuration is unavailable"
        ) from exc
    required = {
        "schema",
        "request_root",
        "snapshot_root",
        "request_profile_config",
        "candidate_evidence_descriptor_config",
        "execution_evidence_sink_config",
        "execution_evidence_pin_source",
        "resource_registry_root",
        "broker_grant_root",
        "context_credential_config",
        "evidence_credential_config",
        "resource_credential_config",
        "broker_credential_config",
    }
    if (
        set(value) != required
        or value.get("schema") != PROTECTED_REVIEW_CONFIG_SCHEMA
    ):
        raise ReviewRunnerError("protected governed-review configuration is invalid")
    result: dict[str, Path] = {}
    credential_names = {
        "context_credential_config",
        "evidence_credential_config",
        "resource_credential_config",
        "broker_credential_config",
    }
    try:
        candidate_root = candidate_repository.resolve(strict=True)
        for name in required - {"schema"}:
            raw = value.get(name)
            if not isinstance(raw, str) or not raw:
                raise ReviewRunnerError("protected governed-review path is invalid")
            configured = Path(raw)
            if not configured.is_absolute():
                raise ReviewRunnerError(
                    "protected governed-review path must be absolute"
                )
            if name in credential_names:
                # These root-only source files are copied into a private
                # service credential directory by systemd.  The coding worker
                # must retain their protected binding without gaining search
                # access to the source credential directory.
                if (
                    candidate_root == configured
                    or candidate_root in configured.parents
                    or configured in candidate_root.parents
                ):
                    raise ReviewRunnerError(
                        "protected governed-review path overlaps the candidate repository"
                    )
                result[name] = configured
                continue
            resolved = configured.resolve(strict=True)
            if (
                resolved == candidate_root
                or candidate_root in resolved.parents
                or resolved in candidate_root.parents
            ):
                raise ReviewRunnerError(
                    "protected governed-review path overlaps the candidate repository"
                )
            result[name] = resolved
    except OSError as exc:
        raise ReviewRunnerError(
            "protected governed-review path is unavailable"
        ) from exc
    for name in (
        "request_root", "snapshot_root", "resource_registry_root",
        "broker_grant_root",
    ):
        if not result[name].is_dir():
            raise ReviewRunnerError(
                f"protected governed-review {name} is invalid"
            )
    return result


def _validated_bindings(
    payload: Mapping[str, Any], worktree: Path
) -> tuple[
    Mapping[str, Any],
    Mapping[str, Any],
    Mapping[str, Any],
    Mapping[str, Any],
]:
    lifecycle = payload.get("coding_lifecycle")
    candidate = payload.get("coding_candidate")
    plan = payload.get("plan_binding")
    task = payload.get("task_spec")
    if not all(
        isinstance(value, Mapping)
        for value in (lifecycle, candidate, plan, task)
    ):
        raise ReviewRunnerError("independent review bindings are incomplete")
    if (
        set(task) != {"schema", "todo_id", "agent", "body"}
        or task.get("schema") != "coding-task/v1"
        or task.get("todo_id") != payload.get("todo_id")
        or not isinstance(task.get("body"), str)
        or not task["body"].strip()
    ):
        raise ReviewRunnerError("independent review task binding is invalid")
    commit = str(candidate.get("commit", ""))
    tree = str(candidate.get("tree", ""))
    fingerprint = source_fingerprint(worktree)
    if (
        _ROOT.fullmatch(str(lifecycle.get("root_id", ""))) is None
        or _SHA256.fullmatch(str(lifecycle.get("binding_hash", ""))) is None
        or _SHA256.fullmatch(str(lifecycle.get("job_binding_hash", ""))) is None
        or _SHA256.fullmatch(
            str(candidate.get("candidate_binding_hash", ""))
        )
        is None
        or _COMMIT.fullmatch(commit) is None
        or _COMMIT.fullmatch(tree) is None
        or _COMMIT.fullmatch(str(plan.get("plan_commit", ""))) is None
        or candidate.get("root_id") != lifecycle.get("root_id")
        or candidate.get("job_binding_hash") != lifecycle.get("job_binding_hash")
        or fingerprint["changed_paths"]
        or fingerprint["head"] != commit
        or fingerprint["tree"] != tree
    ):
        raise ReviewRunnerError("independent review candidate binding is stale")
    return lifecycle, candidate, plan, task


def run_local_review(
    payload: Mapping[str, Any],
    worktree: Path,
    *,
    governed_runner: Callable[[Path], Mapping[str, Any]] = execute_request,
    execution_validator: Callable[[Mapping[str, Any]], Mapping[str, Any]] = (
        validate_execution
    ),
    protected_config: Path = DEFAULT_PROTECTED_REVIEW_CONFIG,
    config_loader: Callable[..., Mapping[str, Path]] = (
        load_protected_review_config
    ),
) -> dict[str, Any]:
    """Run the established governed reviewer and project its exact result."""

    lifecycle, candidate, plan, task = _validated_bindings(payload, worktree)
    job_id = payload.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise ReviewRunnerError("independent review job identity is absent")
    configuration = config_loader(
        protected_config,
        candidate_repository=worktree,
        trusted_uid=0,
    )
    request_path = configuration["request_root"] / (
        f"{candidate['commit']}.request.json"
    )
    try:
        finalized = governed_runner(request_path)
    except (OSError, ValueError, ReviewRunnerError) as exc:
        raise ReviewRunnerError(
            f"governed independent review failed: {exc}"
        ) from exc
    if not isinstance(finalized, Mapping):
        raise ReviewRunnerError("governed independent review result is absent")
    execution = finalized.get("execution")
    role_receipt = finalized.get("governed_review_receipt")
    governed_bundle = finalized.get("governed_execution_bundle")
    result = finalized.get("result")
    validation = finalized.get("validation")
    if not all(
        isinstance(item, Mapping)
        for item in (execution, role_receipt, governed_bundle, result, validation)
    ):
        raise ReviewRunnerError(
            "governed independent review evidence is incomplete"
        )
    normalized = dict(execution_validator(execution))
    review = normalized.get("review")
    source_protection = normalized.get("source_protection")
    normalized_source = normalized.get("source")
    if (
        not isinstance(normalized_source, Mapping)
        or normalized_source.get("commit") != candidate["commit"]
        or normalized_source.get("tree") != candidate["tree"]
        or normalized.get("plan_commit") != plan.get("plan_commit")
        or not isinstance(review, Mapping)
        or not isinstance(source_protection, Mapping)
        or source_protection.get("held_through_use") is not True
        or role_receipt.get("role") != "independent-review"
        or role_receipt.get("status") != "PASS"
        or role_receipt.get("established_conditions") != ["reviewed"]
        or governed_bundle.get("source_commit") != candidate["commit"]
        or governed_bundle.get("source_tree") != candidate["tree"]
        or governed_bundle.get("plan_commit") != plan.get("plan_commit")
        or governed_bundle.get("role") != "independent-review"
        or validation.get("status") != "PASS"
        or result.get("overall") != "PASS"
        or review.get("verdict") != "PASS"
        or review.get("findings") != []
    ):
        raise ReviewRunnerError(
            "governed independent review did not pass exact bindings"
        )
    candidate_pointer = governed_bundle.get("candidate_receipt")
    if (
        not isinstance(candidate_pointer, Mapping)
        or set(candidate_pointer) != {"ref", "content_sha256"}
        or _SHA256.fullmatch(str(candidate_pointer.get("content_sha256", "")))
        is None
    ):
        raise ReviewRunnerError(
            "governed independent review candidate receipt is absent"
        )
    for label, value in (
        ("execution", normalized.get("execution_hash")),
        ("role receipt", role_receipt.get("receipt_hash")),
        ("governed bundle", governed_bundle.get("bundle_hash")),
        ("result", result.get("result_hash")),
    ):
        if _SHA256.fullmatch(str(value or "")) is None:
            raise ReviewRunnerError(f"governed independent review {label} hash is invalid")
    protected = {
        "schema": "tgw-local-governed-review-projection/v1",
        "provider_neutral": True,
        "privileged_authority": False,
        "candidate_commit": candidate["commit"],
        "candidate_tree": candidate["tree"],
        "plan_commit": plan["plan_commit"],
        "execution_hash": normalized["execution_hash"],
        "role_receipt_hash": role_receipt["receipt_hash"],
        "candidate_receipt_hash": candidate_pointer["content_sha256"],
        "governed_bundle_hash": governed_bundle["bundle_hash"],
        "result_hash": result["result_hash"],
        "source_protection": dict(source_protection),
        "source_protection_hash": _hash(source_protection),
    }
    artifact = {
        "kind": "tgw_governed_review_projection",
        "diagnostic_verdict": _VERDICT,
        "root_id": lifecycle["root_id"],
        "binding_hash": lifecycle["binding_hash"],
        "job_binding_hash": lifecycle["job_binding_hash"],
        "job_id": job_id,
        "card_idempotency_key": lifecycle["card_idempotency_key"],
        "candidate_binding_hash": candidate["candidate_binding_hash"],
        "task_spec_hash": _hash(task),
        "protected_review": protected,
        "projection_hash": _hash(protected),
    }
    return {
        "outcome": "satisfied",
        "established_conditions": ["reviewed"],
        "artifacts": [artifact],
    }


def validate_review_artifact(
    value: object,
    *,
    payload: Mapping[str, Any],
    worktree: Path,
    expected_job_id: str,
) -> dict[str, Any]:
    """Validate the non-authoritative lifecycle projection of governed review."""

    if not isinstance(value, Mapping):
        raise ReviewRunnerError("review launcher result is not an object")
    if value.get("outcome") != "satisfied" or value.get(
        "established_conditions"
    ) != ["reviewed"]:
        raise ReviewRunnerError("review success conditions are absent")
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 1:
        raise ReviewRunnerError("review success requires one report artifact")
    artifact = artifacts[0]
    lifecycle, candidate, plan, task = _validated_bindings(payload, worktree)
    if not isinstance(artifact, Mapping):
        raise ReviewRunnerError("review projection binding is incomplete")
    protected = artifact.get("protected_review")
    if not isinstance(protected, Mapping):
        raise ReviewRunnerError("review protected-evidence projection is absent")
    required_protected = {
        "schema",
        "provider_neutral",
        "privileged_authority",
        "candidate_commit",
        "candidate_tree",
        "plan_commit",
        "execution_hash",
        "role_receipt_hash",
        "candidate_receipt_hash",
        "governed_bundle_hash",
        "result_hash",
        "source_protection",
        "source_protection_hash",
    }
    source_protection = protected.get("source_protection")
    if (
        set(protected) != required_protected
        or protected.get("schema")
        != "tgw-local-governed-review-projection/v1"
        or protected.get("provider_neutral") is not True
        or protected.get("privileged_authority") is not False
        or protected.get("candidate_commit") != candidate.get("commit")
        or protected.get("candidate_tree") != candidate.get("tree")
        or protected.get("plan_commit") != plan.get("plan_commit")
        or any(
            _SHA256.fullmatch(str(protected.get(field, ""))) is None
            for field in (
                "execution_hash",
                "role_receipt_hash",
                "candidate_receipt_hash",
                "governed_bundle_hash",
                "result_hash",
                "source_protection_hash",
            )
        )
        or not isinstance(source_protection, Mapping)
        or source_protection.get("held_through_use") is not True
        or protected.get("source_protection_hash") != _hash(source_protection)
        or artifact.get("projection_hash") != _hash(protected)
        or artifact.get("kind") != "tgw_governed_review_projection"
        or artifact.get("diagnostic_verdict") != _VERDICT
        or artifact.get("root_id") != lifecycle.get("root_id")
        or artifact.get("binding_hash") != lifecycle.get("binding_hash")
        or artifact.get("job_binding_hash") != lifecycle.get("job_binding_hash")
        or artifact.get("job_id") != expected_job_id
        or artifact.get("card_idempotency_key")
        != lifecycle.get("card_idempotency_key")
        or artifact.get("candidate_binding_hash")
        != candidate.get("candidate_binding_hash")
        or artifact.get("task_spec_hash") != _hash(task)
    ):
        raise ReviewRunnerError(
            "review projection is empty, stale, or contradictory"
        )
    return dict(artifact)


def main() -> int:
    try:
        payload = json.loads(os.environ["TGW_CODING_JOB"])
        if not isinstance(payload, dict):
            raise ReviewRunnerError("independent review job must be an object")
        result = run_local_review(payload, Path.cwd())
    except (KeyError, OSError, ValueError, ReviewRunnerError) as exc:
        result = {
            "outcome": "failed",
            "established_conditions": [],
            "artifacts": [
                {"kind": "independent_review_failure", "detail": str(exc)}
            ],
        }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
