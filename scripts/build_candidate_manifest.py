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
    parser.add_argument("--plan-commit", required=True)
    parser.add_argument("--solution-hash", required=True)
    parser.add_argument("--closure-hash", required=True)
    parser.add_argument("--focused-receipt", type=Path, required=True)
    parser.add_argument("--migration-receipt", type=Path)
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
    migration = MigrationSafetyReceipt(**json.loads(args.migration_receipt.read_text())) if args.migration_receipt else None
    graph = json.loads(args.graph.read_text()) if args.graph else None
    conformance = json.loads(args.luet_conformance_receipt.read_text()) if args.luet_conformance_receipt else None
    manifest = build_candidate_manifest(
        args.repo,
        commit=args.commit,
        base_commit=args.base_commit,
        plan_commit=args.plan_commit,
        solution_hash=args.solution_hash,
        closure_hash=args.closure_hash,
        focused_receipt=focused,
        full_suite=(".venv/bin/pytest", "-q"),
        graph=graph,
        conformance_receipt=conformance,
        migration_receipt=migration,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
