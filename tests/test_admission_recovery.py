
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tgw.admission_recovery import (
    AdmissionRecoveryError,
    compile_recovery_invocation,
    compile_release_admission,
    sign_environment_preflight_receipt,
    validate_environment_preflight_for_admission,
    validate_release_admission,
)

HASH = "sha256:" + "a" * 64
COMMIT = "b" * 40
ADMISSION_KEY = Ed25519PrivateKey.generate()
PREFLIGHT_KEY = Ed25519PrivateKey.generate()
ISSUED = "2026-08-20T00:00:00Z"
EXPIRES = "2026-08-21T00:00:00Z"
CURRENT = "2026-08-20T12:00:00Z"


def compile_admission(value):
    return compile_release_admission(
        request=value,
        signing_private_key=ADMISSION_KEY,
        signer_key_id="admission-authority",
        issued_at=ISSUED,
        expires_at=EXPIRES,
    )


def admission():
    evidence = {"status": "PASS", "candidate_commit": COMMIT, "solution_hash": HASH, "receipt_hash": HASH}
    return {
        "schema": "tgw-w16-release-admission-request/v1",
        "request_id": "w16-request",
        "candidate": {"commit": COMMIT, "tree": COMMIT},
        "plan": {"commit": COMMIT, "solution_hash": HASH},
        "environment": {"catalog_hash": HASH, "receipt_hash": HASH},
        "review": dict(evidence),
        "admission": dict(evidence),
    }


def recovery():
    return {
        "schema": "tgw-w17-recovery-request/v1",
        "recovery_id": "w17-recovery",
        "operator": "operator",
        "plan": {"commit": COMMIT, "solution_hash": HASH},
        "expiry": "2026-08-20T00:00:00Z",
        "effects": ["diagnose-platform"],
        "receipt_sink": HASH,
        "candidate_commit": COMMIT,
    }


def test_admission_is_deterministic_and_declarative():
    assert compile_admission(admission()) == compile_admission(admission())
    assert compile_admission(admission())["status"] == "ADMITTED"


def test_admitted_receipt_is_rehashed_and_exactly_candidate_bound():
    receipt = compile_admission(admission())
    validation = {
        "trusted_public_key": ADMISSION_KEY.public_key(), "current_time": CURRENT,
        "current_plan_commit": COMMIT, "current_solution_hash": HASH,
    }
    assert validate_release_admission(receipt, candidate_commit=COMMIT, candidate_tree=COMMIT, **validation) == receipt
    with pytest.raises(AdmissionRecoveryError, match="candidate tree mismatch"):
        validate_release_admission(receipt, candidate_commit=COMMIT, candidate_tree="c" * 40, **validation)
    receipt["status"] = "REFUSED"
    with pytest.raises(AdmissionRecoveryError, match="hash mismatch"):
        validate_release_admission(receipt, candidate_commit=COMMIT, candidate_tree=COMMIT, **validation)


def test_admission_refuses_forged_expired_or_stale_plan_receipt():
    receipt = compile_admission(admission())
    common = {
        "candidate_commit": COMMIT, "candidate_tree": COMMIT,
        "trusted_public_key": ADMISSION_KEY.public_key(),
        "current_plan_commit": COMMIT, "current_solution_hash": HASH,
    }
    forged = dict(receipt)
    forged["signature"] = "A" * 86 + "=="
    with pytest.raises(AdmissionRecoveryError, match="signature"):
        validate_release_admission(forged, current_time=CURRENT, **common)
    with pytest.raises(AdmissionRecoveryError, match="currently valid"):
        validate_release_admission(receipt, current_time="2026-08-21T00:00:00Z", **common)
    with pytest.raises(AdmissionRecoveryError, match="Plan binding is stale"):
        validate_release_admission(
            receipt, current_time=CURRENT, **{**common, "current_plan_commit": "c" * 40},
        )


def test_environment_preflight_is_exactly_bound_to_admission_hashes():
    receipt = {
        "schema": "tgw-environment-preflight-receipt/v1",
        "result": "PASS",
        "catalog_sha256": HASH,
        "actor": "codex",
        "profile": "development",
        "attempt_id": "attempt-1",
        "tools": [],
    }
    receipt = sign_environment_preflight_receipt(
        receipt, signing_private_key=PREFLIGHT_KEY, signer_key_id="preflight-authority",
        issued_at=ISSUED, expires_at=EXPIRES,
    )
    assert validate_environment_preflight_for_admission(
        receipt, catalog_hash=HASH, receipt_hash=receipt["receipt_hash"],
        trusted_public_key=PREFLIGHT_KEY.public_key(), current_time=CURRENT,
    ) == receipt
    receipt["actor"] = "claude"
    with pytest.raises(AdmissionRecoveryError, match="receipt hash mismatch"):
        validate_environment_preflight_for_admission(
            receipt, catalog_hash=HASH, receipt_hash=receipt["receipt_hash"],
            trusted_public_key=PREFLIGHT_KEY.public_key(), current_time=CURRENT,
        )


