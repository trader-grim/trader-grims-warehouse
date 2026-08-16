#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from tgw.candidate_manifest import create_luet_conformance_receipt
from tgw.logging import announce_script_run
from tgw.plan_luet import conform, normalize_conformance_graph, verify_pinned_luet_binary


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
    input_graph = json.loads(args.graph.read_text())
    graph = normalize_conformance_graph(input_graph)
    commit = subprocess.check_output(["git", "rev-parse", f"{args.candidate}^{{commit}}"], cwd=args.repo, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", f"{commit}^{{tree}}"], cwd=args.repo, text=True).strip()
    binary_hash = verify_pinned_luet_binary(args.luet)
    result = conform(graph, luet_binary=args.luet, expected_plan_commit=graph["plan_commit"])
    receipt = create_luet_conformance_receipt(result, graph=input_graph, plan_commit=graph["plan_commit"], source_commit=commit, source_tree=tree, binary_sha256=binary_hash)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
