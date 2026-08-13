"""Bounded, zero-write host-state observation for the A3 integration path.

Production dispatch remains unavailable until an exact Plan authority, a real-sshd
parity authority, dedicated SSH material, immutable evidence/token roots, and a
fresh one-attempt grant are mounted together.  The remote helper is stdlib-only and
observes identities; it never invokes Nix or mutates the repository.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import stat
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from tgw.a3_observation_authority import (
    DurableObservationToken,
    ObservationAlreadyConsumed,
    ObservationTokenPersistenceAmbiguous,
)
from tgw.a3_preintegration_observation import (
    ImmutableEvidenceStore as _AtomicEvidenceStore,
)
from tgw.a3_preintegration_observation import (
    ObservationError,
    ObservationHold,
    _bounded_stream,
    _group_empty_or_kill,
    _held_regular,
    _inode_identity,
    _post_reap_group_state,
    _run_held_bounded,
    _sealed,
    canonical,
    digest,
)
from tgw.plan_solver import validate_solution_integrity

EFFECT_KIND = "tgw-prod-a3-host-state-observation"
HANDLER_ID = EFFECT_KIND + "@1"
REQUEST_SCHEMA = "tgw-prod-a3-host-state-observation-request/v1"
RECEIPT_SCHEMA = "tgw-prod-a3-host-state-observation-receipt/v1"
TERMINAL_SCHEMA = "tgw-prod-a3-host-state-observation-terminal/v1"
RESULT_SCHEMA = "tgw-prod-a3-host-state-observation-result/v1"
PERSISTENCE_CONTEXT_SCHEMA = "tgw-prod-a3-host-state-observation-persistence-context/v1"
PLAN_AUTHORITY_SCHEMA = "tgw-prod-a3-host-state-plan-authority/v1"
PARITY_SCHEMA = "tgw-prod-a3-host-state-sshd-parity/v1"
GRANT_SCHEMA = "tgw-prod-a3-host-state-observation-grant/v1"
GRANT_OBSERVATION_SCHEMA = "tgw-prod-a3-host-state-mounted-grant-observation/v1"
DEPENDENCY_SCHEMA = "tgw-prod-a3-host-state-observation-dependency/v1"
COMPOSITION_SCHEMA = "tgw-prod-a3-host-state-observation-composition/v1"

_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
_STORE = re.compile(r"^/nix/store/[0-9a-z]{32}-[A-Za-z0-9+._?=-]+$")
_OPERATION = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_PLAN_SEAL = object()
_PARITY_SEAL = object()
_COMPOSITION_SEAL = object()
_GRANT_SEAL = object()
_PARITY_EVIDENCE_ROLES = {
    "sshd_executable",
    "sshd_config",
    "host_key_public",
    "correct_key_log",
    "wrong_key_log",
    "default_key_log",
    "agent_rejection_log",
    "ambient_config_rejection_log",
    "framing_log",
    "process_group_log",
}
_LOCAL_PROCESS_ENVIRONMENT = {
    "HOME": "/nonexistent",
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
    "TZ": "UTC",
}


def _local_process_environment() -> dict[str, str]:
    """Return the exact environment admitted for local SSH/tool processes."""
    return dict(_LOCAL_PROCESS_ENVIRONMENT)


class HostStateError(ObservationError):
    pass


class HostStatePersistenceAmbiguous(HostStateError):
    def __init__(
        self,
        terminal_value: Mapping[str, Any],
        context: Mapping[str, Any],
    ):
        super().__init__("host-state evidence persistence is ambiguous")
        self.terminal = dict(terminal_value)
        self.context = dict(context)


class HostStateDispatchAmbiguous(HostStateError):
    pass


def _exact(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise HostStateError(f"{label} fields are not exact")
    return value


def _hash(value: object) -> str:
    return digest(canonical(value))


def _strict_positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise HostStateError(f"{label} is not a positive integer")
    return value


def _validate_inode_identity_list(value: Any, label: str) -> list[int]:
    if not isinstance(value, list) or len(value) != 9 or any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in value) or value[5] < 1:
        raise HostStateError(f"{label} inode identity is invalid")
    return value


def _parse_time(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise HostStateError(f"{label} is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise HostStateError(f"{label} has no timezone")
    return parsed


def _protected_file(path: Path, expected_sha256: str, *, uid: int = 0, mode: int = 0o444) -> tuple[int, bytes]:
    absolute = path.absolute()
    for ancestor in (absolute.parent, *absolute.parents):
        item = os.lstat(ancestor)
        if not stat.S_ISDIR(item.st_mode) or stat.S_ISLNK(item.st_mode) or item.st_uid != 0 or stat.S_IMODE(item.st_mode) & 0o022:
            raise HostStateError("protected authority ancestor is mutable")
        if ancestor == Path("/"):
            break
    fd, raw = _held_regular(path, expected_sha256)
    st = os.fstat(fd)
    if st.st_uid != uid or stat.S_IMODE(st.st_mode) != mode or st.st_nlink != 1:
        os.close(fd)
        raise HostStateError("protected authority metadata differs")
    return fd, raw


def _reject_symlink_ancestors(path: Path) -> None:
    absolute = path.absolute()
    for ancestor in (*reversed(absolute.parents), absolute):
        if ancestor == Path("/"):
            continue
        if ancestor.exists() or ancestor.is_symlink():
            item = os.lstat(ancestor)
            if stat.S_ISLNK(item.st_mode):
                raise HostStateError(f"symlink path component is not admitted: {ancestor}")


def _validate_approval(value: Any) -> dict[str, Any]:
    fields = {
        "schema",
        "approval_sha256",
        "approved_commit",
        "approved_tree",
        "approver",
        "authority",
        "decision",
        "delta",
        "instruction",
        "observed_at",
        "packet",
        "proposal_solution_sha256",
        "source",
        "target",
    }
    approval = dict(_exact(value, fields, "Plan approval"))
    if approval["schema"] != "tgw-plan-approval-receipt/v1" or approval["decision"] != "APPROVE":
        raise HostStateError("Plan approval is not affirmative")
    authority = _exact(
        approval["authority"],
        {"authorized_effects", "excluded_effects", "ssh_read_only_requires_separate_request_bound_grant"},
        "Plan approval authority",
    )
    if authority["ssh_read_only_requires_separate_request_bound_grant"] is not True:
        raise HostStateError("Plan approval does not preserve the SSH grant boundary")
    if not isinstance(authority["authorized_effects"], list) or not isinstance(authority["excluded_effects"], list):
        raise HostStateError("Plan approval effect sets are invalid")
    claimed = approval.pop("approval_sha256")
    if claimed != _hash(approval):
        raise HostStateError("Plan approval hash differs")
    approval["approval_sha256"] = claimed
    return approval


def validate_plan_authority(value: Any) -> dict[str, Any]:
    fields = {"schema", "approval", "solution", "authority_sha256"}
    authority = dict(_exact(value, fields, "Plan authority"))
    if authority["schema"] != PLAN_AUTHORITY_SCHEMA:
        raise HostStateError("Plan authority schema differs")
    approval = _validate_approval(authority["approval"])
    solution = dict(authority["solution"])
    validate_solution_integrity(solution, current_plan_commit=approval["approved_commit"])
    if (
        solution.get("root") != {"id": "A3O02", "profile": "production", "minimum_state": "operationally_verified"}
        or not solution.get("complete")
        or not solution.get("conformance_verified")
        or not solution.get("dispatchable")
        or solution.get("unresolved")
        or "operations.tgw-prod-host-state-observation@1" not in solution.get("selected_capabilities", [])
    ):
        raise HostStateError("Plan authority is not an executable A3O02 solution")
    claimed = authority.pop("authority_sha256")
    if claimed != _hash(authority):
        raise HostStateError("Plan authority hash differs")
    authority["authority_sha256"] = claimed
    return authority


class MountedHostStatePlanAuthority:
    __slots__ = ("authority", "fd", "identity", "sha256", "path")

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise TypeError("MountedHostStatePlanAuthority is sealed")

    def __init__(self, path: Path, expected_sha256: str, *, _token: object) -> None:
        if _token is not _PLAN_SEAL or path.name != expected_sha256.removeprefix("sha256:") + ".json":
            raise HostStateError("Plan authority locator is not content addressed")
        fd, raw = _protected_file(path, expected_sha256)
        try:
            authority = validate_plan_authority(json.loads(raw))
        except Exception:
            os.close(fd)
            raise
        self.authority = authority
        self.fd = fd
        self.identity = _inode_identity(os.fstat(fd))
        self.sha256 = expected_sha256
        self.path = path


def load_plan_authority(path: Path, expected_sha256: str) -> MountedHostStatePlanAuthority:
    return MountedHostStatePlanAuthority(path, expected_sha256, _token=_PLAN_SEAL)


def validate_sshd_parity(
    value: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    fields = {
        "schema",
        "status",
        "ssh_sha256",
        "identity_public",
        "known_hosts_sha256",
        "identity_public_sha256",
        "sshd_sha256",
        "sshd_config_sha256",
        "host_key_public_sha256",
        "observed_at",
        "correct_key",
        "wrong_key_rejected",
        "default_key_rejected",
        "agent_rejected",
        "ambient_config_rejected",
        "framing_verified",
        "process_group_verified",
        "ssh_argv_policy",
        "local_process_environment",
        "evidence",
        "receipt_sha256",
    }
    receipt = dict(_exact(value, fields, "sshd parity receipt"))
    if receipt["schema"] != PARITY_SCHEMA or receipt["status"] != "PASS":
        raise HostStateError("sshd parity is not PASS")
    for field in (
        "correct_key",
        "wrong_key_rejected",
        "default_key_rejected",
        "agent_rejected",
        "ambient_config_rejected",
        "framing_verified",
        "process_group_verified",
    ):
        if receipt[field] is not True:
            raise HostStateError(f"sshd parity {field} is not proved")
    for field in (
        "ssh_sha256",
        "known_hosts_sha256",
        "identity_public_sha256",
        "sshd_sha256",
        "sshd_config_sha256",
        "host_key_public_sha256",
    ):
        if not isinstance(receipt[field], str) or not _SHA.fullmatch(receipt[field]):
            raise HostStateError(f"sshd parity {field} is invalid")
    if not isinstance(receipt["identity_public"], str) or len(receipt["identity_public"].split()) < 2:
        raise HostStateError("sshd parity identity is invalid")
    if not isinstance(receipt["ssh_argv_policy"], list) or not receipt["ssh_argv_policy"] or any(not isinstance(item, str) or not item for item in receipt["ssh_argv_policy"]):
        raise HostStateError("sshd parity SSH argv policy is invalid")
    if receipt["local_process_environment"] != _local_process_environment():
        raise HostStateError("sshd parity local process environment differs")
    if digest((receipt["identity_public"] + "\n").encode()) != receipt["identity_public_sha256"]:
        raise HostStateError("sshd parity public identity hash differs")
    observed_at = _parse_time(receipt["observed_at"], "sshd parity observed_at")
    if now is not None:
        if observed_at > now + timedelta(seconds=5):
            raise HostStateError("sshd parity is future-dated")
        if now - observed_at > timedelta(hours=24):
            raise HostStateError("sshd parity is stale")
    evidence = _exact(receipt["evidence"], _PARITY_EVIDENCE_ROLES, "sshd parity evidence")
    evidence_paths: set[str] = set()
    for item in evidence.values():
        ref = _exact(item, {"path", "sha256", "size"}, "sshd parity evidence ref")
        if (
            not isinstance(ref["path"], str)
            or not ref["path"].startswith("/")
            or not _SHA.fullmatch(str(ref["sha256"]))
            or isinstance(ref["size"], bool)
            or not isinstance(ref["size"], int)
            or ref["size"] <= 0
        ):
            raise HostStateError("sshd parity evidence ref is invalid")
        evidence_paths.add(str(ref["path"]))
    if len(evidence_paths) != len(_PARITY_EVIDENCE_ROLES):
        raise HostStateError("sshd parity evidence paths are not distinct")
    if (
        evidence["sshd_executable"]["sha256"] != receipt["sshd_sha256"]
        or evidence["sshd_config"]["sha256"] != receipt["sshd_config_sha256"]
        or evidence["host_key_public"]["sha256"] != receipt["host_key_public_sha256"]
    ):
        raise HostStateError("sshd parity artifact identities differ")
    claimed = receipt.pop("receipt_sha256")
    if claimed != _hash(receipt):
        raise HostStateError("sshd parity receipt hash differs")
    receipt["receipt_sha256"] = claimed
    return receipt


class MountedSshdParityAuthority:
    __slots__ = ("receipt", "fd", "identity", "sha256", "path", "evidence_fds", "evidence_identities")

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise TypeError("MountedSshdParityAuthority is sealed")

    def __init__(self, path: Path, expected_sha256: str, *, _token: object) -> None:
        if _token is not _PARITY_SEAL or path.name != expected_sha256.removeprefix("sha256:") + ".json":
            raise HostStateError("sshd parity locator is not content addressed")
        fd, raw = _protected_file(path, expected_sha256)
        try:
            receipt = validate_sshd_parity(json.loads(raw))
            evidence_fds: dict[str, int] = {}
            evidence_identities: dict[str, tuple[int, ...]] = {}
            for role, ref in receipt["evidence"].items():
                evidence_fd, evidence_raw = _protected_file(Path(ref["path"]), ref["sha256"])
                if len(evidence_raw) != ref["size"]:
                    os.close(evidence_fd)
                    raise HostStateError("sshd parity evidence size differs")
                evidence_fds[role] = evidence_fd
                evidence_identities[role] = _inode_identity(os.fstat(evidence_fd))
        except Exception:
            os.close(fd)
            for evidence_fd in locals().get("evidence_fds", {}).values():
                os.close(evidence_fd)
            raise
        self.receipt = receipt
        self.fd = fd
        self.identity = _inode_identity(os.fstat(fd))
        self.sha256 = expected_sha256
        self.path = path
        self.evidence_fds = dict(evidence_fds)
        self.evidence_identities = dict(evidence_identities)


def load_sshd_parity_authority(path: Path, expected_sha256: str) -> MountedSshdParityAuthority:
    return MountedSshdParityAuthority(path, expected_sha256, _token=_PARITY_SEAL)


def validate_request(
    value: Any,
    *,
    now: datetime | None = None,
    plan_authority: Mapping[str, Any] | MountedHostStatePlanAuthority | None = None,
    parity_authority: Mapping[str, Any] | MountedSshdParityAuthority | None = None,
    allow_fixture: bool = False,
) -> dict[str, Any]:
    fields = {
        "schema",
        "operation_id",
        "plan",
        "target",
        "transport",
        "prerequisites",
        "bounds",
        "freshness",
        "policy",
        "request_sha256",
    }
    request = dict(_exact(value, fields, "host-state request"))
    if request["schema"] != REQUEST_SCHEMA or not isinstance(request["operation_id"], str) or not _OPERATION.fullmatch(request["operation_id"]):
        raise HostStateError("host-state request identity is invalid")
    plan = _exact(request["plan"], {"commit", "solution_sha256", "closure_sha256", "approval_sha256", "authority_sha256"}, "host-state Plan")
    if not all(isinstance(plan[key], str) and (_SHA.fullmatch(plan[key]) if key != "commit" else re.fullmatch(r"[0-9a-f]{40}", plan[key])) for key in plan):
        raise HostStateError("host-state Plan identities are invalid")
    mounted_plan = plan_authority.authority if type(plan_authority) is MountedHostStatePlanAuthority else plan_authority
    if mounted_plan is None and not allow_fixture:
        raise HostStateError("production host-state request has no mounted Plan authority")
    if mounted_plan is not None:
        fixed = validate_plan_authority(mounted_plan)
        solution = fixed["solution"]
        expected = {
            "commit": fixed["approval"]["approved_commit"],
            "solution_sha256": solution["solution_hash"],
            "closure_sha256": solution["closure_hash"],
            "approval_sha256": fixed["approval"]["approval_sha256"],
            "authority_sha256": fixed["authority_sha256"],
        }
        if dict(plan) != expected:
            raise HostStateError("host-state Plan request differs from mounted authority")
    target = {
        "host": "tgw-prod",
        "user": "codex",
        "port": 22,
        "system": "x86_64-linux",
        "remote_python": "/usr/bin/python3",
        "remote_git": "/usr/bin/git",
        "repository": "/home/db/tgw-flake",
        "expected_branch": "main",
    }
    if request["target"] != target:
        raise HostStateError("host-state target is not exact")
    transport_fields = {
        "ssh_sha256",
        "ssh_keygen_sha256",
        "known_hosts_sha256",
        "identity_sha256",
        "identity_public",
        "identity_public_sha256",
        "helper_sha256",
    }
    transport = _exact(request["transport"], transport_fields, "host-state transport")
    if any(not isinstance(transport[key], str) or not _SHA.fullmatch(transport[key]) for key in transport if key != "identity_public"):
        raise HostStateError("host-state transport identity is invalid")
    if not isinstance(transport["identity_public"], str) or len(transport["identity_public"].split()) < 2:
        raise HostStateError("host-state public identity is invalid")
    if digest((transport["identity_public"] + "\n").encode()) != transport["identity_public_sha256"]:
        raise HostStateError("host-state public identity artifact hash differs")
    prerequisites = _exact(request["prerequisites"], {"sshd_parity_sha256", "sshd_parity_receipt_sha256"}, "host-state prerequisites")
    if any(not isinstance(item, str) or not _SHA.fullmatch(item) for item in prerequisites.values()):
        raise HostStateError("host-state prerequisite identity is invalid")
    mounted_parity = parity_authority.receipt if type(parity_authority) is MountedSshdParityAuthority else parity_authority
    if mounted_parity is None and not allow_fixture:
        raise HostStateError("production host-state request has no mounted sshd parity authority")
    if mounted_parity is not None:
        parity = validate_sshd_parity(mounted_parity, now=now)
        expected = {
            "sshd_parity_sha256": parity_authority.sha256 if type(parity_authority) is MountedSshdParityAuthority else digest(canonical(parity)),
            "sshd_parity_receipt_sha256": parity["receipt_sha256"],
        }
        if dict(prerequisites) != expected:
            raise HostStateError("host-state sshd parity binding differs")
        if (
            parity["ssh_sha256"] != transport["ssh_sha256"]
            or parity["known_hosts_sha256"] != transport["known_hosts_sha256"]
            or parity["identity_public"] != transport["identity_public"]
            or parity["identity_public_sha256"] != transport["identity_public_sha256"]
            or parity["ssh_argv_policy"] != _ssh_argv_policy(request)
        ):
            raise HostStateError("host-state transport differs from sshd parity")
    bounds = _exact(request["bounds"], {"timeout_seconds", "max_output_bytes", "max_diagnostic_bytes"}, "host-state bounds")
    for name, bound in bounds.items():
        _strict_positive_int(bound, name)
    if bounds["timeout_seconds"] > 120 or bounds["max_output_bytes"] > 1_048_576 or bounds["max_diagnostic_bytes"] > 262_144:
        raise HostStateError("host-state bounds exceed policy")
    if request["policy"] != {
        "read_only": True,
        "remote_write": False,
        "repository_write": False,
        "nix": False,
        "network_beyond_ssh": False,
        "platform_bootstrap_grant_consumption": False,
    }:
        raise HostStateError("host-state zero-effect policy differs")
    freshness = _exact(request["freshness"], {"issued_at", "expires_at"}, "host-state freshness")
    issued = _parse_time(freshness["issued_at"], "issued_at")
    expires = _parse_time(freshness["expires_at"], "expires_at")
    if expires <= issued or expires - issued > timedelta(minutes=10) or (now is not None and not issued <= now < expires):
        raise HostStateError("host-state request is stale")
    claimed = request.pop("request_sha256")
    if claimed != _hash(request):
        raise HostStateError("host-state request hash differs")
    request["request_sha256"] = claimed
    return request


def _trusted_executable(path: Path, *, trusted_uid: int) -> tuple[int, bytes, dict[str, Any]]:
    resolved = path.resolve(strict=True)
    for ancestor in (resolved.parent, *resolved.parents):
        item = os.lstat(ancestor)
        if not stat.S_ISDIR(item.st_mode) or stat.S_ISLNK(item.st_mode) or item.st_uid != 0 or stat.S_IMODE(item.st_mode) & 0o022:
            raise HostStateError("remote executable ancestor is mutable")
        if ancestor == Path("/"):
            break
    fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != trusted_uid or not st.st_mode & 0o111 or st.st_nlink != 1:
            raise HostStateError("remote executable metadata differs")
        raw = bytearray()
        while True:
            block = os.read(fd, 1 << 20)
            if not block:
                break
            raw.extend(block)
        os.lseek(fd, 0, os.SEEK_SET)
        identity = {
            "path": str(path),
            "realpath": str(resolved),
            "sha256": digest(bytes(raw)),
            "size": st.st_size,
            "uid": st.st_uid,
            "gid": st.st_gid,
            "mode": stat.S_IMODE(st.st_mode),
            "nlink": st.st_nlink,
            "dev": st.st_dev,
            "ino": st.st_ino,
        }
        return fd, bytes(raw), identity
    except Exception:
        os.close(fd)
        raise


def _symlink_observation(path: Path, *, require_target: bool = True) -> tuple[os.stat_result, str, dict[str, int]]:
    before = os.lstat(path)
    if not stat.S_ISLNK(before.st_mode):
        raise ObservationHold(f"{path} is not a symlink")
    target = os.readlink(path)
    if not _STORE.fullmatch(target):
        raise ObservationHold(f"{path} target is not a Nix store path")
    after = os.lstat(path)
    if _inode_identity(before) != _inode_identity(after) or os.readlink(path) != target:
        raise HostStateError(f"{path} changed while observed")
    if not require_target:
        return before, target, {"dev": 0, "ino": 0, "uid": 0, "gid": 0, "mode": 0o555}
    try:
        target_st = os.stat(target, follow_symlinks=True)
    except FileNotFoundError as exc:
        raise ObservationHold(f"{path} target is absent") from exc
    if not stat.S_ISDIR(target_st.st_mode) or target_st.st_uid != 0 or stat.S_IMODE(target_st.st_mode) & 0o022:
        raise ObservationHold(f"{path} target is not an immutable root-owned store directory")
    return (
        before,
        target,
        {
            "dev": target_st.st_dev,
            "ino": target_st.st_ino,
            "uid": target_st.st_uid,
            "gid": target_st.st_gid,
            "mode": stat.S_IMODE(target_st.st_mode),
        },
    )


def _read_branch(repository: Path, expected: str, *, logical_path: str | None = None) -> tuple[dict[str, Any], tuple[int, ...], tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    repo_st = os.lstat(repository)
    git_st = os.lstat(repository / ".git")
    if not stat.S_ISDIR(repo_st.st_mode) or stat.S_ISLNK(repo_st.st_mode) or not stat.S_ISDIR(git_st.st_mode) or stat.S_ISLNK(git_st.st_mode):
        raise ObservationHold("production repository or .git is not a direct directory")
    head_path = repository / ".git/HEAD"
    ref_path = repository / ".git/refs/heads" / expected
    try:
        fd = os.open(head_path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError as exc:
        raise ObservationHold("production repository HEAD is absent") from exc
    ref_fd = -1
    try:
        try:
            ref_fd = os.open(ref_path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError as exc:
            raise ObservationHold("production repository main ref is absent") from exc
        head_st = os.fstat(fd)
        raw = os.read(fd, 4097)
        if not stat.S_ISREG(head_st.st_mode) or head_st.st_nlink != 1 or len(raw) > 4096:
            raise HostStateError("production repository HEAD is invalid")
        text = raw.decode("ascii", errors="strict").strip()
        prefix = "ref: refs/heads/"
        if not text.startswith(prefix) or text[len(prefix) :] != expected:
            raise ObservationHold("production repository branch differs from approved main")
        if _inode_identity(os.fstat(fd)) != _inode_identity(head_st) or _inode_identity(os.stat(head_path, follow_symlinks=False)) != _inode_identity(head_st):
            raise HostStateError("production repository HEAD changed")
        ref_st = os.fstat(ref_fd)
        ref_raw = os.read(ref_fd, 42)
        if (
            not stat.S_ISREG(ref_st.st_mode)
            or ref_st.st_nlink != 1
            or not re.fullmatch(rb"[0-9a-f]{40}\n", ref_raw)
            or _inode_identity(os.fstat(ref_fd)) != _inode_identity(ref_st)
            or _inode_identity(os.stat(ref_path, follow_symlinks=False)) != _inode_identity(ref_st)
        ):
            raise HostStateError("production repository branch ref is invalid")
        return (
            {
                "path": logical_path or str(repository),
                "branch": expected,
                "uid": repo_st.st_uid,
                "gid": repo_st.st_gid,
                "mode": stat.S_IMODE(repo_st.st_mode),
                "dev": repo_st.st_dev,
                "ino": repo_st.st_ino,
                "head_sha256": digest(raw),
                "ref_sha256": digest(ref_raw),
                "commit": ref_raw.decode("ascii").strip(),
            },
            _inode_identity(repo_st),
            _inode_identity(git_st),
            _inode_identity(head_st),
            _inode_identity(ref_st),
        )
    finally:
        if ref_fd >= 0:
            os.close(ref_fd)
        os.close(fd)


def _tool_version(fd: int, label: str) -> tuple[str, str, str]:
    rc, stdout, stderr = _run_held_bounded(
        [f"/proc/{os.getpid()}/fd/{fd}", "--version"],
        pass_fds=(fd,),
        timeout=10,
        limit=65536,
        env=_local_process_environment(),
    )
    raw = stdout or stderr
    if rc or not raw or len(raw) > 65536:
        raise HostStateError(f"{label} version observation failed")
    value = raw.decode("utf-8", errors="strict").strip()
    return value, digest(raw), base64.b64encode(raw).decode("ascii")


def observe_host_state(
    request_value: Mapping[str, Any],
    *,
    current_system: Path = Path("/run/current-system"),
    system_profile: Path = Path("/nix/var/nix/profiles/system"),
    repository: Path = Path("/home/db/tgw-flake"),
    python_path: Path | None = None,
    git_path: Path | None = None,
    trusted_uid: int = 0,
    allow_fixture: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    observed_now = now or datetime.now(timezone.utc)
    request = validate_request(request_value, now=observed_now, allow_fixture=allow_fixture)
    current_st, current, current_identity = _symlink_observation(current_system, require_target=not allow_fixture)
    profile_st, profile, profile_identity = _symlink_observation(system_profile, require_target=not allow_fixture)
    if current != profile or current_identity != profile_identity:
        raise ObservationHold("current-system and system profile CAS differ")
    expected_python = python_path or Path(request["target"]["remote_python"])
    expected_git = git_path or Path(request["target"]["remote_git"])
    python_fd, _python_raw, python_identity = _trusted_executable(expected_python, trusted_uid=trusted_uid)
    git_fd, _git_raw, git_identity = _trusted_executable(expected_git, trusted_uid=trusted_uid)
    try:
        proc_self = os.stat("/proc/self/exe")
        if (proc_self.st_dev, proc_self.st_ino) != (os.fstat(python_fd).st_dev, os.fstat(python_fd).st_ino):
            raise ObservationHold("remote helper interpreter differs from observed Python")
        python_version, python_version_sha, python_version_b64 = _tool_version(python_fd, "Python")
        git_version, git_version_sha, git_version_b64 = _tool_version(git_fd, "Git")
        python_identity.update({"version": python_version, "version_sha256": python_version_sha, "version_b64": python_version_b64})
        git_identity.update({"version": git_version, "version_sha256": git_version_sha, "version_b64": git_version_b64})
        repo, repo_before, git_before, head_before, ref_before = _read_branch(
            repository,
            request["target"]["expected_branch"],
            logical_path=request["target"]["repository"],
        )
        if (
            _inode_identity(os.lstat(repository)) != repo_before
            or _inode_identity(os.lstat(repository / ".git")) != git_before
            or _inode_identity(os.stat(repository / ".git/HEAD", follow_symlinks=False)) != head_before
            or _inode_identity(
                os.stat(
                    repository / ".git/refs/heads" / request["target"]["expected_branch"],
                    follow_symlinks=False,
                )
            )
            != ref_before
        ):
            raise HostStateError("production repository identity changed")
        if _inode_identity(os.lstat(current_system)) != _inode_identity(current_st) or _inode_identity(os.lstat(system_profile)) != _inode_identity(profile_st):
            raise HostStateError("system CAS links changed")
        if (
            _inode_identity(os.fstat(python_fd))[:5] != _inode_identity(os.stat(expected_python.resolve(strict=True), follow_symlinks=False))[:5]
            or _inode_identity(os.fstat(git_fd))[:5] != _inode_identity(os.stat(expected_git.resolve(strict=True), follow_symlinks=False))[:5]
        ):
            raise HostStateError("remote tool named identity changed")
        value = {
            "schema": RECEIPT_SCHEMA,
            "request_sha256": request["request_sha256"],
            "observed_at": observed_now.isoformat(),
            "target": request["target"],
            "current_cas": current,
            "profile_cas": profile,
            "system_identity": current_identity,
            "tools": {"python": python_identity, "git": git_identity},
            "tool_environment": _local_process_environment(),
            "repository": repo,
            "effects": {"remote_write": False, "repository_write": False, "nix": False},
        }
        value["receipt_sha256"] = _hash(value)
        return validate_receipt(value, request, now=observed_now)
    finally:
        os.close(git_fd)
        os.close(python_fd)


def _validate_tool(value: Any, label: str) -> dict[str, Any]:
    fields = {"path", "realpath", "sha256", "size", "uid", "gid", "mode", "nlink", "dev", "ino", "version", "version_sha256", "version_b64"}
    tool = dict(_exact(value, fields, label))
    if not _SHA.fullmatch(str(tool["sha256"])) or not _SHA.fullmatch(str(tool["version_sha256"])):
        raise HostStateError(f"{label} digest is invalid")
    for field in ("size", "uid", "gid", "mode", "nlink", "dev", "ino"):
        if isinstance(tool[field], bool) or not isinstance(tool[field], int) or tool[field] < 0:
            raise HostStateError(f"{label} {field} is invalid")
    if tool["size"] <= 0 or tool["nlink"] != 1 or not tool["mode"] & 0o111 or tool["uid"] != 0 or tool["mode"] & 0o022:
        raise HostStateError(f"{label} executable metadata is invalid")
    if (
        not isinstance(tool["path"], str)
        or not tool["path"].startswith("/")
        or not isinstance(tool["realpath"], str)
        or not tool["realpath"].startswith("/")
        or not isinstance(tool["version"], str)
        or not tool["version"].strip()
    ):
        raise HostStateError(f"{label} path or version is invalid")
    if not isinstance(tool["version_b64"], str):
        raise HostStateError(f"{label} version encoding is invalid")
    try:
        version_raw = base64.b64decode(tool["version_b64"], validate=True)
        decoded_version = version_raw.decode("utf-8", errors="strict").strip()
    except (ValueError, UnicodeDecodeError) as exc:
        raise HostStateError(f"{label} version encoding is invalid") from exc
    if digest(version_raw) != tool["version_sha256"] or decoded_version != tool["version"] or len(version_raw) > 65536:
        raise HostStateError(f"{label} version bytes differ")
    return tool


def validate_receipt(value: Any, request_value: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    request = validate_request(request_value, allow_fixture=True)
    fields = {
        "schema",
        "request_sha256",
        "observed_at",
        "target",
        "current_cas",
        "profile_cas",
        "system_identity",
        "tools",
        "tool_environment",
        "repository",
        "effects",
        "receipt_sha256",
    }
    receipt = dict(_exact(value, fields, "host-state receipt"))
    if receipt["schema"] != RECEIPT_SCHEMA or receipt["request_sha256"] != request["request_sha256"] or receipt["target"] != request["target"]:
        raise HostStateError("host-state receipt binding differs")
    observed = _parse_time(receipt["observed_at"], "host-state observed_at")
    if now is not None and not timedelta(0) <= now - observed <= timedelta(minutes=10):
        raise HostStateError("host-state receipt is stale")
    if receipt["current_cas"] != receipt["profile_cas"] or not _STORE.fullmatch(str(receipt["current_cas"])):
        raise HostStateError("host-state CAS differs")
    tools = _exact(receipt["tools"], {"python", "git"}, "host-state tools")
    _validate_tool(tools["python"], "Python")
    _validate_tool(tools["git"], "Git")
    if receipt["tool_environment"] != _local_process_environment():
        raise HostStateError("host-state tool environment differs")
    system_identity = _exact(
        receipt["system_identity"],
        {"dev", "ino", "uid", "gid", "mode"},
        "host-state system identity",
    )
    for field in system_identity:
        if isinstance(system_identity[field], bool) or not isinstance(system_identity[field], int) or system_identity[field] < 0:
            raise HostStateError("host-state system identity is invalid")
    if system_identity["uid"] != 0 or system_identity["mode"] & 0o022:
        raise HostStateError("host-state system identity is mutable")
    repository = _exact(
        receipt["repository"],
        {"path", "branch", "uid", "gid", "mode", "dev", "ino", "head_sha256", "ref_sha256", "commit"},
        "host-state repository",
    )
    if repository["path"] != request["target"]["repository"] or repository["branch"] != request["target"]["expected_branch"] or not _SHA.fullmatch(str(repository["head_sha256"])):
        raise HostStateError("host-state repository binding differs")
    if not _SHA.fullmatch(str(repository["ref_sha256"])) or not re.fullmatch(r"[0-9a-f]{40}", str(repository["commit"])):
        raise HostStateError("host-state repository commit identity is invalid")
    if repository["head_sha256"] != digest(f"ref: refs/heads/{repository['branch']}\n".encode()) or repository["ref_sha256"] != digest((repository["commit"] + "\n").encode()):
        raise HostStateError("host-state repository ref evidence differs")
    if tools["python"]["path"] != request["target"]["remote_python"] or tools["git"]["path"] != request["target"]["remote_git"]:
        raise HostStateError("host-state tool logical paths differ")
    for field in ("uid", "gid", "mode", "dev", "ino"):
        if isinstance(repository[field], bool) or not isinstance(repository[field], int) or repository[field] < 0:
            raise HostStateError("host-state repository identity is invalid")
    if receipt["effects"] != {"remote_write": False, "repository_write": False, "nix": False}:
        raise HostStateError("host-state effect evidence differs")
    claimed = receipt.pop("receipt_sha256")
    if claimed != _hash(receipt):
        raise HostStateError("host-state receipt hash differs")
    receipt["receipt_sha256"] = claimed
    return receipt


def dependency_projection(receipt_value: Mapping[str, Any], request_value: Mapping[str, Any], *, ssh_sha256: str, descriptor_sha256: str) -> dict[str, Any]:
    receipt = validate_receipt(receipt_value, request_value)
    compact = {
        "schema": "tgw-prod-a3-host-state-observation-receipt/v1",
        "observed_at": receipt["observed_at"],
        "current_cas": receipt["current_cas"],
        "profile_cas": receipt["profile_cas"],
        "tools": {
            "python_sha256": receipt["tools"]["python"]["sha256"],
            "git_sha256": receipt["tools"]["git"]["sha256"],
            "ssh_sha256": ssh_sha256,
        },
    }
    compact["receipt_sha256"] = _hash(compact)
    return {
        "schema": DEPENDENCY_SCHEMA,
        "status": "SATISFIED",
        "descriptor_sha256": descriptor_sha256,
        "receipt_sha256": compact["receipt_sha256"],
        "observed_at": compact["observed_at"],
        "current_cas": compact["current_cas"],
        "profile_cas": compact["profile_cas"],
        "tools": compact["tools"],
        "receipt": compact,
    }


def encode_helper_response(receipt: Mapping[str, Any]) -> bytes:
    raw = canonical(receipt)
    return len(raw).to_bytes(8, "big") + raw


def decode_helper_response(raw: bytes, request: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    if len(raw) < 8:
        raise HostStateError("host-state response is truncated")
    size = int.from_bytes(raw[:8], "big")
    bounds = validate_request(request, allow_fixture=True)["bounds"]
    if size <= 0 or size > bounds["max_output_bytes"] or len(raw) != size + 8:
        raise HostStateError("host-state response bound differs")
    try:
        value = json.loads(raw[8:])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HostStateError("host-state response is malformed") from exc
    if isinstance(value, Mapping) and value.get("schema") == TERMINAL_SCHEMA:
        remote_terminal = validate_terminal(value, request_sha256=request["request_sha256"], now=now)
        if (
            remote_terminal["outcome"],
            remote_terminal["stage"],
            remote_terminal["code"],
            remote_terminal["dispatched"],
        ) not in _REMOTE_TERMINALS:
            raise HostStateError("host-state helper returned a non-remote terminal")
        return remote_terminal
    return validate_receipt(value, request, now=now)


_TERMINALS = {
    ("PASS", "complete", "NONE", True),
    ("HOLD", "prelaunch", "NOT_READY", False),
    ("FAILED", "prelaunch", "VALIDATION_FAILED", False),
    ("HOLD", "remote", "HOST_NOT_READY", True),
    ("FAILED", "remote", "HELPER_FAILED", True),
    ("AMBIGUOUS", "authority", "TOKEN_UNCERTAIN", False),
    ("AMBIGUOUS", "dispatch", "DISPATCH_UNCERTAIN", True),
    ("AMBIGUOUS", "persistence", "PERSISTENCE_UNCERTAIN", False),
    ("AMBIGUOUS", "persistence", "PERSISTENCE_UNCERTAIN", True),
}
_REMOTE_TERMINALS = {
    ("HOLD", "remote", "HOST_NOT_READY", True),
    ("FAILED", "remote", "HELPER_FAILED", True),
}


def terminal(*, outcome: str, stage: str, code: str, dispatched: bool, request_sha256: str, observed_at: str, diagnostic: bytes = b"") -> dict[str, Any]:
    if (outcome, stage, code, dispatched) not in _TERMINALS or not _SHA.fullmatch(request_sha256):
        raise HostStateError("host-state terminal tuple is invalid")
    value = {
        "schema": TERMINAL_SCHEMA,
        "outcome": outcome,
        "stage": stage,
        "code": code,
        "dispatched": dispatched,
        "request_sha256": request_sha256,
        "observed_at": observed_at,
        "diagnostic_b64": base64.b64encode(diagnostic).decode("ascii"),
        "diagnostic_sha256": digest(diagnostic),
        "diagnostic_size": len(diagnostic),
    }
    value["terminal_sha256"] = _hash(value)
    return value


def validate_terminal(
    value: Any,
    *,
    request_sha256: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    fields = {"schema", "outcome", "stage", "code", "dispatched", "request_sha256", "observed_at", "diagnostic_b64", "diagnostic_sha256", "diagnostic_size", "terminal_sha256"}
    result = dict(_exact(value, fields, "host-state terminal"))
    if result["schema"] != TERMINAL_SCHEMA or (result["outcome"], result["stage"], result["code"], result["dispatched"]) not in _TERMINALS:
        raise HostStateError("host-state terminal semantics differ")
    if request_sha256 is not None and result["request_sha256"] != request_sha256:
        raise HostStateError("host-state terminal request differs")
    observed_at = _parse_time(result["observed_at"], "host-state terminal time")
    if now is not None and not timedelta(0) <= now - observed_at <= timedelta(minutes=10):
        raise HostStateError("host-state terminal is stale")
    if not isinstance(result["diagnostic_b64"], str):
        raise HostStateError("host-state terminal diagnostic encoding differs")
    try:
        diagnostic = base64.b64decode(result["diagnostic_b64"], validate=True)
    except ValueError as exc:
        raise HostStateError("host-state terminal diagnostic encoding differs") from exc
    if (
        not _SHA.fullmatch(str(result["diagnostic_sha256"]))
        or result["diagnostic_sha256"] != digest(diagnostic)
        or isinstance(result["diagnostic_size"], bool)
        or not isinstance(result["diagnostic_size"], int)
        or result["diagnostic_size"] != len(diagnostic)
        or len(diagnostic) > 256
    ):
        raise HostStateError("host-state terminal diagnostic differs")
    claimed = result.pop("terminal_sha256")
    if claimed != _hash(result):
        raise HostStateError("host-state terminal hash differs")
    result["terminal_sha256"] = claimed
    return result


@dataclass(frozen=True)
class HostStateComposition:
    """Truthful default: source exists, but production authorities are not mounted."""

    schema: str = COMPOSITION_SCHEMA
    status: str = "NOT_EXECUTABLE"
    reason: str = "a mounted executable A3O02 Plan solution, real-sshd parity receipt, dedicated credential authority, and one-attempt observation grant are required"

    @property
    def receipt_sha256(self) -> str:
        return _hash({"schema": self.schema, "status": self.status, "reason": self.reason})

    def execute(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        validate_request(request, allow_fixture=True)
        raise ObservationHold(self.reason)


def _root_identity(value: Any, label: str) -> dict[str, Any]:
    fields = {"path", "uid", "gid", "mode", "dev", "ino", "nlink"}
    result = dict(_exact(value, fields, label))
    for field in fields - {"path"}:
        if isinstance(result[field], bool) or not isinstance(result[field], int) or result[field] < 0:
            raise HostStateError(f"{label} {field} is invalid")
    if not isinstance(result["path"], str) or not result["path"].startswith("/"):
        raise HostStateError(f"{label} path is invalid")
    # Some admitted filesystems report one link for directories; inode/dev and
    # the retained parent/root descriptors provide the displacement proof.
    if result["mode"] != 0o700 or result["nlink"] < 1:
        raise HostStateError(f"{label} metadata is invalid")
    return result


def _same_root_authority(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Directory nlink may grow when an admitted committed attempt is published."""
    fields = ("path", "uid", "gid", "mode", "dev", "ino")
    return all(left.get(field) == right.get(field) for field in fields)


