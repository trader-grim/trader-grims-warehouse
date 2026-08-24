import json
from pathlib import Path

import pytest
import yaml

from tgw.plan_luet import (
    LUET_VERSION,
    conform,
    load_direct_development_luet_binding,
    verify_direct_development_luet,
    verify_direct_development_solution,
)
from tgw.plan_solver import solve

COMMIT = "fb9fee3e9db756ad0f5071525e943794bf1dab9b"


def graph(*, providers, required):
    capabilities = sorted({item for provider in providers for item in provider.get("provides", [])})
    return {
        "schema": "tgw-plan/v2", "plan_commit": COMMIT,
        "capabilities": capabilities, "providers": providers, "observations": [],
        "target": {"id": "fixture", "profile": "implementation",
                   "minimum_state": "admitted", "required_capabilities": required},
    }


def fake_luet(_binary: Path, tree: Path):
    """Small SAT-shaped fixture runner for generated Luet package trees.

    It resolves exact and ``provides`` references, dependencies, and symmetric
    conflicts.  Production conformance invokes pinned Luet; this lets unit
    tests verify that the generated virtual-package encoding preserves TGW's
    nested alternatives without putting a binary in the test environment.
    """
    definitions = {}
    for path in tree.rglob("definition.yaml"):
        data = yaml.safe_load(path.read_text())
        data["_reference"] = {
            "category": data["category"], "name": data["name"],
            "version": str(data["version"]),
        }
        definitions[_key(data["_reference"])] = data

    def matches(reference, package):
        return _key(reference) == _key(package["_reference"]) or any(
            _key(reference) == _key(provided)
            for provided in package.get("provides", [])
        )

    def conflicts(left, right):
        return any(matches(reference, right) for reference in left.get("conflicts", [])) or any(
            matches(reference, left) for reference in right.get("conflicts", [])
        )

    def candidates(reference):
        return sorted(
            (package for package in definitions.values() if matches(reference, package)),
            key=lambda package: (
                -int(package.get("annotations", {}).get("tgw.preference", 0)),
                _key(package["_reference"]),
            ),
        )

    def satisfy(requirements, selected):
        if not requirements:
            return selected
        reference, *remaining = requirements
        if any(matches(reference, package) for package in selected.values()):
            return satisfy(remaining, selected)
        for package in candidates(reference):
            key = _key(package["_reference"])
            if any(conflicts(package, chosen) for chosen in selected.values()):
                continue
            nested = satisfy(package.get("requires", []), {**selected, key: package})
            if nested is not None:
                resolved = satisfy(remaining, nested)
                if resolved is not None:
                    return resolved
        return None

    target = definitions[("tgw-target", "closure", "1.0")]
    selected = satisfy(target.get("requires", []), {})
    if selected is None:
        raise RuntimeError("fixture Luet solver found no solution")
    return {
        "packages": [package["_reference"] for package in selected.values()],
    }


def _key(reference):
    return reference["category"], reference["name"], str(reference["version"])


def test_direct_development_binding_is_exact_and_is_not_nix_metadata():
    binding = load_direct_development_luet_binding()

    assert LUET_VERSION == "0.9.26"
    assert binding.executable_path == Path("/opt/TGW/tgw-lib/development-tools/luet-0.9.26-g")
    assert binding.plan_commit == "058e2f980201cc78245358e4901cf007063f2c29"
    assert "nix" not in binding.executable_path.name


def test_direct_development_binding_rejects_nix_metadata(tmp_path):
    binding = tmp_path / "binding.json"
    binding.write_text(Path("nix/luet.nix").read_text())

    with pytest.raises(ValueError, match="binding"):
        load_direct_development_luet_binding(binding)


@pytest.mark.parametrize(
    "ephemeral_path",
    [
        "/opt/TGW/var/cache/tgw/t/luet",
        "/opt/TGW/w/example/luet",
        "/opt/TGW/tgw-lib/worktrees/example/luet",
        "/opt/TGW/tgw-lib/releases/example/luet",
        "/opt/TGW/tgw-lib/actor-runtime/current/luet",
        "/home/codex/bin/luet",
    ],
)
def test_direct_development_binding_rejects_ephemeral_or_noncanonical_paths(tmp_path, ephemeral_path):
    binding = json.loads(
        Path("agent-services/catalogs/direct-development-luet-v1.json").read_text(),
    )
    binding["executable_path"] = ephemeral_path
    path = tmp_path / "binding.json"
    path.write_text(json.dumps(binding))

    with pytest.raises(ValueError, match="binding"):
        load_direct_development_luet_binding(path)


def test_direct_development_binding_accepts_the_canonical_durable_executable():
    assert verify_direct_development_luet(
        "/opt/TGW/tgw-lib/development-tools/luet-0.9.26-g",
        plan_commit="058e2f980201cc78245358e4901cf007063f2c29",
    ) == "sha256:c227742324a92eef4767961a9e49f687195b13356881336cc83d006e43d86c87"


