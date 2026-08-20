import pytest

from tgw.admission_recovery import AdmissionRecoveryError, compile_recovery_invocation, compile_release_admission

HASH = "sha256:" + "a" * 64
COMMIT = "b" * 40


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
    assert compile_release_admission(request=admission()) == compile_release_admission(request=admission())
    assert compile_release_admission(request=admission())["status"] == "ADMITTED"


@pytest.mark.parametrize(
    "field,value,reason",
    [("status", "FAIL", "review-not-passed"), ("candidate_commit", "c" * 40, "review-candidate-mismatch"), ("solution_hash", "sha256:" + "c" * 64, "review-solution-mismatch")],
)
def test_missing_or_mismatched_review_refuses(field, value, reason):
    value_request = admission()
    value_request["review"][field] = value
    receipt = compile_release_admission(request=value_request)
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
        compile_release_admission(request=value)
