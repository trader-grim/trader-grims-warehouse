import hashlib
import json
from pathlib import Path

import pytest

from tgw.candidate_review import (
    CandidateReviewError,
    generate_review_packet,
    validate_review_result,
)
from tgw.harness_registry import load_registry, observe_health
from tgw.review_runner import snapshot_hash

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "agent-services/catalogs/harness-providers-v1.json"


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
    unsigned = {
        "schema": "tgw-governed-coding-receipt/v1",
        "status": "PASS" if passed else "FAIL",
        "role": "independent-review",
        "selected_provider": packet_value["selected_provider"],
        "execution_identity": "review-context:1",
        "card_hash": "sha256:card",
        "promptcraft_receipt_hash": "sha256:promptcraft",
        "resource_receipt_hash": "sha256:resources",
        "harness_resource_receipt_hash": "sha256:resources",
        "resource_service_descriptor_hash": "sha256:service",
        "outcome": "satisfied" if passed else "failed",
        "established_conditions": ["reviewed"] if passed else [],
        "artifacts": [],
    }
    return {**unsigned, "receipt_hash": hash_object(unsigned)}


def result(packet_value, *, semantic="PASS", security="PASS"):
    passed = semantic == security == "PASS"

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

    unsigned = {
        "schema": "tgw-integrated-candidate-review-result/v1",
        "packet_hash": packet_value["packet_hash"],
        "candidate_manifest_hash": packet_value["candidate_manifest_hash"],
        "selected_provider": packet_value["selected_provider"],
        "governed_review_receipt": governed_receipt(packet_value, passed=passed),
        "dimensions": {
            "semantic": dimension(semantic, "semantic defect"),
            "security": dimension(security, "security defect"),
        },
        "overall": "PASS" if passed else "FAIL",
    }
    return {**unsigned, "result_hash": hash_object(unsigned)}


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


def test_result_validator_accepts_both_dimensions_only_when_receipt_is_bound(tmp_path):
    value, _ = packet(tmp_path)
    review = result(value)

    validated = validate_review_result(value, review)

    assert validated["status"] == "PASS"
    assert validated["candidate_manifest_hash"] == value["candidate_manifest_hash"]
    assert validated["review_receipt_hash"] == review["governed_review_receipt"]["receipt_hash"]


def test_security_failure_is_validated_as_fail_and_never_reviewed(tmp_path):
    value, _ = packet(tmp_path)
    review = result(value, security="FAIL")

    validated = validate_review_result(value, review)

    assert validated["status"] == "FAIL"
    assert review["governed_review_receipt"]["established_conditions"] == []


def test_held_packet_cannot_accept_review_result(tmp_path):
    value, _ = packet(tmp_path, configured=False)

    with pytest.raises(CandidateReviewError, match="held"):
        validate_review_result(value, result(value))


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
    review = result(value)
    review["selected_provider"] = "other"
    with pytest.raises(CandidateReviewError, match="result hash mismatch"):
        validate_review_result(value, review)
