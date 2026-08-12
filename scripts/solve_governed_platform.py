#!/usr/bin/env python3
"""Emit the exact governed-platform solution using native and pinned Luet solvers."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import yaml

from tgw.plan_catalog import compose_catalog, load_provider_catalog
from tgw.plan_luet import conform
from tgw.plan_solver import solve, validate_for_dispatch


def plan_commit(root: Path) -> str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={root}", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan-root", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--luet", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.plan_root.resolve()
    commit = plan_commit(root)
    execution = yaml.safe_load(
        (root / "plan/execution/GOVERNED-EXECUTION-PLATFORM-v1.yaml").read_text()
    )
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
