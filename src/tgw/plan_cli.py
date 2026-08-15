"""Standalone, local-only command line for governed ``tgw-plan/v1`` data."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tgw.workflow.standalone_plan import (
    PlanValidationError,
    compile_plan,
    completion_candidate,
    load_plan,
    persist_artifact,
    status,
    validate_plan,
)


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _receipts(directory: Path | None) -> list[dict[str, Any]]:
    if directory is None or not directory.exists():
        return []
    return [_json(path) for path in sorted(directory.glob("*.json"))]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tgw-plan")
    sub = parser.add_subparsers(dest="operation", required=True)
    for operation in ("validate", "compile", "status", "completion-candidate"):
        command = sub.add_parser(operation)
        command.add_argument("plan", type=Path)
        command.add_argument("--registry", required=True, type=Path)
        command.add_argument("--repository-binding", required=True, type=Path)
        command.add_argument("--evidence", type=Path)
        command.add_argument("--store", type=Path)
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    plan = load_plan(args.plan)
    registry = _json(args.registry)
    validate_plan(plan, registry)
    graph = compile_plan(plan, registry, _json(args.repository_binding))
    if args.operation == "validate":
        return {"ok": True, "schema": plan.metadata["schema"], "plan_id": plan.metadata["plan_id"], "version": plan.metadata["version"], "scope_hash": plan.scope_hash}
    artifact: dict[str, Any]
    if args.operation == "compile":
        artifact = graph
    elif args.operation == "status":
        artifact = status(graph, _receipts(args.evidence))
    else:
        now = datetime.now(timezone.utc)
        artifact = completion_candidate(
            graph, _receipts(args.evidence), created_at=now.isoformat(),
            expires_at=(now + timedelta(days=7)).isoformat(),
        )
    if args.store is not None:
        persisted = persist_artifact(args.store, artifact)
        return {"artifact": artifact, "persisted": str(persisted)}
    return artifact


def main() -> int:
    try:
        result = run(_parser().parse_args())
    except (OSError, json.JSONDecodeError, PlanValidationError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
