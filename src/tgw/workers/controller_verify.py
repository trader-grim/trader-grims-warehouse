"""Source-bound deterministic verifier for the local controller treatment."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_COMMIT = re.compile(r"[0-9a-f]{40}\Z")


class ControllerVerificationError(RuntimeError):
    """The local job cannot be verified against its bound source."""


def _git_paths(cwd: Path, *args: str) -> tuple[str, ...]:
    completed = subprocess.run(
        ["git", *args],
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
        ["git", *args],
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode:
        raise ControllerVerificationError(f"Git verification probe failed: {completed.stderr[-500:]}")
    return completed.stdout.strip()


def _source_bound_python_files() -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        job = json.loads(os.environ["TGW_CODING_JOB"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise ControllerVerificationError("controller job payload is unavailable") from exc
    binding = job.get("plan_binding") if isinstance(job, dict) else None
    baseline = binding.get("source_commit") if isinstance(binding, dict) else None
    if not isinstance(baseline, str) or _COMMIT.fullmatch(baseline) is None:
        raise ControllerVerificationError("controller job lacks a Plan-bound source commit")

    cwd = Path.cwd().resolve()
    if _git_text(cwd, "rev-parse", "HEAD") != baseline:
        raise ControllerVerificationError("controller worktree HEAD differs from its bound source")
    tracked = _git_paths(
        cwd,
        "diff",
        "--name-only",
        "--diff-filter=ACMRT",
        "-z",
        baseline,
        "--",
    )
    untracked = _git_paths(cwd, "ls-files", "--others", "--exclude-standard", "-z")

    python_files: list[str] = []
    for relative in sorted(set((*tracked, *untracked))):
        candidate = Path(relative)
        resolved = (cwd / candidate).resolve()
        if candidate.is_absolute() or cwd not in resolved.parents or resolved.suffix != ".py" or not resolved.is_file():
            continue
        python_files.append(relative)
    tests = tuple(item for item in python_files if Path(item).parts[:1] == ("tests",) and Path(item).name.startswith("test_"))
    if not python_files:
        raise ControllerVerificationError("implementation has no changed Python files to verify")
    if not tests:
        raise ControllerVerificationError("implementation has no changed source-bound tests")
    return tuple(python_files), tests


def _verification_commands() -> tuple[tuple[str, list[str]], ...]:
    python_files, tests = _source_bound_python_files()
    return (
        ("pytest", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests]),
        ("ruff", [sys.executable, "-m", "ruff", "check", "--no-cache", *python_files]),
    )


def _run_check(name: str, command: list[str]) -> dict[str, Any]:
    env = dict(os.environ)
    worktree_source = env.get("TGW_CODING_WORKTREE_SRC") or str(Path.cwd() / "src")
    inherited = env.get("PYTHONPATH")
    env["PYTHONPATH"] = worktree_source + (os.pathsep + inherited if inherited else "")
    try:
        completed = subprocess.run(command, check=False, text=True, capture_output=True, env=env)
    except OSError as exc:
        return {"kind": "check", "name": name, "status": "failed", "detail": str(exc)}
    result: dict[str, Any] = {
        "kind": "check",
        "name": name,
        "status": "passed" if completed.returncode == 0 else "failed",
        "targets": command[6:] if name == "pytest" else command[5:],
    }
    if completed.returncode:
        result["detail"] = (completed.stderr or completed.stdout)[-2000:]
    return result


def main() -> int:
    """Verify only candidate bytes and tests changed from the Plan-bound source."""
    try:
        checks = _verification_commands()
    except ControllerVerificationError as exc:
        print(
            json.dumps(
                {
                    "outcome": "failed",
                    "established_conditions": [],
                    "artifacts": [{"kind": "verification_scope", "status": "failed", "detail": str(exc)}],
                }
            )
        )
        return 0

    artifacts: list[dict[str, Any]] = []
    for name, command in checks:
        artifact = _run_check(name, command)
        artifacts.append(artifact)
        if artifact["status"] != "passed":
            print(json.dumps({"outcome": "failed", "established_conditions": [], "artifacts": artifacts}))
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
