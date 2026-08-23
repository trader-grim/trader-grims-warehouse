from __future__ import annotations

from pathlib import Path

import pytest

from tgw import context_generation_status as generation_status


def _status_projection() -> tuple[dict, dict]:
    journal = {"schema": "tgw-actor-provider-journal/v1", "status": "VERIFIED"}
    actors = sorted(generation_status._INSTRUCTION_ENTRY_POINTS)
    actor_verifications = [
        {
            "actor": actor,
            "actor_proof_hash": "sha256:" + str(index) * 64,
            "primary_real_store_semantic_sha256": (
                "sha256:" + str(index + 3) * 64
            ),
            "instruction_entry_point_path": (
                generation_status._INSTRUCTION_ENTRY_POINTS[actor]
            ),
            "instruction_entry_point_sha256": "sha256:" + "a" * 64,
        }
        for index, actor in enumerate(actors, start=1)
    ]
    current_plan_sources = {
        path: "sha256:" + str(index) * 64
        for index, path in enumerate(
            generation_status._CURRENT_PLAN_SOURCES, start=5
        )
    }
    revisions = {
        "approved_plan": "1" * 40,
        "approved_solution": "sha256:" + "1" * 64,
        "evidence_plan": "2" * 40,
        "evidence_tree": "3" * 40,
        "source_commit": "4" * 40,
        "source_tree": "5" * 40,
        "current_plan_sources": current_plan_sources,
        "current_plan_sources_sha256": generation_status._hash(
            current_plan_sources
        ),
        "catalog": "sha256:" + "2" * 64,
        "bootstrap": "sha256:" + "3" * 64,
        "broker_policy": "sha256:" + "4" * 64,
        "review": "sha256:" + "5" * 64,
        "admission": "sha256:" + "6" * 64,
    }
    transaction = {
        "schema": "tgw-fleet-convergence-projection/v1",
        "status": "VERIFIED",
        "transaction_id": "instruction-status-fixture",
        "actors": actors,
        "target_generation": "sha256:" + "b" * 64,
        "target_revisions": revisions,
        "journal_sha256": generation_status._hash(journal),
        "journal_payload_sha256": "sha256:" + "7" * 64,
        "ledger_sequence": 1,
        "ledger_record_sha256": "sha256:" + "8" * 64,
        "coordinator_binding_sha256": "sha256:" + "9" * 64,
        "confinement_state": "NON_CONFINING_ACTOR_COMPOSITE_STORES",
        "selected_release": {"generation": "fixture"},
        "admission_evidence": {
            "review_receipt_sha256": revisions["review"],
            "admission_receipt_sha256": revisions["admission"],
        },
        "actor_verifications": actor_verifications,
        "obligations": [],
        "global_pending": [],
        "cold_handoff_evidence_sha256": generation_status._hash([]),
    }
    projection = {
        "schema": "tgw-fleet-convergence-set/v1",
        "state": "TERMINAL",
        "generation_status": "CURRENT",
        "active_pointer_sha256": None,
        "supersessions_sha256": generation_status._hash({}),
        "transaction": transaction,
    }
    _rehash_projection(projection)
    return projection, journal


def _rehash_projection(projection: dict) -> None:
    transaction = projection["transaction"]
    rows = [
        {
            "actor": item.get("actor"),
            "semantic_sha256": item.get(
                "primary_real_store_semantic_sha256"
            ),
            "instruction_path": item.get("instruction_entry_point_path"),
            "instruction_sha256": item.get("instruction_entry_point_sha256"),
            "proof_sha256": item.get("actor_proof_hash"),
        }
        for item in transaction["actor_verifications"]
    ]
    transaction["real_store_evidence_sha256"] = generation_status._hash(
        sorted(rows, key=lambda item: str(item["actor"]))
    )
    transaction.pop("projection_sha256", None)
    transaction["projection_sha256"] = generation_status._hash(transaction)
    projection.pop("projection_sha256", None)
    projection["projection_sha256"] = generation_status._hash(projection)


