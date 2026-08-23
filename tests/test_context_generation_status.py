from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from tgw import context_generation_status as generation_status

_INSTRUCTION_RAW = b"# TGW agent entry point\n"
_INSTRUCTION_SHA256 = "sha256:" + hashlib.sha256(_INSTRUCTION_RAW).hexdigest()


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
            "instruction_entry_point_sha256": _INSTRUCTION_SHA256,
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
    *,
    instruction_drift: tuple[str, str] | None = None,
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
    original_entry_points = dict(generation_status._INSTRUCTION_ENTRY_POINTS)
    instruction_entry_points = {
        actor: str(tmp_path / "homes" / actor / Path(path).name)
        for actor, path in original_entry_points.items()
    }
    monkeypatch.setattr(
        generation_status, "_INSTRUCTION_ENTRY_POINTS", instruction_entry_points,
    )
    for item in projection["transaction"]["actor_verifications"]:
        actor = item.get("actor")
        if (
            actor in original_entry_points
            and item.get("instruction_entry_point_path")
            == original_entry_points[actor]
        ):
            item["instruction_entry_point_path"] = instruction_entry_points[actor]
    _rehash_projection(projection)
    instruction_targets: dict[str, Path] = {}
    instruction_links: dict[str, Path] = {}
    for actor, raw_path in instruction_entry_points.items():
        destination = Path(raw_path)
        destination.parent.mkdir(parents=True)
        target = tmp_path / "generation-instructions" / actor / "AGENTS.md"
        target.parent.mkdir(parents=True)
        target.write_bytes(_INSTRUCTION_RAW)
        target.chmod(0o644)
        destination.symlink_to(target)
        instruction_targets[actor] = target
        instruction_links[actor] = destination
    if instruction_drift is not None:
        actor, drift = instruction_drift
        if drift == "missing":
            instruction_links[actor].unlink()
        elif drift == "wrong-hash":
            instruction_targets[actor].write_bytes(b"wrong instruction\n")
        elif drift == "copied":
            instruction_links[actor].unlink()
            instruction_links[actor].write_bytes(_INSTRUCTION_RAW)
            instruction_links[actor].chmod(0o644)
        else:
            raise AssertionError(f"unknown instruction drift: {drift}")
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
            "instructions": {
                actor: {"path": path, "sha256": _INSTRUCTION_SHA256}
                for actor, path in instruction_entry_points.items()
            },
            "instructions_sha256": generation_status._hash({
                actor: {"path": path, "sha256": _INSTRUCTION_SHA256}
                for actor, path in instruction_entry_points.items()
            }),
        },
    )
    monkeypatch.setattr(
        generation_status,
        "_verify_selected_release",
        lambda *_args, **_kwargs: {
            "generation": "fixture", "state": "CURRENT",
        },
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
        paths, trusted_uid=os.getuid(), trusted_gid=os.getgid()
    )


def test_current_status_reports_every_actor_instruction_hash(
    monkeypatch, tmp_path,
):
    projection, journal = _status_projection()

    result = _run_direct_status(monkeypatch, tmp_path, projection, journal)

    expected = {
        actor: {
            "path": path,
            "sha256": _INSTRUCTION_SHA256,
            "observed_sha256": _INSTRUCTION_SHA256,
            "readback_state": "CURRENT",
            "materialization": "SYMLINK",
        }
        for actor, binding in result["actor_generation"]["instructions"].items()
        for path in [binding["path"]]
    }
    assert result["status"] == "CURRENT"
    assert result["actor_instructions"] == expected
    assert result["actor_instructions_sha256"] == generation_status._hash(expected)
    assert (
        "instructions="
        + result["actor_instructions_sha256"].removeprefix("sha256:")[:12]
        in result["line"]
    )


@pytest.mark.parametrize(
    "invalid_binding", ["missing", "swapped", "bad-hash", "additional"],
)
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
    elif invalid_binding == "additional":
        verifications.append(dict(verifications[0]))
    else:
        verifications[0]["instruction_entry_point_sha256"] = "sha256:invalid"
    _rehash_projection(projection)

    with pytest.raises(
        generation_status.ContextGenerationStatusError,
        match="active real-store verification is incomplete",
    ):
        _run_direct_status(monkeypatch, tmp_path, projection, journal)


