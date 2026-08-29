"""Fixed local independent-review runner and semantic receipt validator.

The review worker is intentionally not a command broker.  It executes one
source-defined diagnostic sequence against the exact candidate and emits the
provider-neutral ``tgw-code-review/v1`` report as canonical bytes.  The queue
worker and lifecycle supervisor both call :func:`validate_review_artifact` so
ordinary queue success cannot substitute for independent review evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tgw.codex_review_backend import CodexReviewBackendError
from tgw.codex_review_backend import run as run_codex_review
from tgw.protected_git import protected_git_command, protected_git_environment
from tgw.review_contract import ReviewRunnerError, validate_review_report

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


def _bytes_hash(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _git(worktree: Path, *args: str) -> str:
    completed = subprocess.run(
        protected_git_command(worktree, *args),
        cwd=worktree,
        check=False,
        text=True,
        capture_output=True,
        env=dict(protected_git_environment()),
    )
    if completed.returncode:
        raise ReviewRunnerError(
            f"independent review Git probe failed: {completed.stderr[-300:]}"
        )
    return completed.stdout.strip()


def _candidate_snapshot_hash(commit: str, tree: str) -> str:
    return _hash(
        {
            "schema": "tgw-local-review-snapshot/v1",
            "commit": commit,
            "tree": tree,
        }
    )


def _run_check(
    name: str,
    command: Sequence[str],
    worktree: Path,
    *,
    env: Mapping[str, str],
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    completed = runner(
        list(command),
        cwd=worktree,
        check=False,
        text=True,
        capture_output=True,
        env=dict(env),
    )
    output = (completed.stdout + "\n" + completed.stderr).encode()
    return {
        "name": name,
        "returncode": completed.returncode,
        "output_sha256": _bytes_hash(output),
    }


def run_local_review(
    payload: Mapping[str, Any],
    worktree: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    semantic_backend: Callable[[Mapping[str, Any], Path], Mapping[str, Any]] = (
        run_codex_review
    ),
) -> dict[str, Any]:
    """Execute fixed checks plus one ephemeral semantic Codex review."""

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
    task_hash = _hash(task)
    commit = str(candidate.get("commit", ""))
    tree = str(candidate.get("tree", ""))
    if (
        _ROOT.fullmatch(str(lifecycle.get("root_id", ""))) is None
        or _SHA256.fullmatch(str(lifecycle.get("binding_hash", ""))) is None
        or _SHA256.fullmatch(str(lifecycle.get("job_binding_hash", ""))) is None
        or _SHA256.fullmatch(str(candidate.get("candidate_binding_hash", "")))
        is None
        or _COMMIT.fullmatch(commit) is None
        or _COMMIT.fullmatch(tree) is None
        or candidate.get("root_id") != lifecycle.get("root_id")
        or candidate.get("job_binding_hash") != lifecycle.get("job_binding_hash")
        or _git(worktree, "rev-parse", "HEAD") != commit
        or _git(worktree, "rev-parse", "HEAD^{tree}") != tree
    ):
        raise ReviewRunnerError("independent review candidate binding is stale")
    job_id = payload.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise ReviewRunnerError("independent review job identity is absent")

    with tempfile.TemporaryDirectory(prefix="tgw-local-review-cache-") as cache:
        check_env = {
            **protected_git_environment(),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_ADDOPTS": "-p no:cacheprovider",
            "RUFF_CACHE_DIR": str(Path(cache) / "ruff"),
            "TMPDIR": str(Path(cache) / "tmp"),
        }
        Path(check_env["TMPDIR"]).mkdir()
        checks = [
            _run_check(
                "git-diff-check",
                tuple(
                    protected_git_command(
                        worktree,
                        "diff",
                        "--check",
                        f"{plan['source_commit']}..{commit}",
                    )
                ),
                worktree,
                env=check_env,
                runner=runner,
            ),
        ]
    if (
        _git(worktree, "status", "--porcelain=v1")
        or _git(worktree, "rev-parse", "HEAD") != commit
        or _git(worktree, "rev-parse", "HEAD^{tree}") != tree
    ):
        raise ReviewRunnerError("independent review mutated the exact candidate")
    snapshot_hash = _candidate_snapshot_hash(commit, tree)
    request = {
        "schema": "tgw-code-review-request/v1",
        "handoff_hash": lifecycle["job_binding_hash"],
        "card_hash": lifecycle["card_idempotency_key"],
        "snapshot_hash": snapshot_hash,
        "snapshot_root": str(worktree.resolve()),
        "output_contract": "tgw-code-review/v1",
    }
    review_context_unsigned = {
        "schema": "tgw-local-coding-semantic-review-context/v1",
        "root_id": lifecycle["root_id"],
        "plan_commit": plan["plan_commit"],
        "solution_hash": plan["solution_hash"],
        "closure_hash": plan["closure_hash"],
        "source_commit": plan["source_commit"],
        "card_idempotency_key": lifecycle["card_idempotency_key"],
        "candidate_commit": commit,
        "candidate_tree": tree,
        "task_spec": dict(task),
        "task_spec_hash": task_hash,
        "review_mode": "NON_ADMITTING_DIAGNOSTIC",
    }
    request["review_context"] = {
        **review_context_unsigned,
        "context_hash": _hash(review_context_unsigned),
    }
    try:
        semantic_report = dict(semantic_backend(request, worktree))
    except (CodexReviewBackendError, OSError, ValueError) as exc:
        raise ReviewRunnerError(
            f"independent semantic review backend failed: {exc}"
        ) from exc
    validate_review_report(semantic_report, snapshot_hash, worktree)
    failed = [item for item in checks if item["returncode"] != 0]
    report = dict(semantic_report)
    if failed:
        report["verdict"] = "FAIL"
        report["summary"] = (
            f"{semantic_report['summary']}; fixed diagnostic checks failed"
        )[:4000]
        report["findings"] = [*semantic_report["findings"]] + [
            {
                "severity": "high",
                "path": "pyproject.toml",
                "line": 1,
                "message": f"fixed independent check failed: {item['name']}",
            }
            for item in failed
        ]
    validate_review_report(report, snapshot_hash, worktree)
    passed = report["verdict"] == "PASS" and not report["findings"]
    report_bytes = _canonical(report)
    actor = pwd.getpwuid(os.geteuid()).pw_name
    required_actor = os.environ.get("TGW_REVIEW_REQUIRE_ACTOR")
    if required_actor and actor != required_actor:
        raise ReviewRunnerError("independent review execution actor is invalid")
    context_unsigned = {
        "schema": "tgw-local-independent-review-context/v1",
        "mode": "exact-clean-candidate-semantic-review",
        "snapshot_hash": snapshot_hash,
        "worktree": str(worktree.resolve()),
        "plan_commit": plan["plan_commit"],
        "source_commit": plan["source_commit"],
        "card_idempotency_key": lifecycle["card_idempotency_key"],
        "candidate_binding_hash": candidate["candidate_binding_hash"],
        "task_spec_hash": task_hash,
    }
    context = {**context_unsigned, "context_hash": _hash(context_unsigned)}
    execution_unsigned = {
        "schema": "tgw-local-independent-review-execution/v1",
        "actor": actor,
        "uid": os.geteuid(),
        "pid": os.getpid(),
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
        "context": context,
    }
    execution = {**execution_unsigned, "execution_hash": _hash(execution_unsigned)}
    artifact = {
        "kind": "tgw_review_report",
        "diagnostic_verdict": _VERDICT if passed else "FAIL",
        "execution": execution,
        "root_id": lifecycle["root_id"],
        "binding_hash": lifecycle["binding_hash"],
        "job_binding_hash": lifecycle["job_binding_hash"],
        "job_id": job_id,
        "card_idempotency_key": lifecycle["card_idempotency_key"],
        "candidate_binding_hash": candidate["candidate_binding_hash"],
        "candidate_commit": commit,
        "candidate_tree": tree,
        "report": report,
        "report_bytes": report_bytes.decode(),
        "report_sha256": _bytes_hash(report_bytes),
        "checks": checks,
    }
    return {
        "outcome": "satisfied" if passed else "failed",
        "established_conditions": ["reviewed"] if passed else [],
        "artifacts": [artifact],
    }


def _validate_bound_review_artifact(
    value: object,
    *,
    payload: Mapping[str, Any],
    worktree: Path,
    expected_job_id: str,
    passed: bool,
) -> dict[str, Any]:
    """Validate exact positive or diagnostic-negative semantic evidence."""

    if not isinstance(value, Mapping):
        raise ReviewRunnerError("review launcher result is not an object")
    expected_outcome = "satisfied" if passed else "failed"
    expected_conditions = ["reviewed"] if passed else []
    if (
        value.get("outcome") != expected_outcome
        or value.get("established_conditions") != expected_conditions
    ):
        raise ReviewRunnerError("review outcome conditions are contradictory")
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 1:
        raise ReviewRunnerError("review requires one report artifact")
    artifact = artifacts[0]
    lifecycle = payload.get("coding_lifecycle")
    candidate = payload.get("coding_candidate")
    plan = payload.get("plan_binding")
    task = payload.get("task_spec")
    if not all(
        isinstance(item, Mapping)
        for item in (artifact, lifecycle, candidate, plan, task)
    ):
        raise ReviewRunnerError("review report binding is incomplete")
    report = artifact.get("report")
    report_bytes = artifact.get("report_bytes")
    execution = artifact.get("execution")
    checks = artifact.get("checks")
    if (
        artifact.get("kind") != "tgw_review_report"
        or artifact.get("diagnostic_verdict")
        != (_VERDICT if passed else "FAIL")
        or artifact.get("root_id") != lifecycle.get("root_id")
        or artifact.get("binding_hash") != lifecycle.get("binding_hash")
        or artifact.get("job_binding_hash") != lifecycle.get("job_binding_hash")
        or artifact.get("job_id") != expected_job_id
        or artifact.get("card_idempotency_key")
        != lifecycle.get("card_idempotency_key")
        or artifact.get("candidate_binding_hash")
        != candidate.get("candidate_binding_hash")
        or artifact.get("candidate_commit") != candidate.get("commit")
        or artifact.get("candidate_tree") != candidate.get("tree")
        or not isinstance(report, Mapping)
        or not isinstance(report_bytes, str)
        or report_bytes.encode() != _canonical(report)
        or artifact.get("report_sha256") != _bytes_hash(report_bytes.encode())
        or not isinstance(checks, list)
        or sorted(item.get("name") for item in checks if isinstance(item, Mapping))
        != ["git-diff-check"]
        or any(
            not isinstance(item, Mapping)
            or not isinstance(item.get("returncode"), int)
            or (passed and item.get("returncode") != 0)
            or _SHA256.fullmatch(str(item.get("output_sha256", ""))) is None
            for item in checks
        )
        or not isinstance(execution, Mapping)
    ):
        raise ReviewRunnerError("review report is empty, stale, or contradictory")
    execution_unsigned = dict(execution)
    claimed_execution = execution_unsigned.pop("execution_hash", None)
    if (
        set(execution)
        != {
            "schema",
            "actor",
            "uid",
            "pid",
            "service",
            "queue",
            "network",
            "provider",
            "independence",
            "context",
            "execution_hash",
        }
        or
        claimed_execution != _hash(execution_unsigned)
        or execution.get("schema")
        != "tgw-local-independent-review-execution/v1"
        or not isinstance(execution.get("actor"), str)
        or not execution.get("actor")
        or not isinstance(execution.get("uid"), int)
        or execution.get("uid", -1) < 0
        or not isinstance(execution.get("pid"), int)
        or execution.get("pid", 0) <= 0
        or execution.get("queue") != "claude-review"
        or execution.get("service") != "tgw-claude-review-worker.service"
        or execution.get("network") is not True
        or execution.get("provider") != "codex-ephemeral-read-only"
        or execution.get("independence")
        != {
            "separate_queue_job": True,
            "ephemeral_provider_session": True,
            "candidate_sandbox": "read-only",
            "authority": False,
        }
    ):
        raise ReviewRunnerError("review execution identity/context is not independent")
    snapshot_hash = _candidate_snapshot_hash(
        str(candidate.get("commit")), str(candidate.get("tree"))
    )
    context = execution.get("context")
    if not isinstance(context, Mapping):
        raise ReviewRunnerError("review execution context is absent")
    context_unsigned = dict(context)
    claimed_context = context_unsigned.pop("context_hash", None)
    if (
        set(context)
        != {
            "schema",
            "mode",
            "snapshot_hash",
            "worktree",
            "plan_commit",
            "source_commit",
            "card_idempotency_key",
            "candidate_binding_hash",
            "task_spec_hash",
            "context_hash",
        }
        or claimed_context != _hash(context_unsigned)
        or context.get("schema")
        != "tgw-local-independent-review-context/v1"
        or context.get("mode") != "exact-clean-candidate-semantic-review"
        or context.get("snapshot_hash") != snapshot_hash
        or context.get("worktree") != str(worktree.resolve())
        or context.get("plan_commit") != plan.get("plan_commit")
        or context.get("source_commit") != plan.get("source_commit")
        or context.get("card_idempotency_key")
        != lifecycle.get("card_idempotency_key")
        or context.get("candidate_binding_hash")
        != candidate.get("candidate_binding_hash")
        or context.get("task_spec_hash") != _hash(payload.get("task_spec"))
    ):
        raise ReviewRunnerError("review execution context binding is invalid")
    validated = validate_review_report(dict(report), snapshot_hash, worktree)
    if passed:
        if validated["verdict"] != "PASS" or validated["findings"]:
            raise ReviewRunnerError("review did not return PASS with zero findings")
    elif validated["verdict"] != "FAIL" or not validated["findings"]:
        raise ReviewRunnerError("failed review has no diagnostic findings")
    return dict(artifact)


def validate_review_artifact(
    value: object,
    *,
    payload: Mapping[str, Any],
    worktree: Path,
    expected_job_id: str,
) -> dict[str, Any]:
    """Validate semantic PASS evidence independently of queue state."""

    return _validate_bound_review_artifact(
        value,
        payload=payload,
        worktree=worktree,
        expected_job_id=expected_job_id,
        passed=True,
    )


def validate_failed_review_artifact(
    value: object,
    *,
    payload: Mapping[str, Any],
    worktree: Path,
    expected_job_id: str,
) -> dict[str, Any]:
    """Validate a diagnostic FAIL before it may request code remediation."""

    return _validate_bound_review_artifact(
        value,
        payload=payload,
        worktree=worktree,
        expected_job_id=expected_job_id,
        passed=False,
    )


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