def _run_direct_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    projection: dict,
    journal: dict,
) -> dict:
    paths = generation_status.GenerationStatusPaths(
        state_root=tmp_path / "state",
        coordinator_transaction_root=tmp_path / "coordinator",
        admission_root=tmp_path / "admissions",
        actor_generation_root=tmp_path / "generations",
        release_root=tmp_path / "runtime",
        plan_repository=tmp_path / "plans",
        git=tmp_path / "git",
        actor_public_key=tmp_path / "actor.pub",
        admission_public_key=tmp_path / "admission.pub",
        scratch_root=tmp_path / "scratch",
    )
    ledger_record = projection["transaction"]["ledger_record_sha256"]
    monkeypatch.setattr(
        generation_status, "_protected_directory", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        generation_status,
        "_verify_ledger",
        lambda *_args, **_kwargs: ([{"record_sha256": ledger_record}], ledger_record),
    )
    monkeypatch.setattr(
        generation_status, "_verify_pointer", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        generation_status, "_verify_supersessions", lambda *_args, **_kwargs: {}
    )

    def protected_json(path, _label, _trusted_uid, **_kwargs):
        if path == paths.projection:
            return projection
        if path == paths.private_root / "instruction-status-fixture.actor-provider.json":
            return journal
        raise AssertionError(f"unexpected protected JSON path: {path}")

    monkeypatch.setattr(generation_status, "_protected_json", protected_json)
    monkeypatch.setattr(
        generation_status,
        "_verify_plan_repository",
        lambda *_args, **_kwargs: {
            "state": "CURRENT",
            "observed_evidence_commit": "2" * 40,
        },
    )
    monkeypatch.setattr(
        generation_status,
        "_verify_actor_generation",
        lambda *_args, **_kwargs: {
            "generation": projection["transaction"]["target_generation"],
            "receipt_hash": "sha256:" + "c" * 64,
        },
    )
    monkeypatch.setattr(
        generation_status,
        "_verify_selected_release",
        lambda *_args, **_kwargs: {"generation": "fixture"},
    )
    monkeypatch.setattr(
        generation_status,
        "_verify_coordinator_openings",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        generation_status,
        "_verify_admission_references",
        lambda *_args, **_kwargs: {
            "verified": [projection["transaction"]["target_revisions"]["admission"]],
            "unverified_external_historical": [],
        },
    )
    return generation_status.verify_generation_status(
        paths, trusted_uid=1000, trusted_gid=1000
    )


def test_current_status_reports_every_actor_instruction_hash(
    monkeypatch, tmp_path,
):
    projection, journal = _status_projection()

    result = _run_direct_status(monkeypatch, tmp_path, projection, journal)

    expected = {
        actor: {
            "path": path,
            "sha256": "sha256:" + "a" * 64,
        }
        for actor, path in generation_status._INSTRUCTION_ENTRY_POINTS.items()
    }
    assert result["status"] == "CURRENT"
    assert result["actor_instructions"] == expected
    assert result["actor_instructions_sha256"] == generation_status._hash(expected)
    assert (
        "instructions="
        + result["actor_instructions_sha256"].removeprefix("sha256:")[:12]
        in result["line"]
    )


@pytest.mark.parametrize("invalid_binding", ["missing", "swapped", "bad-hash"])
def test_current_status_rejects_incomplete_actor_instruction_proof(
    monkeypatch, tmp_path, invalid_binding,
):
    projection, journal = _status_projection()
    verifications = projection["transaction"]["actor_verifications"]
    if invalid_binding == "missing":
        verifications.pop()
    elif invalid_binding == "swapped":
        verifications[0]["instruction_entry_point_path"] = (
            generation_status._INSTRUCTION_ENTRY_POINTS["codex"]
        )
    else:
        verifications[0]["instruction_entry_point_sha256"] = "sha256:invalid"
    _rehash_projection(projection)

    with pytest.raises(
        generation_status.ContextGenerationStatusError,
        match="active real-store verification is incomplete",
    ):
        _run_direct_status(monkeypatch, tmp_path, projection, journal)
