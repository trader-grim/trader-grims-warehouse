#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tgw.candidate_manifest import MigrationSafetyReceipt, build_candidate_manifest
from tgw.logging import announce_script_run


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--base-commit", required=True)
    parser.add_argument("--predecessor-release-manifest", type=Path, required=True)
    parser.add_argument("--plan-commit", required=True)
    parser.add_argument("--solution-hash", required=True)
    parser.add_argument("--closure-hash", required=True)
    parser.add_argument("--focused-receipt", type=Path, required=True)
    parser.add_argument("--full-suite-receipt", type=Path, required=True)
    parser.add_argument("--focused-output-artifact", type=Path, required=True)
    parser.add_argument("--full-suite-output-artifact", type=Path, required=True)
    parser.add_argument(
        "--migration-receipt", type=Path, action="append", default=[],
        help="one independently verified executable database-migration receipt; repeat per migration",
    )
    parser.add_argument("--graph", type=Path)
    parser.add_argument("--luet-conformance-receipt", type=Path)
    args = parser.parse_args()
    announce_script_run(
        "build_candidate_manifest.py",
        "build an immutable, hash-bound integrated candidate manifest",
        candidate=args.commit,
        plan_commit=args.plan_commit,
    )
    focused = json.loads(args.focused_receipt.read_text())
    full_suite = json.loads(args.full_suite_receipt.read_text())
    focused_output = json.loads(args.focused_output_artifact.read_text())
    full_suite_output = json.loads(args.full_suite_output_artifact.read_text())
    predecessor = json.loads(args.predecessor_release_manifest.read_text())
    migrations = [
        MigrationSafetyReceipt(**json.loads(path.read_text()))
        for path in args.migration_receipt
    ]
    graph = json.loads(args.graph.read_text()) if args.graph else None
    conformance = json.loads(args.luet_conformance_receipt.read_text()) if args.luet_conformance_receipt else None
    manifest = build_candidate_manifest(
        args.repo,
        commit=args.commit,
        base_commit=args.base_commit,
        predecessor_release=predecessor,
        plan_commit=args.plan_commit,
        solution_hash=args.solution_hash,
        closure_hash=args.closure_hash,
        focused_receipt=focused,
        full_suite_receipt=full_suite,
        focused_output_artifact=focused_output,
        full_suite_output_artifact=full_suite_output,
        graph=graph,
        conformance_receipt=conformance,
        migration_receipts=migrations,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
