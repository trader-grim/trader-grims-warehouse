import json

import pytest

from tgw.candidate_manifest import CandidateManifestError, create_luet_conformance_receipt, verify_luet_conformance_receipt
from tgw.plan_luet import PROVIDER_ID


def test_live_agreement_becomes_hash_bound_persistable_receipt():
    graph = {"schema": "tgw-plan/v2", "plan_commit": "plan"}
    result = {"provider_id": PROVIDER_ID, "available": True, "status": "AGREEMENT", "closure_hash": "sha256:closure", "selected_providers": ["a"]}
    receipt = create_luet_conformance_receipt(result, graph=graph, plan_commit="plan", source_commit="c" * 40, source_tree="t" * 40, binary_sha256="sha256:" + "b" * 64)
    verified = verify_luet_conformance_receipt(receipt, graph=graph, plan_commit="plan", closure_hash="sha256:closure", source_commit="c" * 40, source_tree="t" * 40)
    assert verified == receipt
    json.dumps(receipt)


@pytest.mark.parametrize("status", ["DISAGREEMENT", "UNAVAILABLE", "UNREPRESENTABLE"])
def test_nonagreement_can_never_be_persisted_as_conformance(status):
    with pytest.raises(CandidateManifestError, match="does not prove agreement"):
        create_luet_conformance_receipt(
            {"provider_id": PROVIDER_ID, "available": status == "DISAGREEMENT", "status": status}, graph={}, plan_commit="p", source_commit="c", source_tree="t", binary_sha256="sha256:" + "b" * 64
        )
