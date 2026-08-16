"""Exact W09 bootstrap-deployment preflight contracts.

The authority effect carries only a reference and hash for a contract retained
in X.  Before an effect can cross the authority execution boundary, this
module re-derives the W08 candidate identity from S through the externally
pinned D descriptor.  It intentionally has no writer, deployment provider,
or default resolver: retaining a contract and mounting a deployment provider
remain separate operator actions.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Protocol

from tgw.candidate_receipt_sink import (
    CandidateReceiptSinkError,
    PinnedCandidateEvidenceDescriptor,
    PinnedGitReceiptSink,
    resolve_approved_plan_authority,
    verify_candidate_evidence_bundle,
)

BOOTSTRAP_DEPLOYMENT_CONTRACT_SCHEMA = "tgw-bootstrap-deployment-contract/v1"
BOOTSTRAP_DEPLOYMENT_DECLARATION_SCHEMA = "tgw-bootstrap-deployment-declaration/v1"
BOOTSTRAP_HEALTH_CONTRACT_SCHEMA = "tgw-bootstrap-health-contract/v1"
BOOTSTRAP_ROLLBACK_CONTRACT_SCHEMA = "tgw-bootstrap-rollback-contract/v1"

_GIT_OBJECT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_GENERATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_PROBE_ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_NIX_SYSTEM = re.compile(
    r"/nix/store/[0-9abcdfghijklmnpqrsvwxyz]{32}-nixos-system-tgw-prod-[A-Za-z0-9._+-]+\Z"
)
_CONTRACT_REF = re.compile(r"candidate:([0-9a-f]{40}):bootstrap-deployment:v1\Z")


class BootstrapDeploymentContractError(ValueError):
    """An external bootstrap contract cannot establish a safe deployment intent."""


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise BootstrapDeploymentContractError("bootstrap deployment contract is not canonical JSON") from exc


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _mapping(value: Any, *, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise BootstrapDeploymentContractError(f"{label} is invalid")
    return dict(value)


def _hash_value(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise BootstrapDeploymentContractError(f"{label} is invalid")
    return value


def _git_value(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _GIT_OBJECT.fullmatch(value) is None:
        raise BootstrapDeploymentContractError(f"{label} is invalid")
    return value


def _generation(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _GENERATION.fullmatch(value) is None:
        raise BootstrapDeploymentContractError(f"{label} is invalid")
    return value


def _nix_system(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _NIX_SYSTEM.fullmatch(value) is None:
        raise BootstrapDeploymentContractError(f"{label} must be an exact tgw-prod Nix closure")
    return value


def _safe_git_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise BootstrapDeploymentContractError(f"{label} is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value:
        raise BootstrapDeploymentContractError(f"{label} must be a contained Git path")
    return value


def _candidate_identity(repository: Path, candidate: str) -> tuple[str, str]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", f"{candidate}^{{commit}}"], cwd=repository, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "rev-parse", f"{commit}^{{tree}}"], cwd=repository, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BootstrapDeploymentContractError("candidate Git identity is unavailable") from exc
    return _git_value(commit, label="candidate commit"), _git_value(tree, label="candidate tree")


def bootstrap_deployment_contract_ref(candidate_commit: str) -> str:
    """Return the one X-store reference allowed for a candidate's W09 contract."""

    return f"candidate:{_git_value(candidate_commit, label='candidate commit')}:bootstrap-deployment:v1"


def _validate_target(value: Any) -> dict[str, str]:
    target = _mapping(value, fields={"host", "flake_repository_id"}, label="bootstrap target")
    if target.get("host") != "tgw-prod" or target.get("flake_repository_id") != "tgw-flake":
        raise BootstrapDeploymentContractError("bootstrap target is outside the registered production bound")
    return {"host": "tgw-prod", "flake_repository_id": "tgw-flake"}


