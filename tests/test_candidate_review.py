import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import tgw.governed_review_adapter as governed_adapter
from tgw import execute_candidate_review
from tgw.candidate_review import (
    CandidateReviewError,
    create_review_report,
    create_review_result,
    generate_review_packet,
    validate_review_result,
)
from tgw.execute_candidate_review import REVIEW_LEASE_SECONDS, _card
from tgw.execution_resources import issue_harness_retrieval_attestation
from tgw.harness_registry import load_registry, observe_health
from tgw.review_runner import snapshot_hash

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "agent-services/catalogs/harness-providers-v1.json"
TEST_ATTESTATION_PRIVATE_KEY = Ed25519PrivateKey.generate()


def hash_object(value):
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def manifest():
    return {
        "schema": "tgw-integrated-candidate-manifest/v1",
        "source": {
            "commit": "a" * 40,
            "tree": "b" * 40,
            "archive_sha256": "sha256:" + "c" * 64,
            "base_commit": "d" * 40,
        },
        "plan": {
            "commit": "plan-commit",
            "solution_hash": "sha256:" + "e" * 64,
            "closure_hash": "sha256:" + "f" * 64,
        },
        "tests": {"focused": {"status": "passed"}},
        "candidate_closed": True,
        "installed": False,
    }


def adapters():
    return {
        "tgw-plan": ROOT / "agent-services/skills/tgw-plan",
        "promptcraft": ROOT / "agent-services/providers/promptcraft",
        "promptcraft-card-handoff": ROOT / "agent-services/providers/promptcraft/bin/promptcraft-handoff",
    }


def snapshot(tmp_path):
    path = tmp_path / "candidate"
    path.mkdir()
    (path / "app.py").write_text("answer = 42\n")
    return path


def executable(path, marker=None):
    body = "#!/bin/sh\n"
    if marker:
        body += f"touch {marker}\n"
    body += "exit 0\n"
    path.write_text(body)
    path.chmod(0o755)
    return str(path)


