"""Read-only CLI for TGW Steward current context and recovery acceptance."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from tgw.agent_contract import AgentContractError
from tgw.environment_recovery_acceptance import (
    RecoveryAcceptanceError,
    audit_environment_recovery,
)
from tgw.environment_registry import EnvironmentRegistryError
from tgw.steward_context import StewardContextError, answer_steward_query


def _mapping(path: Path, *, yaml_format: bool) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
        value = yaml.safe_load(text) if yaml_format else json.loads(text)
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise RecoveryAcceptanceError(f"cannot load structured input {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RecoveryAcceptanceError(f"structured input must be a mapping: {path}")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tgw-environment-recovery")
    sub = parser.add_subparsers(dest="operation", required=True)
    query = sub.add_parser("query")
    query.add_argument("--registry", required=True, type=Path)
    query.add_argument("--contract", required=True, type=Path)
    query.add_argument("--kind", required=True, choices=("host", "repository", "authority", "historical-reference"))
    query.add_argument("--identity", required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("--root", required=True, type=Path)
    audit.add_argument("--observed-at", required=True)
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.operation == "query":
        return answer_steward_query(
            _mapping(args.registry, yaml_format=True),
            _mapping(args.contract, yaml_format=False),
            {"schema": "tgw-steward-query/v1", "kind": args.kind, "identity": args.identity},
        )
    return audit_environment_recovery(args.root, observed_at=args.observed_at)


def main() -> int:
    try:
        result = run(_parser().parse_args())
    except (
        AgentContractError,
        EnvironmentRegistryError,
        RecoveryAcceptanceError,
        StewardContextError,
    ) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
