"""Root-only, bounded coordinator for an admitted TGW Context update.

The coordinator is deliberately narrower than a general deployment runner.  A
caller supplies identities and independently produced PASS receipt identities;
it cannot supply a command, filesystem target, service, signer, or credential.
All live targets and commands are compiled from constants plus the signed actor
generation bundle.

Preparation is effect-safe:

* the candidate is copied with ``git clone --no-local --no-hardlinks`` into a
  retained root outside temporary storage and its exact clean commit/tree are
  re-observed;
* release/evidence/actor-generation artifacts are prepared while unselected;
* every bounded live preimage, including actual secret-bearing bytes, is fsynced
  to an immutable root:root journal before a sanitized ledger opening; and
* only the salted whole-journal hash is allowed into shared status evidence.

The immutable journal never changes.  Resume/rollback progress is a separate
root-private record, so the provider binding and the opening ledger hash cannot
be invalidated by a later checkpoint.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import grp
import hashlib
import json
import os
import pwd
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tgw.actor_contract import verify_signed_actor_contract
from tgw.actor_generation_builder import build_actor_generation
from tgw.actor_host_bootstrap import HostPaths, install_actor_host
from tgw.admission_recovery import (
    sign_environment_preflight_receipt,
    validate_environment_preflight_for_admission,
)
from tgw.code_graph.provider import build_snapshot
from tgw.environment_preflight import preflight
from tgw.plan_solver import validate_for_dispatch
from tgw.release_installer import (
    current_generation,
    materialize,
    select_owner_directed,
)
from tgw.release_installer import select as select_release

_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_TRANSACTION = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_MAX_REQUEST = 4 * 1024 * 1024
_MAX_JOURNAL = 8 * 1024 * 1024
_MAX_PREIMAGE_ENTRIES = 20_000
_MAX_PREIMAGE_FILE = 4 * 1024 * 1024
_MAX_PROVIDER_RESPONSE = 8 * 1024 * 1024
_MAX_COLD_TRANSCRIPT = 16 * 1024 * 1024
_MAX_COLD_WORKSPACE = 64 * 1024 * 1024
_MAX_OWNER_DIRECTIVE = 64 * 1024
_DEEPSEEK_UID = 1005
_CLAUDE_UID = 1006
_DEEPSEEK_UNIT = "dsh.service"
_DEEPSEEK_UNIT_PATH = Path("/home/deepseek/.config/systemd/user/dsh.service")
_CLAUDE_EXECUTABLE = Path("/home/claude/.local/bin/claude")
_CLAUDE_INSTRUCTION_ENTRY_POINT = Path("/home/claude/.claude/CLAUDE.md")
_MANAGED_SERVICE_ALLOWLIST = {
    "tgw-coding-provision-pull.timer",
    "tgw-coding-provision-pull.service",
}
_CURRENT_PLAN_SOURCES = (
    "plan/execution/AMENDMENT-20260823-MCP-LIVE-CLIENT-CONVERGENCE.yaml",
    "pp/PP-ACTOR-MCP-BOUNDARY-001.md",
    "plan/execution/targets/W19-W21-MCP-ONLY-ACTOR-HARDENING-v1.yaml",
    "plan/execution/ACTIVE-PLAN-AMENDMENT-PROCESS-v1.yaml",
)
_CURRENT_PLAN_AMENDMENT = Path(_CURRENT_PLAN_SOURCES[0])
_APPROVED_PLAN_REF = "refs/tgw/approved/GOVERNED-EXECUTION-PLATFORM"
_PLAN_SOLUTION_DIRECTORY = Path("plan/execution/solutions")
_HISTORICAL_APPROVED_PLAN_REFS = frozenset(
    {"f0a8cf22b2c7b2f064292a048ffcb8ee98919e99"}
)
_OWNER_DIRECT_SOURCE_LABELS = {"operator-conversation", "operator-console"}
_ALLOWED_PROVIDER_ENDPOINTS = {
    "http://100.68.223.70:7556",
    "http://127.0.0.1:7556",
    "http://[::1]:7556",
}
_PROVIDER_STEPS = {
    "bind-coordinator",
    "nonterminal-transactions",
    "supersede-transactions",
    "quiesce",
    "rebuild",
    "activate",
    "restart",
    "health",
    "verify-actor",
    "rollback",
    "record-context-parent-transition",
}
_EFFECT_ACTIONS = (
    "INSTALL_PLATFORM_TRUST",
    "PUBLISH_ADMISSION",
    "INSTALL_CATALOG",
    "SELECT_RELEASE",
    "INSTALL_ACTOR_HOST",
    "INSTALL_STABLE_LAUNCHER",
    "INSTALL_DIRECT_STATUS",
    "INSTALL_CONFIRMATION_RELAY",
    "RESTART_PROVIDER",
    "BIND_COORDINATOR",
    "QUIESCE_ACTORS",
    "REBUILD_ACTORS",
    "ACTIVATE_ACTORS",
    "VERIFY_COLD_CONTINUITY",
    "TRANSITION_DEEPSEEK_SERVICE",
    "RESTART_ACTORS",
    "HEALTH_ACTORS",
    "VERIFY_ACTORS",
    "FINALIZE_TRANSACTION",
)


class ContextUpdateCoordinatorError(RuntimeError):
    """An update was unsafe, stale, incomplete, or divergent."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _file_hash(path: Path, *, prefixed: bool = True) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    value = digest.hexdigest()
    return "sha256:" + value if prefixed else value


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, body: bytes) -> None:
    view = memoryview(body)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise ContextUpdateCoordinatorError("short durable write")
        view = view[written:]


def _atomic_bytes(
    path: Path,
    body: bytes,
    *,
    mode: int,
    uid: int | None = None,
    gid: int | None = None,
) -> None:
    stage = path.with_name(f".{path.name}.next-{os.getpid()}-{secrets.token_hex(8)}")
    descriptor = os.open(
        stage,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )
    try:
        _write_all(descriptor, body)
        os.fsync(descriptor)
        os.fchmod(descriptor, mode)
        if uid is not None or gid is not None:
            os.fchown(descriptor, -1 if uid is None else uid, -1 if gid is None else gid)
        # Persist ownership/mode as part of the same durable precondition.  A
        # crash after the byte fsync but before metadata persistence must never
        # publish a journal or ledger segment with permissive inherited mode.
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(stage, path)
        _fsync_directory(path.parent)
    finally:
        if stage.exists() and not stage.is_symlink():
            stage.unlink()


def _atomic_json(
    path: Path,
    value: Mapping[str, Any],
    *,
    mode: int,
    uid: int | None = None,
    gid: int | None = None,
) -> None:
    _atomic_bytes(path, _canonical(value) + b"\n", mode=mode, uid=uid, gid=gid)


def _read_json(path: Path, label: str, *, maximum: int = _MAX_JOURNAL) -> dict[str, Any]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            before = os.fstat(descriptor)
            raw = bytearray()
            while len(raw) <= maximum:
                chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ContextUpdateCoordinatorError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or len(raw) > maximum
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise ContextUpdateCoordinatorError(f"{label} changed while read")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContextUpdateCoordinatorError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ContextUpdateCoordinatorError(f"{label} must be a JSON object")
    return value


def _durable(path: Path, label: str) -> Path:
    if (
        not path.is_absolute()
        or ".." in path.parts
        or path == Path("/tmp")
        or Path("/tmp") in path.parents
        or path == Path("/opt/TGW/var/tmp")
        or Path("/opt/TGW/var/tmp") in path.parents
    ):
        raise ContextUpdateCoordinatorError(f"{label} is not a durable path")
    return path


def _exact_root_directory(
    path: Path, *, mode: int, trusted_uid: int, trusted_gid: int
) -> None:
    _durable(path, "private coordinator root")
    observed = path.stat(follow_symlinks=False)
    if (
        path.is_symlink()
        or not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid != trusted_uid
        or observed.st_gid != trusted_gid
        or stat.S_IMODE(observed.st_mode) != mode
    ):
        raise ContextUpdateCoordinatorError("private coordinator root is not exact")


def _ensure_private_directory(
    path: Path, *, trusted_uid: int, trusted_gid: int
) -> None:
    _durable(path, "private coordinator directory")
    if not path.exists() and not path.is_symlink():
        path.mkdir(parents=False, mode=0o700)
        os.chmod(path, 0o700)
        if os.geteuid() == 0:
            os.chown(path, trusted_uid, trusted_gid)
        _fsync_directory(path.parent)
    _exact_root_directory(
        path, mode=0o700, trusted_uid=trusted_uid, trusted_gid=trusted_gid
    )


def _protected_key(path: Path, *, trusted_uid: int) -> bytes:
    try:
        metadata = path.stat(follow_symlinks=False)
        raw = path.read_bytes()
    except OSError as exc:
        raise ContextUpdateCoordinatorError("internal signer is unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != trusted_uid
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or len(raw) != 32
    ):
        raise ContextUpdateCoordinatorError("internal signer is not protected")
    return raw


def _protected_public_key(path: Path, *, trusted_uid: int) -> bytes:
    try:
        metadata = path.stat(follow_symlinks=False)
        raw = path.read_bytes()
    except OSError as exc:
        raise ContextUpdateCoordinatorError("trusted public key is unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != trusted_uid
        or metadata.st_mode & 0o022
        or len(raw) != 32
    ):
        raise ContextUpdateCoordinatorError("trusted public key is not protected")
    return raw


@dataclass(frozen=True)
class CoordinatorPaths:
    source_repository: Path = Path("/opt/TGW/tgw-lib/src/trader-grims-warehouse")
    plan_repository: Path = Path("/opt/TGW/library/plans")
    approved_plan_root: Path = Path("/opt/TGW/library/approved")
    retained_source_root: Path = Path(
        "/var/lib/tgw/context-update/retained-sources"
    )
    artifact_root: Path = Path("/var/lib/tgw/context-update/artifacts")
    evidence_root: Path = Path("/var/lib/tgw/context-update/evidence")
    private_root: Path = Path("/var/lib/tgw/context-update/transactions")
    scratch_root: Path = Path("/var/cache/tgw/context-update")
    host_receipt_root: Path = Path(
        "/var/lib/tgw/context-update/host-bootstrap-receipts"
    )
    governed_authority_config: Path = Path(
        "/etc/tgw/context-update-governed-authority.json"
    )
    fleet_root: Path = Path("/var/lib/tgw/actor-fleet")
    legacy_fleet_root: Path = Path("/opt/TGW/tgw-lib/var/fleet")
    release_root: Path = Path("/opt/TGW/tgw-lib/actor-runtime")
    admission_root: Path = Path("/opt/TGW/tgw-lib/actor-runtime/admissions")
    actor_generation_root: Path = Path(
        "/opt/TGW/tgw-lib/actor-runtime/actor-generations"
    )
    installed_catalog: Path = Path("/etc/tgw/execution-environment-catalog.json")
    startup_binding_root: Path = Path("/etc/tgw/actors")
    provider_unit: Path = Path("/etc/systemd/system/tgw-actor-fleet-provider.service")
    provider_tmpfiles: Path = Path("/etc/tmpfiles.d/tgw-actor-host.conf")
    relay_unit: Path = Path("/etc/systemd/system/tgw-context-confirmation-relay.service")
    stable_launcher: Path = Path("/opt/TGW/tgw-lib/bin/tgw-actor")
    status_executable: Path = Path(
        "/opt/TGW/tgw-lib/bin/tgw-context-generation-status"
    )
    status_sudoers: Path = Path("/etc/sudoers.d/tgw-context-generation-status")
    actor_signer: Path = Path("/var/lib/tgw-platform-signers/actor-contract.key")
    actor_signer_public: Path = Path(
        "/var/lib/tgw-platform-signers/actor-contract.pub"
    )
    environment_signer: Path = Path(
        "/var/lib/tgw-platform-signers/environment-preflight.key"
    )
    environment_signer_public: Path = Path(
        "/var/lib/tgw-platform-signers/environment-preflight.pub"
    )
    admission_signer: Path = Path(
        "/var/lib/tgw-platform-signers/release-admission.key"
    )
    admission_signer_public: Path = Path(
        "/var/lib/tgw-platform-signers/release-admission.pub"
    )
    environment_public_key: Path = Path("/etc/tgw/trust/environment-preflight.pub")
    admission_public_key: Path = Path("/etc/tgw/trust/release-admission.pub")
    actor_public_key: Path = Path("/etc/tgw/trust/actor-contract.pub")
    provider_config: Path = Path(
        "/opt/TGW/tgw-lib/config/tgw-governed-actor-control.json"
    )
    actor_cache_root: Path = Path("/opt/TGW/var/cache/tgw/actors")
    claude_executable: Path = _CLAUDE_EXECUTABLE
    deepseek_unit: Path = _DEEPSEEK_UNIT_PATH
    deepseek_linger: Path = Path("/var/lib/systemd/linger/deepseek")
    provider_environment: Path = Path("/etc/tgw/actor-fleet.env")
    git: Path = Path("/usr/bin/git")
    systemctl: Path = Path("/usr/bin/systemctl")
    systemd_tmpfiles: Path = Path("/usr/bin/systemd-tmpfiles")
    sudo: Path = Path("/usr/bin/sudo")
    loginctl: Path = Path("/usr/bin/loginctl")
    python3: Path = Path("/usr/bin/python3")

    @property
    def ledger_root(self) -> Path:
        return self.fleet_root / "generation-ledger"

    @property
    def fleet_private_root(self) -> Path:
        return self.fleet_root / "private"

    @property
    def ledger_lock(self) -> Path:
        return self.fleet_root / ".generation-ledger.lock"


@dataclass(frozen=True)
class SnapshotTarget:
    target_id: str
    path: Path
    recursive: bool = True


def _run(command: Sequence[str], *, timeout: int = 1800) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        env={
            "HOME": "/root",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "XDG_CONFIG_HOME": "/var/empty",
            "TMPDIR": "/var/cache/tgw/context-update",
        },
    )


def transaction_runner(
    paths: CoordinatorPaths,
    transaction_id: str,
    *,
    trusted_uid: int = 0,
    trusted_gid: int = 0,
) -> Callable[[Sequence[str]], subprocess.CompletedProcess[str]]:
    """Return a no-shell runner with one retained, tracked scratch root."""
    if _TRANSACTION.fullmatch(transaction_id) is None:
        raise ContextUpdateCoordinatorError("transaction scratch identity is invalid")
    scratch_root = paths.scratch_root
    _durable(scratch_root, "transaction scratch root")
    scratch_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(scratch_root, 0o700)
    if os.geteuid() == 0:
        os.chown(scratch_root, trusted_uid, trusted_gid)
    transaction_scratch = scratch_root / transaction_id
    _ensure_private_directory(
        transaction_scratch, trusted_uid=trusted_uid, trusted_gid=trusted_gid
    )

    def run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(command),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=1800,
            env={
                "HOME": "/root",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_NO_LAZY_FETCH": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "XDG_CONFIG_HOME": "/var/empty",
                "TMPDIR": str(transaction_scratch),
            },
        )

    return run


def _required(
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]],
    command: Sequence[str],
    label: str,
) -> str:
    result = runner(command)
    if result.returncode != 0:
        raise ContextUpdateCoordinatorError(f"{label} failed")
    return result.stdout


def _git_identity(
    repository: Path,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]],
) -> tuple[str, str, str]:
    commit = _required(
        runner,
        _protected_git_command(
            CoordinatorPaths.git, repository, "rev-parse", "HEAD^{commit}"
        ),
        "Git commit observation",
    ).strip()
    tree = _required(
        runner,
        _protected_git_command(
            CoordinatorPaths.git, repository, "rev-parse", "HEAD^{tree}"
        ),
        "Git tree observation",
    ).strip()
    dirty = _required(
        runner,
        _protected_git_command(
            CoordinatorPaths.git,
            repository,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ),
        "Git cleanliness observation",
    )
    if _COMMIT.fullmatch(commit) is None or _COMMIT.fullmatch(tree) is None:
        raise ContextUpdateCoordinatorError("Git identity is invalid")
    return commit, tree, dirty


def _protected_git_command(
    executable: Path, repository: Path, *arguments: str
) -> list[str]:
    if not repository.is_absolute() or ".." in repository.parts:
        raise ContextUpdateCoordinatorError("Git repository path is not exact")
    return [
        str(executable),
        "-c", f"safe.directory={repository}",
        "-c", "core.fsmonitor=false",
        "-c", "core.hooksPath=/dev/null",
        "-c", "maintenance.auto=false",
        "-C", str(repository),
        *arguments,
    ]


def _git_call(
    paths: CoordinatorPaths,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]],
    *arguments: str,
    label: str,
) -> str:
    if "-C" in arguments:
        position = arguments.index("-C")
        if position != 0 or len(arguments) < 3:
            raise ContextUpdateCoordinatorError("Git repository observation is unbounded")
        repository = Path(arguments[1])
        command = _protected_git_command(
            paths.git, repository, *arguments[2:]
        )
    else:
        # The only no-repository operation is the fixed local retained clone.
        # Disable all root ambient configuration and force the local transport
        # explicitly; the caller still supplies neither a URL nor a command.
        if not arguments or arguments[0] != "clone":
            raise ContextUpdateCoordinatorError("Git command has no protected repository")
        command = [
            str(paths.git),
            "-c", "protocol.file.allow=always",
            "-c", "core.fsmonitor=false",
            "-c", "core.hooksPath=/dev/null",
            "-c", "maintenance.auto=false",
            arguments[0],
            "--upload-pack=" + " ".join(
                (
                    shlex.quote(str(paths.git)),
                    "-c",
                    shlex.quote(
                        f"safe.directory={paths.source_repository / '.git'}"
                    ),
                    "upload-pack",
                )
            ),
            *arguments[1:],
        ]
    return _required(runner, command, label)


def _validate_retained_checkout(root: Path, *, trusted_uid: int) -> None:
    if root.is_symlink() or not root.is_dir():
        raise ContextUpdateCoordinatorError("retained source root is unsafe")
    for ancestor in (root, *root.parents):
        observed = ancestor.stat(follow_symlinks=False)
        if (
            ancestor.is_symlink()
            or not stat.S_ISDIR(observed.st_mode)
            or observed.st_uid not in (
                {trusted_uid} if trusted_uid == 0 else {0, trusted_uid}
            )
            or observed.st_mode & 0o022
        ):
            raise ContextUpdateCoordinatorError("retained source ancestry is writable")
        if ancestor == Path("/"):
            break
    alternates = root / ".git" / "objects" / "info" / "alternates"
    if alternates.exists() or alternates.is_symlink():
        raise ContextUpdateCoordinatorError("retained source uses object alternates")
    count = 0
    for path in root.rglob("*"):
        count += 1
        if count > _MAX_PREIMAGE_ENTRIES * 20:
            raise ContextUpdateCoordinatorError("retained source exceeds its entry bound")
        metadata = path.stat(follow_symlinks=False)
        if path.is_symlink() or metadata.st_mode & 0o022 or (
            stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1
        ):
            raise ContextUpdateCoordinatorError("retained source contains a link")
        if metadata.st_uid != trusted_uid:
            raise ContextUpdateCoordinatorError("retained source ownership differs")


def retain_source(
    *,
    paths: CoordinatorPaths,
    commit: str,
    tree: str,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] = _run,
    trusted_uid: int = 0,
) -> Path:
    """Create or validate one exact no-local/no-hardlinks retained clone."""
    if _COMMIT.fullmatch(commit) is None or _COMMIT.fullmatch(tree) is None:
        raise ContextUpdateCoordinatorError("candidate Git identity is invalid")
    _durable(paths.retained_source_root, "retained source root")
    paths.retained_source_root.mkdir(parents=True, exist_ok=True, mode=0o755)
    retained_state = paths.retained_source_root.stat(follow_symlinks=False)
    if (
        paths.retained_source_root.is_symlink()
        or not stat.S_ISDIR(retained_state.st_mode)
        or retained_state.st_uid != trusted_uid
        or retained_state.st_mode & 0o022
    ):
        raise ContextUpdateCoordinatorError("retained source container is unsafe")
    os.chmod(paths.retained_source_root, 0o755)
    _fsync_directory(paths.retained_source_root.parent)

    def verify_actor_access(checkout: Path) -> None:
        probe = checkout / "src/tgw/context_mcp_server.py"
        if probe.is_symlink() or not probe.is_file():
            raise ContextUpdateCoordinatorError("retained Context source probe is absent")
        for actor in ("claude", "codex", "deepseek"):
            result = runner(
                [
                    str(paths.sudo), "-n", "-u", actor,
                    "/usr/bin/test", "-x", str(checkout), "-a", "-r", str(probe),
                ]
            )
            if result.returncode != 0:
                raise ContextUpdateCoordinatorError(
                    f"retained source is not readable by actor: {actor}"
                )
    final = paths.retained_source_root / commit
    if final.exists() or final.is_symlink():
        observed_commit = _git_call(
            paths, runner, "-C", str(final), "rev-parse", "HEAD^{commit}", label="retained commit observation"
        ).strip()
        observed_tree = _git_call(
            paths, runner, "-C", str(final), "rev-parse", "HEAD^{tree}", label="retained tree observation"
        ).strip()
        dirty = _git_call(
            paths,
            runner,
            "-C",
            str(final),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            label="retained cleanliness observation",
        )
        if (observed_commit, observed_tree, dirty) != (commit, tree, ""):
            raise ContextUpdateCoordinatorError("existing retained source differs")
        _validate_retained_checkout(final, trusted_uid=trusted_uid)
        verify_actor_access(final)
        return final
    stage = paths.retained_source_root / f".{commit}.next-{secrets.token_hex(8)}"
    if stage.exists() or stage.is_symlink():
        raise ContextUpdateCoordinatorError("retained source stage is occupied")
    try:
        _git_call(
            paths,
            runner,
            "clone",
            "--no-local",
            "--no-hardlinks",
            "--no-checkout",
            "--",
            str(paths.source_repository),
            str(stage),
            label="retained source clone",
        )
        _git_call(
            paths,
            runner,
            "-C",
            str(stage),
            "checkout",
            "--detach",
            commit,
            label="retained source checkout",
        )
        observed_commit = _git_call(
            paths, runner, "-C", str(stage), "rev-parse", "HEAD^{commit}", label="retained commit observation"
        ).strip()
        observed_tree = _git_call(
            paths, runner, "-C", str(stage), "rev-parse", "HEAD^{tree}", label="retained tree observation"
        ).strip()
        dirty = _git_call(
            paths,
            runner,
            "-C",
            str(stage),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            label="retained cleanliness observation",
        )
        gitlinks = _git_call(
            paths,
            runner,
            "-C",
            str(stage),
            "ls-tree",
            "-r",
            commit,
            label="retained tree link observation",
        )
        if (
            (observed_commit, observed_tree, dirty) != (commit, tree, "")
            or any(row.startswith("160000 ") or row.startswith("120000 ") for row in gitlinks.splitlines())
        ):
            raise ContextUpdateCoordinatorError("retained source identity differs")
        _validate_retained_checkout(stage, trusted_uid=trusted_uid)
        for path in sorted(stage.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if path.is_dir():
                os.chmod(path, 0o555)
            elif path.is_file():
                executable = bool(path.stat().st_mode & 0o111)
                os.chmod(path, 0o555 if executable else 0o444)
        os.chmod(stage, 0o555)
        os.replace(stage, final)
        _fsync_directory(paths.retained_source_root)
        verify_actor_access(final)
        return final
    except Exception:
        if stage.exists() and not stage.is_symlink():
            for path in sorted(stage.rglob("*"), key=lambda item: len(item.parts), reverse=True):
                try:
                    os.chmod(path, 0o700 if path.is_dir() else 0o600)
                except OSError:
                    pass
            os.chmod(stage, 0o700)
            shutil.rmtree(stage)
        raise


def _refresh_catalog(source: Path, retained: Path, commit: str) -> dict[str, Any]:
    catalog = _read_json(source, "installed environment catalog", maximum=4 * 1024 * 1024)
    if catalog.get("schema") != "tgw-execution-environment-catalog/v3":
        raise ContextUpdateCoordinatorError("environment catalog schema differs")
    flake = catalog.get("flake_lock")
    bootstrap = catalog.get("bootstrap_revision")
    broker = catalog.get("broker_policy_revision")
    boundary = catalog.get("enforcement_boundary")
    if not all(isinstance(item, Mapping) for item in (flake, bootstrap, broker, boundary)):
        raise ContextUpdateCoordinatorError("environment catalog source bindings are incomplete")
    flake_path = retained / str(flake.get("path"))
    bootstrap_path = retained / str(bootstrap.get("source_relative_path"))
    if not flake_path.is_file() or flake_path.is_symlink() or not bootstrap_path.is_file() or bootstrap_path.is_symlink():
        raise ContextUpdateCoordinatorError("candidate environment source is unavailable")
    catalog["flake_lock"] = {**dict(flake), "sha256": _file_hash(flake_path, prefixed=False)}
    catalog["bootstrap_revision"] = {
        **dict(bootstrap), "content_sha256": _file_hash(bootstrap_path)
    }
    members: dict[str, str] = {}
    for actor in sorted(catalog.get("actors", {})):
        policy = retained / "agent-services" / "harnesses" / actor / "tgw-context-policy.json"
        if policy.is_symlink() or not policy.is_file():
            raise ContextUpdateCoordinatorError(f"candidate actor policy is absent: {actor}")
        members[actor] = _file_hash(policy)
    broker_body = {"schema": "tgw-harness-broker-policy-set/v1", "members": members}
    catalog["broker_policy_revision"] = {
        **broker_body, "content_sha256": _hash(broker_body)
    }
    refreshed_boundary = dict(boundary)
    for group in ("components", "assets"):
        rows = refreshed_boundary.get(group)
        if not isinstance(rows, list):
            raise ContextUpdateCoordinatorError("environment boundary declaration differs")
        updated: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise ContextUpdateCoordinatorError("environment boundary entry differs")
            path = retained / str(row.get("relative_path"))
            if path.is_symlink() or not path.is_file():
                raise ContextUpdateCoordinatorError("candidate boundary component is absent")
            updated.append({**dict(row), "content_sha256": _file_hash(path)})
        refreshed_boundary[group] = updated
    refreshed_boundary["version"] = f"source-{commit}"
    catalog["enforcement_boundary"] = refreshed_boundary
    return catalog


def _plan_evidence(
    paths: CoordinatorPaths,
    *,
    approved_commit: str,
    evidence_commit: str,
    evidence_tree: str,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]],
) -> dict[str, Any]:
    observed_commit = _git_call(
        paths, runner, "-C", str(paths.plan_repository), "rev-parse", "HEAD^{commit}", label="Plan evidence commit observation"
    ).strip()
    observed_tree = _git_call(
        paths, runner, "-C", str(paths.plan_repository), "rev-parse", "HEAD^{tree}", label="Plan evidence tree observation"
    ).strip()
    dirty = _git_call(
        paths,
        runner,
        "-C",
        str(paths.plan_repository),
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        label="Plan evidence cleanliness observation",
    )
    ancestry = runner(
        _protected_git_command(
            paths.git,
            paths.plan_repository,
            "merge-base",
            "--is-ancestor",
            approved_commit,
            evidence_commit,
        )
    )
    if (
        (observed_commit, observed_tree, dirty) != (evidence_commit, evidence_tree, "")
        or ancestry.returncode != 0
    ):
        raise ContextUpdateCoordinatorError("canonical Plan evidence differs")
    source_hashes: dict[str, str] = {}
    for relative in _CURRENT_PLAN_SOURCES:
        path = paths.plan_repository / relative
        if path.is_symlink() or not path.is_file():
            raise ContextUpdateCoordinatorError("canonical Plan source is unavailable")
        source_hashes[relative] = _file_hash(path)
    return {
        "approved_plan": approved_commit,
        "evidence_plan": evidence_commit,
        "evidence_tree": evidence_tree,
        "current_plan_sources": source_hashes,
    }


def validate_update_request(value: Any) -> dict[str, Any]:
    """Validate only operator/governed provenance; live identities are derived.

    In particular, no caller can name the candidate, source tree, Plan,
    solution, or predecessor generation.  Those identities are observed from
    the fixed repositories and protected startup/selector state below.
    """
    fields = {"schema", "transaction_id", "authority"}
    if (
        not isinstance(value, Mapping)
        or set(value) != fields
        or value.get("schema") != "tgw-context-root-update-request/v2"
        or _TRANSACTION.fullmatch(str(value.get("transaction_id", ""))) is None
    ):
        raise ContextUpdateCoordinatorError("root update request fields differ")
    authority = value.get("authority")
    if (
        not isinstance(authority, Mapping)
        or authority.get("schema") != "tgw-context-update-authority/v1"
        or authority.get("mode") not in {"OWNER_DIRECT", "GOVERNED_AUTOMATION"}
    ):
        raise ContextUpdateCoordinatorError("root update authority differs")
    if authority["mode"] == "GOVERNED_AUTOMATION":
        if set(authority) != {"schema", "mode"}:
            raise ContextUpdateCoordinatorError(
                "governed authority cannot accept caller evidence"
            )
        normalized_authority = dict(authority)
    else:
        if set(authority) != {
            "schema", "mode", "instruction_utf8", "source_label"
        }:
            raise ContextUpdateCoordinatorError("owner directive fields differ")
        if (
            authority.get("source_label") not in _OWNER_DIRECT_SOURCE_LABELS
            or not isinstance(authority.get("instruction_utf8"), str)
            or not 1 <= len(authority["instruction_utf8"].encode())
            <= _MAX_OWNER_DIRECTIVE
            or "\x00" in authority["instruction_utf8"]
        ):
            raise ContextUpdateCoordinatorError("owner directive is invalid")
        normalized_authority = dict(authority)
    return {
        "schema": value["schema"],
        "transaction_id": str(value["transaction_id"]),
        "authority": normalized_authority,
    }


def _plan_solution_path(commit: str) -> Path:
    return _PLAN_SOLUTION_DIRECTORY / f"GOVERNED-EXECUTION-PLATFORM-{commit[:7]}.json"


def _plan_ref_commit(
    paths: CoordinatorPaths,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]],
) -> str:
    commit = _git_call(
        paths,
        runner,
        "-C",
        str(paths.plan_repository),
        "rev-parse",
        f"{_APPROVED_PLAN_REF}^{{commit}}",
        label="approved Plan ref observation",
    ).strip()
    if _COMMIT.fullmatch(commit) is None:
        raise ContextUpdateCoordinatorError("approved Plan ref is invalid")
    return commit


