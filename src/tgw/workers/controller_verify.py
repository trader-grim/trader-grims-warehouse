"""Source-bound deterministic verifier for the local controller treatment."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from tgw.development.worktree_lease import exclusive_worktree_lease
from tgw.errors import HardFailure

_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_RECEIPTS = frozenset({
    "implementation-receipt.json", "controller-harness-receipt.json",
    "review-receipt.json", "deployment-receipt.json", "stitch-receipt.json",
    "operator-admit-pending.json",
})
_HISTORY_FILE = re.compile(
    r"\.tgw-coding-history/implementation/[0-9]{6}-[0-9a-f]{64}\.json\Z"
)


class ControllerVerificationError(RuntimeError):
    """The local job cannot be verified against its bound source."""


def _git_paths(cwd: Path, *args: str) -> tuple[str, ...]:
    completed = subprocess.run(
        ["git", "-c", f"safe.directory={cwd.resolve()}", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
    )
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", errors="replace")[-500:]
        raise ControllerVerificationError(f"Git verification probe failed: {detail}")
    raw = completed.stdout
    if not raw:
        return ()
    if not raw.endswith(b"\0"):
        raise ControllerVerificationError("Git verification probe returned malformed NUL output")
    fields = raw[:-1].split(b"\0")
    if any(not item for item in fields):
        raise ControllerVerificationError("Git verification probe returned malformed NUL output")
    try:
        return tuple(item.decode("utf-8", errors="strict") for item in fields)
    except UnicodeDecodeError as exc:
        raise ControllerVerificationError(
            "Git verification probe returned an undecodable path"
        ) from exc


def _git_text(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-c", f"safe.directory={cwd.resolve()}", *args],
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode:
        raise ControllerVerificationError(f"Git verification probe failed: {completed.stderr[-500:]}")
    return completed.stdout.strip()


def _is_workflow_evidence_path(relative: str) -> bool:
    """Return whether a safe repository-relative path is controller-owned evidence."""
    return relative in _RECEIPTS or _HISTORY_FILE.fullmatch(relative) is not None


def _assert_source_status_clean(cwd: Path) -> None:
    """Fail closed unless every NUL-delimited status entry is owned evidence."""
    status = _git_paths(
        cwd,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--ignored=matching",
    )
    for item in status:
        # Wrapper evidence is untracked or ignored. A staged, tracked, renamed,
        # copied, unmerged, malformed, or undecodable entry is source mutation,
        # even when its path resembles an evidence filename.
        if (
            len(item) < 4
            or item[2] != " "
            or item[:2] not in {"??", "!!"}
        ):
            raise ControllerVerificationError(
                "controller candidate source is mutable or uncommitted"
            )
        relative = item[3:]
        preservation = relative.startswith(".tgw-coding-preservation/")
        if preservation:
            from tgw.development.coding_snapshot import _preservation_evidence
            if item[:2] != "??" or _preservation_evidence(cwd, relative) is None:
                raise ControllerVerificationError(
                    "controller candidate source is mutable or uncommitted"
                )
        elif not _is_workflow_evidence_path(relative):
            raise ControllerVerificationError(
                "controller candidate source is mutable or uncommitted"
            )


def _assert_implementation_lineage(
    cwd: Path, job: dict[str, Any], baseline: str, head: str, tree: str,
) -> None:
    """Bind controller consumption to the exact published implementation."""
    try:
        from tgw.development.partial_resume import validate_implementation_lineage
        receipt = json.loads((cwd / "implementation-receipt.json").read_text(encoding="utf-8"))
        if not isinstance(receipt, dict):
            raise ValueError("implementation receipt is not an object")
        latest = validate_implementation_lineage(
            cwd, base_commit=baseline, candidate_commit=head,
            candidate_tree=tree, receipt=receipt, expected={
                "todo_id": job.get("todo_id"),
                "plan_commit": job.get("plan_binding", {}).get("plan_commit"),
                "solution_hash": job.get("plan_binding", {}).get("solution_hash"),
                "source_commit": baseline,
                "source_tree": _git_text(cwd, "rev-parse", f"{baseline}^{{tree}}"),
                "actor": job.get("todo_agent") or job.get("agent"),
                "worktree": str(cwd),
                "treatment_id": "codex-implement",
                "treatment_version": "1",
            },
        )
        if job.get("implementation_attempt_hash") != latest.get("attempt_hash"):
            raise ValueError("controller implementation attempt hash is absent or stale")
        expected_plan = job.get("plan_binding")
        if (
            not isinstance(expected_plan, dict)
            or receipt.get("plan_binding") != expected_plan
            or latest.get("plan_commit") != expected_plan.get("plan_commit")
            or latest.get("solution_hash") != expected_plan.get("solution_hash")
            or latest.get("source_commit") != expected_plan.get("source_commit")
            or latest.get("source_tree") != _git_text(cwd, "rev-parse", f"{baseline}^{{tree}}")
            or latest.get("todo_id") != job.get("todo_id")
            or latest.get("worktree") != str(cwd)
        ):
            raise ValueError("implementation lineage does not match controller job")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ControllerVerificationError(
            f"exact implementation lineage is absent or stale: {exc}"
        ) from exc


def _source_bound_candidate_files() -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], str, str]:
    try:
        job = json.loads(os.environ["TGW_CODING_JOB"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise ControllerVerificationError("controller job payload is unavailable") from exc
    binding = job.get("plan_binding") if isinstance(job, dict) else None
    baseline = binding.get("source_commit") if isinstance(binding, dict) else None
    if not isinstance(baseline, str) or _COMMIT.fullmatch(baseline) is None:
        raise ControllerVerificationError("controller job lacks a Plan-bound source commit")

    cwd = Path.cwd().resolve()
    head = _git_text(cwd, "rev-parse", "HEAD")
    tree = _git_text(cwd, "rev-parse", "HEAD^{tree}")
    if head == baseline:
        raise ControllerVerificationError("controller worktree has no committed successor")
    if subprocess.run(
        ["git", "-c", f"safe.directory={cwd}", "merge-base", "--is-ancestor", baseline, head],
        cwd=cwd, check=False, capture_output=True,
    ).returncode:
        raise ControllerVerificationError("controller candidate does not descend from its bound source")
    _assert_source_status_clean(cwd)
    expected_generation = job.get("object_generation")
    actual_generation = hashlib.sha256(f"{head}|{tree}".encode()).hexdigest()[:16]
    if expected_generation != actual_generation:
        raise ControllerVerificationError("controller candidate commit/tree differs from its dispatched generation")
    _assert_implementation_lineage(cwd, job, baseline, head, tree)
    tracked = _git_paths(
        cwd,
        "diff",
        "--name-only",
        "-z",
        f"{baseline}..{head}",
        "--",
    )
    if not tracked:
        raise ControllerVerificationError("controller candidate has no source-bound changes")

    python_files: list[str] = []
    all_files: list[str] = []
    for relative in sorted(set(tracked)):
        candidate = Path(relative)
        resolved = (cwd / candidate).resolve()
        if candidate.is_absolute() or cwd not in resolved.parents:
            raise ControllerVerificationError("controller candidate contains an unsafe path")
        all_files.append(relative)
        if resolved.suffix != ".py" or not resolved.is_file():
            continue
        python_files.append(relative)
    tests = tuple(item for item in python_files if Path(item).parts[:1] == ("tests",) and Path(item).name.startswith("test_"))
    if not python_files:
        raise ControllerVerificationError("implementation has no changed Python files to verify")
    if not tests:
        raise ControllerVerificationError("implementation has no changed source-bound tests")
    return tuple(all_files), tuple(python_files), tests, baseline, head


def _source_bound_python_files() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Compatibility view used by focused tests and callers."""
    _all_files, python_files, tests, _baseline, _head = _source_bound_candidate_files()
    return python_files, tests


