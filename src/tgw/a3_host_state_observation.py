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
PLAN_AUTHORITY_SCHEMA = "tgw-prod-a3-host-state-plan-authority/v1"
PARITY_SCHEMA = "tgw-prod-a3-host-state-sshd-parity/v1"
GRANT_SCHEMA = "tgw-prod-a3-host-state-observation-grant/v1"
DEPENDENCY_SCHEMA = "tgw-prod-a3-host-state-observation-dependency/v1"
COMPOSITION_SCHEMA = "tgw-prod-a3-host-state-observation-composition/v1"

_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
_STORE = re.compile(r"^/nix/store/[0-9a-z]{32}-[A-Za-z0-9+._?=-]+$")
_OPERATION = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_PLAN_SEAL = object()
_PARITY_SEAL = object()


class HostStateError(ObservationError):
    pass


class HostStatePersistenceAmbiguous(HostStateError):
    def __init__(self, terminal_value: Mapping[str, Any]):
        super().__init__("host-state evidence persistence is ambiguous")
        self.terminal = dict(terminal_value)


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
    __slots__ = ("authority", "fd", "identity", "sha256")

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


def load_plan_authority(path: Path, expected_sha256: str) -> MountedHostStatePlanAuthority:
    return MountedHostStatePlanAuthority(path, expected_sha256, _token=_PLAN_SEAL)


def validate_sshd_parity(value: Any) -> dict[str, Any]:
    fields = {
        "schema",
        "status",
        "ssh_sha256",
        "identity_public",
        "known_hosts_sha256",
        "correct_key",
        "wrong_key_rejected",
        "default_key_rejected",
        "agent_rejected",
        "ambient_config_rejected",
        "framing_verified",
        "process_group_verified",
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
    for field in ("ssh_sha256", "known_hosts_sha256"):
        if not isinstance(receipt[field], str) or not _SHA.fullmatch(receipt[field]):
            raise HostStateError(f"sshd parity {field} is invalid")
    if not isinstance(receipt["identity_public"], str) or len(receipt["identity_public"].split()) < 2:
        raise HostStateError("sshd parity identity is invalid")
    if not isinstance(receipt["evidence"], list) or not receipt["evidence"] or not all(isinstance(item, str) and item for item in receipt["evidence"]):
        raise HostStateError("sshd parity evidence is absent")
    claimed = receipt.pop("receipt_sha256")
    if claimed != _hash(receipt):
        raise HostStateError("sshd parity receipt hash differs")
    receipt["receipt_sha256"] = claimed
    return receipt


class MountedSshdParityAuthority:
    __slots__ = ("receipt", "fd", "identity", "sha256")

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise TypeError("MountedSshdParityAuthority is sealed")

    def __init__(self, path: Path, expected_sha256: str, *, _token: object) -> None:
        if _token is not _PARITY_SEAL or path.name != expected_sha256.removeprefix("sha256:") + ".json":
            raise HostStateError("sshd parity locator is not content addressed")
        fd, raw = _protected_file(path, expected_sha256)
        try:
            receipt = validate_sshd_parity(json.loads(raw))
        except Exception:
            os.close(fd)
            raise
        self.receipt = receipt
        self.fd = fd
        self.identity = _inode_identity(os.fstat(fd))
        self.sha256 = expected_sha256


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
        "helper_sha256",
    }
    transport = _exact(request["transport"], transport_fields, "host-state transport")
    if any(not isinstance(transport[key], str) or not _SHA.fullmatch(transport[key]) for key in transport if key != "identity_public"):
        raise HostStateError("host-state transport identity is invalid")
    if not isinstance(transport["identity_public"], str) or len(transport["identity_public"].split()) < 2:
        raise HostStateError("host-state public identity is invalid")
    prerequisites = _exact(request["prerequisites"], {"sshd_parity_sha256", "sshd_parity_receipt_sha256"}, "host-state prerequisites")
    if any(not isinstance(item, str) or not _SHA.fullmatch(item) for item in prerequisites.values()):
        raise HostStateError("host-state prerequisite identity is invalid")
    mounted_parity = parity_authority.receipt if type(parity_authority) is MountedSshdParityAuthority else parity_authority
    if mounted_parity is None and not allow_fixture:
        raise HostStateError("production host-state request has no mounted sshd parity authority")
    if mounted_parity is not None:
        parity = validate_sshd_parity(mounted_parity)
        expected = {
            "sshd_parity_sha256": parity_authority.sha256 if type(parity_authority) is MountedSshdParityAuthority else digest(canonical(parity)),
            "sshd_parity_receipt_sha256": parity["receipt_sha256"],
        }
        if dict(prerequisites) != expected:
            raise HostStateError("host-state sshd parity binding differs")
        if parity["ssh_sha256"] != transport["ssh_sha256"] or parity["known_hosts_sha256"] != transport["known_hosts_sha256"] or parity["identity_public"] != transport["identity_public"]:
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


