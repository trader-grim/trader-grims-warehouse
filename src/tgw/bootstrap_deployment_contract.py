"""Exact W09 bootstrap-deployment preflight contracts.

The authority effect carries only a reference and hash for a contract retained
in a dedicated W09 Y store.  Before an effect can cross the authority execution boundary, this
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
    candidate_admission_gate,
    resolve_approved_plan_authority,
    verify_candidate_evidence_bundle,
)

BOOTSTRAP_DEPLOYMENT_CONTRACT_SCHEMA = "tgw-bootstrap-deployment-contract/v2"
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
_CONTRACT_REF = re.compile(r"candidate:([0-9a-f]{40}):bootstrap-deployment:v2\Z")


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
    """Return the one Y-store reference allowed for a candidate's W09 contract."""

    return f"candidate:{_git_value(candidate_commit, label='candidate commit')}:bootstrap-deployment:v2"


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


def _validate_sink_identity(value: Any, *, label: str) -> dict[str, str]:
    identity = _mapping(value, fields={"sink_id", "commit", "tree", "manifest_hash"}, label=label)
    sink_id = identity.get("sink_id")
    if not isinstance(sink_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", sink_id):
        raise BootstrapDeploymentContractError(f"{label} identity is invalid")
    return {
        "sink_id": sink_id,
        "commit": _git_value(identity.get("commit"), label=f"{label} commit"),
        "tree": _git_value(identity.get("tree"), label=f"{label} tree"),
        "manifest_hash": _hash_value(identity.get("manifest_hash"), label=f"{label} manifest hash"),
    }


def _governed_admission_binding(value: Mapping[str, Any]) -> dict[str, Any]:
    """Retain the exact independent X admission result needed by deployment.

    ``gate_hash`` commits to the whole admission result.  The review packet,
    result, governed execution evidence, and X review bundle are repeated here
    deliberately so an operator can inspect the production-facing contract
    without silently treating S-only candidate evidence as reviewed.
    """
    review = value.get("independent_review_evidence")
    if value.get("allowed") is not True or not isinstance(review, Mapping):
        raise BootstrapDeploymentContractError("candidate is not admitted by governed independent review")
    common_review = {
        "candidate_manifest_hash",
        "review_packet_hash",
        "review_result_hash",
        "bundle_hash",
    }
    qes_review = common_review | {"qualified_execution_proof_hash"}
    governed_review = common_review | {
        "governed_review_execution_hash", "review_execution_provider",
    }
    if set(review) not in (qes_review, governed_review):
        raise BootstrapDeploymentContractError("governed independent review binding is invalid")
    normalized_review = {
        name: _hash_value(review.get(name), label=f"independent review {name}")
        for name in sorted(set(review) - {"review_execution_provider"})
    }
    if "review_execution_provider" in review:
        provider = review["review_execution_provider"]
        if not isinstance(provider, str) or not provider:
            raise BootstrapDeploymentContractError("governed review provider identity is invalid")
        normalized_review["review_execution_provider"] = provider
    return {
        "gate_hash": _hash_value(value.get("gate_hash"), label="governed admission gate hash"),
        "execution_evidence_sink": _validate_sink_identity(
            value.get("execution_evidence_sink"), label="execution evidence sink",
        ),
        "independent_review": normalized_review,
    }


def _validate_governed_admission_binding(value: Any) -> dict[str, Any]:
    binding = _mapping(
        value,
        fields={"gate_hash", "execution_evidence_sink", "independent_review"},
        label="governed admission binding",
    )
    review_value = binding.get("independent_review")
    if not isinstance(review_value, Mapping):
        raise BootstrapDeploymentContractError("governed independent review binding is invalid")
    common = {"candidate_manifest_hash", "review_packet_hash", "review_result_hash", "bundle_hash"}
    if set(review_value) not in (
        common | {"qualified_execution_proof_hash"},
        common | {"governed_review_execution_hash", "review_execution_provider"},
    ):
        raise BootstrapDeploymentContractError("governed independent review binding is invalid")
    review = dict(review_value)
    normalized_review = {
        name: _hash_value(review.get(name), label=f"independent review {name}")
        for name in sorted(set(review) - {"review_execution_provider"})
    }
    if "review_execution_provider" in review:
        provider = review["review_execution_provider"]
        if not isinstance(provider, str) or not provider:
            raise BootstrapDeploymentContractError("governed review provider identity is invalid")
        normalized_review["review_execution_provider"] = provider
    return {
        "gate_hash": _hash_value(binding.get("gate_hash"), label="governed admission gate hash"),
        "execution_evidence_sink": _validate_sink_identity(
            binding.get("execution_evidence_sink"), label="execution evidence sink",
        ),
        "independent_review": normalized_review,
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
        fields={"commit", "tree", "manifest_hash", "candidate_evidence", "governed_admission"},
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
        "governed_admission": _validate_governed_admission_binding(candidate.get("governed_admission")),
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
    execution_evidence_sink: PinnedGitReceiptSink,
) -> tuple[Path, str, str, str, PinnedGitReceiptSink, dict[str, Any], dict[str, Any]]:
    if not isinstance(candidate_evidence_descriptor, PinnedCandidateEvidenceDescriptor):
        raise BootstrapDeploymentContractError("candidate evidence descriptor must be externally pinned")
    if not isinstance(execution_evidence_sink, PinnedGitReceiptSink):
        raise BootstrapDeploymentContractError("execution evidence sink must be externally pinned")
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
            candidate_sink, candidate_evidence_descriptor=candidate_evidence_descriptor,
            repository=repo, source_commit=source_commit, source_tree=source_tree,
            plan_commit=plan_authority["approved_commit"],
            plan_repository=Path(plan_authority["repository"]),
        )
        admission = candidate_admission_gate(
            repo,
            candidate=source_commit,
            plan_repository=plan_repository,
            plan_approved_ref=plan_approved_ref,
            candidate_evidence_descriptor=candidate_evidence_descriptor,
            execution_sink=execution_evidence_sink,
        )
    except CandidateReceiptSinkError as exc:
        raise BootstrapDeploymentContractError("exact W08 candidate evidence is unavailable") from exc
    if admission.get("allowed") is not True:
        raise BootstrapDeploymentContractError("candidate is not admitted by exact W08 governed review evidence")
    return repo, source_commit, source_tree, plan_authority["approved_commit"], candidate_sink, evidence, admission