def _validated_plan_activation(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContextUpdateCoordinatorError("Plan activation binding is unavailable")
    body = dict(value)
    claimed = body.pop("activation_sha256", None)
    predecessor = body.get("predecessor")
    successor = body.get("successor")
    ref_disposition = body.get("observed_ref_disposition")
    if (
        set(body)
        != {
            "schema",
            "approved_ref",
            "plan_repository",
            "observed_named_ref",
            "predecessor",
            "successor",
            "observed_ref_disposition",
        }
        or body.get("schema") != "tgw-context-plan-activation/v1"
        or body.get("approved_ref") != _APPROVED_PLAN_REF
        or not isinstance(body.get("plan_repository"), str)
        or _COMMIT.fullmatch(str(body.get("observed_named_ref", ""))) is None
        or not isinstance(predecessor, Mapping)
        or set(predecessor)
        != {
            "commit",
            "solution_hash",
            "materialization",
            "control_config_sha256",
        }
        or _COMMIT.fullmatch(str(predecessor.get("commit", ""))) is None
        or _HASH.fullmatch(str(predecessor.get("solution_hash", ""))) is None
        or _HASH.fullmatch(str(predecessor.get("control_config_sha256", "")))
        is None
        or not isinstance(predecessor.get("materialization"), str)
        or not isinstance(successor, Mapping)
        or set(successor)
        != {
            "commit",
            "tree",
            "solution_hash",
            "solution_artifact",
            "solution_artifact_sha256",
            "materialization",
        }
        or any(
            _COMMIT.fullmatch(str(successor.get(name, ""))) is None
            for name in ("commit", "tree")
        )
        or _HASH.fullmatch(str(successor.get("solution_hash", ""))) is None
        or _HASH.fullmatch(str(successor.get("solution_artifact_sha256", "")))
        is None
        or not isinstance(successor.get("solution_artifact"), str)
        or not isinstance(successor.get("materialization"), str)
        or not isinstance(ref_disposition, Mapping)
        or set(ref_disposition) != {"observed_commit", "disposition"}
        or ref_disposition.get("observed_commit") != body.get("observed_named_ref")
        or ref_disposition.get("disposition")
        != (
            "COHERENT_PREDECESSOR"
            if body.get("observed_named_ref") == predecessor.get("commit")
            else (
                "SUCCESSOR_REF_CONFIG_PENDING"
                if body.get("observed_named_ref") == successor.get("commit")
                else "HISTORICAL_ONLY_NOT_ROLLBACK_AUTHORITY"
            )
        )
        or predecessor.get("commit") == successor.get("commit")
        or claimed != _hash(body)
    ):
        raise ContextUpdateCoordinatorError("Plan activation binding differs")
    return {**body, "activation_sha256": claimed}


def _inspect_plan_materialization(
    paths: CoordinatorPaths,
    *,
    materialization: Path,
    expected_commit: str,
    expected_tree: str | None,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]],
) -> dict[str, str]:
    expected_path = paths.approved_plan_root / expected_commit
    if (
        materialization != expected_path
        or materialization.is_symlink()
        or not materialization.is_dir()
    ):
        raise ContextUpdateCoordinatorError("approved Plan materialization is unavailable")
    root = _git_call(
        paths,
        runner,
        "-C",
        str(materialization),
        "rev-parse",
        "--show-toplevel",
        label="approved Plan materialization root",
    ).strip()
    commit = _git_call(
        paths,
        runner,
        "-C",
        str(materialization),
        "rev-parse",
        "HEAD^{commit}",
        label="approved Plan materialization commit",
    ).strip()
    tree = _git_call(
        paths,
        runner,
        "-C",
        str(materialization),
        "rev-parse",
        "HEAD^{tree}",
        label="approved Plan materialization tree",
    ).strip()
    dirty = _git_call(
        paths,
        runner,
        "-C",
        str(materialization),
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        label="approved Plan materialization cleanliness",
    )
    symbolic = runner(
        _protected_git_command(
            paths.git, materialization, "symbolic-ref", "-q", "HEAD"
        )
    )
    if (
        root != str(materialization)
        or commit != expected_commit
        or _COMMIT.fullmatch(tree) is None
        or (expected_tree is not None and tree != expected_tree)
        or dirty
        or symbolic.returncode != 1
    ):
        raise ContextUpdateCoordinatorError("approved Plan materialization differs")
    return {"path": str(materialization), "commit": commit, "tree": tree}


def _prepare_plan_materialization(
    paths: CoordinatorPaths,
    activation: Mapping[str, Any],
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]],
) -> dict[str, str]:
    value = _validated_plan_activation(activation)
    successor = value["successor"]
    destination = Path(str(successor["materialization"]))
    if destination.exists() or destination.is_symlink():
        observed = _inspect_plan_materialization(
            paths,
            materialization=destination,
            expected_commit=str(successor["commit"]),
            expected_tree=str(successor["tree"]),
            runner=runner,
        )
        return {
            **observed,
            "pre_effect_disposition": "RETAIN_IMMUTABLE_UNSELECTED",
        }
    root = paths.approved_plan_root
    if root.is_symlink() or not root.is_dir():
        raise ContextUpdateCoordinatorError("approved Plan root is unavailable")
    result = runner(
        _protected_git_command(
            paths.git,
            paths.plan_repository,
            "worktree",
            "add",
            "--detach",
            str(destination),
            str(successor["commit"]),
        )
    )
    if result.returncode != 0:
        raise ContextUpdateCoordinatorError(
            "successor Plan materialization preparation failed"
        )
    try:
        _fsync_directory(root)
        observed = _inspect_plan_materialization(
            paths,
            materialization=destination,
            expected_commit=str(successor["commit"]),
            expected_tree=str(successor["tree"]),
            runner=runner,
        )
    except Exception:
        cleanup = runner(
            _protected_git_command(
                paths.git,
                paths.plan_repository,
                "worktree",
                "remove",
                "--force",
                str(destination),
            )
        )
        if cleanup.returncode != 0 or destination.exists() or destination.is_symlink():
            raise ContextUpdateCoordinatorError(
                "failed successor Plan materialization was not contained"
            )
        _fsync_directory(root)
        raise
    return {
        **observed,
        "pre_effect_disposition": "RETAIN_IMMUTABLE_UNSELECTED",
    }


def _derive_plan_activation(
    *,
    paths: CoordinatorPaths,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]],
    evidence_commit: str,
    amendment: Mapping[str, Any],
) -> dict[str, Any]:
    predecessor = amendment.get("predecessor")
    successor = amendment.get("successor")
    if not isinstance(predecessor, Mapping) or not isinstance(successor, Mapping):
        raise ContextUpdateCoordinatorError("current Plan amendment binding is invalid")
    predecessor_commit = str(predecessor.get("approved_plan_commit", ""))
    predecessor_solution = str(predecessor.get("approved_solution_hash", ""))
    successor_commit = str(successor.get("plan_commit", ""))
    successor_solution = str(successor.get("solution_hash", ""))
    if (
        _COMMIT.fullmatch(predecessor_commit) is None
        or _HASH.fullmatch(predecessor_solution) is None
        or _COMMIT.fullmatch(successor_commit) is None
        or _HASH.fullmatch(successor_solution) is None
        or successor.get("cutover_receipt") != "pending"
    ):
        raise ContextUpdateCoordinatorError("current Plan successor is not closed")
    ancestry = runner(
        _protected_git_command(
            paths.git,
            paths.plan_repository,
            "merge-base",
            "--is-ancestor",
            successor_commit,
            evidence_commit,
        )
    )
    if ancestry.returncode != 0:
        raise ContextUpdateCoordinatorError("current Plan successor is not in evidence")
    successor_tree = _git_call(
        paths,
        runner,
        "-C",
        str(paths.plan_repository),
        "rev-parse",
        f"{successor_commit}^{{tree}}",
        label="successor Plan tree observation",
    ).strip()
    solution_path = _plan_solution_path(successor_commit)
    raw_solution = _git_call(
        paths,
        runner,
        "-C",
        str(paths.plan_repository),
        "show",
        f"{evidence_commit}:{solution_path.as_posix()}",
        label="successor Plan solution observation",
    ).encode()
    if len(raw_solution) > _MAX_PREIMAGE_FILE:
        raise ContextUpdateCoordinatorError("successor Plan solution exceeds its bound")
    try:
        solution_value = json.loads(raw_solution)
        validate_for_dispatch(
            solution_value, current_plan_commit=successor_commit
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ContextUpdateCoordinatorError(
            "successor Plan solution is not dispatchable"
        ) from exc
    if solution_value.get("solution_hash") != successor_solution:
        raise ContextUpdateCoordinatorError("successor Plan solution hash differs")
    provider_config = _read_json(paths.provider_config, "actor fleet provider config")
    predecessor_materialization = paths.approved_plan_root / predecessor_commit
    if (
        provider_config.get("plan_approved_commit") != predecessor_commit
        or provider_config.get("plan_approved_solution_hash") != predecessor_solution
        or provider_config.get("plan_repository_root") != str(paths.plan_repository)
        or provider_config.get("standalone_plan_root")
        != str(predecessor_materialization)
        or not isinstance(provider_config.get("actor_fleet_provider"), Mapping)
    ):
        raise ContextUpdateCoordinatorError(
            "governed control config is not the coherent Plan predecessor"
        )
    _inspect_plan_materialization(
        paths,
        materialization=predecessor_materialization,
        expected_commit=predecessor_commit,
        expected_tree=None,
        runner=runner,
    )
    observed_ref = _plan_ref_commit(paths, runner)
    if observed_ref not in {
        predecessor_commit,
        successor_commit,
        *_HISTORICAL_APPROVED_PLAN_REFS,
    }:
        raise ContextUpdateCoordinatorError(
            "approved Plan ref is neither predecessor nor classified history"
        )
    body = {
        "schema": "tgw-context-plan-activation/v1",
        "approved_ref": _APPROVED_PLAN_REF,
        "plan_repository": str(paths.plan_repository),
        "observed_named_ref": observed_ref,
        "predecessor": {
            "commit": predecessor_commit,
            "solution_hash": predecessor_solution,
            "materialization": str(predecessor_materialization),
            "control_config_sha256": _hash(provider_config),
        },
        "successor": {
            "commit": successor_commit,
            "tree": successor_tree,
            "solution_hash": successor_solution,
            "solution_artifact": solution_path.as_posix(),
            "solution_artifact_sha256": "sha256:"
            + hashlib.sha256(raw_solution).hexdigest(),
            "materialization": str(paths.approved_plan_root / successor_commit),
        },
        "observed_ref_disposition": {
            "observed_commit": observed_ref,
            "disposition": (
                "COHERENT_PREDECESSOR"
                if observed_ref == predecessor_commit
                else (
                    "SUCCESSOR_REF_CONFIG_PENDING"
                    if observed_ref == successor_commit
                    else "HISTORICAL_ONLY_NOT_ROLLBACK_AUTHORITY"
                )
            ),
        },
    }
    return {**body, "activation_sha256": _hash(body)}


def _derived_update_request(
    request: Mapping[str, Any],
    *,
    paths: CoordinatorPaths,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]],
    owner_directive: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if request["authority"]["mode"] == "OWNER_DIRECT" and not isinstance(
        owner_directive, Mapping
    ):
        raise ContextUpdateCoordinatorError("owner directive was not durably recorded")
    candidate_commit, candidate_tree, candidate_dirty = _git_identity(
        paths.source_repository, runner
    )
    evidence_commit, evidence_tree, plan_dirty = _git_identity(
        paths.plan_repository, runner
    )
    if candidate_dirty or plan_dirty:
        raise ContextUpdateCoordinatorError(
            "canonical source or Plan repository is not clean"
        )
    raw_amendment = _git_call(
        paths,
        runner,
        "-C",
        str(paths.plan_repository),
        "show",
        f"{evidence_commit}:{_CURRENT_PLAN_AMENDMENT.as_posix()}",
        label="current Plan amendment observation",
    )
    try:
        amendment = yaml.safe_load(raw_amendment)
    except (TypeError, KeyError, yaml.YAMLError) as exc:
        raise ContextUpdateCoordinatorError(
            "current Plan amendment binding is invalid"
        ) from exc
    if (
        not isinstance(amendment, Mapping)
        or amendment.get("schema") != "tgw-plan-amendment/v1"
        or amendment.get("id")
        != "AMENDMENT-20260823-MCP-LIVE-CLIENT-CONVERGENCE"
        or amendment.get("status") != "proposed-successor"
    ):
        raise ContextUpdateCoordinatorError("current Plan amendment differs")
    plan_activation = _derive_plan_activation(
        paths=paths,
        runner=runner,
        evidence_commit=evidence_commit,
        amendment=amendment,
    )
    successor = plan_activation["successor"]
    startup_generations: set[str] = set()
    for actor in ("claude", "codex", "deepseek"):
        startup = _read_json(
            paths.startup_binding_root / f"{actor}-startup.json",
            f"{actor} startup binding",
        )
        generation = startup.get("expected_generation")
        if _HASH.fullmatch(str(generation or "")) is None:
            raise ContextUpdateCoordinatorError("startup generation differs")
        startup_generations.add(str(generation))
    if len(startup_generations) != 1:
        raise ContextUpdateCoordinatorError("predecessor actor generation is mixed")
    return {
        "schema": "tgw-context-root-derived-update-request/v2",
        "transaction_id": request["transaction_id"],
        "candidate": {"commit": candidate_commit, "tree": candidate_tree},
        "plan": {
            "approved_commit": successor["commit"],
            "approved_solution": successor["solution_hash"],
            "evidence_commit": evidence_commit,
            "evidence_tree": evidence_tree,
        },
        "plan_activation": plan_activation,
        "expected_current": {
            "release_generation": current_generation(paths.release_root),
            "actor_generation": next(iter(startup_generations)),
        },
        "authority": (
            {
                "schema": "tgw-context-update-authority/v1",
                "mode": "OWNER_DIRECT",
                "directive": dict(owner_directive),
            }
            if request["authority"]["mode"] == "OWNER_DIRECT"
            and isinstance(owner_directive, Mapping)
            else dict(request["authority"])
        ),
    }


def _invocation_identity(pid: int) -> dict[str, Any]:
    try:
        stat_raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        marker = stat_raw.rfind(")")
        fields = stat_raw[marker + 2 :].split()
        start_ticks = int(fields[19])
        executable = os.readlink(f"/proc/{pid}/exe")
        command = Path(f"/proc/{pid}/cmdline").read_bytes()
        metadata = Path(f"/proc/{pid}").stat()
        after = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError, IndexError) as exc:
        raise ContextUpdateCoordinatorError(
            "root coordinator invocation identity is unavailable"
        ) from exc
    if stat_raw != after or not executable.startswith("/"):
        raise ContextUpdateCoordinatorError(
            "root coordinator invocation changed while observed"
        )
    return {
        "pid": pid,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "start_ticks": start_ticks,
        "executable": executable,
        "argv_sha256": "sha256:" + hashlib.sha256(command).hexdigest(),
    }


def _owner_directive(
    transaction_root: Path,
    authority: Mapping[str, Any],
    *,
    trusted_uid: int,
    trusted_gid: int,
    now: Callable[[], datetime],
) -> dict[str, Any]:
    path = transaction_root / "owner-directive.json"
    if path.exists() or path.is_symlink():
        value = _read_json(path, "owner directive")
        _private_file_is_exact(
            path, trusted_uid=trusted_uid, trusted_gid=trusted_gid
        )
        unsigned = dict(value)
        claimed = unsigned.pop("directive_sha256", None)
        if (
            value.get("schema") != "tgw-context-owner-directive/v1"
            or value.get("instruction_utf8") != authority.get("instruction_utf8")
            or value.get("source_label") != authority.get("source_label")
            or claimed != _hash(unsigned)
        ):
            raise ContextUpdateCoordinatorError("owner directive retry differs")
        return value
    body = {
        "schema": "tgw-context-owner-directive/v1",
        "instruction_utf8": authority["instruction_utf8"],
        "source_label": authority["source_label"],
        "observed_at": _utc(now()),
        "invocation": _invocation_identity(os.getpid()),
        "parent_invocation": _invocation_identity(os.getppid()),
        "provenance_semantics": "RECORDED_ASSERTION_NOT_OPERATOR_AUTHENTICATION",
    }
    value = {**body, "directive_sha256": _hash(body)}
    _atomic_json(
        path,
        value,
        mode=0o600,
        uid=trusted_uid,
        gid=trusted_gid,
    )
    _private_file_is_exact(
        path, trusted_uid=trusted_uid, trusted_gid=trusted_gid
    )
    return value


def _public_from_private(private_raw: bytes) -> bytes:
    if len(private_raw) != 32:
        raise ContextUpdateCoordinatorError("canonical signer private key differs")
    try:
        return Ed25519PrivateKey.from_private_bytes(private_raw).public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    except ValueError as exc:
        raise ContextUpdateCoordinatorError("canonical signer private key differs") from exc


def _owner_authority_evidence(
    authority: Mapping[str, Any],
    *,
    candidate: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    directive = authority.get("directive")
    if authority.get("mode") != "OWNER_DIRECT" or not isinstance(
        directive, Mapping
    ):
        raise ContextUpdateCoordinatorError("owner directive evidence is unavailable")
    disposition_body = {
        "schema": "tgw-context-review-disposition/v1",
        "authority_mode": "OWNER_DIRECT",
        "disposition": "NOT_APPLICABLE_OWNER_DIRECT",
        "directive_sha256": directive["directive_sha256"],
        "candidate": dict(candidate),
        "plan": {
            "commit": plan["approved_commit"],
            "solution_hash": plan["approved_solution"],
        },
        "basis": "EXPLICIT_OWNER_DIRECTIVE",
    }
    review_disposition = {
        **disposition_body,
        "disposition_sha256": _hash(disposition_body),
    }
    body = {
        "schema": "tgw-context-owner-directive-summary/v1",
        "authority_mode": "OWNER_DIRECT",
        "source_label": directive["source_label"],
        "observed_at": directive["observed_at"],
        "directive_sha256": directive["directive_sha256"],
        "review_disposition": review_disposition,
        "integrity_semantics": "PLATFORM_SIGNATURE_IS_NOT_OPERATOR_AUTHENTICATION",
    }
    return {**body, "authority_evidence_sha256": _hash(body)}


def _compile_owner_directed_admission(
    *,
    request_id: str,
    candidate: Mapping[str, Any],
    plan: Mapping[str, Any],
    environment: Mapping[str, Any],
    authority_evidence: Mapping[str, Any],
    signing_private_key: bytes,
    issued_at: datetime,
    expires_at: datetime,
) -> dict[str, Any]:
    """Sign bounded integrity evidence without claiming operator authentication."""
    unsigned = {
        "schema": "tgw-context-owner-directed-admission/v1",
        "request_id": request_id,
        "authority_mode": "OWNER_DIRECT",
        "authority_evidence": dict(authority_evidence),
        "candidate": dict(candidate),
        "plan": {
            "commit": plan["approved_commit"],
            "solution_hash": plan["approved_solution"],
        },
        "environment": dict(environment),
        "status": "ADMITTED_OWNER_DIRECT",
        "activation": "declarative-only",
        "operator_authentication": "NOT_PERFORMED_NOT_REQUIRED",
        "issued_at": _utc(issued_at),
        "expires_at": _utc(expires_at),
        "signer_key_id": "tgw-release-admission",
    }
    if expires_at <= issued_at:
        raise ContextUpdateCoordinatorError("owner-directed admission expiry differs")
    hashed = {**unsigned, "receipt_hash": _hash(unsigned)}
    key = Ed25519PrivateKey.from_private_bytes(signing_private_key)
    return {
        **hashed,
        "signature": base64.b64encode(key.sign(_canonical(hashed))).decode(),
    }


def _validate_derived_request(value: Any) -> dict[str, Any]:
    fields = {
        "schema", "transaction_id", "candidate", "plan", "plan_activation",
        "expected_current", "authority",
    }
    candidate = value.get("candidate") if isinstance(value, Mapping) else None
    plan = value.get("plan") if isinstance(value, Mapping) else None
    expected = value.get("expected_current") if isinstance(value, Mapping) else None
    if (
        not isinstance(value, Mapping)
        or set(value) != fields
        or value.get("schema") != "tgw-context-root-derived-update-request/v2"
        or _TRANSACTION.fullmatch(str(value.get("transaction_id", ""))) is None
        or not isinstance(candidate, Mapping)
        or set(candidate) != {"commit", "tree"}
        or any(
            _COMMIT.fullmatch(str(candidate.get(name, ""))) is None
            for name in ("commit", "tree")
        )
        or not isinstance(plan, Mapping)
        or set(plan) != {
            "approved_commit", "approved_solution", "evidence_commit",
            "evidence_tree",
        }
        or any(
            _COMMIT.fullmatch(str(plan.get(name, ""))) is None
            for name in ("approved_commit", "evidence_commit", "evidence_tree")
        )
        or _HASH.fullmatch(str(plan.get("approved_solution", ""))) is None
        or not isinstance(value.get("plan_activation"), Mapping)
        or not isinstance(expected, Mapping)
        or set(expected) != {"release_generation", "actor_generation"}
        or not isinstance(expected.get("release_generation"), (str, type(None)))
        or _HASH.fullmatch(str(expected.get("actor_generation", ""))) is None
        or not isinstance(value.get("authority"), Mapping)
    ):
        raise ContextUpdateCoordinatorError("derived root update request differs")
    plan_activation = _validated_plan_activation(value["plan_activation"])
    if (
        plan_activation["successor"]["commit"] != plan["approved_commit"]
        or plan_activation["successor"]["solution_hash"]
        != plan["approved_solution"]
    ):
        raise ContextUpdateCoordinatorError("derived Plan activation differs")
    return {
        **dict(value),
        "candidate": dict(candidate),
        "plan": dict(plan),
        "plan_activation": plan_activation,
        "expected_current": dict(expected),
        "authority": dict(value["authority"]),
    }


def prepare_trust_projection(
    *,
    paths: CoordinatorPaths,
    transaction_root: Path,
    plan_activation: Mapping[str, Any],
    trusted_uid: int,
    trusted_gid: int,
) -> dict[str, Any]:
    """Bind existing platform-held signers to repaired public projections."""
    activation = _validated_plan_activation(plan_activation)
    pairs = {
        "actor-contract": (paths.actor_signer, paths.actor_signer_public),
        "environment-preflight": (
            paths.environment_signer, paths.environment_signer_public,
        ),
        "release-admission": (paths.admission_signer, paths.admission_signer_public),
    }
    publics: dict[str, bytes] = {}
    for name, (private_path, canonical_public_path) in pairs.items():
        derived = _public_from_private(
            _protected_key(private_path, trusted_uid=trusted_uid)
        )
        canonical = _protected_public_key(
            canonical_public_path, trusted_uid=trusted_uid
        )
        if derived != canonical:
            raise ContextUpdateCoordinatorError(
                f"canonical platform signer pair differs: {name}"
            )
        publics[name] = derived
    manifest_path = transaction_root / "candidate-trust-projection.json"
    config_path = transaction_root / "candidate-provider-config.json"
    if manifest_path.exists() or manifest_path.is_symlink():
        _private_file_is_exact(
            manifest_path, trusted_uid=trusted_uid, trusted_gid=trusted_gid
        )
        projection = _read_json(manifest_path, "candidate trust projection")
        if (
            projection.get("schema") != "tgw-platform-trust-projection/v1"
            or projection.get("public_keys")
            != {
                name: base64.b64encode(raw).decode("ascii")
                for name, raw in sorted(publics.items())
            }
            or projection.get("public_key_sha256")
            != {
                name: "sha256:" + hashlib.sha256(raw).hexdigest()
                for name, raw in sorted(publics.items())
            }
            or projection.get("provider_config_path") != str(config_path)
            or projection.get("plan_activation_sha256")
            != activation["activation_sha256"]
            or _HASH.fullmatch(
                str(projection.get("predecessor_actor_public_sha256", ""))
            ) is None
        ):
            raise ContextUpdateCoordinatorError("candidate trust projection retry differs")
        _private_file_is_exact(
            config_path, trusted_uid=trusted_uid, trusted_gid=trusted_gid
        )
        if _hash(_read_json(config_path, "candidate provider config")) != projection.get(
            "provider_config_sha256"
        ):
            raise ContextUpdateCoordinatorError("candidate provider config retry differs")
        return projection

    provider_config = _read_json(paths.provider_config, "actor fleet provider config")
    if _hash(provider_config) != activation["predecessor"][
        "control_config_sha256"
    ]:
        raise ContextUpdateCoordinatorError(
            "actor fleet provider config changed before projection"
        )
    provider = provider_config.get("actor_fleet_provider")
    if not isinstance(provider, Mapping):
        raise ContextUpdateCoordinatorError("actor fleet provider config differs")
    try:
        predecessor_actor_public = base64.b64decode(
            str(provider.get("contract_public_key", "")), validate=True
        )
    except (TypeError, ValueError) as exc:
        raise ContextUpdateCoordinatorError(
            "predecessor actor verifier identity differs"
        ) from exc
    if len(predecessor_actor_public) != 32:
        raise ContextUpdateCoordinatorError("predecessor actor verifier identity differs")
    updated_provider = dict(provider)
    updated_provider["contract_public_key"] = base64.b64encode(
        publics["actor-contract"]
    ).decode("ascii")
    updated_provider["state_root"] = str(paths.fleet_root)
    successor = activation["successor"]
    updated_config = {
        **provider_config,
        "actor_fleet_provider": updated_provider,
        "plan_approved_commit": successor["commit"],
        "plan_approved_solution_hash": successor["solution_hash"],
        "plan_repository_root": str(paths.plan_repository),
        "standalone_plan_root": successor["materialization"],
    }
    _atomic_json(
        config_path,
        updated_config,
        mode=0o600,
        uid=trusted_uid,
        gid=trusted_gid,
    )
    projection = {
        "schema": "tgw-platform-trust-projection/v1",
        "canonical_public_paths": {
            name: str(pair[1]) for name, pair in sorted(pairs.items())
        },
        "public_keys": {
            name: base64.b64encode(raw).decode("ascii")
            for name, raw in sorted(publics.items())
        },
        "public_key_sha256": {
            name: "sha256:" + hashlib.sha256(raw).hexdigest()
            for name, raw in sorted(publics.items())
        },
        "provider_config_path": str(config_path),
        "provider_config_sha256": _hash(updated_config),
        "plan_activation_sha256": activation["activation_sha256"],
        "predecessor_actor_public_key": base64.b64encode(
            predecessor_actor_public
        ).decode("ascii"),
        "predecessor_actor_public_sha256": "sha256:"
        + hashlib.sha256(predecessor_actor_public).hexdigest(),
        "successor_actor_public_sha256": "sha256:"
        + hashlib.sha256(publics["actor-contract"]).hexdigest(),
    }
    _atomic_json(
        manifest_path,
        projection,
        mode=0o600,
        uid=trusted_uid,
        gid=trusted_gid,
    )
    return projection


def _snapshot_node(
    path: Path,
    *,
    relative: str | None = None,
    recursive: bool = True,
    counter: list[int],
) -> dict[str, Any]:
    counter[0] += 1
    if counter[0] > _MAX_PREIMAGE_ENTRIES:
        raise ContextUpdateCoordinatorError("private preimage entry bound exceeded")
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        value: dict[str, Any] = {
            "kind": "absent", "mode": None, "uid": None, "gid": None,
            "nlink": None, "payload": {},
        }
    except OSError as exc:
        raise ContextUpdateCoordinatorError("private preimage is unavailable") from exc
    else:
        common = {
            "mode": stat.S_IMODE(metadata.st_mode),
            "uid": metadata.st_uid,
            "gid": metadata.st_gid,
            "nlink": metadata.st_nlink,
        }
        if stat.S_ISLNK(metadata.st_mode):
            value = {
                "kind": "symlink", **common,
                "payload": {"target": os.readlink(path)},
            }
        elif stat.S_ISREG(metadata.st_mode):
            if metadata.st_size > _MAX_PREIMAGE_FILE:
                raise ContextUpdateCoordinatorError("private preimage file exceeds its bound")
            try:
                descriptor = os.open(
                    path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
                )
                try:
                    before = os.fstat(descriptor)
                    raw = bytearray()
                    while len(raw) <= _MAX_PREIMAGE_FILE:
                        chunk = os.read(
                            descriptor,
                            min(1024 * 1024, _MAX_PREIMAGE_FILE + 1 - len(raw)),
                        )
                        if not chunk:
                            break
                        raw.extend(chunk)
                    after = os.fstat(descriptor)
                finally:
                    os.close(descriptor)
            except OSError as exc:
                raise ContextUpdateCoordinatorError("private preimage file changed") from exc
            if (
                len(raw) > _MAX_PREIMAGE_FILE
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            ):
                raise ContextUpdateCoordinatorError("private preimage file changed")
            value = {
                "kind": "file", **common,
                "payload": {
                    "encoding": "base64",
                    "content": base64.b64encode(raw).decode("ascii"),
                },
            }
        elif stat.S_ISDIR(metadata.st_mode):
            entries: list[dict[str, Any]] = []
            if recursive:
                try:
                    children = sorted(path.iterdir(), key=lambda item: item.name)
                except OSError as exc:
                    raise ContextUpdateCoordinatorError("private preimage directory changed") from exc
                for child in children:
                    if "/" in child.name or child.name in {"", ".", ".."}:
                        raise ContextUpdateCoordinatorError("private preimage name is unsafe")
                    entries.append(
                        _snapshot_node(
                            child,
                            relative=child.name,
                            recursive=True,
                            counter=counter,
                        )
                    )
            value = {
                "kind": "directory", **common,
                "payload": {
                    "coverage": "recursive" if recursive else "metadata-only",
                    "entries": entries,
                },
            }
        else:
            raise ContextUpdateCoordinatorError("special private preimage is refused")
    if relative is not None:
        value = {"relative_path": relative, **value}
    return value


def snapshot_targets(targets: Iterable[SnapshotTarget]) -> list[dict[str, Any]]:
    """Capture exact bytes and metadata for an internally compiled target set."""
    normalized = sorted(targets, key=lambda item: item.target_id)
    if (
        not normalized
        or len(normalized) > _MAX_PREIMAGE_ENTRIES
        or len({item.target_id for item in normalized}) != len(normalized)
        or len({item.path for item in normalized}) != len(normalized)
    ):
        raise ContextUpdateCoordinatorError("private preimage target set differs")
    result: list[dict[str, Any]] = []
    counter = [0]
    for target in normalized:
        if (
            _TRANSACTION.fullmatch(target.target_id) is None
            or not target.path.is_absolute()
            or ".." in target.path.parts
        ):
            raise ContextUpdateCoordinatorError("private preimage target is unsafe")
        result.append(
            {
                "target_id": target.target_id,
                "path": str(target.path),
                **_snapshot_node(
                    target.path, recursive=target.recursive, counter=counter
                ),
            }
        )
    return result


def _service_preimage(
    paths: CoordinatorPaths,
    service: str,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]],
) -> dict[str, Any]:
    if service not in {
        "tgw-actor-fleet-provider.service",
        "tgw-context-confirmation-relay.service",
        *_MANAGED_SERVICE_ALLOWLIST,
    }:
        raise ContextUpdateCoordinatorError("service preimage target is not allowlisted")
    properties = (
        "LoadState", "ActiveState", "SubState", "UnitFileState", "MainPID",
        "FragmentPath", "ExecMainStartTimestampMonotonic",
    )
    result = runner(
        [
            str(paths.systemctl), "show", service,
            *[f"--property={name}" for name in properties],
            "--no-pager",
        ]
    )
    if result.returncode not in {0, 1}:
        raise ContextUpdateCoordinatorError("service preimage is unavailable")
    observed: dict[str, str] = {}
    for row in result.stdout.splitlines():
        name, separator, value = row.partition("=")
        if separator and name in properties and name not in observed:
            observed[name] = value
    if set(observed) != set(properties):
        raise ContextUpdateCoordinatorError("service preimage fields differ")
    target_id = {
        "tgw-actor-fleet-provider.service": "provider-service",
        "tgw-context-confirmation-relay.service": "relay-service",
    }.get(service, f"managed-service-{hashlib.sha256(service.encode()).hexdigest()[:16]}")
    return {"target_id": target_id, "service": service, "properties": observed}


