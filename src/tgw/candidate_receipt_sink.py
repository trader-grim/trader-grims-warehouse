"""Pinned immutable receipt retrieval for governed candidate admission.

Candidate evidence is not accepted from a reviewer's worktree or from paths
named by the candidate.  The trust boundary is a separately provisioned sink
descriptor which pins an external Git commit and tree.  Every artifact is read
from that Git object, checked against the sink manifest, then checked again
against the bundle that names it.

This module deliberately has no writer and no default sink.  Installing a
trusted receipt service or its trust anchor remains an operational action;
without a configured, externally located sink the admission gate holds.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from tgw.governed_coding import admission_gate
from tgw.governed_execution_receipt import (
    GovernedExecutionReceiptError,
    verify_candidate_governed_execution_receipt,
)

RECEIPT_SINK_SCHEMA = "tgw-pinned-git-candidate-receipt-sink/v1"
RECEIPT_SINK_MANIFEST_SCHEMA = "tgw-pinned-git-candidate-receipt-sink-manifest/v1"
GOVERNED_EXECUTION_BUNDLE_SCHEMA = "tgw-candidate-governed-execution-bundle/v1"
GOVERNED_CANDIDATE_ADMISSION_SCHEMA = "tgw-governed-candidate-admission-gate/v1"

GOVERNED_ROLES = (
    "implementation",
    "independent-review",
    "controller-verification",
)

_GIT_OBJECT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SINK_ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_ARTIFACT_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@=-]{0,255}\Z")
_BUNDLE_ARTIFACTS = (
    "candidate_receipt",
    "card",
    "resource_receipt",
    "role_receipt",
    "resource_service_catalog",
)


class CandidateReceiptSinkError(ValueError):
    """The configured immutable receipt sink cannot establish candidate evidence."""


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise CandidateReceiptSinkError("receipt sink value is not canonical JSON data") from exc


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _hash(value: Any) -> str:
    return _hash_bytes(_canonical(value))


def _safe_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise CandidateReceiptSinkError(f"{label} is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value:
        raise CandidateReceiptSinkError(f"{label} must be a contained Git path")
    return value


def _object(value: bytes, *, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateReceiptSinkError(f"{label} is not JSON") from exc
    if not isinstance(parsed, dict):
        raise CandidateReceiptSinkError(f"{label} must be a JSON object")
    return parsed


def _git(repository: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", *args], cwd=repository, check=True, capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CandidateReceiptSinkError("pinned receipt sink Git object is unavailable") from exc


def _candidate_identity(repository: Path, candidate: str) -> tuple[str, str]:
    commit = _git(repository, "rev-parse", f"{candidate}^{{commit}}").decode().strip()
    tree = _git(repository, "rev-parse", f"{commit}^{{tree}}").decode().strip()
    if _GIT_OBJECT.fullmatch(commit) is None or _GIT_OBJECT.fullmatch(tree) is None:
        raise CandidateReceiptSinkError("candidate Git identity is invalid")
    return commit, tree


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def governed_execution_bundle_ref(source_commit: str, role: str) -> str:
    """Return the one sink reference reserved for a role's evidence bundle."""

    if _GIT_OBJECT.fullmatch(source_commit) is None or role not in GOVERNED_ROLES:
        raise CandidateReceiptSinkError("governed execution bundle identity is invalid")
    return f"candidate:{source_commit}:governed-execution:{role}"