@pytest.mark.parametrize(
    ("drift", "expected_state"),
    [
        ("missing", "MISSING"),
        ("wrong-hash", "WRONG_HASH"),
        ("copied", "UNBOUND_COPY"),
    ],
)
def test_current_status_reads_installed_instruction_and_reports_drift(
    monkeypatch, tmp_path, drift, expected_state,
):
    projection, journal = _status_projection()

    result = _run_direct_status(
        monkeypatch,
        tmp_path,
        projection,
        journal,
        instruction_drift=("codex", drift),
    )

    assert result["fleet_state"] == "CURRENT"
    assert result["status"] == "MIXED"
    assert result["actor_instructions"]["codex"]["readback_state"] == expected_state


def _actor_generation_records() -> tuple[str, dict, dict]:
    generation = "sha256:" + "b" * 64
    actors = {
        actor: {
            "bindings": [
                {
                    "kind": "instruction",
                    "name": "agent-entry-point",
                    "capability": "agent-entry-point",
                    "source": "AGENTS.md",
                    "destination": destination,
                    "sha256": _INSTRUCTION_SHA256,
                }
            ]
        }
        for actor, destination in generation_status._INSTRUCTION_ENTRY_POINTS.items()
    }
    bundle = {
        "schema": "tgw-complete-actor-contract-bundle/v1",
        "generation": generation,
        "actors": actors,
    }
    receipt_body = {
        "schema": "tgw-actor-generation-receipt/v1",
        "status": "PREPARED",
        "generation": generation,
        "actors": sorted(actors),
        "bundle_hash": generation_status._hash(bundle),
    }
    receipt = {
        **receipt_body,
        "receipt_hash": generation_status._hash(receipt_body),
    }
    return generation, receipt, bundle


def test_actor_generation_status_rejects_an_additional_instruction_binding(
    monkeypatch, tmp_path,
):
    generation, receipt, bundle = _actor_generation_records()
    bundle["actors"]["codex"]["bindings"].append(
        {
            "kind": "instruction",
            "name": "additional",
            "capability": "additional",
            "source": "AGENTS.md",
            "destination": "/home/codex/AGENTS.md",
            "sha256": _INSTRUCTION_SHA256,
        }
    )
    receipt["bundle_hash"] = generation_status._hash(bundle)
    receipt.pop("receipt_hash")
    receipt["receipt_hash"] = generation_status._hash(receipt)
    root = tmp_path / generation.removeprefix("sha256:")
    monkeypatch.setattr(
        generation_status, "_protected_directory", lambda *_args, **_kwargs: None,
    )

    def protected_json(path, _label, _trusted_uid, **_kwargs):
        if path == root / "generation-receipt.json":
            return receipt
        if path == root / "bundle.json":
            return bundle
        raise AssertionError(f"unexpected protected JSON path: {path}")

    monkeypatch.setattr(generation_status, "_protected_json", protected_json)

    with pytest.raises(
        generation_status.ContextGenerationStatusError,
        match="missing or additional instructions",
    ):
        generation_status._verify_actor_generation(
            generation_status.GenerationStatusPaths(
                actor_generation_root=tmp_path,
            ),
            generation,
            trusted_uid=os.getuid(),
        )


def test_selected_predecessor_release_is_update_pending_not_corruption(
    monkeypatch, tmp_path,
):
    runtime = tmp_path / "runtime"
    release = runtime / "releases" / "predecessor"
    release.mkdir(parents=True)
    (runtime / "current").symlink_to("releases/predecessor")
    manifest = {"commit": "1" * 40, "git_tree": "2" * 40}
    projection = {
        "path": str(release),
        "generation": "predecessor",
        "commit": manifest["commit"],
        "tree": manifest["git_tree"],
        "manifest_sha256": generation_status._hash(manifest),
    }
    monkeypatch.setattr(
        generation_status, "_protected_directory", lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        generation_status,
        "_protected_json",
        lambda *_args, **_kwargs: manifest,
    )

    result = generation_status._verify_selected_release(
        generation_status.GenerationStatusPaths(release_root=runtime),
        {"source_commit": "3" * 40, "source_tree": "4" * 40},
        projection,
        trusted_uid=os.getuid(),
    )

    assert result["state"] == "UPDATE_PENDING"
    assert result["commit"] == manifest["commit"]