def _managed_units(provider_config_path: Path) -> tuple[list[str], list[str]]:
    value = _read_json(provider_config_path, "candidate actor fleet provider config")
    provider = value.get("actor_fleet_provider")
    if not isinstance(provider, Mapping):
        raise ContextUpdateCoordinatorError("candidate actor fleet provider config differs")
    managed = provider.get("managed_services")
    quiescence = provider.get("quiescence_units")
    if (
        not isinstance(managed, list)
        or not managed
        or managed != sorted(set(managed))
        or not isinstance(quiescence, list)
        or quiescence != sorted(set(quiescence))
        or any(
            not isinstance(unit, str) or unit not in _MANAGED_SERVICE_ALLOWLIST
            for unit in [*managed, *quiescence]
        )
    ):
        raise ContextUpdateCoordinatorError("provider managed service set is not allowlisted")
    return list(managed), list(quiescence)


def _bounded_read(path: Path, maximum: int, label: str) -> bytes:
    try:
        with path.open("rb") as handle:
            raw = handle.read(maximum + 1)
    except OSError as exc:
        raise ContextUpdateCoordinatorError(f"{label} is unavailable") from exc
    if len(raw) > maximum:
        raise ContextUpdateCoordinatorError(f"{label} exceeds its bound")
    return raw


def _stable_regular_file(
    path: Path, maximum: int, label: str
) -> tuple[os.stat_result, bytes]:
    """Read one regular file through O_NOFOLLOW and reject a racing rewrite."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as exc:
        raise ContextUpdateCoordinatorError(f"{label} is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise ContextUpdateCoordinatorError(f"{label} is unsafe")
        body = bytearray()
        while len(body) <= maximum:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    def identity(value: os.stat_result) -> tuple[int, ...]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_mode,
            value.st_uid,
            value.st_gid,
            value.st_nlink,
        )
    if len(body) > maximum or identity(before) != identity(after):
        raise ContextUpdateCoordinatorError(f"{label} changed during inspection")
    return after, bytes(body)


def _strong_process_identity(pid: int, *, expected_uid: int) -> dict[str, Any]:
    """Match the provider's process identity without returning raw environment."""
    if pid <= 1:
        raise ContextUpdateCoordinatorError("managed service process identity is invalid")
    root = Path("/proc") / str(pid)
    status_rows = _bounded_read(root / "status", 64 * 1024, "process status").decode(
        "utf-8"
    ).splitlines()
    status = {
        row.split(":", 1)[0]: row.split(":", 1)[1].strip()
        for row in status_rows if ":" in row
    }
    raw_stat = _bounded_read(root / "stat", 64 * 1024, "process stat").decode(
        "utf-8"
    )
    arguments = [
        raw.decode("utf-8", errors="replace")
        for raw in _bounded_read(root / "cmdline", 256 * 1024, "process command").split(b"\0")
        if raw
    ]
    executable = root / "exe"
    before = executable.stat()
    executable_path = str(executable.resolve(strict=True))
    executable_hash = _file_hash(executable)
    after = executable.stat()
    try:
        start_ticks = int(raw_stat.rsplit(") ", 1)[1].split()[19])
        uid = int(status["Uid"].split()[0])
        ppid = int(status.get("PPid", "0"))
    except (KeyError, ValueError, IndexError) as exc:
        raise ContextUpdateCoordinatorError("managed process identity differs") from exc
    if uid != expected_uid or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise ContextUpdateCoordinatorError("managed process identity changed")
    shape = [Path(arguments[0]).name if arguments else ""]
    shape.extend(
        item for item in arguments[1:]
        if item.startswith("--") or item in {"-m", "tgw.context_mcp_server"}
    )
    value = {
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="utf-8"
        ).strip(),
        "pid": pid,
        "start_ticks": start_ticks,
        "uid": uid,
        "ppid": ppid,
        "executable_path": executable_path,
        "executable_device": before.st_dev,
        "executable_inode": before.st_ino,
        "executable_sha256": executable_hash,
        "cmdline_shape": shape,
        "cmdline_sha256": _hash(arguments),
    }
    return {**value, "identity_hash": _hash(value)}


def _deepseek_user_command(paths: CoordinatorPaths, *arguments: str) -> list[str]:
    return [
        str(paths.sudo), "-n", "-u", "deepseek", "/usr/bin/env",
        "HOME=/home/deepseek", "XDG_RUNTIME_DIR=/run/user/1005",
        "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1005/bus",
        str(paths.systemctl), "--user", *arguments,
    ]


def _deepseek_service_preimage(
    paths: CoordinatorPaths,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]],
) -> dict[str, Any]:
    try:
        account = pwd.getpwnam("deepseek")
    except KeyError as exc:
        raise ContextUpdateCoordinatorError("DeepSeek service account is unavailable") from exc
    if account.pw_uid != _DEEPSEEK_UID or Path(account.pw_dir) != Path("/home/deepseek"):
        raise ContextUpdateCoordinatorError("DeepSeek service account identity differs")
    if paths.deepseek_unit != _DEEPSEEK_UNIT_PATH or paths.deepseek_unit.is_symlink():
        raise ContextUpdateCoordinatorError("DeepSeek managed unit path differs")
    unit_state = paths.deepseek_unit.stat(follow_symlinks=False)
    if not stat.S_ISREG(unit_state.st_mode) or unit_state.st_uid != _DEEPSEEK_UID:
        raise ContextUpdateCoordinatorError("DeepSeek managed unit is unavailable")
    enabled = runner(
        [
            str(paths.sudo), "-n", "-u", "deepseek", "/usr/bin/env",
            "HOME=/home/deepseek", "XDG_CONFIG_HOME=/home/deepseek/.config",
            str(paths.systemctl), "--user", "--root=/", "is-enabled", _DEEPSEEK_UNIT,
        ]
    )
    unit_file_state = enabled.stdout.strip()
    if enabled.returncode not in {0, 1} or unit_file_state not in {
        "enabled", "disabled", "static", "indirect", "masked"
    }:
        raise ContextUpdateCoordinatorError("DeepSeek unit enablement differs")
    login = runner(
        [
            str(paths.loginctl), "show-user", str(_DEEPSEEK_UID),
            "--property=Linger", "--property=State", "--property=Sessions",
            "--no-pager",
        ]
    )
    login_properties = (
        dict(row.split("=", 1) for row in login.stdout.splitlines() if "=" in row)
        if login.returncode == 0 else {"Linger": "no", "State": "offline", "Sessions": ""}
    )
    if set(login_properties) != {"Linger", "State", "Sessions"}:
        raise ContextUpdateCoordinatorError("DeepSeek logind state differs")
    runtime = Path("/run/user/1005")
    bus = runtime / "bus"
    manager_available = runtime.is_dir() and bus.exists()
    linger_present = paths.deepseek_linger.is_file() and not paths.deepseek_linger.is_symlink()
    if paths.deepseek_linger.exists() and not linger_present:
        raise ContextUpdateCoordinatorError("DeepSeek linger state is unsafe")
    if login_properties["Linger"] not in {"yes", "no"}:
        raise ContextUpdateCoordinatorError("DeepSeek linger projection differs")
    if (login_properties["Linger"] == "yes") != linger_present:
        raise ContextUpdateCoordinatorError("DeepSeek linger file and logind differ")
    properties: dict[str, str] | None = None
    parent_identity: dict[str, Any] | None = None
    if manager_available:
        names = (
            "LoadState", "ActiveState", "SubState", "UnitFileState", "MainPID",
            "FragmentPath", "ExecMainStartTimestampMonotonic",
        )
        result = runner(
            _deepseek_user_command(
                paths, "show", _DEEPSEEK_UNIT,
                *[f"--property={name}" for name in names], "--no-pager",
            )
        )
        if result.returncode not in {0, 1}:
            raise ContextUpdateCoordinatorError("DeepSeek user service state is unavailable")
        properties = dict(
            row.split("=", 1) for row in result.stdout.splitlines() if "=" in row
        )
        if set(properties) != set(names):
            raise ContextUpdateCoordinatorError("DeepSeek user service state differs")
        try:
            main_pid = int(properties["MainPID"])
        except ValueError as exc:
            raise ContextUpdateCoordinatorError("DeepSeek user service PID differs") from exc
        if properties["ActiveState"] == "active":
            parent_identity = _strong_process_identity(
                main_pid, expected_uid=_DEEPSEEK_UID
            )
        elif main_pid != 0:
            raise ContextUpdateCoordinatorError("inactive DeepSeek service has a live PID")
    return {
        "target_id": "deepseek-user-service",
        "service": _DEEPSEEK_UNIT,
        "actor": "deepseek",
        "uid": _DEEPSEEK_UID,
        "unit_path": str(paths.deepseek_unit),
        "unit_sha256": _file_hash(paths.deepseek_unit),
        "unit_mode": stat.S_IMODE(unit_state.st_mode),
        "unit_uid": unit_state.st_uid,
        "unit_gid": unit_state.st_gid,
        "unit_nlink": unit_state.st_nlink,
        "unit_file_state": unit_file_state,
        "runtime_directory": str(runtime),
        "bus_path": str(bus),
        "runtime_present": runtime.is_dir(),
        "manager_available": manager_available,
        "linger_path": str(paths.deepseek_linger),
        "linger_present": linger_present,
        "linger_sha256": _file_hash(paths.deepseek_linger) if linger_present else None,
        "login": login_properties,
        "properties": properties,
        "parent_identity": parent_identity,
    }


def _effect_plan(
    transaction_id: str,
    preimages: Sequence[Mapping[str, Any]],
    service_preimages: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    kinds = {str(item["target_id"]): str(item["kind"]) for item in preimages}
    service_ids = {str(item["target_id"]) for item in service_preimages}
    managed_service_ids = sorted(
        identity for identity in service_ids if identity.startswith("managed-service-")
    )
    actor_ids = sorted(
        identity for identity in kinds
        if identity.startswith(("actor-", "startup-", "parent-"))
    )
    tmpfiles_ids = sorted(
        identity for identity in kinds if identity.startswith("tmpfiles-dir-")
    )
    provider_state_ids = sorted(
        identity for identity in kinds if identity.startswith("provider-state-")
    )
    retained_evidence_ids = {
        "status-executable",
        "status-sudoers",
        "stable-bin-parent",
        "host-bootstrap-receipt",
        "release-selection-receipt",
        "cold-continuity-transcript",
        "cold-continuity-receipt",
        "deepseek-service-action-receipt",
        "deepseek-service-progress",
        "deepseek-linger-token",
        "provider-attestation-receipt",
        "relay-python-interpreter",
        "relay-script",
        "coordinator-terminal-receipt",
    }
    targets_by_action: dict[str, list[tuple[str, str]]] = {
        "INSTALL_PLATFORM_TRUST": [
            ("PLAN_REF", "approved-plan-ref"),
            ("FILESYSTEM", "actor-public-trust"),
            ("FILESYSTEM", "environment-public-trust"),
            ("FILESYSTEM", "admission-public-trust"),
            ("FILESYSTEM", "provider-config"),
        ],
        "PUBLISH_ADMISSION": [("FILESYSTEM", "release-admission")],
        "INSTALL_CATALOG": [("FILESYSTEM", "environment-catalog")],
        "SELECT_RELEASE": [
            ("FILESYSTEM", "release-selector"),
            ("FILESYSTEM", "release-selection-receipt"),
        ],
        "INSTALL_ACTOR_HOST": [
            ("FILESYSTEM", "provider-unit"),
            ("FILESYSTEM", "provider-tmpfiles"),
            ("FILESYSTEM", "host-bootstrap-receipt"),
            *[("FILESYSTEM", identity) for identity in tmpfiles_ids],
        ],
        "INSTALL_STABLE_LAUNCHER": [
            ("FILESYSTEM", "stable-launcher"),
            ("FILESYSTEM", "stable-bin-parent"),
        ],
        "INSTALL_DIRECT_STATUS": [
            ("FILESYSTEM", "status-executable"),
            ("FILESYSTEM", "status-sudoers"),
            ("FILESYSTEM", "stable-bin-parent"),
        ],
        "INSTALL_CONFIRMATION_RELAY": [
            ("FILESYSTEM", "relay-unit"),
            ("FILESYSTEM", "relay-python-interpreter"),
            ("FILESYSTEM", "relay-script"),
        ],
        "RESTART_PROVIDER": [
            ("SERVICE", "provider-service"),
            ("SERVICE", "relay-service"),
            ("FILESYSTEM", "provider-attestation-receipt"),
        ],
        "BIND_COORDINATOR": [
            ("PROVIDER", "actor-fleet-provider-api"),
            *[("FILESYSTEM", identity) for identity in provider_state_ids],
        ],
        "QUIESCE_ACTORS": [
            ("PROVIDER", "actor-fleet-provider-api"),
            *[("SERVICE", identity) for identity in managed_service_ids],
        ],
        "REBUILD_ACTORS": [
            ("PROVIDER", "actor-fleet-provider-api"),
            *[
                ("FILESYSTEM", identity) for identity in kinds
                if identity.startswith("actor-cache-")
            ],
        ],
        "ACTIVATE_ACTORS": [
            ("PROVIDER", "actor-fleet-provider-api"),
            *[("FILESYSTEM", identity) for identity in actor_ids],
        ],
        "VERIFY_COLD_CONTINUITY": [
            ("FILESYSTEM", "transaction-scratch-root"),
            ("FILESYSTEM", "cold-continuity-workspace"),
            ("FILESYSTEM", "cold-continuity-transcript"),
            ("FILESYSTEM", "cold-continuity-receipt"),
        ],
        "RESTART_ACTORS": [
            ("PROVIDER", "actor-fleet-provider-api"),
            *[("SERVICE", identity) for identity in managed_service_ids],
        ],
        "TRANSITION_DEEPSEEK_SERVICE": [
            ("SERVICE", "deepseek-user-service"),
            ("FILESYSTEM", "deepseek-service-action-receipt"),
            ("FILESYSTEM", "deepseek-service-progress"),
            ("FILESYSTEM", "deepseek-linger-token"),
            ("FILESYSTEM", "deepseek-linger"),
            ("PROVIDER", "actor-fleet-provider-api"),
        ],
        "HEALTH_ACTORS": [("PROVIDER", "actor-fleet-provider-api")],
        "VERIFY_ACTORS": [("PROVIDER", "actor-fleet-provider-api")],
        "FINALIZE_TRANSACTION": [
            ("FILESYSTEM", "coordinator-terminal-receipt"),
            ("COORDINATOR", "coordinator-progress"),
        ],
    }
    effects = []
    for sequence, action in enumerate(_EFFECT_ACTIONS, 1):
        targets = []
        for target_class, target_id in targets_by_action[action]:
            if target_class == "FILESYSTEM":
                expected = kinds[target_id]
            elif target_class == "SERVICE":
                if target_id not in service_ids:
                    raise ContextUpdateCoordinatorError("effect service preimage is absent")
                expected = "service"
            elif target_class == "PROVIDER":
                expected = "provider-request"
            elif target_class == "PLAN_REF":
                expected = "git-ref"
            else:
                expected = "private-progress"
            targets.append(
                {
                    "target_class": target_class,
                    "target_id": target_id,
                    "expected_preimage_kind": expected,
                    "rollback_disposition": (
                        "RESTORE_COHERENT_PREDECESSOR"
                        if target_class == "PLAN_REF"
                        else
                        "RETAIN_MONOTONIC"
                        if target_id in retained_evidence_ids
                        or target_id in provider_state_ids
                        else "RESTORE_PREIMAGE"
                    ),
                }
            )
        effects.append(
            {
                "sequence": sequence,
                "action": action,
                "targets": targets,
            }
        )
    body = {
        "schema": "tgw-context-update-effect-plan/v1",
        "transaction_id": transaction_id,
        "effects": effects,
    }
    return {**body, "effect_plan_sha256": _hash(body)}


def _private_file_is_exact(
    path: Path, *, trusted_uid: int, trusted_gid: int
) -> None:
    metadata = path.stat(follow_symlinks=False)
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != trusted_uid
        or metadata.st_gid != trusted_gid
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise ContextUpdateCoordinatorError("private coordinator file is not exact")


def write_private_journal(
    *,
    paths: CoordinatorPaths,
    transaction_id: str,
    request_sha256: str,
    candidate: Mapping[str, Any],
    trust_projection: Mapping[str, Any],
    plan_activation: Mapping[str, Any],
    targets: Sequence[SnapshotTarget],
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] = _run,
    trusted_uid: int = 0,
    trusted_gid: int = 0,
    now: Callable[[], datetime] = _utc_now,
    event_hook: Callable[[str], None] | None = None,
) -> tuple[dict[str, Any], str]:
    """Fsync the immutable actual-preimage journal and return its salted hash."""
    if (
        _TRANSACTION.fullmatch(transaction_id) is None
        or _HASH.fullmatch(request_sha256) is None
    ):
        raise ContextUpdateCoordinatorError("private journal identity is invalid")
    _exact_root_directory(
        paths.private_root,
        mode=0o700,
        trusted_uid=trusted_uid,
        trusted_gid=trusted_gid,
    )
    transaction_root = paths.private_root / transaction_id
    _ensure_private_directory(
        transaction_root, trusted_uid=trusted_uid, trusted_gid=trusted_gid
    )
    journal_path = transaction_root / "private-journal.json"
    preimages = snapshot_targets(targets)
    activation = _validated_plan_activation(plan_activation)
    config_preimages = [
        item for item in preimages if item.get("target_id") == "provider-config"
    ]
    if len(config_preimages) != 1 or config_preimages[0].get("kind") != "file":
        raise ContextUpdateCoordinatorError(
            "governed control config preimage is unavailable"
        )
    try:
        config_raw = base64.b64decode(
            str(config_preimages[0]["payload"]["content"]), validate=True
        )
        config_value = json.loads(config_raw)
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise ContextUpdateCoordinatorError(
            "governed control config preimage differs"
        ) from exc
    if (
        not isinstance(config_value, Mapping)
        or _hash(config_value)
        != activation["predecessor"]["control_config_sha256"]
    ):
        raise ContextUpdateCoordinatorError(
            "governed control config changed before journal"
        )
    managed, quiescence = _managed_units(paths.provider_config)
    service_preimages = [
        _service_preimage(paths, service, runner)
        for service in sorted({
            "tgw-actor-fleet-provider.service",
            "tgw-context-confirmation-relay.service",
            *managed,
            *quiescence,
        })
    ]
    service_preimages.append(_deepseek_service_preimage(paths, runner))
    effect_plan = _effect_plan(transaction_id, preimages, service_preimages)
    body = {
        "schema": "tgw-context-update-private-journal/v1",
        "transaction_id": transaction_id,
        "created_at": _utc(now()),
        "nonce": secrets.token_hex(32),
        "request_sha256": request_sha256,
        "candidate": dict(candidate),
        "trust_projection": dict(trust_projection),
        "plan_activation": activation,
        "managed_services": managed,
        "quiescence_units": quiescence,
        "preimages": preimages,
        "service_preimages": service_preimages,
        "effect_plan": effect_plan,
        "rollback_order": [
            int(item["sequence"]) for item in reversed(effect_plan["effects"])
        ],
    }
    raw = _canonical(body) + b"\n"
    if len(raw) > _MAX_JOURNAL:
        raise ContextUpdateCoordinatorError("private journal exceeds provider bound")
    if journal_path.exists() or journal_path.is_symlink():
        existing = _read_json(journal_path, "private coordinator journal")
        _private_file_is_exact(
            journal_path, trusted_uid=trusted_uid, trusted_gid=trusted_gid
        )
        stable_existing = dict(existing)
        stable_new = dict(body)
        for value in (stable_existing, stable_new):
            value.pop("created_at", None)
            value.pop("nonce", None)
        if stable_existing != stable_new:
            raise ContextUpdateCoordinatorError("private journal retry differs")
        journal = existing
    else:
        _atomic_json(
            journal_path,
            body,
            mode=0o600,
            uid=trusted_uid,
            gid=trusted_gid,
        )
        _private_file_is_exact(
            journal_path, trusted_uid=trusted_uid, trusted_gid=trusted_gid
        )
        journal = body
    if event_hook is not None:
        event_hook("PRIVATE_JOURNAL_FSYNCED")
    return journal, _hash(journal)