def test_direct_development_binding_rejects_wrong_executable(tmp_path):
    wrong = tmp_path / "luet"
    wrong.write_text("#!/bin/sh\necho 'luet version 0.9.26-g'\n")
    wrong.chmod(0o755)

    with pytest.raises(ValueError, match="direct-development binding"):
        verify_direct_development_luet(
            wrong,
            plan_commit="058e2f980201cc78245358e4901cf007063f2c29",
        )


def test_direct_development_binding_rejects_a_tampered_solution_identity():
    with pytest.raises(ValueError, match="resolved solution"):
        verify_direct_development_solution(
            {
                "plan_commit": "058e2f980201cc78245358e4901cf007063f2c29",
                "solution_hash": "sha256:" + "0" * 64,
            },
        )


def test_representable_unique_closure_agrees_with_native(tmp_path):
    document = graph(
        providers=[
            {"id": "app", "provides": ["app@1"], "requires": ["queue@1"]},
            {"id": "queue", "provides": ["queue@1"]},
        ], required=["app@1"],
    )
    binary = tmp_path / "luet"
    binary.write_text("pinned fixture")
    result = conform(document, luet_binary=binary, runner=fake_luet)
    native = solve(document)
    assert result["status"] == "AGREEMENT"
    assert result["selected_providers"] == ["app", "queue"]
    assert result["closure_hash"] == native["closure_hash"]


def test_multiple_provider_choice_is_a_luet_virtual_package_alternative(tmp_path):
    document = graph(
        providers=[
            {"id": "a", "provides": ["app@1"]},
            {"id": "b", "provides": ["app@1"]},
        ], required=["app@1"],
    )
    binary = tmp_path / "luet"
    binary.write_text("pinned fixture")
    result = conform(document, luet_binary=binary, runner=fake_luet)
    assert result["status"] == "AGREEMENT"
    assert result["selected_providers"] == ["a"]


def test_nested_any_agrees_through_selector_packages(tmp_path):
    document = graph(
        providers=[
            {"id": "a", "provides": ["a@1"]},
            {"id": "b", "provides": ["b@1"]},
            {"id": "c", "provides": ["c@1"]},
        ],
        required={"any": [{"all": ["a@1", {"any": ["b@1", "c@1"]}]}, "c@1"]},
    )
    binary = tmp_path / "luet"
    binary.write_text("pinned fixture")
    result = conform(document, luet_binary=binary, runner=fake_luet)
    native = solve(document)
    assert result["status"] == "AGREEMENT"
    assert result["selected_providers"] == native["selected_providers"] == ["a", "b"]
    assert result["closure_hash"] == native["closure_hash"]


def test_preference_is_translated_and_mismatch_remains_fail_closed(tmp_path):
    document = graph(
        providers=[
            {"id": "preferred", "provides": ["app@1"], "preference": 10},
            {"id": "fallback", "provides": ["app@1"]},
        ],
        required=["app@1"],
    )
    binary = tmp_path / "luet"
    binary.write_text("pinned fixture")
    agreed = conform(document, luet_binary=binary, runner=fake_luet)
    assert agreed["status"] == "AGREEMENT"
    assert agreed["selected_providers"] == ["preferred"]

    def lower_ranked_runner(_binary, tree):
        for path in tree.glob("tgw-provider/*/1.0/definition.yaml"):
            definition = yaml.safe_load(path.read_text())
            if definition["annotations"]["tgw.preference"] == "0":
                return {"packages": [{
                    "category": definition["category"], "name": definition["name"],
                    "version": str(definition["version"]),
                }]}
        raise AssertionError("fixture did not contain a lower-ranked provider")

    disagreement = conform(document, luet_binary=binary, runner=lower_ranked_runner)
    assert disagreement["available"] is True
    assert disagreement["status"] == "DISAGREEMENT"
    assert disagreement["selected_providers"] == ["fallback"]


def test_global_non_greedy_alternative_agrees_with_native(tmp_path):
    document = graph(
        providers=[
            {"id": "app-high", "provides": ["app@1"], "requires": ["db@1"], "preference": 100},
            {"id": "app-low", "provides": ["app@1"], "preference": 10},
            {"id": "db", "provides": ["db@1"], "conflicts": ["exclusive@1"]},
            {"id": "exclusive", "provides": ["exclusive@1"]},
        ],
        required=["app@1", "exclusive@1"],
    )
    binary = tmp_path / "luet"
    binary.write_text("pinned fixture")
    result = conform(document, luet_binary=binary, runner=fake_luet)
    native = solve(document)
    assert result["status"] == "AGREEMENT"
    assert result["selected_providers"] == native["selected_providers"] == ["app-low", "exclusive"]
    assert result["closure_hash"] == native["closure_hash"]


def test_missing_pinned_binary_is_unavailable(tmp_path):
    document = graph(providers=[{"id": "a", "provides": ["a@1"]}], required=["a@1"])
    result = conform(document, luet_binary=tmp_path / "absent")
    assert result["available"] is False
    assert result["status"] == "UNAVAILABLE"
