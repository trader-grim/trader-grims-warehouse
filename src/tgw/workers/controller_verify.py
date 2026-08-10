"""Local deterministic verifier for the controller-verify coding treatment."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

_CHECKS = (
    ("pytest", [sys.executable, "-m", "pytest", "-q"]),
    ("ruff", [sys.executable, "-m", "ruff", "check", "."]),
)


def _run_check(name: str, command: list[str]) -> dict[str, str]:
    env = dict(os.environ)
    worktree_source = env.get("TGW_CODING_WORKTREE_SRC")
    if worktree_source:
        inherited = env.get("PYTHONPATH")
        env["PYTHONPATH"] = worktree_source + (os.pathsep + inherited if inherited else "")
    try:
        completed = subprocess.run(command, check=False, text=True, capture_output=True, env=env)
    except OSError as exc:
        return {"kind": "check", "name": name, "status": "failed", "detail": str(exc)}
    result = {"kind": "check", "name": name, "status": "passed" if completed.returncode == 0 else "failed"}
    if completed.returncode:
        result["detail"] = (completed.stderr or completed.stdout)[-500:]
    return result


def main() -> int:
    """Run project tests and lint, emitting the coding-runner JSON protocol."""
    artifacts: list[dict[str, Any]] = []
    for name, command in _CHECKS:
        artifact = _run_check(name, command)
        artifacts.append(artifact)
        if artifact["status"] != "passed":
            print(json.dumps({"outcome": "failed", "established_conditions": [], "artifacts": artifacts}))
            return 0
    print(json.dumps({
        "outcome": "satisfied",
        "established_conditions": ["tested", "linted", "controller_verified"],
        "artifacts": artifacts,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
