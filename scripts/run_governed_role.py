#!/usr/bin/env python3
"""Dispatch one governed role through a registered resource service."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from tgw.execution_resources import (
    HTTPRegisteredResourceResolver,
    ResourceVerificationError,
    load_resource_service_catalog,
    verify_resource_service_registration,
)
from tgw.governed_coding import GovernedCodingError, dispatch_role
from tgw.harness_registry import ProviderRegistryError, load_registry, observe_health
from tgw.logging import announce_script_run


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _adapters(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if not separator or not name or not raw_path or name in result:
            raise ValueError("each adapter must be a unique name=path binding")
        result[name] = Path(raw_path).resolve()
    if not result:
        raise ValueError("at least one adapter binding is required")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(prog="run-governed-role")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--coding-config", type=Path, required=True)
    parser.add_argument("--card-template", type=Path, required=True)
    parser.add_argument("--resource-service", type=Path, required=True)
    parser.add_argument("--resource-service-catalog", type=Path, required=True)
    parser.add_argument("--adapter", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--role", choices=("implementation", "independent-review", "controller-verification"), required=True)
    parser.add_argument("--execution-identity", required=True)
    parser.add_argument("--required-capability", action="append", default=[])
    parser.add_argument("--independent-from", action="append", default=[])
    args = parser.parse_args()
    try:
        registry = load_registry(args.registry)
        coding_config = _object(args.coding_config, "coding config")
        card_template = _object(args.card_template, "card template")
        resource_service = _object(args.resource_service, "registered resource service")
        resolver = HTTPRegisteredResourceResolver.from_descriptor(resource_service)
        resource_service = verify_resource_service_registration(
            load_resource_service_catalog(args.resource_service_catalog),
            resource_service,
            resolver=resolver,
        )
        adapters = _adapters(args.adapter)
        health = observe_health(registry, coding_config=coding_config, adapters=adapters)
        announce_script_run(
            "run_governed_role.py",
            "dispatch one capability-qualified governed role with registered resource retrieval",
            role=args.role,
            execution_identity=args.execution_identity,
        )
        receipt = dispatch_role(
            registry,
            health,
            role=args.role,
            adapters=adapters,
            card_template=card_template,
            execution_identity=args.execution_identity,
            required_capabilities=args.required_capability,
            independent_from=args.independent_from,
            resource_resolver=resolver,
            resource_service=resource_service,
            require_harness_retrieval_attestation=True,
        )
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        return 0 if receipt["status"] == "PASS" else 2
    except (GovernedCodingError, ProviderRegistryError, ResourceVerificationError, ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc), "error_type": type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