def _symlink_observation(path: Path) -> tuple[os.stat_result, str]:
    before = os.lstat(path)
    if not stat.S_ISLNK(before.st_mode):
        raise ObservationHold(f"{path} is not a symlink")
    target = os.readlink(path)
    if not _STORE.fullmatch(target):
        raise ObservationHold(f"{path} target is not a Nix store path")
    after = os.lstat(path)
    if _inode_identity(before) != _inode_identity(after) or os.readlink(path) != target:
        raise HostStateError(f"{path} changed while observed")
    return before, target


def _read_branch(repository: Path, expected: str, *, logical_path: str | None = None) -> tuple[dict[str, Any], tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    repo_st = os.lstat(repository)
    git_st = os.lstat(repository / ".git")
    if not stat.S_ISDIR(repo_st.st_mode) or stat.S_ISLNK(repo_st.st_mode) or not stat.S_ISDIR(git_st.st_mode) or stat.S_ISLNK(git_st.st_mode):
        raise ObservationHold("production repository or .git is not a direct directory")
    head_path = repository / ".git/HEAD"
    fd = os.open(head_path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
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
            },
            _inode_identity(repo_st),
            _inode_identity(git_st),
            _inode_identity(head_st),
        )
    finally:
        os.close(fd)


def _tool_version(fd: int, label: str) -> tuple[str, str]:
    rc, stdout, stderr = _run_held_bounded([f"/proc/{os.getpid()}/fd/{fd}", "--version"], pass_fds=(fd,), timeout=10, limit=65536)
    raw = stdout or stderr
    if rc or not raw or len(raw) > 65536:
        raise HostStateError(f"{label} version observation failed")
    value = raw.decode("utf-8", errors="strict").strip()
    return value, digest(raw)


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
    current_st, current = _symlink_observation(current_system)
    profile_st, profile = _symlink_observation(system_profile)
    if current != profile:
        raise ObservationHold("current-system and system profile CAS differ")
    expected_python = python_path or Path(request["target"]["remote_python"])
    expected_git = git_path or Path(request["target"]["remote_git"])
    python_fd, _python_raw, python_identity = _trusted_executable(expected_python, trusted_uid=trusted_uid)
    git_fd, _git_raw, git_identity = _trusted_executable(expected_git, trusted_uid=trusted_uid)
    try:
        proc_self = os.stat("/proc/self/exe")
        if (proc_self.st_dev, proc_self.st_ino) != (os.fstat(python_fd).st_dev, os.fstat(python_fd).st_ino):
            raise ObservationHold("remote helper interpreter differs from observed Python")
        python_version, python_version_sha = _tool_version(python_fd, "Python")
        git_version, git_version_sha = _tool_version(git_fd, "Git")
        python_identity.update({"version": python_version, "version_sha256": python_version_sha})
        git_identity.update({"version": git_version, "version_sha256": git_version_sha})
        repo, repo_before, git_before, head_before = _read_branch(
            repository,
            request["target"]["expected_branch"],
            logical_path=request["target"]["repository"],
        )
        if (
            _inode_identity(os.lstat(repository)) != repo_before
            or _inode_identity(os.lstat(repository / ".git")) != git_before
            or _inode_identity(os.stat(repository / ".git/HEAD", follow_symlinks=False)) != head_before
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
            "tools": {"python": python_identity, "git": git_identity},
            "repository": repo,
            "effects": {"remote_write": False, "repository_write": False, "nix": False},
        }
        value["receipt_sha256"] = _hash(value)
        return validate_receipt(value, request, now=observed_now)
    finally:
        os.close(git_fd)
        os.close(python_fd)


