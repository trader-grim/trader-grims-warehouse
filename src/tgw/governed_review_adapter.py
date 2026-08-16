"""Root-captured, provider-neutral adapter for the established ``tgw-review`` path.

This is not a model reviewer.  It launches the selected qualified harness with
the canonical provider-neutral review skill/MCP context and retains an
admission-verifiable execution record.  Any qualified harness can use the same
contract. QES is a separate optional execution path.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import selectors
import signal
import stat
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from tgw.execution_resources import (
    CARD_RESOURCE_NAMES,
    ResourceVerificationError,
    content_hash,
    resource_service_descriptor_hash,
    validate_harness_retrieval_attestation,
)
from tgw.review_runner import ReviewRunnerError, _validate_report

EXECUTION_SCHEMA = "tgw-governed-review-execution/v1"
IDENTITY_SCHEMA = "tgw-governed-review-provider-identity/v1"
_SHA256_PREFIX = "sha256:"
_SANDBOX_FLAGS = (
    "--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup",
    "--die-with-parent", "--new-session", "--tmpfs", "/", "--proc", "/proc",
    "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/home",
)
_CONTEXT_BINDING_NAMES = (
    "plan_input", "plan_commit", "plan_graph", "codegraph_snapshot",
    "source_tree", "execution_environment",
)
_CONTEXT_BUNDLE_TOOL = "tgw_context_bundle"
_MAX_CONTEXT_BUNDLE_BYTES = 64 * 1024 * 1024


def _review_execution_identity(challenge: str, uid: int, gid: int) -> str:
    return f"governed-review:{challenge}:uid={uid}:gid={gid}"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return _SHA256_PREFIX + hashlib.sha256(_canonical(value)).hexdigest()


def _validate_skill_provenance(
    provenance: Any, projection_manifest_hash: Any,
) -> None:
    if not isinstance(provenance, Mapping) or set(provenance) != {
        "schema", "source_ref", "source_manifest_hash",
        "projection_manifest_hash", "projection_receipt_hash",
    }:
        raise ReviewRunnerError("governed review skill source provenance is invalid")
    unsigned = dict(provenance)
    receipt_hash = unsigned.pop("projection_receipt_hash")
    if (
        provenance.get("schema") != "tgw-review-skill-projection-receipt/v1"
        or not isinstance(provenance.get("source_ref"), str)
        or not provenance["source_ref"]
        or not isinstance(provenance.get("source_manifest_hash"), str)
        or not provenance["source_manifest_hash"].startswith(_SHA256_PREFIX)
        or provenance.get("projection_manifest_hash") != projection_manifest_hash
        or receipt_hash != _hash(unsigned)
    ):
        raise ReviewRunnerError("governed review skill source provenance is invalid")


def _verify_ed25519_receipt(
    receipt: Mapping[str, Any], *, public_key: str, label: str,
) -> None:
    unsigned = dict(receipt)
    signature_text = unsigned.pop("signature", None)
    claimed = unsigned.pop("receipt_hash", None)
    if claimed != _hash(unsigned):
        raise ReviewRunnerError(f"{label} receipt hash is invalid")
    try:
        signature = base64.b64decode(str(signature_text), validate=True)
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True))
        key.verify(signature, _canonical({**unsigned, "receipt_hash": claimed}))
    except (ValueError, InvalidSignature) as exc:
        raise ReviewRunnerError(f"{label} receipt signature is invalid") from exc


def _validate_report_evidence(report: Any, expected_snapshot: str) -> dict[str, Any]:
    if not isinstance(report, Mapping) or set(report) != {
        "schema", "verdict", "snapshot_hash", "summary", "findings",
    }:
        raise ReviewRunnerError("governed review report fields are invalid")
    if (
        report.get("schema") != "tgw-code-review/v1"
        or report.get("verdict") not in {"PASS", "FAIL"}
        or report.get("snapshot_hash") != expected_snapshot
        or not isinstance(report.get("summary"), str)
        or not report["summary"].strip()
        or not isinstance(report.get("findings"), list)
    ):
        raise ReviewRunnerError("governed review report contract is invalid")
    for finding in report["findings"]:
        if (
            not isinstance(finding, Mapping)
            or set(finding) != {"severity", "path", "line", "message"}
            or finding.get("severity") not in {"critical", "high", "medium", "low"}
            or not isinstance(finding.get("path"), str)
            or not finding["path"]
            or Path(finding["path"]).is_absolute()
            or ".." in Path(finding["path"]).parts
            or not isinstance(finding.get("line"), int)
            or finding["line"] < 1
            or not isinstance(finding.get("message"), str)
            or not finding["message"].strip()
        ):
            raise ReviewRunnerError("governed review finding is invalid")
    if (report["verdict"] == "PASS") == bool(report["findings"]):
        raise ReviewRunnerError("governed review verdict/findings are inconsistent")
    return dict(report)


def _validate_registered_resource_retrieval(
    value: Any, *, provider_identity: Mapping[str, Any], card: Mapping[str, Any],
    handoff_hash: str, resource_receipt_hash: str, challenge: str | None = None,
) -> dict[str, Any]:
    required = {
        "schema", "status", "run_id", "challenge", "skill_contract_hash",
        "runtime_identity", "bindings", "retrieval_attestation",
        "resource_bundle_hash", "bundle_hash",
    }
    if (
        not isinstance(value, Mapping)
        or set(value) != required
        or value.get("schema") != "tgw-registered-review-resource-retrieval/v1"
        or value.get("status") != "PASS"
        or not isinstance(value.get("run_id"), str)
        or not value["run_id"]
        or not isinstance(value.get("challenge"), str)
        or len(value["challenge"]) != 64
        or challenge is not None and value["challenge"] != challenge
    ):
        raise ReviewRunnerError("governed review registered resource retrieval is invalid")
    unsigned = dict(value)
    claimed = unsigned.pop("bundle_hash")
    if claimed != _hash(unsigned):
        raise ReviewRunnerError("governed review registered resource retrieval hash is invalid")
    sandbox_identity = provider_identity["sandbox_identity"]
    expected_bindings = {
        name: card["bindings"][name] for name in _CONTEXT_BINDING_NAMES
    }
    if (
        not isinstance(value.get("skill_contract_hash"), str)
        or not value["skill_contract_hash"].startswith(_SHA256_PREFIX)
        or not isinstance(value.get("resource_bundle_hash"), str)
        or not value["resource_bundle_hash"].startswith(_SHA256_PREFIX)
        or value.get("runtime_identity") != sandbox_identity
        or value.get("bindings") != expected_bindings
    ):
        raise ReviewRunnerError("governed review context comparison is invalid")
    service = provider_identity["context_bundle_service"]
    attestation = value.get("retrieval_attestation")
    try:
        verified_attestation = validate_harness_retrieval_attestation(
            attestation,
            expected={
                "service_id": service["service_id"],
                "client_id": service["client_id"],
                "card_hash": card["card_hash"],
                "role": "independent-review",
                "execution_identity": _review_execution_identity(
                    value["challenge"], sandbox_identity["uid"], sandbox_identity["gid"],
                ),
                "handoff_hash": handoff_hash,
                "resource_receipt_hash": resource_receipt_hash,
                "resources": {name: card["bindings"][name] for name in sorted(card["bindings"])},
            },
            attestation_key_id=service["attestation_key_id"],
            attestation_public_key=service["attestation_public_key"],
        )
    except ResourceVerificationError as exc:
        raise ReviewRunnerError("governed review context attestation is invalid") from exc
    if verified_attestation["run_id"] != value["run_id"]:
        raise ReviewRunnerError("governed review context attestation run differs")
    return dict(value)


def _compose_registered_resource_retrieval(
    attestation: Mapping[str, Any], *, run_id: str, challenge: str,
    provider_identity: Mapping[str, Any], card: Mapping[str, Any],
    handoff_hash: str, resource_receipt_hash: str,
    runtime_identity: Mapping[str, int], consumed_skill_contract_hash: str,
    resource_bundle_hash: str,
) -> dict[str, Any]:
    unsigned = {
        "schema": "tgw-registered-review-resource-retrieval/v1", "status": "PASS",
        "run_id": run_id, "challenge": challenge,
        "skill_contract_hash": consumed_skill_contract_hash,
        "resource_bundle_hash": resource_bundle_hash,
        "runtime_identity": dict(runtime_identity),
        "bindings": {name: card["bindings"][name] for name in _CONTEXT_BINDING_NAMES},
        "retrieval_attestation": dict(attestation),
    }
    value = {**unsigned, "bundle_hash": _hash(unsigned)}
    return _validate_registered_resource_retrieval(
        value, provider_identity=provider_identity, card=card,
        handoff_hash=handoff_hash,
        resource_receipt_hash=resource_receipt_hash, challenge=challenge,
    )


def _validate_service_resource_bundle(
    value: Any, *, card: Mapping[str, Any], client_id: str,
    challenge: str, skill_contract_hash: str,
) -> dict[str, Any]:
    required = {
        "schema", "client_id", "challenge", "skill_contract_hash",
        "retrieval_attestation", "resources", "bundle_hash",
    }
    if (
        not isinstance(value, Mapping)
        or set(value) != required
        or value.get("schema") != "tgw-context-review-resource-bundle/v1"
        or value.get("client_id") != client_id
        or value.get("challenge") != challenge
        or value.get("skill_contract_hash") != skill_contract_hash
        or not isinstance(value.get("resources"), Mapping)
        or set(value["resources"]) != CARD_RESOURCE_NAMES
    ):
        raise ReviewRunnerError("governed review service resource bundle is invalid")
    unsigned = dict(value)
    claimed = unsigned.pop("bundle_hash")
    if claimed != _hash(unsigned):
        raise ReviewRunnerError("governed review service resource bundle hash is invalid")
    for name in sorted(CARD_RESOURCE_NAMES):
        item = value["resources"][name]
        binding = card["bindings"][name]
        if (
            not isinstance(item, Mapping)
            or set(item) != {"ref", "hash", "content_sha256", "content_base64"}
            or item.get("ref") != binding["ref"]
            or item.get("hash") != binding["hash"]
            or not isinstance(item.get("content_base64"), str)
        ):
            raise ReviewRunnerError("governed review service resource binding differs")
        try:
            raw = base64.b64decode(item["content_base64"], validate=True)
        except (TypeError, ValueError) as exc:
            raise ReviewRunnerError("governed review service resource encoding is invalid") from exc
        if content_hash(raw) != item["content_sha256"]:
            raise ReviewRunnerError("governed review service resource content differs")
    return dict(value)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return _SHA256_PREFIX + digest.hexdigest()


def _fd_hash(descriptor: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while True:
        block = os.pread(descriptor, 1024 * 1024, offset)
        if not block:
            break
        digest.update(block)
        offset += len(block)
    return _SHA256_PREFIX + digest.hexdigest()


def _stat_identity(value: os.stat_result) -> dict[str, Any]:
    return {
        "device": value.st_dev,
        "inode": value.st_ino,
        "uid": value.st_uid,
        "gid": value.st_gid,
        "mode": stat.S_IMODE(value.st_mode),
        "size": value.st_size,
        "mtime_ns": value.st_mtime_ns,
        "ctime_ns": value.st_ctime_ns,
    }


def _identity(path: Path) -> dict[str, Any]:
    return _stat_identity(path.stat(follow_symlinks=False))


def _command_identity(path: Path) -> dict[str, Any]:
    value = path.lstat()
    return {
        "device": value.st_dev, "inode": value.st_ino, "uid": value.st_uid,
        "gid": value.st_gid, "mode": stat.S_IMODE(value.st_mode),
        "nlink": value.st_nlink, "size": value.st_size, "mtime_ns": value.st_mtime_ns,
        "is_symlink": path.is_symlink(),
        "link_target": os.readlink(path) if path.is_symlink() else None,
        "resolved_path": str(path.resolve()),
    }


def _check_policy(value: os.stat_result, policy: Mapping[str, Any], *, label: str) -> None:
    if set(policy) != {"uid", "gid", "forbidden_mode"} or not all(
        isinstance(policy.get(field), int) for field in policy
    ):
        raise ReviewRunnerError(f"{label} owner policy is invalid")
    if value.st_uid != policy["uid"] or value.st_gid != policy["gid"]:
        raise ReviewRunnerError(f"{label} resolved owner is not admitted")
    if stat.S_IMODE(value.st_mode) & policy["forbidden_mode"]:
        raise ReviewRunnerError(f"{label} resolved mode is not admitted")


def _walk_held_tree(
    root_fd: int, *, policy: Mapping[str, Any], label: str,
    max_file_bytes: int = 64 * 1024 * 1024,
    max_total_bytes: int = 512 * 1024 * 1024,
    max_entries: int = 100_000,
    max_depth: int = 64,
    retain_contents: bool = True,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    entries: list[dict[str, Any]] = []
    contents: dict[str, bytes] = {}
    total_bytes = 0

    def visit(directory_fd: int, prefix: str, depth: int) -> None:
        nonlocal total_bytes
        if depth > max_depth:
            raise ReviewRunnerError(f"{label} exceeds its depth bound")
        for name in sorted(os.listdir(directory_fd)):
            if len(entries) >= max_entries:
                raise ReviewRunnerError(f"{label} exceeds its entry bound")
            if not name or "/" in name or name in {".", ".."}:
                raise ReviewRunnerError(f"{label} contains an invalid path")
            relative = f"{prefix}/{name}" if prefix else name
            value = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISLNK(value.st_mode):
                raise ReviewRunnerError(f"{label} cannot contain symlinks")
            _check_policy(value, policy, label=label)
            if stat.S_ISDIR(value.st_mode):
                child = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=directory_fd,
                )
                try:
                    entries.append({"path": relative, "kind": "directory", **_stat_identity(value)})
                    visit(child, relative, depth + 1)
                finally:
                    os.close(child)
            elif stat.S_ISREG(value.st_mode):
                if value.st_nlink != 1:
                    raise ReviewRunnerError(f"{label} cannot contain hard links")
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
                try:
                    digest = hashlib.sha256()
                    data = bytearray() if retain_contents else None
                    file_bytes = 0
                    while True:
                        block = os.read(descriptor, 1024 * 1024)
                        if not block:
                            break
                        file_bytes += len(block)
                        if file_bytes > max_file_bytes:
                            raise ReviewRunnerError(f"{label} file exceeds its bound")
                        digest.update(block)
                        if data is not None:
                            data.extend(block)
                    total_bytes += file_bytes
                    if total_bytes > max_total_bytes:
                        raise ReviewRunnerError(f"{label} exceeds its aggregate byte bound")
                    entries.append({
                        "path": relative, "kind": "file", **_stat_identity(value),
                        "sha256": _SHA256_PREFIX + digest.hexdigest(),
                    })
                    if data is not None:
                        contents[relative] = bytes(data)
                finally:
                    os.close(descriptor)
            else:
                raise ReviewRunnerError(f"{label} contains a special file")

    root_value = os.fstat(root_fd)
    _check_policy(root_value, policy, label=label)
    visit(root_fd, "", 0)
    return {
        "root_identity": _stat_identity(root_value), "entries": entries,
        "aggregate_bytes": total_bytes,
    }, contents


def _held_snapshot(
    root_fd: int, *, trusted_uid: int, trusted_gid: int,
) -> tuple[str, dict[str, Any]]:
    manifest, contents = _walk_held_tree(
        root_fd,
        policy={"uid": trusted_uid, "gid": trusted_gid, "forbidden_mode": 0o022},
        label="governed review snapshot",
    )
    digest = hashlib.sha256()
    for relative in sorted(contents):
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(contents[relative])
        digest.update(b"\0")
    return _SHA256_PREFIX + digest.hexdigest(), manifest["root_identity"]


def snapshot_hash(root: Path) -> str:
    """Hash a protected snapshot after anchoring its root descriptor."""

    value = root.stat(follow_symlinks=False)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        result, _ = _held_snapshot(
            descriptor, trusted_uid=value.st_uid, trusted_gid=value.st_gid,
        )
        return result
    finally:
        os.close(descriptor)


def validate_execution(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema", "provider", "receiver_identity", "card_hash", "handoff_hash",
        "promptcraft_receipt_hash", "resource_receipt_hash", "source",
        "source_protection", "plan_commit", "bindings",
        "provider_identity", "invocation", "lifecycle", "output",
        "registered_resource_retrieval", "review",
        "execution_hash",
    }
    if not isinstance(value, Mapping) or set(value) != required or value.get("schema") != EXECUTION_SCHEMA:
        raise ReviewRunnerError("governed review execution contract is invalid")
    unsigned = dict(value)
    claimed = unsigned.pop("execution_hash")
    if claimed != _hash(unsigned):
        raise ReviewRunnerError("governed review execution hash mismatch")
    if not isinstance(value.get("provider"), str) or not value["provider"]:
        raise ReviewRunnerError("governed review provider identity is missing")
    if value.get("receiver_identity") != f"{value['provider']}:tgw-review":
        raise ReviewRunnerError("governed review receiver identity mismatch")
    provider_identity = value.get("provider_identity")
    identity_fields = {
        "schema", "provider", "account_identity", "version", "skill", "artifacts",
        "skill_source_provenance", "sandbox_layout", "context_bundle_service",
        "sandbox_identity",
        "environment_sha256", "argv_template", "argv_template_hash",
        "command_policy", "network_policy", "health",
    }
    if not isinstance(provider_identity, Mapping) or set(provider_identity) != identity_fields or provider_identity.get("schema") != IDENTITY_SCHEMA:
        raise ReviewRunnerError("governed review live provider identity is invalid")
    if (
        provider_identity.get("provider") != value["provider"]
        or provider_identity.get("skill") != "tgw-review"
    ):
        raise ReviewRunnerError("governed review provider account/skill is unavailable")
    artifacts = provider_identity.get("artifacts")
    if not isinstance(artifacts, Mapping) or set(artifacts) != {
        "sandbox", "runtime", "executable", "skill_contract",
        "mcp_config", "credential", "execution_environment",
    }:
        raise ReviewRunnerError("governed review provider artifacts are invalid")
    _validate_skill_provenance(
        provider_identity.get("skill_source_provenance"),
        artifacts["skill_contract"].get("manifest_hash"),
    )
    if not isinstance(provider_identity.get("account_identity"), str) or not provider_identity["account_identity"].startswith(_SHA256_PREFIX):
        raise ReviewRunnerError("governed review account identity is invalid")
    source = value.get("source")
    if not isinstance(source, Mapping) or set(source) != {"commit", "tree", "snapshot_hash"}:
        raise ReviewRunnerError("governed review source identity is invalid")
    if not all(isinstance(source.get(field), str) and source[field] for field in source):
        raise ReviewRunnerError("governed review source identity is invalid")
    bindings = value.get("bindings")
    command_policy = provider_identity.get("command_policy")
    if (
        not isinstance(bindings, Mapping)
        or not isinstance(command_policy, Mapping)
        or command_policy.get("context_bindings")
        != {name: bindings.get(name) for name in _CONTEXT_BINDING_NAMES}
        or source["snapshot_hash"] != bindings.get("source_tree", {}).get("hash")
    ):
        raise ReviewRunnerError("governed review retained context binding is invalid")
    source_protection = value.get("source_protection")
    if (
        not isinstance(source_protection, Mapping)
        or set(source_protection) != {"trusted_uid", "trusted_gid", "root_identity", "held_through_use"}
        or source_protection.get("held_through_use") is not True
        or not isinstance(source_protection.get("trusted_uid"), int)
        or not isinstance(source_protection.get("trusted_gid"), int)
        or not isinstance(source_protection.get("root_identity"), Mapping)
    ):
        raise ReviewRunnerError("governed review source protection evidence is invalid")
    lifecycle = value.get("lifecycle")
    if (
        not isinstance(lifecycle, Mapping)
        or set(lifecycle) != {
            "started_at", "completed_at", "exit_code", "timed_out",
            "outer_process_group_reaped", "containment",
        }
        or lifecycle.get("exit_code") != 0
        or lifecycle.get("timed_out") is not False
        or lifecycle.get("outer_process_group_reaped") is not True
        or lifecycle.get("containment") != "bubblewrap-pid-namespace-empty-on-exit"
    ):
        raise ReviewRunnerError("governed review process did not complete cleanly")
    health = provider_identity.get("health")
    try:
        started = datetime.fromisoformat(str(lifecycle["started_at"]).replace("Z", "+00:00"))
        observed = datetime.fromisoformat(str(health["observed_at"]).replace("Z", "+00:00"))
        expires = datetime.fromisoformat(str(health["expires_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ReviewRunnerError("governed review provider health time is invalid") from exc
    if not observed <= started < expires:
        raise ReviewRunnerError("governed review provider health was stale at execution")
    network_policy = _validate_network_policy(
        provider_identity.get("network_policy"), observed_at=started,
    )
    context_service = _validate_context_service(
        provider_identity.get("context_bundle_service"),
    )
    if (
        context_service["context_service_endpoint"] not in network_policy["endpoints"]
        or context_service["broker_endpoint"] in network_policy["endpoints"]
    ):
        raise ReviewRunnerError("governed review context service egress is not admitted")
    invocation = value.get("invocation")
    if (
        not isinstance(invocation, Mapping)
        or set(invocation) != {
            "argv_sha256", "argv_template_hash", "tool_policy", "skill_contract_hash",
            "skill_manifest_hash", "held_mcp_config", "installed_skill_discovery",
            "sandbox_profile_hash", "pid_namespace", "root_read_only",
            "network_policy_hash", "runtime_uid", "runtime_gid",
            "timeout_seconds", "output_limit",
        }
        or invocation.get("argv_template_hash") != provider_identity.get("argv_template_hash")
        or invocation.get("tool_policy") != provider_identity.get("command_policy", {}).get("tool_policy")
        or invocation.get("skill_manifest_hash") != artifacts["skill_contract"].get("manifest_hash")
        or invocation.get("held_mcp_config") is not True
        or invocation.get("installed_skill_discovery") != "protected-held-contract-only"
        or invocation.get("sandbox_profile_hash") != _hash(list(_SANDBOX_FLAGS))
        or invocation.get("pid_namespace") is not True
        or invocation.get("root_read_only") is not True
        or invocation.get("network_policy_hash") != provider_identity.get("network_policy", {}).get("policy_hash")
        or invocation.get("runtime_uid") != provider_identity.get("sandbox_identity", {}).get("uid")
        or invocation.get("runtime_gid") != provider_identity.get("sandbox_identity", {}).get("gid")
    ):
        raise ReviewRunnerError("governed review invocation evidence is invalid")
    try:
        completed = datetime.fromisoformat(str(lifecycle["completed_at"]).replace("Z", "+00:00"))
        allowed_duration = float(invocation["timeout_seconds"]) + 3.0
    except (TypeError, ValueError) as exc:
        raise ReviewRunnerError("governed review lifecycle duration is invalid") from exc
    if (
        started.tzinfo is None or completed.tzinfo is None
        or completed < started
        or (completed - started).total_seconds() > allowed_duration
    ):
        raise ReviewRunnerError("governed review lifecycle duration is invalid")
    registered_retrieval = _validate_registered_resource_retrieval(
        value.get("registered_resource_retrieval"), provider_identity=provider_identity,
        card={"card_hash": value["card_hash"], "bindings": bindings},
        handoff_hash=value["handoff_hash"],
        resource_receipt_hash=value["resource_receipt_hash"],
    )
    if registered_retrieval["skill_contract_hash"] != invocation["skill_contract_hash"]:
        raise ReviewRunnerError("governed review consumed skill contract is invalid")
    _validate_report_evidence(value.get("review"), source["snapshot_hash"])
    return dict(value)


def validate_execution_handoff_binding(
    execution: Mapping[str, Any], card: Mapping[str, Any], handoff: Mapping[str, Any],
) -> None:
    """Cross-bind retained execution bytes to the retained Promptcraft chain."""

    normalized = validate_execution(execution)
    if (
        handoff.get("card") != card
        or normalized["card_hash"] != card.get("card_hash")
        or normalized["bindings"] != card.get("bindings")
        or normalized["handoff_hash"] != handoff.get("handoff_hash")
        or normalized["promptcraft_receipt_hash"]
        != handoff.get("receipt", {}).get("receipt_hash")
        or normalized["resource_receipt_hash"]
        != handoff.get("resource_receipt", {}).get("receipt_hash")
        or normalized["provider"] != card.get("selected_provider")
        or normalized["plan_commit"] != card.get("plan_commit")
    ):
        raise ReviewRunnerError("governed review execution/handoff binding mismatch")


def _bounded_run(
    argv: Sequence[str], *, environment: Mapping[str, str], timeout_seconds: float,
    output_limit: int, pass_fds: Sequence[int],
) -> tuple[int, bytes, bytes, bool, bool]:
    selector = selectors.DefaultSelector()
    process: subprocess.Popen[bytes] | None = None
    buffers: dict[Any, bytearray] = {}
    timed_out = False
    overflow = False

    def terminate() -> None:
        if process is None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired as exc:
                raise ReviewRunnerError("governed review process could not be reaped") from exc

    try:
        process = subprocess.Popen(
            list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=dict(environment), start_new_session=True, pass_fds=tuple(pass_fds),
        )
        assert process.stdout is not None and process.stderr is not None
        for stream in (process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
            buffers[stream] = bytearray()
        deadline = time.monotonic() + timeout_seconds
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            for key, _ in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffers[key.fileobj].extend(chunk)
                if sum(len(item) for item in buffers.values()) > output_limit:
                    overflow = True
                    break
            if overflow:
                break
        if not timed_out and not overflow:
            try:
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
        if timed_out or overflow:
            terminate()
        else:
            # Bubblewrap is PID 1 of the provider namespace. Its clean exit is
            # the containment proof: the kernel destroys any escaped session.
            terminate()
        if process.poll() is None:
            raise ReviewRunnerError("governed review process remains live")
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise ReviewRunnerError("governed review outer process group is not empty")
        return (
            int(process.returncode), bytes(buffers[process.stdout]), bytes(buffers[process.stderr]),
            timed_out, overflow,
        )
    except Exception:
        terminate()
        raise
    finally:
        selector.close()
        if process is not None:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()


def run_governed_review(
    handoff: Mapping[str, Any], *, snapshot: Path, source_commit: str, source_tree: str,
    plan_commit: str, provider: str, provider_identity: Mapping[str, Any],
    provider_argv: Sequence[str],
    environment: Mapping[str, str], trusted_uid: int, trusted_gid: int,
    evidence_sink_descriptor: Mapping[str, Any],
    publish_execution: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    read_execution: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    read_context_bundle: Callable[[str], Mapping[str, Any]],
    timeout_seconds: float = 900, output_limit: int = 8 * 1024 * 1024,
    challenge_source: Callable[[], str] | None = None,
) -> dict[str, Any]:
    """Execute and capture one exact provider-neutral tgw-review invocation.

    ``provider_argv`` is the operator-admitted harness command.  It must contain
    exactly one ``{prompt}`` and one ``{snapshot}`` token, preventing the
    adapter from silently changing the provider-specific CLI contract.
    """

    from promptcraft.handoff import HandoffError, verify_for_launcher

    execution_observed_at = datetime.now(timezone.utc)
    try:
        invocation = verify_for_launcher(handoff, now=execution_observed_at)
    except HandoffError as exc:
        raise ReviewRunnerError(f"invalid governed review handoff: {exc}") from exc
    card = handoff["card"]
    receiver_identity = f"{provider}:tgw-review"
    if invocation["receiver_identity"] != receiver_identity or card["selected_provider"] != provider:
        raise ReviewRunnerError("Promptcraft handoff does not select the governed review provider")
    if card["plan_commit"] != plan_commit:
        raise ReviewRunnerError("governed review Plan binding is stale")
    _validate_evidence_sink_descriptor(
        evidence_sink_descriptor, card_binding=card["bindings"]["receipt_sink"],
    )
    _validate_card_context_service(
        card, _validate_context_service(provider_identity.get("context_bundle_service")),
    )
    if (
        list(provider_argv).count("{prompt}") != 1
        or list(provider_argv).count("{snapshot}") != 1
        or list(provider_argv).count("{mcp_config}") != 1
    ):
        raise ReviewRunnerError("governed review provider command framing is invalid")
    expected_snapshot = card["bindings"]["source_tree"]["hash"]
    snapshot_named_before = _command_identity(snapshot)
    snapshot_fd = os.open(snapshot, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        before_hash, before_identity = _held_snapshot(
            snapshot_fd, trusted_uid=trusted_uid, trusted_gid=trusted_gid,
        )
        if before_hash != expected_snapshot:
            raise ReviewRunnerError("governed review source X does not match the card")
        held = validate_execution_identity(
            provider_identity, provider, provider_argv, environment,
            observed_at=execution_observed_at,
        )
        expected_context = {
            name: card["bindings"][name] for name in _CONTEXT_BINDING_NAMES
        }
        if held["context_bindings"] != expected_context:
            raise ReviewRunnerError("governed review context bindings are stale")
    except Exception:
        os.close(snapshot_fd)
        raise
    challenge = secrets.token_hex(32) if challenge_source is None else challenge_source()
    if not isinstance(challenge, str) or len(challenge) != 64:
        raise ReviewRunnerError("governed review challenge source is invalid")
    card_json = _canonical(card).decode("utf-8")
    prompt = "\n".join([
        handoff["instruction"],
        "Use only the protected tgw-review skill installed at "
        f"{held['sandbox_layout']['skill_mount']}; its exact held contract follows.",
        f"Held skill contract hash: {held['skill_contract_hash']}",
        held["skill_contract"],
        "You must use Skill and the admitted read-only MCP tools. Call "
        f"{_CONTEXT_BUNDLE_TOOL} with challenge={challenge}, "
        f"card_json={card_json}, handoff_hash={handoff['handoff_hash']}, and "
        f"resource_receipt_hash={handoff['resource_receipt']['receipt_hash']}, and "
        f"skill_contract_hash={held['skill_contract_hash']}. Compare its exact "
        "Plan, source, CodeGraph, and environment bindings to the review card, and "
        "confirm that it reports governed_review status PASS.",
        "Return only one JSON object satisfying tgw-code-review/v1.",
    ]) + "\n"
    if len(prompt.encode()) > 1024 * 1024:
        for descriptor in (
            snapshot_fd, held["sandbox_fd"], held["executable_fd"],
            held["runtime_fd"], held["skill_fd"],
            held["mcp_fd"], held["credential_fd"], held["execution_environment_fd"],
        ):
            os.close(descriptor)
        raise ReviewRunnerError("governed review prompt exceeds its framing limit")
    sandbox_fd = held["sandbox_fd"]
    runtime_fd = held["runtime_fd"]
    executable_fd = held["executable_fd"]
    skill_fd = held["skill_fd"]
    mcp_fd = held["mcp_fd"]
    credential_fd = held["credential_fd"]
    execution_environment_fd = held["execution_environment_fd"]
    held_before = {
        descriptor: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        for descriptor in (
            snapshot_fd, sandbox_fd, runtime_fd, executable_fd, skill_fd, mcp_fd,
            credential_fd,
            execution_environment_fd,
        )
        for value in (os.fstat(descriptor),)
    }
    provider_command = [
        prompt
        if item == "{prompt}"
        else "/tmp/workspace"
        if item == "{snapshot}"
        else f"/proc/self/fd/{mcp_fd}"
        if item == "{mcp_config}"
        else item
        for item in provider_argv
    ]
    provider_command[0] = f"/proc/self/fd/{executable_fd}"
    layout = held["sandbox_layout"]
    home = PurePosixPath(layout["home"])
    mount_parents = {
        parent
        for configured in (
            layout["skill_mount"], layout["credential_mount"],
        )
        for parent in PurePosixPath(configured).parents
        if parent != home and home in parent.parents
    }
    layout_directories = [
        item
        for path in sorted(mount_parents, key=lambda item: len(item.parts))
        for item in ("--dir", str(path))
    ]
    argv = [
        f"/proc/self/fd/{sandbox_fd}", *_SANDBOX_FLAGS,
        "--uid", str(held["sandbox_identity"]["uid"]),
        "--gid", str(held["sandbox_identity"]["gid"]),
        "--dir", "/usr", "--dir", "/lib", "--dir", "/lib64", "--dir", "/etc",
        "--dir", "/etc/ssl",
        "--ro-bind", f"/proc/self/fd/{runtime_fd}/usr", "/usr",
        "--ro-bind", f"/proc/self/fd/{runtime_fd}/lib", "/lib",
        "--ro-bind", f"/proc/self/fd/{runtime_fd}/lib64", "/lib64",
        "--ro-bind", f"/proc/self/fd/{runtime_fd}/etc/ssl", "/etc/ssl",
        "--ro-bind", f"/proc/self/fd/{runtime_fd}/etc/resolv.conf", "/etc/resolv.conf",
        "--ro-bind", f"/proc/self/fd/{runtime_fd}/etc/nsswitch.conf", "/etc/nsswitch.conf",
        "--ro-bind", f"/proc/self/fd/{runtime_fd}/etc/hosts", "/etc/hosts",
        "--dir", layout["home"], *layout_directories,
        "--dir", layout["workspace"],
        "--ro-bind", f"/proc/self/fd/{skill_fd}", layout["skill_mount"],
        "--ro-bind", f"/proc/self/fd/{credential_fd}", layout["credential_mount"],
        "--ro-bind", f"/proc/self/fd/{snapshot_fd}", layout["workspace"],
        "--setenv", "HOME", layout["home"], "--chdir", layout["workspace"],
        "--", *provider_command,
    ]
    started = datetime.now(timezone.utc)
    try:
        exit_code, stdout, stderr, timed_out, overflow = _bounded_run(
            argv, environment=environment, timeout_seconds=timeout_seconds,
            output_limit=output_limit,
            pass_fds=(
                snapshot_fd, sandbox_fd, runtime_fd, executable_fd, skill_fd,
                mcp_fd,
                credential_fd,
            ),
        )
        for descriptor, expected in held_before.items():
            value = os.fstat(descriptor)
            if (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns) != expected:
                raise ReviewRunnerError("governed review held input changed during execution")
        after_hash, after_identity = _held_snapshot(
            snapshot_fd, trusted_uid=trusted_uid, trusted_gid=trusted_gid,
        )
        if (after_hash, after_identity) != (before_hash, before_identity):
            raise ReviewRunnerError("governed review mutated or exchanged source X")
        revalidated = validate_execution_identity(
            provider_identity, provider, provider_argv, environment,
            observed_at=started,
        )
        try:
            if (
                revalidated["skill_contract_hash"] != held["skill_contract_hash"]
                or revalidated["skill_manifest_hash"] != held["skill_manifest_hash"]
            ):
                raise ReviewRunnerError("governed review skill contract changed")
        finally:
            for descriptor in (
                revalidated["sandbox_fd"], revalidated["runtime_fd"],
                revalidated["executable_fd"],
                revalidated["skill_fd"], revalidated["mcp_fd"],
                revalidated["credential_fd"],
                revalidated["execution_environment_fd"],
            ):
                os.close(descriptor)
        if timed_out:
            raise ReviewRunnerError("governed review timed out")
        if overflow:
            raise ReviewRunnerError("governed review exceeded its output limit")
        if exit_code != 0:
            raise ReviewRunnerError(
                f"governed review failed with exit code {exit_code} "
                f"and stderr {_SHA256_PREFIX}{hashlib.sha256(stderr).hexdigest()}"
            )
        try:
            envelope = json.loads(stdout)
            if (
                not isinstance(envelope, Mapping)
                or envelope.get("is_error") is not False
                or "result" not in envelope
            ):
                raise ValueError
            raw_result = envelope.get("result")
            review = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ReviewRunnerError("governed review returned malformed output") from exc
        review = _validate_report(
            review, expected_snapshot, Path(f"/proc/self/fd/{snapshot_fd}"),
        )
        context_readback = read_context_bundle(challenge)
        context_readback = _validate_service_resource_bundle(
            context_readback, card=card,
            client_id=provider_identity["context_bundle_service"]["client_id"],
            challenge=challenge,
            skill_contract_hash=held["skill_contract_hash"],
        )
        attestation = context_readback["retrieval_attestation"]
        registered_retrieval = _compose_registered_resource_retrieval(
            attestation,
            run_id=attestation["run_id"], challenge=challenge,
            provider_identity=provider_identity, card=card,
            handoff_hash=handoff["handoff_hash"],
            resource_receipt_hash=handoff["resource_receipt"]["receipt_hash"],
            runtime_identity={
                "uid": held["sandbox_identity"]["uid"],
                "gid": held["sandbox_identity"]["gid"],
            },
            consumed_skill_contract_hash=held["skill_contract_hash"],
            resource_bundle_hash=context_readback["bundle_hash"],
        )
    finally:
        for descriptor in (
            snapshot_fd, sandbox_fd, runtime_fd, executable_fd, skill_fd, mcp_fd,
            credential_fd,
            execution_environment_fd,
        ):
            os.close(descriptor)
    completed = datetime.now(timezone.utc)
    if _command_identity(snapshot) != snapshot_named_before:
        raise ReviewRunnerError("governed review named source root changed")
    unsigned = {
        "schema": EXECUTION_SCHEMA,
        "provider": provider,
        "receiver_identity": receiver_identity,
        "card_hash": card["card_hash"],
        "handoff_hash": handoff["handoff_hash"],
        "promptcraft_receipt_hash": handoff["receipt"]["receipt_hash"],
        "resource_receipt_hash": handoff["resource_receipt"]["receipt_hash"],
        "source": {"commit": source_commit, "tree": source_tree, "snapshot_hash": expected_snapshot},
        "source_protection": {
            "trusted_uid": trusted_uid, "trusted_gid": trusted_gid,
            "root_identity": before_identity, "held_through_use": True,
        },
        "plan_commit": plan_commit,
        "bindings": dict(card["bindings"]),
        "provider_identity": dict(provider_identity),
        "invocation": {
            "argv_sha256": _hash(argv),
            "argv_template_hash": provider_identity["argv_template_hash"],
            "tool_policy": provider_identity["command_policy"]["tool_policy"],
            "skill_contract_hash": held["skill_contract_hash"],
            "skill_manifest_hash": held["skill_manifest_hash"],
            "held_mcp_config": True,
            "installed_skill_discovery": "protected-held-contract-only",
            "sandbox_profile_hash": _hash(list(_SANDBOX_FLAGS)),
            "network_policy_hash": provider_identity["network_policy"]["policy_hash"],
            "runtime_uid": held["sandbox_identity"]["uid"],
            "runtime_gid": held["sandbox_identity"]["gid"],
            "pid_namespace": True, "root_read_only": True,
            "timeout_seconds": timeout_seconds, "output_limit": output_limit,
        },
        "lifecycle": {
            "started_at": started.isoformat(), "completed_at": completed.isoformat(),
            "exit_code": exit_code, "timed_out": False,
            "outer_process_group_reaped": True,
            "containment": "bubblewrap-pid-namespace-empty-on-exit",
        },
        "output": {
            "stdout_sha256": _SHA256_PREFIX + hashlib.sha256(stdout).hexdigest(),
            "stderr_sha256": _SHA256_PREFIX + hashlib.sha256(stderr).hexdigest(),
            "stdout_size": len(stdout), "stderr_size": len(stderr),
        },
        "registered_resource_retrieval": registered_retrieval,
        "review": review,
    }
    result = {**unsigned, "execution_hash": _hash(unsigned)}
    normalized = validate_execution(result)
    publication = publish_execution(normalized)
    expected_publication = {
        "schema": "tgw-governed-review-publication/v1",
        "sink_ref": card["bindings"]["receipt_sink"]["ref"],
        "execution_hash": normalized["execution_hash"],
    }
    if (
        not isinstance(publication, Mapping)
        or any(publication.get(field) != expected for field, expected in expected_publication.items())
        or not isinstance(publication.get("artifact_ref"), str)
        or not publication["artifact_ref"]
        or publication.get("artifact_hash") != _hash(normalized)
    ):
        raise ReviewRunnerError("bound receipt sink did not retain governed review execution")
    retained = read_execution(publication)
    if retained != normalized or _hash(retained) != publication["artifact_hash"]:
        raise ReviewRunnerError("bound receipt sink readback differs from governed review execution")
    return normalized


def _open_file_artifact(name: str, artifact: Mapping[str, Any]) -> int:
    required = {
        "kind", "configured_path", "configured_identity", "resolved_path",
        "resolved_identity", "content_sha256", "policy",
    }
    if set(artifact) != required or artifact.get("kind") != "file":
        raise ReviewRunnerError(f"governed review {name} artifact is invalid")
    configured = Path(str(artifact["configured_path"]))
    if _command_identity(configured) != artifact["configured_identity"]:
        raise ReviewRunnerError(f"governed review {name} configured identity mismatch")
    resolved = configured.resolve()
    if str(resolved) != artifact["resolved_path"] or resolved.is_symlink():
        raise ReviewRunnerError(f"governed review {name} resolved path mismatch")
    descriptor = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        value = os.fstat(descriptor)
        if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
            raise ReviewRunnerError(f"governed review {name} must be a single-link file")
        _check_policy(value, artifact["policy"], label=f"governed review {name}")
        if _stat_identity(value) != artifact["resolved_identity"]:
            raise ReviewRunnerError(f"governed review {name} resolved identity mismatch")
        if _fd_hash(descriptor) != artifact["content_sha256"]:
            raise ReviewRunnerError(f"governed review {name} content mismatch")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_tree_artifact(
    name: str, artifact: Mapping[str, Any], *, retain_contents: bool = True,
    max_file_bytes: int = 1024 * 1024,
) -> tuple[int, dict[str, bytes]]:
    required = {
        "kind", "configured_path", "configured_identity", "resolved_path",
        "resolved_identity", "manifest", "manifest_hash", "policy",
    }
    if set(artifact) != required or artifact.get("kind") != "tree":
        raise ReviewRunnerError(f"governed review {name} artifact is invalid")
    configured = Path(str(artifact["configured_path"]))
    if _command_identity(configured) != artifact["configured_identity"]:
        raise ReviewRunnerError(f"governed review {name} configured identity mismatch")
    resolved = configured.resolve()
    if str(resolved) != artifact["resolved_path"] or resolved.is_symlink():
        raise ReviewRunnerError(f"governed review {name} resolved path mismatch")
    descriptor = os.open(resolved, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        manifest, contents = _walk_held_tree(
            descriptor, policy=artifact["policy"], label=f"governed review {name}",
            max_file_bytes=max_file_bytes,
            max_total_bytes=1024 * 1024 if retain_contents else 1024 * 1024 * 1024,
            max_entries=10_000, max_depth=32, retain_contents=retain_contents,
        )
        if manifest["root_identity"] != artifact["resolved_identity"]:
            raise ReviewRunnerError(f"governed review {name} resolved identity mismatch")
        if manifest != artifact["manifest"] or _hash(manifest) != artifact["manifest_hash"]:
            raise ReviewRunnerError(f"governed review {name} manifest mismatch")
        return descriptor, contents
    except Exception:
        os.close(descriptor)
        raise


def _open_secret_artifact(name: str, artifact: Mapping[str, Any]) -> int:
    required = {
        "kind", "configured_path", "configured_identity", "resolved_path",
        "resolved_identity", "policy", "secret_ref",
    }
    if set(artifact) != required or artifact.get("kind") != "secret-file":
        raise ReviewRunnerError(f"governed review {name} artifact is invalid")
    if not isinstance(artifact.get("secret_ref"), str) or not artifact["secret_ref"]:
        raise ReviewRunnerError(f"governed review {name} secret reference is invalid")
    configured = Path(str(artifact["configured_path"]))
    if _command_identity(configured) != artifact["configured_identity"]:
        raise ReviewRunnerError(f"governed review {name} configured identity mismatch")
    resolved = configured.resolve()
    if str(resolved) != artifact["resolved_path"] or resolved.is_symlink():
        raise ReviewRunnerError(f"governed review {name} resolved path mismatch")
    descriptor = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        value = os.fstat(descriptor)
        if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
            raise ReviewRunnerError(f"governed review {name} must be a single-link file")
        _check_policy(value, artifact["policy"], label=f"governed review {name}")
        if _stat_identity(value) != artifact["resolved_identity"]:
            raise ReviewRunnerError(f"governed review {name} resolved identity mismatch")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _render_skill_contract(contents: Mapping[str, bytes]) -> tuple[str, str]:
    if "SKILL.md" not in contents:
        raise ReviewRunnerError("governed review skill contract lacks SKILL.md")
    sections: list[str] = []
    total = 0
    for relative in sorted(contents):
        try:
            text = contents[relative].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReviewRunnerError("governed review skill contract must be UTF-8 text") from exc
        total += len(contents[relative])
        if total > 1024 * 1024:
            raise ReviewRunnerError("governed review skill contract exceeds its bound")
        content_hash = _SHA256_PREFIX + hashlib.sha256(contents[relative]).hexdigest()
        sections.append(f"--- {relative} ({content_hash}) ---\n{text}")
    rendered = "\n".join(sections)
    return rendered, _SHA256_PREFIX + hashlib.sha256(rendered.encode()).hexdigest()


def _validate_context_service(value: Any) -> dict[str, Any]:
    required = {
        "schema", "endpoint", "credential_env", "timeout_seconds", "service_id",
        "client_id", "broker_endpoint", "context_service_endpoint",
        "resource_service_descriptor_hash",
        "resource_service_catalog_ref", "resource_service_catalog_hash",
        "attestation_key_id", "attestation_public_key", "descriptor_hash",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise ReviewRunnerError("governed review context service descriptor is invalid")
    unsigned = dict(value)
    claimed = unsigned.pop("descriptor_hash")
    parsed = urllib_parse.urlsplit(str(value.get("endpoint", "")))
    broker = urllib_parse.urlsplit(str(value.get("broker_endpoint", "")))
    context_service = urllib_parse.urlsplit(
        str(value.get("context_service_endpoint", ""))
    )
    try:
        public_key = base64.b64decode(str(value.get("attestation_public_key", "")), validate=True)
    except ValueError as exc:
        raise ReviewRunnerError("governed review context service key is invalid") from exc
    if (
        value.get("schema") != "tgw-context-bundle-service/v1"
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or broker.scheme != "https"
        or not broker.hostname
        or broker.username is not None
        or broker.password is not None
        or broker.query
        or broker.fragment
        or context_service.scheme != "https"
        or not context_service.hostname
        or context_service.username is not None
        or context_service.password is not None
        or context_service.query
        or context_service.fragment
        or value.get("context_service_endpoint") == value.get("broker_endpoint")
        or not isinstance(value.get("credential_env"), str)
        or not value["credential_env"]
        or not isinstance(value.get("service_id"), str)
        or not value["service_id"]
        or not isinstance(value.get("client_id"), str)
        or not value["client_id"]
        or not isinstance(value.get("timeout_seconds"), int)
        or not 1 <= value["timeout_seconds"] <= 60
        or not isinstance(value.get("resource_service_catalog_ref"), str)
        or not value["resource_service_catalog_ref"]
        or not isinstance(value.get("resource_service_catalog_hash"), str)
        or not value["resource_service_catalog_hash"].startswith(_SHA256_PREFIX)
        or not isinstance(value.get("attestation_key_id"), str)
        or not value["attestation_key_id"]
        or len(public_key) != 32
        or value.get("resource_service_descriptor_hash")
        != resource_service_descriptor_hash({
            "schema": "tgw-registered-resource-service/v2",
            "id": value.get("service_id"), "client_id": value.get("client_id"),
            "endpoint": value.get("endpoint"),
            "credential_env": value.get("credential_env"),
            "timeout_seconds": value.get("timeout_seconds"),
        })
        or claimed != _hash(unsigned)
    ):
        raise ReviewRunnerError("governed review context service descriptor is invalid")
    return dict(value)


def _validate_evidence_sink_descriptor(
    value: Any, *, card_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    required = {
        "schema", "sink_ref", "endpoint", "credential_env", "timeout_seconds",
        "descriptor_hash",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise ReviewRunnerError("governed review evidence sink descriptor is invalid")
    unsigned = dict(value)
    claimed = unsigned.pop("descriptor_hash")
    parsed = urllib_parse.urlsplit(str(value.get("endpoint", "")))
    if (
        value.get("schema") != "tgw-governed-review-evidence-sink-client/v1"
        or not isinstance(value.get("sink_ref"), str)
        or not value["sink_ref"]
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not isinstance(value.get("credential_env"), str)
        or not value["credential_env"]
        or not isinstance(value.get("timeout_seconds"), (int, float))
        or not 0 < float(value["timeout_seconds"]) <= 30
        or claimed != _hash(unsigned)
    ):
        raise ReviewRunnerError("governed review evidence sink descriptor is invalid")
    if card_binding is not None and card_binding != {
        "ref": value["sink_ref"], "hash": value["descriptor_hash"],
    }:
        raise ReviewRunnerError("governed review evidence sink differs from the review card")
    return dict(value)


def _validate_card_context_service(
    card: Mapping[str, Any], service: Mapping[str, Any],
) -> None:
    expected = {
        "id": service["service_id"], "client_id": service["client_id"],
        "descriptor_hash": service["resource_service_descriptor_hash"],
        "catalog_ref": service["resource_service_catalog_ref"],
        "catalog_hash": service["resource_service_catalog_hash"],
    }
    if card.get("resource_service") != expected:
        raise ReviewRunnerError("governed review card resource service differs from provider context")


def _validate_network_policy(
    value: Any, *, observed_at: datetime, expected_key_id: str | None = None,
    expected_public_key: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema", "mode", "endpoints", "enforcement_key_id",
        "enforcement_public_key", "policy_hash", "enforcement_receipt",
    }:
        raise ReviewRunnerError("governed review network policy is invalid")
    endpoints = value.get("endpoints")
    endpoint_strings = (
        isinstance(endpoints, list)
        and all(isinstance(item, str) for item in endpoints)
    )
    parsed_endpoints = [urllib_parse.urlsplit(item) for item in endpoints] if endpoint_strings else []
    policy_unsigned = {
        "schema": value.get("schema"), "mode": value.get("mode"), "endpoints": endpoints,
        "enforcement_key_id": value.get("enforcement_key_id"),
        "enforcement_public_key": value.get("enforcement_public_key"),
    }
    if (
        value.get("schema") != "tgw-governed-review-network-policy/v1"
        or value.get("mode") != "shared-network-enforced-endpoints"
        or not endpoints
        or not endpoint_strings
        or endpoints != sorted(set(endpoints))
        or not all(
            parsed.scheme == "https" and parsed.hostname
            and parsed.username is None and parsed.password is None
            and not parsed.query and not parsed.fragment
            for parsed in parsed_endpoints
        )
        or not isinstance(value.get("enforcement_key_id"), str)
        or not value["enforcement_key_id"]
        or not isinstance(value.get("enforcement_public_key"), str)
        or not value["enforcement_public_key"]
        or value.get("policy_hash") != _hash(policy_unsigned)
        or expected_key_id is not None and value.get("enforcement_key_id") != expected_key_id
        or expected_public_key is not None
        and value.get("enforcement_public_key") != expected_public_key
    ):
        raise ReviewRunnerError("governed review network policy is invalid")
    receipt = value.get("enforcement_receipt")
    if not isinstance(receipt, Mapping) or set(receipt) != {
        "schema", "status", "policy_hash", "enforcement_id", "observed_at",
        "expires_at", "key_id", "receipt_hash", "signature",
    }:
        raise ReviewRunnerError("governed review egress enforcement receipt is invalid")
    try:
        enforced_at = datetime.fromisoformat(str(receipt["observed_at"]).replace("Z", "+00:00"))
        expires_at = datetime.fromisoformat(str(receipt["expires_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewRunnerError("governed review egress enforcement time is invalid") from exc
    if (
        receipt.get("schema") != "tgw-governed-review-egress-enforcement/v1"
        or receipt.get("status") != "ENFORCED"
        or receipt.get("policy_hash") != value["policy_hash"]
        or receipt.get("key_id") != value["enforcement_key_id"]
        or not isinstance(receipt.get("enforcement_id"), str)
        or not receipt["enforcement_id"]
        or not isinstance(receipt.get("key_id"), str)
        or not receipt["key_id"]
        or not enforced_at <= observed_at < expires_at
    ):
        raise ReviewRunnerError("governed review egress policy is not enforced")
    _verify_ed25519_receipt(
        receipt, public_key=value["enforcement_public_key"],
        label="governed review egress enforcement",
    )
    return dict(value)


def validate_execution_identity(
    identity: Mapping[str, Any], provider: str, provider_argv: Sequence[str],
    environment: Mapping[str, str], *, observed_at: datetime,
) -> dict[str, Any]:
    required = {
        "schema", "provider", "account_identity", "version", "skill", "artifacts",
        "skill_source_provenance", "sandbox_layout", "context_bundle_service",
        "sandbox_identity",
        "environment_sha256", "argv_template", "argv_template_hash",
        "command_policy", "network_policy", "health",
    }
    if not isinstance(identity, Mapping) or set(identity) != required or identity.get("schema") != IDENTITY_SCHEMA:
        raise ReviewRunnerError("governed review provider identity is invalid")
    if (
        identity.get("provider") != provider
        or identity.get("skill") != "tgw-review"
    ):
        raise ReviewRunnerError("governed review provider account/skill is unavailable")
    sandbox_identity = identity.get("sandbox_identity")
    if (
        not isinstance(sandbox_identity, Mapping)
        or set(sandbox_identity) != {"uid", "gid"}
        or not all(isinstance(sandbox_identity.get(name), int) for name in ("uid", "gid"))
        or sandbox_identity["uid"] <= 0
        or sandbox_identity["gid"] <= 0
    ):
        raise ReviewRunnerError("governed review sandbox identity is invalid")
    if not provider_argv:
        raise ReviewRunnerError("governed review provider executable is unavailable")
    layout = identity.get("sandbox_layout")
    if not isinstance(layout, Mapping) or set(layout) != {
        "home", "skill_mount", "credential_mount", "workspace",
    }:
        raise ReviewRunnerError("governed review provider sandbox layout is invalid")
    try:
        home = PurePosixPath(layout["home"])
        skill_mount = PurePosixPath(layout["skill_mount"])
        credential_mount = PurePosixPath(layout["credential_mount"])
    except TypeError as exc:
        raise ReviewRunnerError("governed review provider sandbox layout is invalid") from exc
    if (
        str(home) != "/home/reviewer"
        or layout.get("workspace") != "/tmp/workspace"
        or home not in skill_mount.parents
        or home not in credential_mount.parents
        or skill_mount.name != "tgw-review"
        or skill_mount == credential_mount
        or any(
            part in {"", ".", ".."}
            for part in (
                *skill_mount.parts, *credential_mount.parts,
            )
        )
        or environment.get("HOME") != str(home)
    ):
        raise ReviewRunnerError("governed review provider sandbox layout is invalid")
    if list(provider_argv) != identity.get("argv_template") or identity.get("argv_template_hash") != _hash(list(provider_argv)):
        raise ReviewRunnerError("governed review provider argv template mismatch")
    policy = identity.get("command_policy")
    if (
        not isinstance(policy, Mapping)
        or set(policy) != {
            "tool_policy", "read_only", "settings_sources_disabled",
            "held_mcp_config", "held_skill_contract", "sandbox_profile_hash",
            "pid_namespace", "root_read_only", "mcp_endpoints", "context_bindings",
            "argv_policy_fragments", "forbidden_argv_tokens", "required_mcp_tools",
        }
        or policy.get("read_only") is not True
        or policy.get("settings_sources_disabled") is not True
        or policy.get("held_mcp_config") is not True
        or policy.get("held_skill_contract") is not True
        or policy.get("pid_namespace") is not True
        or policy.get("root_read_only") is not True
        or policy.get("sandbox_profile_hash") != _hash(list(_SANDBOX_FLAGS))
        or not isinstance(policy.get("tool_policy"), list)
        or not all(isinstance(item, str) and item for item in policy["tool_policy"])
        or not isinstance(policy.get("mcp_endpoints"), list)
        or not policy.get("mcp_endpoints")
        or not all(
            isinstance(item, str) and item.startswith("https://")
            for item in policy["mcp_endpoints"]
        )
        or not isinstance(policy.get("context_bindings"), Mapping)
        or set(policy["context_bindings"]) != set(_CONTEXT_BINDING_NAMES)
        or any(
            not isinstance(binding, Mapping)
            or set(binding) != {"ref", "hash"}
            or not isinstance(binding.get("ref"), str)
            or not isinstance(binding.get("hash"), str)
            or not binding["hash"].startswith(_SHA256_PREFIX)
            for binding in policy["context_bindings"].values()
        )
        or not isinstance(policy.get("argv_policy_fragments"), list)
        or not all(
            isinstance(fragment, list)
            and fragment
            and all(isinstance(item, str) for item in fragment)
            for fragment in policy["argv_policy_fragments"]
        )
        or not isinstance(policy.get("forbidden_argv_tokens"), list)
        or not all(
            isinstance(item, str) and item for item in policy["forbidden_argv_tokens"]
        )
        or not isinstance(policy.get("required_mcp_tools"), list)
        or not policy["required_mcp_tools"]
        or not all(
            isinstance(item, str) and item for item in policy["required_mcp_tools"]
        )
        or "Skill" not in policy.get("tool_policy", [])
        or not set(policy["required_mcp_tools"]) <= set(policy["tool_policy"])
        or not any(_CONTEXT_BUNDLE_TOOL in item for item in policy["required_mcp_tools"])
    ):
        raise ReviewRunnerError("governed review command policy is invalid")
    forbidden_tools = {"bash", "edit", "write", "notebookedit"}
    if any(item.split("(", 1)[0].lower() in forbidden_tools for item in policy["tool_policy"]):
        raise ReviewRunnerError("governed review command policy permits mutation tools")
    if any(item in provider_argv for item in policy["forbidden_argv_tokens"]):
        raise ReviewRunnerError("governed review command contains a forbidden argument")
    for fragment in policy["argv_policy_fragments"]:
        width = len(fragment)
        if not any(
            list(provider_argv[index:index + width]) == fragment
            for index in range(len(provider_argv) - width + 1)
        ):
            raise ReviewRunnerError("governed review command policy is not enforced by argv")
    if list(provider_argv).count("{mcp_config}") != 1:
        raise ReviewRunnerError("governed review command does not consume held MCP config")
    network_policy = _validate_network_policy(
        identity.get("network_policy"), observed_at=observed_at,
    )
    context_service = _validate_context_service(identity.get("context_bundle_service"))
    if (
        context_service["context_service_endpoint"] not in network_policy["endpoints"]
        or context_service["broker_endpoint"] in network_policy["endpoints"]
    ):
        raise ReviewRunnerError("governed review context service egress is not admitted")
    if identity.get("environment_sha256") != _hash(dict(environment)):
        raise ReviewRunnerError("governed review environment identity mismatch")
    allowed_environment = {"HOME", "PATH", "USER", "LOGNAME", "LANG", "LC_ALL", "SSL_CERT_FILE"}
    if not set(environment) <= allowed_environment or any(
        word in key.upper() for key in environment for word in ("TOKEN", "SECRET", "PASSWORD", "KEY")
    ):
        raise ReviewRunnerError("governed review environment is not closed")
    artifacts = identity.get("artifacts")
    if not isinstance(artifacts, Mapping) or set(artifacts) != {
        "sandbox", "runtime", "executable", "skill_contract",
        "mcp_config", "credential", "execution_environment",
    }:
        raise ReviewRunnerError("governed review provider artifacts are invalid")
    health = identity.get("health")
    if not isinstance(health, Mapping) or set(health) != {
        "schema", "provider", "account_identity", "observed_at", "expires_at",
        "status", "evidence_hash",
    }:
        raise ReviewRunnerError("governed review provider health evidence is invalid")
    health_unsigned = dict(health)
    claimed_health = health_unsigned.pop("evidence_hash")
    if (
        health.get("schema") != "tgw-governed-review-provider-health/v1"
        or health.get("provider") != provider
        or health.get("account_identity") != identity.get("account_identity")
        or health.get("status") != "AUTHENTICATED"
        or claimed_health != _hash(health_unsigned)
    ):
        raise ReviewRunnerError("governed review provider health evidence is invalid")
    try:
        health_observed = datetime.fromisoformat(str(health["observed_at"]).replace("Z", "+00:00"))
        health_expires = datetime.fromisoformat(str(health["expires_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewRunnerError("governed review provider health time is invalid") from exc
    if not health_observed <= observed_at < health_expires:
        raise ReviewRunnerError("governed review provider health is stale")
    descriptors: list[int] = []
    try:
        sandbox_fd = _open_file_artifact("sandbox", artifacts["sandbox"])
        descriptors.append(sandbox_fd)
        runtime_fd, _ = _open_tree_artifact(
            "runtime", artifacts["runtime"], retain_contents=False,
            max_file_bytes=512 * 1024 * 1024,
        )
        descriptors.append(runtime_fd)
        executable_fd = _open_file_artifact("executable", artifacts["executable"])
        descriptors.append(executable_fd)
        if Path(provider_argv[0]).resolve() != Path(artifacts["executable"]["resolved_path"]):
            raise ReviewRunnerError("governed review command does not use the retained executable")
        skill_fd, skill_contents = _open_tree_artifact("skill contract", artifacts["skill_contract"])
        descriptors.append(skill_fd)
        _validate_skill_provenance(
            identity.get("skill_source_provenance"),
            artifacts["skill_contract"]["manifest_hash"],
        )
        skill_contract, skill_contract_hash = _render_skill_contract(skill_contents)
        mcp_fd = _open_file_artifact("MCP config", artifacts["mcp_config"])
        descriptors.append(mcp_fd)
        try:
            mcp = json.loads(os.pread(mcp_fd, 1024 * 1024 + 1, 0))
            servers = mcp["mcpServers"]
            endpoints = sorted(server["url"] for server in servers.values())
        except (AttributeError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReviewRunnerError("governed review MCP config is invalid") from exc
        if (
            not isinstance(servers, Mapping)
            or endpoints != sorted(policy["mcp_endpoints"])
            or any(
                not isinstance(server, Mapping)
                or set(server) != {"type", "url"}
                or server.get("type") != "sse"
                or server.get("url") != context_service["context_service_endpoint"]
                for server in servers.values()
            )
        ):
            raise ReviewRunnerError("governed review MCP config is not closure-bound")
        credential_fd = _open_secret_artifact("credential", artifacts["credential"])
        descriptors.append(credential_fd)
        execution_environment_fd = _open_file_artifact(
            "execution environment authority", artifacts["execution_environment"],
        )
        descriptors.append(execution_environment_fd)
        try:
            environment_authority = json.loads(os.pread(
                execution_environment_fd, 64 * 1024 + 1, 0,
            ))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReviewRunnerError("governed review environment authority is invalid") from exc
        if (
            not isinstance(environment_authority, Mapping)
            or set(environment_authority) != {
                "schema", "provider", "runtime_uid", "runtime_gid",
                "egress_key_id", "egress_public_key", "network_policy_hash",
            }
            or environment_authority.get("schema")
            != "tgw-governed-review-environment-authority/v1"
            or environment_authority.get("provider") != provider
            or environment_authority.get("runtime_uid") != sandbox_identity["uid"]
            or environment_authority.get("runtime_gid") != sandbox_identity["gid"]
            or artifacts["execution_environment"]["content_sha256"]
            != policy["context_bindings"]["execution_environment"]["hash"]
            or environment_authority.get("network_policy_hash")
            != network_policy["policy_hash"]
        ):
            raise ReviewRunnerError("governed review environment authority is invalid")
        _validate_network_policy(
            network_policy, observed_at=observed_at,
            expected_key_id=environment_authority["egress_key_id"],
            expected_public_key=environment_authority["egress_public_key"],
        )
        return {
            "sandbox_fd": sandbox_fd, "runtime_fd": runtime_fd,
            "execution_environment_fd": execution_environment_fd,
            "executable_fd": executable_fd,
            "skill_fd": skill_fd, "mcp_fd": mcp_fd, "credential_fd": credential_fd,
            "skill_contract": skill_contract,
            "skill_contract_hash": skill_contract_hash,
            "skill_manifest_hash": artifacts["skill_contract"]["manifest_hash"],
            "context_bindings": dict(policy["context_bindings"]),
            "sandbox_layout": dict(layout),
            "sandbox_identity": dict(sandbox_identity),
        }
    except Exception:
        for descriptor in descriptors:
            os.close(descriptor)
        raise


class HTTPReviewEvidenceSink:
    """Bound non-test X publisher with immediate pinned-byte readback."""

    def __init__(self, descriptor: Mapping[str, Any]) -> None:
        descriptor = _validate_evidence_sink_descriptor(descriptor)
        endpoint = descriptor["endpoint"]
        credential_env = descriptor["credential_env"]
        credential = os.environ.get(credential_env)
        if not credential:
            raise ReviewRunnerError("governed review evidence sink credential is unavailable")
        self.endpoint = endpoint.rstrip("/")
        self.timeout = float(descriptor["timeout_seconds"])
        self.authorization = "Bearer " + credential
        self.sink_ref = descriptor["sink_ref"]
        self.descriptor_hash = descriptor["descriptor_hash"]

    def _request(self, request: urllib_request.Request) -> dict[str, Any]:
        request.add_header("Authorization", self.authorization)
        try:
            with urllib_request.urlopen(request, timeout=self.timeout) as response:
                body = response.read(1024 * 1024 + 1)
        except (OSError, urllib_error.URLError) as exc:
            raise ReviewRunnerError("governed review evidence sink request failed") from exc
        if len(body) > 1024 * 1024:
            raise ReviewRunnerError("governed review evidence sink response exceeds its bound")
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReviewRunnerError("governed review evidence sink response is invalid") from exc
        if not isinstance(value, dict):
            raise ReviewRunnerError("governed review evidence sink response is invalid")
        return value

    def publish(self, execution: Mapping[str, Any]) -> dict[str, Any]:
        body = _canonical(execution)
        return self._request(urllib_request.Request(
            self.endpoint + "/executions", data=body, method="POST",
            headers={"Content-Type": "application/json"},
        ))

    def publish_artifact(self, artifact_ref: str, value: Mapping[str, Any]) -> dict[str, Any]:
        body = _canonical({"ref": artifact_ref, "value": value})
        pointer = self._request(urllib_request.Request(
            self.endpoint + "/artifacts", data=body, method="POST",
            headers={"Content-Type": "application/json"},
        ))
        expected = {"ref": artifact_ref, "content_sha256": _hash(value)}
        if pointer != expected:
            raise ReviewRunnerError("governed review evidence sink artifact ack is invalid")
        if self.read_artifact(pointer) != value:
            raise ReviewRunnerError("governed review evidence sink artifact readback differs")
        return pointer

    def read_artifact(self, pointer: Mapping[str, Any]) -> dict[str, Any]:
        if (
            not isinstance(pointer, Mapping)
            or set(pointer) != {"ref", "content_sha256"}
            or not isinstance(pointer.get("ref"), str)
            or not isinstance(pointer.get("content_sha256"), str)
        ):
            raise ReviewRunnerError("governed review evidence pointer is invalid")
        query = urllib_parse.urlencode({"ref": pointer["ref"]})
        value = self._request(urllib_request.Request(
            self.endpoint + "/artifacts?" + query, method="GET",
        ))
        if _hash(value) != pointer["content_sha256"]:
            raise ReviewRunnerError("governed review evidence readback hash differs")
        return value

    def read(self, publication: Mapping[str, Any]) -> dict[str, Any]:
        artifact_ref = publication.get("artifact_ref")
        if not isinstance(artifact_ref, str) or not artifact_ref:
            raise ReviewRunnerError("governed review publication reference is invalid")
        query = urllib_parse.urlencode({"ref": artifact_ref})
        return self._request(urllib_request.Request(
            self.endpoint + "/artifacts?" + query, method="GET",
        ))


class HTTPContextBundleClient:
    """Read back the exact signed context-tool run from its registered service."""

    def __init__(self, descriptor: Mapping[str, Any]) -> None:
        descriptor = _validate_context_service(descriptor)
        credential = os.environ.get(descriptor["credential_env"])
        if not credential:
            raise ReviewRunnerError("governed review context service credential is unavailable")
        self.endpoint = descriptor["endpoint"].rstrip("/")
        self.authorization = "Bearer " + credential

    def read(self, challenge: str) -> dict[str, Any]:
        if not challenge or len(challenge) != 64:
            raise ReviewRunnerError("governed review context challenge is invalid")
        request = urllib_request.Request(
            self.endpoint + "/v1/review-context-challenges/"
            + urllib_parse.quote(challenge, safe="") + "/bundle",
            method="GET",
        )
        request.add_header("Authorization", self.authorization)
        try:
            with urllib_request.urlopen(request, timeout=10) as response:
                body = response.read(_MAX_CONTEXT_BUNDLE_BYTES + 1)
        except (OSError, urllib_error.URLError) as exc:
            raise ReviewRunnerError("governed review context readback failed") from exc
        if len(body) > _MAX_CONTEXT_BUNDLE_BYTES:
            raise ReviewRunnerError("governed review context readback exceeds its bound")
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReviewRunnerError("governed review context readback is invalid") from exc
        if not isinstance(value, dict):
            raise ReviewRunnerError("governed review context readback is invalid")
        return value


def execute_request(request_path: Path) -> dict[str, Any]:
    """Execute one production request through the provider-neutral path."""

    named_before = _command_identity(request_path)
    descriptor = os.open(request_path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        request_stat = os.fstat(descriptor)
        request_identity = _stat_identity(request_stat)
        if (
            named_before.get("is_symlink") is not False
            or any(
                named_before.get(field) != request_identity[field]
                for field in (
                    "device", "inode", "uid", "gid", "mode", "size",
                    "mtime_ns",
                )
            )
            or named_before.get("nlink") != request_stat.st_nlink
            or not stat.S_ISREG(request_stat.st_mode)
            or request_stat.st_uid != 0
            or stat.S_IMODE(request_stat.st_mode) & 0o022
            or request_stat.st_nlink != 1
        ):
            raise ReviewRunnerError("governed review request is not root-protected")
        body = b""
        while len(body) <= 1024 * 1024:
            block = os.read(descriptor, min(64 * 1024, 1024 * 1024 + 1 - len(body)))
            if not block:
                break
            body += block
        if len(body) > 1024 * 1024:
            raise ReviewRunnerError("governed review request exceeds its bound")
        value = json.loads(body)
        if _stat_identity(os.fstat(descriptor)) != request_identity:
            raise ReviewRunnerError("governed review request changed while held")
        required = {
            "schema", "handoff", "snapshot", "source_commit", "source_tree",
            "plan_commit", "provider", "provider_identity", "provider_argv",
            "environment", "trusted_uid", "trusted_gid", "timeout_seconds",
            "output_limit", "evidence_sink", "review_packet",
            "resource_service_catalog",
        }
        if (
            not isinstance(value, Mapping)
            or set(value) != required
            or value.get("schema") != "tgw-governed-review-request/v1"
        ):
            raise ReviewRunnerError("governed review request contract is invalid")
        sink = HTTPReviewEvidenceSink(value["evidence_sink"])
        context_client = HTTPContextBundleClient(
            value["provider_identity"]["context_bundle_service"],
        )
        execution = run_governed_review(
            value["handoff"], snapshot=Path(value["snapshot"]),
            source_commit=value["source_commit"], source_tree=value["source_tree"],
            plan_commit=value["plan_commit"], provider=value["provider"],
            provider_identity=value["provider_identity"],
            provider_argv=value["provider_argv"], environment=value["environment"],
            trusted_uid=value["trusted_uid"], trusted_gid=value["trusted_gid"],
            evidence_sink_descriptor=value["evidence_sink"],
            publish_execution=sink.publish, read_execution=sink.read,
            read_context_bundle=context_client.read,
            timeout_seconds=value["timeout_seconds"], output_limit=value["output_limit"],
        )
        if (
            _stat_identity(os.fstat(descriptor)) != request_identity
            or _command_identity(request_path) != named_before
            or _fd_hash(descriptor)
            != _SHA256_PREFIX + hashlib.sha256(body).hexdigest()
        ):
            raise ReviewRunnerError("governed review request changed during composition")
        from tgw.execute_candidate_review import finalize_and_publish_governed_review

        result = finalize_and_publish_governed_review(
            value["review_packet"], execution, value["handoff"],
            value["resource_service_catalog"], sink,
        )
        if (
            _stat_identity(os.fstat(descriptor)) != request_identity
            or _command_identity(request_path) != named_before
            or _fd_hash(descriptor)
            != _SHA256_PREFIX + hashlib.sha256(body).hexdigest()
        ):
            raise ReviewRunnerError("governed review request changed during composition")
        return result
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewRunnerError("governed review request is invalid") from exc
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-governed-review")
    parser.add_argument("--request", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        execution = execute_request(arguments.request)
    except (ReviewRunnerError, OSError, ValueError) as exc:
        print(json.dumps({"status": "HOLD", "reason": str(exc)}))
        return 2
    print(json.dumps({
        "status": execution["validation"]["status"],
        "execution_hash": execution["execution"]["execution_hash"],
        "provider": execution["execution"]["provider"],
        "verdict": execution["execution"]["review"]["verdict"],
        "result_hash": execution["result"]["result_hash"],
        "evidence_bundle_hash": execution["evidence_bundle"]["bundle_hash"],
    }, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
