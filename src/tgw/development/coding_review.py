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
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tgw.claude_review_backend import ClaudeReviewBackendError
from tgw.claude_review_backend import run as run_claude_review
from tgw.codex_review_backend import CodexReviewBackendError
from tgw.codex_review_backend import run as run_codex_review
from tgw.development.partial_resume import HISTORY, PRESERVATION
from tgw.protected_git import protected_git_command, protected_git_environment
from tgw.review_contract import ReviewRunnerError, validate_review_report

_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ROOT = re.compile(r"coding:[0-9a-f]{64}\Z")
_VERDICT = "PASS_NON_ADMITTING"
_RECEIPT_FILES = frozenset({
    "implementation-receipt.json",
    "controller-harness-receipt.json",
    "review-receipt.json",
    "deployment-receipt.json",
    "stitch-receipt.json",
    "operator-admit-pending.json",
})
_HISTORY_ROOT = HISTORY.split("/", 1)[0]
_PRESERVATION_ROOT = PRESERVATION.split("/", 1)[0]


def _source_status(worktree: Path) -> str:
    """Worktree status excluding workflow-evidence files.

    Receipt files and the partial-resume history/preservation trees are
    deliberately untracked workflow evidence, never candidate source.  The
    candidate-integrity check must ignore them or every closed candidate
    is misclassified as mutated.
    """
    lines = _git(
        worktree, "status", "--porcelain=v1", "--untracked-files=all"
    ).splitlines()
    kept = []
    for line in lines:
        if len(line) < 4:
            kept.append(line)
            continue
        path = line[3:]
        if path in _RECEIPT_FILES:
            continue
        if path == _HISTORY_ROOT or path.startswith(_HISTORY_ROOT + "/"):
            continue
        if path == _PRESERVATION_ROOT or path.startswith(_PRESERVATION_ROOT + "/"):
            continue
        kept.append(line)
    return "\n".join(kept)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _validate_plan_leaf_citation(
    task: Mapping[str, Any], plan: Mapping[str, Any]
) -> None:
    """Require the task's Plan leaf citation to match the job's Plan binding.

    Independent review must check the candidate against the same approved Plan
    leaf the implementer was bound to, so a drift between the citation and the
    job binding is a hard binding failure, not a review finding.
    """
    leaf = task.get("plan_leaf")
    if leaf is None:
        # Pre-plan-citation tasks are legacy; their job binding still governs.
        return
    if not isinstance(leaf, Mapping) or leaf.get("schema") != "tgw-plan-leaf-citation/v1":
        raise ReviewRunnerError("review task Plan leaf citation schema is invalid")
    for field in (
        "plan_commit",
        "solution_hash",
        "closure_hash",
        "capability",
        "treatment_id",
        "source_commit",
    ):
        observed = leaf.get(field)
        expected = plan.get(field)
        if not isinstance(observed, str) or not observed:
            raise ReviewRunnerError(f"review task Plan leaf citation lacks {field}")
        if observed != expected:
            raise ReviewRunnerError(
                f"review task Plan leaf {field} differs from the job Plan binding"
            )


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



_MANUAL_REVIEW_REL = ".tgw-coding-history/implementation/review-manual"
_MANUAL_REVIEW_REQUEST_NAME = "request.json"
_MANUAL_REVIEW_DONE_NAME = "done.json"


def _review_poll_seconds() -> float:
    try:
        return float(os.environ.get("TGW_REVIEW_MANUAL_POLL", "5"))
    except ValueError as exc:
        raise ReviewRunnerError("manual review poll interval is invalid") from exc


def _review_timeout_seconds() -> float:
    try:
        return float(os.environ.get("TGW_REVIEW_MANUAL_TIMEOUT", "1500"))
    except ValueError as exc:
        raise ReviewRunnerError("manual review timeout is invalid") from exc


def _manual_review_executor(request: Mapping[str, Any], worktree: Path) -> Mapping[str, Any]:
    """Supervised review handshake: request card out, report marker in.

    The supervisor/agent inspects the candidate snapshot read-only and writes
    ``done.json`` with the exact ``tgw-code-review/v1`` report.  The runner
    still validates the report against the snapshot hash and finding paths;
    the marker is never completion evidence on its own.
    """
    root = worktree / _MANUAL_REVIEW_REL
    root.mkdir(parents=True, exist_ok=True)
    task_path = root / _MANUAL_REVIEW_REQUEST_NAME
    done_path = root / _MANUAL_REVIEW_DONE_NAME
    task_path.write_text(
        json.dumps(
            {
                "schema": "tgw-manual-review-request/v1",
                "request": dict(request),
                "done_marker": str(done_path.resolve()),
                "output_contract": "tgw-code-review/v1",
                "note": (
                    "Review the candidate snapshot at snapshot_root read-only. Write the done "
                    "marker with the exact tgw-code-review/v1 report; the runner validates it "
                    "against the snapshot hash and finding paths."
                ),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    task_path.chmod(0o600)
    deadline = time.monotonic() + _review_timeout_seconds()
    while time.monotonic() < deadline:
        if done_path.is_file():
            try:
                report = json.loads(done_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ReviewRunnerError(
                    f"manual review completion report is invalid: {exc}"
                ) from exc
            shutil.rmtree(root, ignore_errors=True)
            return report
        time.sleep(_review_poll_seconds())
    raise ReviewRunnerError("manual review timed out; candidate snapshot unchanged")


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
        set(task) not in (
            {"schema", "todo_id", "agent", "body"},
            {"schema", "todo_id", "agent", "body", "plan_leaf"},
        )
        or task.get("schema") != "coding-task/v1"
        or task.get("todo_id") != payload.get("todo_id")
        or not isinstance(task.get("body"), str)
        or not task["body"].strip()
    ):
        raise ReviewRunnerError("independent review task binding is invalid")
    _validate_plan_leaf_citation(task, plan)
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
        _source_status(worktree)
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
    backend = semantic_backend
    executor = os.environ.get("TGW_REVIEW_EXECUTOR", "codex")
    if executor == "manual":
        backend = _manual_review_executor
    elif executor == "claude":
        backend = run_claude_review
    try:
        semantic_report = dict(backend(request, worktree))
    except (CodexReviewBackendError, ClaudeReviewBackendError, OSError, ValueError) as exc:
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
