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
    return tuple(item.decode("utf-8", errors="surrogateescape") for item in completed.stdout.split(b"\0") if item)


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
    status = _git_paths(
        cwd,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--ignored=matching",
    )
    if any(item[3:] not in _RECEIPTS for item in status):
        raise ControllerVerificationError("controller candidate source is mutable or uncommitted")
    expected_generation = job.get("object_generation")
    actual_generation = hashlib.sha256(f"{head}|{tree}".encode()).hexdigest()[:16]
    if expected_generation != actual_generation:
        raise ControllerVerificationError("controller candidate commit/tree differs from its dispatched generation")
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