def _verification_commands() -> tuple[tuple[str, list[str]], ...]:
    all_files, python_files, tests, baseline, head = _source_bound_candidate_files()
    cwd = Path.cwd().resolve()
    return (
        (
            "candidate-diff",
            [
                "git", "-c", f"safe.directory={cwd}", "diff", "--check",
                f"{baseline}..{head}", "--", *all_files,
            ],
        ),
        ("pytest", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests]),
        ("ruff", [sys.executable, "-m", "ruff", "check", "--no-cache", *python_files]),
    )


def _run_check(name: str, command: list[str]) -> dict[str, Any]:
    env = dict(os.environ)
    worktree_source = env.get("TGW_CODING_WORKTREE_SRC") or str(Path.cwd() / "src")
    inherited = env.get("PYTHONPATH")
    env["PYTHONPATH"] = worktree_source + (os.pathsep + inherited if inherited else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # The outer worker payload controls this controller process.  It must not
    # leak into pytest, where unit calls to ``main`` would otherwise mistake
    # themselves for a dispatched controller and acquire the live worktree
    # lease through mocked subprocess probes.
    env.pop("TGW_CODING_JOB", None)
    try:
        completed = subprocess.run(command, check=False, text=True, capture_output=True, env=env)
    except OSError as exc:
        return {"kind": "check", "name": name, "status": "failed", "detail": str(exc)}
    result: dict[str, Any] = {
        "kind": "check",
        "name": name,
        "status": "passed" if completed.returncode == 0 else "failed",
        "targets": (
            command[7:]
            if name == "candidate-diff"
            else command[6:]
            if name == "pytest"
            else command[5:]
        ),
    }
    if completed.returncode:
        result["detail"] = (completed.stderr or completed.stdout)[-2000:]
    return result


def main() -> int:
    """Verify only candidate bytes and tests changed from the Plan-bound source."""
    try:
        # Unit callers without a dispatched job remain pure. Every production
        # controller job has TGW_CODING_JOB and holds the same inode lease used
        # by implementation from the first identity check through the last.
        lease = (
            exclusive_worktree_lease(Path.cwd().resolve())
            if "TGW_CODING_JOB" in os.environ
            else nullcontext()
        )
        with lease:
            checks = _verification_commands()
            artifacts: list[dict[str, Any]] = []
            for name, command in checks:
                artifact = _run_check(name, command)
                artifacts.append(artifact)
                if artifact["status"] != "passed":
                    print(json.dumps({"outcome": "failed", "established_conditions": [], "artifacts": artifacts}))
                    return 0
            if "TGW_CODING_JOB" in os.environ:
                # Re-evaluate HEAD, tree, generation, status and the complete
                # path set after the checks, before emitting success.
                _source_bound_candidate_files()
    except (ControllerVerificationError, HardFailure) as exc:
        print(json.dumps({
            "outcome": "failed",
            "established_conditions": [],
            "artifacts": [
                {"kind": "verification_scope", "status": "failed", "detail": str(exc)}
            ],
        }))
        return 0

    print(
        json.dumps(
            {
                "outcome": "satisfied",
                "established_conditions": ["tested", "linted", "controller_verified"],
                "artifacts": artifacts,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
