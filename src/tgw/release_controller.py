"""Mountable orchestrator for exact immutable TGW application releases."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tgw.candidate_manifest import verify_migration_safety_receipt
from tgw.platform_bootstrap import BootstrapStateAmbiguous
from tgw.release_installer import materialize, verify


@dataclass(frozen=True)
class ReviewedCandidate:
    generation: str
    commit: str
    tree: str
    archive_sha256: str
    artifact_ref: str
    review_receipt: str
    controller_receipt: str
    migration_receipts: tuple[Mapping[str, Any], ...]


class MountedReleaseController:
    """Drive one journaled release sequence without a generic command seam.

    Every mutating callback is a typed, mounted provider.  ``record_stage`` is
    required so reconciliation after a process crash can determine which
    boundaries were crossed; rollback never assumes that selection occurred.
    """

    def __init__(
        self,
        *,
        roots: Mapping[str, Path],
        artifacts: Mapping[str, Path],
        observe_predecessor: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        quiesce_services: Callable[[str, str, Sequence[str], str], Mapping[str, Any]],
        backup: Callable[[str, str, str], Mapping[str, Any]],
        migrate: Callable[[str, str, Path, Sequence[Mapping[str, Any]], str, str], Mapping[str, Any]],
        stage_runtime: Callable[[str, str, Path, Mapping[str, Any], Mapping[str, Any], str], Mapping[str, Any]],
        activate_generation: Callable[[str, str, str, str, str], Mapping[str, Any]],
        restart_services: Callable[[str, str, Sequence[str], str], Mapping[str, Any]],
        health: Callable[[str, str, Sequence[str], str], Mapping[str, Any]],
        verify_unrelated_state: Callable[[str, str, str], Mapping[str, Any]],
        record_stage: Callable[[str, str, Sequence[str]], Mapping[str, Any]],
        reconcile_predecessor: Callable[[str, str, str, Sequence[str], Sequence[str]], Mapping[str, Any]],
    ) -> None:
        self._roots = {identity: Path(path) for identity, path in roots.items()}
        self._artifacts = {identity: Path(path) for identity, path in artifacts.items()}
        self._observe_predecessor = observe_predecessor
        self._quiesce_services = quiesce_services
        self._backup = backup
        self._migrate = migrate
        self._stage_runtime = stage_runtime
        self._activate_generation = activate_generation
        self._restart_services = restart_services
        self._health = health
        self._verify_unrelated_state = verify_unrelated_state
        self._record_stage = record_stage
        self._reconcile_predecessor = reconcile_predecessor

    @staticmethod
    def _candidate(parameters: Mapping[str, Any]) -> ReviewedCandidate:
        return ReviewedCandidate(
            generation=str(parameters["generation"]),
            commit=str(parameters["candidate_commit"]),
            tree=str(parameters["candidate_tree"]),
            archive_sha256=str(parameters["archive_sha256"]).removeprefix("sha256:"),
            artifact_ref=str(parameters["artifact_ref"]),
            review_receipt=str(parameters["review_receipt"]),
            controller_receipt=str(parameters["controller_receipt"]),
            migration_receipts=tuple(parameters["migration_receipts"]),
        )

    @staticmethod
    def _receipt(result: Mapping[str, Any], *, statuses: set[str], label: str) -> str:
        receipt = result.get("receipt")
        if result.get("status") not in statuses or not isinstance(receipt, str) or not receipt:
            raise RuntimeError(f"{label} was not proven by an immutable receipt")
        return receipt

    def _stage(self, operation_id: str, stage: str, evidence: Sequence[str]) -> str:
        result = self._record_stage(operation_id, stage, tuple(evidence))
        return self._receipt(result, statuses={"RECORDED", "ALREADY_RECORDED"}, label=f"{stage} stage")

    @staticmethod
    def _verified_migrations(candidate: ReviewedCandidate, release: Path) -> tuple[dict[str, Any], ...]:
        verified: list[dict[str, Any]] = []
        for supplied in candidate.migration_receipts:
            path = supplied.get("migration_path") if isinstance(supplied, Mapping) else None
            if not isinstance(path, str) or Path(path).name in {"schema.sql", "live_schema.sql"}:
                raise ValueError("release migration binding is not an executable migration")
            source_path = release / path
            if not source_path.is_file() or release.resolve() not in source_path.resolve().parents:
                raise ValueError("release migration is absent from the materialized candidate")
            snapshot_path = supplied.get("schema_snapshot_path")
            snapshot_source = None
            if snapshot_path is not None:
                snapshot_file = release / str(snapshot_path)
                if not snapshot_file.is_file() or release.resolve() not in snapshot_file.resolve().parents:
                    raise ValueError("release migration snapshot binding is absent")
                snapshot_source = snapshot_file.read_bytes()
            normalized = verify_migration_safety_receipt(
                supplied,
                candidate_commit=candidate.commit,
                candidate_tree=candidate.tree,
                base_commit=str(supplied.get("base_commit", "")),
                base_tree=str(supplied.get("base_tree", "")),
                migration_paths=(path,),
                migration_source=source_path.read_bytes(),
                schema_snapshot_source=snapshot_source,
            )
            verified.append(dict(normalized.__dict__))
        return tuple(verified)

    def install(self, parameters: Mapping[str, Any]) -> Mapping[str, Any]:
        candidate = self._candidate(parameters)
        root = self._resolve(self._roots, str(parameters["root_id"]), "release root")
        archive = self._resolve(self._artifacts, candidate.artifact_ref, "candidate artifact")
        operation_id = str(parameters["operation_id"])
        evidence = [candidate.review_receipt, candidate.controller_receipt]

        predecessor = self._observe_predecessor(parameters)
        predecessor_receipt = self._receipt(predecessor, statuses={"MATCH"}, label="fresh predecessor observation")
        if (
            predecessor_receipt == parameters["predecessor_observation_hash"]
            or predecessor.get("bound_observation") != parameters["predecessor_observation_hash"]
            or predecessor.get("generation") != parameters["expected_current"]
            or predecessor.get("nix_system_path") != parameters["nix_system_path"]
            or predecessor.get("services") != list(parameters["services"])
            or predecessor.get("health_probes") != list(parameters["health_probes"])
        ):
            raise ValueError("fresh predecessor preflight did not independently cross-bind current host facts")
        evidence += [predecessor_receipt, self._stage(operation_id, "predecessor-verified", evidence + [predecessor_receipt])]

        quiesced = self._quiesce_services(
            str(parameters["root_id"]), str(parameters["expected_current"]),
            tuple(parameters["services"]), operation_id,
        )
        quiesce_receipt = self._receipt(quiesced, statuses={"QUIESCED", "ALREADY_QUIESCED"}, label="predecessor quiescence")
        evidence += [quiesce_receipt, self._stage(operation_id, "services-quiesced", [quiesce_receipt])]

        backed_up = self._backup(str(parameters["root_id"]), str(parameters["expected_current"]), operation_id)
        backup_receipt = self._receipt(backed_up, statuses={"BACKED_UP", "ALREADY_BACKED_UP"}, label="database backup")
        evidence += [backup_receipt, self._stage(operation_id, "database-backed-up", [backup_receipt])]

        manifest = materialize(
            root, archive, generation=candidate.generation, commit=candidate.commit,
            tree=candidate.tree, archive_sha256=candidate.archive_sha256,
        )
        release = root / "releases" / candidate.generation
        verification = verify(root, candidate.generation)
        projection_path = release / str(parameters["projection"]["release_path"])
        projection_hash = str(parameters["projection"]["content_sha256"]).removeprefix("sha256:")
        if not projection_path.is_file() or hashlib.sha256(projection_path.read_bytes()).hexdigest() != projection_hash:
            raise ValueError("materialized runtime projection does not match W09 contract")
        migrations = self._verified_migrations(candidate, release)
        materialization_receipt = "manifest:" + manifest["content_manifest_sha256"]
        evidence += [materialization_receipt, "verification:" + verification["status"]]
        evidence.append(self._stage(operation_id, "release-materialized", evidence[-2:]))

        migrated = self._migrate(
            str(parameters["root_id"]), candidate.generation, release, migrations,
            backup_receipt, operation_id,
        )
        migration_receipt = self._receipt(migrated, statuses={"APPLIED", "ALREADY_APPLIED"}, label="ordered candidate migrations")
        if migrated.get("applied_paths") != [receipt["migration_path"] for receipt in migrations]:
            raise RuntimeError("candidate migrations were not applied in their exact reviewed order")
        evidence += [migration_receipt, self._stage(operation_id, "migrations-applied", [migration_receipt])]

        staged = self._stage_runtime(
            str(parameters["root_id"]), candidate.generation, release,
            parameters["projection"], parameters["runtime_config"], operation_id,
        )
        runtime_receipt = self._receipt(staged, statuses={"STAGED", "ALREADY_STAGED"}, label="generation runtime artifacts")
        if staged.get("generation_path") != parameters["immutable_generation_path"]:
            raise RuntimeError("runtime artifacts were not staged inside the immutable generation")
        evidence += [runtime_receipt, self._stage(operation_id, "runtime-staged", [runtime_receipt])]

        activated = self._activate_generation(
            str(parameters["root_id"]), str(parameters["expected_current"]),
            candidate.generation, operation_id, runtime_receipt,
        )
        activation_receipt = self._receipt(activated, statuses={"ACTIVATED"}, label="generation compare-and-swap")
        if activated.get("prior_generation") != parameters["expected_current"] or activated.get("generation") != candidate.generation:
            raise RuntimeError("generation activation did not prove the exact compare-and-swap")
        evidence += [activation_receipt, self._stage(operation_id, "generation-activated", [activation_receipt])]

        restarted = self._restart_services(
            str(parameters["root_id"]), candidate.generation, tuple(parameters["services"]), operation_id,
        )
        restart_receipt = self._receipt(restarted, statuses={"RESTARTED"}, label="successor service restart")
        evidence += [restart_receipt, self._stage(operation_id, "successor-restarted", [restart_receipt])]

        healthy = self._health(
            str(parameters["root_id"]), candidate.generation, tuple(parameters["health_probes"]), operation_id,
        )
        health_receipt = self._receipt(healthy, statuses={"HEALTHY"}, label="successor health")
        invariant = self._verify_unrelated_state(str(parameters["root_id"]), operation_id, predecessor_receipt)
        invariant_receipt = self._receipt(invariant, statuses={"UNCHANGED"}, label="unrelated host state")
        evidence += [health_receipt, invariant_receipt]
        evidence.append(self._stage(operation_id, "successor-verified", evidence[-2:]))
        return {"evidence": evidence}

    def rollback(self, parameters: Mapping[str, Any]) -> Mapping[str, Any]:
        # The provider's durable stage journal determines which restorations are
        # necessary; this path is valid before or after selector activation.
        restored = self._reconcile_predecessor(
            str(parameters["root_id"]), str(parameters["operation_id"]),
            str(parameters["expected_current"]), tuple(parameters["services"]),
            tuple(parameters["health_probes"]),
        )
        receipt = restored.get("receipt")
        if (
            restored.get("status") not in {"RESTORED", "NO_MUTATION"}
            or not isinstance(receipt, str) or not receipt
            or restored.get("generation") != parameters["expected_current"]
            or restored.get("predecessor_healthy") is not True
        ):
            evidence = tuple(str(item) for item in restored.get("evidence", ()) if isinstance(item, str))
            raise BootstrapStateAmbiguous(
                "application rollback could not prove selector, database, config, services, and predecessor health",
                evidence=evidence or ("application-reconciliation:unproven",),
                rollback_required=False,
            )
        return {"receipt": receipt, "evidence": list(restored.get("evidence", ())) }

    @staticmethod
    def _resolve(mounts: Mapping[str, Path], identity: str, label: str) -> Path:
        try:
            return mounts[identity]
        except KeyError as exc:
            raise ValueError(f"unknown mounted {label}: {identity}") from exc