def _validate_descriptor_identity(value: Any) -> dict[str, str]:
    descriptor = _mapping(
        value,
        fields={"repository", "commit", "tree", "path", "content_sha256", "descriptor_hash"},
        label="candidate evidence descriptor identity",
    )
    repository = descriptor.get("repository")
    if not isinstance(repository, str) or not repository or not Path(repository).is_absolute():
        raise BootstrapDeploymentContractError("candidate evidence descriptor repository is invalid")
    return {
        "repository": repository,
        "commit": _git_value(descriptor.get("commit"), label="candidate evidence descriptor commit"),
        "tree": _git_value(descriptor.get("tree"), label="candidate evidence descriptor tree"),
        "path": _safe_git_path(descriptor.get("path"), label="candidate evidence descriptor path"),
        "content_sha256": _hash_value(descriptor.get("content_sha256"), label="candidate evidence descriptor content hash"),
        "descriptor_hash": _hash_value(descriptor.get("descriptor_hash"), label="candidate evidence descriptor hash"),
    }


def _validate_sink_descriptor(value: Any) -> dict[str, str]:
    descriptor = _mapping(
        value,
        fields={"schema", "sink_id", "repository", "commit", "tree", "manifest_path", "manifest_content_sha256"},
        label="candidate evidence sink descriptor",
    )
    if descriptor.get("schema") != "tgw-pinned-git-candidate-receipt-sink/v1":
        raise BootstrapDeploymentContractError("candidate evidence sink descriptor schema is invalid")
    sink_id = descriptor.get("sink_id")
    repository = descriptor.get("repository")
    if not isinstance(sink_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", sink_id):
        raise BootstrapDeploymentContractError("candidate evidence sink identity is invalid")
    if not isinstance(repository, str) or not repository or not Path(repository).is_absolute():
        raise BootstrapDeploymentContractError("candidate evidence sink repository is invalid")
    return {
        "schema": "tgw-pinned-git-candidate-receipt-sink/v1",
        "sink_id": sink_id,
        "repository": repository,
        "commit": _git_value(descriptor.get("commit"), label="candidate evidence sink commit"),
        "tree": _git_value(descriptor.get("tree"), label="candidate evidence sink tree"),
        "manifest_path": _safe_git_path(descriptor.get("manifest_path"), label="candidate evidence sink manifest path"),
        "manifest_content_sha256": _hash_value(
            descriptor.get("manifest_content_sha256"), label="candidate evidence sink manifest content hash",
        ),
    }


def _validate_declaration(value: Mapping[str, Any]) -> dict[str, Any]:
    declaration = _mapping(
        value,
        fields={"schema", "target", "expected_prior_closure", "intended_next_closure", "health_probes"},
        label="bootstrap deployment declaration",
    )
    if declaration.get("schema") != BOOTSTRAP_DEPLOYMENT_DECLARATION_SCHEMA:
        raise BootstrapDeploymentContractError("bootstrap deployment declaration schema is invalid")
    probes = declaration.get("health_probes")
    if (
        not isinstance(probes, list)
        or not probes
        or not all(isinstance(probe, str) and _PROBE_ID.fullmatch(probe) is not None for probe in probes)
        or probes != sorted(set(probes))
    ):
        raise BootstrapDeploymentContractError("bootstrap health probes must be a sorted unique list")
    return {
        "target": _validate_target(declaration.get("target")),
        "expected_prior_closure": _nix_system(
            declaration.get("expected_prior_closure"), label="expected prior closure",
        ),
        "intended_next_closure": _nix_system(
            declaration.get("intended_next_closure"), label="intended next closure",
        ),
        "health_probes": list(probes),
    }


def _validate_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    contract = _mapping(
        value,
        fields={
            "schema", "candidate", "plan", "release", "rollback", "deployment", "typed_effects",
            "health_contract", "rollback_contract", "contract_hash",
        },
        label="bootstrap deployment contract",
    )
    if contract.get("schema") != BOOTSTRAP_DEPLOYMENT_CONTRACT_SCHEMA:
        raise BootstrapDeploymentContractError("bootstrap deployment contract schema is invalid")
    candidate = _mapping(
        contract.get("candidate"),
        fields={"commit", "tree", "manifest_hash", "candidate_evidence"},
        label="bootstrap candidate binding",
    )
    candidate_evidence = _mapping(
        candidate.get("candidate_evidence"), fields={"descriptor", "sink", "bundle_hash"},
        label="bootstrap candidate evidence binding",
    )
    normalized_candidate = {
        "commit": _git_value(candidate.get("commit"), label="bootstrap candidate commit"),
        "tree": _git_value(candidate.get("tree"), label="bootstrap candidate tree"),
        "manifest_hash": _hash_value(candidate.get("manifest_hash"), label="bootstrap candidate manifest hash"),
        "candidate_evidence": {
            "descriptor": _validate_descriptor_identity(candidate_evidence.get("descriptor")),
            "sink": _validate_sink_descriptor(candidate_evidence.get("sink")),
            "bundle_hash": _hash_value(candidate_evidence.get("bundle_hash"), label="candidate evidence bundle hash"),
        },
    }
    plan = _mapping(contract.get("plan"), fields={"commit"}, label="bootstrap Plan binding")
    release = _mapping(contract.get("release"), fields={"manifest_hash"}, label="bootstrap release binding")
    rollback = _mapping(contract.get("rollback"), fields={"manifest_hash"}, label="bootstrap rollback binding")
    deployment = _mapping(
        contract.get("deployment"), fields={"target", "expected_prior", "intended_next"},
        label="bootstrap deployment binding",
    )
    expected_prior = _mapping(
        deployment.get("expected_prior"), fields={"generation", "closure"}, label="expected prior deployment",
    )
    intended_next = _mapping(
        deployment.get("intended_next"), fields={"generation", "closure"}, label="intended next deployment",
    )
    normalized_deployment = {
        "target": _validate_target(deployment.get("target")),
        "expected_prior": {
            "generation": _generation(expected_prior.get("generation"), label="expected prior generation"),
            "closure": _nix_system(expected_prior.get("closure"), label="expected prior closure"),
        },
        "intended_next": {
            "generation": _generation(intended_next.get("generation"), label="intended next generation"),
            "closure": _nix_system(intended_next.get("closure"), label="intended next closure"),
        },
    }
    if normalized_deployment["expected_prior"] == normalized_deployment["intended_next"]:
        raise BootstrapDeploymentContractError("bootstrap successor must differ from the expected prior deployment")
    typed_effects = _mapping(
        contract.get("typed_effects"), fields={"install", "rollback"}, label="bootstrap typed effects",
    )
    expected_install = {
        "kind": "nixos-reviewed-generation-switch@2",
        "target_host": "tgw-prod",
        "generation": normalized_deployment["intended_next"]["generation"],
        "closure": normalized_deployment["intended_next"]["closure"],
    }
    expected_rollback = {
        "kind": "nixos-reviewed-generation-rollback@2",
        "target_host": "tgw-prod",
        "generation": normalized_deployment["expected_prior"]["generation"],
        "closure": normalized_deployment["expected_prior"]["closure"],
    }
    if typed_effects.get("install") != expected_install or typed_effects.get("rollback") != expected_rollback:
        raise BootstrapDeploymentContractError("bootstrap typed effects do not match the exact deployment binding")
    health = _mapping(
        contract.get("health_contract"), fields={"schema", "target_host", "generation", "closure", "required_probes"},
        label="bootstrap health contract",
    )
    if (
        health.get("schema") != BOOTSTRAP_HEALTH_CONTRACT_SCHEMA
        or health.get("target_host") != "tgw-prod"
        or health.get("generation") != normalized_deployment["intended_next"]["generation"]
        or health.get("closure") != normalized_deployment["intended_next"]["closure"]
        or not isinstance(health.get("required_probes"), list)
        or not health["required_probes"]
        or not all(isinstance(probe, str) and _PROBE_ID.fullmatch(probe) is not None for probe in health["required_probes"])
        or health["required_probes"] != sorted(set(health["required_probes"]))
    ):
        raise BootstrapDeploymentContractError("bootstrap health contract is invalid")
    rollback_contract = _mapping(
        contract.get("rollback_contract"),
        fields={"schema", "target_host", "generation", "closure", "rollback_manifest_hash"},
        label="bootstrap rollback contract",
    )
    if (
        rollback_contract.get("schema") != BOOTSTRAP_ROLLBACK_CONTRACT_SCHEMA
        or rollback_contract.get("target_host") != "tgw-prod"
        or rollback_contract.get("generation") != normalized_deployment["expected_prior"]["generation"]
        or rollback_contract.get("closure") != normalized_deployment["expected_prior"]["closure"]
        or rollback_contract.get("rollback_manifest_hash") != rollback["manifest_hash"]
    ):
        raise BootstrapDeploymentContractError("bootstrap rollback contract is invalid")
    unsigned = dict(contract)
    claimed_hash = unsigned.pop("contract_hash")
    if _hash_value(claimed_hash, label="bootstrap deployment contract hash") != _hash(unsigned):
        raise BootstrapDeploymentContractError("bootstrap deployment contract hash mismatch")
    return {
        **unsigned,
        "candidate": normalized_candidate,
        "plan": {"commit": _git_value(plan.get("commit"), label="bootstrap Plan commit")},
        "release": {"manifest_hash": _hash_value(release.get("manifest_hash"), label="bootstrap release manifest hash")},
        "rollback": {"manifest_hash": _hash_value(rollback.get("manifest_hash"), label="bootstrap rollback manifest hash")},
        "deployment": normalized_deployment,
        "typed_effects": {"install": expected_install, "rollback": expected_rollback},
        "health_contract": {
            "schema": BOOTSTRAP_HEALTH_CONTRACT_SCHEMA,
            "target_host": "tgw-prod",
            "generation": normalized_deployment["intended_next"]["generation"],
            "closure": normalized_deployment["intended_next"]["closure"],
            "required_probes": list(health["required_probes"]),
        },
        "rollback_contract": {
            "schema": BOOTSTRAP_ROLLBACK_CONTRACT_SCHEMA,
            "target_host": "tgw-prod",
            "generation": normalized_deployment["expected_prior"]["generation"],
            "closure": normalized_deployment["expected_prior"]["closure"],
            "rollback_manifest_hash": rollback["manifest_hash"],
        },
        "contract_hash": claimed_hash,
    }


def _verified_w08_evidence(
    repository: Path,
    *,
    candidate: str,
    plan_repository: Path,
    plan_approved_ref: str,
    candidate_evidence_descriptor: PinnedCandidateEvidenceDescriptor,
) -> tuple[Path, str, str, str, PinnedGitReceiptSink, dict[str, Any]]:
    if not isinstance(candidate_evidence_descriptor, PinnedCandidateEvidenceDescriptor):
        raise BootstrapDeploymentContractError("candidate evidence descriptor must be externally pinned")
    try:
        repo = repository.resolve(strict=True)
    except OSError as exc:
        raise BootstrapDeploymentContractError("candidate repository is unavailable") from exc
    try:
        plan_authority = resolve_approved_plan_authority(
            plan_repository, approved_ref=plan_approved_ref, candidate_repository=repo,
        )
        candidate_sink = PinnedGitReceiptSink(
            candidate_evidence_descriptor.candidate_evidence_sink_descriptor,
            candidate_repository=repo,
        )
        source_commit, source_tree = _candidate_identity(repo, candidate)
        evidence = verify_candidate_evidence_bundle(
            candidate_sink, repository=repo, source_commit=source_commit, source_tree=source_tree,
            plan_commit=plan_authority["approved_commit"],
        )
    except CandidateReceiptSinkError as exc:
        raise BootstrapDeploymentContractError("exact W08 candidate evidence is unavailable") from exc
    return repo, source_commit, source_tree, plan_authority["approved_commit"], candidate_sink, evidence


def derive_bootstrap_deployment_contract(
    repository: Path,
    *,
    candidate: str,
    plan_repository: Path,
    plan_approved_ref: str,
    candidate_evidence_descriptor: PinnedCandidateEvidenceDescriptor,
    deployment_declaration: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive the canonical W09 contract from immutable W08 S/D evidence.

    The caller must retain this returned object in the separate X evidence
    store before its pin is configured.  The returned object has no ambient
    target path, credentials, commands, or service selector.
    """

    declaration = _validate_declaration(deployment_declaration)
    _repo, source_commit, source_tree, plan_commit, candidate_sink, evidence = _verified_w08_evidence(
        repository, candidate=candidate, plan_repository=plan_repository,
        plan_approved_ref=plan_approved_ref, candidate_evidence_descriptor=candidate_evidence_descriptor,
    )
    unsigned = {
        "schema": BOOTSTRAP_DEPLOYMENT_CONTRACT_SCHEMA,
        "candidate": {
            "commit": source_commit,
            "tree": source_tree,
            "manifest_hash": evidence["candidate_manifest_hash"],
            "candidate_evidence": {
                "descriptor": candidate_evidence_descriptor.identity,
                "sink": candidate_sink.descriptor,
                "bundle_hash": evidence["bundle_hash"],
            },
        },
        "plan": {"commit": plan_commit},
        "release": {"manifest_hash": evidence["release_manifest_hash"]},
        "rollback": {"manifest_hash": evidence["rollback_manifest_hash"]},
        "deployment": {
            "target": declaration["target"],
            "expected_prior": {
                "generation": evidence["rollback_generation"],
                "closure": declaration["expected_prior_closure"],
            },
            "intended_next": {
                "generation": evidence["release_generation"],
                "closure": declaration["intended_next_closure"],
            },
        },
        "typed_effects": {
            "install": {
                "kind": "nixos-reviewed-generation-switch@2",
                "target_host": "tgw-prod",
                "generation": evidence["release_generation"],
                "closure": declaration["intended_next_closure"],
            },
            "rollback": {
                "kind": "nixos-reviewed-generation-rollback@2",
                "target_host": "tgw-prod",
                "generation": evidence["rollback_generation"],
                "closure": declaration["expected_prior_closure"],
            },
        },
        "health_contract": {
            "schema": BOOTSTRAP_HEALTH_CONTRACT_SCHEMA,
            "target_host": "tgw-prod",
            "generation": evidence["release_generation"],
            "closure": declaration["intended_next_closure"],
            "required_probes": declaration["health_probes"],
        },
        "rollback_contract": {
            "schema": BOOTSTRAP_ROLLBACK_CONTRACT_SCHEMA,
            "target_host": "tgw-prod",
            "generation": evidence["rollback_generation"],
            "closure": declaration["expected_prior_closure"],
            "rollback_manifest_hash": evidence["rollback_manifest_hash"],
        },
    }
    contract = {**unsigned, "contract_hash": _hash(unsigned)}
    return _validate_contract(contract)


@dataclass(frozen=True)
class VerifiedBootstrapDeploymentContract:
    """The small immutable provider input after complete S/D/X verification."""

    reference: str
    contract_hash: str
    intended_next_generation: str

    def provider_binding(self) -> dict[str, str]:
        """Return precisely the immutable reference/hash accepted by a provider."""

        return {
            "bootstrap_contract_ref": self.reference,
            "bootstrap_contract_hash": self.contract_hash,
        }


class BootstrapDeploymentContractResolver(Protocol):
    """Resolve one canonical immutable W09 contract without ambient lookup."""

    def resolve(
        self, bootstrap_contract_ref: str, bootstrap_contract_hash: str,
    ) -> VerifiedBootstrapDeploymentContract: ...


class PinnedBootstrapDeploymentContractResolver:
    """Resolve X-retained W09 contracts against exact candidate S/D evidence."""

    def __init__(
        self,
        repository: Path,
        *,
        plan_repository: Path,
        plan_approved_ref: str,
        candidate_evidence_descriptor: PinnedCandidateEvidenceDescriptor,
        execution_evidence_sink: PinnedGitReceiptSink,
    ) -> None:
        if not isinstance(candidate_evidence_descriptor, PinnedCandidateEvidenceDescriptor):
            raise BootstrapDeploymentContractError("candidate evidence descriptor must be externally pinned")
        if not isinstance(execution_evidence_sink, PinnedGitReceiptSink):
            raise BootstrapDeploymentContractError("execution evidence sink must be externally pinned")
        try:
            repo = repository.resolve(strict=True)
            candidate_sink = PinnedGitReceiptSink(
                candidate_evidence_descriptor.candidate_evidence_sink_descriptor,
                candidate_repository=repo,
            )
        except CandidateReceiptSinkError as exc:
            raise BootstrapDeploymentContractError("candidate evidence sink is unavailable") from exc
        roots = (
            candidate_sink.repository,
            candidate_evidence_descriptor.authority_repository,
            execution_evidence_sink.repository,
        )
        if any(
            left == right or left in right.parents or right in left.parents
            for index, left in enumerate(roots) for right in roots[index + 1:]
        ):
            raise BootstrapDeploymentContractError("candidate evidence, descriptor, and execution roots must be disjoint")
        self._repository = repo
        self._plan_repository = plan_repository
        self._plan_approved_ref = plan_approved_ref
        self._candidate_evidence_descriptor = candidate_evidence_descriptor
        self._candidate_sink = candidate_sink
        self._execution_sink = execution_evidence_sink

    def resolve(
        self, bootstrap_contract_ref: str, bootstrap_contract_hash: str,
    ) -> VerifiedBootstrapDeploymentContract:
        if not isinstance(bootstrap_contract_ref, str):
            raise BootstrapDeploymentContractError("bootstrap deployment contract reference is invalid")
        match = _CONTRACT_REF.fullmatch(bootstrap_contract_ref)
        if match is None:
            raise BootstrapDeploymentContractError("bootstrap deployment contract reference is symbolic or invalid")
        expected_commit = match.group(1)
        expected_hash = _hash_value(bootstrap_contract_hash, label="bootstrap deployment contract hash")
        try:
            contract = _validate_contract(self._execution_sink.fetch_object(bootstrap_contract_ref))
            plan_authority = resolve_approved_plan_authority(
                self._plan_repository, approved_ref=self._plan_approved_ref,
                candidate_repository=self._repository,
            )
            source_commit, source_tree = _candidate_identity(self._repository, expected_commit)
            evidence = verify_candidate_evidence_bundle(
                self._candidate_sink, repository=self._repository,
                source_commit=source_commit, source_tree=source_tree,
                plan_commit=plan_authority["approved_commit"],
            )
        except CandidateReceiptSinkError as exc:
            raise BootstrapDeploymentContractError("exact W08 candidate evidence is unavailable") from exc
        candidate = contract["candidate"]
        candidate_evidence = candidate["candidate_evidence"]
        if (
            contract["contract_hash"] != expected_hash
            or candidate["commit"] != source_commit
            or candidate["tree"] != source_tree
            or candidate["manifest_hash"] != evidence["candidate_manifest_hash"]
            or candidate_evidence["descriptor"] != self._candidate_evidence_descriptor.identity
            or candidate_evidence["sink"] != self._candidate_sink.descriptor
            or candidate_evidence["bundle_hash"] != evidence["bundle_hash"]
            or contract["plan"]["commit"] != plan_authority["approved_commit"]
            or contract["release"]["manifest_hash"] != evidence["release_manifest_hash"]
            or contract["rollback"]["manifest_hash"] != evidence["rollback_manifest_hash"]
            or contract["deployment"]["expected_prior"]["generation"] != evidence["rollback_generation"]
            or contract["deployment"]["intended_next"]["generation"] != evidence["release_generation"]
        ):
            raise BootstrapDeploymentContractError("bootstrap deployment contract does not match exact W08 evidence")
        return VerifiedBootstrapDeploymentContract(
            reference=bootstrap_contract_ref,
            contract_hash=expected_hash,
            intended_next_generation=contract["deployment"]["intended_next"]["generation"],
        )