def _validate_tool(value: Any, label: str) -> dict[str, Any]:
    fields = {"path", "realpath", "sha256", "size", "uid", "gid", "mode", "nlink", "dev", "ino", "version", "version_sha256"}
    tool = dict(_exact(value, fields, label))
    if not _SHA.fullmatch(str(tool["sha256"])) or not _SHA.fullmatch(str(tool["version_sha256"])):
        raise HostStateError(f"{label} digest is invalid")
    for field in ("size", "uid", "gid", "mode", "nlink", "dev", "ino"):
        if isinstance(tool[field], bool) or not isinstance(tool[field], int) or tool[field] < 0:
            raise HostStateError(f"{label} {field} is invalid")
    if tool["size"] <= 0 or tool["nlink"] != 1 or not tool["mode"] & 0o111:
        raise HostStateError(f"{label} executable metadata is invalid")
    return tool


def validate_receipt(value: Any, request_value: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    request = validate_request(request_value, allow_fixture=True)
    fields = {"schema", "request_sha256", "observed_at", "target", "current_cas", "profile_cas", "tools", "repository", "effects", "receipt_sha256"}
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
    repository = _exact(receipt["repository"], {"path", "branch", "uid", "gid", "mode", "dev", "ino", "head_sha256"}, "host-state repository")
    if repository["path"] != request["target"]["repository"] or repository["branch"] != request["target"]["expected_branch"] or not _SHA.fullmatch(str(repository["head_sha256"])):
        raise HostStateError("host-state repository binding differs")
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
    return validate_receipt(value, request, now=now)


_TERMINALS = {
    ("PASS", "complete", "NONE", True),
    ("HOLD", "prelaunch", "NOT_READY", False),
    ("FAILED", "prelaunch", "VALIDATION_FAILED", False),
    ("AMBIGUOUS", "authority", "TOKEN_UNCERTAIN", False),
    ("AMBIGUOUS", "dispatch", "DISPATCH_UNCERTAIN", True),
    ("AMBIGUOUS", "persistence", "PERSISTENCE_UNCERTAIN", True),
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
        "diagnostic_sha256": digest(diagnostic),
        "diagnostic_size": len(diagnostic),
    }
    value["terminal_sha256"] = _hash(value)
    return value


def validate_terminal(value: Any, *, request_sha256: str | None = None) -> dict[str, Any]:
    fields = {"schema", "outcome", "stage", "code", "dispatched", "request_sha256", "observed_at", "diagnostic_sha256", "diagnostic_size", "terminal_sha256"}
    result = dict(_exact(value, fields, "host-state terminal"))
    if result["schema"] != TERMINAL_SCHEMA or (result["outcome"], result["stage"], result["code"], result["dispatched"]) not in _TERMINALS:
        raise HostStateError("host-state terminal semantics differ")
    if request_sha256 is not None and result["request_sha256"] != request_sha256:
        raise HostStateError("host-state terminal request differs")
    _parse_time(result["observed_at"], "host-state terminal time")
    if not _SHA.fullmatch(str(result["diagnostic_sha256"])) or isinstance(result["diagnostic_size"], bool) or not isinstance(result["diagnostic_size"], int) or result["diagnostic_size"] < 0:
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
    if result["mode"] != 0o700 or result["nlink"] < 2:
        raise HostStateError(f"{label} metadata is invalid")
    return result


def _same_root_authority(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Directory nlink may grow when an admitted committed attempt is published."""
    fields = ("path", "uid", "gid", "mode", "dev", "ino")
    return all(left.get(field) == right.get(field) for field in fields)


@dataclass(frozen=True)
class HostStateObservationGrant:
    value: Mapping[str, Any]

    @classmethod
    def issue(
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
        return cls(cls.validate(payload, request=validated, now=observed))

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


class HostStateEvidenceStore:
    """Atomic attempt-directory evidence publication using the admitted store primitive."""

    def __init__(self, root: Path, *, trusted_uid: int | None = None) -> None:
        self._store = _AtomicEvidenceStore(root, trusted_uid=trusted_uid)
        self.root = root

    @property
    def identity(self) -> dict[str, Any]:
        return dict(self._store.identity)

    def ready(self) -> bool:
        try:
            return _root_identity(self.identity, "evidence root")["uid"] == self._store.trusted_uid
        except (OSError, HostStateError):
            return False

    def persist(
        self,
        *,
        request: Mapping[str, Any],
        receipt: Mapping[str, Any],
        terminal_value: Mapping[str, Any],
        grant: Mapping[str, Any],
        token_identity: Mapping[str, Any],
        dependency: Mapping[str, Any],
    ) -> tuple[dict[str, Any], ...]:
        validated_request = validate_request(request, allow_fixture=True)
        validated_receipt = validate_receipt(receipt, validated_request)
        validated_terminal = validate_terminal(terminal_value, request_sha256=validated_request["request_sha256"])
        validated_grant = HostStateObservationGrant.validate(grant, request=validated_request)
        attachments = {
            "terminal.json": validated_terminal,
            "grant.json": validated_grant,
            "token-root.json": _root_identity(token_identity, "token root"),
            "dependency.json": dict(dependency),
        }
        try:
            paths = self._store.persist(validated_receipt, b"", validated_request, attachments)
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
            raise HostStatePersistenceAmbiguous(ambiguous) from exc
        refs: list[dict[str, Any]] = []
        for path in paths:
            raw = path.read_bytes()
            refs.append({"path": str(path), "sha256": digest(raw), "size": len(raw)})
        return tuple(refs)


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


class SshHostStateProvider:
    __slots__ = (
        "request",
        "ssh_path",
        "ssh_keygen_path",
        "known_hosts_path",
        "identity_path",
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
        self.helper_path = helper_path
        self.plan_authority = plan_authority
        self.parity_authority = parity_authority
        self.artifact_uid = os.getuid() if artifact_uid is None else artifact_uid
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
        helper_path: Path,
    ) -> "SshHostStateProvider":
        """Explicit test-only construction; production controller always rejects it."""
        self = object.__new__(cls)
        self.request = validate_request(request, allow_fixture=True)
        self.ssh_path = ssh_path
        self.ssh_keygen_path = ssh_keygen_path
        self.known_hosts_path = known_hosts_path
        self.identity_path = identity_path
        self.helper_path = helper_path
        self.plan_authority = None
        self.parity_authority = None
        self.artifact_uid = os.getuid()
        self.production_authority = False
        return self

    def prepare_launch(self, request_value: Mapping[str, Any]) -> Any:
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
            (self.ssh_path, "ssh_sha256", True, {0o555, 0o755}),
            (self.ssh_keygen_path, "ssh_keygen_sha256", True, {0o555, 0o755}),
            (self.known_hosts_path, "known_hosts_sha256", False, {0o400, 0o444}),
            (self.identity_path, "identity_sha256", False, {0o400}),
            (self.helper_path, "helper_sha256", False, {0o400, 0o444}),
        )
        raw_values: list[bytes] = []
        try:
            for path, field, executable, modes in specifications:
                fd, raw = _held_regular(path, request["transport"][field], executable=executable)
                opened.append(fd)
                raw_values.append(raw)
                st = os.fstat(fd)
                if st.st_uid != self.artifact_uid or st.st_nlink != 1 or stat.S_IMODE(st.st_mode) not in modes:
                    raise ObservationHold("sealed host-state artifact metadata differs")
            _known_host(raw_values[2], request["target"]["host"])
            rc, stdout, _stderr = _run_held_bounded(
                [f"/proc/{os.getpid()}/fd/{opened[1]}", "-y", "-f", f"/proc/{os.getpid()}/fd/{opened[3]}"],
                pass_fds=(opened[1], opened[3]),
                timeout=5,
                limit=8192,
            )
            if rc or stdout.decode("utf-8", errors="strict").strip() != request["transport"]["identity_public"]:
                raise ObservationHold("sealed private/public identity differs")
            initial = tuple(_artifact_snapshot(fd, raw) for fd, raw in zip(opened, raw_values, strict=True))
            named = tuple(_inode_identity(os.stat(path, follow_symlinks=False)) for path, *_ in specifications)
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
            ssh_fd, _keygen_fd, hosts_fd, identity_fd, _helper_fd = opened
            sealed_hosts = _sealed("a3-host-state-hosts", raw_values[2])
            sealed_identity = _sealed("a3-host-state-identity", raw_values[3])
            try:
                bootstrap = (
                    "ns={'__name__':'tgw_remote_helper'};exec(compile("
                    + repr(raw_values[4].decode("utf-8", errors="strict"))
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
                    "-oCanonicalizeHostname=no",
                    "-oProxyCommand=none",
                    "-oProxyJump=none",
                    f"-oHostKeyAlias={request['target']['host']}",
                    f"-oUserKnownHostsFile=/proc/{os.getpid()}/fd/{sealed_hosts}",
                    f"-oIdentityFile=/proc/{os.getpid()}/fd/{sealed_identity}",
                    "-oPasswordAuthentication=no",
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
                    os.close(fd)
                if post_error is not None:
                    raise post_error

        launch.close = close  # type: ignore[attr-defined]
        return launch


class HostStateObservationController:
    def __init__(self, *, allow_test_provider: bool = False) -> None:
        self.allow_test_provider = allow_test_provider

    def execute(
        self,
        *,
        request: Mapping[str, Any],
        provider: Any,
        grant: HostStateObservationGrant,
        token: DurableObservationToken,
        evidence_store: HostStateEvidenceStore,
        composition_sha256: str,
        plan_authority: MountedHostStatePlanAuthority | Mapping[str, Any] | None = None,
        parity_authority: MountedSshdParityAuthority | Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        observed = now or datetime.now(timezone.utc)
        validated = validate_request(
            request,
            now=observed,
            plan_authority=plan_authority,
            parity_authority=parity_authority,
            allow_fixture=self.allow_test_provider,
        )
        if not self.allow_test_provider and (type(provider) is not SshHostStateProvider or provider.production_authority is not True):
            raise HostStateError("production host-state provider is not sealed")
        fixed_grant = HostStateObservationGrant.validate(grant.value, request=validated, now=observed)
        if fixed_grant["composition_sha256"] != composition_sha256:
            raise HostStateError("host-state grant composition differs")
        if token.grant_sha256 != fixed_grant["grant_sha256"] or token.identity != fixed_grant["token_root_identity"]:
            raise HostStateError("host-state token authority differs")
        if not evidence_store.ready() or not _same_root_authority(evidence_store.identity, fixed_grant["evidence_root_identity"]):
            raise HostStateError("host-state evidence store is not ready")
        try:
            launch = provider.prepare_launch(validated)
        except Exception as exc:
            hold = terminal(
                outcome="HOLD",
                stage="prelaunch",
                code="NOT_READY",
                dispatched=False,
                request_sha256=validated["request_sha256"],
                observed_at=observed.isoformat(),
                diagnostic=type(exc).__name__.encode(),
            )
            return {"schema": RESULT_SCHEMA, "terminal": hold, "receipt": None, "dependency": None, "evidence": []}
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
            return {"schema": RESULT_SCHEMA, "terminal": ambiguous, "receipt": None, "dependency": None, "evidence": []}
        try:
            receipt = validate_receipt(launch(), validated, now=datetime.now(timezone.utc))
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
            return {"schema": RESULT_SCHEMA, "terminal": ambiguous, "receipt": None, "dependency": None, "evidence": []}
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
        evidence = evidence_store.persist(
            request=validated,
            receipt=receipt,
            terminal_value=success,
            grant=fixed_grant,
            token_identity=token.identity,
            dependency=dependency,
        )
        result = {
            "schema": RESULT_SCHEMA,
            "terminal": success,
            "receipt": receipt,
            "dependency": dependency,
            "evidence": list(evidence),
        }
        validate_result(result, validated)
        return result


def validate_result(value: Any, request_value: Mapping[str, Any]) -> dict[str, Any]:
    request = validate_request(request_value, allow_fixture=True)
    result = dict(_exact(value, {"schema", "terminal", "receipt", "dependency", "evidence"}, "host-state result"))
    if result["schema"] != RESULT_SCHEMA:
        raise HostStateError("host-state result schema differs")
    terminal_value = validate_terminal(result["terminal"], request_sha256=request["request_sha256"])
    if terminal_value["outcome"] == "PASS":
        receipt = validate_receipt(result["receipt"], request)
        if result["dependency"] != dependency_projection(
            receipt,
            request,
            ssh_sha256=request["transport"]["ssh_sha256"],
            descriptor_sha256=result["dependency"]["descriptor_sha256"],
        ):
            raise HostStateError("host-state dependency projection differs")
        if not isinstance(result["evidence"], list) or not result["evidence"]:
            raise HostStateError("host-state PASS evidence is absent")
        for ref in result["evidence"]:
            exact = _exact(ref, {"path", "sha256", "size"}, "host-state evidence ref")
            if not isinstance(exact["path"], str) or not _SHA.fullmatch(str(exact["sha256"])) or isinstance(exact["size"], bool) or not isinstance(exact["size"], int) or exact["size"] < 0:
                raise HostStateError("host-state evidence ref is invalid")
    elif result["receipt"] is not None or result["dependency"] is not None or result["evidence"] != []:
        raise HostStateError("non-PASS host-state result has success evidence")
    return result