def validate_receipt_sink_descriptor(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a configuration which pins a Git receipt sink, not a mutable ref."""

    required = {
        "schema", "sink_id", "repository", "commit", "tree", "manifest_path",
        "manifest_content_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != required or value.get("schema") != RECEIPT_SINK_SCHEMA:
        raise CandidateReceiptSinkError("pinned receipt sink descriptor is invalid")
    if not isinstance(value.get("sink_id"), str) or _SINK_ID.fullmatch(value["sink_id"]) is None:
        raise CandidateReceiptSinkError("pinned receipt sink identity is invalid")
    repository = value.get("repository")
    if not isinstance(repository, str) or not repository or not Path(repository).is_absolute():
        raise CandidateReceiptSinkError("pinned receipt sink repository must be absolute")
    for field in ("commit", "tree"):
        if not isinstance(value.get(field), str) or _GIT_OBJECT.fullmatch(value[field]) is None:
            raise CandidateReceiptSinkError("pinned receipt sink Git identity is invalid")
    if not isinstance(value.get("manifest_content_sha256"), str) or _SHA256.fullmatch(value["manifest_content_sha256"]) is None:
        raise CandidateReceiptSinkError("pinned receipt sink manifest content hash is invalid")
    return {
        "schema": RECEIPT_SINK_SCHEMA,
        "sink_id": value["sink_id"],
        "repository": repository,
        "commit": value["commit"],
        "tree": value["tree"],
        "manifest_path": _safe_path(value["manifest_path"], label="pinned receipt sink manifest path"),
        "manifest_content_sha256": value["manifest_content_sha256"],
    }


def load_receipt_sink_descriptor(path: str | Path, *, candidate_repository: Path) -> dict[str, Any]:
    """Load a separately configured sink descriptor and reject candidate-local config."""

    try:
        descriptor_path = Path(path).resolve(strict=True)
        candidate_root = candidate_repository.resolve(strict=True)
    except OSError as exc:
        raise CandidateReceiptSinkError("pinned receipt sink configuration is unavailable") from exc
    if _is_within(descriptor_path, candidate_root):
        raise CandidateReceiptSinkError("pinned receipt sink configuration must be outside the candidate repository")
    try:
        value = json.loads(descriptor_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateReceiptSinkError("pinned receipt sink configuration is unreadable") from exc
    return validate_receipt_sink_descriptor(value)


def _validate_manifest(value: Mapping[str, Any], *, descriptor: Mapping[str, Any]) -> dict[str, Any]:
    required = {"schema", "sink_id", "artifacts", "manifest_hash"}
    if not isinstance(value, Mapping) or set(value) != required or value.get("schema") != RECEIPT_SINK_MANIFEST_SCHEMA:
        raise CandidateReceiptSinkError("pinned receipt sink manifest is invalid")
    if value.get("sink_id") != descriptor["sink_id"]:
        raise CandidateReceiptSinkError("pinned receipt sink manifest identity mismatch")
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise CandidateReceiptSinkError("pinned receipt sink manifest is empty")
    seen_refs: set[str] = set()
    seen_paths: set[str] = set()
    normalized: dict[str, dict[str, str]] = {}
    for artifact in artifacts:
        if not isinstance(artifact, Mapping) or set(artifact) != {"ref", "path", "content_sha256"}:
            raise CandidateReceiptSinkError("pinned receipt sink artifact is invalid")
        ref = artifact["ref"]
        if not isinstance(ref, str) or _ARTIFACT_REF.fullmatch(ref) is None or ref in seen_refs:
            raise CandidateReceiptSinkError("pinned receipt sink artifact reference is invalid")
        artifact_path = _safe_path(artifact["path"], label="pinned receipt sink artifact path")
        if artifact_path in seen_paths:
            raise CandidateReceiptSinkError("pinned receipt sink artifact path is duplicated")
        digest = artifact["content_sha256"]
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise CandidateReceiptSinkError("pinned receipt sink artifact hash is invalid")
        seen_refs.add(ref)
        seen_paths.add(artifact_path)
        normalized[ref] = {"path": artifact_path, "content_sha256": digest}
    unsigned = dict(value)
    claimed = unsigned.pop("manifest_hash")
    if not isinstance(claimed, str) or _SHA256.fullmatch(claimed) is None or claimed != _hash(unsigned):
        raise CandidateReceiptSinkError("pinned receipt sink manifest hash is invalid")
    return {
        "schema": RECEIPT_SINK_MANIFEST_SCHEMA,
        "sink_id": descriptor["sink_id"],
        "artifacts": normalized,
        "manifest_hash": claimed,
    }


class PinnedGitReceiptSink:
    """Read artifact blobs only from one externally pinned Git tree."""

    def __init__(self, descriptor: Mapping[str, Any], *, candidate_repository: Path) -> None:
        self._descriptor = validate_receipt_sink_descriptor(descriptor)
        try:
            self._repository = Path(self._descriptor["repository"]).resolve(strict=True)
            candidate_root = candidate_repository.resolve(strict=True)
        except OSError as exc:
            raise CandidateReceiptSinkError("pinned receipt sink repository is unavailable") from exc
        if _is_within(self._repository, candidate_root):
            raise CandidateReceiptSinkError("pinned receipt sink repository must be outside the candidate repository")
        commit, tree = _candidate_identity(self._repository, self._descriptor["commit"])
        if commit != self._descriptor["commit"] or tree != self._descriptor["tree"]:
            raise CandidateReceiptSinkError("pinned receipt sink Git identity mismatch")
        raw_manifest = self._show(self._descriptor["manifest_path"])
        if _hash_bytes(raw_manifest) != self._descriptor["manifest_content_sha256"]:
            raise CandidateReceiptSinkError("pinned receipt sink manifest content hash mismatch")
        self._manifest = _validate_manifest(_object(raw_manifest, label="pinned receipt sink manifest"), descriptor=self._descriptor)

    def _show(self, path: str) -> bytes:
        return _git(self._repository, "show", f"{self._descriptor['commit']}:{path}")

    @property
    def identity(self) -> dict[str, str]:
        return {
            "sink_id": self._descriptor["sink_id"],
            "commit": self._descriptor["commit"],
            "tree": self._descriptor["tree"],
            "manifest_hash": self._manifest["manifest_hash"],
        }

    def fetch_bytes(self, ref: str) -> bytes:
        """Retrieve a manifest-listed blob and verify its immutable content hash."""

        if not isinstance(ref, str) or _ARTIFACT_REF.fullmatch(ref) is None:
            raise CandidateReceiptSinkError("pinned receipt sink artifact reference is invalid")
        try:
            artifact = self._manifest["artifacts"][ref]
        except KeyError as exc:
            raise CandidateReceiptSinkError("pinned receipt sink artifact is not retained") from exc
        value = self._show(artifact["path"])
        if _hash_bytes(value) != artifact["content_sha256"]:
            raise CandidateReceiptSinkError("pinned receipt sink artifact content hash mismatch")
        return value

    def fetch_object(self, ref: str) -> dict[str, Any]:
        return _object(self.fetch_bytes(ref), label="pinned receipt sink artifact")


def validate_governed_execution_bundle(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the compact pointer bundle stored by a receipt sink."""

    required = {
        "schema", "source_commit", "source_tree", "plan_commit", "role",
        *_BUNDLE_ARTIFACTS, "bundle_hash",
    }
    if not isinstance(value, Mapping) or set(value) != required or value.get("schema") != GOVERNED_EXECUTION_BUNDLE_SCHEMA:
        raise CandidateReceiptSinkError("governed execution evidence bundle is invalid")
    for field in ("source_commit", "source_tree", "plan_commit"):
        if not isinstance(value.get(field), str) or _GIT_OBJECT.fullmatch(value[field]) is None:
            raise CandidateReceiptSinkError("governed execution evidence bundle Git binding is invalid")
    if value.get("role") not in GOVERNED_ROLES:
        raise CandidateReceiptSinkError("governed execution evidence bundle role is invalid")
    result = dict(value)
    for name in _BUNDLE_ARTIFACTS:
        pointer = value.get(name)
        if not isinstance(pointer, Mapping) or set(pointer) != {"ref", "content_sha256"}:
            raise CandidateReceiptSinkError("governed execution evidence bundle artifact pointer is invalid")
        ref = pointer["ref"]
        digest = pointer["content_sha256"]
        if not isinstance(ref, str) or _ARTIFACT_REF.fullmatch(ref) is None or not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise CandidateReceiptSinkError("governed execution evidence bundle artifact pointer is invalid")
        result[name] = {"ref": ref, "content_sha256": digest}
    unsigned = dict(value)
    claimed = unsigned.pop("bundle_hash")
    if not isinstance(claimed, str) or _SHA256.fullmatch(claimed) is None or claimed != _hash(unsigned):
        raise CandidateReceiptSinkError("governed execution evidence bundle hash is invalid")
    return result


def _bundle_artifact(sink: PinnedGitReceiptSink, pointer: Mapping[str, str]) -> dict[str, Any]:
    value = sink.fetch_bytes(pointer["ref"])
    if _hash_bytes(value) != pointer["content_sha256"]:
        raise CandidateReceiptSinkError("governed execution evidence bundle artifact hash mismatch")
    return _object(value, label="governed execution evidence bundle artifact")


def verify_governed_execution_bundle(
    sink: PinnedGitReceiptSink, *, source_commit: str, source_tree: str, plan_commit: str, role: str,
) -> dict[str, Any]:
    """Fetch and verify one role's complete evidence only from the pinned sink."""

    bundle = validate_governed_execution_bundle(sink.fetch_object(governed_execution_bundle_ref(source_commit, role)))
    if (
        bundle["source_commit"] != source_commit
        or bundle["source_tree"] != source_tree
        or bundle["plan_commit"] != plan_commit
        or bundle["role"] != role
    ):
        raise CandidateReceiptSinkError("governed execution evidence bundle candidate binding mismatch")
    artifacts = {name: _bundle_artifact(sink, bundle[name]) for name in _BUNDLE_ARTIFACTS}
    try:
        receipt = verify_candidate_governed_execution_receipt(
            artifacts["candidate_receipt"],
            card=artifacts["card"],
            resource_receipt=artifacts["resource_receipt"],
            role_receipt=artifacts["role_receipt"],
            resource_service_catalog=artifacts["resource_service_catalog"],
            source_commit=source_commit,
            source_tree=source_tree,
            plan_commit=plan_commit,
        )
    except GovernedExecutionReceiptError as exc:
        raise CandidateReceiptSinkError("governed execution evidence bundle cannot be verified") from exc
    if receipt["role"] != role or artifacts["role_receipt"].get("role") != role:
        raise CandidateReceiptSinkError("governed execution evidence bundle role mismatch")
    return {"receipt": receipt, "role_receipt": artifacts["role_receipt"], "bundle_hash": bundle["bundle_hash"]}


def candidate_admission_gate(
    repository: Path, *, candidate: str, plan_commit: str, sink: PinnedGitReceiptSink,
) -> dict[str, Any]:
    """Fail-closed governed-role admission for one closed candidate Git object."""

    repo = repository.resolve()
    source_commit, source_tree = _candidate_identity(repo, candidate)
    if not isinstance(plan_commit, str) or _GIT_OBJECT.fullmatch(plan_commit) is None:
        raise CandidateReceiptSinkError("candidate admission Plan commit is invalid")
    reasons: list[str] = []
    governed_receipts: list[dict[str, Any]] = []
    role_receipts: list[dict[str, Any]] = []
    bundle_hashes: list[str] = []
    for role in GOVERNED_ROLES:
        try:
            verified = verify_governed_execution_bundle(
                sink, source_commit=source_commit, source_tree=source_tree,
                plan_commit=plan_commit, role=role,
            )
        except CandidateReceiptSinkError:
            reasons.append(f"missing-or-invalid-governed-evidence:{role}")
            continue
        governed_receipts.append(verified["receipt"])
        role_receipts.append(verified["role_receipt"])
        bundle_hashes.append(verified["bundle_hash"])
    role_gate: dict[str, Any] | None = None
    if len(role_receipts) == len(GOVERNED_ROLES):
        try:
            role_gate = admission_gate(role_receipts)
        except ValueError:
            reasons.append("invalid-governed-role-receipt")
        else:
            reasons.extend(f"role-gate:{reason}" for reason in role_gate["reasons"])
    unsigned = {
        "schema": GOVERNED_CANDIDATE_ADMISSION_SCHEMA,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "plan_commit": plan_commit,
        "receipt_sink": sink.identity,
        "allowed": not reasons,
        "reasons": sorted(set(reasons)),
        "governed_execution_receipt_hashes": sorted(receipt["receipt_hash"] for receipt in governed_receipts),
        "bundle_hashes": sorted(bundle_hashes),
        "role_gate": role_gate,
    }
    return {**unsigned, "gate_hash": _hash(unsigned)}
