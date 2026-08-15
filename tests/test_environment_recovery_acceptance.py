from __future__ import annotations

from pathlib import Path

from tgw.environment_recovery_acceptance import audit_environment_recovery

ROOT = Path(__file__).parents[1]


def test_current_program_audit_proves_server_and_reports_satellite_gaps_exactly():
    audit = audit_environment_recovery(ROOT, observed_at="2026-08-11T10:00:00-07:00")
    by_id = {item["id"]: item for item in audit["checks"]}
    assert by_id["server-registry-current"]["status"] == "proved"
    assert by_id["task-context-reproducible"]["status"] == "proved"
    assert by_id["clean-steward-boundary"]["status"] == "proved"
    assert by_id["registered-procedures"]["status"] == "proved"
    assert by_id["registered-procedures"]["detail"] == (
        "sha256:3fd90e890ee075a1e50432fc8b13ff48519764b9504b7c62d1e6442b68abf93d"
    )
    for host in ("catnanny", "helicrew"):
        assert by_id[f"{host}-evidence-package"]["status"] == "missing"
        assert by_id[f"{host}-review-complete"]["status"] == "missing"
        assert by_id[f"{host}-human-machine-disposition"]["status"] == "missing"
    assert by_id["human-final-acceptance"]["status"] == "missing"
    assert audit["counts"] == {"proved": 4, "missing": 7, "failed": 0}
    assert audit["ready_for_human_acceptance"] is False
    assert audit["complete"] is False


def test_acceptance_audit_never_claims_external_actions_or_memory_authority():
    audit = audit_environment_recovery(ROOT, observed_at="2026-08-11T10:00:00-07:00")
    assert audit["external_actions_performed"] is False
    assert audit["history_or_memory_granted_authority"] is False