def _validate_production_private_root(
    value: Mapping[str, Any],
    label: str,
    *,
    trusted_uid: int,
) -> dict[str, Any]:
    identity = _root_identity(value, label)
    path = Path(identity["path"])
    _reject_symlink_ancestors(path)
    current = os.stat(path, follow_symlinks=False)
    if not stat.S_ISDIR(current.st_mode) or current.st_uid != trusted_uid or stat.S_IMODE(current.st_mode) != 0o700 or (current.st_dev, current.st_ino) != (identity["dev"], identity["ino"]):
        raise HostStateError(f"{label} live identity differs")
    for ancestor in path.parents:
        item = os.stat(ancestor, follow_symlinks=False)
        if item.st_uid != 0 or stat.S_IMODE(item.st_mode) & 0o022:
            raise HostStateError(f"{label} ancestor is mutable")
        if ancestor == Path("/"):
            break
    return identity


class HostStateObservationGrant:
    __slots__ = ("_value",)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise TypeError("HostStateObservationGrant is sealed")

    def __init__(self, value: Mapping[str, Any], *, _token: object) -> None:
        if _token is not _GRANT_SEAL:
            raise HostStateError("host-state grant may only be loaded as authority")
        self._value = dict(value)

    @property
    def value(self) -> dict[str, Any]:
        return dict(self._value)

    @classmethod
    def _fixture_issue(
        cls,
        *,
        request: Mapping[str, Any],
        composition_sha256: str,
        token_root_identity: Mapping[str, Any],
        evidence_root_identity: Mapping[str, Any],
        expires_at: str,
        now: datetime | None = None,
    ) -> "HostStateObservationGrant":
        observed = now or datetime.now(timezone.utc)
        validated = validate_request(request, now=observed, allow_fixture=True)
        payload = {
            "schema": GRANT_SCHEMA,
            "effect_kind": EFFECT_KIND,
            "plan": dict(validated["plan"]),
            "request_sha256": validated["request_sha256"],
            "target": dict(validated["target"]),
            "composition_sha256": composition_sha256,
            "token_root_identity": _root_identity(token_root_identity, "token root"),
            "evidence_root_identity": _root_identity(evidence_root_identity, "evidence root"),
            "attempts": 1,
            "issued_at": observed.isoformat(),
            "not_before": observed.isoformat(),
            "expires_at": expires_at,
        }
        payload["grant_sha256"] = _hash(payload)
        return cls(
            cls.validate(payload, request=validated, now=observed),
            _token=_GRANT_SEAL,
        )

    @staticmethod
    def validate(
        value: Any,
        *,
        request: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        fields = {
            "schema",
            "effect_kind",
            "plan",
            "request_sha256",
            "target",
            "composition_sha256",
            "token_root_identity",
            "evidence_root_identity",
            "attempts",
            "issued_at",
            "not_before",
            "expires_at",
            "grant_sha256",
        }
        grant = dict(_exact(value, fields, "host-state grant"))
        if grant["schema"] != GRANT_SCHEMA or grant["effect_kind"] != EFFECT_KIND:
            raise HostStateError("host-state grant kind differs")
        if isinstance(grant["attempts"], bool) or grant["attempts"] != 1:
            raise HostStateError("host-state grant is not exactly one attempt")
        if not isinstance(grant["composition_sha256"], str) or not _SHA.fullmatch(grant["composition_sha256"]):
            raise HostStateError("host-state grant composition is invalid")
        _root_identity(grant["token_root_identity"], "token root")
        _root_identity(grant["evidence_root_identity"], "evidence root")
        issued = _parse_time(grant["issued_at"], "grant issued_at")
        not_before = _parse_time(grant["not_before"], "grant not_before")
        expires = _parse_time(grant["expires_at"], "grant expires_at")
        if not issued <= not_before < expires or expires - issued > timedelta(minutes=10):
            raise HostStateError("host-state grant interval is invalid")
        if now is not None and not not_before <= now < expires:
            raise HostStateError("host-state grant is stale")
        if request is not None:
            validated = validate_request(request, now=now, allow_fixture=True)
            if grant["plan"] != validated["plan"] or grant["request_sha256"] != validated["request_sha256"] or grant["target"] != validated["target"]:
                raise HostStateError("host-state grant authority differs from request")
        claimed = grant.pop("grant_sha256")
        if claimed != _hash(grant):
            raise HostStateError("host-state grant hash differs")
        grant["grant_sha256"] = claimed
        return grant


class MountedHostStateObservationGrant:
    __slots__ = (
        "_fd",
        "_grant_raw",
        "_identity",
        "_path",
        "_sealed",
        "_sha256",
    )

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise TypeError("MountedHostStateObservationGrant is sealed")

    def __init__(
        self,
        path: Path,
        expected_sha256: str,
        *,
        request: Mapping[str, Any],
        now: datetime,
        _token: object,
    ) -> None:
        if _token is not _GRANT_SEAL or path.name != expected_sha256.removeprefix("sha256:") + ".json":
            raise HostStateError("host-state grant locator is not content addressed")
        fd, raw = _protected_file(path, expected_sha256)
        try:
            grant = HostStateObservationGrant.validate(json.loads(raw), request=request, now=now)
            if raw != canonical(grant):
                raise HostStateError("mounted host-state grant is not canonical")
        except Exception:
            os.close(fd)
            raise
        object.__setattr__(self, "_sealed", False)
        object.__setattr__(self, "_fd", fd)
        object.__setattr__(self, "_grant_raw", canonical(grant))
        object.__setattr__(self, "_identity", _inode_identity(os.fstat(fd)))
        object.__setattr__(self, "_path", path)
        object.__setattr__(self, "_sha256", expected_sha256)
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("MountedHostStateObservationGrant is immutable")
        object.__setattr__(self, name, value)

    @property
    def fd(self) -> int:
        return self._fd

    @property
    def grant(self) -> dict[str, Any]:
        return dict(json.loads(self._grant_raw))

    @property
    def identity(self) -> tuple[int, ...]:
        return tuple(self._identity)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def sha256(self) -> str:
        return self._sha256

    @property
    def authority(self) -> dict[str, Any]:
        return {
            "path": str(self._path),
            "sha256": self._sha256,
            "grant_sha256": self.grant["grant_sha256"],
            "identity": list(self._identity),
        }

    def observation(self) -> dict[str, Any]:
        held_identity: list[int] | None = None
        named_identity: list[int] | None = None
        held_sha256: str | None = None
        try:
            held = os.fstat(self._fd)
            held_identity = list(_inode_identity(held))
            held_sha256 = digest(os.pread(self._fd, held.st_size + 1, 0))
        except OSError:
            pass
        try:
            named_identity = list(_inode_identity(os.stat(self._path, follow_symlinks=False)))
        except OSError:
            pass
        status = "PASS" if held_identity == list(self._identity) and named_identity == list(self._identity) and held_sha256 == self._sha256 else "FAILED"
        value = {
            "schema": GRANT_OBSERVATION_SCHEMA,
            **self.authority,
            "held_identity": held_identity,
            "named_identity": named_identity,
            "held_sha256": held_sha256,
            "postcheck": status,
        }
        value["observation_sha256"] = _hash(value)
        return value

    def assert_observation(self, expected: Mapping[str, Any]) -> None:
        if self.observation() != expected:
            raise HostStateError("mounted host-state grant changed before publication")

    def postcheck(self) -> None:
        if self.observation()["postcheck"] != "PASS":
            raise HostStateError("mounted host-state grant changed")

    def read(
        self,
        *,
        request: Mapping[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        self.postcheck()
        held = os.fstat(self._fd)
        raw = os.pread(self._fd, held.st_size + 1, 0)
        parsed = HostStateObservationGrant.validate(json.loads(raw), request=request, now=now)
        if raw != canonical(parsed):
            raise HostStateError("mounted host-state grant is not canonical")
        return parsed

    def close(self) -> None:
        os.close(self._fd)


def load_host_state_observation_grant(
    path: Path,
    expected_sha256: str,
    *,
    request: Mapping[str, Any],
    now: datetime | None = None,
) -> MountedHostStateObservationGrant:
    observed = now or datetime.now(timezone.utc)
    return MountedHostStateObservationGrant(
        path,
        expected_sha256,
        request=request,
        now=observed,
        _token=_GRANT_SEAL,
    )


def _validate_grant_authority(
    value: Any,
    *,
    grant: Mapping[str, Any],
) -> dict[str, Any]:
    authority = dict(
        _exact(
            value,
            {"path", "sha256", "grant_sha256", "identity"},
            "mounted host-state grant authority",
        )
    )
    if (
        not isinstance(authority["path"], str)
        or not authority["path"].startswith("/")
        or not isinstance(authority["sha256"], str)
        or not _SHA.fullmatch(authority["sha256"])
        or authority["grant_sha256"] != grant["grant_sha256"]
        or not isinstance(authority["identity"], list)
    ):
        raise HostStateError("mounted host-state grant authority differs")
    _validate_inode_identity_list(authority["identity"], "mounted host-state grant authority")
    return authority


def _validate_grant_observation(
    value: Any,
    *,
    grant: Mapping[str, Any],
    expected_authority: Mapping[str, Any],
) -> dict[str, Any]:
    fields = {
        "schema",
        "path",
        "sha256",
        "grant_sha256",
        "identity",
        "held_identity",
        "named_identity",
        "held_sha256",
        "postcheck",
        "observation_sha256",
    }
    observation = dict(_exact(value, fields, "mounted host-state grant observation"))
    claimed = observation.pop("observation_sha256")
    if observation["schema"] != GRANT_OBSERVATION_SCHEMA or claimed != _hash(observation):
        raise HostStateError("mounted host-state grant observation identity differs")
    observation["observation_sha256"] = claimed
    authority = _validate_grant_authority(
        {field: observation[field] for field in ("path", "sha256", "grant_sha256", "identity")},
        grant=grant,
    )
    if authority != _validate_grant_authority(
        expected_authority,
        grant=grant,
    ):
        raise HostStateError("mounted host-state grant authority is not expected")
    for field in ("held_identity", "named_identity"):
        identity = observation[field]
        if identity is not None:
            _validate_inode_identity_list(identity, f"mounted host-state grant {field}")
    if observation["held_sha256"] is not None and (not isinstance(observation["held_sha256"], str) or not _SHA.fullmatch(observation["held_sha256"])):
        raise HostStateError("mounted host-state grant held hash is invalid")
    passed = observation["held_identity"] == authority["identity"] and observation["named_identity"] == authority["identity"] and observation["held_sha256"] == authority["sha256"]
    if observation["postcheck"] != ("PASS" if passed else "FAILED"):
        raise HostStateError("mounted host-state grant postcheck differs")
    return observation


class HostStateEvidenceStore:
    """Atomic attempt-directory evidence publication using the admitted store primitive."""

    __slots__ = ("_sealed", "_store", "root")

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise TypeError("HostStateEvidenceStore is sealed")

    def __init__(self, root: Path, *, trusted_uid: int | None = None) -> None:
        if not root.is_absolute():
            raise HostStateError("host-state evidence root is not absolute")
        _reject_symlink_ancestors(root.parent)
        object.__setattr__(self, "_sealed", False)
        object.__setattr__(self, "_store", _AtomicEvidenceStore(root, trusted_uid=trusted_uid))
        object.__setattr__(self, "root", root)
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("HostStateEvidenceStore is immutable")
        object.__setattr__(self, name, value)

    @property
    def identity(self) -> dict[str, Any]:
        return dict(self._store.identity)

    def ready(self) -> bool:
        try:
            admitted = _root_identity(self.identity, "evidence root")
            held = os.fstat(self._store._root_fd)
            named = os.stat(
                self._store._root_name,
                dir_fd=self._store._parent_fd,
                follow_symlinks=False,
            )
            return (
                admitted["uid"] == self._store.trusted_uid
                and stat.S_ISDIR(held.st_mode)
                and stat.S_ISDIR(named.st_mode)
                and (held.st_dev, held.st_ino) == (admitted["dev"], admitted["ino"])
                and (named.st_dev, named.st_ino) == (admitted["dev"], admitted["ino"])
                and held.st_uid == named.st_uid == admitted["uid"]
                and stat.S_IMODE(held.st_mode) == stat.S_IMODE(named.st_mode) == 0o700
            )
        except (OSError, HostStateError):
            return False

    def close(self) -> None:
        os.close(self._store._root_fd)
        os.close(self._store._parent_fd)

    def _persistence_context(
        self,
        *,
        request: Mapping[str, Any],
        attempted_receipt: Mapping[str, Any],
        attachments: Mapping[str, Mapping[str, Any]],
        original_terminal: Mapping[str, Any],
        observed_receipt: Mapping[str, Any] | None,
        observed_dependency: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        request_raw = canonical(request)
        receipt_raw = canonical(attempted_receipt)
        attachment_raw = {name: canonical(value) for name, value in sorted(attachments.items())}
        manifest = {
            "request_sha256": digest(request_raw),
            "receipt_sha256": digest(receipt_raw),
            "archive_sha256": digest(b""),
            "archive_size": 0,
            "attachments": {name: {"sha256": digest(raw), "size": len(raw)} for name, raw in attachment_raw.items()},
        }
        item_raw = {
            "request.json": request_raw,
            "receipt.json": receipt_raw,
            "archive.tar": b"",
            **attachment_raw,
            "manifest.json": canonical(manifest),
        }
        attempt_name = str(attempted_receipt["receipt_sha256"]).split(":", 1)[1]
        context = {
            "schema": PERSISTENCE_CONTEXT_SCHEMA,
            "status": "UNVERIFIED_NOT_DURABLE",
            "attempt_name": attempt_name,
            "attempt_path": str(self.root / attempt_name),
            "attempted_receipt": dict(attempted_receipt),
            "original_terminal": dict(original_terminal),
            "observed_receipt": (None if observed_receipt is None else dict(observed_receipt)),
            "observed_dependency": (None if observed_dependency is None else dict(observed_dependency)),
            "attempted_attachments": {name: dict(value) for name, value in sorted(attachments.items())},
            "expected_items": {name: {"sha256": digest(raw), "size": len(raw)} for name, raw in sorted(item_raw.items())},
        }
        context["context_sha256"] = _hash(context)
        return context

    def persist(
        self,
        *,
        request: Mapping[str, Any],
        receipt: Mapping[str, Any],
        terminal_value: Mapping[str, Any],
        grant: Mapping[str, Any],
        grant_observation: Mapping[str, Any],
        token_identity: Mapping[str, Any],
        dependency: Mapping[str, Any],
        composition_value: Mapping[str, Any],
        before_publish: Any | None = None,
    ) -> tuple[dict[str, Any], ...]:
        validated_request = validate_request(request, allow_fixture=True)
        validated_receipt = validate_receipt(receipt, validated_request)
        validated_terminal = validate_terminal(terminal_value, request_sha256=validated_request["request_sha256"])
        validated_grant = HostStateObservationGrant.validate(grant, request=validated_request)
        validated_grant_observation = _validate_grant_observation(
            grant_observation,
            grant=validated_grant,
            expected_authority={field: grant_observation[field] for field in ("path", "sha256", "grant_sha256", "identity")},
        )
        _validate_composition_value(
            composition_value,
            validated_request,
            expected_sha256=validated_grant["composition_sha256"],
            expected_evidence_root=self.identity,
        )
        if dependency.get("descriptor_sha256") != validated_grant["composition_sha256"]:
            raise HostStateError("persisted dependency differs from composition")
        attachments = {
            "terminal.json": validated_terminal,
            "grant.json": validated_grant,
            "grant-authority.json": validated_grant_observation,
            "token-root.json": _root_identity(token_identity, "token root"),
            "dependency.json": dict(dependency),
            "composition.json": dict(composition_value),
        }
        try:
            paths = self._store.persist(
                validated_receipt,
                b"",
                validated_request,
                attachments,
                before_publish=before_publish,
            )
            return _validate_evidence_paths(paths)
        except Exception as exc:
            ambiguous = terminal(
                outcome="AMBIGUOUS",
                stage="persistence",
                code="PERSISTENCE_UNCERTAIN",
                dispatched=True,
                request_sha256=validated_request["request_sha256"],
                observed_at=datetime.now(timezone.utc).isoformat(),
                diagnostic=type(exc).__name__.encode(),
            )
            context = self._persistence_context(
                request=validated_request,
                attempted_receipt=validated_receipt,
                attachments=attachments,
                original_terminal=validated_terminal,
                observed_receipt=validated_receipt,
                observed_dependency=dependency,
            )
            raise HostStatePersistenceAmbiguous(ambiguous, context) from exc

    def persist_terminal(
        self,
        *,
        request: Mapping[str, Any],
        terminal_value: Mapping[str, Any],
        grant: Mapping[str, Any],
        grant_observation: Mapping[str, Any],
        token_identity: Mapping[str, Any],
        composition_sha256: str,
        composition_value: Mapping[str, Any],
        before_publish: Any | None = None,
    ) -> tuple[dict[str, Any], ...]:
        validated_request = validate_request(request, allow_fixture=True)
        validated_terminal = validate_terminal(terminal_value, request_sha256=validated_request["request_sha256"])
        validated_grant = HostStateObservationGrant.validate(grant, request=validated_request)
        validated_grant_observation = _validate_grant_observation(
            grant_observation,
            grant=validated_grant,
            expected_authority={field: grant_observation[field] for field in ("path", "sha256", "grant_sha256", "identity")},
        )
        if composition_sha256 != validated_grant["composition_sha256"]:
            raise HostStateError("persisted failure composition differs")
        _validate_composition_value(
            composition_value,
            validated_request,
            expected_sha256=composition_sha256,
            expected_evidence_root=self.identity,
        )
        failure = {
            "schema": "tgw-prod-a3-host-state-observation-failure-evidence/v1",
            "request_sha256": validated_request["request_sha256"],
            "terminal_sha256": validated_terminal["terminal_sha256"],
            "composition_sha256": composition_sha256,
        }
        failure["receipt_sha256"] = _hash(failure)
        attachments = {
            "terminal.json": validated_terminal,
            "grant.json": validated_grant,
            "grant-authority.json": validated_grant_observation,
            "token-root.json": _root_identity(token_identity, "token root"),
            "composition.json": dict(composition_value),
        }
        try:
            paths = self._store.persist(
                failure,
                b"",
                validated_request,
                attachments,
                before_publish=before_publish,
            )
            return _validate_evidence_paths(paths)
        except Exception as exc:
            ambiguous = terminal(
                outcome="AMBIGUOUS",
                stage="persistence",
                code="PERSISTENCE_UNCERTAIN",
                dispatched=validated_terminal["dispatched"],
                request_sha256=validated_request["request_sha256"],
                observed_at=datetime.now(timezone.utc).isoformat(),
                diagnostic=type(exc).__name__.encode(),
            )
            context = self._persistence_context(
                request=validated_request,
                attempted_receipt=failure,
                attachments=attachments,
                original_terminal=validated_terminal,
                observed_receipt=None,
                observed_dependency=None,
            )
            raise HostStatePersistenceAmbiguous(ambiguous, context) from exc


def _verify_evidence_bundle(
    paths: tuple[Path, ...],
) -> tuple[tuple[dict[str, Any], ...], dict[str, bytes]]:
    refs: list[dict[str, Any]] = []
    contents: dict[str, bytes] = {}
    parents: set[Path] = set()
    file_owners: set[tuple[int, int]] = set()
    for path in paths:
        _reject_symlink_ancestors(path)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o400:
                raise HostStateError("persisted evidence metadata differs")
            file_owners.add((before.st_uid, before.st_gid))
            raw = bytearray()
            while True:
                block = os.read(fd, 1 << 20)
                if not block:
                    break
                raw.extend(block)
            after = os.fstat(fd)
            named = os.stat(path, follow_symlinks=False)
            if _inode_identity(before) != _inode_identity(after) or _inode_identity(after) != _inode_identity(named):
                raise HostStateError("persisted evidence identity changed")
        finally:
            os.close(fd)
        parents.add(path.parent)
        final_raw = bytes(raw)
        contents[path.name] = final_raw
        refs.append({"path": str(path), "sha256": digest(final_raw), "size": len(final_raw)})
    if len(parents) != 1:
        raise HostStateError("persisted evidence is not one atomic bundle")
    parent = next(iter(parents))
    root = parent.parent
    parent_st = os.stat(parent, follow_symlinks=False)
    root_st = os.stat(root, follow_symlinks=False)
    if (
        len(file_owners) != 1
        or not stat.S_ISDIR(parent_st.st_mode)
        or not stat.S_ISDIR(root_st.st_mode)
        or stat.S_IMODE(parent_st.st_mode) != 0o700
        or stat.S_IMODE(root_st.st_mode) != 0o700
        or (parent_st.st_uid, parent_st.st_gid) not in file_owners
        or (root_st.st_uid, root_st.st_gid) not in file_owners
    ):
        raise HostStateError("persisted evidence bundle ownership differs")
    names = {Path(ref["path"]).name for ref in refs}
    if len(names) != len(refs):
        raise HostStateError("persisted evidence contains duplicate names")
    required = {
        "request.json",
        "receipt.json",
        "archive.tar",
        "terminal.json",
        "grant.json",
        "grant-authority.json",
        "token-root.json",
        "manifest.json",
    }
    if not required <= names or not ({"dependency.json", "composition.json"} & names):
        raise HostStateError("persisted evidence bundle is incomplete")
    manifest = _exact(
        json.loads(contents["manifest.json"]),
        {"request_sha256", "receipt_sha256", "archive_sha256", "archive_size", "attachments"},
        "persisted evidence manifest",
    )
    if not isinstance(manifest["attachments"], Mapping):
        raise HostStateError("persisted evidence manifest attachments differ")
    if any(not isinstance(name, str) or not name.endswith(".json") or "/" in name for name in manifest["attachments"]):
        raise HostStateError("persisted evidence manifest attachment name differs")
    if names != required | set(manifest["attachments"]):
        raise HostStateError("persisted evidence names differ from manifest")
    by_name = {Path(ref["path"]).name: ref for ref in refs}
    if (
        manifest["request_sha256"] != by_name["request.json"]["sha256"]
        or manifest["receipt_sha256"] != by_name["receipt.json"]["sha256"]
        or manifest["archive_sha256"] != by_name["archive.tar"]["sha256"]
        or manifest["archive_size"] != by_name["archive.tar"]["size"]
    ):
        raise HostStateError("persisted evidence manifest core differs")
    for name, identity in manifest["attachments"].items():
        if name not in by_name or identity != {"sha256": by_name[name]["sha256"], "size": by_name[name]["size"]}:
            raise HostStateError("persisted evidence manifest attachment differs")
    return tuple(refs), contents


def _validate_evidence_paths(paths: tuple[Path, ...]) -> tuple[dict[str, Any], ...]:
    refs, _contents = _verify_evidence_bundle(paths)
    return refs


def _validate_evidence_refs(
    value: Any,
) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    if not isinstance(value, list) or not value:
        raise HostStateError("host-state evidence refs are absent")
    paths: list[Path] = []
    supplied: dict[str, dict[str, Any]] = {}
    for item in value:
        ref = dict(_exact(item, {"path", "sha256", "size"}, "host-state evidence ref"))
        if (
            not isinstance(ref["path"], str)
            or not ref["path"].startswith("/")
            or not _SHA.fullmatch(str(ref["sha256"]))
            or isinstance(ref["size"], bool)
            or not isinstance(ref["size"], int)
            or ref["size"] < 0
        ):
            raise HostStateError("host-state evidence ref is invalid")
        paths.append(Path(ref["path"]))
        supplied[ref["path"]] = ref
    if len(supplied) != len(paths):
        raise HostStateError("host-state evidence refs contain duplicates")
    verified_tuple, contents = _verify_evidence_bundle(tuple(paths))
    verified = list(verified_tuple)
    if {ref["path"]: ref for ref in verified} != supplied:
        raise HostStateError("host-state evidence bytes differ from refs")
    return verified, contents


def _artifact_snapshot(fd: int, raw: bytes) -> tuple[tuple[int, ...], str]:
    return _inode_identity(os.fstat(fd)), digest(raw)


def _known_host(raw: bytes, host: str) -> None:
    try:
        lines = raw.decode("ascii", errors="strict").splitlines()
    except UnicodeDecodeError as exc:
        raise ObservationHold("known-host authority is not ASCII") from exc
    if len(lines) != 1:
        raise ObservationHold("known-host authority is not one exact record")
    parts = lines[0].split()
    if len(parts) != 3 or parts[0] != host or parts[1] not in {"ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256"}:
        raise ObservationHold("known-host authority grammar differs")
    try:
        base64.b64decode(parts[2], validate=True)
    except ValueError as exc:
        raise ObservationHold("known-host authority key is malformed") from exc


def _ssh_argv_policy(request: Mapping[str, Any]) -> list[str]:
    return [
        "-F",
        "/dev/null",
        "-p",
        str(request["target"]["port"]),
        "-oBatchMode=yes",
        "-oIdentitiesOnly=yes",
        "-oIdentityAgent=none",
        "-oClearAllForwardings=yes",
        "-oStrictHostKeyChecking=yes",
        "-oGlobalKnownHostsFile=/dev/null",
        "-oCanonicalizeHostname=no",
        "-oProxyCommand=none",
        "-oProxyJump=none",
        "-oPreferredAuthentications=publickey",
        "-oKbdInteractiveAuthentication=no",
        "-oGSSAPIAuthentication=no",
        "-oHostbasedAuthentication=no",
        "-oPubkeyAuthentication=yes",
        "-oPermitLocalCommand=no",
        "-oControlMaster=no",
        "-oControlPath=none",
        "-oUpdateHostKeys=no",
        "-oVerifyHostKeyDNS=no",
        "-oForwardAgent=no",
        "-oForwardX11=no",
        f"-oHostKeyAlias={request['target']['host']}",
        "-oUserKnownHostsFile=<held-fd>",
        "-oIdentityFile=<sealed-fd>",
        "-oPasswordAuthentication=no",
        "-T",
        f"{request['target']['user']}@{request['target']['host']}",
        f"{request['target']['remote_python']} -I -c <held-helper-bootstrap>",
    ]


def _validate_composition_value(
    value: Any,
    request: Mapping[str, Any],
    *,
    expected_sha256: str,
    expected_evidence_root: Mapping[str, Any],
) -> dict[str, Any]:
    composition = dict(
        _exact(
            value,
            {
                "schema",
                "status",
                "request_sha256",
                "plan_authority",
                "sshd_parity_authority",
                "artifacts",
                "ssh_version",
                "ssh_argv_policy",
                "local_process_environment",
                "token_root_identity",
                "evidence_root_identity",
            },
            "host-state production composition",
        )
    )
    if composition["schema"] != COMPOSITION_SCHEMA or composition["status"] != "EXECUTABLE" or composition["request_sha256"] != request["request_sha256"] or _hash(composition) != expected_sha256:
        raise HostStateError("host-state production composition identity differs")
    plan = _exact(composition["plan_authority"], {"path", "sha256", "identity"}, "composition Plan authority")
    parity = _exact(
        composition["sshd_parity_authority"],
        {"path", "sha256", "identity", "evidence_identities"},
        "composition sshd parity authority",
    )
    if (
        not isinstance(plan["path"], str)
        or not plan["path"].startswith("/")
        or not _SHA.fullmatch(str(plan["sha256"]))
        or not isinstance(plan["identity"], list)
        or not isinstance(parity["path"], str)
        or not parity["path"].startswith("/")
        or parity["sha256"] != request["prerequisites"]["sshd_parity_sha256"]
        or not isinstance(parity["identity"], list)
        or not isinstance(parity["evidence_identities"], Mapping)
        or set(parity["evidence_identities"]) != _PARITY_EVIDENCE_ROLES
        or any(not isinstance(identity, list) for identity in parity["evidence_identities"].values())
    ):
        raise HostStateError("host-state composition authority evidence differs")
    _validate_inode_identity_list(plan["identity"], "composition Plan authority")
    _validate_inode_identity_list(parity["identity"], "composition parity authority")
    for role, identity in parity["evidence_identities"].items():
        _validate_inode_identity_list(identity, f"composition parity {role}")
    artifacts = _exact(
        composition["artifacts"],
        {
            "ssh_sha256",
            "ssh_keygen_sha256",
            "known_hosts_sha256",
            "identity_sha256",
            "identity_public_sha256",
            "helper_sha256",
        },
        "composition artifacts",
    )
    for field, artifact in artifacts.items():
        item = _exact(artifact, {"path", "sha256", "identity"}, f"composition {field}")
        if item["sha256"] != request["transport"][field] or not isinstance(item["path"], str) or not item["path"].startswith("/") or not isinstance(item["identity"], list):
            raise HostStateError("host-state composition artifact differs")
        _validate_inode_identity_list(item["identity"], f"composition {field}")
    version = _exact(composition["ssh_version"], {"value", "sha256", "b64"}, "composition SSH version")
    try:
        version_raw = base64.b64decode(str(version["b64"]), validate=True)
        version_text = version_raw.decode("utf-8", errors="strict").strip()
    except (ValueError, UnicodeDecodeError) as exc:
        raise HostStateError("host-state composition SSH version encoding differs") from exc
    if (
        not isinstance(version["value"], str)
        or not version["value"]
        or not _SHA.fullmatch(str(version["sha256"]))
        or digest(version_raw) != version["sha256"]
        or version_text != version["value"]
        or len(version_raw) > 8192
    ):
        raise HostStateError("host-state composition SSH version differs")
    if composition["ssh_argv_policy"] != _ssh_argv_policy(request):
        raise HostStateError("host-state composition SSH argv policy differs")
    if composition["local_process_environment"] != _local_process_environment():
        raise HostStateError("host-state composition local process environment differs")
    _root_identity(composition["token_root_identity"], "composition token root")
    evidence_root = _root_identity(composition["evidence_root_identity"], "composition evidence root")
    if not _same_root_authority(evidence_root, expected_evidence_root):
        raise HostStateError("host-state composition evidence root differs")
    return composition


class SshHostStateProvider:
    __slots__ = (
        "request",
        "ssh_path",
        "ssh_keygen_path",
        "known_hosts_path",
        "identity_path",
        "identity_public_path",
        "helper_path",
        "plan_authority",
        "parity_authority",
        "artifact_uid",
        "production_authority",
    )

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise TypeError("SshHostStateProvider is sealed")

    def __init__(
        self,
        *,
        request: Mapping[str, Any],
        ssh_path: Path,
        ssh_keygen_path: Path,
        known_hosts_path: Path,
        identity_path: Path,
        identity_public_path: Path,
        helper_path: Path,
        plan_authority: MountedHostStatePlanAuthority,
        parity_authority: MountedSshdParityAuthority,
        artifact_uid: int | None = None,
    ) -> None:
        if type(plan_authority) is not MountedHostStatePlanAuthority or type(parity_authority) is not MountedSshdParityAuthority:
            raise HostStateError("production provider authority is not mounted and sealed")
        self.request = validate_request(request, plan_authority=plan_authority, parity_authority=parity_authority)
        self.ssh_path = ssh_path
        self.ssh_keygen_path = ssh_keygen_path
        self.known_hosts_path = known_hosts_path
        self.identity_path = identity_path
        self.identity_public_path = identity_public_path
        self.helper_path = helper_path
        self.plan_authority = plan_authority
        self.parity_authority = parity_authority
        if artifact_uid is not None and artifact_uid != os.getuid():
            raise HostStateError("production private identity owner is not the controller uid")
        self.artifact_uid = os.getuid()
        self.production_authority = True

    @classmethod
    def fixture(
        cls,
        *,
        request: Mapping[str, Any],
        ssh_path: Path,
        ssh_keygen_path: Path,
        known_hosts_path: Path,
        identity_path: Path,
        identity_public_path: Path,
        helper_path: Path,
    ) -> "SshHostStateProvider":
        """Explicit test-only construction; production controller always rejects it."""
        self = object.__new__(cls)
        self.request = validate_request(request, allow_fixture=True)
        self.ssh_path = ssh_path
        self.ssh_keygen_path = ssh_keygen_path
        self.known_hosts_path = known_hosts_path
        self.identity_path = identity_path
        self.identity_public_path = identity_public_path
        self.helper_path = helper_path
        self.plan_authority = None
        self.parity_authority = None
        self.artifact_uid = os.getuid()
        self.production_authority = False
        return self

    def prepare_launch(
        self,
        request_value: Mapping[str, Any],
        *,
        _token: object | None = None,
    ) -> Any:
        if _token is not _COMPOSITION_SEAL:
            raise HostStateError("host-state launch may only be prepared by a sealed composition")
        request = validate_request(
            request_value,
            now=datetime.now(timezone.utc),
            plan_authority=self.plan_authority,
            parity_authority=self.parity_authority,
            allow_fixture=not self.production_authority,
        )
        if request != self.request:
            raise ObservationHold("sealed host-state provider request differs")
        opened: list[int] = []
        specifications = (
            (self.ssh_path, "ssh_sha256", True, {0o555, 0o755}, 0 if self.production_authority else self.artifact_uid),
            (self.ssh_keygen_path, "ssh_keygen_sha256", True, {0o555, 0o755}, 0 if self.production_authority else self.artifact_uid),
            (self.known_hosts_path, "known_hosts_sha256", False, {0o400, 0o444}, 0 if self.production_authority else self.artifact_uid),
            (self.identity_path, "identity_sha256", False, {0o400}, self.artifact_uid),
            (self.identity_public_path, "identity_public_sha256", False, {0o444}, 0 if self.production_authority else self.artifact_uid),
            (self.helper_path, "helper_sha256", False, {0o400, 0o444}, 0 if self.production_authority else self.artifact_uid),
        )
        raw_values: list[bytes] = []
        try:
            for path, field, executable, modes, expected_uid in specifications:
                if self.production_authority:
                    _reject_symlink_ancestors(path)
                fd, raw = _held_regular(path, request["transport"][field], executable=executable)
                opened.append(fd)
                raw_values.append(raw)
                st = os.fstat(fd)
                if st.st_uid != expected_uid or st.st_nlink != 1 or stat.S_IMODE(st.st_mode) not in modes:
                    raise ObservationHold("sealed host-state artifact metadata differs")
            _known_host(raw_values[2], request["target"]["host"])
            if raw_values[4].decode("utf-8", errors="strict") != request["transport"]["identity_public"] + "\n":
                raise ObservationHold("sealed public identity artifact differs")
            rc, stdout, _stderr = _run_held_bounded(
                [f"/proc/{os.getpid()}/fd/{opened[1]}", "-y", "-f", f"/proc/{os.getpid()}/fd/{opened[3]}"],
                pass_fds=(opened[1], opened[3]),
                timeout=5,
                limit=8192,
                env=_local_process_environment(),
            )
            if rc or stdout.decode("utf-8", errors="strict").strip() != request["transport"]["identity_public"]:
                raise ObservationHold("sealed private/public identity differs")
            initial = tuple(_artifact_snapshot(fd, raw) for fd, raw in zip(opened, raw_values, strict=True))
            named = tuple(_inode_identity(os.stat(path, follow_symlinks=False)) for path, *_ in specifications)
            ssh_version: dict[str, str] | None = None
            if self.production_authority:
                version_rc, version_out, version_err = _run_held_bounded(
                    [f"/proc/{os.getpid()}/fd/{opened[0]}", "-V"],
                    pass_fds=(opened[0],),
                    timeout=5,
                    limit=8192,
                    env=_local_process_environment(),
                )
                version_raw = version_out or version_err
                if version_rc or not version_raw:
                    raise ObservationHold("sealed SSH version observation failed")
                ssh_version = {
                    "value": version_raw.decode("utf-8", errors="strict").strip(),
                    "sha256": digest(version_raw),
                    "b64": base64.b64encode(version_raw).decode("ascii"),
                }
        except Exception:
            for fd in reversed(opened):
                os.close(fd)
            raise
        used = False

        def close() -> None:
            nonlocal used
            if not used:
                used = True
                for fd in reversed(opened):
                    os.close(fd)

        def launch() -> Mapping[str, Any]:
            nonlocal used
            if used:
                raise HostStateError("sealed host-state launch is not reusable")
            used = True
            ssh_fd, _keygen_fd, hosts_fd, identity_fd, _public_fd, _helper_fd = opened
            sealed_hosts = -1
            sealed_identity = -1
            try:
                sealed_hosts = _sealed("a3-host-state-hosts", raw_values[2])
                sealed_identity = _sealed("a3-host-state-identity", raw_values[3])
                bootstrap = (
                    "ns={'__name__':'tgw_remote_helper'};exec(compile("
                    + repr(raw_values[5].decode("utf-8", errors="strict"))
                    + ",'a3-host-state-helper','exec'),ns);raise SystemExit(ns['helper_main']())"
                )
                remote = shlex.join([request["target"]["remote_python"], "-I", "-c", bootstrap])
                argv = [
                    f"/proc/{os.getpid()}/fd/{ssh_fd}",
                    "-F",
                    "/dev/null",
                    "-p",
                    str(request["target"]["port"]),
                    "-oBatchMode=yes",
                    "-oIdentitiesOnly=yes",
                    "-oIdentityAgent=none",
                    "-oClearAllForwardings=yes",
                    "-oStrictHostKeyChecking=yes",
                    "-oGlobalKnownHostsFile=/dev/null",
                    "-oCanonicalizeHostname=no",
                    "-oProxyCommand=none",
                    "-oProxyJump=none",
                    "-oPreferredAuthentications=publickey",
                    "-oKbdInteractiveAuthentication=no",
                    "-oGSSAPIAuthentication=no",
                    "-oHostbasedAuthentication=no",
                    "-oPubkeyAuthentication=yes",
                    "-oPermitLocalCommand=no",
                    "-oControlMaster=no",
                    "-oControlPath=none",
                    "-oUpdateHostKeys=no",
                    "-oVerifyHostKeyDNS=no",
                    "-oForwardAgent=no",
                    "-oForwardX11=no",
                    f"-oHostKeyAlias={request['target']['host']}",
                    f"-oUserKnownHostsFile=/proc/{os.getpid()}/fd/{sealed_hosts}",
                    f"-oIdentityFile=/proc/{os.getpid()}/fd/{sealed_identity}",
                    "-oPasswordAuthentication=no",
                    "-T",
                    f"{request['target']['user']}@{request['target']['host']}",
                    remote,
                ]
                process = subprocess.Popen(
                    argv,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                    pass_fds=(ssh_fd, sealed_hosts, sealed_identity),
                    env=_local_process_environment(),
                )
                stream_error: Exception | None = None
                try:
                    stdout, _stderr = _bounded_stream(
                        process,
                        canonical(request),
                        stdout_limit=request["bounds"]["max_output_bytes"] + 8,
                        stderr_limit=request["bounds"]["max_diagnostic_bytes"],
                        timeout=request["bounds"]["timeout_seconds"],
                    )
                except Exception as exc:
                    stream_error = exc
                    stdout = b""
                state = _group_empty_or_kill(process.pid)
                try:
                    process.wait(timeout=1)
                    state["reaped"] = True
                except subprocess.TimeoutExpired:
                    state["reaped"] = False
                state = _post_reap_group_state(process.pid, state)
                if stream_error is not None:
                    raise HostStateDispatchAmbiguous("SSH stream failed after dispatch") from stream_error
                if process.returncode != 0 or state.get("had_survivor") or not state.get("removed") or not state.get("reaped"):
                    raise HostStateDispatchAmbiguous("SSH helper or process-group lifecycle differs")
                return decode_helper_response(stdout, request, now=datetime.now(timezone.utc))
            finally:
                post_error: Exception | None = None
                try:
                    for index, (fd, raw) in enumerate(zip(opened, raw_values, strict=True)):
                        before_inode, before_hash = initial[index]
                        if _inode_identity(os.fstat(fd)) != before_inode or digest(os.pread(fd, len(raw) + 1, 0)) != before_hash:
                            post_error = HostStateDispatchAmbiguous("held SSH artifact changed")
                    for index, (path, *_rest) in enumerate(specifications):
                        if _inode_identity(os.stat(path, follow_symlinks=False)) != named[index]:
                            post_error = HostStateDispatchAmbiguous("named SSH artifact changed")
                except Exception as exc:
                    post_error = HostStateDispatchAmbiguous("SSH artifact postcheck failed")
                    post_error.__cause__ = exc
                for fd in (sealed_identity, sealed_hosts, *reversed(opened)):
                    if fd >= 0:
                        os.close(fd)
                if post_error is not None:
                    raise post_error

        launch.close = close  # type: ignore[attr-defined]
        launch.artifact_evidence = {  # type: ignore[attr-defined]
            field: {
                "path": str(path),
                "sha256": request["transport"][field],
                "identity": list(initial[index][0]),
            }
            for index, (path, field, _executable, _modes, _uid) in enumerate(specifications)
        }
        launch.ssh_version = ssh_version  # type: ignore[attr-defined]
        return launch


class HostStateProductionComposition:
    __slots__ = ("provider", "evidence_store", "launch", "authority_check", "value", "receipt_sha256", "used")

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise TypeError("HostStateProductionComposition is sealed")

    def __init__(
        self,
        *,
        provider: SshHostStateProvider,
        token_root_identity: Mapping[str, Any],
        evidence_store: HostStateEvidenceStore,
        _token: object,
    ) -> None:
        if (
            _token is not _COMPOSITION_SEAL
            or type(provider) is not SshHostStateProvider
            or provider.production_authority is not True
            or type(provider.plan_authority) is not MountedHostStatePlanAuthority
            or type(provider.parity_authority) is not MountedSshdParityAuthority
            or type(evidence_store) is not HostStateEvidenceStore
        ):
            raise HostStateError("production host-state composition is not sealed")
        fixed_token_root = _validate_production_private_root(
            token_root_identity,
            "production token root",
            trusted_uid=os.getuid(),
        )
        if evidence_store._store.trusted_uid != os.getuid():
            raise HostStateError("production evidence root owner is not the controller uid")
        fixed_evidence_root = _validate_production_private_root(
            evidence_store.identity,
            "production evidence root",
            trusted_uid=os.getuid(),
        )
        prepared_launch = provider.prepare_launch(
            provider.request,
            _token=_COMPOSITION_SEAL,
        )
        authority_records = (
            (
                provider.plan_authority.fd,
                provider.plan_authority.path,
                provider.plan_authority.identity,
                provider.plan_authority.sha256,
            ),
            (
                provider.parity_authority.fd,
                provider.parity_authority.path,
                provider.parity_authority.identity,
                provider.parity_authority.sha256,
            ),
            *tuple(
                (
                    provider.parity_authority.evidence_fds[role],
                    Path(ref["path"]),
                    provider.parity_authority.evidence_identities[role],
                    ref["sha256"],
                )
                for role, ref in provider.parity_authority.receipt["evidence"].items()
            ),
        )
        authorities_closed = False

        def close_authorities() -> None:
            nonlocal authorities_closed
            if not authorities_closed:
                authorities_closed = True
                for fd, _path, _identity, _sha256 in reversed(authority_records):
                    os.close(fd)

        def postcheck_authorities() -> None:
            errors: list[Exception] = []
            for fd, path, initial_identity, expected_sha256 in authority_records:
                try:
                    held = os.fstat(fd)
                    named = os.stat(path, follow_symlinks=False)
                    raw = os.pread(fd, held.st_size + 1, 0)
                    if _inode_identity(held) != initial_identity or _inode_identity(named) != initial_identity or digest(raw) != expected_sha256:
                        raise HostStateError("mounted authority changed")
                except Exception as exc:
                    errors.append(exc)
            if errors:
                raise HostStateError("mounted authority identity changed") from errors[0]

        def launch() -> Mapping[str, Any]:
            try:
                return prepared_launch()
            finally:
                post_error: Exception | None = None
                try:
                    postcheck_authorities()
                except Exception as exc:
                    post_error = HostStateDispatchAmbiguous("mounted authority postcheck failed")
                    post_error.__cause__ = exc
                close_authorities()
                if post_error is not None:
                    raise post_error

        def close_launch() -> None:
            try:
                prepared_launch.close()
            finally:
                close_authorities()

        launch.close = close_launch  # type: ignore[attr-defined]
        launch.artifact_evidence = prepared_launch.artifact_evidence  # type: ignore[attr-defined]
        launch.ssh_version = prepared_launch.ssh_version  # type: ignore[attr-defined]
        artifacts = dict(launch.artifact_evidence)
        value = {
            "schema": COMPOSITION_SCHEMA,
            "status": "EXECUTABLE",
            "request_sha256": provider.request["request_sha256"],
            "plan_authority": {
                "path": str(provider.plan_authority.path),
                "sha256": provider.plan_authority.sha256,
                "identity": list(provider.plan_authority.identity),
            },
            "sshd_parity_authority": {
                "path": str(provider.parity_authority.path),
                "sha256": provider.parity_authority.sha256,
                "identity": list(provider.parity_authority.identity),
                "evidence_identities": {role: list(identity) for role, identity in provider.parity_authority.evidence_identities.items()},
            },
            "artifacts": artifacts,
            "ssh_version": launch.ssh_version,
            "ssh_argv_policy": _ssh_argv_policy(provider.request),
            "local_process_environment": _local_process_environment(),
            "token_root_identity": fixed_token_root,
            "evidence_root_identity": fixed_evidence_root,
        }
        receipt_sha256 = _hash(value)
        _validate_composition_value(
            value,
            provider.request,
            expected_sha256=receipt_sha256,
            expected_evidence_root=evidence_store.identity,
        )
        self.provider = provider
        self.evidence_store = evidence_store
        self.launch = launch
        self.authority_check = postcheck_authorities
        self.value = value
        self.receipt_sha256 = receipt_sha256
        self.used = False

    def take_launch(self) -> Any:
        if self.used:
            raise HostStateError("production host-state composition is already used")
        self.used = True
        return self.launch

    def close(self) -> None:
        if not self.used:
            self.used = True
            self.launch.close()


def build_host_state_production_composition(
    *,
    provider: SshHostStateProvider,
    token_root_identity: Mapping[str, Any],
    evidence_store: HostStateEvidenceStore,
) -> HostStateProductionComposition:
    return HostStateProductionComposition(
        provider=provider,
        token_root_identity=token_root_identity,
        evidence_store=evidence_store,
        _token=_COMPOSITION_SEAL,
    )


def _validate_persistence_context(
    value: Any,
    *,
    request: Mapping[str, Any],
    expected_composition_sha256: str,
    evidence_root: Mapping[str, Any],
    expected_grant_authority: Mapping[str, Any],
    persistence_terminal: Mapping[str, Any],
) -> dict[str, Any]:
    fields = {
        "schema",
        "status",
        "attempt_name",
        "attempt_path",
        "attempted_receipt",
        "original_terminal",
        "observed_receipt",
        "observed_dependency",
        "attempted_attachments",
        "expected_items",
        "context_sha256",
    }
    context = dict(_exact(value, fields, "host-state persistence context"))
    claimed = context.pop("context_sha256")
    if context["schema"] != PERSISTENCE_CONTEXT_SCHEMA or context["status"] != "UNVERIFIED_NOT_DURABLE" or claimed != _hash(context):
        raise HostStateError("host-state persistence context identity differs")
    context["context_sha256"] = claimed
    original = validate_terminal(
        context["original_terminal"],
        request_sha256=request["request_sha256"],
    )
    original_tuple = (
        original["outcome"],
        original["stage"],
        original["code"],
        original["dispatched"],
    )
    if original_tuple not in {
        ("PASS", "complete", "NONE", True),
        ("HOLD", "remote", "HOST_NOT_READY", True),
        ("FAILED", "remote", "HELPER_FAILED", True),
        ("AMBIGUOUS", "authority", "TOKEN_UNCERTAIN", False),
        ("AMBIGUOUS", "dispatch", "DISPATCH_UNCERTAIN", True),
    }:
        raise HostStateError("host-state persistence origin is not controller-produced")
    if original["dispatched"] is not persistence_terminal["dispatched"]:
        raise HostStateError("host-state persistence origin dispatch differs")
    attachments = context["attempted_attachments"]
    if not isinstance(attachments, Mapping):
        raise HostStateError("host-state persistence attachments differ")
    common_names = {
        "terminal.json",
        "grant.json",
        "grant-authority.json",
        "token-root.json",
        "composition.json",
    }
    expected_names = common_names | ({"dependency.json"} if original["outcome"] == "PASS" else set())
    if set(attachments) != expected_names or attachments["terminal.json"] != original:
        raise HostStateError("host-state persistence attachment set differs")
    grant = HostStateObservationGrant.validate(attachments["grant.json"], request=request)
    grant_observation = _validate_grant_observation(
        attachments["grant-authority.json"],
        grant=grant,
        expected_authority=expected_grant_authority,
    )
    token_root = _root_identity(attachments["token-root.json"], "persistence token root")
    composition = _validate_composition_value(
        attachments["composition.json"],
        request,
        expected_sha256=expected_composition_sha256,
        expected_evidence_root=evidence_root,
    )
    if (
        grant["composition_sha256"] != expected_composition_sha256
        or not _same_root_authority(grant["evidence_root_identity"], evidence_root)
        or not _same_root_authority(token_root, grant["token_root_identity"])
        or not _same_root_authority(token_root, composition["token_root_identity"])
    ):
        raise HostStateError("host-state persistence authority differs")
    if original["outcome"] != "AMBIGUOUS" and grant_observation["postcheck"] != "PASS":
        raise HostStateError("host-state persistence grant was not stable")
    attempted_receipt = context["attempted_receipt"]
    if not isinstance(attempted_receipt, Mapping):
        raise HostStateError("host-state persistence attempted receipt differs")
    if original["outcome"] == "PASS":
        observed_receipt = validate_receipt(context["observed_receipt"], request)
        dependency = context["observed_dependency"]
        if (
            attempted_receipt != observed_receipt
            or not isinstance(dependency, Mapping)
            or attachments["dependency.json"] != dependency
            or dependency
            != dependency_projection(
                observed_receipt,
                request,
                ssh_sha256=request["transport"]["ssh_sha256"],
                descriptor_sha256=grant["composition_sha256"],
            )
        ):
            raise HostStateError("host-state persistence success origin differs")
    else:
        if context["observed_receipt"] is not None or context["observed_dependency"] is not None:
            raise HostStateError("host-state persistence terminal origin differs")
        expected_failure = {
            "schema": "tgw-prod-a3-host-state-observation-failure-evidence/v1",
            "request_sha256": request["request_sha256"],
            "terminal_sha256": original["terminal_sha256"],
            "composition_sha256": grant["composition_sha256"],
        }
        expected_failure["receipt_sha256"] = _hash(expected_failure)
        if attempted_receipt != expected_failure:
            raise HostStateError("host-state persistence failure origin differs")
    request_raw = canonical(request)
    receipt_raw = canonical(attempted_receipt)
    attachment_raw = {name: canonical(item) for name, item in sorted(attachments.items())}
    manifest = {
        "request_sha256": digest(request_raw),
        "receipt_sha256": digest(receipt_raw),
        "archive_sha256": digest(b""),
        "archive_size": 0,
        "attachments": {name: {"sha256": digest(raw), "size": len(raw)} for name, raw in attachment_raw.items()},
    }
    item_raw = {
        "request.json": request_raw,
        "receipt.json": receipt_raw,
        "archive.tar": b"",
        **attachment_raw,
        "manifest.json": canonical(manifest),
    }
    expected_items = {name: {"sha256": digest(raw), "size": len(raw)} for name, raw in sorted(item_raw.items())}
    attempt_name = str(attempted_receipt["receipt_sha256"]).split(":", 1)[1]
    root = Path(str(evidence_root["path"]))
    if context["attempt_name"] != attempt_name or context["attempt_path"] != str(root / attempt_name) or context["expected_items"] != expected_items:
        raise HostStateError("host-state persistence attempt identity differs")
    return context


class HostStateObservationController:
    def execute(
        self,
        *,
        request: Mapping[str, Any],
        composition: HostStateProductionComposition,
        grant: MountedHostStateObservationGrant,
        token: DurableObservationToken,
    ) -> dict[str, Any]:
        cleanup_errors: list[Exception] = []
        try:
            if type(grant) is not MountedHostStateObservationGrant:
                raise HostStateError("production host-state grant is not mounted")
            if type(token) is not DurableObservationToken:
                raise HostStateError("production host-state token is not sealed")
            return self._execute(
                request=request,
                composition=composition,
                grant=grant,
                token=token,
            )
        finally:
            if type(composition) is HostStateProductionComposition:
                try:
                    composition.close()
                except Exception as exc:
                    cleanup_errors.append(exc)
            try:
                if type(composition) is HostStateProductionComposition and type(composition.evidence_store) is HostStateEvidenceStore:
                    composition.evidence_store.close()
            except Exception as exc:
                cleanup_errors.append(exc)
            try:
                if type(token) is DurableObservationToken:
                    token.close()
            except Exception as exc:
                cleanup_errors.append(exc)
            try:
                if type(grant) is MountedHostStateObservationGrant:
                    grant.close()
            except Exception as exc:
                cleanup_errors.append(exc)
            if cleanup_errors:
                try:
                    raise HostStateDispatchAmbiguous("host-state controller authority cleanup is uncertain") from cleanup_errors[0]
                finally:
                    cleanup_errors.clear()

    def _execute(
        self,
        *,
        request: Mapping[str, Any],
        composition: HostStateProductionComposition,
        grant: MountedHostStateObservationGrant,
        token: DurableObservationToken,
    ) -> dict[str, Any]:
        if type(composition) is not HostStateProductionComposition:
            raise HostStateError("production host-state composition is not sealed")
        if type(composition.evidence_store) is not HostStateEvidenceStore:
            raise HostStateError("production host-state evidence store is not sealed")
        try:
            provider = composition.provider
            evidence_store = composition.evidence_store
            composition_sha256 = composition.receipt_sha256
            observed = datetime.now(timezone.utc)
            validated = validate_request(
                request,
                now=observed,
                plan_authority=provider.plan_authority,
                parity_authority=provider.parity_authority,
            )
            if type(provider) is not SshHostStateProvider or provider.production_authority is not True:
                raise HostStateError("production host-state provider is not sealed")
            if composition.value["request_sha256"] != validated["request_sha256"]:
                raise HostStateError("host-state composition request differs")
            _validate_composition_value(
                composition.value,
                validated,
                expected_sha256=composition_sha256,
                expected_evidence_root=evidence_store.identity,
            )
            composition.authority_check()
            fixed_grant = grant.read(request=validated, now=observed)
            expected_grant_authority = grant.authority
            if fixed_grant["composition_sha256"] != composition_sha256:
                raise HostStateError("host-state grant composition differs")
            if (
                not token.ready()
                or token.grant_sha256 != fixed_grant["grant_sha256"]
                or token.identity != fixed_grant["token_root_identity"]
                or token.identity != composition.value["token_root_identity"]
            ):
                raise HostStateError("host-state token authority differs")
            if (
                not evidence_store.ready()
                or not _same_root_authority(evidence_store.identity, fixed_grant["evidence_root_identity"])
                or not _same_root_authority(evidence_store.identity, composition.value["evidence_root_identity"])
            ):
                raise HostStateError("host-state evidence store is not ready")
        except Exception:
            composition.close()
            raise
        launch = composition.take_launch()

        def persistence_result(exc: HostStatePersistenceAmbiguous) -> dict[str, Any]:
            persistence_terminal = validate_terminal(
                exc.terminal,
                request_sha256=validated["request_sha256"],
            )
            persistence_tuple = (
                persistence_terminal["outcome"],
                persistence_terminal["stage"],
                persistence_terminal["code"],
                persistence_terminal["dispatched"],
            )
            if persistence_tuple not in {
                ("AMBIGUOUS", "persistence", "PERSISTENCE_UNCERTAIN", False),
                ("AMBIGUOUS", "persistence", "PERSISTENCE_UNCERTAIN", True),
            }:
                raise HostStateError("host-state persistence terminal differs")
            result = {
                "schema": RESULT_SCHEMA,
                "composition_sha256": composition_sha256,
                "evidence_root_identity": evidence_store.identity,
                "terminal": persistence_terminal,
                "receipt": None,
                "dependency": None,
                "evidence": [],
                "persistence": exc.context,
            }
            result["result_sha256"] = _hash(result)
            validate_result(
                result,
                validated,
                expected_composition_sha256=composition_sha256,
                expected_evidence_root_identity=evidence_store.identity,
                expected_grant_authority=expected_grant_authority,
            )
            return result

        def grant_observation(*, require_pass: bool) -> dict[str, Any]:
            observation = grant.observation()
            _validate_grant_observation(
                observation,
                grant=fixed_grant,
                expected_authority=expected_grant_authority,
            )
            if require_pass and observation["postcheck"] != "PASS":
                raise HostStateDispatchAmbiguous("mounted host-state grant changed after dispatch")
            return observation

        try:
            token.consume()
        except ObservationAlreadyConsumed:
            launch.close()
            raise
        except ObservationTokenPersistenceAmbiguous as exc:
            launch.close()
            ambiguous = terminal(
                outcome="AMBIGUOUS",
                stage="authority",
                code="TOKEN_UNCERTAIN",
                dispatched=False,
                request_sha256=validated["request_sha256"],
                observed_at=observed.isoformat(),
                diagnostic=type(exc).__name__.encode(),
            )
            try:
                authority_observation = grant_observation(require_pass=False)
                evidence = evidence_store.persist_terminal(
                    request=validated,
                    terminal_value=ambiguous,
                    grant=fixed_grant,
                    grant_observation=authority_observation,
                    token_identity=token.identity,
                    composition_sha256=composition_sha256,
                    composition_value=composition.value,
                    before_publish=lambda: grant.assert_observation(authority_observation),
                )
            except HostStatePersistenceAmbiguous as persistence_exc:
                return persistence_result(persistence_exc)
            result = {
                "schema": RESULT_SCHEMA,
                "composition_sha256": composition_sha256,
                "evidence_root_identity": evidence_store.identity,
                "terminal": ambiguous,
                "receipt": None,
                "dependency": None,
                "evidence": list(evidence),
                "persistence": None,
            }
            result["result_sha256"] = _hash(result)
            validate_result(
                result,
                validated,
                expected_composition_sha256=composition_sha256,
                expected_evidence_root_identity=evidence_store.identity,
                expected_grant_authority=expected_grant_authority,
            )
            return result
        except Exception as exc:
            try:
                launch.close()
            except Exception as close_exc:
                raise HostStateDispatchAmbiguous("host-state predispatch cleanup is uncertain") from close_exc
            raise ObservationHold("host-state authority could not be consumed before dispatch") from exc

        try:
            untrusted = launch()
            if untrusted.get("schema") == TERMINAL_SCHEMA:
                remote_terminal = validate_terminal(untrusted, request_sha256=validated["request_sha256"])
                if (
                    remote_terminal["outcome"],
                    remote_terminal["stage"],
                    remote_terminal["code"],
                    remote_terminal["dispatched"],
                ) not in _REMOTE_TERMINALS:
                    raise HostStateError("host-state launch returned a non-remote terminal")
                authority_observation = grant_observation(require_pass=True)
                evidence = evidence_store.persist_terminal(
                    request=validated,
                    terminal_value=remote_terminal,
                    grant=fixed_grant,
                    grant_observation=authority_observation,
                    token_identity=token.identity,
                    composition_sha256=composition_sha256,
                    composition_value=composition.value,
                    before_publish=lambda: grant.assert_observation(authority_observation),
                )
                result = {
                    "schema": RESULT_SCHEMA,
                    "composition_sha256": composition_sha256,
                    "evidence_root_identity": evidence_store.identity,
                    "terminal": remote_terminal,
                    "receipt": None,
                    "dependency": None,
                    "evidence": list(evidence),
                    "persistence": None,
                }
                result["result_sha256"] = _hash(result)
                validate_result(
                    result,
                    validated,
                    expected_composition_sha256=composition_sha256,
                    expected_evidence_root_identity=evidence_store.identity,
                    expected_grant_authority=expected_grant_authority,
                )
                return result
            receipt = validate_receipt(untrusted, validated, now=datetime.now(timezone.utc))
            authority_observation = grant_observation(require_pass=True)
        except HostStatePersistenceAmbiguous as exc:
            return persistence_result(exc)
        except Exception as exc:
            ambiguous = terminal(
                outcome="AMBIGUOUS",
                stage="dispatch",
                code="DISPATCH_UNCERTAIN",
                dispatched=True,
                request_sha256=validated["request_sha256"],
                observed_at=datetime.now(timezone.utc).isoformat(),
                diagnostic=type(exc).__name__.encode(),
            )
            try:
                authority_observation = grant_observation(require_pass=False)
                evidence = evidence_store.persist_terminal(
                    request=validated,
                    terminal_value=ambiguous,
                    grant=fixed_grant,
                    grant_observation=authority_observation,
                    token_identity=token.identity,
                    composition_sha256=composition_sha256,
                    composition_value=composition.value,
                    before_publish=lambda: grant.assert_observation(authority_observation),
                )
            except HostStatePersistenceAmbiguous as persistence_exc:
                return persistence_result(persistence_exc)
            result = {
                "schema": RESULT_SCHEMA,
                "composition_sha256": composition_sha256,
                "evidence_root_identity": evidence_store.identity,
                "terminal": ambiguous,
                "receipt": None,
                "dependency": None,
                "evidence": list(evidence),
                "persistence": None,
            }
            result["result_sha256"] = _hash(result)
            validate_result(
                result,
                validated,
                expected_composition_sha256=composition_sha256,
                expected_evidence_root_identity=evidence_store.identity,
                expected_grant_authority=expected_grant_authority,
            )
            return result
        success = terminal(
            outcome="PASS",
            stage="complete",
            code="NONE",
            dispatched=True,
            request_sha256=validated["request_sha256"],
            observed_at=receipt["observed_at"],
        )
        dependency = dependency_projection(
            receipt,
            validated,
            ssh_sha256=validated["transport"]["ssh_sha256"],
            descriptor_sha256=composition_sha256,
        )
        try:
            evidence = evidence_store.persist(
                request=validated,
                receipt=receipt,
                terminal_value=success,
                grant=fixed_grant,
                grant_observation=authority_observation,
                token_identity=token.identity,
                dependency=dependency,
                composition_value=composition.value,
                before_publish=lambda: grant.assert_observation(authority_observation),
            )
        except HostStatePersistenceAmbiguous as exc:
            return persistence_result(exc)
        result = {
            "schema": RESULT_SCHEMA,
            "composition_sha256": composition_sha256,
            "evidence_root_identity": evidence_store.identity,
            "terminal": success,
            "receipt": receipt,
            "dependency": dependency,
            "evidence": list(evidence),
            "persistence": None,
        }
        result["result_sha256"] = _hash(result)
        validate_result(
            result,
            validated,
            expected_composition_sha256=composition_sha256,
            expected_evidence_root_identity=evidence_store.identity,
            expected_grant_authority=expected_grant_authority,
        )
        return result


def validate_result(
    value: Any,
    request_value: Mapping[str, Any],
    *,
    expected_composition_sha256: str,
    expected_evidence_root_identity: Mapping[str, Any],
    expected_grant_authority: Mapping[str, Any],
) -> dict[str, Any]:
    request = validate_request(request_value, allow_fixture=True)
    result = dict(
        _exact(
            value,
            {
                "schema",
                "composition_sha256",
                "evidence_root_identity",
                "terminal",
                "receipt",
                "dependency",
                "evidence",
                "persistence",
                "result_sha256",
            },
            "host-state result",
        )
    )
    if result["schema"] != RESULT_SCHEMA:
        raise HostStateError("host-state result schema differs")
    claimed_result_sha256 = result.pop("result_sha256")
    if claimed_result_sha256 != _hash(result):
        raise HostStateError("host-state result hash differs")
    result["result_sha256"] = claimed_result_sha256
    if result["composition_sha256"] != expected_composition_sha256:
        raise HostStateError("host-state result composition differs")
    if not isinstance(expected_composition_sha256, str) or not _SHA.fullmatch(expected_composition_sha256):
        raise HostStateError("expected host-state composition is invalid")
    if not _same_root_authority(
        _root_identity(result["evidence_root_identity"], "evidence root"),
        _root_identity(expected_evidence_root_identity, "evidence root"),
    ):
        raise HostStateError("host-state result evidence root differs")
    terminal_value = validate_terminal(result["terminal"], request_sha256=request["request_sha256"])
    produced_terminal = (
        terminal_value["outcome"],
        terminal_value["stage"],
        terminal_value["code"],
        terminal_value["dispatched"],
    )
    if produced_terminal not in {
        ("PASS", "complete", "NONE", True),
        ("HOLD", "remote", "HOST_NOT_READY", True),
        ("FAILED", "remote", "HELPER_FAILED", True),
        ("AMBIGUOUS", "authority", "TOKEN_UNCERTAIN", False),
        ("AMBIGUOUS", "dispatch", "DISPATCH_UNCERTAIN", True),
        ("AMBIGUOUS", "persistence", "PERSISTENCE_UNCERTAIN", False),
        ("AMBIGUOUS", "persistence", "PERSISTENCE_UNCERTAIN", True),
    }:
        raise HostStateError("host-state result terminal is not controller-produced")
    is_persistence_uncertain = produced_terminal in {
        ("AMBIGUOUS", "persistence", "PERSISTENCE_UNCERTAIN", False),
        ("AMBIGUOUS", "persistence", "PERSISTENCE_UNCERTAIN", True),
    }
    if is_persistence_uncertain:
        if result["evidence"]:
            raise HostStateError("persistence uncertainty claims durable evidence")
        _validate_persistence_context(
            result["persistence"],
            request=request,
            expected_composition_sha256=expected_composition_sha256,
            evidence_root=expected_evidence_root_identity,
            expected_grant_authority=expected_grant_authority,
            persistence_terminal=terminal_value,
        )
    elif result["persistence"] is not None:
        raise HostStateError("non-persistence result has persistence context")
    persistence_uncertain_without_refs = is_persistence_uncertain and not result["evidence"]
    root = Path(str(expected_evidence_root_identity["path"]))
    if not persistence_uncertain_without_refs:
        _reject_symlink_ancestors(root)
        current_root = os.stat(root, follow_symlinks=False)
        current_root_identity = {
            "path": str(root),
            "uid": current_root.st_uid,
            "gid": current_root.st_gid,
            "mode": stat.S_IMODE(current_root.st_mode),
            "dev": current_root.st_dev,
            "ino": current_root.st_ino,
            "nlink": current_root.st_nlink,
        }
        if not _same_root_authority(current_root_identity, expected_evidence_root_identity):
            raise HostStateError("host-state evidence root current identity differs")
    refs, evidence_contents = _validate_evidence_refs(result["evidence"]) if result["evidence"] else ([], {})
    if any(Path(ref["path"]).parent.parent != root for ref in refs):
        raise HostStateError("host-state evidence ref is outside the admitted root")
    if refs:
        try:
            persisted_request = json.loads(evidence_contents["request.json"])
            persisted_terminal = json.loads(evidence_contents["terminal.json"])
            persisted_grant = HostStateObservationGrant.validate(json.loads(evidence_contents["grant.json"]), request=request)
            persisted_grant_observation = _validate_grant_observation(
                json.loads(evidence_contents["grant-authority.json"]),
                grant=persisted_grant,
                expected_authority=expected_grant_authority,
            )
            persisted_token_root = _root_identity(json.loads(evidence_contents["token-root.json"]), "persisted token root")
            persisted_composition = json.loads(evidence_contents["composition.json"])
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HostStateError("host-state evidence bundle is malformed") from exc
        if (
            persisted_request != request
            or persisted_terminal != terminal_value
            or persisted_grant["composition_sha256"] != expected_composition_sha256
            or not _same_root_authority(
                persisted_grant["evidence_root_identity"],
                expected_evidence_root_identity,
            )
            or not _same_root_authority(
                persisted_token_root,
                persisted_grant["token_root_identity"],
            )
        ):
            raise HostStateError("host-state durable authority evidence differs")
        if terminal_value["outcome"] != "AMBIGUOUS" and persisted_grant_observation["postcheck"] != "PASS":
            raise HostStateError("host-state durable grant was not stable")
        _validate_composition_value(
            persisted_composition,
            request,
            expected_sha256=expected_composition_sha256,
            expected_evidence_root=expected_evidence_root_identity,
        )
    if terminal_value["outcome"] == "PASS":
        receipt = validate_receipt(result["receipt"], request)
        if not isinstance(result["dependency"], Mapping) or result["dependency"].get("descriptor_sha256") != expected_composition_sha256:
            raise HostStateError("host-state dependency composition differs")
        if result["dependency"] != dependency_projection(
            receipt,
            request,
            ssh_sha256=request["transport"]["ssh_sha256"],
            descriptor_sha256=result["dependency"]["descriptor_sha256"],
        ):
            raise HostStateError("host-state dependency projection differs")
        if not refs:
            raise HostStateError("PASS host-state result lacks durable evidence")
        try:
            persisted_receipt = json.loads(evidence_contents["receipt.json"])
            persisted_dependency = json.loads(evidence_contents["dependency.json"])
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HostStateError("PASS host-state evidence is incomplete") from exc
        if persisted_receipt != receipt or persisted_dependency != result["dependency"] or evidence_contents["archive.tar"] != b"":
            raise HostStateError("PASS host-state durable evidence differs")
    elif result["receipt"] is not None or result["dependency"] is not None:
        raise HostStateError("non-PASS host-state result has success receipt")
    elif terminal_value["dispatched"] and not result["evidence"] and not persistence_uncertain_without_refs:
        raise HostStateError("post-dispatch host-state result lacks durable evidence")
    elif refs:
        try:
            failure = json.loads(evidence_contents["receipt.json"])
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HostStateError("terminal host-state evidence is incomplete") from exc
        expected_failure = {
            "schema": "tgw-prod-a3-host-state-observation-failure-evidence/v1",
            "request_sha256": request["request_sha256"],
            "terminal_sha256": terminal_value["terminal_sha256"],
            "composition_sha256": expected_composition_sha256,
        }
        expected_failure["receipt_sha256"] = _hash(expected_failure)
        if failure != expected_failure or evidence_contents["archive.tar"] != b"" or "dependency.json" in evidence_contents:
            raise HostStateError("terminal host-state durable evidence differs")
    return result
