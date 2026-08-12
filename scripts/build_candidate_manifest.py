#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tgw.candidate_manifest import MigrationSafetyReceipt, build_candidate_manifest


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
    args = parser.parse_args()
    focused = json.loads(args.focused_receipt.read_text())
    migration = MigrationSafetyReceipt(**json.loads(args.migration_receipt.read_text())) if args.migration_receipt else None
    manifest = build_candidate_manifest(
        args.repo,
        commit=args.commit,
        base_commit=args.base_commit,
        plan_commit=args.plan_commit,
        solution_hash=args.solution_hash,
        closure_hash=args.closure_hash,
        focused_receipt=focused,
        full_suite=(".venv/bin/pytest", "-q"),
        migration_receipt=migration,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
