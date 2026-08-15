"""Pinned Luet conformance provider for the representable TGW graph subset.

Luet validates package dependency/conflict closure.  TGW remains responsible
for typed state, diagnostics, preference ranking, and canonical solution form.
The adapter refuses graphs whose semantics cannot be preserved exactly.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping

from tgw.plan_solver import CapabilityGraph, PlanResolutionError, Requirement, solve

LUET_VERSION = "0.9.26"
LUET_REVISION = "48f17dbc7a9edb94b1415a2eeeac4e5c2d45f5d3"
PROVIDER_ID = f"luet-pinned-{LUET_VERSION}@1"


def _package_name(identity: str) -> str:
    readable = re.sub(r"[^a-z0-9]+", "-", identity.lower()).strip("-")[:40]
    digest = hashlib.sha256(identity.encode()).hexdigest()[:12]
    return f"{readable or 'provider'}-{digest}"


def _leaf_requirements(requirement: Requirement) -> set[str]:
    if requirement.kind == "capability":
        return {str(requirement.value)}
    if requirement.kind == "any":
        raise PlanResolutionError("LUET_UNREPRESENTABLE: nested any requirement")
    return set().union(*(_leaf_requirements(item) for item in requirement.value))


def _unique_closure(graph: CapabilityGraph) -> list[Any]:
    selected: dict[str, Any] = {}
    pending = sorted(_leaf_requirements(graph.target.requires))
    while pending:
        capability = pending.pop(0)
        if any(capability in provider.provides for provider in selected.values()):
            continue
        providers = [
            provider for provider in graph.providers
            if provider.available and capability in provider.provides
        ]
        if len(providers) != 1:
            raise PlanResolutionError(
                f"LUET_UNREPRESENTABLE: capability {capability} has {len(providers)} available providers"
            )
        provider = providers[0]
        if provider.preference:
            raise PlanResolutionError(
                f"LUET_UNREPRESENTABLE: provider preference on {provider.id}"
            )
        selected[provider.id] = provider
        pending.extend(sorted(_leaf_requirements(provider.requires)))
        pending = sorted(set(pending))
    return [selected[key] for key in sorted(selected)]


def _yaml_package(provider: Any, owners: Mapping[str, str]) -> str:
    lines = [
        'category: "tgw-provider"',
        f'name: "{_package_name(provider.id)}"',
        'version: "1.0"',
    ]
    dependencies = sorted({owners[item] for item in _leaf_requirements(provider.requires)})
    if dependencies:
        lines.append("requires:")
        for dependency in dependencies:
            lines.extend([
                '- category: "tgw-provider"',
                f'  name: "{_package_name(dependency)}"',
                '  version: "1.0"',
            ])
    conflicts = sorted(item for item in provider.conflicts if item in owners or item in owners.values())
    if conflicts:
        lines.append("conflicts:")
        for conflict in conflicts:
            owner = owners.get(conflict, conflict)
            lines.extend([
                '- category: "tgw-provider"',
                f'  name: "{_package_name(owner)}"',
                '  version: "1.0"',
            ])
    return "\n".join(lines) + "\n"


def _write_tree(root: Path, providers: list[Any], graph: CapabilityGraph) -> dict[str, str]:
    owners = {
        capability: provider.id
        for provider in providers for capability in provider.provides
    }
    package_to_provider: dict[str, str] = {}
    for provider in providers:
        package = _package_name(provider.id)
        package_to_provider[package] = provider.id
        directory = root / "tgw-provider" / package / "1.0"
        directory.mkdir(parents=True)
        (directory / "definition.yaml").write_text(_yaml_package(provider, owners))
        (directory / "build.yaml").write_text("\n")
    target = root / "tgw-target" / "closure" / "1.0"
    target.mkdir(parents=True)
    target_lines = ['category: "tgw-target"', 'name: "closure"', 'version: "1.0"', "requires:"]
    for provider in providers:
        target_lines.extend([
            '- category: "tgw-provider"',
            f'  name: "{_package_name(provider.id)}"',
            '  version: "1.0"',
        ])
    (target / "definition.yaml").write_text("\n".join(target_lines) + "\n")
    (target / "build.yaml").write_text("\n")
    return package_to_provider


def _run_luet(binary: Path, tree: Path) -> Mapping[str, Any]:
    proc = subprocess.run(
        [str(binary), "tree", "pkglist", "--tree", str(tree), "--deps",
         "--matches", "^tgw-target/closure$", "--output", "json"],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "NO_COLOR": "1"},
    )
    if proc.returncode:
        raise PlanResolutionError(f"Luet failed ({proc.returncode}): {proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise PlanResolutionError("Luet returned non-JSON output") from exc


def conform(
    graph_data: Mapping[str, Any], *, luet_binary: Path | str,
    expected_plan_commit: str | None = None,
    runner: Callable[[Path, Path], Mapping[str, Any]] = _run_luet,
) -> dict[str, Any]:
    """Return a ConformanceResult mapping; never claim unsupported agreement."""
    graph = CapabilityGraph.from_mapping(graph_data, expected_plan_commit=expected_plan_commit)
    native = solve(graph_data, expected_plan_commit=expected_plan_commit)
    try:
        providers = _unique_closure(graph)
    except PlanResolutionError as exc:
        return {
            "provider_id": PROVIDER_ID, "available": False, "closure_hash": None,
            "status": "UNREPRESENTABLE", "reason": str(exc),
        }
    binary = Path(luet_binary)
    if not binary.is_file():
        return {
            "provider_id": PROVIDER_ID, "available": False, "closure_hash": None,
            "status": "UNAVAILABLE", "reason": f"pinned Luet binary absent: {binary}",
        }
    with tempfile.TemporaryDirectory(prefix="tgw-luet-") as temporary:
        tree = Path(temporary) / "tree"
        package_map = _write_tree(tree, providers, graph)
        result = runner(binary, tree)
    packages = result.get("packages")
    if not isinstance(packages, list):
        raise PlanResolutionError("Luet JSON lacks packages list")
    selected = sorted({
        package_map[item.get("name")]
        for item in packages
        if isinstance(item, Mapping) and item.get("category") == "tgw-provider"
        and item.get("name") in package_map
    })
    expected = sorted(provider.id for provider in providers)
    if selected != expected or selected != native.get("selected_providers"):
        observed_hash = "sha256:" + hashlib.sha256(
            json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return {
            "provider_id": PROVIDER_ID, "available": True,
            "closure_hash": observed_hash, "status": "DISAGREEMENT",
            "selected_providers": selected,
        }
    return {
        "provider_id": PROVIDER_ID, "available": True,
        "closure_hash": native["closure_hash"], "status": "AGREEMENT",
        "selected_providers": selected,
    }
