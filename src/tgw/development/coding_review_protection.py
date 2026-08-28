"""Root-owned preparation for the existing governed local-review request.

The ordinary coding lifecycle can name only an already recorded candidate.
All paths, provider material, evidence bindings, snapshot bytes, and the final
request are reconstructed below from fixed protected configuration.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from tgw.candidate_receipt_sink import PinnedCandidateEvidenceDescriptor
from tgw.candidate_review import build_executable_review_packet
from tgw.execution_resources import (
    card_resource_receipt,
    resource_service_catalog_hash,
    resource_service_descriptor_hash,
    validate_resource_service_catalog,
    validate_resource_service_descriptor,
)
from tgw.governed_review_adapter import (
    _validate_evidence_sink_descriptor,
    snapshot_hash,
)
from tgw.review_contract import ReviewRunnerError

PROTECTED_REVIEW_ROOT = Path("/var/lib/tgw/coding-protected-review")
PROTECTED_REVIEW_CONFIG = PROTECTED_REVIEW_ROOT / "config.json"
PROTECTED_REVIEW_PROFILE = PROTECTED_REVIEW_ROOT / "request-profile.json"
PROTECTED_REVIEW_REQUEST_ROOT = PROTECTED_REVIEW_ROOT / "requests"
PROTECTED_REVIEW_SNAPSHOT_ROOT = PROTECTED_REVIEW_ROOT / "snapshots"
PROTECTED_CANDIDATE_DESCRIPTOR = (
    PROTECTED_REVIEW_ROOT / "candidate-evidence-descriptor.json"
)
PROTECTED_EXECUTION_SINK = PROTECTED_REVIEW_ROOT / "execution-evidence-sink.json"
PROTECTED_EXECUTION_PIN_SOURCE = (
    PROTECTED_REVIEW_ROOT / "execution-evidence-published.json"
)

PROFILE_SCHEMA = "tgw-local-coding-protected-review-profile/v1"
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _git(repository: Path, *arguments: str, binary: bool = False) -> bytes | str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={repository.resolve()}", *arguments],
        cwd=repository,
        check=False,
        capture_output=True,
        text=not binary,
    )
    if result.returncode:
        detail = result.stderr if isinstance(result.stderr, str) else result.stderr.decode()
        raise ReviewRunnerError(detail[-500:] or "protected review Git probe failed")
    return result.stdout


def _extract_snapshot(archive: bytes, destination: Path) -> None:
    destination.mkdir(mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as stream:
        for member in stream.getmembers():
            relative = Path(member.name)
            if (
                relative.is_absolute()
                or not relative.parts
                or any(part in ("", ".", "..") for part in relative.parts)
                or not (member.isdir() or member.isfile())
            ):
                raise ReviewRunnerError("candidate archive contains an unsafe entry")
            target = destination / relative
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
                continue
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            source = stream.extractfile(member)
            if source is None:
                raise ReviewRunnerError("candidate archive entry is unreadable")
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o400,
            )
            try:
                with os.fdopen(descriptor, "wb", closefd=False) as output:
                    output.write(source.read())
                    output.flush()
                    os.fsync(descriptor)
                os.fchmod(descriptor, 0o444)
            finally:
                os.close(descriptor)
    directories = [destination, *(item for item in destination.rglob("*") if item.is_dir())]
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        directory.chmod(0o555)


def _root_owned_snapshot(path: Path) -> None:
    entries = [path, *path.rglob("*")]
    for entry in entries:
        observed = entry.stat(follow_symlinks=False)
        if (
            entry.is_symlink()
            or observed.st_uid != 0
            or observed.st_gid != 0
            or observed.st_mode & 0o022
            or not (stat.S_ISDIR(observed.st_mode) or stat.S_ISREG(observed.st_mode))
        ):
            raise ReviewRunnerError(
                "protected governed-review snapshot is writable or substituted"
            )


def _discard_root_owned_snapshot(path: Path) -> None:
    _root_owned_snapshot(path)
    directories = [path, *(entry for entry in path.rglob("*") if entry.is_dir())]
    for directory in directories:
        directory.chmod(0o700)
    shutil.rmtree(path)


def _atomic_root_json(path: Path, value: Mapping[str, Any], *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        payload = _canonical(value) + b"\n"
        os.write(descriptor, payload)
        os.fsync(descriptor)
        os.fchown(descriptor, 0, 0)
        os.fchmod(descriptor, mode)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary_path, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _protected_json(path: Path, label: str, trusted_uid: int) -> dict[str, Any]:
    from tgw import context_generation_status

    try:
        context_generation_status._protected_directory(
            path.parent, f"{label} parent", trusted_uid
        )
        return context_generation_status._protected_json(
            path, label, trusted_uid
        )
    except context_generation_status.ContextGenerationStatusError as exc:
        raise ReviewRunnerError(f"{label} is unavailable") from exc


def validate_profile(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate every protected profile field used to construct a request."""

    required = {
        "schema",
        "provider_identity",
        "environment",
        "evidence_sink",
        "resource_service",
        "resource_service_catalog",
        "receiver_profile",
        "environment_preflight_receipt",
        "skill_contract_hash",
        "timeout_seconds",
        "output_limit",
    }
    if set(value) != required or value.get("schema") != PROFILE_SCHEMA:
        raise ReviewRunnerError("coding protected-review profile is invalid")
    provider_identity = value.get("provider_identity")
    environment = value.get("environment")
    evidence_sink = value.get("evidence_sink")
    resource_service = value.get("resource_service")
    resource_catalog = value.get("resource_service_catalog")
    receiver_profile = value.get("receiver_profile")
    preflight = value.get("environment_preflight_receipt")
    if (
        not isinstance(provider_identity, Mapping)
        or not isinstance(environment, Mapping)
        or not all(
            isinstance(name, str) and isinstance(item, str)
            for name, item in environment.items()
        )
        or not isinstance(evidence_sink, Mapping)
        or not isinstance(resource_service, Mapping)
        or not isinstance(resource_catalog, Mapping)
        or not isinstance(receiver_profile, Mapping)
        or not receiver_profile
        or not isinstance(preflight, Mapping)
        or not isinstance(value.get("skill_contract_hash"), str)
        or _SHA256.fullmatch(value["skill_contract_hash"]) is None
        or not isinstance(value.get("timeout_seconds"), int)
        or isinstance(value.get("timeout_seconds"), bool)
        or not 1 <= value["timeout_seconds"] <= 900
        or not isinstance(value.get("output_limit"), int)
        or isinstance(value.get("output_limit"), bool)
        or not 1024 <= value["output_limit"] <= 64 * 1024 * 1024
    ):
        raise ReviewRunnerError("coding protected-review profile bindings are invalid")
    try:
        provider = provider_identity["provider"]
        provider_argv = provider_identity["argv_template"]
        execution_environment = provider_identity["artifacts"][
            "execution_environment"
        ]
        command_policy = provider_identity["command_policy"]
        service_identity = provider_identity["context_bundle_service"]
        sandbox_identity = provider_identity["sandbox_identity"]
        execution_path = Path(execution_environment["resolved_path"])
        hashes = (
            execution_environment["content_sha256"],
            service_identity["resource_service_descriptor_hash"],
            service_identity["resource_service_catalog_hash"],
            evidence_sink["descriptor_hash"],
        )
        strings = (
            provider,
            evidence_sink["sink_ref"],
            resource_service["id"],
            resource_service["client_id"],
            service_identity["client_id"],
            service_identity["resource_service_catalog_ref"],
        )
        if (
            not all(isinstance(item, str) and item for item in strings)
            or not all(isinstance(item, str) and _SHA256.fullmatch(item) for item in hashes)
            or not isinstance(provider_argv, list)
            or not all(isinstance(item, str) for item in provider_argv)
            or provider_argv.count("{prompt}") != 1
            or provider_argv.count("{snapshot}") != 1
            or provider_argv.count("{mcp_config}") != 1
            or not execution_path.is_absolute()
            or not isinstance(command_policy, Mapping)
            or not isinstance(service_identity, Mapping)
            or not isinstance(sandbox_identity, Mapping)
            or not isinstance(sandbox_identity.get("uid"), int)
            or isinstance(sandbox_identity.get("uid"), bool)
            or not isinstance(sandbox_identity.get("gid"), int)
            or isinstance(sandbox_identity.get("gid"), bool)
        ):
            raise ReviewRunnerError(
                "coding protected-review provider profile is invalid"
            )
        normalized_service = validate_resource_service_descriptor(resource_service)
        normalized_catalog = validate_resource_service_catalog(resource_catalog)
        _validate_evidence_sink_descriptor(evidence_sink)
        if (
            normalized_service["id"] != service_identity["service_id"]
            or normalized_service["client_id"] != service_identity["client_id"]
            or resource_service_descriptor_hash(normalized_service)
            != service_identity["resource_service_descriptor_hash"]
            or normalized_catalog["catalog_ref"]
            != service_identity["resource_service_catalog_ref"]
            or resource_service_catalog_hash(normalized_catalog)
            != service_identity["resource_service_catalog_hash"]
        ):
            raise ReviewRunnerError(
                "coding protected-review resource service profile is invalid"
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise ReviewRunnerError(
            "coding protected-review provider profile is invalid"
        ) from exc
    return dict(value)


def load_profile(path: Path, *, trusted_uid: int = 0) -> dict[str, Any]:
    value = _protected_json(path, "coding protected-review profile", trusted_uid)
    return validate_profile(value)


def _binding(ref: str, value: Any) -> dict[str, str]:
    return {"ref": ref, "hash": _hash(value)}


def prepare_governed_request(
    *,
    repository: Path,
    candidate_commit: str,
    candidate_tree: str,
    plan_commit: str,
    solution_hash: str,
    closure_hash: str,
    profile_path: Path,
    candidate_descriptor_path: Path,
    request_root: Path,
    snapshot_root: Path,
    trusted_uid: int = 0,
) -> dict[str, Any]:
    """Prepare one exact root-owned governed request and immutable Git snapshot."""

    if os.geteuid() != 0 or trusted_uid != 0:
        raise ReviewRunnerError("protected governed-review preparation requires root")
    if (
        any(
            _COMMIT.fullmatch(value) is None
            for value in (candidate_commit, candidate_tree, plan_commit)
        )
        or any(
            _SHA256.fullmatch(value) is None
            for value in (solution_hash, closure_hash)
        )
    ):
        raise ReviewRunnerError("protected governed-review identity is invalid")
    protected_identity(request_root, uid=0, mode=0o755)
    protected_identity(snapshot_root, uid=0, mode=0o755)
    observed_commit = str(_git(repository, "rev-parse", candidate_commit)).strip()
    observed_tree = str(_git(repository, "rev-parse", f"{candidate_commit}^{{tree}}")).strip()
    if (observed_commit, observed_tree) != (candidate_commit, candidate_tree):
        raise ReviewRunnerError("protected governed-review candidate Git binding differs")
    profile = load_profile(profile_path, trusted_uid=trusted_uid)
    descriptor_value = _protected_json(
        candidate_descriptor_path,
        "coding protected-review candidate evidence descriptor",
        trusted_uid,
    )
    descriptor = PinnedCandidateEvidenceDescriptor(
        descriptor_value, candidate_repository=repository
    )

    archive = _git(repository, "archive", candidate_commit, binary=True)
    assert isinstance(archive, bytes)
    archive_hash = "sha256:" + hashlib.sha256(archive).hexdigest()
    snapshot = snapshot_root / candidate_commit
    staging = snapshot_root / f".{candidate_commit}.{secrets.token_hex(8)}"
    try:
        _extract_snapshot(archive, staging)
        expected_snapshot = snapshot_hash(staging)
        if snapshot.exists():
            if snapshot.is_symlink() or not snapshot.is_dir():
                raise ReviewRunnerError(
                    "protected governed-review snapshot destination is unsafe"
                )
            _root_owned_snapshot(snapshot)
            if snapshot_hash(snapshot) == expected_snapshot:
                _discard_root_owned_snapshot(staging)
            else:
                displaced = snapshot_root / (
                    f".{candidate_commit}.replaced.{secrets.token_hex(8)}"
                )
                os.replace(snapshot, displaced)
                try:
                    os.replace(staging, snapshot)
                except Exception:
                    os.replace(displaced, snapshot)
                    raise
                _discard_root_owned_snapshot(displaced)
        else:
            os.replace(staging, snapshot)
    finally:
        if staging.exists():
            _discard_root_owned_snapshot(staging)
    _root_owned_snapshot(snapshot)
    existing = snapshot_hash(snapshot)
    if existing != expected_snapshot:
        raise ReviewRunnerError("protected governed-review snapshot differs from Git")

    evidence_sink = dict(profile["evidence_sink"])
    service = dict(profile["resource_service"])
    catalog = dict(profile["resource_service_catalog"])
    if catalog.get("plan_commit") != plan_commit:
        raise ReviewRunnerError(
            "protected governed-review resource catalog Plan binding differs"
        )
    environment = dict(profile["environment"])
    provider_identity = json.loads(json.dumps(profile["provider_identity"]))
    provider = str(provider_identity.get("provider", ""))
    provider_argv = list(provider_identity.get("argv_template", []))
    bindings = {
        "plan_input": _binding(
            f"mcp:tgw-context/approved-plan-source/{plan_commit}",
            {"plan_commit": plan_commit},
        ),
        "plan_commit": _binding(
            f"mcp:tgw-context/approved-plan/{plan_commit}",
            {"commit": plan_commit},
        ),
        "plan_graph": {"ref": "mcp:tgw-context/plan-graph", "hash": solution_hash},
        "codegraph_snapshot": _binding(
            f"mcp:tgw-context/codegraph/{candidate_commit}",
            {"commit": candidate_commit, "tree": candidate_tree},
        ),
        "source_tree": {"ref": f"git:tree:{candidate_tree}", "hash": existing},
        "execution_environment": {
            "ref": Path(
                provider_identity["artifacts"]["execution_environment"]["resolved_path"]
            ).as_uri(),
            "hash": provider_identity["artifacts"]["execution_environment"][
                "content_sha256"
            ],
        },
        "authority_conditions": {
            "ref": "tgw-plan:closure",
            "hash": closure_hash,
        },
        "candidate_evidence": descriptor.card_binding(),
        "receipt_sink": {
            "ref": evidence_sink["sink_ref"],
            "hash": evidence_sink["descriptor_hash"],
        },
    }
    provider_identity["command_policy"]["context_bindings"] = {
        name: bindings[name]
        for name in (
            "plan_input",
            "plan_commit",
            "plan_graph",
            "codegraph_snapshot",
            "source_tree",
            "execution_environment",
        )
    }
    service_binding = {
        "id": service["id"],
        "client_id": service["client_id"],
        "descriptor_hash": provider_identity["context_bundle_service"][
            "resource_service_descriptor_hash"
        ],
        "catalog_ref": provider_identity["context_bundle_service"][
            "resource_service_catalog_ref"
        ],
        "catalog_hash": provider_identity["context_bundle_service"][
            "resource_service_catalog_hash"
        ],
    }
    now = datetime.now(timezone.utc)
    # The broker permits a maximum 900-second window including the one-second
    # not-before skew used below.
    expires = now + timedelta(minutes=14, seconds=59)
    from promptcraft.handoff import ExecutionCard, craft_handoff

    card = ExecutionCard.create(
        {
            "card_id": f"candidate-review-{candidate_commit[:12]}",
            "solution_id": solution_hash,
            "role": "independent-review",
            "selected_provider": provider,
            "plan_commit": plan_commit,
            "bindings": bindings,
            "authority": ["read-only semantic and security review of the bound snapshot"],
            "exclusions": [
                "source mutation",
                "deployment",
                "installation",
                "authority broadening",
            ],
            "acceptance": [
                "validated semantic and security verdict bound to the candidate"
            ],
            "receiver_profile": dict(profile["receiver_profile"]),
            "lease": {
                "id": f"review:{candidate_commit}:{secrets.token_hex(6)}",
                "expires_at": expires.isoformat(),
                "stop_policy": "hold",
            },
            "resource_service": service_binding,
        }
    )
    resource_receipt = card_resource_receipt(card.value)
    handoff = craft_handoff(
        {
            "card": card.value,
            "resource_receipt": resource_receipt,
            "resource_service": service,
        },
        receiver_identity=f"{provider}:tgw-review",
    )
    manifest = {
        "schema": "tgw-integrated-candidate-manifest/v1",
        "source": {
            "commit": candidate_commit,
            "tree": candidate_tree,
            "archive_sha256": archive_hash,
        },
        "plan": {
            "commit": plan_commit,
            "solution_hash": solution_hash,
            "closure_hash": closure_hash,
        },
        "candidate_closed": True,
        "installed": False,
    }
    packet = build_executable_review_packet(
        manifest,
        snapshot_ref=snapshot.resolve().as_uri(),
        snapshot_hash=existing,
        selected_provider=provider,
        receiver_profile=profile["receiver_profile"],
        runner_argv=provider_argv,
    )
    challenge = secrets.token_hex(32)
    service_identity = provider_identity["context_bundle_service"]
    sandbox_identity = provider_identity["sandbox_identity"]
    issued = now - timedelta(seconds=1)
    grant_request = {
        "schema": "tgw-context-review-broker-request/v2",
        "client_id": service_identity["client_id"],
        "challenge": challenge,
        "skill_contract_hash": profile["skill_contract_hash"],
        "card_hash": card.hash,
        "role": "independent-review",
        "execution_identity": (
            f"governed-review:{challenge}:uid={sandbox_identity['uid']}:"
            f"gid={sandbox_identity['gid']}"
        ),
        "handoff_hash": handoff["handoff_hash"],
        "resource_receipt_hash": resource_receipt["receipt_hash"],
        "resource_service_catalog_ref": service_identity[
            "resource_service_catalog_ref"
        ],
        "resource_service_catalog_hash": service_identity[
            "resource_service_catalog_hash"
        ],
        "resources": {name: bindings[name] for name in sorted(bindings)},
        "issued_at": issued.isoformat(),
        "not_before": issued.isoformat(),
        "expires_at": expires.isoformat(),
    }
    context_grant = {
        "schema": "tgw-governed-review-context-grant/v1",
        "request": grant_request,
        "request_hash": _hash(grant_request),
    }
    request = {
        "schema": "tgw-governed-review-request/v1",
        "handoff": handoff,
        "snapshot": str(snapshot),
        "source_commit": candidate_commit,
        "source_tree": candidate_tree,
        "plan_commit": plan_commit,
        "provider": provider,
        "provider_identity": provider_identity,
        "provider_argv": provider_argv,
        "environment": environment,
        "trusted_uid": 0,
        "trusted_gid": 0,
        "timeout_seconds": profile["timeout_seconds"],
        "output_limit": profile["output_limit"],
        "evidence_sink": evidence_sink,
        "review_packet": packet,
        "resource_service_catalog": catalog,
        "context_grant": context_grant,
        "environment_preflight_receipt": dict(
            profile["environment_preflight_receipt"]
        ),
    }
    request_path = request_root / f"{candidate_commit}.request.json"
    _atomic_root_json(request_path, request, mode=0o444)
    return {
        "request_path": str(request_path),
        "request_sha256": "sha256:" + hashlib.sha256(_canonical(request)).hexdigest(),
        "snapshot": str(snapshot),
        "snapshot_hash": existing,
        "expires_at": expires.isoformat(),
        "card_hash": card.hash,
        "packet_hash": packet["packet_hash"],
    }


def protected_identity(path: Path, *, uid: int = 0, mode: int | None = None) -> None:
    """Require one direct root-owned, non-writable protected surface."""

    observed = path.stat(follow_symlinks=False)
    if (
        path.is_symlink()
        or observed.st_uid != uid
        or observed.st_gid != 0
        or observed.st_mode & 0o022
        or (mode is not None and stat.S_IMODE(observed.st_mode) != mode)
    ):
        raise ReviewRunnerError("protected governed-review surface is writable or substituted")
