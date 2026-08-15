#!/usr/bin/env python3
"""Emit the exact governed-platform solution using native and pinned Luet solvers."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

import yaml

from tgw.logging import announce_script_run
from tgw.plan_catalog import compose_catalog, load_provider_catalog
from tgw.plan_luet import conform
from tgw.plan_solver import solve, validate_for_dispatch

_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def plan_commit(root: Path, approved_ref: str) -> str:
    if not _COMMIT.fullmatch(approved_ref):
        raise ValueError("--plan-commit must be a full Git commit")
    result = subprocess.run(
        [
            "git", "-c", f"safe.directory={root}", "-C", str(root),
            "rev-parse", "--verify", f"{approved_ref}^{{commit}}",
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    resolved = result.stdout.strip()
    if resolved != approved_ref:
        raise ValueError("approved Plan ref did not resolve to the exact commit")
    return resolved


def plan_file(root: Path, commit: str, relative_path: str) -> str:
    result = subprocess.run(
        [
            "git", "-c", f"safe.directory={root}", "-C", str(root),
            "show", f"{commit}:{relative_path}",
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan-root", type=Path, required=True)
    parser.add_argument("--plan-commit", required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--luet", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    announce_script_run(
        "solve_governed_platform.py",
        "resolve the governed platform graph with native and pinned Luet providers",
        plan_root=str(args.plan_root),
    )
    root = args.plan_root.resolve()
    commit = plan_commit(root, args.plan_commit)
    execution = yaml.safe_load(plan_file(
        root, commit, "plan/execution/GOVERNED-EXECUTION-PLATFORM-v1.yaml"
    ))
    graph = compose_catalog(
        execution, load_provider_catalog(args.catalog), plan_commit=commit
    )
    conformance = conform(
        graph, luet_binary=args.luet, expected_plan_commit=commit
    )
    solution = solve(
        graph, expected_plan_commit=commit, conformance_result=conformance
    )
    validate_for_dispatch(solution, current_plan_commit=commit)
    rendered = json.dumps(solution, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
