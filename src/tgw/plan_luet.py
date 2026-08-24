"""Pinned Luet conformance provider for TGW capability graphs.

The adapter maps capabilities to Luet virtual packages.  A TGW provider is a
real Luet package which ``provides`` every capability it can establish; nested
``all``/``any`` expressions become synthetic selector packages.  This keeps
alternative selection inside the Luet SAT solve instead of preselecting one
provider in Python.  TGW still owns typed diagnostics, observed state, and the
canonical preference ranking; a Luet closure which differs from that ranking
is a disagreement and holds dispatch.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from tgw.plan_solver import CapabilityGraph, PlanResolutionError, Requirement, solve

LUET_REVISION = "48f17dbc7a9edb94b1415a2eeeac4e5c2d45f5d3"
_DIRECT_DEVELOPMENT_BINDING = (
    Path(__file__).resolve().parents[2]
    / "agent-services/catalogs/direct-development-luet-v1.json"
)
_VERSION = "1.0"
_PROVIDER_CATEGORY = "tgw-provider"
_CAPABILITY_CATEGORY = "tgw-capability"
_SELECTOR_CATEGORY = "tgw-selector"


@dataclass(frozen=True)
class DirectDevelopmentLuetBinding:
    executable_path: Path
    sha256: str
    version: str
    version_output: str
    invocation: tuple[str, ...]
    plan_commit: str
    plan_solution_hash: str


def load_direct_development_luet_binding(
    path: Path | str = _DIRECT_DEVELOPMENT_BINDING,
) -> DirectDevelopmentLuetBinding:
    """Load the source-owned, non-Nix binding for direct-development Luet."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("direct-development Luet binding is unavailable") from exc
    required = {
        "schema", "id", "executable_path", "sha256", "version", "version_output",
        "invocation", "plan_commit", "plan_solution_hash",
    }
    if (
        not isinstance(value, Mapping)
        or set(value) != required
        or value.get("schema") != "tgw-direct-development-luet-binding/v1"
        or value.get("id") != "tgw-lib-direct-development-luet@1"
        or not isinstance(value.get("executable_path"), str)
        or not value["executable_path"].startswith("/opt/TGW/")
        or not isinstance(value.get("sha256"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["sha256"])
        or not isinstance(value.get("version"), str)
        or not isinstance(value.get("version_output"), str)
        or not isinstance(value.get("invocation"), list)
        or value["invocation"] != ["--version"]
        or not isinstance(value.get("plan_commit"), str)
        or not re.fullmatch(r"[0-9a-f]{40}", value["plan_commit"])
        or not isinstance(value.get("plan_solution_hash"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["plan_solution_hash"])
    ):
        raise ValueError("direct-development Luet binding is invalid")
    return DirectDevelopmentLuetBinding(
        executable_path=Path(value["executable_path"]),
        sha256=value["sha256"],
        version=value["version"],
        version_output=value["version_output"],
        invocation=tuple(value["invocation"]),
        plan_commit=value["plan_commit"],
        plan_solution_hash=value["plan_solution_hash"],
    )


_BINDING = load_direct_development_luet_binding()
LUET_VERSION = _BINDING.version
PROVIDER_ID = f"luet-pinned-{LUET_VERSION}@1"
PINNED_LUET_BINARY_SHA256 = _BINDING.sha256


def normalize_conformance_graph(document: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize a checked-in provider catalog to the solver graph shape.

    The raw catalog remains an immutable input identity.  This conversion is
    shared by receipt production and verification so neither side can silently
    hash a different representation of the same declared fixture.
    """
    if not isinstance(document, Mapping):
        raise ValueError("Luet conformance graph must be a JSON object")
    if document.get("schema") == "tgw-plan/v2":
        return dict(document)
    required = {
        "schema", "id", "plan_id", "plan_commit", "profiles", "capabilities",
        "providers", "observations",
    }
    if document.get("schema") != "tgw-plan-provider-catalog/v1" or set(document) != required:
        raise ValueError("Luet conformance input is neither a capability graph nor provider catalog")
    profiles = document["profiles"]
    if not isinstance(profiles, Mapping) or set(profiles) != {"production"}:
        raise ValueError("provider catalog lacks the production profile")
    production = profiles["production"]
    if not isinstance(production, Mapping) or set(production) != {"minimum_state"}:
        raise ValueError("provider catalog production profile is invalid")
    capabilities = document["capabilities"]
    if not isinstance(capabilities, list) or not all(isinstance(item, str) and item for item in capabilities):
        raise ValueError("provider catalog capabilities are invalid")
    return {
        **document,
        "schema": "tgw-plan/v2",
        "target": {
            "id": document["plan_id"],
            "profile": "production",
            "minimum_state": production["minimum_state"],
            "required_capabilities": capabilities,
        },
    }


def pinned_luet_binary_sha256(binary: Path | str) -> str:
    """Return the executable content identity, rejecting a non-file input."""
    target = Path(binary)
    if not target.is_file() or not os.access(target, os.X_OK):
        raise ValueError(f"pinned Luet binary is not an executable file: {target}")
    return "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()


def verify_pinned_luet_binary(binary: Path | str) -> str:
    """Compatibility wrapper for the source-owned direct-development binding."""
    return verify_direct_development_luet(binary, plan_commit=_BINDING.plan_commit)


def verify_direct_development_luet(
    binary: Path | str,
    *,
    plan_commit: str,
    binding_path: Path | str = _DIRECT_DEVELOPMENT_BINDING,
) -> str:
    """Fail closed unless this is the exact source-bound development executable."""
    binding = load_direct_development_luet_binding(binding_path)
    if plan_commit != binding.plan_commit:
        raise ValueError("direct-development Luet binding does not match the approved Plan commit")
    observed = pinned_luet_binary_sha256(binary)
    target = Path(binary).resolve(strict=True)
    if target != binding.executable_path.resolve(strict=True) or observed != binding.sha256:
        raise ValueError("Luet binary does not match the direct-development binding")
    version = subprocess.run(
        [str(target), *binding.invocation], check=False, capture_output=True, text=True, timeout=10,
    )
    if version.returncode or binding.version_output not in (version.stdout + version.stderr).lower():
        raise ValueError("Luet binary does not report the direct-development binding version")
    return observed


def _package_name(identity: str) -> str:
    readable = re.sub(r"[^a-z0-9]+", "-", identity.lower()).strip("-")[:40]
    digest = hashlib.sha256(identity.encode()).hexdigest()[:12]
    return f"{readable or 'provider'}-{digest}"


def _reference(category: str, name: str) -> dict[str, str]:
    return {"category": category, "name": name, "version": _VERSION}


def _capability_reference(identity: str) -> dict[str, str]:
    return _reference(_CAPABILITY_CATEGORY, _package_name(f"capability:{identity}"))


def _provider_reference(identity: str) -> dict[str, str]:
    return _reference(_PROVIDER_CATEGORY, _package_name(f"provider:{identity}"))


def _reference_key(reference: Mapping[str, str]) -> tuple[str, str, str]:
    return reference["category"], reference["name"], reference["version"]


def _yaml_reference(reference: Mapping[str, str]) -> list[str]:
    return [
        f'- category: {json.dumps(reference["category"])}',
        f'  name: {json.dumps(reference["name"])}',
        f'  version: {json.dumps(reference["version"])}',
    ]


def _yaml_package(
    reference: Mapping[str, str], *, requires: list[dict[str, str]] = (),
    provides: list[dict[str, str]] = (), conflicts: list[dict[str, str]] = (),
    annotations: Mapping[str, str] = (),
) -> str:
    """Render a minimal Luet runtime package definition without YAML aliases."""
    lines = [
        f'category: {json.dumps(reference["category"])}',
        f'name: {json.dumps(reference["name"])}',
        f'version: {json.dumps(reference["version"])}',
    ]
    for field, values in (("requires", requires), ("provides", provides), ("conflicts", conflicts)):
        if values:
            lines.append(f"{field}:")
            for value in values:
                lines.extend(_yaml_reference(value))
    if annotations:
        lines.append("annotations:")
        for key, value in sorted(annotations.items()):
            lines.append(f"  {json.dumps(key)}: {json.dumps(value)}")
    return "\n".join(lines) + "\n"


class _RequirementTranslator:
    """Translate a TGW Boolean requirement tree into Luet virtual packages."""

    def __init__(self) -> None:
        self.packages: list[tuple[dict[str, str], str]] = []
        self._translated: dict[str, dict[str, str]] = {}

    @staticmethod
    def _identity(requirement: Requirement) -> str:
        return json.dumps(requirement.as_data(), sort_keys=True, separators=(",", ":"))

    def reference(self, requirement: Requirement) -> dict[str, str]:
        if requirement.kind == "capability":
            return _capability_reference(str(requirement.value))
        identity = self._identity(requirement)
        if identity in self._translated:
            return self._translated[identity]
        provided = _reference(_SELECTOR_CATEGORY, _package_name(f"requirement:{identity}"))
        self._translated[identity] = provided
        children = tuple(requirement.value)
        if requirement.kind == "all":
            definition = _yaml_package(
                _reference(_SELECTOR_CATEGORY, _package_name(f"all:{identity}")),
                requires=[self.reference(child) for child in children], provides=[provided],
            )
            self.packages.append((
                _reference(_SELECTOR_CATEGORY, _package_name(f"all:{identity}")), definition,
            ))
            return provided
        if requirement.kind != "any":  # Requirement is validated by plan_solver.
            raise PlanResolutionError(f"unsupported requirement kind: {requirement.kind}")
        for index, child in enumerate(children):
            option = _reference(_SELECTOR_CATEGORY, _package_name(f"any:{identity}:{index}"))
            self.packages.append((
                option,
                _yaml_package(option, requires=[self.reference(child)], provides=[provided]),
            ))
        return provided


def _conflict_reference(identity: str, provider_ids: set[str]) -> dict[str, str]:
    """Keep the native provider-ID and capability conflict namespaces distinct."""
    if identity in provider_ids:
        return _provider_reference(identity)
    return _capability_reference(identity)


def _write_definition(root: Path, reference: Mapping[str, str], definition: str) -> None:
    directory = root / reference["category"] / reference["name"] / reference["version"]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "definition.yaml").write_text(definition)
    (directory / "build.yaml").write_text("\n")


def _write_tree(root: Path, graph: CapabilityGraph) -> dict[str, str]:
    """Write the complete graph, preserving alternatives for Luet to solve."""
    translator = _RequirementTranslator()
    package_to_provider: dict[str, str] = {}
    provider_ids = {provider.id for provider in graph.providers}
    provider_definitions: list[tuple[dict[str, str], str]] = []
    for provider in graph.providers:
        # Blocked/rejected providers have no selectable package.  TGW continues
        # to report their typed status independently of the SAT result.
        if not provider.available:
            continue
        reference = _provider_reference(provider.id)
        package_to_provider[reference["name"]] = provider.id
        provider_definitions.append((
            reference,
            _yaml_package(
                reference,
                requires=[translator.reference(provider.requires)],
                provides=[_capability_reference(capability) for capability in sorted(provider.provides)],
                conflicts=[_conflict_reference(conflict, provider_ids) for conflict in sorted(provider.conflicts)],
                annotations={
                    "tgw.provider_id": provider.id,
                    "tgw.preference": str(provider.preference),
                },
            ),
        ))
    target = _reference("tgw-target", "closure")
    # Building provider expressions can create synthetic packages lazily, so
    # create the target only after all provider dependency trees are traversed.
    target_definition = _yaml_package(target, requires=[translator.reference(graph.target.requires)])
    for reference, definition in [*translator.packages, *provider_definitions]:
        _write_definition(root, reference, definition)
    _write_definition(root, target, target_definition)
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


def _selected_provider_ids(result: Mapping[str, Any], package_map: Mapping[str, str]) -> list[str]:
    packages = result.get("packages")
    if not isinstance(packages, list):
        raise PlanResolutionError("Luet JSON lacks packages list")
    return sorted({
        package_map[item.get("name")]
        for item in packages
        if isinstance(item, Mapping) and item.get("category") == _PROVIDER_CATEGORY
        and item.get("name") in package_map
    })


def conform(
    graph_data: Mapping[str, Any], *, luet_binary: Path | str,
    expected_plan_commit: str | None = None,
    runner: Callable[[Path, Path], Mapping[str, Any]] = _run_luet,
) -> dict[str, Any]:
    """Run the full translated closure and compare it to TGW's exact result."""
    graph = CapabilityGraph.from_mapping(graph_data, expected_plan_commit=expected_plan_commit)
    native = solve(graph_data, expected_plan_commit=expected_plan_commit)
    binary = Path(luet_binary)
    if not binary.is_file():
        return {
            "provider_id": PROVIDER_ID, "available": False, "closure_hash": None,
            "status": "UNAVAILABLE", "reason": f"pinned Luet binary absent: {binary}",
        }
    try:
        with tempfile.TemporaryDirectory(prefix="tgw-luet-") as temporary:
            tree = Path(temporary) / "tree"
            package_map = _write_tree(tree, graph)
            selected = _selected_provider_ids(runner(binary, tree), package_map)
    except PlanResolutionError as exc:
        return {
            "provider_id": PROVIDER_ID, "available": True, "closure_hash": None,
            "status": "UNSATISFIABLE", "reason": str(exc),
        }
    expected = native.get("selected_providers", [])
    if selected != expected or not native.get("complete"):
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
