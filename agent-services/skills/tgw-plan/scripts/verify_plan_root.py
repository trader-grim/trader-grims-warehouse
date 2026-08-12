#!/usr/bin/env python3
"""Verify and report an exact standalone TGW Plan repository binding."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={root}", "-C", str(root), *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise SystemExit(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify_plan_root.py PLAN_ROOT")
    root = Path(sys.argv[1]).resolve()
    top = Path(git(root, "rev-parse", "--show-toplevel").strip()).resolve()
    if top != root:
        raise SystemExit(f"Plan root mismatch: requested={root} git={top}")
    required = [
        "plan/SPEC-plan-capability-graph-v2.md",
        "plan/PLAN-governed-execution-platform-build.md",
    ]
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise SystemExit(f"missing canonical Plan files: {', '.join(missing)}")
    payload = {
        "schema": "tgw-plan-repository-binding/v1",
        "root": str(root),
        "commit": git(root, "rev-parse", "HEAD").strip(),
        "branch": git(root, "branch", "--show-current").strip() or None,
        "clean": not bool(git(root, "status", "--porcelain=v1")),
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["clean"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
