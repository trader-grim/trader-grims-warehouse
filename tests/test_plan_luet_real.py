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
import yaml

from tgw.plan_catalog import compose_catalog
from tgw.plan_luet import LUET_VERSION, PINNED_LUET_BINARY_SHA256, conform
from tgw.plan_solver import solve

pytestmark = pytest.mark.real_luet

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "058e2f980201cc78245358e4901cf007063f2c29"
PLAN_ROOT = Path("/opt/TGW/library/plans")


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
    execution = yaml.safe_load(subprocess.check_output(
        ["git", "-C", str(PLAN_ROOT), "show", f"{COMMIT}:plan/execution/GOVERNED-EXECUTION-PLATFORM-v1.yaml"],
        text=True,
    ))
    assert catalog["plan_commit"] == COMMIT
    return compose_catalog(execution, catalog, plan_commit=COMMIT)


def test_real_pinned_luet_agrees_on_current_approved_plan_graph(real_luet_binary: Path):
    document = canonical_graph()

    result = conform(
        document, luet_binary=real_luet_binary, expected_plan_commit=COMMIT
    )

    assert result["status"] == "AGREEMENT"
    assert result["selected_providers"] == solve(document)["selected_providers"]
    assert result["closure_hash"] == "sha256:16db00efe71a3c84d27faf012e58e5e664abe47e7eece40f2436dd125943f7bb"


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
