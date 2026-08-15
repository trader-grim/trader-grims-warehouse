from pathlib import Path

from tgw.plan_luet import LUET_REVISION, LUET_VERSION, conform
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
    packages = []
    for definition in sorted((tree / "tgw-provider").glob("*/1.0/definition.yaml")):
        name = definition.parts[-3]
        packages.append({"category": "tgw-provider", "name": name, "version": "1.0"})
    return {"packages": packages}


def test_pin_is_exact_and_nix_source_records_same_revision():
    expression = Path("nix/luet.nix").read_text()
    assert LUET_VERSION == "0.9.26"
    assert LUET_REVISION in expression
    assert "sha256-wN2VRYsPdF88Cj73ONh7AYTtowjp/X+EtDzOUYTCLCI=" in expression
    assert "vendorHash = null" in expression


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


def test_multiple_provider_choice_is_truthfully_unrepresentable(tmp_path):
    document = graph(
        providers=[
            {"id": "a", "provides": ["app@1"]},
            {"id": "b", "provides": ["app@1"]},
        ], required=["app@1"],
    )
    result = conform(document, luet_binary=tmp_path / "absent")
    assert result["available"] is False
    assert result["status"] == "UNREPRESENTABLE"
    assert "2 available providers" in result["reason"]


def test_any_and_preference_are_not_reported_as_agreement(tmp_path):
    any_graph = graph(
        providers=[{"id": "a", "provides": ["a@1"]}, {"id": "b", "provides": ["b@1"]}],
        required={"any": ["a@1", "b@1"]},
    )
    preferred = graph(
        providers=[{"id": "a", "provides": ["a@1"], "preference": 10}],
        required=["a@1"],
    )
    assert conform(any_graph, luet_binary=tmp_path / "absent")["status"] == "UNREPRESENTABLE"
    assert conform(preferred, luet_binary=tmp_path / "absent")["status"] == "UNREPRESENTABLE"


def test_missing_pinned_binary_is_unavailable(tmp_path):
    document = graph(providers=[{"id": "a", "provides": ["a@1"]}], required=["a@1"])
    result = conform(document, luet_binary=tmp_path / "absent")
    assert result["available"] is False
    assert result["status"] == "UNAVAILABLE"