def packet(tmp_path, *, configured=True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    registry = load_registry(REGISTRY)
    command = {}
    marker = tmp_path / "invoked"
    if configured:
        command["harness-review"] = [executable(tmp_path / "review-runner", marker)]
    health = observe_health(registry, coding_config={"commands": command}, adapters=adapters())
    source = snapshot(tmp_path)
    value = generate_review_packet(
        manifest(),
        registry,
        health,
        adapters=adapters(),
        snapshot_ref=source.resolve().as_uri(),
        snapshot_hash=snapshot_hash(source),
    )
    return value, marker


def governed_receipt(packet_value, *, passed):
    card_hash = "sha256:" + "d" * 64
    resource_receipt_hash = "sha256:" + "e" * 64
    resources = {
        name: {"ref": f"test:{name}", "hash": "sha256:" + "c" * 64}
        for name in (
            "plan_input", "plan_commit", "plan_graph", "codegraph_snapshot", "source_tree",
            "execution_environment", "authority_conditions", "candidate_evidence", "receipt_sink",
        )
    }
    handoff_hash = "sha256:" + "b" * 64
    attestation_payload = {
        "schema": "tgw-registered-resource-retrieval-attestation/v3",
        "service_id": "review-service", "run_id": "review-run", "card_hash": card_hash,
        "client_id": "candidate-review-client",
        "role": "independent-review", "execution_identity": "review-context:1",
        "handoff_hash": handoff_hash, "resource_receipt_hash": resource_receipt_hash,
        "resources": resources,
        "attestation_key_id": "candidate-review-test-key-1",
    }
    attestation = issue_harness_retrieval_attestation(
        attestation_payload, signing_private_key=TEST_ATTESTATION_PRIVATE_KEY,
    )
    unsigned = {
        "schema": "tgw-governed-coding-receipt/v1",
        "status": "PASS" if passed else "FAIL",
        "role": "independent-review",
        "selected_provider": packet_value["selected_provider"],
        "execution_identity": "review-context:1",
        "card_hash": card_hash,
        "promptcraft_receipt_hash": "sha256:promptcraft",
        "handoff_hash": handoff_hash,
        "resource_receipt_hash": resource_receipt_hash,
        "harness_resource_receipt_hash": resource_receipt_hash,
        "harness_retrieval_attestation_hash": attestation["attestation_hash"],
        "harness_retrieval_attestation": attestation,
        "resource_service_descriptor_hash": "sha256:service",
        "resource_service_client_id": "candidate-review-client",
        "resource_service_catalog_ref": "catalog:review-service@1",
        "resource_service_catalog_hash": "sha256:catalog",
        "outcome": "satisfied" if passed else "failed",
        "established_conditions": ["reviewed"] if passed else [],
        "artifacts": [],
    }
    return {**unsigned, "receipt_hash": hash_object(unsigned)}


def report(packet_value, *, semantic="PASS", security="PASS"):
    def dimension(verdict, message):
        findings = []
        if verdict == "FAIL":
            findings = [
                {
                    "severity": "high",
                    "path": "app.py",
                    "line": 1,
                    "message": message,
                }
            ]
        return {"verdict": verdict, "findings": findings}

    return create_review_report(
        packet_value,
        {
            "semantic": dimension(semantic, "semantic defect"),
            "security": dimension(security, "security defect"),
        },
    )


def result(packet_value, *, semantic="PASS", security="PASS"):
    review_report = report(packet_value, semantic=semantic, security=security)
    passed = review_report["overall"] == "PASS"
    return review_report, create_review_result(
        packet_value,
        review_report,
        governed_receipt(packet_value, passed=passed),
        qualified_execution_proof_hash="sha256:" + "9" * 64,
    )


def test_governed_interactive_result_is_provider_neutral_and_qes_optional(tmp_path):
    packet_value, _ = packet(tmp_path)
    review_report = report(packet_value)
    review_result = create_review_result(
        packet_value,
        review_report,
        governed_receipt(packet_value, passed=True),
        governed_review_execution_hash="sha256:" + "8" * 64,
    )
    validation = validate_review_result(packet_value, review_report, review_result)
    assert "qualified_execution_proof_hash" not in review_result
    assert validation["review_execution_kind"] == "governed-interactive"
    assert validation["review_execution_provider"] == packet_value["selected_provider"]


def test_generator_emits_executable_packet_without_invoking_configured_runner(tmp_path):
    value, marker = packet(tmp_path)

    assert value["status"] == "EXECUTABLE"
    assert value["selected_provider"] == "codex-isolated-review-runner"
    assert value["required_dimensions"] == ["semantic", "security"]
    assert value["packet_hash"].startswith("sha256:")
    assert marker.exists() is False


def test_generator_emits_exact_hold_when_isolated_runner_is_not_configured(tmp_path):
    value, marker = packet(tmp_path, configured=False)

    assert value["status"] == "HOLD"
    assert value["selected_provider"] is None
    assert value["hold"]["code"] == "REVIEW_PROVIDER_UNAVAILABLE"
    claude = next(
        item
        for item in value["hold"]["considered"]
        if item["provider"] == "claude-local-runner"
    )
    assert any("not present" in reason for reason in claude["reasons"])
    assert marker.exists() is False


def test_generator_selects_configured_provider_neutral_interactive_review(tmp_path):
    registry = load_registry(REGISTRY)
    runner = executable(tmp_path / "governed-review")
    health = observe_health(
        registry,
        coding_config={"commands": {"governed-review": [runner, "{prompt}", "{snapshot}"]}},
        adapters=adapters(),
    )
    source = snapshot(tmp_path)
    value = generate_review_packet(
        manifest(), registry, health, adapters=adapters(),
        snapshot_ref=source.resolve().as_uri(), snapshot_hash=snapshot_hash(source),
        required_capabilities=("governed-interactive-review",),
    )
    assert value["status"] == "EXECUTABLE"
    assert value["selected_provider"] == "claude"
    assert value["receiver_profile"] == {"id": "claude-code", "version": 1}


def test_result_validator_accepts_both_dimensions_only_when_receipt_is_bound(tmp_path):
    value, _ = packet(tmp_path)
    review_report, review = result(value)

    validated = validate_review_result(value, review_report, review)

    assert validated["status"] == "PASS"
    assert validated["candidate_manifest_hash"] == value["candidate_manifest_hash"]
    assert validated["review_receipt_hash"] == review["governed_review_receipt"]["receipt_hash"]


def test_security_failure_is_validated_as_fail_and_never_reviewed(tmp_path):
    value, _ = packet(tmp_path)
    review_report, review = result(value, security="FAIL")

    validated = validate_review_result(value, review_report, review)

    assert validated["status"] == "FAIL"
    assert review["governed_review_receipt"]["established_conditions"] == []


def test_held_packet_cannot_accept_review_result(tmp_path):
    value, _ = packet(tmp_path, configured=False)

    with pytest.raises(CandidateReviewError, match="held"):
        review_report, review = result(value)
        validate_review_result(value, review_report, review)


def test_candidate_manifest_and_review_result_tampering_fail_closed(tmp_path):
    candidate = manifest()
    candidate["manifest_hash"] = "sha256:" + "0" * 64
    registry = load_registry(REGISTRY)
    health = observe_health(registry, coding_config={"commands": {}}, adapters=adapters())
    source = snapshot(tmp_path)
    with pytest.raises(CandidateReviewError, match="manifest hash mismatch"):
        generate_review_packet(
            candidate,
            registry,
            health,
            adapters=adapters(),
            snapshot_ref=source.resolve().as_uri(),
            snapshot_hash=snapshot_hash(source),
        )

    value, _ = packet(tmp_path / "second")
    review_report, review = result(value)
    review["selected_provider"] = "other"
    with pytest.raises(CandidateReviewError, match="result hash mismatch"):
        validate_review_result(value, review_report, review)


def test_result_cannot_precede_or_substitute_the_qualified_report(tmp_path):
    value, _ = packet(tmp_path)
    first_report, review = result(value)
    later_report = report(value, security="FAIL")

    with pytest.raises(CandidateReviewError, match="report binding mismatch"):
        validate_review_result(value, later_report, review)
    fabricated = dict(review)
    fabricated["qualified_execution_proof_hash"] = None
    fabricated_unsigned = {key: item for key, item in fabricated.items() if key != "result_hash"}
    fabricated["result_hash"] = hash_object(fabricated_unsigned)
    with pytest.raises(CandidateReviewError, match="proof binding"):
        validate_review_result(value, first_report, fabricated)


def test_governed_review_card_lease_is_fresh_bounded_and_attempt_specific(tmp_path):
    packet_value, _ = packet(tmp_path)
    observed = datetime(2026, 8, 16, 12, 34, 56, 789, tzinfo=timezone.utc)

    binding = {"ref": "evidence:descriptor", "hash": "sha256:" + "8" * 64}
    codegraph = {"ref": "codegraph:snapshot:1", "hash": "sha256:" + "6" * 64}
    environment = {"ref": "environment:manifest:1", "hash": "sha256:" + "7" * 64}
    review_input = {"ref": "review:input:1", "hash": "sha256:" + "5" * 64}
    common = {
        "candidate_evidence_binding": binding,
        "receipt_sink_binding": {"ref": "x:review", "hash": "sha256:" + "4" * 64},
        "codegraph_binding": codegraph,
        "execution_environment_binding": environment,
        "review_input_binding": review_input,
    }
    first = _card(
        manifest(), packet_value, observed_at=observed, **common,
    )["lease"]
    second = _card(
        manifest(), packet_value, observed_at=observed + timedelta(seconds=1),
        **common,
    )["lease"]

    assert datetime.fromisoformat(first["expires_at"].replace("Z", "+00:00")) == (
        observed + timedelta(seconds=REVIEW_LEASE_SECONDS)
    )
    assert first["id"] != second["id"]
    assert first["stop_policy"] == "hold"


def test_governed_review_card_rejects_an_unzoned_observation_time(tmp_path):
    packet_value, _ = packet(tmp_path)
    common = {
        "candidate_evidence_binding": {
            "ref": "evidence:d", "hash": "sha256:" + "8" * 64,
        },
        "receipt_sink_binding": {
            "ref": "x:review", "hash": "sha256:" + "4" * 64,
        },
        "codegraph_binding": {
            "ref": "codegraph:1", "hash": "sha256:" + "6" * 64,
        },
        "execution_environment_binding": {
            "ref": "env:1", "hash": "sha256:" + "7" * 64,
        },
        "review_input_binding": {
            "ref": "review:1", "hash": "sha256:" + "5" * 64,
        },
    }

    with pytest.raises(ValueError, match="include a timezone"):
        _card(
            manifest(), packet_value,
            observed_at=datetime(2026, 8, 16, 12, 0), **common,
        )


def test_governed_review_card_rejects_source_and_argv_substitution(tmp_path):
    packet_value, _ = packet(tmp_path)
    common = {
        "observed_at": datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc),
        "candidate_evidence_binding": {"ref": "evidence:d", "hash": "sha256:" + "8" * 64},
        "receipt_sink_binding": {"ref": "x:review", "hash": "sha256:" + "4" * 64},
        "execution_environment_binding": {"ref": "env:1", "hash": "sha256:" + "7" * 64},
        "review_input_binding": {"ref": "review:1", "hash": "sha256:" + "5" * 64},
    }
    with pytest.raises(ValueError, match="CodeGraph"):
        _card(manifest(), packet_value, codegraph_binding={"ref": packet_value["snapshot"]["ref"], "hash": manifest()["source"]["archive_sha256"]}, **common)
    with pytest.raises(ValueError, match="runner argv"):
        _card(
            manifest(), packet_value,
            codegraph_binding={"ref": "codegraph:1", "hash": "sha256:" + "6" * 64},
            **{**common, "execution_environment_binding": {"ref": "env:argv", "hash": hash_object(packet_value["runner_argv"])}},
        )


def test_governed_request_cli_reports_finalized_result(
    tmp_path, monkeypatch, capsys,
):
    request = tmp_path / "request.json"
    request.write_text("{}")
    finalized = {
        "execution": {"provider": "claude", "execution_hash": "sha256:" + "1" * 64},
        "result": {"status": "PASS", "result_hash": "sha256:" + "2" * 64},
        "evidence_bundle": {"bundle_hash": "sha256:" + "3" * 64},
    }
    monkeypatch.setattr(governed_adapter, "execute_request", lambda _path: finalized)
    monkeypatch.setattr(sys, "argv", ["tgw-execute-candidate-review", "--governed-request", str(request)])
    assert execute_candidate_review.main() == 0
    assert json.loads(capsys.readouterr().out) == {
        "schema": "tgw-governed-review-result-summary/v1", "provider": "claude",
        "execution_hash": "sha256:" + "1" * 64, "verdict": "PASS",
        "result_hash": "sha256:" + "2" * 64,
        "evidence_bundle_hash": "sha256:" + "3" * 64,
    }
