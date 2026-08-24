#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

from tgw.candidate_manifest import create_luet_conformance_receipt
from tgw.logging import announce_script_run
from tgw.plan_catalog import compose_catalog
from tgw.plan_luet import (
    conform,
    verify_pinned_luet_binary,
)

CATALOG_PATH = "agent-services/catalogs/governed-execution-platform-v1.json"
EXECUTION_PATH = "plan/execution/GOVERNED-EXECUTION-PLATFORM-v1.yaml"
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _git(repo: Path, *arguments: str, text: bool = True) -> str | bytes:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={repo.resolve()}", *arguments],
        cwd=repo, check=True, capture_output=True, text=text,
    )
    return result.stdout


def _approved_plan_commit(repository: Path, approved_ref: str) -> str:
    if not _COMMIT.fullmatch(approved_ref):
        raise ValueError("approved Plan binding must be an exact full Git commit")
    commit = str(_git(repository, "rev-parse", f"{approved_ref}^{{commit}}")).strip()
    if commit != approved_ref:
        raise ValueError("approved Plan binding did not resolve to the exact commit")
    return commit


def _candidate_catalog(repository: Path, candidate: str) -> tuple[str, str, dict[str, Any]]:
    """Read the sole conformance catalog from the candidate's committed tree."""

    commit = str(_git(repository, "rev-parse", f"{candidate}^{{commit}}")).strip()
    tree = str(_git(repository, "rev-parse", f"{commit}^{{tree}}")).strip()
    try:
        raw = _git(repository, "show", f"{commit}:{CATALOG_PATH}", text=False)
        value = json.loads(bytes(raw))
    except (subprocess.CalledProcessError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("candidate canonical Luet catalog is unavailable") from exc
    if not isinstance(value, dict):
        raise ValueError("candidate canonical Luet catalog must be an object")
    return commit, tree, value


def _approved_execution(repository: Path, *, plan_commit: str) -> dict[str, Any]:
    """Read the only execution source allowed to define the Luet graph."""

    try:
        raw = _git(repository, "show", f"{plan_commit}:{EXECUTION_PATH}", text=False)
        value = yaml.safe_load(bytes(raw))
    except (subprocess.CalledProcessError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError("approved governed execution Plan source is unavailable") from exc
    if not isinstance(value, dict):
        raise ValueError("approved governed execution Plan source must be an object")
    return value


def _bound_candidate_catalog(
    repository: Path, candidate: str, *, plan_repository: Path, approved_ref: str,
) -> tuple[str, str, dict[str, Any], dict[str, Any], str]:
    """Resolve the candidate catalog only when it binds the approved Plan."""

    approved_plan_commit = _approved_plan_commit(plan_repository, approved_ref)
    commit, tree, input_graph = _candidate_catalog(repository, candidate)
    if input_graph.get("plan_commit") != approved_plan_commit:
        raise ValueError("candidate Luet catalog does not bind the approved Plan commit")
    execution = _approved_execution(plan_repository, plan_commit=approved_plan_commit)
    try:
        graph = compose_catalog(execution, input_graph, plan_commit=approved_plan_commit)
    except ValueError as exc:
        raise ValueError("candidate Luet catalog does not derive the approved Plan graph") from exc
    return commit, tree, input_graph, graph, approved_plan_commit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--luet", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--plan-repository", type=Path, required=True)
    parser.add_argument("--plan-approved-ref", required=True,
                        help="exact approved Plan commit (never a movable ref)")
    args = parser.parse_args()
    announce_script_run(
        "run_luet_conformance.py",
        "run the pinned Luet adapter and emit a candidate-bound conformance receipt",
        candidate=args.candidate,
    )
    repository = args.repo.resolve()
    plan_repository = args.plan_repository.resolve()
    commit, tree, input_graph, graph, approved_plan_commit = _bound_candidate_catalog(
        repository, args.candidate, plan_repository=plan_repository, approved_ref=args.plan_approved_ref,
    )
    binary_hash = verify_pinned_luet_binary(args.luet)
    result = conform(graph, luet_binary=args.luet, expected_plan_commit=approved_plan_commit)
    receipt = create_luet_conformance_receipt(
        result, graph=graph, plan_commit=approved_plan_commit,
        source_commit=commit, source_tree=tree, binary_sha256=binary_hash,
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
