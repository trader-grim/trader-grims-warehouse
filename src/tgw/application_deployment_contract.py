"""Pinned W09 contract for the dynamic TGW application cutover.

The existing NixOS closure is a prerequisite, not the thing W09 installs.
This contract binds the admitted source release and every host input needed to
move ``/opt/TGW/current`` without allowing the historical Nix-only bootstrap
receipt to masquerade as application installation evidence.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol, Sequence

from tgw.candidate_receipt_sink import (
    CandidateReceiptSinkError,
    PinnedCandidateEvidenceDescriptor,
    PinnedGitReceiptSink,
    candidate_admission_gate,
    candidate_evidence_bundle_ref,
    resolve_approved_plan_authority,
    validate_candidate_evidence_bundle,
    verify_candidate_evidence_bundle,
)
from tgw.plan_runtime_projection import validate_projection
from tgw.release_installer import ReleaseError, runtime_manifest_identity
from tgw.a3_preintegration_observation import (
    _held_regular,
    _inode_identity,
    _run_held_bounded,
)

SCHEMA = "tgw-governed-application-bootstrap-contract/v2"
EFFECT_SCHEMA = "tgw-approval-application-bootstrap/v1"
PLAN_COMMIT = "f0a8cf22b2c7b2f064292a048ffcb8ee98919e99"
SOLUTION_HASH = "sha256:1c3684135769e5dcabcaf130c55df160a4cecc0d3ebcee6ccd129ab97cdd709b"
CLOSURE_HASH = "sha256:5d3e52999223f7df9a5421bd0a5f6549c9f0b2965b8cca55adb5c002492ae4a5"
_PRODUCTION_RESOLVER_SEAL = object()
PROJECTION_PATH = "agent-services/plan-runtime/GOVERNED-EXECUTION-PLATFORM-f0a8cf22.json"
CONFIG_PATH = "config/tgw-api-config.json"
OPERATIONAL_CONFIG_SCHEMA = "tgw-production-operational-config/v1"
MIGRATION_PATHS = (
    "src/tgw/plan_authority.sql",
    "src/tgw/queue/migrations/20260815_terminal_lease_expiry_fence.sql",
)
STAGES = (
    "verify-admission-and-predecessor",
    "consume-bootstrap-authority",
    "quiesce-predecessor-services",
    "backup-database",
    "materialize-and-verify-release",
    "apply-plan-authority-migration",
    "apply-terminal-lease-migration",
    "stage-operational-config-and-projection",
    "compare-and-swap-generation",
    "restart-successor-services",
    "verify-full-health",
    "verify-unrelated-state",
    "persist-terminal-receipt",
)

_SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")
_GIT = re.compile(r"[0-9a-f]{40}\Z")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/=-]{0,255}\Z")
_NIX = re.compile(r"/nix/store/[0-9abcdfghijklmnpqrsvwxyz]{32}-nixos-system-tgw-prod-[A-Za-z0-9._+-]+\Z")


class ApplicationDeploymentContractError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _hash(value: Any) -> str:
    return _hash_bytes(_canonical(value))


def _exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ApplicationDeploymentContractError(f"{label} is invalid")
    return dict(value)


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise ApplicationDeploymentContractError(f"{label} is invalid")
    return value


def _identity(value: Any, label: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        raise ApplicationDeploymentContractError(f"{label} is invalid")
    return value


def _contained_path(value: Any, expected: str, label: str) -> str:
    if value != expected or PurePosixPath(str(value)).is_absolute() or ".." in PurePosixPath(str(value)).parts:
        raise ApplicationDeploymentContractError(f"{label} is not an immutable generation-relative path")
    return str(value)


def _identities(value: Any, label: str) -> list[str]:
    if (
        not isinstance(value, list) or not value or value != sorted(set(value))
        or any(not isinstance(item, str) or _IDENTITY.fullmatch(item) is None for item in value)
    ):
        raise ApplicationDeploymentContractError(f"{label} are invalid")
    return list(value)


def validate_application_deployment_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    """Reject mutable, Nix-only, reordered, or underbound W09 contracts."""

    contract = _exact(value, {
        "schema", "authorization", "candidate", "archive", "plan", "runtime_config", "migrations",
        "deployment", "services", "health_probes", "stage_order", "rollback", "operation_sink",
        "contract_hash",
    }, "application deployment contract")
    if contract["schema"] != SCHEMA:
        raise ApplicationDeploymentContractError("application deployment contract schema is invalid")

    authorization = _exact(contract["authorization"], {
        "operator_instruction", "observed_at", "capabilities", "phases", "repositories",
        "effect_set", "exclusions", "expires_at", "retirement_condition", "deployment_uses",
    }, "application authorization")
    instruction = _exact(authorization["operator_instruction"], {"ref", "content_sha256"}, "operator instruction")
    _identity(instruction["ref"], "operator instruction reference")
    _sha(instruction["content_sha256"], "operator instruction hash")
    try:
        from datetime import datetime
        observed = datetime.fromisoformat(str(authorization["observed_at"]).replace("Z", "+00:00"))
        expires = datetime.fromisoformat(str(authorization["expires_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApplicationDeploymentContractError("application authorization time is invalid") from exc
    if observed.tzinfo is None or expires.tzinfo is None or expires <= observed:
        raise ApplicationDeploymentContractError("application authorization observation/expiry is invalid")
    if (
        authorization["capabilities"] != ["coding.governed-execution@1"]
        or authorization["phases"] != ["W09"]
        or authorization["effect_set"] != ["approval-platform-bootstrap-deployment"]
        or authorization["retirement_condition"] != "W10:canonical-gate-operational"
        or authorization["deployment_uses"] != 1
    ):
        raise ApplicationDeploymentContractError("application authorization scope is invalid")
    _identities(authorization["exclusions"], "application exclusions")
    repositories = _exact(authorization["repositories"], {"source", "plan", "flake"}, "application repositories")
    for name, repository in repositories.items():
        _identity(repository, f"application {name} repository")

    candidate = _exact(contract["candidate"], {
        "commit", "tree", "archive_sha256", "candidate_evidence_bundle_hash", "manifest_hash",
        "release_manifest_hash", "rollback_manifest_hash", "admission_gate_hash",
        "predecessor_commit", "predecessor_tree", "predecessor_archive_sha256",
        "predecessor_release_manifest_hash", "predecessor_content_manifest_sha256",
    }, "application candidate")
    if any(not isinstance(candidate[name], str) or _GIT.fullmatch(candidate[name]) is None for name in ("commit", "tree")):
        raise ApplicationDeploymentContractError("application candidate Git identity is invalid")
    for name in ("predecessor_commit", "predecessor_tree"):
        if not isinstance(candidate[name], str) or _GIT.fullmatch(candidate[name]) is None:
            raise ApplicationDeploymentContractError("application predecessor Git identity is invalid")
    for name in set(candidate) - {"commit", "tree", "predecessor_commit", "predecessor_tree"}:
        _sha(candidate[name], f"application candidate {name}")

    raw_deployment = contract["deployment"] if isinstance(contract["deployment"], Mapping) else {}
    archive = _exact(contract["archive"], {
        "artifact_ref", "content_sha256", "size_bytes", "embedded_commit",
        "release_manifest_hash", "content_manifest_sha256", "file_count",
    }, "application archive")
    if (
        archive["artifact_ref"] != raw_deployment.get("artifact_ref")
        or archive["content_sha256"] != candidate["archive_sha256"]
        or archive["embedded_commit"] != candidate["commit"]
        or archive["release_manifest_hash"] != candidate["release_manifest_hash"]
        or not isinstance(archive["size_bytes"], int) or archive["size_bytes"] <= 0
        or not isinstance(archive["file_count"], int) or archive["file_count"] <= 0
    ):
        raise ApplicationDeploymentContractError("application archive is underbound")
    _sha(archive["content_sha256"], "application archive hash")
    _sha(archive["content_manifest_sha256"], "application archive manifest hash")

    plan = _exact(contract["plan"], {
        "commit", "tree", "solution_hash", "closure_hash", "graph_hash", "work_unit",
        "authorization_ref", "projection",
    }, "application Plan")
    if (plan["commit"], plan["solution_hash"], plan["closure_hash"]) != (PLAN_COMMIT, SOLUTION_HASH, CLOSURE_HASH):
        raise ApplicationDeploymentContractError("application Plan does not match approved f0 solution")
    if _GIT.fullmatch(str(plan["tree"])) is None or plan["work_unit"] != "W09":
        raise ApplicationDeploymentContractError("application Plan tree/work unit is invalid")
    _sha(plan["graph_hash"], "application Plan graph hash")
    _identity(plan["authorization_ref"], "Plan authorization reference")
    if plan["authorization_ref"] != instruction["ref"]:
        raise ApplicationDeploymentContractError("Plan authorization and protected operator instruction differ")
    projection = _exact(plan["projection"], {"release_path", "content_sha256"}, "runtime projection")
    _contained_path(projection["release_path"], PROJECTION_PATH, "runtime projection")
    _sha(projection["content_sha256"], "runtime projection hash")

    runtime = _exact(contract["runtime_config"], {
        "artifact_ref", "generation_path", "content_sha256", "overlay_manifest_sha256",
        "config_schema", "executor_principal",
        "operator_principals", "executor_credential_env", "credential_reference",
        "trusted_root", "trusted_uid", "forbidden_paths",
    }, "operational config")
    _contained_path(runtime["generation_path"], CONFIG_PATH, "operational config")
    if runtime["config_schema"] != OPERATIONAL_CONFIG_SCHEMA:
        raise ApplicationDeploymentContractError("operational config schema is invalid")
    if not isinstance(runtime["artifact_ref"], str) or not runtime["artifact_ref"].startswith("config:"):
        raise ApplicationDeploymentContractError("operational config artifact reference is invalid")
    _sha(runtime["content_sha256"], "operational config hash")
    _sha(runtime["overlay_manifest_sha256"], "operational config overlay manifest hash")
    try:
        expected_overlay = runtime_manifest_identity(
            str(raw_deployment.get("next_generation")),
            {runtime["generation_path"]: runtime["content_sha256"].removeprefix("sha256:")},
        )
    except ReleaseError as exc:
        raise ApplicationDeploymentContractError("operational config overlay is invalid") from exc
    if runtime["overlay_manifest_sha256"] != "sha256:" + expected_overlay["manifest_sha256"]:
        raise ApplicationDeploymentContractError("operational config overlay manifest is underbound")
    _identity(runtime["executor_principal"], "executor principal")
    _identity(runtime["credential_reference"], "credential reference")
    if not isinstance(runtime["executor_credential_env"], str) or re.fullmatch(r"[A-Z][A-Z0-9_]*", runtime["executor_credential_env"]) is None:
        raise ApplicationDeploymentContractError("executor credential environment reference is invalid")
    _identities(runtime["operator_principals"], "operator principals")
    if runtime["trusted_root"] != raw_deployment.get("immutable_generation_path") or runtime["trusted_uid"] != 0:
        raise ApplicationDeploymentContractError("operational config trusted release root/uid is invalid")
    if runtime["forbidden_paths"] != sorted(set(runtime["forbidden_paths"])) or any(
        not isinstance(path, str) or not path.startswith("/") for path in runtime["forbidden_paths"]
    ) or not {"/run/tgw/no-local-plan", "/opt/TGW/src"}.issubset(runtime["forbidden_paths"]):
        raise ApplicationDeploymentContractError("operational config does not retire legacy Plan/source paths")

    migrations = contract["migrations"]
    if not isinstance(migrations, list) or [item.get("path") for item in migrations if isinstance(item, Mapping)] != list(MIGRATION_PATHS):
        raise ApplicationDeploymentContractError("application migrations are absent, reordered, or substituted")
    for item in migrations:
        normalized = _exact(item, {"path", "source_sha256", "receipt_hash"}, "application migration")
        _sha(normalized["source_sha256"], "migration source hash")
        _sha(normalized["receipt_hash"], "migration receipt hash")

    deployment = _exact(contract["deployment"], {
        "target_host", "root_id", "release_root", "artifact_ref", "prior_generation",
        "next_generation", "immutable_generation_path", "current_selector", "nix_system_path",
        "predecessor_observation_ref", "predecessor_observation_hash",
        "provider_observation_ref", "provider_observation_hash",
        "prior_projection_sha256", "prior_runtime_config_sha256",
    }, "application deployment")
    expected_generation_path = f"/opt/TGW/releases/{deployment['next_generation']}"
    if (
        deployment["target_host"] != "tgw-prod"
        or deployment["release_root"] != "/opt/TGW"
        or deployment["current_selector"] != "/opt/TGW/current"
        or deployment["immutable_generation_path"] != expected_generation_path
        or deployment["prior_generation"] == deployment["next_generation"]
        or not isinstance(deployment["nix_system_path"], str)
        or _NIX.fullmatch(deployment["nix_system_path"]) is None
    ):
        raise ApplicationDeploymentContractError("application deployment target/generation is invalid")
    for name in ("root_id", "artifact_ref", "prior_generation", "next_generation", "predecessor_observation_ref"):
        _identity(deployment[name], f"deployment {name}")
    _sha(deployment["predecessor_observation_hash"], "predecessor observation hash")
    _identity(deployment["provider_observation_ref"], "provider observation reference")
    _sha(deployment["provider_observation_hash"], "provider observation hash")
    if deployment["prior_projection_sha256"] is not None:
        _sha(deployment["prior_projection_sha256"], "predecessor projection hash")
    _sha(deployment["prior_runtime_config_sha256"], "predecessor config hash")
    services = _identities(contract["services"], "application services")
    probes = _identities(contract["health_probes"], "application health probes")
    if tuple(contract["stage_order"]) != STAGES:
        raise ApplicationDeploymentContractError("application deployment stage order is invalid")

    rollback = _exact(contract["rollback"], {
        "generation", "manifest_hash", "database_backup_required", "selector_cas_required",
        "config_reconciliation_required", "service_reconciliation_required", "predecessor_health_required",
    }, "application rollback")
    if (
        rollback["generation"] != deployment["prior_generation"]
        or rollback["manifest_hash"] != candidate["rollback_manifest_hash"]
        or any(rollback[name] is not True for name in set(rollback) - {"generation", "manifest_hash"})
    ):
        raise ApplicationDeploymentContractError("application rollback is underbound")
    operation_sink = _exact(contract["operation_sink"], {"sink_id", "descriptor_hash"}, "operation sink")
    _identity(operation_sink["sink_id"], "operation sink identity")
    _sha(operation_sink["descriptor_hash"], "operation sink descriptor hash")

    unsigned = dict(contract)
    claimed = unsigned.pop("contract_hash")
    if _sha(claimed, "application contract hash") != _hash(unsigned):
        raise ApplicationDeploymentContractError("application deployment contract hash mismatch")
    return {**contract, "services": services, "health_probes": probes}


class ImmutableGitObjectReader(Protocol):
    """Externally admitted, held-object Git reader; never an ambient binary."""

    def identity(self, revision: str) -> tuple[str, str]: ...
    def show(self, commit: str, path: str) -> bytes: ...


class ProtectedGitObjectReader:
    """Held Git executable and protected immutable object repository."""

    __slots__ = ("repository", "_repo_fd", "_git_fd", "_git_raw", "_git_path", "_git_identity", "_repo_identity", "_frozen")

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_frozen", False):
            raise AttributeError("sealed Git object reader is immutable")
        object.__setattr__(self, name, value)

    def __init__(self, repository: Path, *, git_path: Path, git_sha256: str) -> None:
        self.repository = Path(repository).resolve(strict=True)
        self._git_path = Path(git_path).resolve(strict=True)
        for path in (self.repository, self._git_path):
            absolute = path.absolute()
            for ancestor in (absolute, *absolute.parents):
                metadata = os.lstat(ancestor)
                if ancestor == absolute and path == self._git_path:
                    continue
                if stat.S_ISDIR(metadata.st_mode) and (
                    metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o022
                ):
                    raise ApplicationDeploymentContractError("production Git authority is not protected")
        repo = self.repository.lstat()
        if not stat.S_ISDIR(repo.st_mode) or repo.st_uid != 0 or stat.S_IMODE(repo.st_mode) & 0o022:
            raise ApplicationDeploymentContractError("production Git object repository is mutable")
        self._repo_identity = _inode_identity(repo)
        for current, directories, files in os.walk(self.repository, followlinks=False):
            for name in (*directories, *files):
                item = Path(current) / name
                metadata = item.lstat()
                if (
                    stat.S_ISLNK(metadata.st_mode) or metadata.st_uid != 0
                    or stat.S_IMODE(metadata.st_mode) & 0o022
                    or not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode))
                ):
                    raise ApplicationDeploymentContractError("production Git object closure is mutable")
        self._repo_fd = os.open(
            self.repository, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        if _inode_identity(os.fstat(self._repo_fd)) != self._repo_identity:
            os.close(self._repo_fd)
            raise ApplicationDeploymentContractError("production Git repository changed while opening")
        try:
            self._git_fd, self._git_raw = _held_regular(self._git_path, git_sha256, executable=True)
        except Exception:
            os.close(self._repo_fd)
            raise
        git_metadata = os.fstat(self._git_fd)
        if (
            git_metadata.st_uid != 0 or git_metadata.st_nlink != 1
            or stat.S_IMODE(git_metadata.st_mode) not in {0o555, 0o755}
        ):
            os.close(self._git_fd)
            os.close(self._repo_fd)
            raise ApplicationDeploymentContractError("production Git executable is not protected")
        self._git_identity = _inode_identity(git_metadata)
        self._frozen = True

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise TypeError("ProtectedGitObjectReader is sealed")

    def close(self) -> None:
        if self._git_fd >= 0:
            os.close(self._git_fd)
            object.__setattr__(self, "_git_fd", -1)
        if self._repo_fd >= 0:
            os.close(self._repo_fd)
            object.__setattr__(self, "_repo_fd", -1)

    def _run(self, *arguments: str, limit: int = 4 * 1024 * 1024) -> bytes:
        if self._git_fd < 0:
            raise ApplicationDeploymentContractError("production Git reader is closed")
        argv = [
            f"/proc/{os.getpid()}/fd/{self._git_fd}",
            "-c", f"safe.directory=/proc/{os.getpid()}/fd/{self._repo_fd}",
            "-C", f"/proc/{os.getpid()}/fd/{self._repo_fd}",
            *arguments,
        ]
        rc, stdout, _stderr = _run_held_bounded(
            argv, pass_fds=(self._git_fd, self._repo_fd), timeout=20, limit=limit,
            env={"PATH": "", "LANG": "C", "LC_ALL": "C"},
        )
        if rc:
            raise ApplicationDeploymentContractError("protected Git object read failed")
        if (
            _inode_identity(os.fstat(self._git_fd)) != self._git_identity
            or _inode_identity(os.stat(self._git_path, follow_symlinks=False)) != self._git_identity
            or _hash_bytes(os.pread(self._git_fd, len(self._git_raw) + 1, 0)) != _hash_bytes(self._git_raw)
            or _inode_identity(os.fstat(self._repo_fd)) != self._repo_identity
            or _inode_identity(self.repository.lstat()) != self._repo_identity
        ):
            raise ApplicationDeploymentContractError("production Git authority changed during use")
        return stdout

    def identity(self, revision: str) -> tuple[str, str]:
        if _GIT.fullmatch(str(revision)) is None:
            raise ApplicationDeploymentContractError("production Git revision is not exact")
        commit = self._run("rev-parse", "--verify", f"{revision}^{{commit}}").decode().strip()
        tree = self._run("rev-parse", "--verify", f"{commit}^{{tree}}").decode().strip()
        return commit, tree

    def postcheck(self) -> None:
        if (
            _inode_identity(os.fstat(self._repo_fd)) != self._repo_identity
            or _inode_identity(self.repository.lstat()) != self._repo_identity
            or _inode_identity(os.fstat(self._git_fd)) != self._git_identity
            or _inode_identity(os.stat(self._git_path, follow_symlinks=False)) != self._git_identity
        ):
            raise ApplicationDeploymentContractError("production Git authority changed")

    def show(self, commit: str, path: str) -> bytes:
        if _GIT.fullmatch(str(commit)) is None or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
            raise ApplicationDeploymentContractError("production Git object request is invalid")
        return self._run("show", f"{commit}:{path}", limit=16 * 1024 * 1024)


def _artifact(sink: PinnedGitReceiptSink, pointer: Mapping[str, str]) -> dict[str, Any]:
    raw = sink.fetch_bytes(pointer["ref"])
    if _hash_bytes(raw) != pointer["content_sha256"]:
        raise ApplicationDeploymentContractError("candidate evidence artifact pointer differs from retained bytes")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApplicationDeploymentContractError("candidate evidence artifact is not JSON") from exc
    if not isinstance(value, dict):
        raise ApplicationDeploymentContractError("candidate evidence artifact is not an object")
    return value


def _validate_projection(raw: bytes) -> None:
    try:
        projection = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApplicationDeploymentContractError("runtime projection is invalid JSON") from exc
    try:
        projection = validate_projection(projection, expected_plan_commit=PLAN_COMMIT)
    except ValueError as exc:
        raise ApplicationDeploymentContractError(
            "runtime projection canonical binding is invalid"
        ) from exc
    solution = projection["solution"]
    if (
        solution.get("solution_hash") != SOLUTION_HASH
        or solution.get("closure_hash") != CLOSURE_HASH
    ):
        raise ApplicationDeploymentContractError("runtime projection differs from retained Plan solution")


def _validate_runtime_config(raw: bytes, contract: Mapping[str, Any]) -> None:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApplicationDeploymentContractError("operational config is invalid JSON") from exc
    if not isinstance(value, Mapping):
        raise ApplicationDeploymentContractError("operational config is invalid")
    runtime = contract["runtime_config"]
    deployment = contract["deployment"]
    exact = {
        "schema": runtime["config_schema"],
        "plan_approved_commit": PLAN_COMMIT,
        "plan_approved_solution_hash": SOLUTION_HASH,
        "plan_approved_solution_hash": SOLUTION_HASH,
        "plan_projection_path": deployment["immutable_generation_path"] + "/" + PROJECTION_PATH,
        "plan_projection_root": deployment["immutable_generation_path"],
        "plan_authority_executor_principal": runtime["executor_principal"],
        "plan_authority_executor_credential_env": runtime["executor_credential_env"],
        "plan_authority_executor_credential_ref": runtime["credential_reference"],
        "plan_projection_trusted_uid": runtime["trusted_uid"],
    }
    if any(value.get(name) != expected for name, expected in exact.items()):
        raise ApplicationDeploymentContractError("operational config differs from application contract")
    actual_operators = {
        value.get("plan_authority_operator_api_principal"),
        value.get("plan_authority_operator_session_principal"),
    }
    if None in actual_operators or sorted(actual_operators) != runtime["operator_principals"]:
        raise ApplicationDeploymentContractError("operational config operator principals differ")
    forbidden_key_tokens = {
        "access_token", "refresh_token", "client_secret", "api_key", "password",
        "private_key", "secret_bytes", "credential_value",
    }
    forbidden: set[str] = set()
    configured_paths: set[str] = set()

    def inspect(item: Any, location: str = "$") -> None:
        if isinstance(item, Mapping):
            for name, child in item.items():
                key = str(name).lower()
                if any(token in key for token in forbidden_key_tokens):
                    forbidden.add(f"{location}.{name}")
                inspect(child, f"{location}.{name}")
        elif isinstance(item, list):
            for index, child in enumerate(item):
                inspect(child, f"{location}[{index}]")
        elif isinstance(item, str) and item.startswith("/"):
            configured_paths.add(item)

    inspect(value)
    forbidden_roots = tuple(PurePosixPath(path) for path in runtime["forbidden_paths"])
    path_violation = any(
        path == root or root in path.parents
        for configured in configured_paths
        for path in (PurePosixPath(configured),)
        for root in forbidden_roots
    )
    if forbidden or path_violation:
        raise ApplicationDeploymentContractError("operational config contains secret material instead of a credential reference")


@dataclass(frozen=True)
class ProductionApplicationBinding:
    target_host: str
    root_id: str
    release_root: Path
    services: tuple[str, ...]
    health_probes: tuple[str, ...]
    operation_sink_id: str
    operation_sink_descriptor_hash: str

    def validate(self) -> None:
        if self.target_host != "tgw-prod" or self.root_id != "production-releases" or self.release_root != Path("/opt/TGW"):
            raise ApplicationDeploymentContractError("mounted W09 production target is invalid")
        _identities(list(self.services), "mounted production services")
        _identities(list(self.health_probes), "mounted production health probes")
        _identity(self.operation_sink_id, "mounted operation sink")
        _sha(self.operation_sink_descriptor_hash, "mounted operation sink descriptor hash")


@dataclass(frozen=True)
class VerifiedApplicationDeploymentContract:
    reference: str
    contract_hash: str
    contract: Mapping[str, Any]
    migration_receipts: tuple[Mapping[str, Any], ...]

    @property
    def intended_next_generation(self) -> str:
        return str(self.contract["deployment"]["next_generation"])

    def provider_parameters(self) -> dict[str, Any]:
        value = validate_application_deployment_contract(self.contract)
        if value["contract_hash"] != self.contract_hash:
            raise ApplicationDeploymentContractError("resolved application contract hash mismatch")
        if len(self.migration_receipts) != len(MIGRATION_PATHS) or any(
            receipt.get("migration_path") != binding["path"]
            or receipt.get("receipt_hash") != binding["receipt_hash"]
            or receipt.get("migration_sha256") != binding["source_sha256"]
            for receipt, binding in zip(self.migration_receipts, value["migrations"], strict=True)
        ):
            raise ApplicationDeploymentContractError("resolved migration receipts do not match W09 contract")
        candidate, deployment = value["candidate"], value["deployment"]
        return {
            "generation": deployment["next_generation"],
            "candidate_commit": candidate["commit"], "candidate_tree": candidate["tree"],
            "archive_sha256": candidate["archive_sha256"], "artifact_ref": deployment["artifact_ref"],
            "root_id": deployment["root_id"], "expected_current": deployment["prior_generation"],
            "operation_id": "w09-" + self.contract_hash.removeprefix("sha256:")[:24],
            "review_receipt": candidate["admission_gate_hash"], "controller_receipt": self.contract_hash,
            "migration_receipts": [dict(item) for item in self.migration_receipts],
            "projection": dict(value["plan"]["projection"]), "runtime_config": dict(value["runtime_config"]),
            "services": list(value["services"]), "health_probes": list(value["health_probes"]),
            "nix_system_path": deployment["nix_system_path"],
            "predecessor_observation_ref": deployment["predecessor_observation_ref"],
            "predecessor_observation_hash": deployment["predecessor_observation_hash"],
            "provider_observation_ref": deployment["provider_observation_ref"],
            "provider_observation_hash": deployment["provider_observation_hash"],
            "immutable_generation_path": deployment["immutable_generation_path"],
            "predecessor": {
                "generation": deployment["prior_generation"],
                "selector_target": f"/opt/TGW/releases/{deployment['prior_generation']}",
                "commit": candidate["predecessor_commit"],
                "tree": candidate["predecessor_tree"],
                "archive_sha256": candidate["predecessor_archive_sha256"],
                "release_manifest_hash": candidate["predecessor_release_manifest_hash"],
                "content_manifest_sha256": candidate["predecessor_content_manifest_sha256"],
                "projection_sha256": deployment["prior_projection_sha256"],
                "runtime_config_sha256": deployment["prior_runtime_config_sha256"],
            },
        }


class ApplicationDeploymentContractResolver(Protocol):
    def resolve(self, reference: str, contract_hash: str) -> VerifiedApplicationDeploymentContract: ...


class PinnedApplicationDeploymentContractResolver:
    """Cross-bind Y's W09 contract to disjoint S/D/X/config/host stores."""

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("sealed W09 contract resolver is immutable")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        repository: Path,
        *,
        plan_repository: Path,
        plan_approved_ref: str,
        candidate_evidence_descriptor: PinnedCandidateEvidenceDescriptor,
        execution_evidence_sink: PinnedGitReceiptSink,
        contract_sink: PinnedGitReceiptSink,
        runtime_config_sink: PinnedGitReceiptSink,
        archive_sink: PinnedGitReceiptSink,
        instruction_sink: PinnedGitReceiptSink,
        predecessor_observation_sink: PinnedGitReceiptSink,
        candidate_objects: ImmutableGitObjectReader,
        plan_objects: ImmutableGitObjectReader,
        production: ProductionApplicationBinding,
        now: Callable[[], datetime] | None = None,
        _production_token: object | None = None,
    ) -> None:
        if not isinstance(candidate_evidence_descriptor, PinnedCandidateEvidenceDescriptor):
            raise ApplicationDeploymentContractError("candidate evidence descriptor is not externally pinned")
        sinks = (
            execution_evidence_sink, contract_sink, runtime_config_sink, archive_sink,
            instruction_sink, predecessor_observation_sink,
        )
        if any(not isinstance(item, PinnedGitReceiptSink) for item in sinks):
            raise ApplicationDeploymentContractError("application deployment stores are not externally pinned")
        try:
            repository = repository.resolve(strict=True)
            candidate_sink = PinnedGitReceiptSink(
                candidate_evidence_descriptor.candidate_evidence_sink_descriptor,
                candidate_repository=repository,
            )
        except (OSError, CandidateReceiptSinkError) as exc:
            raise ApplicationDeploymentContractError("candidate evidence store is unavailable") from exc
        roots = (repository, candidate_sink.repository, candidate_evidence_descriptor.authority_repository, *(item.repository for item in sinks))
        if any(left == right or left in right.parents or right in left.parents for index, left in enumerate(roots) for right in roots[index + 1:]):
            raise ApplicationDeploymentContractError("candidate and W09 evidence/config/operation roots must be disjoint")
        production.validate()
        if not all(callable(getattr(reader, name, None)) for reader in (candidate_objects, plan_objects) for name in ("identity", "show")):
            raise ApplicationDeploymentContractError("admitted immutable Git object reader is unavailable")
        if _production_token is not None and (
            _production_token is not _PRODUCTION_RESOLVER_SEAL
            or now is not None
            or type(candidate_objects) is not ProtectedGitObjectReader
            or type(plan_objects) is not ProtectedGitObjectReader
            or any(type(item) is not PinnedGitReceiptSink for item in sinks)
        ):
            raise ApplicationDeploymentContractError("production W09 resolver authority is not exact")
        if _production_token is _PRODUCTION_RESOLVER_SEAL:
            protected_roots = (repository, Path(plan_repository), *(item.repository for item in sinks))
            if (
                candidate_objects.repository != repository
                or plan_objects.repository != Path(plan_repository).resolve(strict=True)
                or any(
                    not stat.S_ISDIR(root.lstat().st_mode)
                    or root.lstat().st_uid != 0
                    or stat.S_IMODE(root.lstat().st_mode) & 0o022
                    for root in protected_roots
                )
            ):
                raise ApplicationDeploymentContractError("production W09 repositories/sinks are not protected")
        self._repository = repository
        self._plan_repository = Path(plan_repository)
        self._plan_approved_ref = plan_approved_ref
        self._descriptor = candidate_evidence_descriptor
        self._candidate_sink = candidate_sink
        (
            self._execution_sink, self._contract_sink, self._config_sink, self._archive_sink,
            self._instruction_sink, self._predecessor_sink,
        ) = sinks
        self._candidate_objects, self._plan_objects = candidate_objects, plan_objects
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._production = production
        self._production_authority = _production_token is _PRODUCTION_RESOLVER_SEAL
        self._sealed = True

    @property
    def production_authority(self) -> bool:
        return self._production_authority

    @classmethod
    def production(cls, **bindings: Any) -> "PinnedApplicationDeploymentContractResolver":
        """Construct production authority with internal UTC and exact held readers."""
        if "now" in bindings or "_production_token" in bindings:
            raise ApplicationDeploymentContractError("production resolver clock/token is internal")
        return cls(**bindings, now=None, _production_token=_PRODUCTION_RESOLVER_SEAL)

    def resolve(self, reference: str, contract_hash: str) -> VerifiedApplicationDeploymentContract:
        if self._production_authority:
            self._candidate_objects.postcheck()
            self._plan_objects.postcheck()
        match = re.fullmatch(r"candidate:([0-9a-f]{40}):application-bootstrap:v1", str(reference))
        if match is None or _SHA.fullmatch(str(contract_hash)) is None:
            raise ApplicationDeploymentContractError("application deployment contract reference is invalid")
        commit, tree = self._candidate_objects.identity(match.group(1))
        if commit != match.group(1) or _GIT.fullmatch(tree) is None:
            raise ApplicationDeploymentContractError("candidate object reader returned a neighboring identity")
        try:
            contract = validate_application_deployment_contract(self._contract_sink.fetch_object(str(reference)))
            authority = resolve_approved_plan_authority(
                self._plan_repository, approved_ref=self._plan_approved_ref,
                candidate_repository=self._repository,
            )
            evidence = verify_candidate_evidence_bundle(
                self._candidate_sink, candidate_evidence_descriptor=self._descriptor,
                repository=self._repository, source_commit=commit, source_tree=tree,
                plan_commit=authority["approved_commit"], plan_repository=Path(authority["repository"]),
            )
            admission = candidate_admission_gate(
                self._repository, candidate=commit, plan_repository=self._plan_repository,
                plan_approved_ref=self._plan_approved_ref, candidate_evidence_descriptor=self._descriptor,
                execution_sink=self._execution_sink,
            )
            bundle = validate_candidate_evidence_bundle(
                self._candidate_sink.fetch_object(candidate_evidence_bundle_ref(commit))
            )
            migrations = tuple(_artifact(self._candidate_sink, pointer) for pointer in bundle["migration_receipts"])
            release_manifest = _artifact(self._candidate_sink, bundle["release_manifest"])
            rollback_manifest = _artifact(self._candidate_sink, bundle["rollback_manifest"])
            config_bytes = self._config_sink.fetch_bytes(contract["runtime_config"]["artifact_ref"])
            archive_bytes = self._archive_sink.fetch_bytes(contract["archive"]["artifact_ref"])
            instruction_bytes = self._instruction_sink.fetch_bytes(contract["authorization"]["operator_instruction"]["ref"])
            predecessor = self._predecessor_sink.fetch_object(contract["deployment"]["predecessor_observation_ref"])
        except CandidateReceiptSinkError as exc:
            raise ApplicationDeploymentContractError("exact W08/W09 evidence is unavailable") from exc
        candidate, deployment = contract["candidate"], contract["deployment"]
        try:
            with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as archive_file:
                embedded_commit = archive_file.pax_headers.get("comment")
        except tarfile.TarError as exc:
            raise ApplicationDeploymentContractError("candidate archive is not a readable release archive") from exc
        plan_commit, plan_tree = self._plan_objects.identity(PLAN_COMMIT)
        projection_bytes = self._candidate_objects.show(commit, PROJECTION_PATH)
        _validate_projection(projection_bytes)
        _validate_runtime_config(config_bytes, contract)
        predecessor_unsigned = dict(predecessor)
        predecessor_receipt_hash = predecessor_unsigned.pop("receipt_hash", None)
        expected_predecessor = {
            "schema": "tgw-application-predecessor-observation/v1",
            "observed_at": predecessor_unsigned.get("observed_at"),
            "expires_at": predecessor_unsigned.get("expires_at"),
            "target_host": "tgw-prod",
            "root_id": self._production.root_id,
            "selector": "/opt/TGW/current",
            "selector_target": f"/opt/TGW/releases/{deployment['prior_generation']}",
            "generation": deployment["prior_generation"],
            "nix_system_path": deployment["nix_system_path"],
            "commit": candidate.get("predecessor_commit"),
            "tree": candidate.get("predecessor_tree"),
            "archive_sha256": candidate.get("predecessor_archive_sha256"),
            "release_manifest_hash": candidate.get("predecessor_release_manifest_hash"),
            "content_manifest_sha256": candidate.get("predecessor_content_manifest_sha256"),
            "projection_sha256": deployment["prior_projection_sha256"],
            "runtime_config_sha256": deployment["prior_runtime_config_sha256"],
            "services": list(self._production.services),
            "health_probes": list(self._production.health_probes),
        }
        try:
            observed_at = datetime.fromisoformat(str(predecessor_unsigned["observed_at"]).replace("Z", "+00:00"))
            expires_at = datetime.fromisoformat(str(predecessor_unsigned["expires_at"]).replace("Z", "+00:00"))
        except (KeyError, ValueError) as exc:
            raise ApplicationDeploymentContractError("predecessor observation freshness is invalid") from exc
        current = self._now()
        if current.tzinfo is None or not observed_at <= current <= expires_at:
            raise ApplicationDeploymentContractError("predecessor observation is stale or future-dated")
        authorization = contract["authorization"]
        authorized_at = datetime.fromisoformat(str(authorization["observed_at"]).replace("Z", "+00:00"))
        authorization_expiry = datetime.fromisoformat(str(authorization["expires_at"]).replace("Z", "+00:00"))
        if not authorized_at <= current <= authorization_expiry:
            raise ApplicationDeploymentContractError("W09 operator authorization is not current at dispatch")
        if (
            contract["contract_hash"] != contract_hash
            or authority["approved_commit"] != PLAN_COMMIT or plan_commit != PLAN_COMMIT
            or contract["plan"]["tree"] != plan_tree
            or contract["plan"]["graph_hash"] != evidence["plan_graph_hash"]
            or admission.get("allowed") is not True
            or candidate["commit"] != commit or candidate["tree"] != tree
            or candidate["archive_sha256"] != _hash_bytes(archive_bytes)
            or contract["archive"]["size_bytes"] != len(archive_bytes)
            or contract["archive"]["embedded_commit"] != embedded_commit
            or contract["archive"]["content_manifest_sha256"] != "sha256:" + release_manifest["content_manifest_sha256"]
            or contract["archive"]["file_count"] != release_manifest["file_count"]
            or candidate["candidate_evidence_bundle_hash"] != evidence["bundle_hash"]
            or candidate["manifest_hash"] != evidence["candidate_manifest_hash"]
            or candidate["release_manifest_hash"] != evidence["release_manifest_hash"]
            or candidate["rollback_manifest_hash"] != evidence["rollback_manifest_hash"]
            or candidate["predecessor_commit"] != rollback_manifest["rollback_release_manifest"]["commit"]
            or candidate["predecessor_tree"] != rollback_manifest["rollback_release_manifest"]["git_tree"]
            or candidate["predecessor_archive_sha256"] != "sha256:" + rollback_manifest["rollback_release_manifest"]["archive_sha256"]
            or candidate["predecessor_release_manifest_hash"] != _hash(rollback_manifest["rollback_release_manifest"])
            or candidate["predecessor_content_manifest_sha256"] != "sha256:" + rollback_manifest["rollback_release_manifest"]["content_manifest_sha256"]
            or candidate["admission_gate_hash"] != admission["gate_hash"]
            or deployment["next_generation"] != evidence["release_generation"]
            or deployment["prior_generation"] != evidence["rollback_generation"]
            or contract["plan"]["projection"]["content_sha256"] != _hash_bytes(projection_bytes)
            or contract["runtime_config"]["content_sha256"] != _hash_bytes(config_bytes)
            or contract["authorization"]["operator_instruction"]["content_sha256"] != _hash_bytes(instruction_bytes)
            or predecessor_unsigned != expected_predecessor
            or predecessor_receipt_hash != _hash(predecessor_unsigned)
            or deployment["predecessor_observation_hash"] != predecessor_receipt_hash
            or (deployment["target_host"], deployment["root_id"], Path(deployment["release_root"]))
               != (self._production.target_host, self._production.root_id, self._production.release_root)
            or tuple(contract["services"]) != self._production.services
            or tuple(contract["health_probes"]) != self._production.health_probes
            or contract["operation_sink"] != {
                "sink_id": self._production.operation_sink_id,
                "descriptor_hash": self._production.operation_sink_descriptor_hash,
            }
        ):
            raise ApplicationDeploymentContractError("application contract differs from exact retained or mounted evidence")
        if self._production_authority:
            self._candidate_objects.postcheck()
            self._plan_objects.postcheck()
        return VerifiedApplicationDeploymentContract(str(reference), str(contract_hash), contract, migrations)
