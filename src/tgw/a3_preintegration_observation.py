"""Zero-effect, fail-closed observation of the external ``tgw-prod`` flake.

The production composition intentionally remains unavailable until a dedicated
SSH identity is admitted.  The helper and validators are nevertheless complete
and testable without touching a production host.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import re
import selectors
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping

EFFECT_KIND = "tgw-prod-a3-preintegration-observation"
HANDLER_ID = EFFECT_KIND + "@1"
REQUEST_SCHEMA = "tgw-prod-a3-preintegration-observation-request/v1"
RECEIPT_SCHEMA = "tgw-prod-a3-preintegration-observation-receipt/v1"
TERMINAL_SCHEMA = "tgw-prod-a3-preintegration-observation-terminal/v1"
COMPOSITION_SCHEMA = "tgw-prod-a3-preintegration-observation-composition/v1"
PLAN_COMMIT = "29ecfd9c09f9c9a0c288b7a032c21a54145e99de"
PLAN_SOLUTION = "sha256:ab08bc1bf4220e67f3b40ab5c7cb819035fa1e6abc1c8a85923867b77c1b101c"
PLAN_CLOSURE = "sha256:c653ae6a66c967fd417ca443eb87252fae4ea1a4caac1ca56ed42bdecb4e3910"
SOURCE_COMMIT = "4ddf0d462c0be20475ddedb97a6234fd0cd28fb6"
SOURCE_TREE = "c69c73f8e92d831dd2d3c8d44b550336bf908436"
EVIDENCE_COMMIT = "6d897e4a2aea0ea12942ed3c7d769cf3c338da6e"
SOURCE_ARCHIVE = "sha256:9255ed323c4a175746c24bfc885c42f2af2291797ea0f44ef2fd4f2d203462f4"
SOURCE_CANDIDATE = "candidate:sha256:7cce5103c8c063ad326b343732046f7ba68812aad1750bbbc94bd8a148e89dd3"
SOURCE_CATALOG = "sha256:bbf928611111e23d81092ab1f4f61a6613fe1dac21bfc0784b8a9772d566661e"
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT = re.compile(r"^[0-9a-f]{40}$")
_OPERATION = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SOURCE_DESCRIPTOR_SCHEMA = "tgw-reviewed-observation-source/v1"
REMOTE_SUDO = "/run/wrappers/bin/sudo"
_SOURCE_AUTHORITY_SEAL = object()
_HOST_STATE_AUTHORITY_SEAL = object()


class ObservationError(RuntimeError):
    pass


class ObservationHold(ObservationError):
    pass


class EvidencePersistenceAmbiguous(ObservationError):
    def __init__(self, terminal: Mapping[str, Any]):
        super().__init__("validated observation evidence could not be persisted atomically")
        self.terminal = dict(terminal)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _rename_noreplace(src_dir_fd: int, src: str, dst_dir_fd: int, dst: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise ObservationError("atomic no-replace publication is unavailable")
    if renameat2(src_dir_fd, src.encode(), dst_dir_fd, dst.encode(), 1) != 0:  # RENAME_NOREPLACE
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), dst)


def _inode_identity(st: os.stat_result) -> tuple[int, ...]:
    return (st.st_dev, st.st_ino, st.st_uid, st.st_gid, stat.S_IMODE(st.st_mode), st.st_nlink, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _exact(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ObservationError(f"{label} fields are not exact")
    return value


def validate_source_descriptor(value: Any) -> dict[str, Any]:
    fields = {"schema", "checkpoint", "commit", "tree", "archive_sha256", "candidate_identity", "catalog_sha256", "helper_sha256", "descriptor_sha256"}
    source = dict(_exact(value, fields, "reviewed source descriptor"))
    if source["schema"] != SOURCE_DESCRIPTOR_SCHEMA or source["checkpoint"] != EVIDENCE_COMMIT:
        raise ObservationError("reviewed source checkpoint is invalid")
    exact = {
        "commit": SOURCE_COMMIT,
        "tree": SOURCE_TREE,
        "archive_sha256": SOURCE_ARCHIVE,
        "candidate_identity": SOURCE_CANDIDATE,
        "catalog_sha256": SOURCE_CATALOG,
    }
    if any(source[key] != expected for key, expected in exact.items()):
        raise ObservationError("reviewed source identity is not the admitted source")
    for key in ("archive_sha256", "catalog_sha256", "helper_sha256"):
        if not _SHA.fullmatch(str(source[key])):
            raise ObservationError(f"reviewed source {key} is invalid")
    if not isinstance(source["candidate_identity"], str) or not source["candidate_identity"].startswith("candidate:sha256:"):
        raise ObservationError("reviewed candidate identity is invalid")
    claimed = source.pop("descriptor_sha256")
    if claimed != digest(canonical(source)):
        raise ObservationError("reviewed source descriptor hash is invalid")
    source["descriptor_sha256"] = claimed
    return source


def load_mounted_source_descriptor(path: Path, expected_sha256: str) -> dict[str, Any]:
    fd, raw = _held_regular(path, expected_sha256)
    try:
        descriptor = validate_source_descriptor(json.loads(raw))
    finally:
        os.close(fd)
    return descriptor


class MountedSourceAuthority:
    """Held root-owned content-addressed source authority for production only."""

    __slots__ = ("descriptor", "fd", "identity", "sha256")

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise TypeError("MountedSourceAuthority is sealed")

    def __init__(self, path: Path, expected_sha256: str, *, _token: object) -> None:
        if _token is not _SOURCE_AUTHORITY_SEAL or path.name != expected_sha256.removeprefix("sha256:") + ".json":
            raise ObservationError("source authority locator is not content addressed")
        absolute = path.absolute()
        for ancestor in (absolute.parent, *absolute.parents):
            ancestor_st = os.lstat(ancestor)
            if not stat.S_ISDIR(ancestor_st.st_mode) or stat.S_ISLNK(ancestor_st.st_mode) or ancestor_st.st_uid != 0 or stat.S_IMODE(ancestor_st.st_mode) & 0o022:
                raise ObservationError("source authority root chain is not protected")
            if ancestor == Path("/"):
                break
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        st = os.fstat(fd)
        raw = os.read(fd, st.st_size + 1)
        if st.st_uid != 0 or stat.S_IMODE(st.st_mode) != 0o444 or st.st_nlink != 1 or digest(raw) != expected_sha256:
            os.close(fd)
            raise ObservationError("source authority file is not root-protected and exact")
        self.descriptor = validate_source_descriptor(json.loads(raw))
        self.fd = fd
        self.identity = _inode_identity(st)
        self.sha256 = expected_sha256


def load_source_authority(path: Path, expected_sha256: str) -> MountedSourceAuthority:
    return MountedSourceAuthority(path, expected_sha256, _token=_SOURCE_AUTHORITY_SEAL)


class MountedHostStateDependency:
    """Immutable held authority for the admitted A3O02 dependency artifact."""

    __slots__ = ("_fd", "_identity", "_path", "_raw", "_sealed", "_sha256")

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise TypeError("MountedHostStateDependency is sealed")

    def __init__(self, path: Path, expected_sha256: str, *, _token: object) -> None:
        if _token is not _HOST_STATE_AUTHORITY_SEAL or not _SHA.fullmatch(expected_sha256):
            raise ObservationError("host-state authority construction is not admitted")
        absolute = path.absolute()
        evidence_root = Path("/opt/TGW/evidence/codex/host-state-observation-v1")
        if (
            absolute.name != "dependency.json"
            or absolute.parent.parent != evidence_root
            or not re.fullmatch(r"[0-9a-f]{64}", absolute.parent.name)
        ):
            raise ObservationError("host-state authority locator is outside the admitted evidence family")
        for ancestor in absolute.parents:
            ancestor_st = os.lstat(ancestor)
            if (
                not stat.S_ISDIR(ancestor_st.st_mode)
                or stat.S_ISLNK(ancestor_st.st_mode)
                or ancestor_st.st_uid != 0
                or stat.S_IMODE(ancestor_st.st_mode) & 0o022
            ):
                raise ObservationError("host-state authority root chain is not protected")
            if ancestor == Path("/"):
                break
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            st = os.fstat(fd)
            raw = os.pread(fd, st.st_size + 1, 0)
            dependency = json.loads(raw)
            if (
                not stat.S_ISREG(st.st_mode)
                or st.st_uid != 0
                or stat.S_IMODE(st.st_mode) not in {0o400, 0o444}
                or st.st_nlink != 1
                or len(raw) != st.st_size
                or digest(raw) != expected_sha256
                or raw != canonical(dependency)
            ):
                raise ObservationError("host-state authority file is not root-protected, canonical, and exact")
        except Exception:
            os.close(fd)
            raise
        object.__setattr__(self, "_fd", fd)
        object.__setattr__(self, "_identity", _inode_identity(st))
        object.__setattr__(self, "_path", absolute)
        object.__setattr__(self, "_raw", raw)
        object.__setattr__(self, "_sha256", expected_sha256)
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("MountedHostStateDependency is immutable")
        object.__setattr__(self, name, value)

    @property
    def dependency(self) -> Mapping[str, Any]:
        return json.loads(self._raw)

    @property
    def sha256(self) -> str:
        return self._sha256

    def postcheck(self) -> None:
        held = os.fstat(self._fd)
        named = os.stat(self._path, follow_symlinks=False)
        held_raw = os.pread(self._fd, held.st_size + 1, 0)
        if (
            _inode_identity(held) != self._identity
            or _inode_identity(named) != self._identity
            or held_raw != self._raw
            or digest(held_raw) != self._sha256
        ):
            raise ObservationError("host-state authority changed while held")

    def close(self) -> None:
        os.close(self._fd)


def load_host_state_dependency(path: Path, expected_sha256: str) -> MountedHostStateDependency:
    return MountedHostStateDependency(path, expected_sha256, _token=_HOST_STATE_AUTHORITY_SEAL)


def _fixture_source_descriptor() -> dict[str, Any]:
    """Non-production descriptor used only by local tests; production mounts one."""
    value = {
        "schema": SOURCE_DESCRIPTOR_SCHEMA,
        "checkpoint": EVIDENCE_COMMIT,
        "commit": SOURCE_COMMIT,
        "tree": SOURCE_TREE,
        "archive_sha256": SOURCE_ARCHIVE,
        "candidate_identity": SOURCE_CANDIDATE,
        "catalog_sha256": SOURCE_CATALOG,
        "helper_sha256": "sha256:" + "4" * 64,
    }
    value["descriptor_sha256"] = digest(canonical(value))
    return value


def validate_request(value: Any, *, now: datetime | None = None) -> dict[str, Any]:
    request_fields = {
        "schema",
        "operation_id",
        "plan",
        "source",
        "host_state_dependency",
        "host_state_dependency_sha256",
        "target",
        "transport",
        "bounds",
        "freshness",
        "repo_expectation",
        "policy",
        "request_sha256",
    }
    request = dict(_exact(value, request_fields, "request"))
    if request["schema"] != REQUEST_SCHEMA or not isinstance(request["operation_id"], str) or not _OPERATION.fullmatch(request["operation_id"]):
        raise ObservationError("request identity is invalid")
    plan = _exact(request["plan"], {"commit", "solution_sha256", "closure_sha256"}, "Plan")
    if dict(plan) != {"commit": PLAN_COMMIT, "solution_sha256": PLAN_SOLUTION, "closure_sha256": PLAN_CLOSURE}:
        raise ObservationError("Plan binding is not exact")
    validate_source_descriptor(request["source"])
    dependency = _exact(
        request["host_state_dependency"],
        {"schema", "status", "descriptor_sha256", "receipt_sha256", "observed_at", "current_cas", "profile_cas", "repository", "tools", "receipt"},
        "host state dependency",
    )
    if (
        dependency["schema"] != "tgw-prod-a3-host-state-observation-dependency/v1"
        or dependency["status"] != "SATISFIED"
        or not _SHA.fullmatch(str(dependency["receipt_sha256"]))
        or not _SHA.fullmatch(str(dependency["descriptor_sha256"]))
    ):
        raise ObservationError("host state dependency is not satisfied")
    if not isinstance(dependency["current_cas"], str) or dependency["current_cas"] != dependency["profile_cas"] or not dependency["current_cas"].startswith("/nix/store/"):
        raise ObservationError("host state CAS is invalid")
    repository = _exact(
        dependency["repository"],
        {"path", "branch", "uid", "gid", "mode", "dev", "ino", "head_sha256", "ref_sha256", "commit"},
        "host repository",
    )
    if (
        repository["path"] != "/home/db/tgw-flake"
        or repository["branch"] != "master"
        or not _GIT.fullmatch(str(repository["commit"]))
        or repository["head_sha256"] != digest(b"ref: refs/heads/master\n")
        or repository["ref_sha256"] != digest((str(repository["commit"]) + "\n").encode())
    ):
        raise ObservationError("host repository authority is invalid")
    for field in ("uid", "gid", "mode", "dev", "ino"):
        if isinstance(repository[field], bool) or not isinstance(repository[field], int) or repository[field] < 0:
            raise ObservationError("host repository identity is invalid")
    tools = _exact(dependency["tools"], {"python_sha256", "git_sha256", "ssh_sha256"}, "host tools")
    if any(not _SHA.fullmatch(str(item)) for item in tools.values()) or any(tools[key] != request["transport"][key] for key in tools):
        raise ObservationError("host tool evidence differs from transport")
    observed = datetime.fromisoformat(str(dependency["observed_at"]).replace("Z", "+00:00"))
    if observed.tzinfo is None or (now is not None and not timedelta(0) <= now - observed <= timedelta(minutes=10)):
        raise ObservationError("host state evidence is stale")
    host_receipt = dict(_exact(dependency["receipt"], {"schema", "observed_at", "current_cas", "profile_cas", "repository", "tools", "receipt_sha256"}, "host state receipt"))
    receipt_hash = host_receipt.pop("receipt_sha256")
    if (
        host_receipt["schema"] != "tgw-prod-a3-host-state-observation-receipt/v1"
        or receipt_hash != digest(canonical(host_receipt))
        or receipt_hash != dependency["receipt_sha256"]
        or host_receipt["observed_at"] != dependency["observed_at"]
        or host_receipt["current_cas"] != dependency["current_cas"]
        or host_receipt["profile_cas"] != dependency["profile_cas"]
        or host_receipt["repository"] != repository
        or host_receipt["tools"] != tools
    ):
        raise ObservationError("host state receipt is not self-hashed and exact")
    if request["host_state_dependency_sha256"] != digest(canonical(dependency)):
        raise ObservationError("host state dependency is not bound to exact artifact bytes")
    if request["target"] != {"host": "tgw-prod", "repository": "/home/db/tgw-flake", "branch": "master", "system": "x86_64-linux", "user": "codex", "port": 22}:
        raise ObservationError("target is not exact")
    transport_fields = {
        "ssh_sha256",
        "ssh_keygen_sha256",
        "known_hosts_sha256",
        "identity_sha256",
        "identity_public",
        "helper_sha256",
        "python_sha256",
        "git_sha256",
    }
    transport = _exact(request["transport"], transport_fields, "transport")
    if any(not isinstance(transport[key], str) or not _SHA.fullmatch(transport[key]) for key in transport if key != "identity_public"):
        raise ObservationError("transport identities are invalid")
    if not isinstance(transport["identity_public"], str) or len(transport["identity_public"].split()) < 2:
        raise ObservationError("dedicated identity public key is invalid")
    if transport["helper_sha256"] != request["source"]["helper_sha256"]:
        raise ObservationError("mounted helper differs from reviewed source descriptor")
    bounds = _exact(request["bounds"], {"timeout_seconds", "max_output_bytes", "max_archive_bytes", "max_members", "max_file_bytes", "max_unpacked_bytes"}, "bounds")
    if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in bounds.values()):
        raise ObservationError("bounds are invalid")
    if bounds["timeout_seconds"] > 120 or bounds["max_output_bytes"] > 1_048_576 or bounds["max_archive_bytes"] > 64 * 1024 * 1024:
        raise ObservationError("bounds exceed policy")
    if request["policy"] != {"read_only": True, "nix": False, "network_beyond_ssh": False, "writes": False, "authority_consumption": False}:
        raise ObservationError("zero-effect policy is invalid")
    repo_expectation = _exact(request["repo_expectation"], {"uid", "gid", "mode", "git_dir", "lock_file"}, "repository expectation")
    if repo_expectation != {
        "uid": repository["uid"],
        "gid": repository["gid"],
        "mode": repository["mode"],
        "git_dir": ".git",
        "lock_file": "flake.lock",
    }:
        raise ObservationError("repository ownership expectation is invalid")
    freshness = _exact(request["freshness"], {"issued_at", "expires_at"}, "freshness")
    issued = datetime.fromisoformat(str(freshness["issued_at"]).replace("Z", "+00:00"))
    expires = datetime.fromisoformat(str(freshness["expires_at"]).replace("Z", "+00:00"))
    if issued.tzinfo is None or expires.tzinfo is None or expires <= issued or expires - issued > timedelta(minutes=10):
        raise ObservationError("request freshness window is invalid")
    if now is not None and not (issued <= now < expires):
        raise ObservationError("request is not fresh")
    claimed = request.pop("request_sha256")
    if claimed != digest(canonical(request)):
        raise ObservationError("request hash is invalid")
    request["request_sha256"] = claimed
    return request


def make_request(*, operation_id: str, transport: Mapping[str, str], source: Mapping[str, Any] | None = None, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    source = dict(source or _fixture_source_descriptor())
    transport = dict(transport)
    transport["helper_sha256"] = source["helper_sha256"]
    transport.setdefault("ssh_keygen_sha256", "sha256:" + "8" * 64)
    transport.setdefault("identity_public", "ssh-ed25519 a2V5")
    host_receipt = {
        "schema": "tgw-prod-a3-host-state-observation-receipt/v1",
        "observed_at": now.isoformat(),
        "current_cas": "/nix/store/00000000000000000000000000000000-test-system",
        "profile_cas": "/nix/store/00000000000000000000000000000000-test-system",
        "repository": {
            "path": "/home/db/tgw-flake",
            "branch": "master",
            "uid": 1001,
            "gid": 1001,
            "mode": 0o755,
            "dev": 1,
            "ino": 2,
            "head_sha256": digest(b"ref: refs/heads/master\n"),
            "ref_sha256": digest(("1" * 40 + "\n").encode()),
            "commit": "1" * 40,
        },
        "tools": {name: transport[name] for name in ("python_sha256", "git_sha256", "ssh_sha256")},
    }
    host_receipt["receipt_sha256"] = digest(canonical(host_receipt))
    dependency = {
        "schema": "tgw-prod-a3-host-state-observation-dependency/v1",
        "status": "SATISFIED",
        "descriptor_sha256": "sha256:" + "7" * 64,
        "receipt_sha256": host_receipt["receipt_sha256"],
        "observed_at": now.isoformat(),
        "current_cas": "/nix/store/00000000000000000000000000000000-test-system",
        "profile_cas": "/nix/store/00000000000000000000000000000000-test-system",
        "repository": host_receipt["repository"],
        "tools": {
            "python_sha256": transport["python_sha256"],
            "git_sha256": transport["git_sha256"],
            "ssh_sha256": transport["ssh_sha256"],
        },
        "receipt": host_receipt,
    }
    value = {
        "schema": REQUEST_SCHEMA,
        "operation_id": operation_id,
        "plan": {"commit": PLAN_COMMIT, "solution_sha256": PLAN_SOLUTION, "closure_sha256": PLAN_CLOSURE},
        "source": source,
        "host_state_dependency": dependency,
        "host_state_dependency_sha256": digest(canonical(dependency)),
        "target": {"host": "tgw-prod", "repository": "/home/db/tgw-flake", "branch": "master", "system": "x86_64-linux", "user": "codex", "port": 22},
        "transport": transport,
        "bounds": {
            "timeout_seconds": 60,
            "max_output_bytes": 262144,
            "max_archive_bytes": 64 * 1024 * 1024,
            "max_members": 100000,
            "max_file_bytes": 16 * 1024 * 1024,
            "max_unpacked_bytes": 256 * 1024 * 1024,
        },
        "freshness": {"issued_at": now.isoformat(), "expires_at": (now + timedelta(minutes=5)).isoformat()},
        "repo_expectation": {
            "uid": host_receipt["repository"]["uid"],
            "gid": host_receipt["repository"]["gid"],
            "mode": host_receipt["repository"]["mode"],
            "git_dir": ".git",
            "lock_file": "flake.lock",
        },
        "policy": {"read_only": True, "nix": False, "network_beyond_ssh": False, "writes": False, "authority_consumption": False},
    }
    value["request_sha256"] = digest(canonical(value))
    return validate_request(value)


def _verify_repository_components(repo: Path, request: Mapping[str, Any], *, enforce_owner: bool) -> tuple[tuple[int, ...], tuple[int, ...]]:
    expectation = request["repo_expectation"]
    repo_stat = os.lstat(repo)
    if not stat.S_ISDIR(repo_stat.st_mode) or stat.S_ISLNK(repo_stat.st_mode):
        raise ObservationError("repository root is not a held directory")
    if enforce_owner and (repo_stat.st_uid, repo_stat.st_gid, stat.S_IMODE(repo_stat.st_mode)) != (expectation["uid"], expectation["gid"], expectation["mode"]):
        raise ObservationHold("repository ownership or mode differs")
    git_dir = repo / expectation["git_dir"]
    git_stat = os.lstat(git_dir)
    if not stat.S_ISDIR(git_stat.st_mode) or stat.S_ISLNK(git_stat.st_mode):
        raise ObservationError("repository .git is not a directory")
    forbidden_files = (git_dir / "objects/info/alternates", git_dir / "info/grafts")
    if any(path.exists() or path.is_symlink() for path in forbidden_files):
        raise ObservationHold("repository uses alternates or grafts")
    replace = git_dir / "refs/replace"
    if replace.exists() and (not replace.is_dir() or any(replace.iterdir())):
        raise ObservationHold("repository uses replacement objects")
    for forbidden in (git_dir / "objects/info/http-alternates", git_dir / "commondir", git_dir / "shallow"):
        if forbidden.exists() or forbidden.is_symlink():
            raise ObservationHold("repository uses forbidden common/shallow object state")
    packed = git_dir / "packed-refs"
    if packed.exists() and "refs/replace/" in packed.read_text(errors="replace"):
        raise ObservationHold("repository uses packed replacement objects")
    config = git_dir / "config"
    if config.exists():
        text = config.read_text(errors="replace").lower()
        if "include.path" in text or "[include" in text or "submodule" in text or "worktreeconfig" in text:
            raise ObservationHold("repository config includes external or submodule state")
    worktree_config = git_dir / "config.worktree"
    if worktree_config.exists() or worktree_config.is_symlink():
        raise ObservationHold("repository uses worktree-specific config")
    for path in repo.rglob(".gitmodules"):
        if path.is_file():
            raise ObservationHold("repository contains submodule configuration")
    return (
        (repo_stat.st_dev, repo_stat.st_ino, repo_stat.st_mode, repo_stat.st_uid, repo_stat.st_gid, repo_stat.st_nlink),
        (git_stat.st_dev, git_stat.st_ino, git_stat.st_mode, git_stat.st_uid, git_stat.st_gid, git_stat.st_nlink),
    )


def observe_repository(repository: Path, request: Mapping[str, Any], *, enforce_owner: bool = False, git_path: str | None = None, git_fd: int | None = None) -> tuple[dict[str, Any], bytes]:
    request = validate_request(request)
    repo = repository.resolve(strict=True)
    if repo != repository or not repo.is_dir():
        raise ObservationError("repository path is not a stable directory")
    component_identity = _verify_repository_components(repo, request, enforce_owner=enforce_owner)
    owned_git = git_fd is None
    if git_fd is None:
        git_path = os.path.realpath(git_path or shutil.which("git") or "")
        if not git_path:
            raise ObservationError("Git executable is unavailable")
        git_fd = os.open(git_path, os.O_RDONLY | os.O_NOFOLLOW)
    git_stat = os.fstat(git_fd)
    if not stat.S_ISREG(git_stat.st_mode) or not git_stat.st_mode & 0o111:
        os.close(git_fd)
        raise ObservationError("Git executable is not a held regular executable")
    held_git = f"/proc/{os.getpid()}/fd/{git_fd}"
    env = {"PATH": "/nonexistent", "LANG": "C", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_OPTIONAL_LOCKS": "0", "HOME": "/nonexistent"}

    def git(*argv: str, binary: bool = False) -> bytes | str:
        closed = [held_git, "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "submodule.recurse=false", "-c", "extensions.objectFormat=sha1", "-c", "protocol.file.allow=never"]
        process = subprocess.Popen([*closed, *argv], cwd=repo, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, pass_fds=(git_fd,))
        failure: Exception | None = None
        try:
            output, _error = _bounded_stream_readonly(
                process,
                stdout_limit=request["bounds"]["max_archive_bytes"],
                stderr_limit=request["bounds"]["max_output_bytes"],
                timeout=request["bounds"]["timeout_seconds"],
            )
            try:
                process.wait(timeout=0.25)
            except subprocess.TimeoutExpired as exc:
                failure = exc
        except Exception as exc:
            failure = exc
        if failure is not None:
            state = _group_empty_or_kill(process.pid)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired as cleanup_exc:
                raise ObservationError("Git leader could not be reaped") from cleanup_exc
            state = _post_reap_group_state(process.pid, state)
            if not state["removed"] or not state["reaped"]:
                raise ObservationError("Git process group cleanup is uncertain") from failure
            raise ObservationError("read-only Git observation exceeded a bound or timed out") from failure
        state = _group_empty_or_kill(process.pid)
        if process.returncode != 0 or state["had_survivor"] or not state["removed"]:
            raise ObservationError("read-only Git observation failed")
        return output if binary else output.decode().strip()

    status = git("status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise ObservationHold("production flake is not clean")
    commit, tree = git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}")
    expected_repository = request["host_state_dependency"]["repository"]
    if commit != expected_repository["commit"]:
        raise ObservationHold("production flake commit differs from fresh host-state authority")
    if enforce_owner:
        repo_dev, repo_ino, repo_mode, repo_uid, repo_gid, _repo_nlink = component_identity[0]
        if (
            repo_dev,
            repo_ino,
            stat.S_IMODE(repo_mode),
            repo_uid,
            repo_gid,
        ) != (
            expected_repository["dev"],
            expected_repository["ino"],
            expected_repository["mode"],
            expected_repository["uid"],
            expected_repository["gid"],
        ):
            raise ObservationHold("production flake identity differs from fresh host-state authority")
    if any(line.startswith("160000 ") for line in str(git("ls-files", "--stage")).splitlines()):
        raise ObservationHold("repository contains gitlinks")
    if git("symbolic-ref", "--short", "HEAD") != request["target"]["branch"]:
        raise ObservationHold("production flake branch differs")
    if not _GIT.fullmatch(str(commit)) or not _GIT.fullmatch(str(tree)):
        raise ObservationError("Git identities are invalid")
    lock_raw = git("show", f"{commit}:flake.lock", binary=True)
    assert isinstance(lock_raw, bytes)
    if len(lock_raw) > request["bounds"]["max_output_bytes"]:
        raise ObservationError("flake.lock is invalid")
    archive = git("archive", "--format=tar", "--prefix=tgw-flake/", str(commit), binary=True)
    assert isinstance(archive, bytes)
    if git("rev-parse", "HEAD") != commit or git("rev-parse", "HEAD^{tree}") != tree or git("status", "--porcelain=v1", "--untracked-files=all"):
        raise ObservationHold("repository changed during observation")
    if _verify_repository_components(repo, request, enforce_owner=enforce_owner) != component_identity:
        raise ObservationHold("repository components changed during observation")
    if _inode_identity(os.fstat(git_fd)) != _inode_identity(git_stat):
        if owned_git:
            os.close(git_fd)
        raise ObservationHold("held Git executable identity changed")
    if owned_git:
        os.close(git_fd)
    if len(archive) > request["bounds"]["max_archive_bytes"]:
        raise ObservationError("archive exceeds bound")
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "outcome": "PASS",
        "request_sha256": request["request_sha256"],
        "repository": {"commit": commit, "tree": tree, "clean": True, "archive_sha256": digest(archive), "archive_size": len(archive), "flake_lock_sha256": digest(lock_raw)},
        "effects": {"nix": False, "store": False, "build": False, "write": False, "install": False, "profile": False, "deploy": False, "keygen": False, "authority_consumption": False},
    }
    receipt["receipt_sha256"] = digest(canonical(receipt))
    return validate_receipt(receipt, request), archive


def validate_receipt(value: Any, request: Mapping[str, Any]) -> dict[str, Any]:
    receipt = dict(_exact(value, {"schema", "outcome", "request_sha256", "repository", "effects", "receipt_sha256"}, "receipt"))
    if receipt["schema"] != RECEIPT_SCHEMA or receipt["outcome"] != "PASS" or receipt["request_sha256"] != request["request_sha256"]:
        raise ObservationError("receipt binding is invalid")
    repo = _exact(receipt["repository"], {"commit", "tree", "clean", "archive_sha256", "archive_size", "flake_lock_sha256"}, "repository receipt")
    if not _GIT.fullmatch(str(repo["commit"])) or not _GIT.fullmatch(str(repo["tree"])) or repo["clean"] is not True:
        raise ObservationError("repository receipt is invalid")
    if (
        not _SHA.fullmatch(str(repo["archive_sha256"]))
        or not _SHA.fullmatch(str(repo["flake_lock_sha256"]))
        or isinstance(repo["archive_size"], bool)
        or not isinstance(repo["archive_size"], int)
        or repo["archive_size"] <= 0
        or repo["archive_size"] > request["bounds"]["max_archive_bytes"]
    ):
        raise ObservationError("repository hashes are invalid")
    expected_effects = {"nix": False, "store": False, "build": False, "write": False, "install": False, "profile": False, "deploy": False, "keygen": False, "authority_consumption": False}
    if receipt["effects"] != expected_effects:
        raise ObservationError("receipt claims forbidden effects")
    claimed = receipt.pop("receipt_sha256")
    if claimed != digest(canonical(receipt)):
        raise ObservationError("receipt hash is invalid")
    receipt["receipt_sha256"] = claimed
    return receipt


def _git_tree(directory: Path) -> str:
    entries = bytearray()
    for child in sorted(directory.iterdir(), key=lambda item: item.name.encode() + (b"/" if item.is_dir() else b"")):
        if child.is_dir():
            mode, object_hash = b"40000", bytes.fromhex(_git_tree(child))
        else:
            raw = child.read_bytes()
            header = b"blob " + str(len(raw)).encode() + b"\0"
            mode = b"100755" if child.stat().st_mode & 0o111 else b"100644"
            object_hash = hashlib.sha1(header + raw).digest()  # noqa: S324 - Git object identity
        entries.extend(mode + b" " + child.name.encode() + b"\0" + object_hash)
    header = b"tree " + str(len(entries)).encode() + b"\0"
    return hashlib.sha1(header + entries).hexdigest()  # noqa: S324 - Git object identity


def replay_archive(archive: bytes, receipt: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any]:
    """Independently reconstruct the exact tree and lock identity from held bytes."""
    validated = validate_receipt(receipt, request)
    if digest(archive) != validated["repository"]["archive_sha256"] or len(archive) != validated["repository"]["archive_size"]:
        raise ObservationError("archive byte identity differs")
    with tarfile.open(fileobj=BytesIO(archive), mode="r:") as stream, tempfile.TemporaryDirectory(prefix="tgw-a3-replay-") as temporary:
        if stream.pax_headers != {"comment": validated["repository"]["commit"]}:
            raise ObservationError("archive PAX commit binding is invalid")
        root = Path(temporary) / "tgw-flake"
        members = stream.getmembers()
        if not members or len(members) > request["bounds"]["max_members"]:
            raise ObservationError("archive member count is invalid")
        seen: set[str] = set()
        unpacked = 0
        for member in members:
            parts = Path(member.name).parts
            raw_parts = member.name.rstrip("/").split("/")
            normalized = "/".join(parts)
            if not parts or parts[0] != "tgw-flake" or any(part in {"", ".", ".."} for part in raw_parts) or ".git" in parts or normalized in seen:
                raise ObservationError("archive path is invalid or duplicated")
            seen.add(normalized)
            if not (member.isdir() or member.isreg()):
                raise ObservationError("archive contains a forbidden member type")
            destination = Path(temporary).joinpath(*parts)
            if member.isdir():
                destination.mkdir(mode=0o700, parents=True, exist_ok=True)
            else:
                unpacked += member.size
                if member.size > request["bounds"]["max_file_bytes"] or unpacked > request["bounds"]["max_unpacked_bytes"]:
                    raise ObservationError("archive unpacked content exceeds bound")
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source = stream.extractfile(member)
                if source is None:
                    raise ObservationError("archive regular member is unreadable")
                raw = source.read(request["bounds"]["max_archive_bytes"] + 1)
                if len(raw) != member.size:
                    raise ObservationError("archive member size differs")
                fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, member.mode & 0o777)
                try:
                    view = memoryview(raw)
                    while view:
                        written = os.write(fd, view)
                        if written <= 0:
                            raise ObservationError("archive replay write failed")
                        view = view[written:]
                finally:
                    os.close(fd)
        output = _git_tree(root)
        if output != validated["repository"]["tree"]:
            raise ObservationError("archive replay tree differs")
        lock_raw = (root / "flake.lock").read_bytes()
        if digest(lock_raw) != validated["repository"]["flake_lock_sha256"]:
            raise ObservationError("archive lock differs")
        try:
            lock = json.loads(lock_raw)
        except json.JSONDecodeError as exc:
            raise ObservationError("flake.lock JSON is invalid") from exc
        if not isinstance(lock, dict) or not isinstance(lock.get("nodes"), dict) or not isinstance(lock.get("root"), str) or lock["root"] not in lock["nodes"]:
            raise ObservationError("flake.lock input graph is invalid")
        for node_name, node in lock["nodes"].items():
            if not isinstance(node_name, str) or not node_name or not isinstance(node, dict) or any(key not in {"inputs", "locked", "original", "flake"} for key in node):
                raise ObservationError("flake.lock node schema is invalid")
            if "flake" in node and not isinstance(node["flake"], bool):
                raise ObservationError("flake.lock node flake flag is invalid")
            for identity_key in ("locked", "original"):
                if identity_key in node and (not isinstance(node[identity_key], dict) or any(not isinstance(k, str) or not isinstance(v, (str, int, bool)) for k, v in node[identity_key].items())):
                    raise ObservationError("flake.lock source identity is invalid")
            inputs = node.get("inputs", {})
            if not isinstance(inputs, dict):
                raise ObservationError("flake.lock inputs are invalid")
            for input_name, target in inputs.items():
                if (
                    not isinstance(input_name, str)
                    or not input_name
                    or not (isinstance(target, str) or (isinstance(target, list) and target and all(isinstance(part, str) and part for part in target)))
                ):
                    raise ObservationError("flake.lock input edge is invalid")
                if isinstance(target, list):
                    current = lock["root"]
                    visited = {current}
                    for edge in target:
                        nested = lock["nodes"][current].get("inputs", {}).get(edge)
                        if isinstance(nested, list):
                            raise ObservationError("nested flake.lock follows path is unsupported")
                        if not isinstance(nested, str) or nested not in lock["nodes"] or nested in visited:
                            raise ObservationError("flake.lock follows path is missing or cyclic")
                        visited.add(nested)
                        current = nested
                elif target not in lock["nodes"]:
                    raise ObservationError("flake.lock input edge target is absent")
        return {"tree": output, "lock_sha256": digest(lock_raw), "lock_nodes": sorted(lock["nodes"])}


_TERMINALS = {
    ("PASS", "complete", "NONE", True),
    ("HOLD", "predispatch", "PROVIDER_NOT_READY", False),
    ("HOLD", "repository", "REPOSITORY_DIRTY", False),
    ("HOLD", "freshness", "REQUEST_EXPIRED", False),
    ("FAILED", "request", "REQUEST_INVALID", False),
    ("FAILED", "helper", "HELPER_INVALID", True),
    ("FAILED", "replay", "ARCHIVE_REPLAY_FAILED", True),
    ("AMBIGUOUS", "dispatch", "POSTDISPATCH_UNCERTAIN", True),
    ("AMBIGUOUS", "persistence", "PERSISTENCE_UNCERTAIN", True),
}


def terminal(*, outcome: str, stage: str, code: str, dispatched: bool, request_sha256: str, observed_at: str, diagnostic: bytes = b"") -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema": TERMINAL_SCHEMA,
        "outcome": outcome,
        "stage": stage,
        "code": code,
        "dispatched": dispatched,
        "request_sha256": request_sha256,
        "observed_at": observed_at,
        "diagnostic": {"bytes": len(diagnostic), "sha256": digest(diagnostic)},
        "effects": {"nix": False, "store": False, "build": False, "write": False, "install": False, "profile": False, "deploy": False, "keygen": False, "authority_consumption": False},
    }
    value["terminal_sha256"] = digest(canonical(value))
    return validate_terminal(value)


def validate_terminal(value: Any) -> dict[str, Any]:
    fields = {"schema", "outcome", "stage", "code", "dispatched", "request_sha256", "observed_at", "diagnostic", "effects", "terminal_sha256"}
    item = dict(_exact(value, fields, "terminal"))
    if item["schema"] != TERMINAL_SCHEMA or (item["outcome"], item["stage"], item["code"], item["dispatched"]) not in _TERMINALS:
        raise ObservationError("terminal state tuple is invalid")
    if not _SHA.fullmatch(str(item["request_sha256"])):
        raise ObservationError("terminal request identity is invalid")
    try:
        observed = datetime.fromisoformat(str(item["observed_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ObservationError("terminal observed_at is invalid") from exc
    if observed.tzinfo is None:
        raise ObservationError("terminal observed_at lacks timezone")
    diagnostic = _exact(item["diagnostic"], {"bytes", "sha256"}, "terminal diagnostic")
    if isinstance(diagnostic["bytes"], bool) or not isinstance(diagnostic["bytes"], int) or not 0 <= diagnostic["bytes"] <= 262144 or not _SHA.fullmatch(str(diagnostic["sha256"])):
        raise ObservationError("terminal diagnostic identity is invalid")
    if diagnostic["bytes"] == 0 and diagnostic["sha256"] != digest(b""):
        raise ObservationError("empty terminal diagnostic hash is invalid")
    expected_effects = {"nix": False, "store": False, "build": False, "write": False, "install": False, "profile": False, "deploy": False, "keygen": False, "authority_consumption": False}
    if item["effects"] != expected_effects:
        raise ObservationError("terminal effects are invalid")
    claimed = item.pop("terminal_sha256")
    if claimed != digest(canonical(item)):
        raise ObservationError("terminal hash is invalid")
    item["terminal_sha256"] = claimed
    return item


@dataclass(frozen=True)
class Composition:
    schema: str = COMPOSITION_SCHEMA
    status: str = "NOT_EXECUTABLE"
    reason: str = "dedicated production SSH authentication identity is not admitted"

    def execute(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        validate_request(request)
        raise ObservationHold(self.reason)


def _held_regular(path: Path, expected_sha256: str, *, executable: bool = False) -> tuple[int, bytes]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or (executable and not st.st_mode & 0o111):
            raise ObservationError("held artifact type or mode is invalid")
        raw = b""
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            raw += chunk
        if digest(raw) != expected_sha256:
            raise ObservationError("held artifact digest differs")
        os.lseek(fd, 0, os.SEEK_SET)
        return fd, raw
    except Exception:
        os.close(fd)
        raise


def _sealed(name: str, raw: bytes) -> int:
    import fcntl

    fd = os.memfd_create(name, os.MFD_ALLOW_SEALING)
    os.fchmod(fd, 0o400)
    view = memoryview(raw)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            os.close(fd)
            raise ObservationError("sealed artifact write was incomplete")
        view = view[written:]
    fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
    os.lseek(fd, 0, os.SEEK_SET)
    return fd


def _group_empty_or_kill(pgid: int) -> dict[str, bool]:
    import time

    for sent in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sent)
        except ProcessLookupError:
            return {"had_survivor": sent == signal.SIGTERM and False, "removed": True, "reaped": True}
        had_survivor = True
        for _ in range(200):
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                return {"had_survivor": had_survivor, "removed": True, "reaped": True}
            time.sleep(0.01)
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return {"had_survivor": True, "removed": True, "reaped": True}
    return {"had_survivor": True, "removed": False, "reaped": False}


def _post_reap_group_state(pgid: int, prior: Mapping[str, bool]) -> dict[str, bool]:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return {"had_survivor": prior["had_survivor"], "removed": True, "reaped": True}
    return {"had_survivor": prior["had_survivor"], "removed": False, "reaped": True}


def _bounded_stream(process: subprocess.Popen[bytes], stdin: bytes, *, stdout_limit: int, stderr_limit: int, timeout: int) -> tuple[bytes, bytes]:
    selector = selectors.DefaultSelector()
    assert process.stdin and process.stdout and process.stderr
    for stream in (process.stdin, process.stdout, process.stderr):
        os.set_blocking(stream.fileno(), False)
    selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    outputs = {"stdout": bytearray(), "stderr": bytearray()}
    offset = 0
    deadline = datetime.now(timezone.utc).timestamp() + timeout
    try:
        while selector.get_map():
            remaining = deadline - datetime.now(timezone.utc).timestamp()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout)
            for key, _ in selector.select(min(remaining, 0.25)):
                if key.data == "stdin":
                    if offset == len(stdin):
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    try:
                        offset += os.write(key.fd, stdin[offset : offset + 65536])
                    except BrokenPipeError:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                else:
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    outputs[key.data].extend(chunk)
                    limit = stdout_limit if key.data == "stdout" else stderr_limit
                    if len(outputs[key.data]) > limit:
                        raise ObservationError(f"SSH observation {key.data} exceeded bound")
        process.wait(timeout=max(0.1, deadline - datetime.now(timezone.utc).timestamp()))
        return bytes(outputs["stdout"]), bytes(outputs["stderr"])
    finally:
        selector.close()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()


def _bounded_stream_readonly(process: subprocess.Popen[bytes], *, stdout_limit: int, stderr_limit: int, timeout: int) -> tuple[bytes, bytes]:
    selector = selectors.DefaultSelector()
    assert process.stdout and process.stderr
    outputs = {"stdout": bytearray(), "stderr": bytearray()}
    for stream, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, name)
    deadline = datetime.now(timezone.utc).timestamp() + timeout
    try:
        while selector.get_map():
            remaining = deadline - datetime.now(timezone.utc).timestamp()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout)
            for key, _ in selector.select(min(remaining, 0.25)):
                block = os.read(key.fd, 65536)
                if not block:
                    selector.unregister(key.fileobj)
                    continue
                outputs[key.data].extend(block)
                limit = stdout_limit if key.data == "stdout" else stderr_limit
                if len(outputs[key.data]) > limit:
                    raise ObservationError(f"Git {key.data} exceeded bound")
    finally:
        selector.close()
        for stream in (process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
    return bytes(outputs["stdout"]), bytes(outputs["stderr"])


def _run_held_bounded(
    argv: list[str],
    *,
    pass_fds: tuple[int, ...],
    timeout: int,
    limit: int,
    env: Mapping[str, str] | None = None,
) -> tuple[int, bytes, bytes]:
    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        pass_fds=pass_fds,
        env=None if env is None else dict(env),
    )
    failure: Exception | None = None
    try:
        stdout, stderr = _bounded_stream_readonly(process, stdout_limit=limit, stderr_limit=limit, timeout=timeout)
        process.wait(timeout=0.25)
    except Exception as exc:
        failure = exc
        stdout = stderr = b""
    if failure is not None:
        state = _group_empty_or_kill(process.pid)
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired as exc:
            raise ObservationError("bounded helper leader could not be reaped") from exc
        state = _post_reap_group_state(process.pid, state)
    else:
        state = _group_empty_or_kill(process.pid)
    if failure is not None or not state["removed"] or state["had_survivor"]:
        raise ObservationError("bounded helper timed out or left descendants") from failure
    return process.returncode, stdout, stderr


@dataclass(frozen=True)
class SshObservationProvider:
    request: Mapping[str, Any]
    ssh_path: Path
    known_hosts_path: Path
    identity_path: Path
    helper_path: Path
    python_path: str
    mounted_source: Mapping[str, Any] | MountedSourceAuthority | None = None
    ssh_keygen_path: Path | None = None
    source_authority_sha256: str | None = None
    mounted_host_state: MountedHostStateDependency | None = None
    host_state_authority_sha256: str | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise TypeError("SshObservationProvider is sealed")

    def ready(self, request: Mapping[str, Any]) -> bool:
        fds: list[int] = []
        try:
            if validate_request(request)["request_sha256"] != validate_request(self.request)["request_sha256"]:
                return False
            if (
                type(self.mounted_source) is not MountedSourceAuthority
                or self.source_authority_sha256 != self.mounted_source.sha256
                or self.mounted_source.descriptor != request["source"]
                or type(self.mounted_host_state) is not MountedHostStateDependency
                or self.host_state_authority_sha256 != request["host_state_dependency_sha256"]
                or self.mounted_host_state.sha256 != request["host_state_dependency_sha256"]
                or self.mounted_host_state.dependency != request["host_state_dependency"]
            ):
                return False
            self.mounted_host_state.postcheck()
            for path, identity, executable, modes in (
                (self.ssh_path, "ssh_sha256", True, {0o555, 0o755}),
                (self.ssh_keygen_path, "ssh_keygen_sha256", True, {0o555, 0o755}),
                (self.known_hosts_path, "known_hosts_sha256", False, {0o400, 0o444}),
                (self.identity_path, "identity_sha256", False, {0o400}),
                (self.helper_path, "helper_sha256", False, {0o400, 0o444}),
            ):
                fd, raw = _held_regular(path, request["transport"][identity], executable=executable)
                fds.append(fd)
                st = os.fstat(fd)
                if st.st_uid != os.getuid() or st.st_nlink != 1 or stat.S_IMODE(st.st_mode) not in modes:
                    raise ObservationError("held SSH artifact ownership/mode/link count is invalid")
                if identity == "known_hosts_sha256":
                    lines = raw.decode().splitlines()
                    if (
                        len(lines) != 1
                        or len(lines[0].split()) != 3
                        or lines[0].split()[0] != request["target"]["host"]
                        or lines[0].split()[1] not in {"ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256"}
                    ):
                        raise ObservationError("known-hosts grammar is invalid")
                    try:
                        base64.b64decode(lines[0].split()[2], validate=True)
                    except ValueError as exc:
                        raise ObservationError("known-hosts key encoding is invalid") from exc
            keygen_fd = fds[1]
            identity_fd = fds[3]
            derived_rc, derived_stdout, _derived_stderr = _run_held_bounded(
                [f"/proc/{os.getpid()}/fd/{keygen_fd}", "-y", "-f", f"/proc/{os.getpid()}/fd/{identity_fd}"],
                timeout=5,
                pass_fds=(keygen_fd, identity_fd),
                limit=8192,
            )
            if derived_rc or derived_stdout.decode().strip() != request["transport"]["identity_public"]:
                raise ObservationError("dedicated private/public key identity differs")
            return True
        except Exception:
            return False
        finally:
            for fd in reversed(fds):
                os.close(fd)

    def observe(self, request: Mapping[str, Any], *, on_dispatch: Any, _held: tuple[Any, ...] | None = None) -> Mapping[str, Any]:
        request = validate_request(request, now=datetime.now(timezone.utc))
        if _held is None:
            ssh_fd, _ = _held_regular(self.ssh_path, request["transport"]["ssh_sha256"], executable=True)
            hosts_fd, hosts = _held_regular(self.known_hosts_path, request["transport"]["known_hosts_sha256"])
            identity_fd, identity = _held_regular(self.identity_path, request["transport"]["identity_sha256"])
            helper_fd, helper = _held_regular(self.helper_path, request["transport"]["helper_sha256"])
            initial = tuple((os.fstat(fd), digest(raw)) for fd, raw in ((ssh_fd, os.pread(ssh_fd, os.fstat(ssh_fd).st_size, 0)), (hosts_fd, hosts), (identity_fd, identity), (helper_fd, helper)))
            named = tuple((path, _inode_identity(os.stat(path, follow_symlinks=False))) for path in (self.ssh_path, self.known_hosts_path, self.identity_path, self.helper_path))
        else:
            ssh_fd, keygen_fd, hosts_fd, identity_fd, helper_fd, hosts, identity, helper, initial, named = _held
        sealed_hosts = _sealed("a3-observation-hosts", hosts)
        sealed_identity = _sealed("a3-observation-identity", identity)
        try:
            bootstrap = _remote_helper_bootstrap(helper)
            remote = shlex.join([REMOTE_SUDO, "-n", "-u", "db", "--", self.python_path, "-I", "-c", bootstrap])
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
                f"-oUserKnownHostsFile=/proc/{os.getpid()}/fd/{sealed_hosts}",
                f"-oIdentityFile=/proc/{os.getpid()}/fd/{sealed_identity}",
                "-oPasswordAuthentication=no",
                f"{request['target']['user']}@{request['target']['host']}",
                remote,
            ]
            on_dispatch()
            process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, pass_fds=(ssh_fd, sealed_hosts, sealed_identity))
            stream_error: Exception | None = None
            try:
                stdout, _stderr = _bounded_stream(
                    process,
                    canonical(request),
                    stdout_limit=request["bounds"]["max_archive_bytes"] + request["bounds"]["max_output_bytes"] + 16,
                    stderr_limit=request["bounds"]["max_output_bytes"],
                    timeout=request["bounds"]["timeout_seconds"],
                )
            except Exception as exc:
                stream_error = exc
                stdout = b""
            group_state = _group_empty_or_kill(process.pid)
            try:
                process.wait(timeout=1)
                group_state["reaped"] = True
            except subprocess.TimeoutExpired:
                group_state["reaped"] = False
            if stream_error is not None:
                if not group_state["removed"] or not group_state["reaped"]:
                    raise ObservationError("SSH observation timed out or failed with a surviving process group") from stream_error
                raise ObservationError("SSH observation timed out or stream failed and process group was terminated") from stream_error
            if process.returncode != 0:
                raise ObservationError("SSH observation helper failed")
            if group_state["had_survivor"] or not group_state["removed"] or not group_state["reaped"]:
                raise ObservationError("SSH leader exited with surviving process-group members")
            receipt, archive = decode_helper_response(stdout, request)
            return {"receipt": receipt, "archive": archive}
        finally:
            post_error: Exception | None = None
            try:
                if any(
                    _inode_identity(os.fstat(fd)) != _inode_identity(before) or digest(os.pread(fd, before.st_size + 1, 0)) != before_hash
                    for fd, (before, before_hash) in zip(
                        (ssh_fd, keygen_fd, hosts_fd, identity_fd, helper_fd) if _held is not None else (ssh_fd, hosts_fd, identity_fd, helper_fd),
                        initial,
                        strict=True,
                    )
                ):
                    post_error = ObservationError("held SSH artifact identity changed during launch")
                elif any(_inode_identity(os.stat(path, follow_symlinks=False)) != identity_before for path, identity_before in named):
                    post_error = ObservationError("named SSH artifact identity changed during launch")
            except Exception as exc:
                post_error = ObservationError("SSH artifact postcheck failed")
                post_error.__cause__ = exc
            finally:
                for fd in (sealed_identity, sealed_hosts, helper_fd, identity_fd, hosts_fd, *((keygen_fd,) if _held is not None else ()), ssh_fd):
                    os.close(fd)
            if post_error is not None:
                raise post_error

    def prepare_launch(self, request: Mapping[str, Any]) -> Any:
        """Return the single controller-invoked launch; no provider callback can skip consumption."""
        validated = validate_request(request, now=datetime.now(timezone.utc))
        configured = validate_request(self.request)
        if (
            validated != configured
            or self.mounted_source is None
            or self.source_authority_sha256 is None
            or type(self.mounted_source) is not MountedSourceAuthority
            or self.source_authority_sha256 != self.mounted_source.sha256
            or self.mounted_source.descriptor != validated["source"]
            or type(self.mounted_host_state) is not MountedHostStateDependency
            or self.host_state_authority_sha256 != validated["host_state_dependency_sha256"]
            or self.mounted_host_state.sha256 != validated["host_state_dependency_sha256"]
            or self.mounted_host_state.dependency != validated["host_state_dependency"]
        ):
            raise ObservationHold("sealed provider request or source authority differs")
        self.mounted_host_state.postcheck()
        opened: list[int] = []
        try:
            ssh_fd, _ = _held_regular(self.ssh_path, validated["transport"]["ssh_sha256"], executable=True)
            opened.append(ssh_fd)
            keygen_fd, _ = _held_regular(self.ssh_keygen_path, validated["transport"]["ssh_keygen_sha256"], executable=True)
            opened.append(keygen_fd)
            hosts_fd, hosts = _held_regular(self.known_hosts_path, validated["transport"]["known_hosts_sha256"])
            opened.append(hosts_fd)
            identity_fd, identity = _held_regular(self.identity_path, validated["transport"]["identity_sha256"])
            opened.append(identity_fd)
            helper_fd, helper = _held_regular(self.helper_path, validated["transport"]["helper_sha256"])
            opened.append(helper_fd)
        except Exception:
            for fd in reversed(opened):
                os.close(fd)
            raise
        held_fds = (ssh_fd, keygen_fd, hosts_fd, identity_fd, helper_fd)
        modes = ({0o555, 0o755}, {0o555, 0o755}, {0o400, 0o444}, {0o400}, {0o400, 0o444})
        try:
            invalid_metadata = any(
                os.fstat(fd).st_uid != os.getuid() or os.fstat(fd).st_nlink != 1 or stat.S_IMODE(os.fstat(fd).st_mode) not in admitted for fd, admitted in zip(held_fds, modes, strict=True)
            )
            lines = hosts.decode().splitlines()
        except Exception:
            for fd in reversed(opened):
                os.close(fd)
            raise
        if invalid_metadata:
            for fd in held_fds:
                os.close(fd)
            raise ObservationHold("sealed SSH artifact metadata is not admitted")
        if len(lines) != 1 or len(lines[0].split()) != 3 or lines[0].split()[0] != validated["target"]["host"]:
            for fd in held_fds:
                os.close(fd)
            raise ObservationHold("sealed known-host authority is invalid")
        try:
            named = tuple((path, _inode_identity(os.stat(path, follow_symlinks=False))) for path in (self.ssh_path, self.ssh_keygen_path, self.known_hosts_path, self.identity_path, self.helper_path))
            initial_raw = (
                (ssh_fd, os.pread(ssh_fd, os.fstat(ssh_fd).st_size, 0)),
                (keygen_fd, os.pread(keygen_fd, os.fstat(keygen_fd).st_size, 0)),
                (hosts_fd, hosts),
                (identity_fd, identity),
                (helper_fd, helper),
            )
            initial = tuple((os.fstat(fd), digest(raw)) for fd, raw in initial_raw)
        except Exception:
            for fd in reversed(opened):
                os.close(fd)
            raise
        try:
            derived_rc, derived_stdout, _derived_stderr = _run_held_bounded(
                [f"/proc/{os.getpid()}/fd/{keygen_fd}", "-y", "-f", f"/proc/{os.getpid()}/fd/{identity_fd}"],
                timeout=5,
                pass_fds=(keygen_fd, identity_fd),
                limit=8192,
            )
        except Exception:
            for fd in reversed(opened):
                os.close(fd)
            raise
        try:
            keygen_before, keygen_hash = initial[1]
            keygen_public = derived_stdout.decode("utf-8", errors="strict").strip()
            keygen_invalid = (
                bool(derived_rc)
                or keygen_public != validated["transport"]["identity_public"]
                or _inode_identity(os.fstat(keygen_fd)) != _inode_identity(keygen_before)
                or digest(os.pread(keygen_fd, keygen_before.st_size + 1, 0)) != keygen_hash
                or _inode_identity(os.stat(self.ssh_keygen_path, follow_symlinks=False)) != named[1][1]
            )
        except (UnicodeDecodeError, OSError, ValueError) as exc:
            for fd in reversed(opened):
                os.close(fd)
            raise ObservationHold("sealed keygen output or postcheck is invalid") from exc
        if keygen_invalid:
            for fd in reversed(opened):
                os.close(fd)
            raise ObservationHold("sealed private/public authority differs")
        held = (ssh_fd, keygen_fd, hosts_fd, identity_fd, helper_fd, hosts, identity, helper, initial, named)
        used = False

        def launch() -> Mapping[str, Any]:
            nonlocal used
            if used:
                raise ObservationError("sealed observation launch was invoked more than once")
            used = True
            try:
                return self.observe(validated, on_dispatch=lambda: None, _held=held)
            finally:
                self.mounted_host_state.postcheck()

        def close() -> None:
            nonlocal used
            if not used:
                used = True
                for fd in (helper_fd, identity_fd, hosts_fd, keygen_fd, ssh_fd):
                    os.close(fd)
                self.mounted_host_state.postcheck()

        launch.close = close  # type: ignore[attr-defined]
        return launch


class ImmutableEvidenceStore:
    def __init__(self, root: Path, *, trusted_uid: int | None = None):
        self.root = root
        self.trusted_uid = os.getuid() if trusted_uid is None else trusted_uid
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._root_name = self.root.name
        self._parent_fd = os.open(self.root.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self._root_fd = os.open(self._root_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._parent_fd)

    @property
    def identity(self) -> dict[str, Any]:
        st = os.fstat(self._root_fd)
        return {"path": str(self.root), "uid": st.st_uid, "gid": st.st_gid, "mode": stat.S_IMODE(st.st_mode), "dev": st.st_dev, "ino": st.st_ino, "nlink": st.st_nlink}

    def persist(
        self,
        receipt: Mapping[str, Any],
        archive: bytes,
        request: Mapping[str, Any] | None = None,
        attachments: Mapping[str, Mapping[str, Any]] | None = None,
        *,
        before_publish: Any | None = None,
    ) -> tuple[Path, ...]:
        root_fd = self._root_fd
        root_stat = os.fstat(root_fd)
        if root_stat.st_uid != self.trusted_uid or stat.S_IMODE(root_stat.st_mode) != 0o700:
            raise ObservationError("evidence root ownership or mode is invalid")
        identity = str(receipt["receipt_sha256"]).split(":", 1)[1]
        request_raw = canonical(request or {"request_sha256": receipt["request_sha256"]})
        receipt_raw = canonical(receipt)
        raw_attachments = {name: canonical(value) for name, value in (attachments or {}).items()}
        if any(not name.endswith(".json") or "/" in name or name.startswith(".") for name in raw_attachments):
            raise ObservationError("evidence attachment name is invalid")
        manifest = {
            "request_sha256": digest(request_raw),
            "receipt_sha256": digest(receipt_raw),
            "archive_sha256": digest(archive),
            "archive_size": len(archive),
            "attachments": {name: {"sha256": digest(raw), "size": len(raw)} for name, raw in sorted(raw_attachments.items())},
        }
        items = (
            ("request.json", request_raw),
            ("receipt.json", receipt_raw),
            ("archive.tar", archive),
            *((name, raw) for name, raw in sorted(raw_attachments.items())),
            ("manifest.json", canonical(manifest)),
        )
        paths: list[Path] = []
        attempt = ".attempt-" + identity
        attempt_fd = -1
        created_names: list[str] = []
        try:
            try:
                existing = os.open(identity, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            except FileNotFoundError:
                pass
            else:
                os.close(existing)
                raise FileExistsError(identity)
            os.mkdir(attempt, mode=0o700, dir_fd=root_fd)
            attempt_fd = os.open(attempt, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            for name, raw in items:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=attempt_fd)
                created_names.append(name)
                try:
                    view = memoryview(raw)
                    while view:
                        written = os.write(fd, view)
                        if written <= 0:
                            raise ObservationError("evidence write was incomplete")
                        view = view[written:]
                    os.fsync(fd)
                    os.lseek(fd, 0, os.SEEK_SET)
                finally:
                    os.close(fd)
                check_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=attempt_fd)
                try:
                    if digest(os.read(check_fd, len(raw) + 1)) != digest(raw):
                        raise ObservationError("evidence readback differs")
                finally:
                    os.close(check_fd)
                paths.append(self.root / identity / name)
            os.fsync(attempt_fd)
            os.close(attempt_fd)
            attempt_fd = -1
            if before_publish is not None:
                before_publish()
            _rename_noreplace(root_fd, attempt, root_fd, identity)
            os.fsync(root_fd)
        except Exception:
            if attempt_fd < 0:
                try:
                    attempt_fd = os.open(attempt, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
                except FileNotFoundError:
                    attempt_fd = -1
            if attempt_fd >= 0:
                for name in reversed(created_names):
                    try:
                        os.unlink(name, dir_fd=attempt_fd)
                    except FileNotFoundError:
                        pass
                os.close(attempt_fd)
            try:
                os.rmdir(attempt, dir_fd=root_fd)
            except FileNotFoundError:
                pass
            raise
        finally:
            named = os.stat(self._root_name, dir_fd=self._parent_fd, follow_symlinks=False)
            if (named.st_dev, named.st_ino) != (root_stat.st_dev, root_stat.st_ino) or _inode_identity(os.fstat(root_fd))[:5] != _inode_identity(root_stat)[:5]:
                raise ObservationError("evidence root identity changed during persistence")
        return tuple(paths)


def persist_evidence(
    store: ImmutableEvidenceStore,
    *,
    request: Mapping[str, Any],
    receipt: Mapping[str, Any],
    archive: bytes,
    observed_at: str,
    attachments: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[Path, ...]:
    """Retain validated in-memory facts when durable state becomes uncertain."""
    request = validate_request(request)
    receipt = validate_receipt(receipt, request)
    replay_archive(archive, receipt, request)
    try:
        return store.persist(receipt, archive, request, attachments)
    except Exception as exc:
        ambiguous = terminal(
            outcome="AMBIGUOUS",
            stage="persistence",
            code="PERSISTENCE_UNCERTAIN",
            dispatched=True,
            request_sha256=request["request_sha256"],
            observed_at=observed_at,
            diagnostic=type(exc).__name__.encode(),
        )
        raise EvidencePersistenceAmbiguous(ambiguous) from exc


def encode_helper_response(receipt: Mapping[str, Any], archive: bytes) -> bytes:
    header = canonical(receipt)
    return len(header).to_bytes(8, "big") + len(archive).to_bytes(8, "big") + header + archive


def decode_helper_response(raw: bytes, request: Mapping[str, Any]) -> tuple[dict[str, Any], bytes]:
    if len(raw) < 16:
        raise ObservationError("helper response is truncated")
    header_size = int.from_bytes(raw[:8], "big")
    archive_size = int.from_bytes(raw[8:16], "big")
    bounds = validate_request(request)["bounds"]
    if header_size <= 0 or header_size > bounds["max_output_bytes"] or archive_size <= 0 or archive_size > bounds["max_archive_bytes"]:
        raise ObservationError("helper response bounds are invalid")
    if len(raw) != 16 + header_size + archive_size:
        raise ObservationError("helper response length is invalid")
    try:
        receipt = json.loads(raw[16 : 16 + header_size])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ObservationError("helper receipt is malformed") from exc
    archive = raw[16 + header_size :]
    validated = validate_receipt(receipt, request)
    if digest(archive) != validated["repository"]["archive_sha256"] or len(archive) != validated["repository"]["archive_size"]:
        raise ObservationError("helper archive differs from receipt")
    replay_archive(archive, validated, request)
    return validated, archive


def _remote_helper_bootstrap(helper: bytes) -> str:
    """Build the isolated helper command with a registered module identity."""
    return (
        "import sys,types;"
        "module=types.ModuleType('tgw_remote_helper');"
        "sys.modules[module.__name__]=module;"
        "exec(compile("
        + repr(helper.decode())
        + ",'a3-helper','exec'),module.__dict__);"
        "raise SystemExit(module.__dict__['helper_main']())"
    )


def helper_main() -> int:
    """Fixed no-argument remote helper.  It performs only the read-only observation."""
    if len(sys.argv) != 1:
        return 64
    request_raw = sys.stdin.buffer.read(1_048_577)
    try:
        if len(request_raw) > 1_048_576:
            raise ObservationError("request exceeds helper bound")
        request = validate_request(json.loads(request_raw), now=datetime.now(timezone.utc))
        python_real = Path(sys.executable).resolve(strict=True)
        git_real = Path("/run/current-system/sw/bin/git").resolve(strict=True)
        python_fd, _ = _held_regular(python_real, request["transport"]["python_sha256"], executable=True)
        git_fd, _ = _held_regular(git_real, request["transport"]["git_sha256"], executable=True)
        os.close(python_fd)
        try:
            receipt, archive = observe_repository(Path("/home/db/tgw-flake"), request, enforce_owner=True, git_fd=git_fd)
        finally:
            os.close(git_fd)
        sys.stdout.buffer.write(encode_helper_response(receipt, archive))
        return 0
    except ObservationHold:
        return 75
    except Exception:
        return 65


def main() -> int:
    """Controller entrypoint; fail closed until an admitted SSH composition exists."""
    if len(sys.argv) != 1:
        return 64
    try:
        request = validate_request(json.load(sys.stdin))
        Composition().execute(request)
    except ObservationHold as exc:
        json.dump({"schema": COMPOSITION_SCHEMA, "status": "HOLD", "reason": str(exc)}, sys.stdout, sort_keys=True)
        sys.stdout.write("\n")
        return 75
    except Exception:
        return 65
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
