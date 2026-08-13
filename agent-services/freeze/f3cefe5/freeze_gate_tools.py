#!/usr/bin/env python3
"""Small, reviewable gate helpers for the immutable f3cefe5 freeze.

This file is evidence tooling, not product code.  Every subcommand takes and
emits explicit files so the execution record can preserve literal argv.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import yaml

from tgw.candidate_manifest import create_luet_conformance_receipt
from tgw.plan_catalog import compose_catalog, load_provider_catalog
from tgw.plan_luet import _unique_closure, _write_tree, conform
from tgw.plan_solver import CapabilityGraph, solve, validate_for_dispatch


def _graph(execution_path: Path, catalog: Path, commit: str) -> dict:
    execution = yaml.safe_load(execution_path.read_text())
    return compose_catalog(
        execution, load_provider_catalog(catalog), plan_commit=commit
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    graph = commands.add_parser("generate-graph")
    graph.add_argument("--execution", type=Path, required=True)
    graph.add_argument("--plan-commit", required=True)
    graph.add_argument("--catalog", type=Path, required=True)
    graph.add_argument("--output", type=Path, required=True)
    graph.add_argument("--runtime-source-tree", type=Path, required=True)

    tree = commands.add_parser("generate-luet-tree")
    tree.add_argument("--graph", type=Path, required=True)
    tree.add_argument("--output", type=Path, required=True)
    tree.add_argument("--runtime-source-tree", type=Path, required=True)

    verify = commands.add_parser("verify-solution")
    verify.add_argument("--solution", type=Path, required=True)
    verify.add_argument("--plan-commit", required=True)
    verify.add_argument("--runtime-source-tree", type=Path, required=True)

    solution = commands.add_parser("generate-solution")
    solution.add_argument("--graph", type=Path, required=True)
    solution.add_argument("--luet", type=Path, required=True)
    solution.add_argument("--output", type=Path, required=True)
    solution.add_argument("--runtime-source-tree", type=Path, required=True)

    receipt = commands.add_parser("derive-luet-receipt")
    receipt.add_argument("--graph", type=Path, required=True)
    receipt.add_argument("--luet", type=Path, required=True)
    receipt.add_argument("--luet-sha256", required=True)
    receipt.add_argument("--source-commit", required=True)
    receipt.add_argument("--source-tree", required=True)
    receipt.add_argument("--runtime-source-tree", type=Path, required=True)

    args = parser.parse_args()
    if not args.runtime_source_tree.is_dir():
        raise SystemExit("protected runtime source tree absent")
    if args.command == "generate-graph":
        value = _graph(args.execution, args.catalog, args.plan_commit)
        args.output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        return 0
    if args.command == "generate-luet-tree":
        graph_data = json.loads(args.graph.read_text())
        graph_value = CapabilityGraph.from_mapping(
            graph_data, expected_plan_commit=graph_data["plan_commit"]
        )
        providers = _unique_closure(graph_value)
        if args.output.exists():
            shutil.rmtree(args.output)
        _write_tree(args.output, providers, graph_value)
        return 0
    if args.command == "verify-solution":
        solution = json.loads(args.solution.read_text())
        validate_for_dispatch(solution, current_plan_commit=args.plan_commit)
        print(json.dumps({"solution_hash": solution["solution_hash"], "status": "PASS"}, sort_keys=True))
        return 0
    if args.command == "generate-solution":
        graph_data = json.loads(args.graph.read_text())
        conformance = conform(
            graph_data, luet_binary=args.luet,
            expected_plan_commit=graph_data["plan_commit"],
        )
        value = solve(
            graph_data, expected_plan_commit=graph_data["plan_commit"],
            conformance_result=conformance,
        )
        validate_for_dispatch(value, current_plan_commit=graph_data["plan_commit"])
        args.output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        return 0
    if args.command == "derive-luet-receipt":
        graph_data = json.loads(args.graph.read_text())
        result = conform(
            graph_data, luet_binary=args.luet,
            expected_plan_commit=graph_data["plan_commit"],
        )
        value = create_luet_conformance_receipt(
            result, graph=graph_data, plan_commit=graph_data["plan_commit"],
            source_commit=args.source_commit, source_tree=args.source_tree,
            binary_sha256=args.luet_sha256,
        )
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