def derive_bootstrap_deployment_contract(
    repository: Path,
    *,
    candidate: str,
    plan_repository: Path,
    plan_approved_ref: str,
    candidate_evidence_descriptor: PinnedCandidateEvidenceDescriptor,
    execution_evidence_sink: PinnedGitReceiptSink,
    deployment_declaration: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive the canonical W09 contract from immutable W08 S/D/X evidence.

    The caller must retain this returned object in a separate W09 contract
    sink, not in W08's execution/review X store.  That separation prevents a
    contract from changing the admission gate it claims to bind.  The returned
    object has no ambient target path, credentials, commands, or service
    selector.
    """

    declaration = _validate_declaration(deployment_declaration)
    _repo, source_commit, source_tree, plan_commit, candidate_sink, evidence, admission = _verified_w08_evidence(
        repository, candidate=candidate, plan_repository=plan_repository,
        plan_approved_ref=plan_approved_ref,
        candidate_evidence_descriptor=candidate_evidence_descriptor,
        execution_evidence_sink=execution_evidence_sink,
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
            "governed_admission": _governed_admission_binding(admission),
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
    expected_prior_generation: str
    expected_prior_closure: str
    intended_next_generation: str
    intended_next_closure: str
    required_health_probes: tuple[str, ...]

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
    """Resolve Y-retained W09 contracts against exact candidate S/D/X evidence."""

    def __init__(
        self,
        repository: Path,
        *,
        plan_repository: Path,
        plan_approved_ref: str,
        candidate_evidence_descriptor: PinnedCandidateEvidenceDescriptor,
        execution_evidence_sink: PinnedGitReceiptSink,
        bootstrap_contract_sink: PinnedGitReceiptSink,
    ) -> None:
        if not isinstance(candidate_evidence_descriptor, PinnedCandidateEvidenceDescriptor):
            raise BootstrapDeploymentContractError("candidate evidence descriptor must be externally pinned")
        if not isinstance(execution_evidence_sink, PinnedGitReceiptSink):
            raise BootstrapDeploymentContractError("execution evidence sink must be externally pinned")
        if not isinstance(bootstrap_contract_sink, PinnedGitReceiptSink):
            raise BootstrapDeploymentContractError("bootstrap contract sink must be externally pinned")
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
            bootstrap_contract_sink.repository,
        )
        if any(
            left == right or left in right.parents or right in left.parents
            for index, left in enumerate(roots) for right in roots[index + 1:]
        ):
            raise BootstrapDeploymentContractError("candidate evidence, descriptor, execution, and contract roots must be disjoint")
        self._repository = repo
        self._plan_repository = plan_repository
        self._plan_approved_ref = plan_approved_ref
        self._candidate_evidence_descriptor = candidate_evidence_descriptor
        self._candidate_sink = candidate_sink
        self._execution_sink = execution_evidence_sink
        self._bootstrap_contract_sink = bootstrap_contract_sink

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
            contract = _validate_contract(self._bootstrap_contract_sink.fetch_object(bootstrap_contract_ref))
            plan_authority = resolve_approved_plan_authority(
                self._plan_repository, approved_ref=self._plan_approved_ref,
                candidate_repository=self._repository,
            )
            source_commit, source_tree = _candidate_identity(self._repository, expected_commit)
            evidence = verify_candidate_evidence_bundle(
                self._candidate_sink,
                candidate_evidence_descriptor=self._candidate_evidence_descriptor,
                repository=self._repository,
                source_commit=source_commit, source_tree=source_tree,
                plan_commit=plan_authority["approved_commit"],
                plan_repository=Path(plan_authority["repository"]),
            )
            admission = candidate_admission_gate(
                self._repository,
                candidate=source_commit,
                plan_repository=self._plan_repository,
                plan_approved_ref=self._plan_approved_ref,
                candidate_evidence_descriptor=self._candidate_evidence_descriptor,
                execution_sink=self._execution_sink,
            )
        except CandidateReceiptSinkError as exc:
            raise BootstrapDeploymentContractError("exact W08 candidate evidence is unavailable") from exc
        if admission.get("allowed") is not True:
            raise BootstrapDeploymentContractError("candidate is not admitted by exact W08 governed review evidence")
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
            or candidate["governed_admission"] != _governed_admission_binding(admission)
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
            expected_prior_generation=contract["deployment"]["expected_prior"]["generation"],
            expected_prior_closure=contract["deployment"]["expected_prior"]["closure"],
            intended_next_generation=contract["deployment"]["intended_next"]["generation"],
            intended_next_closure=contract["deployment"]["intended_next"]["closure"],
            required_health_probes=tuple(contract["health_contract"]["required_probes"]),
        )
