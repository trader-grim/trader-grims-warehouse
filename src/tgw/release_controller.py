"""Mountable typed controller for immutable reviewed release candidates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tgw.candidate_manifest import verify_migration_safety_receipt
from tgw.release_installer import materialize, rollback, select, verify


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


class MigrationAmbiguityError(RuntimeError):
    """The schema stage failed and its backup could not be proven restored."""


class MountedReleaseController:
    """Resolve symbolic mounts and drive the non-shell release installer."""

    def __init__(
        self,
        *,
        roots: Mapping[str, Path],
        artifacts: Mapping[str, Path],
        backup: Callable[[str, str], Mapping[str, Any]],
        health: Callable[[str, str], Mapping[str, Any]],
        migrate: Callable[[str, str, Path, Sequence[Mapping[str, Any]], str], Mapping[str, Any]],
        restore_migration_backup: Callable[[str, str], Mapping[str, Any]],
    ) -> None:
        self._roots = {identity: Path(path) for identity, path in roots.items()}
        self._artifacts = {identity: Path(path) for identity, path in artifacts.items()}
        self._backup = backup
        self._health = health
        self._migrate = migrate
        self._restore_migration_backup = restore_migration_backup

    @staticmethod
    def _candidate(parameters: Mapping[str, Any]) -> ReviewedCandidate:
        return ReviewedCandidate(
            generation=parameters["generation"],
            commit=parameters["candidate_commit"],
            tree=parameters["candidate_tree"],
            archive_sha256=parameters["archive_sha256"].removeprefix("sha256:"),
            artifact_ref=parameters["artifact_ref"],
            review_receipt=parameters["review_receipt"],
            controller_receipt=parameters["controller_receipt"],
            migration_receipts=tuple(parameters["migration_receipts"]),
        )

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
        root = self._resolve(self._roots, parameters["root_id"], "release root")
        archive = self._resolve(self._artifacts, candidate.artifact_ref, "candidate artifact")
        backup = self._backup(parameters["root_id"], parameters["expected_current"])
        backup_receipt = backup.get("receipt")
        if not isinstance(backup_receipt, str) or not backup_receipt:
            raise ValueError("backup provider did not return an immutable receipt")
        manifest = materialize(
            root,
            archive,
            generation=candidate.generation,
            commit=candidate.commit,
            tree=candidate.tree,
            archive_sha256=candidate.archive_sha256,
        )
        release = root / "releases" / candidate.generation
        migrations = self._verified_migrations(candidate, release)
        try:
            migration = self._migrate(
                parameters["root_id"], candidate.generation, release, migrations, backup_receipt,
            )
            if migration.get("status") not in {"APPLIED", "ALREADY_APPLIED"}:
                raise RuntimeError("candidate migration provider did not establish the required schema")
            migration_receipt = migration.get("receipt")
            if not isinstance(migration_receipt, str) or not migration_receipt:
                raise ValueError("candidate migration provider did not return an immutable receipt")
        except Exception as migration_error:
            try:
                restored = self._restore_migration_backup(parameters["root_id"], backup_receipt)
                restore_receipt = restored.get("receipt")
                if restored.get("status") != "RESTORED" or not isinstance(restore_receipt, str) or not restore_receipt:
                    raise RuntimeError("migration backup restoration was not proven")
            except Exception as restore_error:
                raise MigrationAmbiguityError(
                    "candidate migration failed and database restoration is ambiguous"
                ) from restore_error
            raise RuntimeError("candidate migration failed; predecessor database was restored") from migration_error
        selection = select(
            root,
            candidate.generation,
            expected_current=parameters["expected_current"],
            operation_id=parameters["operation_id"],
        )
        verification = verify(root, candidate.generation)
        health = self._health(parameters["root_id"], candidate.generation)
        if health.get("status") != "healthy":
            raise RuntimeError("selected generation failed health verification")
        health_receipt = health.get("receipt")
        if not isinstance(health_receipt, str) or not health_receipt:
            raise ValueError("health provider did not return an immutable receipt")
        return {
            "evidence": [
                backup_receipt,
                candidate.review_receipt,
                candidate.controller_receipt,
                f"manifest:{manifest['content_manifest_sha256']}",
                migration_receipt,
                f"selection:{selection['operation_id']}",
                f"verification:{verification['status']}",
                health_receipt,
            ]
        }

    def rollback(self, parameters: Mapping[str, str]) -> Mapping[str, Any]:
        root = self._resolve(self._roots, parameters["root_id"], "release root")
        source = root / "receipts" / f"{parameters['operation_id']}.json"
        result = rollback(
            root,
            source,
            expected_current=parameters["generation"],
            operation_id=f"{parameters['operation_id']}-rollback",
        )
        return {"receipt": f"rollback:{result['operation_id']}"}

    @staticmethod
    def _resolve(mounts: Mapping[str, Path], identity: str, label: str) -> Path:
        try:
            return mounts[identity]
        except KeyError as exc:
            raise ValueError(f"unknown mounted {label}: {identity}") from exc
