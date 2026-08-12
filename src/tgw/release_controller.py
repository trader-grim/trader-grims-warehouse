"""Mountable typed controller for immutable reviewed release candidates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

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


class MountedReleaseController:
    """Resolve symbolic mounts and drive the non-shell release installer."""

    def __init__(
        self,
        *,
        roots: Mapping[str, Path],
        artifacts: Mapping[str, Path],
        backup: Callable[[str, str], Mapping[str, Any]],
        health: Callable[[str, str], Mapping[str, Any]],
    ) -> None:
        self._roots = {identity: Path(path) for identity, path in roots.items()}
        self._artifacts = {identity: Path(path) for identity, path in artifacts.items()}
        self._backup = backup
        self._health = health

    @staticmethod
    def _candidate(parameters: Mapping[str, str]) -> ReviewedCandidate:
        return ReviewedCandidate(
            generation=parameters["generation"],
            commit=parameters["candidate_commit"],
            tree=parameters["candidate_tree"],
            archive_sha256=parameters["archive_sha256"].removeprefix("sha256:"),
            artifact_ref=parameters["artifact_ref"],
            review_receipt=parameters["review_receipt"],
            controller_receipt=parameters["controller_receipt"],
        )

    def install(self, parameters: Mapping[str, str]) -> Mapping[str, Any]:
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
