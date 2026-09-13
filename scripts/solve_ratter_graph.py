#!/usr/bin/env python3
"""Solve the unified ``ratter`` capability graph with native and pinned Luet solvers.

Mirrors ``solve_governed_platform.py`` exactly in structure and reads
``plan/execution/RATTER-CAPABILITY-GRAPH-v1.yaml`` instead of
``GOVERNED-EXECUTION-PLATFORM-v1.yaml``.

Plan-commit decision (Todo 2008, item R): the ratter graph is not under the
approved ``GOVERNED-EXECUTION-PLATFORM`` Plan commit
(``058e2f980201cc78245358e4901cf007063f2c29``) -- that commit's own
``required_capabilities`` do not mention any LEAF-11/ratter capability, and
binding to it would misrepresent an unrelated, already-ratified closure as
covering this graph. No operator-ratified Plan commit exists yet for
``PLAN-RATTER-CAPABILITY-GRAPH`` either; ratifying one is a separate operator
decision this script does not make. The caller must therefore pass
``--plan-commit`` bound to the exact plan-vault commit that contains the
``RATTER-CAPABILITY-GRAPH-v1.yaml`` document being solved (its current
evidence head) so the solved closure is at least byte-exact to a real,
inspectable document -- never a stand-in for Plan-authority ratification.
"""

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
from tgw.plan_solver import solve, validate_solution_integrity

_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def plan_commit(root: Path, ref: str) -> str:
    if not _COMMIT.fullmatch(ref):
        raise ValueError("--plan-commit must be a full Git commit")
    result = subprocess.run(
        [
            "git", "-c", f"safe.directory={root}", "-C", str(root),
            "rev-parse", "--verify", f"{ref}^{{commit}}",
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    resolved = result.stdout.strip()
    if resolved != ref:
        raise ValueError("Plan ref did not resolve to the exact commit")
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
        "solve_ratter_graph.py",
        "resolve the unified ratter capability graph with native and pinned Luet providers",
        plan_root=str(args.plan_root),
    )
    root = args.plan_root.resolve()
    commit = plan_commit(root, args.plan_commit)
    execution = yaml.safe_load(plan_file(
        root, commit, "plan/execution/RATTER-CAPABILITY-GRAPH-v1.yaml"
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
    # Per Todo 2008: never fabricate a solution. validate_for_dispatch would
    # raise on an incomplete/UNSOLVED closure; validate_solution_integrity
    # checks only the immutable identity/binding of whatever was actually
    # produced, so an honest partial result is still emitted and recorded.
    validate_solution_integrity(solution, current_plan_commit=commit)
    rendered = json.dumps(solution, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
