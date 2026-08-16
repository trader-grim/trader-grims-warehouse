import json
from pathlib import Path

import pytest

from tgw.candidate_manifest import CandidateManifestError, create_luet_conformance_receipt, verify_luet_conformance_receipt
from tgw.plan_luet import PINNED_LUET_BINARY_SHA256, PROVIDER_ID, normalize_conformance_graph, verify_pinned_luet_binary


def test_conformance_script_normalizes_the_canonical_provider_catalog():
    catalog = json.loads(
        (Path(__file__).resolve().parents[1] / "agent-services/catalogs/governed-execution-platform-v1.json").read_text()
    )

    graph = normalize_conformance_graph(catalog)

    assert graph["schema"] == "tgw-plan/v2"
    assert graph["target"] == {
        "id": catalog["plan_id"],
        "profile": "production",
        "minimum_state": "operationally_verified",
        "required_capabilities": catalog["capabilities"],
    }


def test_live_agreement_becomes_hash_bound_persistable_receipt():
    graph = {"schema": "tgw-plan/v2", "plan_commit": "plan"}
    result = {"provider_id": PROVIDER_ID, "available": True, "status": "AGREEMENT", "closure_hash": "sha256:closure", "selected_providers": ["a"]}
    receipt = create_luet_conformance_receipt(result, graph=graph, plan_commit="plan", source_commit="c" * 40, source_tree="t" * 40, binary_sha256=PINNED_LUET_BINARY_SHA256)
    verified = verify_luet_conformance_receipt(receipt, graph=graph, plan_commit="plan", closure_hash="sha256:closure", source_commit="c" * 40, source_tree="t" * 40)
    assert verified == receipt
    json.dumps(receipt)


@pytest.mark.parametrize("status", ["DISAGREEMENT", "UNAVAILABLE", "UNREPRESENTABLE"])
def test_nonagreement_can_never_be_persisted_as_conformance(status):
    with pytest.raises(CandidateManifestError, match="does not prove agreement"):
        create_luet_conformance_receipt(
            {"provider_id": PROVIDER_ID, "available": status == "DISAGREEMENT", "status": status},
            graph={}, plan_commit="p", source_commit="c", source_tree="t",
            binary_sha256=PINNED_LUET_BINARY_SHA256,
        )


def test_operational_binary_pin_rejects_a_version_compatible_wrong_executable(tmp_path):
    binary = tmp_path / "luet"
    binary.write_text("#!/bin/sh\necho 'luet version 0.9.26'\n")
    binary.chmod(0o755)
    with pytest.raises(ValueError, match="pinned executable hash"):
        verify_pinned_luet_binary(binary)
