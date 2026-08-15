#!/usr/bin/env python3
"""Build the deterministic production projection for one approved Plan."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from tgw.logging import announce_script_run
from tgw.plan_runtime_projection import SCHEMA, canonical, sha256, validate_projection
from tgw.plan_solver import validate_for_dispatch

PLAN_FILES = (
    "plan/PLAN-governed-execution-platform-build.md",
    "plan/execution/GOVERNED-EXECUTION-PLATFORM-v1.yaml",
)


def git_show(root: Path, commit: str, relative_path: str) -> bytes:
    return subprocess.run(
        [
            "git", "-c", f"safe.directory={root}", "-C", str(root),
            "show", f"{commit}:{relative_path}",
        ],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan-root", type=Path, required=True)
    parser.add_argument("--plan-commit", required=True)
    parser.add_argument("--solution", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    announce_script_run(
        "build_plan_runtime_projection.py",
        "build the immutable runtime projection for an approved standalone Plan",
        plan_root=str(args.plan_root),
        plan_commit=args.plan_commit,
    )
    solution = json.loads(args.solution.read_text(encoding="utf-8"))
    validate_for_dispatch(solution, current_plan_commit=args.plan_commit)
    unsigned = {
        "schema": SCHEMA,
        "plan_id": "PLAN-GOVERNED-EXECUTION-PLATFORM",
        "plan_commit": args.plan_commit,
        "plan_files": [
            {"path": path, "sha256": sha256(git_show(args.plan_root, args.plan_commit, path))}
            for path in PLAN_FILES
        ],
        "solution": solution,
        "solution_sha256": sha256(canonical(solution)),
    }
    projection = {**unsigned, "projection_sha256": sha256(canonical(unsigned))}
    validate_projection(projection, expected_plan_commit=args.plan_commit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(projection, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