def _ledger_entries(
    paths: CoordinatorPaths, *, trusted_uid: int
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    previous: str | None = None
    for sequence, segment in enumerate(sorted(paths.ledger_root.glob("*.json")), 1):
        value = _read_json(segment, "generation ledger segment")
        unsigned = dict(value)
        claimed = unsigned.pop("record_sha256", None)
        metadata = segment.stat(follow_symlinks=False)
        if (
            segment.is_symlink()
            or metadata.st_uid != trusted_uid
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o022
            or value.get("schema") != "tgw-generation-ledger-entry/v1"
            or value.get("sequence") != sequence
            or value.get("previous_record_sha256") != previous
            or claimed != _hash(unsigned)
            or segment.name
            != f"{sequence:012d}-{str(claimed).removeprefix('sha256:')}.json"
        ):
            raise ContextUpdateCoordinatorError("generation ledger chain differs")
        entries.append(value)
        previous = str(claimed)
    return entries


def _legacy_history(paths: CoordinatorPaths) -> list[dict[str, Any]]:
    """Extract only bounded identities from the superseded unprotected state root."""
    if not paths.legacy_fleet_root.exists():
        return []
    if paths.legacy_fleet_root.is_symlink() or not paths.legacy_fleet_root.is_dir():
        raise ContextUpdateCoordinatorError("legacy actor fleet root is unsafe")
    records: list[dict[str, Any]] = []
    directory_fd = os.open(
        paths.legacy_fleet_root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    try:
        names = sorted(
            name for name in os.listdir(directory_fd)
            if name.endswith(".actor-provider.json")
        )
    except OSError:
        os.close(directory_fd)
        raise
    if len(names) > 1000:
        os.close(directory_fd)
        raise ContextUpdateCoordinatorError("legacy actor fleet history exceeds its bound")
    terminal = {"VERIFIED", "ROLLED_BACK", "SUPERSEDED"}
    try:
      for name in names:
        if "/" in name or name in {".", ".."}:
            raise ContextUpdateCoordinatorError("legacy actor fleet name is unsafe")
        descriptor = os.open(
            name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=directory_fd
        )
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_JOURNAL:
                raise ContextUpdateCoordinatorError("legacy actor fleet segment is unsafe")
            raw = bytearray()
            while len(raw) <= _MAX_JOURNAL:
                chunk = os.read(
                    descriptor, min(1024 * 1024, _MAX_JOURNAL + 1 - len(raw))
                )
                if not chunk:
                    break
                raw.extend(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if (
            len(raw) > _MAX_JOURNAL
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            raise ContextUpdateCoordinatorError("legacy actor fleet segment changed")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ContextUpdateCoordinatorError("legacy actor fleet segment is invalid") from exc
        if not isinstance(value, Mapping):
            raise ContextUpdateCoordinatorError("legacy actor fleet segment is invalid")
        transaction_id = str(value.get("transaction_id", ""))
        status_value = str(value.get("status", "UNKNOWN"))[:64]
        if _TRANSACTION.fullmatch(transaction_id) is None:
            raise ContextUpdateCoordinatorError("legacy transaction identity differs")
        request = value.get("request")
        revisions = request.get("revisions") if isinstance(request, Mapping) else None
        identities: dict[str, Any] = {}
        if isinstance(request, Mapping):
            for name in ("predecessor_generation", "successor_generation"):
                candidate = request.get(name)
                if candidate is None or _HASH.fullmatch(str(candidate)):
                    identities[name] = candidate
        if isinstance(revisions, Mapping):
            for name in ("plan", "solution", "source", "source_tree", "catalog"):
                candidate = str(revisions.get(name, ""))
                if _HASH.fullmatch(candidate) or _COMMIT.fullmatch(candidate):
                    identities[name] = candidate
        records.append(
            {
                "transaction_id": transaction_id,
                "provider_status": status_value,
                "disposition": "HISTORICAL" if status_value in terminal else "SUPERSEDED",
                "legacy_file_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
                "legacy_file_name": name,
                "identities": identities,
            }
        )
    finally:
        os.close(directory_fd)
    return records


def _append_ledger_locked(
    *,
    paths: CoordinatorPaths,
    entries: list[dict[str, Any]],
    fields: Mapping[str, Any],
    trusted_uid: int,
    ledger_gid: int,
) -> dict[str, Any]:
    body = {
        "schema": "tgw-generation-ledger-entry/v1",
        **dict(fields),
        "sequence": len(entries) + 1,
        "previous_record_sha256": entries[-1]["record_sha256"] if entries else None,
    }
    record = {**body, "record_sha256": _hash(body)}
    filename = (
        f"{body['sequence']:012d}-"
        f"{record['record_sha256'].removeprefix('sha256:')}.json"
    )
    _atomic_json(
        paths.ledger_root / filename,
        record,
        mode=0o640,
        uid=trusted_uid,
        gid=ledger_gid,
    )
    entries.append(record)
    return record


def append_ledger_opening(
    *,
    paths: CoordinatorPaths,
    journal: Mapping[str, Any],
    journal_sha256: str,
    request: Mapping[str, Any],
    actor_request: Mapping[str, Any],
    admission_receipt: Mapping[str, Any],
    trust_projection: Mapping[str, Any],
    trusted_uid: int = 0,
    trusted_gid: int = 0,
    now: Callable[[], datetime] = _utc_now,
    event_hook: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Append only the sanitized PREPARED opening after the journal is durable."""
    transaction_id = str(journal.get("transaction_id", ""))
    effect_plan = journal.get("effect_plan")
    if (
        _TRANSACTION.fullmatch(transaction_id) is None
        or _HASH.fullmatch(journal_sha256) is None
        or _hash(journal) != journal_sha256
        or not isinstance(effect_plan, Mapping)
        or _HASH.fullmatch(str(effect_plan.get("effect_plan_sha256", ""))) is None
    ):
        raise ContextUpdateCoordinatorError("ledger opening journal binding differs")
    transaction_root = paths.private_root / transaction_id
    _private_file_is_exact(
        transaction_root / "private-journal.json",
        trusted_uid=trusted_uid,
        trusted_gid=trusted_gid,
    )
    paths.ledger_root.mkdir(parents=True, exist_ok=True, mode=0o750)
    ledger_state = paths.ledger_root.stat(follow_symlinks=False)
    fleet_state = paths.fleet_root.stat(follow_symlinks=False)
    if (
        paths.ledger_root.is_symlink()
        or ledger_state.st_uid != trusted_uid
        or ledger_state.st_gid != fleet_state.st_gid
        or stat.S_IMODE(ledger_state.st_mode) != 0o750
    ):
        raise ContextUpdateCoordinatorError("generation ledger root is not protected")
    descriptor = os.open(
        paths.ledger_lock,
        os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        entries = _ledger_entries(paths, trusted_uid=trusted_uid)
        try:
            boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
                encoding="utf-8"
            ).strip()
        except OSError as exc:
            raise ContextUpdateCoordinatorError("boot identity is unavailable") from exc
        for legacy in _legacy_history(paths):
            legacy_event = _hash(
                {
                    "record_role": "LEGACY_HISTORY_IMPORT",
                    "transaction_id": legacy["transaction_id"],
                    "legacy_file_sha256": legacy["legacy_file_sha256"],
                    "legacy_file_name": legacy["legacy_file_name"],
                    "source_root": str(paths.legacy_fleet_root),
                }
            )
            matches = [
                entry for entry in entries if entry.get("event_id") == legacy_event
            ]
            if matches:
                if len(matches) != 1:
                    raise ContextUpdateCoordinatorError("legacy history import is duplicated")
                continue
            _append_ledger_locked(
                paths=paths,
                entries=entries,
                fields={
                    "record_role": "LEGACY_HISTORY_IMPORT",
                    "event_id": legacy_event,
                    "recorded_at": _utc(now()),
                    "boot_id": boot_id,
                    **legacy,
                    "source_root": str(paths.legacy_fleet_root),
                    "source_trust_state": "HISTORICAL_UNPROTECTED_SOURCE",
                    "ancestry_weakness": "ACTOR_GROUP_WRITABLE_PARENT",
                    "first_imported_by_transaction_id": transaction_id,
                },
                trusted_uid=trusted_uid,
                ledger_gid=ledger_state.st_gid,
            )
        event_id = _hash(
            {
                "transaction_id": transaction_id,
                "coordinator_journal_sha256": journal_sha256,
                "effect_plan_sha256": effect_plan["effect_plan_sha256"],
            }
        )
        existing = [entry for entry in entries if entry.get("event_id") == event_id]
        if existing:
            if len(existing) != 1:
                raise ContextUpdateCoordinatorError("ledger opening retry is duplicated")
            opening = existing[0]
        else:
            revisions = actor_request.get("revisions")
            candidate = request.get("candidate")
            plan = request.get("plan")
            activation = _validated_plan_activation(
                request.get("plan_activation")
            )
            if not all(isinstance(value, Mapping) for value in (revisions, candidate, plan)):
                raise ContextUpdateCoordinatorError("ledger opening evidence is incomplete")
            body = {
                "record_role": "COORDINATOR_OPENING",
                "event_id": event_id,
                "recorded_at": _utc(now()),
                "boot_id": boot_id,
                "transaction_id": transaction_id,
                "provider_status": "PREPARED",
                "request_sha256": journal["request_sha256"],
                "actor_request_sha256": _hash(actor_request),
                "coordinator_journal_sha256": journal_sha256,
                "effect_plan_sha256": effect_plan["effect_plan_sha256"],
                "candidate_commit": candidate["commit"],
                "candidate_tree": candidate["tree"],
                "approved_plan_commit": plan["approved_commit"],
                "approved_plan_solution_hash": plan["approved_solution"],
                "plan_activation_sha256": activation["activation_sha256"],
                "predecessor_plan_commit": activation["predecessor"]["commit"],
                "predecessor_plan_solution_hash": activation["predecessor"][
                    "solution_hash"
                ],
                "observed_approved_ref": activation["observed_named_ref"],
                "approved_ref_disposition": activation[
                    "observed_ref_disposition"
                ]["disposition"],
                "evidence_plan_commit": plan["evidence_commit"],
                "evidence_plan_tree": plan["evidence_tree"],
                "source_commit": revisions["source"],
                "source_tree": revisions["source_tree"],
                "current_plan_sources_sha256": _hash(
                    revisions["current_plan_sources"]
                ),
                "catalog_hash": revisions["catalog"],
                "bootstrap_hash": revisions["bootstrap"],
                "broker_policy_hash": revisions["broker_policy"],
                "authority_mode": request["authority"]["mode"],
                "authority_evidence_sha256": admission_receipt[
                    "authority_evidence"
                ]["authority_evidence_sha256"],
                "review_receipt_hash": revisions["review"],
                "review_disposition_sha256": admission_receipt[
                    "authority_evidence"
                ]["review_disposition"]["disposition_sha256"],
                "admission_receipt_hash": revisions["admission"],
                "review_receipt": None,
                "owner_directive_summary": dict(
                    admission_receipt["authority_evidence"]
                ),
                "admission_receipt": dict(admission_receipt),
                "predecessor_actor_public_sha256": trust_projection[
                    "predecessor_actor_public_sha256"
                ],
                "successor_actor_public_sha256": trust_projection[
                    "successor_actor_public_sha256"
                ],
                "trust_projection_sha256": _hash(trust_projection),
                "actor_generation": actor_request["successor_generation"],
            }
            opening = _append_ledger_locked(
                paths=paths,
                entries=entries,
                fields=body,
                trusted_uid=trusted_uid,
                ledger_gid=ledger_state.st_gid,
            )
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
    if event_hook is not None:
        event_hook("SANITIZED_LEDGER_OPENING_FSYNCED")
    return opening


def append_coordinator_event(
    *,
    paths: CoordinatorPaths,
    transaction_id: str,
    record_role: str,
    provider_status: str,
    journal_sha256: str,
    binding_sha256: str,
    evidence: Mapping[str, Any],
    trusted_uid: int = 0,
    now: Callable[[], datetime] = _utc_now,
) -> dict[str, Any]:
    """Append one sanitized idempotent terminal/failure/rollback event."""
    if (
        _TRANSACTION.fullmatch(transaction_id) is None
        or record_role not in {
            "COORDINATOR_TERMINAL", "COORDINATOR_FAILURE", "COORDINATOR_ROLLBACK",
        }
        or provider_status not in {"COMPLETE", "HOLD", "ROLLED_BACK", "SUPERSEDED"}
        or _HASH.fullmatch(journal_sha256) is None
        or _HASH.fullmatch(binding_sha256) is None
    ):
        raise ContextUpdateCoordinatorError("coordinator ledger event differs")
    evidence_hash = _hash(evidence)
    event_id = _hash(
        {
            "record_role": record_role,
            "transaction_id": transaction_id,
            "provider_status": provider_status,
            "coordinator_journal_sha256": journal_sha256,
            "coordinator_binding_sha256": binding_sha256,
            "evidence_sha256": evidence_hash,
        }
    )
    descriptor = os.open(
        paths.ledger_lock,
        os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        entries = _ledger_entries(paths, trusted_uid=trusted_uid)
        existing = [entry for entry in entries if entry.get("event_id") == event_id]
        if existing:
            if len(existing) != 1:
                raise ContextUpdateCoordinatorError("coordinator ledger event duplicated")
            return existing[0]
        try:
            boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
                encoding="utf-8"
            ).strip()
        except OSError as exc:
            raise ContextUpdateCoordinatorError("boot identity is unavailable") from exc
        ledger_gid = paths.ledger_root.stat(follow_symlinks=False).st_gid
        return _append_ledger_locked(
            paths=paths,
            entries=entries,
            fields={
                "record_role": record_role,
                "event_id": event_id,
                "recorded_at": _utc(now()),
                "boot_id": boot_id,
                "transaction_id": transaction_id,
                "provider_status": provider_status,
                "coordinator_journal_sha256": journal_sha256,
                "coordinator_binding_sha256": binding_sha256,
                "evidence": dict(evidence),
                "evidence_sha256": evidence_hash,
            },
            trusted_uid=trusted_uid,
            ledger_gid=ledger_gid,
        )
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def coordinator_binding(
    *,
    actor_request: Mapping[str, Any],
    journal_sha256: str,
    ledger_opening: Mapping[str, Any],
    effect_plan_sha256: str,
) -> dict[str, Any]:
    body = {
        "schema": "tgw-context-update-coordinator-binding/v1",
        "outer_transaction_id": actor_request.get("transaction_id"),
        "actor_request_sha256": _hash(actor_request),
        "coordinator_journal_sha256": journal_sha256,
        "coordinator_ledger_opening_sha256": ledger_opening.get("record_sha256"),
        "effect_plan_sha256": effect_plan_sha256,
    }
    if (
        _TRANSACTION.fullmatch(str(body["outer_transaction_id"])) is None
        or any(
            _HASH.fullmatch(str(body[name])) is None
            for name in body
            if name != "schema" and name != "outer_transaction_id"
        )
    ):
        raise ContextUpdateCoordinatorError("coordinator binding evidence differs")
    return {**body, "binding_sha256": _hash(body)}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _provider_token(path: Path, *, trusted_uid: int) -> str:
    try:
        metadata = path.stat(follow_symlinks=False)
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ContextUpdateCoordinatorError("actor fleet credential is unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != trusted_uid
        or metadata.st_nlink != 1
        or metadata.st_mode & 0o077
        or len(raw) > 16 * 1024
    ):
        raise ContextUpdateCoordinatorError("actor fleet credential is not protected")
    rows = [row for row in raw.splitlines() if row and not row.lstrip().startswith("#")]
    if len(rows) != 1 or not rows[0].startswith("TGW_ACTOR_FLEET_TOKEN="):
        raise ContextUpdateCoordinatorError("actor fleet credential fields differ")
    token = rows[0].partition("=")[2]
    if not token or any(character.isspace() for character in token):
        raise ContextUpdateCoordinatorError("actor fleet credential is invalid")
    return token


class ActorFleetRootClient:
    """Strict provider client; the token cannot be requested or serialized."""

    def __init__(
        self,
        *,
        endpoint: str,
        token: str,
        timeout: float = 120,
        opener: Any | None = None,
    ) -> None:
        parsed = urlsplit(endpoint) if isinstance(endpoint, str) else None
        if (
            endpoint.rstrip("/") not in _ALLOWED_PROVIDER_ENDPOINTS
            or parsed is None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
            or not token
            or any(character.isspace() for character in token)
        ):
            raise ContextUpdateCoordinatorError("actor fleet endpoint or credential differs")
        self.endpoint = endpoint.rstrip("/")
        self._token = token
        self.timeout = timeout
        self._opener = opener or build_opener(ProxyHandler({}), _NoRedirect())

    def invoke(self, step: str, arguments: Sequence[Any]) -> dict[str, Any]:
        if step not in _PROVIDER_STEPS:
            raise ContextUpdateCoordinatorError("actor fleet step is not allowlisted")
        invocation = {
            "schema": "tgw-actor-fleet-provider-invocation/v1",
            "step": step,
            "arguments": list(arguments),
        }
        body = {**invocation, "invocation_hash": _hash(invocation)}
        request = Request(
            self.endpoint + "/v1/actor-fleet/" + step,
            data=_canonical(body),
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self._token,
            },
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(_MAX_PROVIDER_RESPONSE + 1)
                status = response.status
        except (HTTPError, URLError, OSError) as exc:
            raise ContextUpdateCoordinatorError("actor fleet invocation failed") from exc
        if status != 200 or len(raw) > _MAX_PROVIDER_RESPONSE:
            raise ContextUpdateCoordinatorError("actor fleet response is invalid")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContextUpdateCoordinatorError("actor fleet response is invalid") from exc
        if (
            not isinstance(value, Mapping)
            or value.get("schema") != "tgw-actor-fleet-provider-response/v1"
            or value.get("step") != step
            or value.get("invocation_hash") != body["invocation_hash"]
            or not isinstance(value.get("result"), Mapping)
        ):
            raise ContextUpdateCoordinatorError("actor fleet response differs")
        return dict(value["result"])


def _artifact_directory(path: Path, *, mode: int = 0o755) -> None:
    _durable(path, "coordinator artifact directory")
    path.mkdir(parents=True, exist_ok=True, mode=mode)
    if path.is_symlink() or not path.is_dir():
        raise ContextUpdateCoordinatorError("coordinator artifact directory is unsafe")
    metadata = path.stat(follow_symlinks=False)
    if (os.geteuid() == 0 and metadata.st_uid != 0) or metadata.st_mode & 0o022:
        raise ContextUpdateCoordinatorError("coordinator artifact directory is not protected")


def _write_artifact(path: Path, value: Mapping[str, Any], *, mode: int = 0o444) -> None:
    if path.exists() or path.is_symlink():
        existing = _read_json(path, "coordinator evidence artifact")
        if existing != dict(value):
            raise ContextUpdateCoordinatorError("coordinator evidence artifact differs")
        return
    _atomic_json(path, value, mode=mode, uid=0 if os.geteuid() == 0 else None, gid=0 if os.geteuid() == 0 else None)


def _archive_and_materialize(
    *,
    paths: CoordinatorPaths,
    retained: Path,
    commit: str,
    tree: str,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]],
) -> tuple[str, Path, dict[str, Any]]:
    _artifact_directory(paths.artifact_root)
    archive = paths.artifact_root / f"{commit}.tar"
    if not archive.exists() and not archive.is_symlink():
        stage = archive.with_name(f".{archive.name}.next-{secrets.token_hex(8)}")
        try:
            _git_call(
                paths,
                runner,
                "-C",
                str(retained),
                "archive",
                "--format=tar",
                f"--output={stage}",
                commit,
                label="candidate source archive",
            )
            descriptor = os.open(stage, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.chmod(stage, 0o444)
            os.replace(stage, archive)
            _fsync_directory(archive.parent)
        finally:
            if stage.exists() and not stage.is_symlink():
                stage.unlink()
    if archive.is_symlink() or not archive.is_file():
        raise ContextUpdateCoordinatorError("candidate source archive is unsafe")
    archive_sha256 = _file_hash(archive, prefixed=False)
    generation = f"context-{commit[:12]}-{tree[:12]}"
    manifest = materialize(
        paths.release_root,
        archive,
        generation=generation,
        commit=commit,
        tree=tree,
        archive_sha256=archive_sha256,
    )
    if current_generation(paths.release_root) == generation:
        raise ContextUpdateCoordinatorError("candidate release was selected during preparation")
    return generation, archive, manifest


def _actor_bundle(
    paths: CoordinatorPaths, generation: str
) -> dict[str, Any]:
    root = paths.actor_generation_root / generation.removeprefix("sha256:")
    return _read_json(root / "bundle.json", "actor generation bundle")


def _bounded_actor_targets(
    *,
    paths: CoordinatorPaths,
    bundle: Mapping[str, Any],
    admission_receipt_hash: str,
    release_generation: str,
    transaction_id: str,
    tmpfiles_source: Path,
) -> list[SnapshotTarget]:
    actors = bundle.get("actors")
    generation = str(bundle.get("generation", ""))
    if (
        not isinstance(actors, Mapping)
        or not actors
        or _HASH.fullmatch(generation) is None
    ):
        raise ContextUpdateCoordinatorError("actor generation bundle is incomplete")
    targets = [
        SnapshotTarget("actor-public-trust", paths.actor_public_key),
        SnapshotTarget("environment-public-trust", paths.environment_public_key),
        SnapshotTarget("admission-public-trust", paths.admission_public_key),
        SnapshotTarget("provider-config", paths.provider_config),
        SnapshotTarget("release-admission", paths.admission_root / f"{admission_receipt_hash.removeprefix('sha256:')}.json"),
        SnapshotTarget("environment-catalog", paths.installed_catalog),
        SnapshotTarget("release-selector", paths.release_root / "current"),
        SnapshotTarget(
            "release-selection-receipt",
            paths.release_root / "receipts" / f"select-{transaction_id}.json",
        ),
        SnapshotTarget("provider-unit", paths.provider_unit),
        SnapshotTarget("provider-tmpfiles", paths.provider_tmpfiles),
        SnapshotTarget(
            "host-bootstrap-receipt",
            paths.host_receipt_root / f"host-{transaction_id}.json",
        ),
        SnapshotTarget("relay-unit", paths.relay_unit),
        SnapshotTarget("relay-python-interpreter", paths.python3, recursive=False),
        SnapshotTarget(
            "relay-script",
            paths.release_root / "releases" / release_generation
            / "src/tgw/context_confirmation_relay.py",
            recursive=False,
        ),
        SnapshotTarget("stable-launcher", paths.stable_launcher),
        SnapshotTarget("stable-bin-parent", paths.stable_launcher.parent, recursive=False),
        SnapshotTarget("status-executable", paths.status_executable),
        SnapshotTarget("status-sudoers", paths.status_sudoers),
        SnapshotTarget(
            "provider-state-journal",
            paths.fleet_private_root / f"{transaction_id}.actor-provider.json",
        ),
        SnapshotTarget(
            "provider-state-materializer",
            paths.fleet_private_root / f"{transaction_id}.actor-materializer.json",
        ),
        SnapshotTarget(
            "provider-state-projection", paths.fleet_root / "fleet-convergence.json"
        ),
        SnapshotTarget(
            "provider-state-pointer",
            paths.fleet_root / "active-fleet-transaction.json",
        ),
        SnapshotTarget(
            "cold-continuity-workspace",
            paths.scratch_root / transaction_id / "claude-cold-continuity",
        ),
        SnapshotTarget(
            "transaction-scratch-root",
            paths.scratch_root / transaction_id,
            recursive=False,
        ),
        SnapshotTarget(
            "cold-continuity-transcript",
            paths.private_root / transaction_id / "cold-continuity-transcript.jsonl",
        ),
        SnapshotTarget(
            "cold-continuity-receipt",
            paths.private_root / transaction_id / "cold-continuity-receipt.json",
        ),
        SnapshotTarget(
            "deepseek-service-action-receipt",
            paths.private_root / transaction_id / "deepseek-service-action.json",
        ),
        SnapshotTarget(
            "deepseek-service-progress",
            paths.private_root / transaction_id / "deepseek-service-progress.json",
        ),
        SnapshotTarget(
            "deepseek-linger-token",
            paths.private_root / transaction_id / "deepseek-linger-token",
        ),
        SnapshotTarget("deepseek-linger", paths.deepseek_linger),
        SnapshotTarget(
            "provider-attestation-receipt",
            paths.private_root / transaction_id / "provider-attestation.json",
        ),
        SnapshotTarget(
            "coordinator-terminal-receipt",
            paths.private_root / transaction_id / "terminal-receipt.json",
        ),
    ]
    try:
        tmpfiles_rows = tmpfiles_source.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ContextUpdateCoordinatorError("candidate tmpfiles policy is unavailable") from exc
    for row in tmpfiles_rows:
        stripped = row.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if (
            len(fields) != 6
            or fields[0] != "d"
            or not Path(fields[1]).is_absolute()
            or ".." in Path(fields[1]).parts
            or "%" in fields[1]
        ):
            raise ContextUpdateCoordinatorError("candidate tmpfiles policy is unbounded")
        path = Path(fields[1])
        identity = hashlib.sha256(str(path).encode()).hexdigest()[:16]
        targets.append(
            SnapshotTarget(f"tmpfiles-dir-{identity}", path, recursive=False)
        )
    observed_paths = {target.path for target in targets}
    parent_targets: dict[Path, SnapshotTarget] = {}
    for actor, raw in sorted(actors.items()):
        if not isinstance(raw, Mapping):
            raise ContextUpdateCoordinatorError("actor generation entry differs")
        home = Path(str(raw.get("home", "")))
        expected_home = Path("/home") / str(actor)
        bindings = raw.get("bindings")
        if home != expected_home or not isinstance(bindings, list):
            raise ContextUpdateCoordinatorError("actor generation home differs")
        startup = paths.startup_binding_root / f"{actor}-startup.json"
        targets.append(SnapshotTarget(f"startup-{actor}", startup))
        targets.append(
            SnapshotTarget(
                f"actor-cache-{actor}",
                paths.actor_cache_root / str(actor) / generation.removeprefix("sha256:"),
            )
        )
        observed_paths.add(startup)
        for index, binding in enumerate(bindings):
            if not isinstance(binding, Mapping):
                raise ContextUpdateCoordinatorError("actor generation binding differs")
            destination = Path(str(binding.get("destination", "")))
            if (
                not destination.is_absolute()
                or ".." in destination.parts
                or destination == home
                or home not in destination.parents
            ):
                raise ContextUpdateCoordinatorError("actor generation destination escapes actor home")
            if destination in observed_paths:
                raise ContextUpdateCoordinatorError("actor generation destination is duplicated")
            target_id = f"actor-{actor}-{index:03d}"
            targets.append(SnapshotTarget(target_id, destination))
            observed_paths.add(destination)
            parent = destination.parent
            while parent != home:
                if parent not in observed_paths and parent not in parent_targets:
                    identity = hashlib.sha256(str(parent).encode()).hexdigest()[:16]
                    parent_targets[parent] = SnapshotTarget(
                        f"parent-{identity}", parent, recursive=False
                    )
                parent = parent.parent
    targets.extend(parent_targets.values())
    by_path: dict[Path, SnapshotTarget] = {}
    for target in targets:
        existing = by_path.get(target.path)
        if existing is None:
            by_path[target.path] = target
        elif existing.recursive is False and target.recursive is True:
            by_path[target.path] = target
    return sorted(by_path.values(), key=lambda item: item.target_id)


def prepare_artifacts(
    *,
    request: Mapping[str, Any],
    paths: CoordinatorPaths,
    trust_projection: Mapping[str, Any],
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] = _run,
    trusted_uid: int = 0,
    now: Callable[[], datetime] = _utc_now,
) -> dict[str, Any]:
    """Build all unselected candidate and fresh evidence artifacts."""
    value = _validate_derived_request(request)
    candidate, plan = value["candidate"], value["plan"]
    observed_current = current_generation(paths.release_root)
    if observed_current != value["expected_current"]["release_generation"]:
        raise ContextUpdateCoordinatorError("selected release CAS differs before preparation")
    plan_evidence = _plan_evidence(
        paths,
        approved_commit=plan["approved_commit"],
        evidence_commit=plan["evidence_commit"],
        evidence_tree=plan["evidence_tree"],
        runner=runner,
    )
    plan_materialization = _prepare_plan_materialization(
        paths, value["plan_activation"], runner
    )
    retained = retain_source(
        paths=paths,
        commit=candidate["commit"],
        tree=candidate["tree"],
        runner=runner,
        trusted_uid=trusted_uid,
    )
    release_generation, archive, release_manifest = _archive_and_materialize(
        paths=paths,
        retained=retained,
        commit=candidate["commit"],
        tree=candidate["tree"],
        runner=runner,
    )
    code_graph = build_snapshot(retained, candidate["commit"])
    if (
        code_graph.get("commit") != candidate["commit"]
        or code_graph.get("tree") != candidate["tree"]
        or _HASH.fullmatch(str(code_graph.get("freshness_hash", ""))) is None
    ):
        raise ContextUpdateCoordinatorError("fresh CodeGraph evidence differs")
    _artifact_directory(paths.evidence_root)
    evidence_dir = paths.evidence_root / value["transaction_id"]
    _artifact_directory(evidence_dir, mode=0o750)
    catalog = _refresh_catalog(paths.installed_catalog, retained, candidate["commit"])
    catalog_hash = _hash(catalog)
    catalog_path = evidence_dir / "environment-catalog.json"
    _write_artifact(catalog_path, catalog)
    _write_artifact(evidence_dir / "codegraph.json", code_graph)

    issue_time = now()
    expiry_time = issue_time + timedelta(hours=1)
    authority = value["authority"]
    if authority.get("mode") == "OWNER_DIRECT":
        authority_evidence = _owner_authority_evidence(
            authority, candidate=candidate, plan=plan
        )
        review_receipt: dict[str, Any] | None = None
        controller_receipt: dict[str, Any] | None = None
    else:
        # Future routine autonomous updates use the separately provisioned,
        # root-protected pinned evidence graph.  Caller-supplied PASS envelopes
        # are never accepted as a substitute.  This bootstrap intentionally
        # selects OWNER_DIRECT because that graph is not yet live.
        raise ContextUpdateCoordinatorError(
            "protected governed automation evidence is not configured"
        )
    environment_key = _protected_key(paths.environment_signer, trusted_uid=trusted_uid)
    actor_declarations = catalog.get("actors")
    descriptor = _read_json(
        retained / "config/environment/actor-generation-descriptor-v1.json",
        "actor generation descriptor",
    )
    if not isinstance(actor_declarations, Mapping):
        raise ContextUpdateCoordinatorError("environment catalog actor set differs")
    signed_preflights: dict[str, dict[str, Any]] = {}
    for actor in sorted(
        name for name, declaration in actor_declarations.items()
        if isinstance(declaration, Mapping) and declaration.get("enabled") is True
    ):
        specification = descriptor.get("actors", {}).get(actor)
        if not isinstance(specification, Mapping):
            raise ContextUpdateCoordinatorError("actor descriptor coverage differs")
        receipt = preflight(
            catalog=catalog,
            actor=actor,
            profile=str(specification.get("profile")),
            attempt_id=f"ctx-{candidate['commit'][:12]}",
            request_id=f"ctx-{candidate['commit'][:12]}",
            boundary_root=retained,
        )
        signed = sign_environment_preflight_receipt(
            receipt,
            signing_private_key=environment_key,
            signer_key_id="tgw-environment-preflight",
            issued_at=_utc(issue_time),
            expires_at=_utc(expiry_time),
        )
        validate_environment_preflight_for_admission(
            signed,
            catalog_hash=catalog_hash,
            receipt_hash=signed["receipt_hash"],
            trusted_public_key=base64.b64decode(
                str(trust_projection["public_keys"]["environment-preflight"]),
                validate=True,
            ),
            current_time=_utc(issue_time),
        )
        signed_preflights[str(actor)] = signed
        _write_artifact(evidence_dir / f"preflight-{actor}.json", signed)
    if not signed_preflights:
        raise ContextUpdateCoordinatorError("no enabled actor environment was preflighted")

    paths.actor_generation_root.mkdir(parents=True, exist_ok=True, mode=0o755)
    actor_generation = build_actor_generation(
        catalog_path=catalog_path,
        descriptor_path=retained / "config/environment/actor-generation-descriptor-v1.json",
        source_root=retained,
        context_source_root=retained,
        output_root=paths.actor_generation_root,
        signing_key_path=paths.actor_signer,
        plan_commit=plan["approved_commit"],
        solution_hash=plan["approved_solution"],
        source_commit=candidate["commit"],
        source_tree=candidate["tree"],
        freshness_hash=code_graph["freshness_hash"],
    )
    actor_generation_hash = str(actor_generation.get("generation", ""))
    if _HASH.fullmatch(actor_generation_hash) is None:
        raise ContextUpdateCoordinatorError("actor generation identity differs")
    if actor_generation.get("signer_public_key") != trust_projection["public_keys"][
        "actor-contract"
    ]:
        raise ContextUpdateCoordinatorError("actor generation signer projection differs")
    _write_artifact(evidence_dir / "actor-generation-receipt.json", actor_generation)

    environment_binding = {
        "catalog_hash": catalog_hash,
        "receipt_hash": signed_preflights[
            sorted(signed_preflights)[0]
        ]["receipt_hash"],
    }
    admission = _compile_owner_directed_admission(
        request_id=f"ctx-{candidate['commit'][:12]}",
        candidate=candidate,
        plan=plan,
        environment=environment_binding,
        authority_evidence=authority_evidence,
        signing_private_key=_protected_key(
            paths.admission_signer, trusted_uid=trusted_uid
        ),
        issued_at=issue_time,
        expires_at=expiry_time,
    )
    _write_artifact(evidence_dir / "release-admission.json", admission)
    bootstrap = catalog["bootstrap_revision"]["content_sha256"]
    broker = catalog["broker_policy_revision"]["content_sha256"]
    actors = sorted(signed_preflights)
    revisions = {
        "plan": plan["approved_commit"],
        "solution": plan["approved_solution"],
        "evidence_plan": plan_evidence["evidence_plan"],
        "evidence_tree": plan_evidence["evidence_tree"],
        "source": candidate["commit"],
        "source_tree": candidate["tree"],
        "current_plan_sources": plan_evidence["current_plan_sources"],
        "catalog": catalog_hash,
        "bootstrap": bootstrap,
        "broker_policy": broker,
        "review": authority_evidence["review_disposition"][
            "disposition_sha256"
        ],
        "admission": admission["receipt_hash"],
    }
    actor_request = {
        "schema": "tgw-w18-fleet-refresh-request/v1",
        "transaction_id": value["transaction_id"],
        "idempotency_key": _hash(
            {
                "transaction_id": value["transaction_id"],
                "candidate": candidate,
                "actor_generation": actor_generation_hash,
            }
        ),
        "predecessor_generation": value["expected_current"]["actor_generation"],
        "successor_generation": actor_generation_hash,
        "revisions": revisions,
        "actors": actors,
    }
    bundle = _actor_bundle(paths, actor_generation_hash)
    targets = _bounded_actor_targets(
        paths=paths,
        bundle=bundle,
        admission_receipt_hash=admission["receipt_hash"],
        release_generation=release_generation,
        transaction_id=value["transaction_id"],
        tmpfiles_source=(
            paths.release_root / "releases" / release_generation
            / "config/environment/tmpfiles.d/tgw-actor-host.conf"
        ),
    )
    return {
        "request": value,
        "request_sha256": _hash(value),
        "retained_source": str(retained),
        "archive": str(archive),
        "release_generation": release_generation,
        "release_manifest": release_manifest,
        "code_graph": code_graph,
        "catalog": catalog,
        "catalog_path": str(catalog_path),
        "preflights": signed_preflights,
        "actor_generation_receipt": actor_generation,
        "actor_request": actor_request,
        "admission": admission,
        "authority_evidence": authority_evidence,
        "plan_activation": value["plan_activation"],
        "plan_materialization": plan_materialization,
        "review_receipt": review_receipt,
        "controller_receipt": controller_receipt,
        "trust_projection": dict(trust_projection),
        "targets": targets,
    }


def _initialize_layout(
    paths: CoordinatorPaths, *, trusted_uid: int, trusted_gid: int
) -> None:
    def make_exact(path: Path, *, mode: int, uid: int, gid: int) -> None:
        _durable(path, "coordinator protected root")
        path.mkdir(parents=True, exist_ok=True, mode=mode)
        metadata = path.stat(follow_symlinks=False)
        if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise ContextUpdateCoordinatorError("coordinator protected root differs")
        os.chmod(path, mode)
        if os.geteuid() == 0:
            os.chown(path, uid, gid)
        _fsync_directory(path)
        _fsync_directory(path.parent)

    context_root = paths.private_root.parent
    protected_parent = context_root.parent
    make_exact(protected_parent, mode=0o755, uid=trusted_uid, gid=trusted_gid)
    # Actor launchers must traverse the immutable retained source.  Only the
    # transactions sibling is root-private; the shared protected parent is
    # read/traverse-only to actors.
    make_exact(context_root, mode=0o755, uid=trusted_uid, gid=trusted_gid)
    make_exact(paths.private_root, mode=0o700, uid=trusted_uid, gid=trusted_gid)
    make_exact(
        paths.host_receipt_root, mode=0o700, uid=trusted_uid, gid=trusted_gid
    )
    make_exact(paths.scratch_root, mode=0o700, uid=trusted_uid, gid=trusted_gid)
    try:
        fleet_gid = grp.getgrnam("tgw-coders").gr_gid if trusted_uid == 0 else trusted_gid
    except KeyError as exc:
        raise ContextUpdateCoordinatorError("actor fleet reader group is unavailable") from exc
    make_exact(paths.fleet_root, mode=0o750, uid=trusted_uid, gid=fleet_gid)
    make_exact(paths.fleet_private_root, mode=0o700, uid=trusted_uid, gid=trusted_gid)
    make_exact(paths.ledger_root, mode=0o750, uid=trusted_uid, gid=fleet_gid)
    _exact_root_directory(
        paths.private_root,
        mode=0o700,
        trusted_uid=trusted_uid,
        trusted_gid=trusted_gid,
    )


def _hashed_record(value: Mapping[str, Any], field: str, label: str) -> str:
    unsigned = dict(value)
    claimed = unsigned.pop(field, None)
    if claimed != _hash(unsigned):
        raise ContextUpdateCoordinatorError(f"{label} hash differs")
    return str(claimed)


def _progress(
    *,
    transaction_id: str,
    journal_sha256: str,
    binding: Mapping[str, Any],
) -> dict[str, Any]:
    body = {
        "schema": "tgw-context-update-progress/v1",
        "transaction_id": transaction_id,
        "status": "PREPARED",
        "coordinator_journal_sha256": journal_sha256,
        "coordinator_binding_sha256": binding["binding_sha256"],
        "inflight_sequence": None,
        "completed_effects": [],
        "postimages": {},
        "hold": None,
    }
    return {**body, "progress_sha256": _hash(body)}


def _write_progress(path: Path, value: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(value)
    body.pop("progress_sha256", None)
    normalized = {**body, "progress_sha256": _hash(body)}
    _atomic_json(
        path,
        normalized,
        mode=0o600,
        uid=0 if os.geteuid() == 0 else None,
        gid=0 if os.geteuid() == 0 else None,
    )
    return normalized


def _load_progress(path: Path) -> dict[str, Any]:
    value = _read_json(path, "coordinator progress")
    _hashed_record(value, "progress_sha256", "coordinator progress")
    return value


def _read_transaction_state(
    transaction_id: str,
    *,
    paths: CoordinatorPaths,
    trusted_uid: int,
    trusted_gid: int,
) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Read and verify coordinator state without creating or changing a path."""
    if _TRANSACTION.fullmatch(transaction_id) is None:
        raise ContextUpdateCoordinatorError("transaction identity is invalid")
    _exact_root_directory(
        paths.private_root,
        mode=0o700,
        trusted_uid=trusted_uid,
        trusted_gid=trusted_gid,
    )
    root = paths.private_root / transaction_id
    _exact_root_directory(
        root,
        mode=0o700,
        trusted_uid=trusted_uid,
        trusted_gid=trusted_gid,
    )
    prepared_path = root / "prepared-evidence.json"
    journal_path = root / "private-journal.json"
    binding_path = root / "coordinator-binding.json"
    progress_path = root / "progress.json"
    for path in (prepared_path, journal_path, binding_path, progress_path):
        _private_file_is_exact(
            path,
            trusted_uid=trusted_uid,
            trusted_gid=trusted_gid,
        )
    prepared = _read_json(prepared_path, "prepared coordinator evidence")
    journal = _read_json(journal_path, "private coordinator journal")
    binding = _read_json(binding_path, "coordinator binding")
    progress = _load_progress(progress_path)
    request = prepared.get("request")
    candidate = journal.get("candidate")
    effect_plan = journal.get("effect_plan")
    if (
        prepared.get("schema") != "tgw-context-update-prepared-evidence/v1"
        or journal.get("schema") != "tgw-context-update-private-journal/v1"
        or binding.get("schema")
        != "tgw-context-update-coordinator-binding/v1"
        or progress.get("schema") != "tgw-context-update-progress/v1"
        or prepared.get("transaction_id") != transaction_id
        or journal.get("transaction_id") != transaction_id
        or binding.get("outer_transaction_id") != transaction_id
        or progress.get("transaction_id") != transaction_id
        or not isinstance(request, Mapping)
        or not isinstance(candidate, Mapping)
        or not isinstance(effect_plan, Mapping)
        or journal.get("request_sha256") != prepared.get("request_sha256")
        or journal.get("request_sha256") != _hash(request)
        or candidate.get("prepared_evidence_sha256") != _hash(prepared)
        or binding.get("actor_request_sha256")
        != _hash(prepared.get("actor_request"))
        or binding.get("coordinator_journal_sha256") != _hash(journal)
        or binding.get("effect_plan_sha256")
        != effect_plan.get("effect_plan_sha256")
        or progress.get("coordinator_journal_sha256") != _hash(journal)
        or progress.get("coordinator_binding_sha256")
        != binding.get("binding_sha256")
    ):
        raise ContextUpdateCoordinatorError(
            "coordinator transaction binding differs"
        )
    _hashed_record(binding, "binding_sha256", "coordinator binding")
    return root, prepared, journal, binding, progress


def _transaction_status(
    transaction_id: str,
    prepared: Mapping[str, Any],
    journal: Mapping[str, Any],
    binding: Mapping[str, Any],
    progress: Mapping[str, Any],
) -> dict[str, Any]:
    completed_effects = progress.get("completed_effects")
    effects = journal.get("effect_plan", {}).get("effects")
    request = prepared.get("request")
    actor_request = prepared.get("actor_request")
    if (
        not isinstance(completed_effects, list)
        or not isinstance(effects, list)
        or not isinstance(request, Mapping)
        or not isinstance(request.get("candidate"), Mapping)
        or not isinstance(request.get("plan"), Mapping)
        or not isinstance(actor_request, Mapping)
    ):
        raise ContextUpdateCoordinatorError(
            "coordinator transaction status is incomplete"
        )
    return {
        "schema": "tgw-context-update-status/v1",
        "transaction_id": transaction_id,
        "status": progress["status"],
        "candidate_commit": request["candidate"]["commit"],
        "candidate_tree": request["candidate"]["tree"],
        "approved_plan": request["plan"]["approved_commit"],
        "evidence_plan": request["plan"]["evidence_commit"],
        "actor_generation": actor_request["successor_generation"],
        "completed_effects": len(completed_effects),
        "total_effects": len(effects),
        "binding_sha256": binding["binding_sha256"],
        "hold": progress.get("hold"),
    }


def read_transaction_status(
    transaction_id: str,
    *,
    paths: CoordinatorPaths = CoordinatorPaths(),
    trusted_uid: int = 0,
    trusted_gid: int = 0,
) -> dict[str, Any]:
    """Return verified transaction progress through a strictly read-only path."""
    _root, prepared, journal, binding, progress = _read_transaction_state(
        transaction_id,
        paths=paths,
        trusted_uid=trusted_uid,
        trusted_gid=trusted_gid,
    )
    return _transaction_status(
        transaction_id, prepared, journal, binding, progress
    )


def _prepared_candidate(artifacts: Mapping[str, Any], prepared_sha256: str) -> dict[str, Any]:
    manifest = artifacts["release_manifest"]
    actor_generation = artifacts["actor_generation_receipt"]
    admission = artifacts["admission"]
    request = artifacts["request"]
    return {
        "commit": request["candidate"]["commit"],
        "tree": request["candidate"]["tree"],
        "release_generation": artifacts["release_generation"],
        "release_manifest_sha256": _hash(manifest),
        "actor_generation": actor_generation["generation"],
        "catalog_sha256": _hash(artifacts["catalog"]),
        "admission_receipt_sha256": admission["receipt_hash"],
        "authority_mode": request["authority"]["mode"],
        "authority_evidence_sha256": artifacts["authority_evidence"][
            "authority_evidence_sha256"
        ],
        "review_receipt_sha256": (
            artifacts["review_receipt"]["receipt_hash"]
            if isinstance(artifacts.get("review_receipt"), Mapping) else None
        ),
        "prepared_evidence_sha256": prepared_sha256,
    }


def _prepared_payload(artifacts: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": "tgw-context-update-prepared-evidence/v1",
        "transaction_id": artifacts["request"]["transaction_id"],
        "request": artifacts["request"],
        "request_sha256": artifacts["request_sha256"],
        "retained_source": artifacts["retained_source"],
        "archive": artifacts["archive"],
        "release_generation": artifacts["release_generation"],
        "release_manifest": artifacts["release_manifest"],
        "code_graph_sha256": artifacts["code_graph"]["freshness_hash"],
        "catalog_path": artifacts["catalog_path"],
        "catalog_sha256": _hash(artifacts["catalog"]),
        "preflights": artifacts["preflights"],
        "actor_generation_receipt": artifacts["actor_generation_receipt"],
        "actor_request": artifacts["actor_request"],
        "admission": artifacts["admission"],
        "authority_evidence": artifacts["authority_evidence"],
        "plan_activation": artifacts["plan_activation"],
        "plan_materialization": artifacts["plan_materialization"],
        "review_receipt": artifacts["review_receipt"],
        "controller_receipt": artifacts["controller_receipt"],
        "trust_projection": artifacts["trust_projection"],
    }


_COLD_TOOL_SUFFIXES = {
    "Skill",
    "ToolSearch",
    "tgw_context_status",
    "tgw_context_bundle",
    "tgw_context_plan_graph",
    "tgw_context_plan_source",
}
_COLD_EXACT_TOOLS = {
    "Skill": "Skill",
    "ToolSearch": "ToolSearch",
    **{
        f"mcp__tgw-context__{name}": name
        for name in _COLD_TOOL_SUFFIXES if name.startswith("tgw_context_")
    },
}


def _cold_tool_suffix(name: str) -> str:
    return _COLD_EXACT_TOOLS.get(name, name)


def _walk_json(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _decode_tool_content(value: Any) -> Any:
    if isinstance(value, str):
        candidate: Any = value
        for _ in range(3):
            if not isinstance(candidate, str):
                break
            try:
                candidate = json.loads(candidate)
            except json.JSONDecodeError:
                break
        return candidate
    if isinstance(value, list):
        texts = [
            item.get("text") for item in value
            if isinstance(item, Mapping) and isinstance(item.get("text"), str)
        ]
        if len(texts) == 1:
            return _decode_tool_content(texts[0])
    if isinstance(value, Mapping) and isinstance(value.get("content"), (str, list)):
        return _decode_tool_content(value["content"])
    return value


def _mapping_with(value: Any, *keys: str) -> Mapping[str, Any] | None:
    for candidate in _walk_json(value):
        if all(key in candidate for key in keys):
            return candidate
    return None


def _validated_cold_instruction(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContextUpdateCoordinatorError(
            "cold continuity instruction binding is absent"
        )
    body = dict(value)
    claimed = body.pop("binding_sha256", None)
    if (
        set(body)
        != {
            "schema",
            "actor",
            "path",
            "sha256",
            "bootstrap_receipt_hash",
            "contract_receipt_hash",
        }
        or body.get("schema")
        != "tgw-context-cold-instruction-binding/v1"
        or body.get("actor") != "claude"
        or body.get("path") != str(_CLAUDE_INSTRUCTION_ENTRY_POINT)
        or any(
            _HASH.fullmatch(str(body.get(name, ""))) is None
            for name in (
                "sha256",
                "bootstrap_receipt_hash",
                "contract_receipt_hash",
            )
        )
        or claimed != _hash(body)
    ):
        raise ContextUpdateCoordinatorError(
            "cold continuity instruction binding differs"
        )
    return {**body, "binding_sha256": claimed}


def _instruction_destination_is_exact(
    destination: Path,
    source: Path,
    source_raw: bytes,
    effect: Mapping[str, Any],
) -> bool:
    """Verify the bytes consumed by a harness without fixing copy vs symlink."""
    desired_sha256 = "sha256:" + hashlib.sha256(source_raw).hexdigest()
    materialization = effect.get("materialization")
    if effect.get("desired_sha256") != desired_sha256:
        return False
    if materialization == "symlink":
        try:
            return (
                destination.is_symlink()
                and destination.resolve(strict=True) == source.resolve(strict=True)
                and _file_hash(destination) == desired_sha256
            )
        except OSError:
            return False
    if not isinstance(materialization, str) or not materialization:
        return False
    try:
        destination_state, destination_raw = _stable_regular_file(
            destination,
            _MAX_OWNER_DIRECTIVE,
            "installed concise instruction entry point",
        )
    except (ContextUpdateCoordinatorError, OSError):
        return False
    return (
        destination_raw == source_raw
        and destination_state.st_uid == effect.get("desired_uid")
        and destination_state.st_gid == effect.get("desired_gid")
        and stat.S_IMODE(destination_state.st_mode) == effect.get("desired_mode")
        and destination_state.st_nlink == 1
    )


def verify_cold_continuity_transcript(
    raw: bytes,
    actor_request: Mapping[str, Any],
    expected_instruction: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify actual ordinary-Claude tool events, never the model's prose claim."""
    instruction = _validated_cold_instruction(expected_instruction)
    if not raw or len(raw) > _MAX_COLD_TRANSCRIPT:
        raise ContextUpdateCoordinatorError("cold continuity transcript bound differs")
    events: list[Any] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ContextUpdateCoordinatorError("cold continuity event is invalid") from exc
        events.append(value)
    if not events or len(events) > 20_000:
        raise ContextUpdateCoordinatorError("cold continuity event count differs")
    uses: dict[str, tuple[str, Mapping[str, Any]]] = {}
    results: dict[str, tuple[Any, bool]] = {}
    for event in events:
        for block in _walk_json(event):
            if block.get("type") == "tool_use":
                tool_id = block.get("id")
                name = block.get("name")
                arguments = block.get("input")
                if (
                    not isinstance(tool_id, str)
                    or not isinstance(name, str)
                    or not isinstance(arguments, Mapping)
                    or tool_id in uses
                ):
                    raise ContextUpdateCoordinatorError("cold continuity tool event differs")
                if name not in _COLD_EXACT_TOOLS:
                    raise ContextUpdateCoordinatorError(
                        f"cold continuity used a non-read tool: {name}"
                    )
                suffix = _cold_tool_suffix(name)
                uses[tool_id] = (suffix, dict(arguments))
            elif block.get("type") == "tool_result":
                tool_id = block.get("tool_use_id")
                if isinstance(tool_id, str) and tool_id not in results:
                    results[tool_id] = (
                        _decode_tool_content(block.get("content")),
                        block.get("is_error") is True,
                    )
    completed = [
        (tool_id, name, arguments, results[tool_id][0])
        for tool_id, (name, arguments) in uses.items() if tool_id in results
    ]
    if len(completed) != len(uses) or any(error for _content, error in results.values()):
        raise ContextUpdateCoordinatorError("cold continuity tool transcript is incomplete")
    loaded_skills = {
        name: [
            row
            for row in completed
            if row[1] == "Skill" and row[2].get("skill") == name
        ]
        for name in ("tgw-plan", "tgw-review")
    }
    if any(
        len(rows) != 1 or rows[0][3] in {None, ""}
        for rows in loaded_skills.values()
    ):
        raise ContextUpdateCoordinatorError(
            "cold continuity did not load installed tgw-plan and tgw-review"
        )
    revisions = actor_request.get("revisions")
    if not isinstance(revisions, Mapping):
        raise ContextUpdateCoordinatorError("cold continuity revision binding differs")

    status_rows = [row for row in completed if row[1] == "tgw_context_status"]
    if len(status_rows) != 1 or status_rows[0][2]:
        raise ContextUpdateCoordinatorError("cold continuity status probe differs")
    status_position = completed.index(status_rows[0])
    if any(
        completed.index(rows[0]) > status_position
        for rows in loaded_skills.values()
    ):
        raise ContextUpdateCoordinatorError(
            "cold continuity loaded a required skill after status"
        )
    status = _mapping_with(status_rows[0][3], "plan", "source", "environment")
    if not isinstance(status, Mapping):
        raise ContextUpdateCoordinatorError("cold continuity status result differs")
    fleet = status.get("fleet_convergence")
    transaction = fleet.get("transaction") if isinstance(fleet, Mapping) else None
    generation_status = status.get("generation_status")
    mcp_order = [
        name for _tool_id, name, _arguments, _result in completed
        if name.startswith("tgw_context_")
    ]
    if (
        not mcp_order
        or mcp_order[0] != "tgw_context_status"
        or not isinstance(generation_status, Mapping)
        or not isinstance(generation_status.get("line"), str)
        or not generation_status["line"].strip()
        or status.get("plan", {}).get("approved_commit") != revisions.get("plan")
        or status.get("plan", {}).get("approved_solution_hash")
        != revisions.get("solution")
        or status.get("plan", {}).get("evidence_head")
        != revisions.get("evidence_plan")
        or status.get("plan", {}).get("evidence_tree")
        != revisions.get("evidence_tree")
        or status.get("source", {}).get("commit") != revisions.get("source")
        or status.get("source", {}).get("tree") != revisions.get("source_tree")
        or status.get("environment", {}).get("catalog_hash")
        != revisions.get("catalog")
        or status.get("startup", {}).get("actor") != "claude"
        or status.get("startup", {}).get("generation")
        != actor_request.get("successor_generation")
        or not isinstance(transaction, Mapping)
        or transaction.get("transaction_id") != actor_request.get("transaction_id")
    ):
        raise ContextUpdateCoordinatorError("cold continuity status is stale or mixed")

    bundle_rows = [row for row in completed if row[1] == "tgw_context_bundle"]
    if len(bundle_rows) != 1 or bundle_rows[0][2].get("receiver") != "claude":
        raise ContextUpdateCoordinatorError("cold continuity repair bundle differs")
    bundle = _mapping_with(bundle_rows[0][3], "receiver", "status")
    if (
        not isinstance(bundle, Mapping)
        or bundle.get("receiver") != "claude"
        or bundle.get("status", {}).get("source", {}).get("commit")
        != revisions.get("source")
    ):
        raise ContextUpdateCoordinatorError("cold continuity repair bundle is stale")

    graph_rows = [row for row in completed if row[1] == "tgw_context_plan_graph"]
    if (
        len(graph_rows) != 1
        or graph_rows[0][2].get("receiver") != "claude"
        or graph_rows[0][2].get("operation") != "resolve"
    ):
        raise ContextUpdateCoordinatorError("cold continuity Plan resolution differs")
    graph = _mapping_with(graph_rows[0][3], "plan_commit")
    if not isinstance(graph, Mapping) or graph.get("plan_commit") != revisions.get("plan"):
        raise ContextUpdateCoordinatorError("cold continuity Plan result is stale")

    sources = revisions.get("current_plan_sources")
    if not isinstance(sources, Mapping) or set(sources) != set(_CURRENT_PLAN_SOURCES):
        raise ContextUpdateCoordinatorError("cold continuity Plan sources differ")
    coverage: dict[str, list[tuple[int, int]]] = {path: [] for path in sources}
    totals: dict[str, int] = {}
    byte_counts: dict[str, int] = {}
    chunk_content: dict[str, list[tuple[int, int, str]]] = {
        path: [] for path in sources
    }
    source_receipts: list[dict[str, Any]] = []
    for _tool_id, name, arguments, result in completed:
        if name != "tgw_context_plan_source":
            continue
        path = arguments.get("path")
        if path not in sources or arguments.get("authority") != "current-plan":
            raise ContextUpdateCoordinatorError("cold continuity Plan source call escaped")
        chunk = _mapping_with(result, "confined_path", "content", "blob_sha256")
        if not isinstance(chunk, Mapping):
            raise ContextUpdateCoordinatorError("cold continuity Plan source result differs")
        content = chunk.get("content")
        start, end, total = (
            chunk.get("start_line"), chunk.get("end_line"), chunk.get("total_lines")
        )
        byte_count = chunk.get("bytes")
        if (
            chunk.get("authority") != "current-plan"
            or chunk.get("confined_path") != path
            or chunk.get("commit") != revisions.get("evidence_plan")
            or chunk.get("tree") != revisions.get("evidence_tree")
            or chunk.get("blob_sha256") != sources[path]
            or not isinstance(content, str)
            or chunk.get("content_sha256")
            != "sha256:" + hashlib.sha256(content.encode()).hexdigest()
            or not all(
                isinstance(item, int) for item in (start, end, total, byte_count)
            )
            or start < 1 or end < start - 1 or total < end
        ):
            raise ContextUpdateCoordinatorError("cold continuity Plan source is stale")
        coverage[str(path)].append((start, end))
        totals[str(path)] = total
        if path in byte_counts and byte_counts[str(path)] != byte_count:
            raise ContextUpdateCoordinatorError("cold continuity Plan byte count differs")
        byte_counts[str(path)] = byte_count
        chunk_content[str(path)].append((start, end, content))
        source_receipts.append(
            {
                "path": path, "start_line": start, "end_line": end,
                "content_sha256": chunk["content_sha256"],
            }
        )
    for path in sorted(sources):
        intervals = sorted(coverage[path])
        cursor = 1
        for start, end in intervals:
            if start != cursor:
                raise ContextUpdateCoordinatorError("cold continuity Plan coverage has a gap")
            cursor = end + 1
        if path not in totals or cursor != totals[path] + 1:
            raise ContextUpdateCoordinatorError("cold continuity Plan coverage is incomplete")
        reconstructed = "\n".join(
            content for _start, _end, content in sorted(chunk_content[path])
        ).encode()
        candidates = [reconstructed, reconstructed + b"\n"]
        exact = [
            candidate for candidate in candidates
            if len(candidate) == byte_counts[path]
            and "sha256:" + hashlib.sha256(candidate).hexdigest() == sources[path]
        ]
        if len(exact) != 1:
            raise ContextUpdateCoordinatorError(
                "cold continuity reconstructed Plan source differs"
            )
    proof = {
        "schema": "tgw-context-cold-continuity-proof/v1",
        "status": "PASS",
        "actor": "claude",
        "transaction_id": actor_request["transaction_id"],
        "actor_generation": actor_request["successor_generation"],
        "tool_event_count": len(uses),
        "tool_names": sorted({name for _id, name, _args, _result in completed}),
        "status_sha256": _hash(status),
        "bundle_sha256": _hash(bundle),
        "plan_graph_sha256": _hash(graph),
        "plan_source_receipts_sha256": _hash(source_receipts),
        "instruction_entry_point": instruction,
        "transcript_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
    }
    return {**proof, "proof_sha256": _hash(proof)}


class RootContextUpdateCoordinator:
    """Prepare, resume, and inversely roll back one bounded root transaction."""

    def __init__(
        self,
        *,
        paths: CoordinatorPaths = CoordinatorPaths(),
        endpoint: str = "http://100.68.223.70:7556",
        runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] | None = None,
        provider: ActorFleetRootClient | None = None,
        trusted_uid: int = 0,
        trusted_gid: int = 0,
        require_root: bool = True,
        now: Callable[[], datetime] = _utc_now,
        event_hook: Callable[[str], None] | None = None,
    ) -> None:
        if require_root and os.geteuid() != 0:
            raise ContextUpdateCoordinatorError("Context update coordinator requires root")
        self.paths = paths
        self.endpoint = endpoint
        self._runner = runner
        self._provider_client = provider
        self.trusted_uid = trusted_uid
        self.trusted_gid = trusted_gid
        self.now = now
        self.event_hook = event_hook
        _initialize_layout(
            paths, trusted_uid=trusted_uid, trusted_gid=trusted_gid
        )

    def _run_for(self, transaction_id: str) -> Callable[[Sequence[str]], subprocess.CompletedProcess[str]]:
        return self._runner or transaction_runner(
            self.paths,
            transaction_id,
            trusted_uid=self.trusted_uid,
            trusted_gid=self.trusted_gid,
        )

    def _provider(self) -> ActorFleetRootClient:
        if self._provider_client is None:
            self._provider_client = ActorFleetRootClient(
                endpoint=self.endpoint,
                token=_provider_token(
                    self.paths.provider_environment, trusted_uid=self.trusted_uid
                ),
            )
        return self._provider_client

    def _transaction_root(self, transaction_id: str) -> Path:
        if _TRANSACTION.fullmatch(transaction_id) is None:
            raise ContextUpdateCoordinatorError("transaction identity is invalid")
        root = self.paths.private_root / transaction_id
        _ensure_private_directory(
            root, trusted_uid=self.trusted_uid, trusted_gid=self.trusted_gid
        )
        return root

    def prepare(self, request: Mapping[str, Any]) -> dict[str, Any]:
        requested = validate_update_request(request)
        transaction_id = requested["transaction_id"]
        transaction_root = self._transaction_root(transaction_id)
        prepared_path = transaction_root / "prepared-evidence.json"
        runner = self._run_for(transaction_id)
        directive = (
            _owner_directive(
                transaction_root,
                requested["authority"],
                trusted_uid=self.trusted_uid,
                trusted_gid=self.trusted_gid,
                now=self.now,
            )
            if requested["authority"]["mode"] == "OWNER_DIRECT" else None
        )
        value = _derived_update_request(
            requested,
            paths=self.paths,
            runner=runner,
            owner_directive=directive,
        )
        trust_projection = prepare_trust_projection(
            paths=self.paths,
            transaction_root=transaction_root,
            plan_activation=value["plan_activation"],
            trusted_uid=self.trusted_uid,
            trusted_gid=self.trusted_gid,
        )
        if prepared_path.exists() or prepared_path.is_symlink():
            prepared = _read_json(prepared_path, "prepared coordinator evidence")
            _private_file_is_exact(
                prepared_path,
                trusted_uid=self.trusted_uid,
                trusted_gid=self.trusted_gid,
            )
            if (
                prepared.get("schema") != "tgw-context-update-prepared-evidence/v1"
                or prepared.get("transaction_id") != transaction_id
                or prepared.get("request") != value
                or prepared.get("request_sha256") != _hash(value)
                or prepared.get("trust_projection") != trust_projection
            ):
                raise ContextUpdateCoordinatorError("prepared evidence retry differs")
            observed_materialization = _inspect_plan_materialization(
                self.paths,
                materialization=Path(
                    str(prepared["plan_activation"]["successor"]["materialization"])
                ),
                expected_commit=str(
                    prepared["plan_activation"]["successor"]["commit"]
                ),
                expected_tree=str(
                    prepared["plan_activation"]["successor"]["tree"]
                ),
                runner=runner,
            )
            if prepared.get("plan_materialization") != {
                **observed_materialization,
                "pre_effect_disposition": "RETAIN_IMMUTABLE_UNSELECTED",
            }:
                raise ContextUpdateCoordinatorError(
                    "prepared Plan materialization retry differs"
                )
            actor_request = prepared["actor_request"]
            admission = prepared["admission"]
            bundle = _actor_bundle(
                self.paths, prepared["actor_generation_receipt"]["generation"]
            )
            targets = _bounded_actor_targets(
                paths=self.paths,
                bundle=bundle,
                admission_receipt_hash=admission["receipt_hash"],
                release_generation=prepared["release_generation"],
                transaction_id=transaction_id,
                tmpfiles_source=(
                    self.paths.release_root / "releases" / prepared["release_generation"]
                    / "config/environment/tmpfiles.d/tgw-actor-host.conf"
                ),
            )
        else:
            artifacts = prepare_artifacts(
                request=value,
                paths=self.paths,
                trust_projection=trust_projection,
                runner=runner,
                trusted_uid=self.trusted_uid,
                now=self.now,
            )
            prepared = _prepared_payload(artifacts)
            _atomic_json(
                prepared_path,
                prepared,
                mode=0o600,
                uid=self.trusted_uid,
                gid=self.trusted_gid,
            )
            _private_file_is_exact(
                prepared_path,
                trusted_uid=self.trusted_uid,
                trusted_gid=self.trusted_gid,
            )
            actor_request = artifacts["actor_request"]
            admission = artifacts["admission"]
            targets = artifacts["targets"]
        prepared_sha256 = _hash(prepared)
        candidate = {
            "commit": value["candidate"]["commit"],
            "tree": value["candidate"]["tree"],
            "release_generation": prepared["release_generation"],
            "release_manifest_sha256": _hash(prepared["release_manifest"]),
            "actor_generation": prepared["actor_generation_receipt"]["generation"],
            "catalog_sha256": prepared["catalog_sha256"],
            "admission_receipt_sha256": admission["receipt_hash"],
            "authority_mode": value["authority"]["mode"],
            "authority_evidence_sha256": prepared["authority_evidence"][
                "authority_evidence_sha256"
            ],
            "review_receipt_sha256": (
                prepared["review_receipt"]["receipt_hash"]
                if isinstance(prepared.get("review_receipt"), Mapping) else None
            ),
            "prepared_evidence_sha256": prepared_sha256,
        }
        journal_path = transaction_root / "private-journal.json"
        if journal_path.exists() or journal_path.is_symlink():
            journal = _read_json(journal_path, "private coordinator journal")
            _private_file_is_exact(
                journal_path,
                trusted_uid=self.trusted_uid,
                trusted_gid=self.trusted_gid,
            )
            journal_sha256 = _hash(journal)
            if (
                journal.get("schema") != "tgw-context-update-private-journal/v1"
                or journal.get("transaction_id") != transaction_id
                or journal.get("request_sha256") != _hash(value)
                or journal.get("candidate") != candidate
                or journal.get("plan_activation") != prepared["plan_activation"]
            ):
                raise ContextUpdateCoordinatorError("existing private journal differs")
        else:
            journal, journal_sha256 = write_private_journal(
                paths=self.paths,
                transaction_id=transaction_id,
                request_sha256=_hash(value),
                candidate=candidate,
                trust_projection=trust_projection,
                plan_activation=prepared["plan_activation"],
                targets=targets,
                runner=runner,
                trusted_uid=self.trusted_uid,
                trusted_gid=self.trusted_gid,
                now=self.now,
                event_hook=self.event_hook,
            )
        opening = append_ledger_opening(
            paths=self.paths,
            journal=journal,
            journal_sha256=journal_sha256,
            request=value,
            actor_request=actor_request,
            admission_receipt=admission,
            trust_projection=trust_projection,
            trusted_uid=self.trusted_uid,
            trusted_gid=self.trusted_gid,
            now=self.now,
            event_hook=self.event_hook,
        )
        binding = coordinator_binding(
            actor_request=actor_request,
            journal_sha256=journal_sha256,
            ledger_opening=opening,
            effect_plan_sha256=journal["effect_plan"]["effect_plan_sha256"],
        )
        binding_path = transaction_root / "coordinator-binding.json"
        if binding_path.exists() or binding_path.is_symlink():
            if _read_json(binding_path, "coordinator binding") != binding:
                raise ContextUpdateCoordinatorError("coordinator binding retry differs")
        else:
            _atomic_json(
                binding_path,
                binding,
                mode=0o600,
                uid=self.trusted_uid,
                gid=self.trusted_gid,
            )
        progress_path = transaction_root / "progress.json"
        if not progress_path.exists() and not progress_path.is_symlink():
            _write_progress(
                progress_path,
                _progress(
                    transaction_id=transaction_id,
                    journal_sha256=journal_sha256,
                    binding=binding,
                ),
            )
        else:
            progress = _load_progress(progress_path)
            if (
                progress.get("transaction_id") != transaction_id
                or progress.get("coordinator_journal_sha256") != journal_sha256
                or progress.get("coordinator_binding_sha256")
                != binding["binding_sha256"]
            ):
                raise ContextUpdateCoordinatorError("coordinator progress differs")
        return {
            "schema": "tgw-context-update-preparation-receipt/v1",
            "status": "PREPARED",
            "transaction_id": transaction_id,
            "candidate_commit": value["candidate"]["commit"],
            "candidate_tree": value["candidate"]["tree"],
            "release_generation": prepared["release_generation"],
            "actor_generation": actor_request["successor_generation"],
            "coordinator_journal_sha256": journal_sha256,
            "coordinator_ledger_opening_sha256": opening["record_sha256"],
            "binding_sha256": binding["binding_sha256"],
        }

    def _load_transaction(
        self, transaction_id: str
    ) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        root, prepared, journal, binding, progress = _read_transaction_state(
            transaction_id,
            paths=self.paths,
            trusted_uid=self.trusted_uid,
            trusted_gid=self.trusted_gid,
        )
        bundle = _actor_bundle(
            self.paths, prepared["actor_generation_receipt"]["generation"]
        )
        expected_targets = _bounded_actor_targets(
            paths=self.paths,
            bundle=bundle,
            admission_receipt_hash=prepared["admission"]["receipt_hash"],
            release_generation=prepared["release_generation"],
            transaction_id=transaction_id,
            tmpfiles_source=(
                self.paths.release_root / "releases" / prepared["release_generation"]
                / "config/environment/tmpfiles.d/tgw-actor-host.conf"
            ),
        )
        expected_paths = {
            target.target_id: str(target.path) for target in expected_targets
        }
        observed_paths = {
            str(item.get("target_id")): str(item.get("path"))
            for item in journal.get("preimages", []) if isinstance(item, Mapping)
        }
        if expected_paths != observed_paths:
            raise ContextUpdateCoordinatorError("private journal target allowlist differs")
        activation = _validated_plan_activation(prepared.get("plan_activation"))
        if journal.get("plan_activation") != activation:
            raise ContextUpdateCoordinatorError("private journal Plan binding differs")
        observed_materialization = _inspect_plan_materialization(
            self.paths,
            materialization=Path(str(activation["successor"]["materialization"])),
            expected_commit=str(activation["successor"]["commit"]),
            expected_tree=str(activation["successor"]["tree"]),
            runner=self._run_for(transaction_id),
        )
        if prepared.get("plan_materialization") != {
            **observed_materialization,
            "pre_effect_disposition": "RETAIN_IMMUTABLE_UNSELECTED",
        }:
            raise ContextUpdateCoordinatorError("prepared Plan materialization differs")
        expected_effect_plan = _effect_plan(
            transaction_id,
            journal["preimages"],
            journal["service_preimages"],
        )
        if journal.get("effect_plan") != expected_effect_plan:
            raise ContextUpdateCoordinatorError("private journal effect plan differs")
        return root, prepared, journal, binding, progress

    def _required_command(
        self,
        transaction_id: str,
        command: Sequence[str],
        label: str,
        *,
        accepted: set[int] = {0},
    ) -> subprocess.CompletedProcess[str]:
        result = self._run_for(transaction_id)(command)
        if result.returncode not in accepted:
            raise ContextUpdateCoordinatorError(f"{label} failed")
        return result

    def _checkpoint(self, label: str) -> None:
        if self.event_hook is not None:
            self.event_hook(label)

    def _selected_release(self, prepared: Mapping[str, Any]) -> Path:
        generation = str(prepared["release_generation"])
        selected = current_generation(self.paths.release_root)
        if selected != generation:
            raise ContextUpdateCoordinatorError("selected release differs from transaction")
        release = self.paths.release_root / "releases" / generation
        if release.is_symlink() or not release.is_dir():
            raise ContextUpdateCoordinatorError("selected release is unsafe")
        manifest = _read_json(release / ".release-manifest.json", "selected release manifest")
        if manifest != prepared["release_manifest"]:
            raise ContextUpdateCoordinatorError("selected release manifest differs")
        return release

    def _install_candidate_file(
        self,
        *,
        target: Path,
        body: bytes,
        mode: int,
        expected_preimage: Mapping[str, Any],
    ) -> dict[str, Any]:
        if target.is_symlink():
            current_equal = False
        elif target.is_file():
            current_equal = target.read_bytes() == body
        else:
            current_equal = False
        metadata_exact = False
        if current_equal:
            metadata = target.stat(follow_symlinks=False)
            metadata_exact = (
                stat.S_IMODE(metadata.st_mode) == mode
                and metadata.st_uid == self.trusted_uid
                and metadata.st_gid == self.trusted_gid
                and metadata.st_nlink == 1
            )
        if not metadata_exact:
            observed = _snapshot_node(target, recursive=True, counter=[0])
            expected = {
                key: value for key, value in expected_preimage.items()
                if key not in {"target_id", "path"}
            }
            if observed != expected and not current_equal:
                raise ContextUpdateCoordinatorError("live file CAS differs before install")
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_bytes(
                target,
                body,
                mode=mode,
                uid=self.trusted_uid,
                gid=self.trusted_gid,
            )
        metadata = target.stat(follow_symlinks=False)
        if (
            target.is_symlink()
            or not target.is_file()
            or target.read_bytes() != body
            or stat.S_IMODE(metadata.st_mode) != mode
            or metadata.st_uid != self.trusted_uid
            or metadata.st_gid != self.trusted_gid
            or metadata.st_nlink != 1
        ):
            raise ContextUpdateCoordinatorError("installed live file differs")
        return {
            "path": str(target),
            "sha256": "sha256:" + hashlib.sha256(body).hexdigest(),
            "mode": mode,
        }

    def _activate_plan_binding(
        self,
        transaction_id: str,
        prepared: Mapping[str, Any],
        journal: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Converge the approved ref/config as one resumable journaled effect."""
        activation = _validated_plan_activation(prepared.get("plan_activation"))
        if journal.get("plan_activation") != activation:
            raise ContextUpdateCoordinatorError("journaled Plan activation differs")
        successor = activation["successor"]
        _inspect_plan_materialization(
            self.paths,
            materialization=Path(str(successor["materialization"])),
            expected_commit=str(successor["commit"]),
            expected_tree=str(successor["tree"]),
            runner=self._run_for(transaction_id),
        )
        projection = prepared["trust_projection"]
        if projection.get("plan_activation_sha256") != activation[
            "activation_sha256"
        ]:
            raise ContextUpdateCoordinatorError("candidate Plan projection differs")
        config_path = Path(str(projection["provider_config_path"]))
        config = _read_json(config_path, "candidate provider config")
        if (
            _hash(config) != projection.get("provider_config_sha256")
            or config.get("plan_approved_commit") != successor["commit"]
            or config.get("plan_approved_solution_hash")
            != successor["solution_hash"]
            or config.get("plan_repository_root") != str(self.paths.plan_repository)
            or config.get("standalone_plan_root") != successor["materialization"]
        ):
            raise ContextUpdateCoordinatorError("candidate Plan config differs")
        observed_ref = _plan_ref_commit(
            self.paths, self._run_for(transaction_id)
        )
        if observed_ref != successor["commit"]:
            if observed_ref != activation["observed_named_ref"]:
                raise ContextUpdateCoordinatorError(
                    "approved Plan ref CAS differs before activation"
                )
            self._required_command(
                transaction_id,
                _protected_git_command(
                    self.paths.git,
                    self.paths.plan_repository,
                    "update-ref",
                    activation["approved_ref"],
                    str(successor["commit"]),
                    observed_ref,
                ),
                "approved Plan ref activation",
            )
            self._checkpoint("MUTATION:INSTALL_PLATFORM_TRUST:approved-plan-ref")
        config_receipt = self._install_candidate_file(
            target=self.paths.provider_config,
            body=_canonical(config) + b"\n",
            mode=0o644,
            expected_preimage=self._preimage(journal, "provider-config"),
        )
        self._checkpoint("MUTATION:INSTALL_PLATFORM_TRUST:provider-config")
        if _plan_ref_commit(
            self.paths, self._run_for(transaction_id)
        ) != successor["commit"]:
            raise ContextUpdateCoordinatorError("approved Plan ref activation differs")
        installed = _read_json(self.paths.provider_config, "installed provider config")
        if installed != config:
            raise ContextUpdateCoordinatorError("installed Plan config differs")
        body = {
            "schema": "tgw-context-plan-activation-receipt/v1",
            "transaction_id": transaction_id,
            "activation_sha256": activation["activation_sha256"],
            "observed_named_ref": activation["observed_named_ref"],
            "approved_ref": activation["approved_ref"],
            "approved_commit": successor["commit"],
            "approved_solution": successor["solution_hash"],
            "approved_materialization": successor["materialization"],
            "provider_config_sha256": config_receipt["sha256"],
            "rollback_commit": activation["predecessor"]["commit"],
            "historical_ref_disposition": activation[
                "observed_ref_disposition"
            ]["disposition"],
        }
        return {**body, "receipt_sha256": _hash(body)}

    def _restore_approved_plan_ref(
        self,
        transaction_id: str,
        activation: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Restore the logical predecessor, never a contradictory raw ref preimage."""
        value = _validated_plan_activation(activation)
        predecessor = str(value["predecessor"]["commit"])
        successor = str(value["successor"]["commit"])
        observed = _plan_ref_commit(self.paths, self._run_for(transaction_id))
        if observed != predecessor:
            if observed not in {successor, str(value["observed_named_ref"])}:
                raise ContextUpdateCoordinatorError(
                    "approved Plan ref rollback CAS differs"
                )
            self._required_command(
                transaction_id,
                _protected_git_command(
                    self.paths.git,
                    self.paths.plan_repository,
                    "update-ref",
                    value["approved_ref"],
                    predecessor,
                    observed,
                ),
                "approved Plan ref rollback",
            )
        if _plan_ref_commit(
            self.paths, self._run_for(transaction_id)
        ) != predecessor:
            raise ContextUpdateCoordinatorError("approved Plan ref rollback differs")
        return {
            "approved_ref": value["approved_ref"],
            "restored_commit": predecessor,
            "observed_preimage_not_restored": value["observed_named_ref"],
        }

    def _verify_active_plan_binding(
        self, transaction_id: str, prepared: Mapping[str, Any]
    ) -> dict[str, Any]:
        activation = _validated_plan_activation(prepared.get("plan_activation"))
        successor = activation["successor"]
        materialization = _inspect_plan_materialization(
            self.paths,
            materialization=Path(str(successor["materialization"])),
            expected_commit=str(successor["commit"]),
            expected_tree=str(successor["tree"]),
            runner=self._run_for(transaction_id),
        )
        candidate = _read_json(
            Path(str(prepared["trust_projection"]["provider_config_path"])),
            "candidate provider config",
        )
        installed = _read_json(
            self.paths.provider_config, "installed provider config"
        )
        if (
            _plan_ref_commit(self.paths, self._run_for(transaction_id))
            != successor["commit"]
            or installed != candidate
        ):
            raise ContextUpdateCoordinatorError("active Plan binding differs")
        return {
            "approved_ref": activation["approved_ref"],
            "approved_commit": successor["commit"],
            "approved_solution": successor["solution_hash"],
            "materialization": materialization,
            "provider_config_sha256": _hash(installed),
        }

    @staticmethod
    def _preimage(journal: Mapping[str, Any], target_id: str) -> dict[str, Any]:
        matches = [
            dict(item) for item in journal.get("preimages", [])
            if isinstance(item, Mapping) and item.get("target_id") == target_id
        ]
        if len(matches) != 1:
            raise ContextUpdateCoordinatorError("effect preimage is unavailable")
        return matches[0]

    def _provider_journal_status(self, transaction_id: str) -> str | None:
        path = self.paths.fleet_private_root / f"{transaction_id}.actor-provider.json"
        if not path.exists() and not path.is_symlink():
            return None
        value = _read_json(path, "actor provider journal")
        if value.get("transaction_id") != transaction_id:
            raise ContextUpdateCoordinatorError("actor provider journal differs")
        status = value.get("status")
        if not isinstance(status, str):
            raise ContextUpdateCoordinatorError("actor provider status is unavailable")
        return status

    def _attest_provider(
        self, transaction_id: str, prepared: Mapping[str, Any]
    ) -> dict[str, Any]:
        release = self._selected_release(prepared)
        candidate_unit = release / "config/environment/systemd/tgw-actor-fleet-provider.service"
        candidate_body = candidate_unit.read_bytes()
        if self.paths.provider_unit.read_bytes() != candidate_body:
            raise ContextUpdateCoordinatorError("provider unit differs from selected release")
        expected_bounding: set[str] | None = None
        expected_ambient: set[str] | None = None
        expected_exec: str | None = None
        for row in candidate_body.decode("utf-8").splitlines():
            if row.startswith("CapabilityBoundingSet="):
                expected_bounding = set(row.partition("=")[2].split())
            elif row.startswith("AmbientCapabilities="):
                expected_ambient = set(row.partition("=")[2].split())
            elif row.startswith("ExecStart="):
                expected_exec = row.partition("=")[2].split()[0]
        if expected_bounding is None or expected_ambient is None or expected_exec is None:
            raise ContextUpdateCoordinatorError("provider unit attestation inputs are incomplete")
        properties = (
            "ActiveState", "SubState", "MainPID", "FragmentPath", "ExecStart",
            "CapabilityBoundingSet", "AmbientCapabilities",
        )
        result = self._required_command(
            transaction_id,
            [
                str(self.paths.systemctl), "show", "tgw-actor-fleet-provider.service",
                *[f"--property={name}" for name in properties], "--no-pager",
            ],
            "provider service attestation",
        )
        observed: dict[str, str] = {}
        for row in result.stdout.splitlines():
            name, separator, value = row.partition("=")
            if separator and name in properties:
                observed[name] = value
        try:
            pid = int(observed["MainPID"])
        except (KeyError, ValueError) as exc:
            raise ContextUpdateCoordinatorError("provider service PID is invalid") from exc
        if (
            set(observed) != set(properties)
            or observed["ActiveState"] != "active"
            or observed["SubState"] != "running"
            or observed["FragmentPath"] != str(self.paths.provider_unit)
            or expected_exec not in observed["ExecStart"]
            or set(observed["CapabilityBoundingSet"].split()) != expected_bounding
            or set(observed["AmbientCapabilities"].split()) != expected_ambient
            or pid <= 1
        ):
            raise ContextUpdateCoordinatorError("provider service attestation differs")
        try:
            stat_row = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8")
            executable = (Path("/proc") / str(pid) / "exe").resolve(strict=True)
            start_ticks = int(stat_row.rsplit(") ", 1)[1].split()[19])
        except (OSError, ValueError, IndexError) as exc:
            raise ContextUpdateCoordinatorError("provider process attestation is unavailable") from exc
        receipt = {
            "schema": "tgw-context-provider-attestation/v1",
            "transaction_id": transaction_id,
            "unit_sha256": "sha256:" + hashlib.sha256(candidate_body).hexdigest(),
            "active_state": "active",
            "sub_state": "running",
            "pid": pid,
            "start_ticks": start_ticks,
            "executable": str(executable),
            "executable_sha256": _file_hash(executable),
            "capability_bounding_set": sorted(expected_bounding),
            "ambient_capabilities": sorted(expected_ambient),
        }
        relay_unit = release / (
            "config/environment/systemd/tgw-context-confirmation-relay.service"
        )
        relay_script = release / "src/tgw/context_confirmation_relay.py"
        relay_body = relay_unit.read_bytes()
        if self.paths.relay_unit.read_bytes() != relay_body:
            raise ContextUpdateCoordinatorError(
                "confirmation relay unit differs from selected release"
            )
        relay_exec = next(
            (
                row.partition("=")[2]
                for row in relay_body.decode().splitlines()
                if row.startswith("ExecStart=")
            ),
            None,
        )
        expected_relay_exec = (
            "/usr/bin/python3 -I -s -P "
            "/opt/TGW/tgw-lib/actor-runtime/current/src/tgw/"
            "context_confirmation_relay.py"
        )
        if relay_exec != expected_relay_exec:
            raise ContextUpdateCoordinatorError(
                "confirmation relay executable boundary differs"
            )
        relay_properties = (
            "ActiveState", "SubState", "MainPID", "FragmentPath", "ExecStart",
            "CapabilityBoundingSet", "AmbientCapabilities",
        )
        relay_result = self._required_command(
            transaction_id,
            [
                str(self.paths.systemctl), "show",
                "tgw-context-confirmation-relay.service",
                *[f"--property={name}" for name in relay_properties],
                "--no-pager",
            ],
            "confirmation relay service attestation",
        )
        relay_observed = dict(
            row.split("=", 1) for row in relay_result.stdout.splitlines()
            if "=" in row and row.split("=", 1)[0] in relay_properties
        )
        try:
            relay_pid = int(relay_observed["MainPID"])
            relay_identity = _strong_process_identity(relay_pid, expected_uid=0)
            relay_cmdline = Path(f"/proc/{relay_pid}/cmdline").read_bytes().split(
                b"\0"
            )
        except (KeyError, OSError, ValueError) as exc:
            raise ContextUpdateCoordinatorError(
                "confirmation relay process attestation is unavailable"
            ) from exc
        if (
            set(relay_observed) != set(relay_properties)
            or relay_observed["ActiveState"] != "active"
            or relay_observed["SubState"] != "running"
            or relay_observed["FragmentPath"] != str(self.paths.relay_unit)
            or expected_relay_exec not in relay_observed["ExecStart"]
            or set(relay_observed["CapabilityBoundingSet"].split())
            != {"CAP_SYS_PTRACE"}
            or set(relay_observed["AmbientCapabilities"].split())
            != {"CAP_SYS_PTRACE"}
            or str(relay_script).encode() not in relay_cmdline
        ):
            raise ContextUpdateCoordinatorError(
                "confirmation relay service attestation differs"
            )
        receipt["confirmation_relay"] = {
            "unit_sha256": "sha256:" + hashlib.sha256(relay_body).hexdigest(),
            "interpreter_path": str(self.paths.python3),
            "interpreter_sha256": _file_hash(self.paths.python3),
            "script_path": str(relay_script),
            "script_sha256": _file_hash(relay_script),
            "process_identity_hash": relay_identity["identity_hash"],
            "capability_bounding_set": ["CAP_SYS_PTRACE"],
            "ambient_capabilities": ["CAP_SYS_PTRACE"],
        }
        return {**receipt, "attestation_sha256": _hash(receipt)}

    def _service_restart_landed(
        self, transaction_id: str, journal: Mapping[str, Any]
    ) -> bool:
        baseline = next(
            item for item in journal.get("service_preimages", [])
            if isinstance(item, Mapping) and item.get("target_id") == "provider-service"
        )
        result = self._required_command(
            transaction_id,
            [
                str(self.paths.systemctl), "show", "tgw-actor-fleet-provider.service",
                "--property=ActiveState", "--property=ExecMainStartTimestampMonotonic",
                "--no-pager",
            ],
            "provider restart observation",
        )
        observed = dict(row.split("=", 1) for row in result.stdout.splitlines() if "=" in row)
        return (
            observed.get("ActiveState") == "active"
            and observed.get("ExecMainStartTimestampMonotonic")
            != baseline["properties"]["ExecMainStartTimestampMonotonic"]
        )

    def _prepare_cold_workspace(
        self, transaction_id: str, prepared: Mapping[str, Any], journal: Mapping[str, Any]
    ) -> tuple[Path, Path]:
        workspace = Path(
            str(self._preimage(journal, "cold-continuity-workspace")["path"])
        )
        scratch = Path(
            str(self._preimage(journal, "transaction-scratch-root")["path"])
        )
        if workspace.parent != scratch or scratch != self.paths.scratch_root / transaction_id:
            raise ContextUpdateCoordinatorError("cold continuity workspace escaped")
        if workspace.exists() or workspace.is_symlink():
            self._remove_path(workspace)
        try:
            account = pwd.getpwnam("claude")
        except KeyError as exc:
            raise ContextUpdateCoordinatorError("Claude account is unavailable") from exc
        if account.pw_uid != _CLAUDE_UID or Path(account.pw_dir) != Path("/home/claude"):
            raise ContextUpdateCoordinatorError("Claude account identity differs")
        os.chmod(scratch, 0o711)
        workspace.mkdir(mode=0o711)
        cwd = workspace / "cwd"
        for path, mode, uid, gid in (
            (cwd, 0o555, self.trusted_uid, self.trusted_gid),
            (workspace / "cache", 0o700, account.pw_uid, account.pw_gid),
            (workspace / "tmp", 0o700, account.pw_uid, account.pw_gid),
        ):
            path.mkdir(parents=True, exist_ok=True, mode=mode)
            os.chmod(path, mode)
            if os.geteuid() == 0:
                os.chown(path, uid, gid)
        return workspace, cwd

    def _verify_claude_installed_store(
        self, transaction_id: str, prepared: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Verify Claude's ordinary MCP, skills, and signed instruction entry point."""
        provider_path = (
            self.paths.fleet_private_root / f"{transaction_id}.actor-provider.json"
        )
        _private_file_is_exact(
            provider_path, trusted_uid=self.trusted_uid, trusted_gid=self.trusted_gid
        )
        provider = _read_json(provider_path, "actor provider journal")
        materialization = provider.get("materialization")
        if not isinstance(materialization, Mapping):
            raise ContextUpdateCoordinatorError("Claude materialization is unavailable")
        bindings = materialization.get("bindings")
        if not isinstance(bindings, list):
            raise ContextUpdateCoordinatorError("Claude materialization is incomplete")
        claude_rows = [
            dict(row) for row in bindings
            if isinstance(row, Mapping) and row.get("actor") == "claude"
        ]
        primary_rows = [
            row for row in claude_rows
            if row.get("kind") == "mcp"
            and row.get("name") == "tgw-context"
            and row.get("destination") == "/home/claude/.claude.json"
        ]
        skill_destinations = {
            "tgw-plan": "/home/claude/.claude/skills/tgw-plan",
            "tgw-review": "/home/claude/.claude/skills/tgw-review",
        }
        skill_rows = {
            name: [
                row
                for row in claude_rows
                if row.get("kind") == "skill"
                and row.get("name") == name
                and row.get("destination") == destination
            ]
            for name, destination in skill_destinations.items()
        }
        instruction_rows = [
            row
            for row in claude_rows
            if row.get("kind") == "instruction"
            and row.get("name") == "agent-entry-point"
            and row.get("destination")
            == str(_CLAUDE_INSTRUCTION_ENTRY_POINT)
        ]
        if (
            len(primary_rows) != 1
            or any(len(rows) != 1 for rows in skill_rows.values())
            or len(instruction_rows) != 1
        ):
            raise ContextUpdateCoordinatorError("Claude ordinary installed store is incomplete")

        transaction_path = Path(str(materialization.get("transaction_journal", "")))
        expected_transaction = (
            self.paths.fleet_private_root
            / f"{transaction_id}.actor-materializer.json"
        )
        if transaction_path != expected_transaction:
            raise ContextUpdateCoordinatorError("Claude materializer journal escaped")
        _private_file_is_exact(
            transaction_path,
            trusted_uid=self.trusted_uid,
            trusted_gid=self.trusted_gid,
        )
        materializer = _read_json(transaction_path, "actor materializer journal")
        effects = materializer.get("effects")
        if not isinstance(effects, list):
            raise ContextUpdateCoordinatorError("Claude materializer effects are unavailable")

        primary = primary_rows[0]
        destination = Path(str(primary["destination"]))
        source = Path(str(primary.get("source", "")))
        matching_effects = [
            effect for effect in effects
            if isinstance(effect, Mapping)
            and effect.get("actor") == "claude"
            and effect.get("name") == "tgw-context"
            and effect.get("destination") == str(destination)
            and effect.get("source") == str(source)
        ]
        if len(matching_effects) != 1:
            raise ContextUpdateCoordinatorError("Claude primary projection is unrecorded")
        effect = matching_effects[0]
        if effect.get("materialization") != "claude-user-json":
            raise ContextUpdateCoordinatorError("Claude primary projection mode differs")
        destination_state, destination_raw = _stable_regular_file(
            destination, 16 * 1024 * 1024, "Claude primary configuration"
        )
        _source_state, source_raw = _stable_regular_file(
            source, 1024 * 1024, "Claude Context registration fragment"
        )
        try:
            destination_value = json.loads(destination_raw)
            source_value = json.loads(source_raw)
            destination_endpoint = destination_value["mcpServers"]["tgw-context"]
            source_endpoint = source_value["mcpServers"]["tgw-context"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ContextUpdateCoordinatorError(
                "Claude primary Context registration is invalid"
            ) from exc
        desired_sha256 = "sha256:" + hashlib.sha256(destination_raw).hexdigest()
        source_sha256 = "sha256:" + hashlib.sha256(source_raw).hexdigest()
        if (
            not isinstance(destination_endpoint, Mapping)
            or dict(destination_endpoint) != dict(source_endpoint)
            or desired_sha256 != effect.get("desired_sha256")
            or source_sha256 != primary.get("sha256")
            or destination_state.st_uid != effect.get("desired_uid")
            or destination_state.st_gid != effect.get("desired_gid")
            or stat.S_IMODE(destination_state.st_mode) != effect.get("desired_mode")
            or destination_state.st_nlink != 1
        ):
            raise ContextUpdateCoordinatorError("Claude primary installed store differs")

        for skill_name, rows in skill_rows.items():
            skill = rows[0]
            skill_destination = Path(str(skill["destination"]))
            skill_source = Path(str(skill.get("source", "")))
            if (
                not skill_destination.is_symlink()
                or skill_destination.resolve(strict=True)
                != skill_source.resolve(strict=True)
                or skill_source.is_symlink()
                or not skill_source.is_dir()
            ):
                raise ContextUpdateCoordinatorError(
                    f"Claude installed {skill_name} skill differs"
                )
            digest = hashlib.sha256()
            files = [
                item
                for item in skill_source.rglob("*")
                if item.is_file()
                and not item.is_symlink()
                and "__pycache__" not in item.parts
            ]
            for item in sorted(
                files,
                key=lambda value: value.relative_to(skill_source).as_posix(),
            ):
                digest.update(item.relative_to(skill_source).as_posix().encode())
                digest.update(b"\0")
                digest.update(item.read_bytes())
                digest.update(b"\0")
            if "sha256:" + digest.hexdigest() != skill.get("sha256"):
                raise ContextUpdateCoordinatorError(
                    f"Claude installed {skill_name} skill hash differs"
                )

        generation_receipt = prepared.get("actor_generation_receipt")
        generation = (
            generation_receipt.get("generation")
            if isinstance(generation_receipt, Mapping)
            else None
        )
        if not isinstance(generation, str) or _HASH.fullmatch(generation) is None:
            raise ContextUpdateCoordinatorError("Claude actor generation is invalid")
        bundle = _actor_bundle(self.paths, generation)
        actor_bundle = bundle.get("actors", {}).get("claude")
        bundle_bindings = (
            actor_bundle.get("bindings")
            if isinstance(actor_bundle, Mapping)
            else None
        )
        if (
            generation_receipt.get("bundle_hash") != _hash(bundle)
            or not isinstance(bundle_bindings, list)
        ):
            raise ContextUpdateCoordinatorError(
                "Claude signed generation bundle differs"
            )

        def one_bundle_binding(kind: str, name: str) -> dict[str, Any]:
            matches = [
                dict(row)
                for row in bundle_bindings
                if isinstance(row, Mapping)
                and row.get("kind") == kind
                and row.get("name") == name
            ]
            if len(matches) != 1:
                raise ContextUpdateCoordinatorError(
                    f"Claude generation {kind} binding differs"
                )
            return matches[0]

        instruction_binding = one_bundle_binding(
            "instruction", "agent-entry-point"
        )
        bootstrap_binding = one_bundle_binding("bootstrap", "bootstrap-receipt")
        contract_binding = one_bundle_binding("contract", "actor-contract")
        if (
            instruction_binding.get("destination")
            != str(_CLAUDE_INSTRUCTION_ENTRY_POINT)
            or _HASH.fullmatch(str(instruction_binding.get("sha256", ""))) is None
        ):
            raise ContextUpdateCoordinatorError(
                "Claude generation instruction binding differs"
            )

        contract_path = Path(str(contract_binding.get("source", "")))
        contract_state, contract_raw = _stable_regular_file(
            contract_path, 1024 * 1024, "Claude signed actor contract"
        )
        if (
            contract_state.st_uid != self.trusted_uid
            or contract_state.st_mode & 0o022
            or "sha256:" + hashlib.sha256(contract_raw).hexdigest()
            != contract_binding.get("sha256")
        ):
            raise ContextUpdateCoordinatorError(
                "Claude signed actor contract source differs"
            )
        try:
            contract_document = json.loads(contract_raw)
            trusted_actor_key = base64.b64encode(
                _protected_public_key(
                    self.paths.actor_public_key,
                    trusted_uid=self.trusted_uid,
                )
            ).decode("ascii")
            if (
                trusted_actor_key
                != prepared["trust_projection"]["public_keys"]["actor-contract"]
            ):
                raise ContextUpdateCoordinatorError(
                    "Claude actor contract trust differs"
                )
            contract = verify_signed_actor_contract(
                contract_document,
                trusted_public_key=trusted_actor_key,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ContextUpdateCoordinatorError(
                "Claude signed actor contract is invalid"
            ) from exc
        contract_hashes = generation_receipt.get("contract_receipt_hashes")
        expected_contract_hash = (
            contract_hashes.get("claude")
            if isinstance(contract_hashes, Mapping)
            else None
        )
        if (
            contract.get("actor") != "claude"
            or contract.get("receipt_hash") != expected_contract_hash
        ):
            raise ContextUpdateCoordinatorError(
                "Claude signed actor contract identity differs"
            )

        bootstrap_path = Path(str(bootstrap_binding.get("source", "")))
        bootstrap_state, bootstrap_raw = _stable_regular_file(
            bootstrap_path, 1024 * 1024, "Claude signed bootstrap receipt"
        )
        bootstrap_file_hash = "sha256:" + hashlib.sha256(bootstrap_raw).hexdigest()
        try:
            bootstrap = json.loads(bootstrap_raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContextUpdateCoordinatorError(
                "Claude signed bootstrap receipt is invalid"
            ) from exc
        if not isinstance(bootstrap, Mapping):
            raise ContextUpdateCoordinatorError(
                "Claude signed bootstrap receipt is invalid"
            )
        bootstrap = dict(bootstrap)
        unsigned_bootstrap = dict(bootstrap)
        bootstrap_receipt_hash = unsigned_bootstrap.pop("receipt_hash", None)
        signed_instruction = bootstrap.get("instructions")
        if (
            bootstrap_state.st_uid != self.trusted_uid
            or bootstrap_state.st_mode & 0o022
            or bootstrap_file_hash != bootstrap_binding.get("sha256")
            or contract.get("local", {}).get("bootstrap_receipt_hash")
            != bootstrap_file_hash
            or bootstrap_receipt_hash != _hash(unsigned_bootstrap)
            or bootstrap.get("schema") != "tgw-actor-bootstrap-receipt/v1"
            or bootstrap.get("status") != "READY"
            or bootstrap.get("actor") != "claude"
            or bootstrap.get("generation") != generation
            or not isinstance(signed_instruction, Mapping)
            or set(signed_instruction) != {"agent-entry-point"}
            or signed_instruction.get("agent-entry-point")
            != {
                "path": instruction_binding["destination"],
                "sha256": instruction_binding["sha256"],
            }
        ):
            raise ContextUpdateCoordinatorError(
                "Claude signed instruction receipt differs"
            )

        instruction = instruction_rows[0]
        instruction_source = Path(str(instruction.get("source", "")))
        instruction_destination = Path(str(instruction["destination"]))
        source_state, instruction_raw = _stable_regular_file(
            instruction_source,
            _MAX_OWNER_DIRECTIVE,
            "Claude concise instruction source",
        )
        instruction_hash = "sha256:" + hashlib.sha256(instruction_raw).hexdigest()
        candidate_instruction = self._selected_release(prepared) / "AGENTS.md"
        candidate_state, candidate_raw = _stable_regular_file(
            candidate_instruction,
            _MAX_OWNER_DIRECTIVE,
            "candidate concise instruction entry point",
        )
        instruction_effects = [
            effect
            for effect in effects
            if isinstance(effect, Mapping)
            and effect.get("actor") == "claude"
            and effect.get("name") == "agent-entry-point"
            and effect.get("destination") == str(instruction_destination)
            and effect.get("source") == str(instruction_source)
        ]
        instruction_effect = (
            instruction_effects[0] if len(instruction_effects) == 1 else {}
        )
        if (
            source_state.st_uid != self.trusted_uid
            or source_state.st_mode & 0o022
            or candidate_state.st_uid != self.trusted_uid
            or candidate_state.st_mode & 0o022
            or instruction_source.resolve(strict=True)
            != candidate_instruction.resolve(strict=True)
            or instruction_raw != candidate_raw
            or instruction_hash != instruction_binding.get("sha256")
            or instruction.get("sha256") != instruction_hash
            or len(instruction_effects) != 1
            or instruction.get("materialization")
            != instruction_effect.get("materialization")
            or not _instruction_destination_is_exact(
                instruction_destination,
                instruction_source,
                instruction_raw,
                instruction_effect,
            )
        ):
            raise ContextUpdateCoordinatorError(
                "Claude installed concise instruction entry point differs"
            )
        instruction_proof = {
            "schema": "tgw-context-cold-instruction-binding/v1",
            "actor": "claude",
            "path": str(instruction_destination),
            "sha256": instruction_hash,
            "bootstrap_receipt_hash": bootstrap_file_hash,
            "contract_receipt_hash": str(contract["receipt_hash"]),
        }
        return {
            **instruction_proof,
            "binding_sha256": _hash(instruction_proof),
        }

    @staticmethod
    def _workspace_bytes(path: Path) -> int:
        total = 0
        for item in path.rglob("*"):
            if item.is_file() and not item.is_symlink():
                total += item.stat(follow_symlinks=False).st_size
                if total > _MAX_COLD_WORKSPACE:
                    raise ContextUpdateCoordinatorError(
                        "cold continuity workspace bound exceeded"
                    )
        return total

    def _cold_command(self, workspace: Path) -> list[str]:
        allowed = [
            "Skill", "ToolSearch",
            "mcp__tgw-context__tgw_context_status",
            "mcp__tgw-context__tgw_context_bundle",
            "mcp__tgw-context__tgw_context_plan_graph",
            "mcp__tgw-context__tgw_context_plan_source",
        ]
        prompt = (
            "Load the installed tgw-plan and tgw-review skills. Through the installed "
            "tgw-context MCP "
            "registration only, call status; request a repair/remediation bundle for "
            "receiver claude; resolve the current remediation task with plan_graph "
            "operation resolve; then read every line of each of these four sources "
            "with authority current-plan, continuing in bounded chunks until total_lines: "
            + ", ".join(_CURRENT_PLAN_SOURCES)
            + ". Report only after the tool calls complete. Do not use shell, filesystem "
            "read/write, web, conversation history, resume, or any unlisted tool."
        )
        return [
            str(self.paths.sudo), "-n", "-u", "claude", "/usr/bin/env", "-i",
            "HOME=/home/claude", "USER=claude", "LOGNAME=claude",
            "LANG=C.UTF-8", "LC_ALL=C.UTF-8", "PATH=/usr/local/bin:/usr/bin:/bin",
            f"TMPDIR={workspace / 'tmp'}",
            f"XDG_CACHE_HOME={workspace / 'cache'}",
            str(self.paths.claude_executable), "-p", "--output-format", "stream-json",
            "--verbose", "--no-session-persistence", "--permission-mode", "dontAsk",
            "--setting-sources", "user", "--tools", ",".join(allowed),
            "--allowedTools", ",".join(allowed), "--max-budget-usd", "5", prompt,
        ]

    def _run_cold(
        self, transaction_id: str, command: Sequence[str], cwd: Path
    ) -> subprocess.CompletedProcess[str]:
        if self._runner is not None:
            return self._runner(command)
        return subprocess.run(
            list(command),
            cwd=cwd,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=900,
            env={
                "HOME": "/root", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "TMPDIR": str(self.paths.scratch_root / transaction_id),
            },
        )

    def _cold_continuity(
        self,
        transaction_id: str,
        prepared: Mapping[str, Any],
        journal: Mapping[str, Any],
    ) -> dict[str, Any]:
        instruction = self._verify_claude_installed_store(
            transaction_id, prepared
        )
        transcript_path = Path(
            str(self._preimage(journal, "cold-continuity-transcript")["path"])
        )
        receipt_path = Path(
            str(self._preimage(journal, "cold-continuity-receipt")["path"])
        )
        if receipt_path.exists() or receipt_path.is_symlink():
            receipt = _read_json(receipt_path, "cold continuity receipt")
            _hashed_record(receipt, "receipt_sha256", "cold continuity receipt")
            raw = _bounded_read(
                transcript_path, _MAX_COLD_TRANSCRIPT, "cold continuity transcript"
            )
            proof = verify_cold_continuity_transcript(
                raw, prepared["actor_request"], instruction
            )
            if (
                receipt.get("status") != "PASS"
                or receipt.get("proof_sha256") != proof["proof_sha256"]
                or receipt.get("transcript_sha256") != proof["transcript_sha256"]
            ):
                raise ContextUpdateCoordinatorError("cold continuity receipt differs")
            return receipt
        workspace, cwd = self._prepare_cold_workspace(
            transaction_id, prepared, journal
        )
        try:
            result = self._run_cold(
                transaction_id, self._cold_command(workspace), cwd
            )
            raw = result.stdout.encode("utf-8")
            if result.returncode != 0:
                raise ContextUpdateCoordinatorError("ordinary Claude cold launch failed")
            proof = verify_cold_continuity_transcript(
                raw, prepared["actor_request"], instruction
            )
            workspace_bytes = self._workspace_bytes(workspace)
            _atomic_bytes(
                transcript_path,
                raw,
                mode=0o600,
                uid=self.trusted_uid,
                gid=self.trusted_gid,
            )
            body = {
                "schema": "tgw-context-cold-handoff-receipt/v1",
                "status": "PASS",
                "transaction_id": transaction_id,
                "actor": "claude",
                "actor_generation": prepared["actor_request"]["successor_generation"],
                "proof_sha256": proof["proof_sha256"],
                "transcript_sha256": proof["transcript_sha256"],
                "workspace_peak_bytes": workspace_bytes,
                "completed_at": _utc(self.now()),
            }
            receipt = {**body, "receipt_sha256": _hash(body)}
            _atomic_json(
                receipt_path,
                receipt,
                mode=0o600,
                uid=self.trusted_uid,
                gid=self.trusted_gid,
            )
            return receipt
        finally:
            if workspace.exists() or workspace.is_symlink():
                self._remove_path(workspace)
            scratch = self.paths.scratch_root / transaction_id
            if scratch.is_dir() and not scratch.is_symlink():
                os.chmod(scratch, 0o700)
                _fsync_directory(scratch)

    def _deepseek_baseline(self, journal: Mapping[str, Any]) -> dict[str, Any]:
        matches = [
            dict(item) for item in journal.get("service_preimages", [])
            if isinstance(item, Mapping)
            and item.get("target_id") == "deepseek-user-service"
        ]
        if len(matches) != 1:
            raise ContextUpdateCoordinatorError("DeepSeek service preimage is unavailable")
        return matches[0]

    def _write_deepseek_progress(
        self, path: Path, value: Mapping[str, Any]
    ) -> dict[str, Any]:
        unsigned = dict(value)
        unsigned.pop("progress_sha256", None)
        result = {**unsigned, "progress_sha256": _hash(unsigned)}
        _atomic_json(
            path,
            result,
            mode=0o600,
            uid=self.trusted_uid,
            gid=self.trusted_gid,
        )
        return result

    def _wait_deepseek_manager(
        self, transaction_id: str, *, attempts: int = 40
    ) -> dict[str, Any]:
        """Bound normal logind/user-manager startup without creating a new gate."""
        last_error: Exception | None = None
        for _attempt in range(attempts):
            try:
                observed = _deepseek_service_preimage(
                    self.paths, self._run_for(transaction_id)
                )
                if observed.get("manager_available"):
                    return observed
            except ContextUpdateCoordinatorError as exc:
                # loginctl's Linger projection and the user bus can lag the
                # already durable linger file for a short bounded interval.
                last_error = exc
            if self._runner is None:
                time.sleep(0.25)
        detail = f": {last_error}" if last_error is not None else ""
        raise ContextUpdateCoordinatorError(
            f"DeepSeek user manager is starting; resume this transaction{detail}"
        )

    def _deepseek_transition(
        self,
        transaction_id: str,
        prepared: Mapping[str, Any],
        journal: Mapping[str, Any],
    ) -> dict[str, Any]:
        cold_path = Path(
            str(self._preimage(journal, "cold-continuity-receipt")["path"])
        )
        cold = _read_json(cold_path, "cold continuity receipt")
        _hashed_record(cold, "receipt_sha256", "cold continuity receipt")
        if cold.get("status") != "PASS" or cold.get("transaction_id") != transaction_id:
            raise ContextUpdateCoordinatorError("DeepSeek transition lacks cold continuity")
        baseline = self._deepseek_baseline(journal)
        progress_path = Path(
            str(self._preimage(journal, "deepseek-service-progress")["path"])
        )
        receipt_path = Path(
            str(self._preimage(journal, "deepseek-service-action-receipt")["path"])
        )
        old_identity = baseline.get("parent_identity")
        baseline_active = (
            isinstance(baseline.get("properties"), Mapping)
            and baseline["properties"].get("ActiveState") == "active"
        )
        unit_state = self.paths.deepseek_unit.stat(follow_symlinks=False)
        if (
            self.paths.deepseek_unit.is_symlink()
            or _file_hash(self.paths.deepseek_unit) != baseline.get("unit_sha256")
            or stat.S_IMODE(unit_state.st_mode) != baseline.get("unit_mode")
            or unit_state.st_uid != baseline.get("unit_uid")
            or unit_state.st_gid != baseline.get("unit_gid")
            or unit_state.st_nlink != baseline.get("unit_nlink")
        ):
            raise ContextUpdateCoordinatorError("DeepSeek managed unit changed")
        if progress_path.exists() or progress_path.is_symlink():
            progress = _read_json(progress_path, "DeepSeek service progress")
            _hashed_record(progress, "progress_sha256", "DeepSeek service progress")
            if (
                progress.get("schema") != "tgw-deepseek-service-progress/v1"
                or progress.get("transaction_id") != transaction_id
                or progress.get("baseline_sha256") != _hash(baseline)
            ):
                raise ContextUpdateCoordinatorError("DeepSeek service progress differs")
        else:
            entry = _deepseek_service_preimage(
                self.paths, self._run_for(transaction_id)
            )
            entry_active = (
                isinstance(entry.get("properties"), Mapping)
                and entry["properties"].get("ActiveState") == "active"
            )
            entry_identity = entry.get("parent_identity")
            if baseline_active and entry_active and isinstance(old_identity, Mapping) and (
                isinstance(entry_identity, Mapping)
                and entry_identity.get("identity_hash")
                == old_identity.get("identity_hash")
            ):
                lifecycle = "RESTART"
            elif not baseline_active and not entry_active:
                lifecycle = "START"
            else:
                lifecycle = "OBSERVE_LATE_ARRIVAL"
            progress = self._write_deepseek_progress(
                progress_path,
                {
                    "schema": "tgw-deepseek-service-progress/v1",
                    "transaction_id": transaction_id,
                    "baseline_sha256": _hash(baseline),
                    "lifecycle_action": lifecycle,
                    "entry_state_sha256": _hash(entry),
                    "entry_linger_present": bool(entry.get("linger_present")),
                    "entry_active": entry_active,
                    "entry_parent_identity_hash": (
                        entry_identity.get("identity_hash")
                        if isinstance(entry_identity, Mapping) else None
                    ),
                    "lifecycle_intent_fsynced": True,
                    "linger_enabled_by_transaction": False,
                    "linger_external_after_entry": False,
                    "lifecycle_command_started": False,
                    "lifecycle_command_completed": False,
                    "lifecycle_completed": False,
                    "provider_transition_hashes": [],
                },
            )
        lifecycle = str(progress["lifecycle_action"])
        if lifecycle not in {"START", "RESTART", "OBSERVE_LATE_ARRIVAL"}:
            raise ContextUpdateCoordinatorError("DeepSeek lifecycle action differs")
        # The lifecycle intent is durable before enable-linger because enabling
        # linger may itself start this already-enabled unit.
        if not progress.get("entry_linger_present"):
            linger_token = Path(
                str(self._preimage(journal, "deepseek-linger-token")["path"])
            )
            if not linger_token.exists() and not linger_token.is_symlink():
                _atomic_bytes(
                    linger_token,
                    (transaction_id + "\n").encode(),
                    mode=0o644,
                    uid=self.trusted_uid,
                    gid=self.trusted_gid,
                )
            if linger_token.is_symlink() or not linger_token.is_file():
                raise ContextUpdateCoordinatorError("DeepSeek linger token differs")
            introduced = False
            if not self.paths.deepseek_linger.exists():
                try:
                    os.link(
                        linger_token,
                        self.paths.deepseek_linger,
                        follow_symlinks=False,
                    )
                    _fsync_directory(self.paths.deepseek_linger.parent)
                    introduced = True
                except FileExistsError:
                    introduced = False
            elif self.paths.deepseek_linger.is_symlink():
                raise ContextUpdateCoordinatorError("DeepSeek linger path is unsafe")
            else:
                introduced = os.path.samefile(
                    linger_token, self.paths.deepseek_linger
                )
            progress["linger_enabled_by_transaction"] = introduced
            progress["linger_external_after_entry"] = not introduced
            progress = self._write_deepseek_progress(progress_path, progress)
            self._required_command(
                transaction_id,
                [str(self.paths.loginctl), "enable-linger", "deepseek"],
                "DeepSeek linger enable",
            )
            if self.event_hook is not None:
                self.event_hook("MUTATION:TRANSITION_DEEPSEEK_SERVICE:ENABLE_LINGER")
            if self.paths.deepseek_linger.is_symlink() or not self.paths.deepseek_linger.is_file():
                raise ContextUpdateCoordinatorError("DeepSeek linger enable is pending")

        current = self._wait_deepseek_manager(transaction_id)
        current_properties = current.get("properties")
        current_identity = current.get("parent_identity")
        if not isinstance(current_properties, Mapping):
            raise ContextUpdateCoordinatorError("DeepSeek service state is unavailable")
        if not progress.get("lifecycle_completed"):
            entry_parent = progress.get("entry_parent_identity_hash")
            current_parent = (
                current_identity.get("identity_hash")
                if isinstance(current_identity, Mapping) else None
            )
            externally_changed = (
                not progress.get("lifecycle_command_started")
                and current_parent != entry_parent
                and not (
                    lifecycle == "START"
                    and progress.get("linger_enabled_by_transaction")
                    and current_properties.get("ActiveState") == "active"
                )
            )
            if externally_changed:
                lifecycle = "OBSERVE_LATE_ARRIVAL"
                progress["lifecycle_action"] = lifecycle
            elif lifecycle == "RESTART":
                if (
                    progress.get("lifecycle_command_started")
                    and not progress.get("lifecycle_command_completed")
                    and current_parent != entry_parent
                ):
                    # A process transition observed across the only ambiguous
                    # crash window is never falsely attributed to this
                    # transaction.  The provider will inventory it as a late
                    # arrival rather than accepting a fabricated restart
                    # receipt.
                    lifecycle = "OBSERVE_LATE_ARRIVAL"
                    progress["lifecycle_action"] = lifecycle
                elif not progress.get("lifecycle_command_completed"):
                    progress["lifecycle_command_started"] = True
                    progress = self._write_deepseek_progress(progress_path, progress)
                    self._required_command(
                        transaction_id,
                        _deepseek_user_command(
                            self.paths, "restart", _DEEPSEEK_UNIT
                        ),
                        "DeepSeek managed service restart",
                    )
                    progress["lifecycle_command_completed"] = True
                    progress = self._write_deepseek_progress(progress_path, progress)
                    if self.event_hook is not None:
                        self.event_hook(
                            "MUTATION:TRANSITION_DEEPSEEK_SERVICE:RESTART"
                        )
            elif lifecycle == "START":
                if (
                    progress.get("lifecycle_command_started")
                    and not progress.get("lifecycle_command_completed")
                    and current_properties.get("ActiveState") == "active"
                ):
                    lifecycle = "OBSERVE_LATE_ARRIVAL"
                    progress["lifecycle_action"] = lifecycle
                elif (
                    current_properties.get("ActiveState") != "active"
                    and not progress.get("lifecycle_command_completed")
                ):
                    progress["lifecycle_command_started"] = True
                    progress = self._write_deepseek_progress(
                        progress_path, progress
                    )
                    self._required_command(
                        transaction_id,
                        _deepseek_user_command(
                            self.paths, "start", _DEEPSEEK_UNIT
                        ),
                        "DeepSeek managed service start",
                    )
                    progress["lifecycle_command_completed"] = True
                    progress = self._write_deepseek_progress(progress_path, progress)
                    if self.event_hook is not None:
                        self.event_hook(
                            "MUTATION:TRANSITION_DEEPSEEK_SERVICE:START"
                        )
            progress["lifecycle_completed"] = True
            progress = self._write_deepseek_progress(progress_path, progress)
            current = _deepseek_service_preimage(
                self.paths, self._run_for(transaction_id)
            )
            current_properties = current.get("properties")
            current_identity = current.get("parent_identity")
        if (
            not isinstance(current_properties, Mapping)
            or current_properties.get("ActiveState") != "active"
            or not isinstance(current_identity, Mapping)
            or _HASH.fullmatch(str(current_identity.get("identity_hash", ""))) is None
        ):
            raise ContextUpdateCoordinatorError("DeepSeek managed service is not active")
        if (
            lifecycle == "RESTART"
            and isinstance(old_identity, Mapping)
            and current_identity.get("identity_hash") == old_identity.get("identity_hash")
        ):
            raise ContextUpdateCoordinatorError("DeepSeek managed restart did not replace parent")

        if receipt_path.exists() or receipt_path.is_symlink():
            receipt = _read_json(receipt_path, "DeepSeek service action receipt")
            _hashed_record(receipt, "action_receipt_sha256", "DeepSeek service action receipt")
            if (
                receipt.get("transaction_id") != transaction_id
                or receipt.get("baseline_sha256") != _hash(baseline)
                or receipt.get("lifecycle_action") != lifecycle
            ):
                raise ContextUpdateCoordinatorError("DeepSeek service receipt differs")
        else:
            body = {
                "schema": "tgw-deepseek-managed-service-action/v1",
                "status": "PASS",
                "transaction_id": transaction_id,
                "service_unit": _DEEPSEEK_UNIT,
                "unit_path": str(self.paths.deepseek_unit),
                "unit_sha256": baseline["unit_sha256"],
                "baseline_sha256": _hash(baseline),
                "lifecycle_action": lifecycle,
                "classification": (
                    "DECLARED_USER_SERVICE_RESTART"
                    if lifecycle == "RESTART" else "LATE_ARRIVAL"
                ),
                "linger_enabled_by_transaction": bool(
                    progress["linger_enabled_by_transaction"]
                ),
                "old_parent_identity_hash": (
                    old_identity.get("identity_hash")
                    if isinstance(old_identity, Mapping) else None
                ),
                "new_parent_identity_hash": current_identity["identity_hash"],
                "cold_handoff_receipt_sha256": cold["receipt_sha256"],
                "completed_at": _utc(self.now()),
            }
            receipt = {**body, "action_receipt_sha256": _hash(body)}
            _atomic_json(
                receipt_path,
                receipt,
                mode=0o600,
                uid=self.trusted_uid,
                gid=self.trusted_gid,
            )

        transition_hashes: list[str] = []
        if lifecycle == "RESTART":
            provider_journal = _read_json(
                self.paths.fleet_private_root
                / f"{transaction_id}.actor-provider.json",
                "actor provider journal",
            )
            rebind = provider_journal.get("context_rebind")
            obligations = rebind.get("obligations") if isinstance(rebind, Mapping) else None
            matching = [
                item for item in obligations or []
                if isinstance(item, Mapping)
                and item.get("actor") == "deepseek"
                and isinstance(item.get("baseline"), Mapping)
                and isinstance(item["baseline"].get("parent"), Mapping)
                and item["baseline"]["parent"].get("identity_hash")
                == old_identity.get("identity_hash")
                and _HASH.fullmatch(str(item.get("obligation_id", "")))
            ]
            if not matching:
                raise ContextUpdateCoordinatorError(
                    "DeepSeek provider transition obligation is unavailable"
                )
            for obligation in matching:
                transition_body = {
                    "schema": "tgw-context-parent-transition/v1",
                    "transaction_id": transaction_id,
                    "direction": "successor",
                    "obligation_id": obligation["obligation_id"],
                    "disposition": "DECLARED_USER_SERVICE_RESTART",
                    "service_unit": _DEEPSEEK_UNIT,
                    "old_parent_identity_hash": old_identity["identity_hash"],
                    "new_parent_identity_hash": current_identity["identity_hash"],
                    "action_receipt_sha256": receipt["action_receipt_sha256"],
                }
                transition = {
                    **transition_body,
                    "transition_sha256": _hash(transition_body),
                }
                response = self._provider().invoke(
                    "record-context-parent-transition", [transition]
                )
                if (
                    response.get("status") != "TRANSITION_RECORDED"
                    or response.get("transaction_id") != transaction_id
                    or response.get("obligation_id") != obligation["obligation_id"]
                ):
                    raise ContextUpdateCoordinatorError(
                        "DeepSeek provider transition differs"
                    )
                transition_hashes.append(transition["transition_sha256"])
        progress["provider_transition_hashes"] = sorted(transition_hashes)
        self._write_deepseek_progress(progress_path, progress)
        return {
            "status": "PASS",
            "transaction_id": transaction_id,
            "action_receipt_sha256": receipt["action_receipt_sha256"],
            "classification": receipt["classification"],
            "transition_sha256": sorted(transition_hashes),
        }

    def _finalize_transaction(
        self,
        transaction_id: str,
        prepared: Mapping[str, Any],
        journal: Mapping[str, Any],
        binding: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self._provider_journal_status(transaction_id) != "VERIFIED":
            raise ContextUpdateCoordinatorError("provider transaction is not terminal")
        active_plan = self._verify_active_plan_binding(transaction_id, prepared)
        cold = _read_json(
            Path(str(self._preimage(journal, "cold-continuity-receipt")["path"])),
            "cold handoff receipt",
        )
        deepseek = _read_json(
            Path(
                str(
                    self._preimage(
                        journal, "deepseek-service-action-receipt"
                    )["path"]
                )
            ),
            "DeepSeek action receipt",
        )
        attestation = _read_json(
            Path(
                str(
                    self._preimage(journal, "provider-attestation-receipt")[
                        "path"
                    ]
                )
            ),
            "provider attestation receipt",
        )
        _hashed_record(cold, "receipt_sha256", "cold handoff receipt")
        _hashed_record(
            deepseek, "action_receipt_sha256", "DeepSeek action receipt"
        )
        _hashed_record(
            attestation, "attestation_sha256", "provider attestation receipt"
        )
        projection = _read_json(
            self.paths.fleet_root / "fleet-convergence.json",
            "fleet convergence projection",
        )
        projection_sha256 = _hashed_record(
            projection, "projection_sha256", "fleet convergence projection"
        )
        actor_request = prepared["actor_request"]
        revisions = actor_request["revisions"]
        selected = self._selected_release(prepared)
        receipt_path = Path(
            str(self._preimage(journal, "coordinator-terminal-receipt")["path"])
        )
        body = {
            "schema": "tgw-context-update-terminal-receipt/v1",
            "status": "COMPLETE",
            "transaction_id": transaction_id,
            "coordinator_journal_sha256": _hash(journal),
            "coordinator_binding_sha256": binding["binding_sha256"],
            "effect_plan_sha256": journal["effect_plan"]["effect_plan_sha256"],
            "approved_plan": revisions["plan"],
            "approved_solution": revisions["solution"],
            "plan_activation_sha256": prepared["plan_activation"][
                "activation_sha256"
            ],
            "approved_plan_ref": prepared["plan_activation"]["approved_ref"],
            "approved_plan_materialization": prepared["plan_activation"][
                "successor"
            ]["materialization"],
            "active_plan_binding_sha256": _hash(active_plan),
            "evidence_plan": revisions["evidence_plan"],
            "evidence_tree": revisions["evidence_tree"],
            "current_plan_sources_sha256": _hash(revisions["current_plan_sources"]),
            "source_commit": revisions["source"],
            "source_tree": revisions["source_tree"],
            "catalog": revisions["catalog"],
            "bootstrap": revisions["bootstrap"],
            "broker_policy": revisions["broker_policy"],
            "authority_mode": prepared["request"]["authority"]["mode"],
            "authority_evidence": prepared["authority_evidence"][
                "authority_evidence_sha256"
            ],
            "review": revisions["review"],
            "review_disposition": prepared["authority_evidence"][
                "review_disposition"
            ]["disposition_sha256"],
            "admission": revisions["admission"],
            "actor_generation": actor_request["successor_generation"],
            "selected_release": {
                "path": str(selected),
                "generation": prepared["release_generation"],
                "commit": prepared["request"]["candidate"]["commit"],
                "tree": prepared["request"]["candidate"]["tree"],
                "manifest_sha256": _hash(prepared["release_manifest"]),
            },
            "cold_handoff_receipt_sha256": cold["receipt_sha256"],
            "deepseek_action_receipt_sha256": deepseek["action_receipt_sha256"],
            "provider_attestation_sha256": attestation["attestation_sha256"],
            "fleet_projection_sha256": projection_sha256,
            "predecessor_actor_public_sha256": prepared["trust_projection"][
                "predecessor_actor_public_sha256"
            ],
            "successor_actor_public_sha256": prepared["trust_projection"][
                "successor_actor_public_sha256"
            ],
            "completed_at": _utc(self.now()),
        }
        receipt = {**body, "terminal_receipt_sha256": _hash(body)}
        if receipt_path.exists() or receipt_path.is_symlink():
            existing = _read_json(receipt_path, "coordinator terminal receipt")
            _hashed_record(
                existing, "terminal_receipt_sha256", "coordinator terminal receipt"
            )
            stable_existing, stable_new = dict(existing), dict(receipt)
            for value in (stable_existing, stable_new):
                value.pop("completed_at", None)
                value.pop("terminal_receipt_sha256", None)
            if stable_existing != stable_new:
                raise ContextUpdateCoordinatorError("coordinator terminal retry differs")
            receipt = existing
        else:
            _atomic_json(
                receipt_path,
                receipt,
                mode=0o600,
                uid=self.trusted_uid,
                gid=self.trusted_gid,
            )
        terminal = append_coordinator_event(
            paths=self.paths,
            transaction_id=transaction_id,
            record_role="COORDINATOR_TERMINAL",
            provider_status="COMPLETE",
            journal_sha256=_hash(journal),
            binding_sha256=binding["binding_sha256"],
            evidence={
                key: value for key, value in receipt.items()
                if key not in {"completed_at"}
            },
            trusted_uid=self.trusted_uid,
            now=self.now,
        )
        return {
            "status": "COMPLETE",
            "transaction_id": transaction_id,
            "terminal_receipt_sha256": receipt["terminal_receipt_sha256"],
            "terminal_ledger_record_sha256": terminal["record_sha256"],
        }

    def _restore_deepseek_lifecycle(
        self, transaction_id: str, journal: Mapping[str, Any]
    ) -> dict[str, Any]:
        progress_path = Path(
            str(self._preimage(journal, "deepseek-service-progress")["path"])
        )
        if not progress_path.exists() and not progress_path.is_symlink():
            return {"status": "UNCHANGED"}
        progress = _read_json(progress_path, "DeepSeek service progress")
        _hashed_record(progress, "progress_sha256", "DeepSeek service progress")
        baseline = self._deepseek_baseline(journal)
        baseline_active = (
            isinstance(baseline.get("properties"), Mapping)
            and baseline["properties"].get("ActiveState") == "active"
        )
        action = progress.get("lifecycle_action")
        current = _deepseek_service_preimage(
            self.paths, self._run_for(transaction_id)
        )
        properties = current.get("properties")
        active = (
            isinstance(properties, Mapping)
            and properties.get("ActiveState") == "active"
        )
        transaction_service_effect = bool(
            progress.get("lifecycle_completed")
            or progress.get("lifecycle_command_completed")
            or (
                action == "START"
                and progress.get("linger_enabled_by_transaction")
                and not progress.get("entry_active")
                and active
            )
        )
        if transaction_service_effect and not progress.get(
            "rollback_service_restored"
        ):
            if action == "RESTART" and baseline_active:
                if not current.get("manager_available"):
                    raise ContextUpdateCoordinatorError(
                        "DeepSeek manager unavailable for rollback"
                    )
                self._required_command(
                    transaction_id,
                    _deepseek_user_command(self.paths, "restart", _DEEPSEEK_UNIT),
                    "DeepSeek predecessor service restore",
                )
            elif action == "START" and not baseline_active and active:
                self._required_command(
                    transaction_id,
                    _deepseek_user_command(self.paths, "stop", _DEEPSEEK_UNIT),
                    "DeepSeek inactive service restore",
                )
            # OBSERVE_LATE_ARRIVAL was external state and is never undone.
            progress["rollback_service_restored"] = True
            progress = self._write_deepseek_progress(progress_path, progress)
        if progress.get("linger_enabled_by_transaction") and not progress.get(
            "rollback_linger_restored"
        ):
            token = Path(
                str(self._preimage(journal, "deepseek-linger-token")["path"])
            )
            if (
                token.is_file()
                and not token.is_symlink()
                and self.paths.deepseek_linger.is_file()
                and not self.paths.deepseek_linger.is_symlink()
                and os.path.samefile(token, self.paths.deepseek_linger)
            ):
                self._required_command(
                    transaction_id,
                    [str(self.paths.loginctl), "disable-linger", "deepseek"],
                    "DeepSeek linger rollback",
                )
            progress["rollback_linger_restored"] = True
            progress = self._write_deepseek_progress(progress_path, progress)
        return {
            "status": "RESTORED",
            "service_restored": bool(progress.get("rollback_service_restored")),
            "linger_restored": bool(progress.get("rollback_linger_restored")),
        }

    def _apply_effect(
        self,
        *,
        transaction_id: str,
        action: str,
        prepared: Mapping[str, Any],
        journal: Mapping[str, Any],
        binding: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """Apply one fixed effect; bool false means healthy external wait."""
        actor_request = prepared["actor_request"]
        if action == "INSTALL_PLATFORM_TRUST":
            projection = prepared["trust_projection"]
            installed: dict[str, str] = {}
            for name, target_id, target in (
                ("actor-contract", "actor-public-trust", self.paths.actor_public_key),
                (
                    "environment-preflight", "environment-public-trust",
                    self.paths.environment_public_key,
                ),
                (
                    "release-admission", "admission-public-trust",
                    self.paths.admission_public_key,
                ),
            ):
                try:
                    body = base64.b64decode(
                        str(projection["public_keys"][name]), validate=True
                    )
                except (TypeError, ValueError) as exc:
                    raise ContextUpdateCoordinatorError(
                        "candidate public trust projection differs"
                    ) from exc
                if len(body) != 32:
                    raise ContextUpdateCoordinatorError(
                        "candidate public trust projection differs"
                    )
                receipt = self._install_candidate_file(
                    target=target,
                    body=body,
                    mode=0o444,
                    expected_preimage=self._preimage(journal, target_id),
                )
                installed[name] = str(receipt["sha256"])
                self._checkpoint(
                    f"MUTATION:INSTALL_PLATFORM_TRUST:{target_id}"
                )
            plan_receipt = self._activate_plan_binding(
                transaction_id, prepared, journal
            )
            config = _read_json(
                Path(str(projection["provider_config_path"])),
                "candidate provider config",
            )
            if (
                installed != dict(projection["public_key_sha256"])
                or _hash(config) != projection["provider_config_sha256"]
                or config.get("actor_fleet_provider", {}).get("contract_public_key")
                != projection["public_keys"]["actor-contract"]
            ):
                raise ContextUpdateCoordinatorError("installed platform trust differs")
            receipt = {
                "schema": "tgw-platform-trust-installation/v1",
                "transaction_id": transaction_id,
                "public_key_sha256": installed,
                "provider_config_sha256": plan_receipt[
                    "provider_config_sha256"
                ],
                "plan_activation_receipt_sha256": plan_receipt[
                    "receipt_sha256"
                ],
                "predecessor_actor_public_sha256": projection[
                    "predecessor_actor_public_sha256"
                ],
                "successor_actor_public_sha256": projection[
                    "successor_actor_public_sha256"
                ],
            }
            return {**receipt, "receipt_sha256": _hash(receipt)}, True
        if action == "PUBLISH_ADMISSION":
            admission = prepared["admission"]
            target = self.paths.admission_root / f"{admission['receipt_hash'].removeprefix('sha256:')}.json"
            return self._install_candidate_file(
                target=target,
                body=_canonical(admission) + b"\n",
                mode=0o444,
                expected_preimage=self._preimage(journal, "release-admission"),
            ), True
        if action == "INSTALL_CATALOG":
            body = Path(str(prepared["catalog_path"])).read_bytes()
            if _hash(json.loads(body)) != prepared["catalog_sha256"]:
                raise ContextUpdateCoordinatorError("prepared catalog changed")
            return self._install_candidate_file(
                target=self.paths.installed_catalog,
                body=body,
                mode=0o644,
                expected_preimage=self._preimage(journal, "environment-catalog"),
            ), True
        if action == "SELECT_RELEASE":
            selected = current_generation(self.paths.release_root)
            generation = prepared["release_generation"]
            if selected == generation:
                receipt = _read_json(
                    self.paths.release_root / "receipts" / f"select-{transaction_id}.json",
                    "release selection receipt",
                )
            else:
                if selected != prepared["request"]["expected_current"]["release_generation"]:
                    raise ContextUpdateCoordinatorError("release selector CAS differs")
                first_actor = sorted(prepared["preflights"])[0]
                selector = (
                    select_owner_directed
                    if prepared["request"]["authority"]["mode"]
                    == "OWNER_DIRECT" else select_release
                )
                receipt = selector(
                    self.paths.release_root,
                    generation,
                    expected_current=selected,
                    operation_id=f"select-{transaction_id}",
                    admission_receipt=prepared["admission"],
                    environment_preflight_receipt=prepared["preflights"][first_actor],
                    admission_public_key=_protected_public_key(
                        self.paths.admission_public_key, trusted_uid=self.trusted_uid
                    ),
                    environment_public_key=_protected_public_key(
                        self.paths.environment_public_key, trusted_uid=self.trusted_uid
                    ),
                    current_plan_commit=prepared["request"]["plan"]["approved_commit"],
                    current_solution_hash=prepared["request"]["plan"]["approved_solution"],
                    current_time=_utc(self.now()),
                )
            return {"selection_receipt_sha256": _hash(receipt)}, True
        if action == "INSTALL_ACTOR_HOST":
            receipt = install_actor_host(
                f"host-{transaction_id}",
                paths=HostPaths(
                    current=self.paths.release_root / "current",
                    systemd_unit=self.paths.provider_unit,
                    tmpfiles_config=self.paths.provider_tmpfiles,
                    receipt_root=self.paths.host_receipt_root,
                    systemctl=self.paths.systemctl,
                    systemd_tmpfiles=self.paths.systemd_tmpfiles,
                ),
                runner=self._run_for(transaction_id),
                require_root=self.trusted_uid == 0,
            )
            self._checkpoint("MUTATION:INSTALL_ACTOR_HOST:host-bootstrap")
            return {"host_receipt_sha256": receipt["receipt_hash"]}, True
        if action == "INSTALL_STABLE_LAUNCHER":
            release = self._selected_release(prepared)
            source = release / "scripts/tgw_actor_startup.py"
            return self._install_candidate_file(
                target=self.paths.stable_launcher,
                body=source.read_bytes(),
                mode=0o555,
                expected_preimage=self._preimage(journal, "stable-launcher"),
            ), True
        if action == "INSTALL_DIRECT_STATUS":
            release = self._selected_release(prepared)
            source = release / "src/tgw/context_generation_status.py"
            executable = self._install_candidate_file(
                target=self.paths.status_executable,
                body=source.read_bytes(),
                mode=0o555,
                expected_preimage=self._preimage(journal, "status-executable"),
            )
            self._checkpoint("MUTATION:INSTALL_DIRECT_STATUS:executable")
            sudoers_source = release / "config/environment/sudoers/tgw-context-generation-status"
            sudoers = self._install_candidate_file(
                target=self.paths.status_sudoers,
                body=sudoers_source.read_bytes(),
                mode=0o440,
                expected_preimage=self._preimage(journal, "status-sudoers"),
            )
            self._checkpoint("MUTATION:INSTALL_DIRECT_STATUS:sudoers")
            self._required_command(
                transaction_id,
                ["/usr/sbin/visudo", "-cf", str(self.paths.status_sudoers)],
                "direct status sudoers validation",
            )
            return {
                "status_executable_sha256": executable["sha256"],
                "status_sudoers_sha256": sudoers["sha256"],
            }, True
        if action == "INSTALL_CONFIRMATION_RELAY":
            release = self._selected_release(prepared)
            source = release / "config/environment/systemd/tgw-context-confirmation-relay.service"
            receipt = self._install_candidate_file(
                target=self.paths.relay_unit,
                body=source.read_bytes(),
                mode=0o644,
                expected_preimage=self._preimage(journal, "relay-unit"),
            )
            self._checkpoint("MUTATION:INSTALL_CONFIRMATION_RELAY:unit")
            self._required_command(
                transaction_id, [str(self.paths.systemctl), "daemon-reload"], "systemd reload"
            )
            self._checkpoint("MUTATION:INSTALL_CONFIRMATION_RELAY:daemon-reload")
            self._required_command(
                transaction_id,
                [str(self.paths.systemctl), "enable", "tgw-context-confirmation-relay.service"],
                "confirmation relay enable",
            )
            self._checkpoint("MUTATION:INSTALL_CONFIRMATION_RELAY:enable")
            return receipt, True
        if action == "RESTART_PROVIDER":
            if not self._service_restart_landed(transaction_id, journal):
                self._required_command(
                    transaction_id,
                    [str(self.paths.systemctl), "restart", "tgw-actor-fleet-provider.service"],
                    "provider restart",
                )
                self._checkpoint("MUTATION:RESTART_PROVIDER:provider-restart")
            self._required_command(
                transaction_id,
                [str(self.paths.systemctl), "start", "tgw-context-confirmation-relay.service"],
                "confirmation relay start",
            )
            self._checkpoint("MUTATION:RESTART_PROVIDER:relay-start")
            attestation = self._attest_provider(transaction_id, prepared)
            attestation_path = Path(
                str(self._preimage(journal, "provider-attestation-receipt")["path"])
            )
            if attestation_path.exists() or attestation_path.is_symlink():
                if _read_json(attestation_path, "provider attestation") != attestation:
                    raise ContextUpdateCoordinatorError("provider attestation retry differs")
            else:
                _atomic_json(
                    attestation_path,
                    attestation,
                    mode=0o600,
                    uid=self.trusted_uid,
                    gid=self.trusted_gid,
                )
                self._checkpoint("MUTATION:RESTART_PROVIDER:attestation")
            return attestation, True
        provider = self._provider()
        provider_status = self._provider_journal_status(transaction_id)
        if action == "BIND_COORDINATOR":
            result = provider.invoke("bind-coordinator", [actor_request, binding])
            if result.get("status") != "COORDINATOR_BOUND":
                raise ContextUpdateCoordinatorError("provider coordinator binding differs")
            return result, True
        if action == "QUIESCE_ACTORS":
            result = provider.invoke("quiesce", [actor_request])
            if result.get("status") != "QUIESCED":
                raise ContextUpdateCoordinatorError("provider quiescence differs")
            return result, True
        if action == "REBUILD_ACTORS":
            if provider_status in {
                "REBUILT", "ACTIVATING", "MATERIALIZED", "STARTUP_BINDINGS_PLANNED",
                "ACTIVATED", "RESTARTED", "RESTART_REQUIRED", "HEALTHY", "VERIFYING", "VERIFIED",
            }:
                result = {
                    "status": "REBUILT",
                    "transaction_id": transaction_id,
                    "candidate_commit": actor_request["revisions"]["source"],
                }
            else:
                result = provider.invoke("rebuild", [actor_request])
            if result.get("status") != "REBUILT":
                raise ContextUpdateCoordinatorError("provider rebuild differs")
            return result, True
        if action == "ACTIVATE_ACTORS":
            if provider_status in {
                "ACTIVATED", "RESTARTED", "RESTART_REQUIRED", "HEALTHY", "VERIFYING", "VERIFIED",
            }:
                result = {
                    "status": "ACTIVATED", "transaction_id": transaction_id,
                    "generation": actor_request["successor_generation"],
                }
            else:
                rebuilt = {
                    "status": "REBUILT", "transaction_id": transaction_id,
                    "candidate_commit": actor_request["revisions"]["source"],
                }
                result = provider.invoke("activate", [actor_request, rebuilt])
            if result.get("status") != "ACTIVATED":
                raise ContextUpdateCoordinatorError("provider activation differs")
            return result, True
        if action == "VERIFY_COLD_CONTINUITY":
            if provider_status != "ACTIVATED":
                raise ContextUpdateCoordinatorError(
                    "cold continuity is not bound to activated actors"
                )
            receipt = self._cold_continuity(
                transaction_id, prepared, journal
            )
            return {
                "status": "PASS",
                "transaction_id": transaction_id,
                "cold_handoff_receipt_sha256": receipt["receipt_sha256"],
            }, True
        if action == "TRANSITION_DEEPSEEK_SERVICE":
            if provider_status not in {"ACTIVATED", "RESTART_REQUIRED"}:
                raise ContextUpdateCoordinatorError(
                    "DeepSeek transition is not bound to activated actors"
                )
            return self._deepseek_transition(
                transaction_id, prepared, journal
            ), True
        if action == "RESTART_ACTORS":
            if provider_status in {"RESTARTED", "HEALTHY", "VERIFYING", "VERIFIED"}:
                result = {
                    "status": "RESTARTED", "transaction_id": transaction_id,
                    "generation": actor_request["successor_generation"],
                }
            else:
                activated = {
                    "status": "ACTIVATED", "transaction_id": transaction_id,
                    "generation": actor_request["successor_generation"],
                }
                result = provider.invoke("restart", [activated])
            if result.get("status") == "RESTART_REQUIRED":
                return result, False
            if result.get("status") != "RESTARTED":
                raise ContextUpdateCoordinatorError("provider restart phase differs")
            return result, True
        if action == "HEALTH_ACTORS":
            if provider_status in {"HEALTHY", "VERIFYING", "VERIFIED"}:
                result = {"status": "HEALTHY", "transaction_id": transaction_id}
            else:
                result = provider.invoke(
                    "health", [{"status": "RESTARTED", "transaction_id": transaction_id}]
                )
            if result.get("status") == "RESTART_REQUIRED":
                return result, False
            if result.get("status") != "HEALTHY":
                raise ContextUpdateCoordinatorError("provider health phase differs")
            return result, True
        if action == "VERIFY_ACTORS":
            proofs = []
            actors = sorted(actor_request["actors"], key=lambda actor: (actor == "deepseek", actor))
            for actor in actors:
                result = provider.invoke("verify-actor", [actor, actor_request])
                if result.get("status") == "RESTART_REQUIRED":
                    return result, False
                if result.get("status") != "VERIFIED":
                    raise ContextUpdateCoordinatorError("provider actor verification differs")
                proofs.append(result)
            return {"status": "VERIFIED", "proofs_sha256": _hash(proofs)}, True
        if action == "FINALIZE_TRANSACTION":
            return self._finalize_transaction(
                transaction_id, prepared, journal, binding
            ), True
        raise ContextUpdateCoordinatorError("coordinator effect is not allowlisted")

    def _postimages_for_action(
        self, action: str, journal: Mapping[str, Any]
    ) -> dict[str, Any]:
        matching = [
            effect for effect in journal.get("effect_plan", {}).get("effects", [])
            if isinstance(effect, Mapping) and effect.get("action") == action
        ]
        if len(matching) != 1:
            raise ContextUpdateCoordinatorError("effect postimage plan differs")
        ids = {
            str(target["target_id"])
            for target in matching[0].get("targets", [])
            if isinstance(target, Mapping) and target.get("target_class") == "FILESYSTEM"
        }
        result: dict[str, Any] = {}
        for target_id in sorted(ids):
            before = self._preimage(journal, target_id)
            result[target_id] = _snapshot_node(
                Path(str(before["path"])),
                recursive=before.get("payload", {}).get("coverage") != "metadata-only",
                counter=[0],
            )
        return result

    def resume(self, transaction_id: str) -> dict[str, Any]:
        root, prepared, journal, binding, progress = self._load_transaction(transaction_id)
        if progress.get("status") == "COMPLETE":
            return {
                "schema": "tgw-context-update-result/v1",
                "status": "COMPLETE",
                "transaction_id": transaction_id,
                "binding_sha256": binding["binding_sha256"],
            }
        if progress.get("status") in {"ROLLING_BACK", "ROLLED_BACK"}:
            raise ContextUpdateCoordinatorError("transaction is not resumable")
        completed = {
            int(item["sequence"]): item
            for item in progress.get("completed_effects", [])
            if isinstance(item, Mapping) and isinstance(item.get("sequence"), int)
        }
        if len(completed) != len(progress.get("completed_effects", [])):
            raise ContextUpdateCoordinatorError("completed effect progress differs")
        expected_completed = list(range(1, len(completed) + 1))
        if sorted(completed) != expected_completed:
            raise ContextUpdateCoordinatorError("completed effect order differs")
        inflight = progress.get("inflight_sequence")
        if inflight is not None and (
            not isinstance(inflight, int)
            or inflight in completed
            or inflight != len(completed) + 1
        ):
            raise ContextUpdateCoordinatorError("inflight effect progress differs")
        for effect in journal["effect_plan"]["effects"]:
            sequence = int(effect["sequence"])
            action = str(effect["action"])
            if sequence in completed:
                continue
            progress.update(
                {
                    "status": "RUNNING",
                    "inflight_sequence": sequence,
                    "hold": None,
                }
            )
            progress = _write_progress(root / "progress.json", progress)
            if self.event_hook is not None:
                self.event_hook(f"FIRST_EFFECT:{sequence}:{action}")
            try:
                result, complete = self._apply_effect(
                    transaction_id=transaction_id,
                    action=action,
                    prepared=prepared,
                    journal=journal,
                    binding=binding,
                )
            except Exception as exc:
                progress.update(
                    {
                        "status": "HOLD",
                        "inflight_sequence": sequence,
                        "hold": {"sequence": sequence, "action": action, "reason": str(exc)},
                    }
                )
                progress = _write_progress(root / "progress.json", progress)
                append_coordinator_event(
                    paths=self.paths,
                    transaction_id=transaction_id,
                    record_role="COORDINATOR_FAILURE",
                    provider_status="HOLD",
                    journal_sha256=_hash(journal),
                    binding_sha256=binding["binding_sha256"],
                    evidence={
                        "sequence": sequence,
                        "action": action,
                        "failure_sha256": _hash(
                            {
                                "exception_type": type(exc).__name__,
                                "reason": str(exc),
                            }
                        ),
                    },
                    trusted_uid=self.trusted_uid,
                    now=self.now,
                )
                raise
            if not complete:
                progress.update(
                    {
                        "status": "WAIT_EXTERNAL",
                        "inflight_sequence": sequence,
                        "hold": {
                            "sequence": sequence,
                            "action": action,
                            "reason": "DECLARED_CONTEXT_REBIND_REQUIRED",
                            "provider_result_sha256": _hash(result),
                        },
                    }
                )
                _write_progress(root / "progress.json", progress)
                return {
                    "schema": "tgw-context-update-result/v1",
                    "status": "WAIT_EXTERNAL",
                    "transaction_id": transaction_id,
                    "pending_effect": action,
                    "provider_result": result,
                }
            if self.event_hook is not None:
                self.event_hook(f"EFFECT_APPLIED:{sequence}:{action}")
            postimages = dict(progress.get("postimages", {}))
            postimages.update(self._postimages_for_action(action, journal))
            row = {
                "sequence": sequence,
                "action": action,
                "result_sha256": _hash(result),
            }
            progress["completed_effects"] = [*progress.get("completed_effects", []), row]
            progress.update(
                {
                    "status": "COMPLETE" if action == "FINALIZE_TRANSACTION" else "RUNNING",
                    "inflight_sequence": None,
                    "postimages": postimages,
                    "hold": None,
                }
            )
            progress = _write_progress(root / "progress.json", progress)
        return {
            "schema": "tgw-context-update-result/v1",
            "status": "COMPLETE",
            "transaction_id": transaction_id,
            "binding_sha256": binding["binding_sha256"],
        }

    @staticmethod
    def _snapshot_without_identity(value: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: item for key, item in value.items()
            if key not in {"target_id", "path"}
        }

    def _restore_node(self, path: Path, value: Mapping[str, Any]) -> None:
        kind = value.get("kind")
        if kind == "absent":
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                for child in sorted(
                    path.iterdir(), key=lambda item: len(item.parts), reverse=True
                ):
                    self._remove_path(child)
                path.rmdir()
            return
        if kind == "file":
            payload = value.get("payload")
            if not isinstance(payload, Mapping) or payload.get("encoding") != "base64":
                raise ContextUpdateCoordinatorError("file rollback preimage differs")
            try:
                body = base64.b64decode(str(payload.get("content", "")), validate=True)
            except ValueError as exc:
                raise ContextUpdateCoordinatorError("file rollback preimage differs") from exc
            if path.exists() or path.is_symlink():
                self._remove_path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_bytes(
                path,
                body,
                mode=int(value["mode"]),
                uid=int(value["uid"]),
                gid=int(value["gid"]),
            )
            return
        if kind == "symlink":
            target = value.get("payload", {}).get("target")
            if not isinstance(target, str):
                raise ContextUpdateCoordinatorError("symlink rollback preimage differs")
            if path.exists() or path.is_symlink():
                self._remove_path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(target, path)
            if os.geteuid() == 0:
                os.lchown(path, int(value["uid"]), int(value["gid"]))
            _fsync_directory(path.parent)
            return
        if kind != "directory":
            raise ContextUpdateCoordinatorError("rollback preimage kind differs")
        payload = value.get("payload")
        if not isinstance(payload, Mapping) or payload.get("coverage") not in {
            "recursive", "metadata-only"
        }:
            raise ContextUpdateCoordinatorError("directory rollback preimage differs")
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_dir():
                self._remove_path(path)
        else:
            path.mkdir(parents=True, mode=0o700)
        if payload["coverage"] == "recursive":
            for child in list(path.iterdir()):
                self._remove_path(child)
            for entry in payload.get("entries", []):
                if not isinstance(entry, Mapping):
                    raise ContextUpdateCoordinatorError("nested rollback preimage differs")
                name = entry.get("relative_path")
                if not isinstance(name, str) or name in {"", ".", ".."} or "/" in name:
                    raise ContextUpdateCoordinatorError("nested rollback name differs")
                self._restore_node(path / name, entry)
        os.chmod(path, int(value["mode"]))
        if os.geteuid() == 0:
            os.chown(path, int(value["uid"]), int(value["gid"]))
        _fsync_directory(path)
        _fsync_directory(path.parent)

    def _remove_path(self, path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink()
            _fsync_directory(path.parent)
            return
        if path.is_dir():
            for child in path.iterdir():
                self._remove_path(child)
            path.rmdir()
            _fsync_directory(path.parent)
            return
        if path.exists():
            raise ContextUpdateCoordinatorError("special rollback target is refused")

    def _restore_preimage(
        self,
        preimage: Mapping[str, Any],
        postimage: Mapping[str, Any] | None,
    ) -> None:
        path = Path(str(preimage["path"]))
        recursive = preimage.get("payload", {}).get("coverage") != "metadata-only"
        observed = _snapshot_node(path, recursive=recursive, counter=[0])
        expected_before = self._snapshot_without_identity(preimage)
        if observed == expected_before:
            return
        if postimage is None or observed != dict(postimage):
            raise ContextUpdateCoordinatorError(
                f"rollback CAS differs for fixed target {preimage['target_id']}"
            )
        self._restore_node(path, expected_before)
        restored = _snapshot_node(path, recursive=recursive, counter=[0])
        if restored != expected_before:
            raise ContextUpdateCoordinatorError("rollback preimage restoration differs")

    def _restore_system_service_state(
        self,
        transaction_id: str,
        baseline: Mapping[str, Any],
    ) -> None:
        service = str(baseline["service"])
        expected = baseline.get("properties")
        if not isinstance(expected, Mapping):
            raise ContextUpdateCoordinatorError("service rollback preimage differs")
        observed = _service_preimage(
            self.paths, service, self._run_for(transaction_id)
        )["properties"]
        desired_enablement = expected.get("UnitFileState")
        if observed.get("UnitFileState") != desired_enablement:
            if desired_enablement in {"enabled", "enabled-runtime"}:
                command = [str(self.paths.systemctl), "enable"]
                if desired_enablement == "enabled-runtime":
                    command.append("--runtime")
                command.append(service)
            elif desired_enablement in {"disabled", "masked", "masked-runtime"}:
                verb = "disable" if desired_enablement == "disabled" else "mask"
                command = [str(self.paths.systemctl), verb]
                if desired_enablement == "masked-runtime":
                    command.append("--runtime")
                command.append(service)
            else:
                raise ContextUpdateCoordinatorError(
                    "service unit-file state was not restored by its preimage"
                )
            self._required_command(
                transaction_id, command, "service enablement rollback"
            )
        observed = _service_preimage(
            self.paths, service, self._run_for(transaction_id)
        )["properties"]
        desired_active = expected.get("ActiveState")
        if desired_active == "active" and observed.get("ActiveState") != "active":
            self._required_command(
                transaction_id,
                [str(self.paths.systemctl), "start", service],
                "service activity rollback",
            )
        elif desired_active != "active" and observed.get("ActiveState") == "active":
            self._required_command(
                transaction_id,
                [str(self.paths.systemctl), "stop", service],
                "service inactivity rollback",
            )
        final = _service_preimage(
            self.paths, service, self._run_for(transaction_id)
        )["properties"]
        if (
            final.get("UnitFileState") != desired_enablement
            or final.get("ActiveState") != desired_active
        ):
            raise ContextUpdateCoordinatorError("service rollback state differs")

    def rollback(self, transaction_id: str) -> dict[str, Any]:
        root, prepared, journal, binding, progress = self._load_transaction(transaction_id)
        if progress.get("status") == "ROLLED_BACK":
            return {
                "schema": "tgw-context-update-rollback-result/v1",
                "status": "ROLLED_BACK",
                "transaction_id": transaction_id,
            }
        completed_sequences = {
            int(item["sequence"]) for item in progress.get("completed_effects", [])
            if isinstance(item, Mapping) and isinstance(item.get("sequence"), int)
        }
        inflight_sequence = progress.get("inflight_sequence")
        if isinstance(inflight_sequence, int) and inflight_sequence not in completed_sequences:
            effect = next(
                (
                    item for item in journal["effect_plan"]["effects"]
                    if item["sequence"] == inflight_sequence
                ),
                None,
            )
            if not isinstance(effect, Mapping):
                raise ContextUpdateCoordinatorError("rollback inflight effect differs")
            try:
                settled, complete = self._apply_effect(
                    transaction_id=transaction_id,
                    action=str(effect["action"]),
                    prepared=prepared,
                    journal=journal,
                    binding=binding,
                )
            except Exception:
                if effect.get("action") != "TRANSITION_DEEPSEEK_SERVICE":
                    raise
                complete = False
                settled = {"status": "PARTIAL_DEEPSEEK_LIFECYCLE"}
            if complete:
                postimages = dict(progress.get("postimages", {}))
                postimages.update(
                    self._postimages_for_action(str(effect["action"]), journal)
                )
                progress["postimages"] = postimages
                progress["completed_effects"] = [
                    *progress.get("completed_effects", []),
                    {
                        "sequence": inflight_sequence,
                        "action": effect["action"],
                        "result_sha256": _hash(settled),
                    },
                ]
                completed_sequences.add(inflight_sequence)
        progress.update({"status": "ROLLING_BACK", "hold": None})
        progress = _write_progress(root / "progress.json", progress)
        provider_status = self._provider_journal_status(transaction_id)
        provider_mutated = provider_status in {
            "ACTIVATING", "MATERIALIZED", "STARTUP_BINDINGS_PLANNED",
            "ACTIVATED", "RESTARTED", "RESTART_REQUIRED", "HEALTHY",
            "VERIFYING", "VERIFIED", "ROLLBACK_REBIND_PLANNED",
            "ROLLBACK_RESTART_REQUIRED", "ROLLED_BACK",
        }
        if provider_mutated and provider_status != "ROLLED_BACK":
            result = self._provider().invoke("rollback", [prepared["actor_request"]])
            if result.get("status") == "RESTART_REQUIRED":
                progress.update(
                    {
                        "status": "ROLLING_BACK_WAIT_EXTERNAL",
                        "hold": {
                            "reason": "DECLARED_ROLLBACK_CONTEXT_REBIND_REQUIRED",
                            "provider_result_sha256": _hash(result),
                        },
                    }
                )
                _write_progress(root / "progress.json", progress)
                return {
                    "schema": "tgw-context-update-rollback-result/v1",
                    "status": "WAIT_EXTERNAL",
                    "transaction_id": transaction_id,
                    "provider_result": result,
                }
            if result.get("status") != "ROLLED_BACK":
                raise ContextUpdateCoordinatorError("provider rollback differs")

        inflight_action = next(
            (
                str(item["action"]) for item in journal["effect_plan"]["effects"]
                if item["sequence"] == inflight_sequence
            ),
            None,
        ) if isinstance(inflight_sequence, int) else None
        touched: list[str] = []
        retained_monotonic: list[str] = []
        touched_services: list[str] = []
        completed_service_ids: set[str] = set()
        restore_plan_ref = False
        for effect in reversed(journal["effect_plan"]["effects"]):
            if (
                int(effect["sequence"]) not in completed_sequences
                and effect.get("action") != inflight_action
            ):
                continue
            for target in reversed(effect["targets"]):
                if target["target_class"] == "FILESYSTEM":
                    identity = str(target["target_id"])
                    if target.get("rollback_disposition") == "RETAIN_MONOTONIC":
                        if identity not in retained_monotonic:
                            retained_monotonic.append(identity)
                    elif identity not in touched:
                        touched.append(identity)
                elif (
                    target["target_class"] == "SERVICE"
                    and target["target_id"] not in touched_services
                ):
                    touched_services.append(str(target["target_id"]))
                elif target["target_class"] == "PLAN_REF":
                    if (
                        target.get("target_id") != "approved-plan-ref"
                        or target.get("rollback_disposition")
                        != "RESTORE_COHERENT_PREDECESSOR"
                    ):
                        raise ContextUpdateCoordinatorError(
                            "Plan ref rollback disposition differs"
                        )
                    restore_plan_ref = True
                if (
                    target["target_class"] == "SERVICE"
                    and int(effect["sequence"]) in completed_sequences
                ):
                    completed_service_ids.add(str(target["target_id"]))

        service_before = {
            str(item["target_id"]): item for item in journal["service_preimages"]
        }
        for target_id in list(touched_services):
            if target_id in completed_service_ids:
                continue
            if target_id == "deepseek-user-service":
                progress_path = Path(
                    str(
                        self._preimage(journal, "deepseek-service-progress")["path"]
                    )
                )
                if not progress_path.exists() and not progress_path.is_symlink():
                    touched_services.remove(target_id)
                continue
            observed = _service_preimage(
                self.paths,
                str(service_before[target_id]["service"]),
                self._run_for(transaction_id),
            )
            if observed.get("properties") == service_before[target_id].get("properties"):
                touched_services.remove(target_id)

        deepseek_progress: dict[str, Any] | None = None
        if "deepseek-user-service" in touched_services:
            progress_path = Path(
                str(self._preimage(journal, "deepseek-service-progress")["path"])
            )
            deepseek_progress = _read_json(
                progress_path, "DeepSeek service progress"
            )
            _hashed_record(
                deepseek_progress,
                "progress_sha256",
                "DeepSeek service progress",
            )
            self._restore_deepseek_lifecycle(transaction_id, journal)
            if (
                deepseek_progress.get("linger_external_after_entry") is True
                and "deepseek-linger" in touched
            ):
                # A concurrent operator/logind-owned linger file is not this
                # transaction's postimage and is never removed by rollback.
                touched.remove("deepseek-linger")
        # Only coordinator/provider services actually touched by this transaction
        # are stopped. Ordinary harness parents are never named or signalled.
        for target_id in (
            "relay-service", "provider-service",
        ):
            if target_id not in touched_services:
                continue
            service = str(service_before[target_id]["service"])
            self._required_command(
                transaction_id,
                [str(self.paths.systemctl), "stop", service],
                "coordinator service stop for rollback",
                accepted={0, 5},
            )

        postimages = progress.get("postimages", {})
        preimages = {
            str(item["target_id"]): item for item in journal["preimages"]
        }
        for target_id in sorted(
            touched,
            key=lambda identity: len(Path(str(preimages[identity]["path"])).parts),
            reverse=True,
        ):
            self._restore_preimage(
                preimages[target_id],
                postimages.get(target_id) if isinstance(postimages, Mapping) else None,
            )

        plan_ref_restore = None
        if restore_plan_ref:
            plan_ref_restore = self._restore_approved_plan_ref(
                transaction_id, journal["plan_activation"]
            )

        self._required_command(
            transaction_id,
            [str(self.paths.systemctl), "daemon-reload"],
            "systemd rollback reload",
        )
        for target_id in touched_services:
            if target_id == "deepseek-user-service":
                continue
            self._restore_system_service_state(
                transaction_id, service_before[target_id]
            )

        body = {
            "schema": "tgw-context-update-rollback-receipt/v1",
            "status": "ROLLED_BACK",
            "transaction_id": transaction_id,
            "coordinator_journal_sha256": _hash(journal),
            "coordinator_binding_sha256": binding["binding_sha256"],
            "restored_target_ids": sorted(touched),
            "retained_monotonic_target_ids": sorted(retained_monotonic),
            "plan_ref_restore": plan_ref_restore,
        }
        receipt = {**body, "rollback_sha256": _hash(body)}
        _atomic_json(
            root / "rollback-receipt.json",
            receipt,
            mode=0o600,
            uid=self.trusted_uid,
            gid=self.trusted_gid,
        )
        rollback_ledger = append_coordinator_event(
            paths=self.paths,
            transaction_id=transaction_id,
            record_role="COORDINATOR_ROLLBACK",
            provider_status="ROLLED_BACK",
            journal_sha256=_hash(journal),
            binding_sha256=binding["binding_sha256"],
            evidence=receipt,
            trusted_uid=self.trusted_uid,
            now=self.now,
        )
        progress.update(
            {
                "status": "ROLLED_BACK",
                "inflight_sequence": None,
                "hold": None,
                "rollback_ledger_record_sha256": rollback_ledger[
                    "record_sha256"
                ],
            }
        )
        _write_progress(root / "progress.json", progress)
        return {
            "schema": "tgw-context-update-rollback-result/v1",
            "status": "ROLLED_BACK",
            "transaction_id": transaction_id,
            "rollback_sha256": receipt["rollback_sha256"],
            "rollback_ledger_record_sha256": rollback_ledger["record_sha256"],
        }

    def status(self, transaction_id: str) -> dict[str, Any]:
        _root, prepared, journal, binding, progress = self._load_transaction(transaction_id)
        return _transaction_status(
            transaction_id, prepared, journal, binding, progress
        )


def _cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tgw-context-update")
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser(
        "prepare",
        help="prepare one owner-directed update; read the directive from stdin",
    )
    prepare.add_argument("--transaction-id", required=True)
    prepare.add_argument(
        "--source-label",
        required=True,
        choices=sorted(_OWNER_DIRECT_SOURCE_LABELS),
    )

    for name in ("resume", "rollback"):
        command = commands.add_parser(name)
        command.add_argument("--transaction-id", required=True)

    status = commands.add_parser(
        "status",
        help="read and verify durable transaction progress without mutation",
    )
    status.add_argument("--transaction-id", required=True)
    status.add_argument("--json", action="store_true")
    return parser


def _owner_directive_from_stream(stream: Any) -> str:
    raw = stream.read(_MAX_OWNER_DIRECTIVE + 1)
    if isinstance(raw, str):
        try:
            encoded = raw.encode("utf-8")
        except UnicodeError as exc:
            raise ContextUpdateCoordinatorError(
                "owner directive is not valid UTF-8"
            ) from exc
    elif isinstance(raw, bytes):
        encoded = raw
    else:
        raise ContextUpdateCoordinatorError("owner directive stream is invalid")
    if not 1 <= len(encoded) <= _MAX_OWNER_DIRECTIVE:
        raise ContextUpdateCoordinatorError("owner directive length differs")
    try:
        directive = encoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContextUpdateCoordinatorError(
            "owner directive is not valid UTF-8"
        ) from exc
    if "\x00" in directive:
        raise ContextUpdateCoordinatorError("owner directive is invalid")
    return directive


def _transaction_status_line(value: Mapping[str, Any]) -> str:
    return (
        "TGW Context update: "
        f"tx={value['transaction_id']} status={value['status']} "
        f"effects={value['completed_effects']}/{value['total_effects']} "
        f"candidate={str(value['candidate_commit'])[:12]} "
        f"generation={str(value['actor_generation']).removeprefix('sha256:')[:12]}"
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    input_stream: Any | None = None,
    output_stream: Any | None = None,
    error_stream: Any | None = None,
) -> int:
    """Expose only the fixed coordinator transaction lifecycle."""
    args = _cli_parser().parse_args(argv)
    stdin = input_stream if input_stream is not None else sys.stdin.buffer
    stdout = output_stream if output_stream is not None else sys.stdout
    stderr = error_stream if error_stream is not None else sys.stderr
    try:
        if args.command == "status":
            result = read_transaction_status(args.transaction_id)
            print(
                json.dumps(result, sort_keys=True)
                if args.json
                else _transaction_status_line(result),
                file=stdout,
            )
            return 0

        if args.command == "prepare":
            directive = _owner_directive_from_stream(stdin)
            coordinator = RootContextUpdateCoordinator()
            result = coordinator.prepare(
                {
                    "schema": "tgw-context-root-update-request/v2",
                    "transaction_id": args.transaction_id,
                    "authority": {
                        "schema": "tgw-context-update-authority/v1",
                        "mode": "OWNER_DIRECT",
                        "instruction_utf8": directive,
                        "source_label": args.source_label,
                    },
                }
            )
        elif args.command == "resume":
            coordinator = RootContextUpdateCoordinator()
            result = coordinator.resume(args.transaction_id)
        elif args.command == "rollback":
            coordinator = RootContextUpdateCoordinator()
            result = coordinator.rollback(args.transaction_id)
        else:  # argparse makes this unreachable; keep the mutation surface closed.
            raise ContextUpdateCoordinatorError(
                "Context update command is not allowlisted"
            )
        print(json.dumps(result, sort_keys=True), file=stdout)
        return 3 if result.get("status") == "WAIT_EXTERNAL" else 0
    except (
        ContextUpdateCoordinatorError,
        KeyError,
        OSError,
        TypeError,
        UnicodeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(
            json.dumps(
                {
                    "schema": "tgw-context-update-cli-result/v1",
                    "status": "HOLD",
                    "error": str(exc),
                },
                sort_keys=True,
            ),
            file=stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