def test_environment_preflight_refuses_forged_or_expired_signature():
    receipt = sign_environment_preflight_receipt(
        {
            "schema": "tgw-environment-preflight-receipt/v1", "result": "PASS",
            "catalog_sha256": HASH, "actor": "codex", "profile": "development",
            "attempt_id": "attempt-1", "tools": [],
        },
        signing_private_key=PREFLIGHT_KEY, signer_key_id="preflight-authority",
        issued_at=ISSUED, expires_at=EXPIRES,
    )
    forged = dict(receipt)
    forged["signature"] = "A" * 86 + "=="
    with pytest.raises(AdmissionRecoveryError, match="signature"):
        validate_environment_preflight_for_admission(
            forged, catalog_hash=HASH, receipt_hash=receipt["receipt_hash"],
            trusted_public_key=PREFLIGHT_KEY.public_key(), current_time=CURRENT,
        )
    with pytest.raises(AdmissionRecoveryError, match="currently valid"):
        validate_environment_preflight_for_admission(
            receipt, catalog_hash=HASH, receipt_hash=receipt["receipt_hash"],
            trusted_public_key=PREFLIGHT_KEY.public_key(), current_time="2026-08-21T00:00:00Z",
        )


def test_v2_environment_preflight_evidence_remains_admissible():
    receipt = {
        "schema": "tgw-environment-preflight-receipt/v1",
        "result": "PASS",
        "catalog_sha256": HASH,
        "actor": "codex",
        "profile": "mobile",
        "attempt_id": "attempt-1",
        "tools": [],
        "workspace_root": "/opt/TGW/w/attempts/attempt-1/mobile/worktree",
        "cache_roots": {"home": "/opt/TGW/var/cache/tgw/attempts/attempt-1/mobile/home"},
        "environment": {"HOME": "/opt/TGW/var/cache/tgw/attempts/attempt-1/mobile/home"},
        "artifacts": [],
        "verification_commands": [["flutter", "test"]],
    }
    receipt = sign_environment_preflight_receipt(
        receipt, signing_private_key=PREFLIGHT_KEY, signer_key_id="preflight-authority",
        issued_at=ISSUED, expires_at=EXPIRES,
    )
    assert (
        validate_environment_preflight_for_admission(
            receipt,
            catalog_hash=HASH,
            receipt_hash=receipt["receipt_hash"], trusted_public_key=PREFLIGHT_KEY.public_key(), current_time=CURRENT,
        )
        == receipt
    )


def test_v3_environment_preflight_requires_bootstrap_and_broker_revisions():
    receipt = {
        "schema": "tgw-environment-preflight-receipt/v1",
        "result": "PASS",
        "catalog_sha256": HASH,
        "actor": "codex",
        "profile": "development",
        "attempt_id": "attempt-1",
        "tools": [],
        "workspace_root": "/opt/TGW/w/attempts/attempt-1",
        "cache_roots": {"home": "/opt/TGW/var/cache/tgw/attempts/attempt-1/development/home"},
        "environment": {"HOME": "/opt/TGW/var/cache/tgw/attempts/attempt-1/development/home"},
        "artifacts": [],
        "verification_commands": [["python", "-m", "pytest"]],
        "enforcement_boundary": {"version": 1},
        "bootstrap_revision": HASH,
        "broker_policy_revision": HASH,
    }
    receipt = sign_environment_preflight_receipt(
        receipt, signing_private_key=PREFLIGHT_KEY, signer_key_id="preflight-authority",
        issued_at=ISSUED, expires_at=EXPIRES,
    )
    assert (
        validate_environment_preflight_for_admission(
            receipt,
            catalog_hash=HASH,
            receipt_hash=receipt["receipt_hash"], trusted_public_key=PREFLIGHT_KEY.public_key(), current_time=CURRENT,
        )
        == receipt
    )
    receipt.pop("broker_policy_revision")
    with pytest.raises(AdmissionRecoveryError, match="fields are not exact"):
        validate_environment_preflight_for_admission(
            receipt,
            catalog_hash=HASH,
            receipt_hash=receipt["receipt_hash"], trusted_public_key=PREFLIGHT_KEY.public_key(), current_time=CURRENT,
        )


@pytest.mark.parametrize(
    "field,value,reason",
    [("status", "FAIL", "review-not-passed"), ("candidate_commit", "c" * 40, "review-candidate-mismatch"), ("solution_hash", "sha256:" + "c" * 64, "review-solution-mismatch")],
)
def test_missing_or_mismatched_review_refuses(field, value, reason):
    value_request = admission()
    value_request["review"][field] = value
    receipt = compile_admission(value_request)
    assert receipt["status"] == "REFUSED" and reason in receipt["reasons"]


@pytest.mark.parametrize("effects,reason", [(["generic-shell"], "effect-outside-platform-recovery"), (["repair-tool-environment", "repair-tool-environment"], "duplicate-effects")])
def test_recovery_cannot_escape_platform_effect_set(effects, reason):
    value = recovery()
    value["effects"] = effects
    receipt = compile_recovery_invocation(request=value, observed_at="2026-08-19T00:00:00Z")
    assert receipt["status"] == "REFUSED" and reason in receipt["reasons"]


def test_expired_recovery_refuses_and_valid_recovery_only_prepares():
    assert compile_recovery_invocation(request=recovery(), observed_at="2026-08-19T00:00:00Z")["status"] == "PREPARED"
    assert compile_recovery_invocation(request=recovery(), observed_at="2026-08-21T00:00:00Z")["status"] == "REFUSED"


def test_malformed_bindings_fail_closed():
    value = admission()
    value["candidate"]["commit"] = 1
    with pytest.raises(AdmissionRecoveryError):
        compile_admission(value)
