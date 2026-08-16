"""Opt-in source-level conformance checks against the pinned Luet binary.

Set ``TGW_LUET_BINARY`` to an explicitly provisioned Luet executable to run
these tests.  The repository deliberately does not discover binaries from a
recovery location or ambient ``PATH``.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tgw.plan_luet import LUET_VERSION, PINNED_LUET_BINARY_SHA256, conform
from tgw.plan_solver import solve

pytestmark = pytest.mark.real_luet

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "fb9fee3e9db756ad0f5071525e943794bf1dab9b"


@pytest.fixture(scope="module")
def real_luet_binary() -> Path:
    configured = os.environ.get("TGW_LUET_BINARY")
    if not configured:
        pytest.skip("set TGW_LUET_BINARY to run real Luet conformance tests")
    binary = Path(configured).expanduser()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        pytest.fail("TGW_LUET_BINARY must name an executable file")
    actual_hash = "sha256:" + hashlib.sha256(binary.read_bytes()).hexdigest()
    assert actual_hash == PINNED_LUET_BINARY_SHA256
    version = subprocess.run(
        [str(binary), "--version"], capture_output=True, text=True, timeout=10, check=False
    )
    assert version.returncode == 0, version.stderr
    assert f"luet version {LUET_VERSION}" in (version.stdout + version.stderr).lower()
    return binary


def graph(*, providers: list[dict[str, Any]], required: Any) -> dict[str, Any]:
    capabilities = sorted(
        {capability for provider in providers for capability in provider.get("provides", [])}
    )
    return {
        "schema": "tgw-plan/v2",
        "plan_commit": COMMIT,
        "capabilities": capabilities,
        "providers": providers,
        "observations": [],
        "target": {
            "id": "real-luet-fixture",
            "profile": "implementation",
            "minimum_state": "admitted",
            "required_capabilities": required,
        },
    }


def canonical_graph() -> dict[str, Any]:
    catalog = json.loads(
        (ROOT / "agent-services/catalogs/governed-execution-platform-v1.json").read_text()
    )
    assert catalog["plan_commit"] == COMMIT
    catalog["schema"] = "tgw-plan/v2"
    catalog["target"] = {
        "id": catalog["plan_id"],
        "profile": "production",
        "minimum_state": catalog["profiles"]["production"]["minimum_state"],
        "required_capabilities": catalog["capabilities"],
    }
    return catalog


def test_real_pinned_luet_agrees_on_canonical_fb9_graph(real_luet_binary: Path):
    document = canonical_graph()

    result = conform(
        document, luet_binary=real_luet_binary, expected_plan_commit=COMMIT
    )

    assert result["status"] == "AGREEMENT"
    assert result["selected_providers"] == solve(document)["selected_providers"]
    assert result["closure_hash"] == "sha256:bc0c53b2574fc359c629bd213e078fdd2824e5e1c4a98c0c7a347de869d9e6f8"


def test_real_pinned_luet_agrees_on_global_non_greedy_choice(real_luet_binary: Path):
    document = graph(
        providers=[
            {"id": "app-high", "provides": ["app@1"], "requires": ["db@1"], "preference": 100},
            {"id": "app-low", "provides": ["app@1"], "preference": 10},
            {"id": "db", "provides": ["db@1"], "conflicts": ["exclusive@1"]},
            {"id": "exclusive", "provides": ["exclusive@1"]},
        ],
        required=["app@1", "exclusive@1"],
    )

    result = conform(document, luet_binary=real_luet_binary)

    assert result["status"] == "AGREEMENT"
    assert result["selected_providers"] == ["app-low", "exclusive"]
    assert result["closure_hash"] == solve(document)["closure_hash"]


def test_real_pinned_luet_multiple_provider_difference_is_fail_closed(real_luet_binary: Path):
    document = graph(
        providers=[
            {"id": "a", "provides": ["app@1"]},
            {"id": "b", "provides": ["app@1"]},
        ],
        required=["app@1"],
    )

    result = conform(document, luet_binary=real_luet_binary)

    assert result["status"] == "DISAGREEMENT"
    assert result["selected_providers"] == ["b"]
    assert result["closure_hash"] != solve(document)["closure_hash"]


def test_real_pinned_luet_nested_alternative_difference_is_fail_closed(real_luet_binary: Path):
    document = graph(
        providers=[
            {"id": "a", "provides": ["a@1"]},
            {"id": "b", "provides": ["b@1"]},
            {"id": "c", "provides": ["c@1"]},
        ],
        required={"any": [{"all": ["a@1", {"any": ["b@1", "c@1"]}]}, "c@1"]},
    )

    result = conform(document, luet_binary=real_luet_binary)

    assert result["status"] == "DISAGREEMENT"
    assert result["selected_providers"] == ["c"]
    assert result["closure_hash"] != solve(document)["closure_hash"]
