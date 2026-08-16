#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from tgw.candidate_manifest import create_luet_conformance_receipt
from tgw.logging import announce_script_run
from tgw.plan_luet import conform


def _capability_graph(document: object) -> dict[str, object]:
    """Accept the checked-in provider catalog without mistaking it for a graph.

    The execution catalog is the portable source for the canonical fixture; its
    target profile is deliberately expressed separately from the provider list.
    Normalize that contract here before asking either resolver to solve it.
    """

    if not isinstance(document, dict):
        raise ValueError("Luet conformance graph must be a JSON object")
    if document.get("schema") == "tgw-plan/v2":
        return document
    required = {"schema", "id", "plan_id", "plan_commit", "profiles", "capabilities", "providers", "observations"}
    if document.get("schema") != "tgw-plan-provider-catalog/v1" or set(document) != required:
        raise ValueError("Luet conformance input is neither a capability graph nor provider catalog")
    profiles = document["profiles"]
    if not isinstance(profiles, dict) or set(profiles) != {"production"}:
        raise ValueError("provider catalog lacks the production profile")
    production = profiles["production"]
    if not isinstance(production, dict) or set(production) != {"minimum_state"}:
        raise ValueError("provider catalog production profile is invalid")
    required_capabilities = document["capabilities"]
    if not isinstance(required_capabilities, list) or not all(isinstance(item, str) and item for item in required_capabilities):
        raise ValueError("provider catalog capabilities are invalid")
    return {
        **document,
        "schema": "tgw-plan/v2",
        "target": {
            "id": document["plan_id"],
            "profile": "production",
            "minimum_state": production["minimum_state"],
            "required_capabilities": required_capabilities,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--luet", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    args = parser.parse_args()
    announce_script_run(
        "run_luet_conformance.py",
        "run the pinned Luet adapter and emit a candidate-bound conformance receipt",
        candidate=args.candidate,
    )
    graph = _capability_graph(json.loads(args.graph.read_text()))
    commit = subprocess.check_output(["git", "rev-parse", f"{args.candidate}^{{commit}}"], cwd=args.repo, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", f"{commit}^{{tree}}"], cwd=args.repo, text=True).strip()
    result = conform(graph, luet_binary=args.luet, expected_plan_commit=graph["plan_commit"])
    binary_hash = "sha256:" + hashlib.sha256(args.luet.read_bytes()).hexdigest()
    receipt = create_luet_conformance_receipt(result, graph=graph, plan_commit=graph["plan_commit"], source_commit=commit, source_tree=tree, binary_sha256=binary_hash)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
