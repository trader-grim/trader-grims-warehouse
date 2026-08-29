"""Operator-facing diagnosis and bounded repair for the local TGW substrate.

``tgw doctor`` is deliberately independent of tgw-prod and provider effects.  It
compares the local machine with the identities already declared by the current
task, context snapshot, coding configuration, and immutable runtime.  Repair mode
may only restore those declared bytes and identities; it cannot invent Plan intent,
delete work, change application data, or widen an actor's authority.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import errno
import fcntl
import grp
import hashlib
import importlib.util
import json
import os
import pwd
import re
import secrets
import selectors
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from tgw.protected_git import (
    GIT_EXECUTABLE,
    protected_git_command,
    protected_git_environment,
    read_exact_tree_file,
)


def _postgres_driver() -> tuple[Any, Any]:
    """Load database support only for Doctor operations that actually need it."""

    try:
        import psycopg2
        import psycopg2.extras
    except ModuleNotFoundError as exc:
        raise DoctorError("Doctor database support is unavailable") from exc
    return psycopg2, psycopg2.extras

_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_INVOCATION_ID = re.compile(r"[0-9a-f]{32}\Z")
_LOOSE_OBJECT_DIRECTORY = re.compile(r"[0-9a-f]{2}\Z")
_LOOSE_OBJECT_NAME = re.compile(r"[0-9a-f]{38}\Z")
_STATES = {"PASS", "WARN", "FAIL", "UNKNOWN", "RESTART_REQUIRED"}
_CONTEXT_COLD_PROBE_BUDGET_SECONDS = 30.0
_CONTEXT_COLD_PROBE_STREAM_LIMIT = 1_048_576
_CONTEXT_COLD_PROBE_TERMINATE_GRACE_SECONDS = 0.25
_CONTEXT_COLD_PROBE_LOCK = threading.RLock()
_SERVICE_RUNTIME_PROBE_ATTEMPTS = 2
_PR_SET_CHILD_SUBREAPER = 36
_PR_GET_CHILD_SUBREAPER = 37
_CODING_RUNTIME_GROUP = "tgw-coders"
_CODING_SUPPORT_ROOT_KEYS = (
    "preservation_archive_root",
    "runner_state_root",
    "lifecycle_root",
    "root_effect_root",
)
_CONTEXT_SNAPSHOT_MODE = 0o444
_CONTEXT_PREFLIGHT_SNAPSHOT_MODE = 0o400
_FORBIDDEN_CODING_DEPENDENCIES = (
    "tgw-prod",
    "ssh",
    "sudo",
    "api_endpoint",
    "worker_api_endpoint",
    "execution_card",
    "actor_fleet",
)
_ACTIVE_CODING_UNITS = (
    "tgw-codex-implement-worker.service",
    "tgw-claude-review-worker.service",
    "tgw-controller-verify-worker.service",
    "tgw-coding-lifecycle-supervisor.service",
    "tgw-coding-root-effect.service",
    "tgw-coding-runtime-restart.path",
    "tgw-context-snapshot-promote.path",
    "tgw-coding-local-foreman.timer",
)
_CODING_UNITS = (
    *_ACTIVE_CODING_UNITS,
    "tgw-coding-local-foreman.service",
    "tgw-coding-runtime-restart.service",
    "tgw-context-snapshot-promote.service",
)
_PLAN_RENDER_UNIT = "tgw-plan-render-local.service"
_PLAN_RENDER_DIRECTORY_MODE = 0o2770
_SYSTEMD_UNIT_ROOTS = (
    Path("/etc/systemd/system"),
    Path("/run/systemd/system"),
    Path("/usr/local/lib/systemd/system"),
    Path("/usr/lib/systemd/system"),
    Path("/lib/systemd/system"),
)
_ARCHIVE_DISCOVERY_ROOTS = (
    Path("/opt/TGW"),
    Path("/home"),
    Path("/srv"),
    Path("/var/local"),
    Path("/var/lib"),
    Path("/mnt"),
    Path("/media"),
)
_ARCHIVE_DISCOVERY_MAX_DEPTH = 5
_ARCHIVE_DISCOVERY_PRUNE = {
    ".cache",
    ".git",
    ".local",
    ".npm",
    ".rustup",
    "__pycache__",
    "node_modules",
    "proc",
}
_LOCAL_WORKFLOW_ARGV = (
    "/opt/TGW/.venvs/controller/bin/python3",
    "-m",
    "tgw.development.local_workflow",
    "--config",
    "/opt/TGW/tgw-lib/config/tgw-coding-local.json",
)
_UNIT_ARGV = {
    "tgw-codex-implement-worker.service": (
        *_LOCAL_WORKFLOW_ARGV,
        "worker",
        "--queue",
        "codex-implement",
    ),
    "tgw-controller-verify-worker.service": (
        *_LOCAL_WORKFLOW_ARGV,
        "worker",
        "--queue",
        "controller-verify",
    ),
    "tgw-claude-review-worker.service": (
        *_LOCAL_WORKFLOW_ARGV,
        "worker",
        "--queue",
        "claude-review",
    ),
    "tgw-coding-lifecycle-supervisor.service": (
        "/opt/TGW/.venvs/controller/bin/python3",
        "-m",
        "tgw.development.coding_lifecycle",
        "--config",
        "/opt/TGW/tgw-lib/config/tgw-coding-local.json",
        "--managed",
    ),
    "tgw-coding-root-effect.service": (
        "/opt/TGW/.venvs/controller/bin/python3",
        "-m",
        "tgw.development.coding_root_effect",
        "--config",
        "/opt/TGW/tgw-lib/config/tgw-coding-local.json",
    ),
    "tgw-coding-local-foreman.service": (*_LOCAL_WORKFLOW_ARGV, "foreman"),
}
_UNIT_ARGV[_PLAN_RENDER_UNIT] = (
    "/opt/TGW/.venvs/controller/bin/python3",
    "-m",
    "tgw.workers.plan_render",
    "--config",
    "/opt/TGW/tgw-lib/config/tgw-plan-render-local.json",
)


class DoctorError(RuntimeError):
    """The requested diagnosis or repair cannot be performed safely."""


class _ReceiptPublicationAmbiguous(DoctorError):
    """Receipt bytes may be durable; callers must preserve committed state."""

    def __init__(self, path: Path, detail: str) -> None:
        super().__init__(detail)
        self.path = path


def _preserve_primary_with_cleanup_failure(
    primary: Exception,
    cleanup: Exception,
    *,
    resource: str,
) -> Exception:
    """Annotate the original failure so durable reporters retain both causes."""
    detail = f"{resource} cleanup failed: {cleanup}"
    primary.args = (
        f"{primary}; {detail}",
        *primary.args[1:],
    )
    primary.add_note(detail)
    setattr(primary, "cleanup_failures", (detail,))
    return primary


@dataclass(frozen=True)
class DoctorPaths:
    repository: Path = Path("/opt/TGW/tgw-lib/src/trader-grims-warehouse")
    plan_repository: Path = Path("/opt/TGW/library/plans")
    worktrees: Path = Path("/opt/TGW/var/worktrees")
    coding_config: Path = Path("/opt/TGW/tgw-lib/config/tgw-coding-local.json")
    plan_render_config: Path = Path("/opt/TGW/tgw-lib/config/tgw-plan-render-local.json")
    plan_render_root: Path = Path("/opt/TGW/var/plan-render")
    plan_render_log_root: Path = Path("/opt/TGW/var/plan-render/log")
    postgresql_conf_dir: Path = Path("/etc/postgresql/17/main")
    runtime_root: Path = Path("/opt/TGW/tgw-lib/coding-runtime")
    local_bin: Path = Path("/opt/TGW/tgw-lib/bin")
    operator_cli: Path = Path("/usr/local/bin/tgw")
    coding_bootstrap: Path = Path("/usr/local/sbin/tgw-coding-bootstrap")
    context_snapshot: Path = Path("/opt/TGW/tgw-lib/config/tgw-context-current.json")
    context_task: Path = Path("/opt/TGW/tgw-lib/context-input/current-task.json")
    context_cursor: Path = Path("/opt/TGW/tgw-lib/context-input/plan-cycle-cursor.json")
    context_launcher: Path = Path("/opt/TGW/tgw-lib/bin/tgw-context-mcp")
    context_publisher: Path = Path("/opt/TGW/tgw-lib/bin/tgw-context-publish")
    context_generation_root: Path = Path("/opt/TGW/tgw-lib/context-entrypoints/generations")
    context_generation_pointer: Path = Path("/opt/TGW/tgw-lib/context-entrypoints/current")
    context_runtime_source: Path = Path("/opt/TGW/tgw-lib/context-runtime/src")
    context_catalog: Path = Path("/opt/TGW/tgw-lib/config/tgw-context-debian-v1.json")
    receipts: Path = Path("/opt/TGW/tgw-lib/doctor-receipts")
    cleanup_archive_root: Path = Path("/opt/TGW/tgw-lib/archive/doctor-cleanup")
    cleanup_system_bin: Path = Path("/usr/local/bin")
    cleanup_actor_home: Path = Path("/home/codex")
    cleanup_reference_roots: tuple[Path, ...] = (
        Path("/etc/systemd/system"),
        Path("/run/systemd/system"),
        Path("/usr/local/lib/systemd/system"),
        Path("/opt/TGW/tgw-lib/config"),
    )
    trusted_release_owners: tuple[int, ...] = (0, 65534)
    systemd_install_root: Path = Path("/etc/systemd/system")
    systemd_runtime_root: Path = Path("/run/systemd/system")
    quiescence_root: Path = Path("/run/tgw-doctor")
    systemd_unit_uid: int = 0
    systemd_unit_gid: int = 0
    systemd_unit_mode: int = 0o444
    context_install_uid: int = 0
    context_install_gid: int = 0
    context_launcher_mode: int = 0o555
    coding_root_effect_uid: int | None = None
    systemd_unit_roots: tuple[Path, ...] = _SYSTEMD_UNIT_ROOTS
    archive_discovery_roots: tuple[Path, ...] = _ARCHIVE_DISCOVERY_ROOTS
    archive_discovery_max_depth: int = _ARCHIVE_DISCOVERY_MAX_DEPTH


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _surface_snapshot(path: Path) -> dict[str, Any]:
    """Capture bytes from one pinned inode and reject pathname ABA."""
    parent_descriptor = -1
    target_descriptor = -1
    data_descriptor = -1
    try:
        if not path.name:
            raise DoctorError(f"surface path has no final component: {path}")
        parent_descriptor = os.open(
            path.parent,
            os.O_RDONLY
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
            | os.O_CLOEXEC,
        )
        parent_before = os.fstat(parent_descriptor)
        try:
            target_descriptor = os.open(
                path.name,
                os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent_descriptor,
            )
        except FileNotFoundError:
            try:
                os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                parent_after = os.fstat(parent_descriptor)
                visible_parent = path.parent.stat(follow_symlinks=False)
                if (
                    parent_before.st_dev,
                    parent_before.st_ino,
                ) != (
                    parent_after.st_dev,
                    parent_after.st_ino,
                ) or (
                    parent_after.st_dev,
                    parent_after.st_ino,
                ) != (
                    visible_parent.st_dev,
                    visible_parent.st_ino,
                ):
                    raise DoctorError(
                        f"surface parent changed during inspection: {path.parent}"
                    )
                return {"kind": "missing"}
            raise DoctorError(f"surface appeared during inspection: {path}")

        before = os.fstat(target_descriptor)
        common = {
            "uid": before.st_uid,
            "gid": before.st_gid,
            "mode": stat.S_IMODE(before.st_mode),
            "device": before.st_dev,
            "inode": before.st_ino,
            "size": before.st_size,
            "nlink": before.st_nlink,
            "mtime_ns": before.st_mtime_ns,
            "ctime_ns": before.st_ctime_ns,
        }
        if stat.S_ISLNK(before.st_mode):
            target = os.readlink("", dir_fd=target_descriptor)
            result = {"kind": "symlink", "target": target, **common}
        elif stat.S_ISREG(before.st_mode):
            data_descriptor = os.open(
                f"/proc/self/fd/{target_descriptor}",
                os.O_RDONLY | os.O_CLOEXEC,
            )
            opened = os.fstat(data_descriptor)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise DoctorError(f"surface descriptor identity differs: {path}")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(data_descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            raw = b"".join(chunks)
            result = {
                "kind": "file",
                "raw": raw,
                "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
                **common,
            }
        else:
            raise DoctorError(f"surface has an unsupported type: {path}")

        after = os.fstat(target_descriptor)
        visible = os.stat(
            path.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        fields = (
            "st_dev",
            "st_ino",
            "st_size",
            "st_nlink",
            "st_mtime_ns",
            "st_ctime_ns",
            "st_mode",
            "st_uid",
            "st_gid",
        )
        if any(
            getattr(before, field) != getattr(after, field)
            or getattr(after, field) != getattr(visible, field)
            for field in fields
        ):
            raise DoctorError(f"surface changed during inspection: {path}")
        parent_after = os.fstat(parent_descriptor)
        visible_parent = path.parent.stat(follow_symlinks=False)
        if (
            parent_before.st_dev,
            parent_before.st_ino,
        ) != (
            parent_after.st_dev,
            parent_after.st_ino,
        ) or (
            parent_after.st_dev,
            parent_after.st_ino,
        ) != (
            visible_parent.st_dev,
            visible_parent.st_ino,
        ):
            raise DoctorError(
                f"surface parent changed during inspection: {path.parent}"
            )
        return result
    finally:
        if data_descriptor >= 0:
            os.close(data_descriptor)
        if target_descriptor >= 0:
            os.close(target_descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def _surface_record(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "raw"}


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DoctorError(f"cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DoctorError(f"JSON root must be an object: {path}")
    return value


def _run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int = 15,
    env: Mapping[str, str] | None = None,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        env={**os.environ, **env} if env is not None else None,
        check=False,
        capture_output=True,
        text=True,
        input=input,
        timeout=timeout,
    )


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        protected_git_command(repository, *args),
        cwd=repository,
        env=dict(protected_git_environment()),
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode:
        raise DoctorError(result.stderr.strip() or "Git command failed")
    return result.stdout.strip()


def _check(
    identity: str,
    state: str,
    detail: str,
    *,
    evidence: Mapping[str, Any] | None = None,
    repair: str | None = None,
) -> dict[str, Any]:
    if state not in _STATES:
        raise ValueError(f"invalid doctor check state: {state}")
    result: dict[str, Any] = {
        "id": identity,
        "state": state,
        "detail": detail,
        "repairable": repair is not None,
    }
    if evidence:
        result["evidence"] = dict(evidence)
    if repair:
        result["operator_action"] = repair
    return result


def _failed(identity: str, exc: Exception, *, repair: str | None = None) -> dict[str, Any]:
    return _check(identity, "FAIL", str(exc), repair=repair)


def _source_identity(paths: DoctorPaths) -> tuple[str, str, str]:
    head = _git(paths.repository, "rev-parse", "HEAD^{commit}")
    tree = _git(paths.repository, "rev-parse", "HEAD^{tree}")
    status_command = protected_git_command(paths.repository, "status", "--short")
    status_environment = dict(protected_git_environment())
    # getresuid is deliberately not a test/actor projection: only a genuinely
    # privileged process may cross into the ordinary db account.
    if os.geteuid() == 0 and os.getresuid()[1] == 0:
        status_command = [
            "/usr/sbin/runuser",
            "-u",
            "db",
            "-g",
            _CODING_RUNTIME_GROUP,
            "--",
            "/usr/bin/env",
            "-i",
            *(f"{key}={value}" for key, value in status_environment.items()),
            *status_command,
        ]
        status_environment = None
    observed = subprocess.run(
        status_command,
        cwd=paths.repository,
        env=status_environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if observed.returncode:
        raise DoctorError(
            observed.stderr.strip() or "ordinary-user source status failed"
        )
    status = observed.stdout.strip()
    if _COMMIT.fullmatch(head) is None or _COMMIT.fullmatch(tree) is None:
        raise DoctorError("canonical source has an invalid Git identity")
    return head, tree, status


def _privileged_repair_action(paths: DoctorPaths, target: str) -> str:
    """Return the exact root-owned repair path, never the mutable tgw launcher."""
    try:
        head, _tree, status = _source_identity(paths)
    except Exception:
        return "restore the exact clean canonical source before privileged repair"
    if status:
        return "restore the exact clean canonical source before privileged repair"
    return (
        "sudo -n /usr/local/sbin/tgw-coding-bootstrap "
        f"--commit {head} --repair {target}"
    )


def check_host(paths: DoctorPaths) -> dict[str, Any]:
    host = socket.gethostname().split(".", 1)[0]
    state = "PASS" if host == "tgw-lib" else "FAIL"
    return _check(
        "host.boundary",
        state,
        f"local development host is {host}",
        evidence={"host": host, "production_host": "tgw-prod", "production_dependency": False},
    )


def check_source(paths: DoctorPaths) -> dict[str, Any]:
    try:
        head, tree, status = _source_identity(paths)
        return _check(
            "source.canonical",
            "PASS" if not status else "WARN",
            f"canonical source {head[:12]} is {'clean' if not status else 'dirty'}",
            evidence={
                "repository": str(paths.repository),
                "commit": head,
                "tree": tree,
                "working_tree_clean": not status,
                "status": status.splitlines(),
            },
        )
    except Exception as exc:
        return _failed("source.canonical", exc)


def _validate_snapshot(
    value: Mapping[str, Any], raw: bytes | None = None, *, parser_path: Path | None = None
) -> dict[str, Any]:
    if parser_path is None:
        from tgw.current_context_snapshot import CurrentContextError, parse_bytes
    else:
        spec = importlib.util.spec_from_file_location(
            "_tgw_doctor_selected_context_parser", parser_path
        )
        if spec is None or spec.loader is None:
            raise DoctorError("selected immutable Context parser cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        CurrentContextError = module.CurrentContextError
        has_parse_bytes = hasattr(module, "parse_bytes")
        has_maximum = hasattr(module, "MAX_SNAPSHOT_BYTES")
        selected_parse_bytes = getattr(module, "parse_bytes", None)
        if has_parse_bytes and has_maximum and callable(selected_parse_bytes):
            if module.MAX_SNAPSHOT_BYTES != 256 * 1024:
                raise DoctorError("Context launcher and selected runtime size bounds differ")
            parse_bytes = selected_parse_bytes
        else:
            legacy_parse = getattr(module, "parse", None)
            if has_parse_bytes or has_maximum or not callable(legacy_parse):
                raise DoctorError("selected Context runtime parser API is invalid")

            def parse_bytes(selected_raw: bytes) -> dict[str, Any]:
                if not isinstance(selected_raw, bytes) or len(selected_raw) > 256 * 1024:
                    raise CurrentContextError(
                        "current context snapshot wire format is invalid"
                    )
                try:
                    selected_value = json.loads(
                        selected_raw.decode("utf-8", errors="strict")
                    )
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise CurrentContextError(
                        "current context snapshot is invalid"
                    ) from exc
                canonical = json.dumps(
                    selected_value,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8") + b"\n"
                if selected_raw != canonical:
                    raise CurrentContextError(
                        "current context snapshot wire format is invalid"
                    )
                return legacy_parse(selected_value)

    try:
        if raw is None:
            raise DoctorError("published context raw bytes are missing")
        expanded = parse_bytes(raw)
    except CurrentContextError as exc:
        raise DoctorError(str(exc)) from exc
    task = expanded.get("task")
    cursor = value.get("cursor")
    if not isinstance(task, Mapping) or not isinstance(cursor, Mapping):
        raise DoctorError("published context task/cursor is missing")
    development = task.get("implementation", {}).get("development_source", {})
    if (
        expanded.get("source_commit") != development.get("commit")
        or expanded.get("source_commit") != cursor.get("source_commit")
        or expanded.get("source_tree") != cursor.get("source_tree")
        or expanded.get("plan_commit") != task.get("plan", {}).get("approved_commit")
        or expanded.get("plan_commit") != cursor.get("plan_commit")
    ):
        raise DoctorError("published task, cursor, Plan, and source identities diverge")
    return expanded


def _plan_repository_evidence_identity(
    paths: DoctorPaths, plan: Mapping[str, Any]
) -> tuple[str, str]:
    """Return a stable clean Plan head descended from the exact approved Plan."""

    approved = plan.get("approved_commit")
    approved_tree = plan.get("approved_tree")
    solution = plan.get("approved_solution_hash")
    if (
        not isinstance(approved, str)
        or _COMMIT.fullmatch(approved) is None
        or not isinstance(approved_tree, str)
        or _COMMIT.fullmatch(approved_tree) is None
        or not isinstance(solution, str)
        or _SHA256.fullmatch(solution) is None
        or plan.get("evidence_descends_from_approved") is not True
    ):
        raise DoctorError("current task approved Plan binding is malformed")
    render_config_surface = _surface_snapshot(paths.plan_render_config)
    if render_config_surface.get("kind") != "file":
        raise DoctorError("installed Plan renderer binding is unavailable")
    render_config = _json_from_surface(
        paths.plan_render_config,
        render_config_surface,
    )
    if (
        render_config.get("schema") != "tgw-local-plan-render/v1"
        or render_config.get("plan_repository_root")
        != str(paths.plan_repository)
        or render_config.get("plan_approved_commit") != approved
        or render_config.get("plan_approved_solution_hash") != solution
    ):
        raise DoctorError(
            "current task approved Plan/solution differs from the installed renderer"
        )
    repository = paths.plan_repository.resolve(strict=True)
    if _git(repository, "status", "--porcelain"):
        raise DoctorError("current Plan evidence repository is dirty")
    if _git(repository, "rev-parse", f"{approved}^{{tree}}") != approved_tree:
        raise DoctorError("current task approved Plan tree is stale")
    head = _git(repository, "rev-parse", "HEAD^{commit}")
    tree = _git(repository, "rev-parse", "HEAD^{tree}")
    ancestor = _run(
        protected_git_command(
            repository, "merge-base", "--is-ancestor", approved, head
        ),
        cwd=repository,
        env=protected_git_environment(),
    )
    if ancestor.returncode != 0:
        raise DoctorError("current Plan evidence does not descend from the approved Plan")
    if (
        _git(repository, "status", "--porcelain")
        or _git(repository, "rev-parse", "HEAD^{commit}") != head
        or _git(repository, "rev-parse", "HEAD^{tree}") != tree
    ):
        raise DoctorError("current Plan evidence changed during inspection")
    return head, tree


def _plan_evidence_identity(
    paths: DoctorPaths, task: Mapping[str, Any]
) -> tuple[str, str]:
    """Return the exact current Plan evidence projected by this task.

    The approved Plan remains the immutable intent binding.  The evidence head
    is only the current documentation/reconciliation projection, but silently
    retaining an older evidence head was one source of stale MCP orientation.
    """

    plan = task.get("plan")
    if not isinstance(plan, Mapping):
        raise DoctorError("current task Plan projection is malformed")
    evidence_commit = plan.get("evidence_commit")
    evidence_tree = plan.get("evidence_tree")
    if (
        not isinstance(evidence_commit, str)
        or _COMMIT.fullmatch(evidence_commit) is None
        or not isinstance(evidence_tree, str)
        or _COMMIT.fullmatch(evidence_tree) is None
    ):
        raise DoctorError("current task Plan evidence identity is malformed")
    head, tree = _plan_repository_evidence_identity(paths, plan)
    if (evidence_commit, evidence_tree) != (head, tree):
        raise DoctorError("current task Plan evidence projection is stale")
    return head, tree


def _require_projection_pair(
    label: str,
    projection: Mapping[str, Any],
    first_key: str,
    second_key: str,
    expected: tuple[Any, Any],
) -> None:
    observed = (projection.get(first_key), projection.get(second_key))
    if observed != expected:
        raise DoctorError(
            f"current task {label} projection is stale: "
            f"{first_key}/{second_key} {observed!r} != {expected!r}"
        )


def _required_projection(
    parent: Mapping[str, Any], key: str, label: str
) -> Mapping[str, Any]:
    value = parent.get(key)
    if not isinstance(value, Mapping):
        raise DoctorError(f"current task {label} projection is missing or malformed")
    return value


def _require_git_commit_tree(
    repository: Path,
    *,
    commit: Any,
    tree: Any,
    label: str,
) -> None:
    if (
        not isinstance(commit, str)
        or _COMMIT.fullmatch(commit) is None
        or not isinstance(tree, str)
        or _COMMIT.fullmatch(tree) is None
    ):
        raise DoctorError(f"current task {label} Git identity is malformed")
    try:
        observed_tree = _git(repository, "rev-parse", f"{commit}^{{tree}}")
    except DoctorError as exc:
        raise DoctorError(
            f"current task {label} commit is unavailable in its repository"
        ) from exc
    if observed_tree != tree:
        raise DoctorError(f"current task {label} commit/tree binding is stale")


def _review_surface_binding(
    path_value: Any,
    hash_value: Any,
    *,
    label: str,
) -> dict[str, Any]:
    if (
        not isinstance(path_value, str)
        or not path_value
        or not Path(path_value).is_absolute()
        or not isinstance(hash_value, str)
        or _SHA256.fullmatch(hash_value) is None
    ):
        raise DoctorError(f"current task {label} review evidence is malformed")
    try:
        surface = _surface_snapshot(Path(path_value))
    except OSError as exc:
        raise DoctorError(
            f"current task {label} review evidence is unreadable by the "
            f"invoking actor: {path_value} ({exc}); root verification required"
        ) from exc
    if (
        surface.get("kind") != "file"
        or surface.get("nlink") != 1
        or surface.get("sha256") != hash_value
    ):
        raise DoctorError(f"current task {label} review evidence is unavailable or stale")
    return {
        "path": str(path_value),
        "sha256": str(hash_value),
        "surface": surface,
        "label": label,
    }


def _require_review_file(
    path_value: Any,
    hash_value: Any,
    *,
    label: str,
) -> None:
    _review_surface_binding(path_value, hash_value, label=label)


def _context_projection_review_bindings(
    task: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Bind every diagnostic review surface referenced by one projection."""

    implementation = _required_projection(
        task, "implementation", "implementation"
    )
    development = _required_projection(
        implementation, "development_source", "development source"
    )
    tracks = _required_projection(task, "tracks", "tracks")
    coding = _required_projection(tracks, "coding_lifecycle", "coding lifecycle")
    item = _required_projection(tracks, "item_workflow", "item workflow")
    plan = _required_projection(task, "plan", "Plan")
    todo_1916 = _required_projection(plan, "todo_1916", "Todo 1916 Plan")
    bindings: list[dict[str, Any]] = []
    if coding.get("review_state") != _CONTEXT_TRANSITION_REVIEW_PENDING_STATE:
        bindings.append(
            _review_surface_binding(
                development.get("review"),
                development.get("review_sha256"),
                label="coding review",
            )
        )
    bindings.extend(
        (
            _review_surface_binding(
                item.get("review_path"),
                item.get("review_sha256"),
                label="item workflow review",
            ),
            _review_surface_binding(
                todo_1916.get("review_path"),
                todo_1916.get("review_sha256"),
                label="Todo 1916 Plan review",
            ),
        )
    )
    return tuple(bindings)


def _top_level_review_lines(text: str) -> list[str | None]:
    """Return root Markdown lines, using ``None`` for quoted boundaries."""

    eligible: list[str | None] = []
    fence_character: str | None = None
    fence_length = 0
    lazy_blockquote = False
    html_comment = False
    html_container: str | None = None
    html_block_until_blank = False
    html_block_pattern = (
        r"(address|article|aside|base|basefont|blockquote|body|caption|center|code|"
        r"col|colgroup|dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|"
        r"footer|form|frame|frameset|h[1-6]|head|header|hr|html|iframe|legend|li|"
        r"link|main|menu|menuitem|nav|noframes|ol|optgroup|option|p|param|search|"
        r"section|summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul)"
    )

    def comment_remains_open(value: str, *, initially_open: bool) -> bool:
        source = ("<!--" if initially_open else "") + value
        position = 0
        open_comment = False
        while True:
            if not open_comment:
                opening = source.find("<!--", position)
                if opening < 0:
                    return False
                open_comment = True
                position = opening + 4
            closing = source.find("-->", position)
            if closing < 0:
                return True
            open_comment = False
            position = closing + 3

    for line in text.splitlines():
        stripped = line.strip()
        if fence_character is not None:
            closing = re.match(
                rf"^ {{0,3}}{re.escape(fence_character)}"
                rf"{{{fence_length},}}[ \t]*$",
                line,
            )
            if closing is not None:
                fence_character = None
                fence_length = 0
            eligible.append(None)
            continue
        if html_comment:
            html_comment = comment_remains_open(line, initially_open=True)
            eligible.append(None)
            continue
        if html_block_until_blank:
            if not stripped:
                html_block_until_blank = False
            eligible.append(None)
            continue
        if html_container is not None:
            closing = re.search(rf"</{html_container}\s*>", line, re.IGNORECASE)
            if closing is not None:
                html_container = None
                remainder = line[closing.end() :]
                reopened = re.search(
                    r"<(script|style|pre|textarea)(?:\s|>)",
                    remainder,
                    re.IGNORECASE,
                )
                if reopened is not None:
                    html_container = reopened.group(1).lower()
                elif re.search(
                    rf"<{html_block_pattern}(?:\s|>)",
                    remainder,
                    re.IGNORECASE,
                ) is not None:
                    html_block_until_blank = True
            eligible.append(None)
            continue
        if lazy_blockquote:
            if not stripped:
                lazy_blockquote = False
            eligible.append(None)
            continue

        opening = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if opening is not None:
            marker = opening.group(1)
            fence_character = marker[0]
            fence_length = len(marker)
            eligible.append(None)
            continue
        if re.match(r"^(?: {4,}| {0,3}\t)", line) is not None:
            eligible.append(None)
            continue
        if re.match(r"^ {0,3}>", line) is not None:
            lazy_blockquote = True
            eligible.append(None)
            continue
        if "<!--" in line:
            html_comment = comment_remains_open(line, initially_open=False)
            eligible.append(None)
            continue
        container = re.match(
            r"^ {0,3}<(script|style|pre|textarea)(?:\s|>)",
            line,
            re.IGNORECASE,
        )
        if container is not None:
            tag = container.group(1).lower()
            closing = re.search(rf"</{tag}\s*>", line, re.IGNORECASE)
            if closing is None:
                html_container = tag
            else:
                remainder = line[closing.end() :]
                reopened = re.search(
                    r"<(script|style|pre|textarea)(?:\s|>)",
                    remainder,
                    re.IGNORECASE,
                )
                if reopened is not None:
                    html_container = reopened.group(1).lower()
                elif re.search(
                    rf"<{html_block_pattern}(?:\s|>)",
                    remainder,
                    re.IGNORECASE,
                ) is not None:
                    html_block_until_blank = True
            eligible.append(None)
            continue
        if re.match(
            rf"^ {{0,3}}<{html_block_pattern}(?:\s|>)",
            line,
            re.IGNORECASE,
        ) is not None:
            html_block_until_blank = True
            eligible.append(None)
            continue
        # Review subjects are deliberately accepted only at the document root.
        if line[:1].isspace() and stripped:
            eligible.append(None)
            continue
        eligible.append(line)
    return eligible


def _top_level_review_subjects(text: str) -> list[tuple[str, str]]:
    """Parse complete, unquoted root-level review-subject declarations."""

    lines = _top_level_review_lines(text)
    candidate_commit = re.compile(
        r"^(?:[-*] )?Candidate commit:\s*"
        r"(?:`([0-9a-f]{40})`|([0-9a-f]{40}))\s*$",
        re.IGNORECASE,
    )
    candidate_tree = re.compile(
        r"^(?:[-*] )?Candidate tree:\s*"
        r"(?:`([0-9a-f]{40})`|([0-9a-f]{40}))\s*$",
        re.IGNORECASE,
    )
    exact_commit = re.compile(
        r"^[-*] Commit:\s*(?:`([0-9a-f]{40})`|([0-9a-f]{40}))\s*$",
        re.IGNORECASE,
    )
    exact_tree = re.compile(
        r"^[-*] Tree:\s*(?:`([0-9a-f]{40})`|([0-9a-f]{40}))\s*$",
        re.IGNORECASE,
    )
    immutable = re.compile(
        r"^[-*] Immutable bindings:\s*`([0-9a-f]{40})`"
        r"[^\n]*?\band tree\s*`([0-9a-f]{40})`[^\n]*$",
        re.IGNORECASE,
    )

    def next_nonblank(index: int) -> int | None:
        index += 1
        while (
            index < len(lines)
            and isinstance(lines[index], str)
            and not lines[index].strip()
        ):
            index += 1
        if index >= len(lines) or lines[index] is None:
            return None
        return index

    def matched_hash(match: re.Match[str]) -> str:
        return match.group(1) or match.group(2)

    subjects: list[tuple[str, str]] = []
    explicit_pairs = 0
    incomplete = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if line is None or line == "":
            index += 1
            continue
        commit_match = candidate_commit.fullmatch(line)
        tree_match = candidate_tree.fullmatch(line)
        immutable_match = immutable.fullmatch(line)
        if commit_match is not None:
            tree_index = next_nonblank(index)
            adjacent_tree = (
                candidate_tree.fullmatch(lines[tree_index])
                if tree_index is not None
                else None
            )
            if adjacent_tree is None:
                incomplete = True
                index += 1
                continue
            subjects.append(
                (matched_hash(commit_match), matched_hash(adjacent_tree))
            )
            explicit_pairs += 1
            index = tree_index + 1
            continue
        if tree_match is not None:
            incomplete = True
            index += 1
            continue
        if re.fullmatch(r"Exact candidate:\s*", line, re.IGNORECASE):
            commit_index = next_nonblank(index)
            commit_value = (
                exact_commit.fullmatch(lines[commit_index])
                if commit_index is not None
                else None
            )
            tree_index = next_nonblank(commit_index) if commit_index is not None else None
            tree_value = (
                exact_tree.fullmatch(lines[tree_index])
                if tree_index is not None
                else None
            )
            if commit_value is None or tree_value is None:
                incomplete = True
                index += 1
                continue
            subjects.append(
                (matched_hash(commit_value), matched_hash(tree_value))
            )
            index = tree_index + 1
            continue
        if immutable_match is not None:
            subjects.append((immutable_match.group(1), immutable_match.group(2)))
            index += 1
            continue
        if re.match(
            r"^(?:[-*] )?(?:Candidate commit|Candidate tree|Immutable bindings)\b",
            line,
            re.IGNORECASE,
        ) is not None or re.match(
            r"^Exact candidate\b",
            line,
            re.IGNORECASE,
        ) is not None:
            incomplete = True
        index += 1
    if incomplete or explicit_pairs > 1:
        raise DoctorError(
            "Context transition review evidence has ambiguous candidate fields"
        )
    return subjects


def _context_transition_review_binding(
    path: Path,
    *,
    commit: str,
    tree: str,
) -> dict[str, Any]:
    """Bind diagnostic review bytes for an explicit Context transition.

    Review remains evidence, never authority.  The explicit path avoids
    guessing a harness-specific report location while the exact commit/tree
    text check prevents an operator typo from attaching an unrelated report.
    """

    if not path.is_absolute():
        raise DoctorError("Context transition review evidence path must be absolute")
    surface = _surface_snapshot(path)
    if surface.get("kind") != "file" or surface.get("nlink") != 1:
        raise DoctorError(
            "Context transition review evidence must be a single-link regular file"
        )
    raw = surface["raw"]
    if len(raw) > 2 * 1024 * 1024:
        raise DoctorError("Context transition review evidence exceeds 2 MiB")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DoctorError("Context transition review evidence is not UTF-8") from exc
    subjects = _top_level_review_subjects(text)
    if len(subjects) != 1:
        raise DoctorError(
            "Context transition review evidence must name one unique candidate subject"
        )
    candidate_commit, candidate_tree = subjects[0]
    if (candidate_commit, candidate_tree) != (commit, tree):
        raise DoctorError(
            "Context transition review evidence subject differs from the exact commit/tree"
        )
    return {
        "path": str(path),
        "sha256": surface["sha256"],
        "surface": surface,
        "candidate_commit": candidate_commit,
        "candidate_tree": candidate_tree,
    }


def _stage_context_transition_projections(
    paths: DoctorPaths,
    task: dict[str, Any],
    *,
    source_commit: str,
    source_tree: str,
    review: Mapping[str, Any] | None,
) -> tuple[list[str], tuple[dict[str, Any], ...]]:
    """Stage one explicit source transition without relabelling old evidence.

    Locally provable runtime and Plan mirrors are still reconciled by the
    normal Doctor path.  This migration supplies only the otherwise
    non-derivable diagnostic-review binding and honest pending live states,
    while converting legacy free-form mirrors to the structured schema in the
    same task/cursor/snapshot transaction.
    """

    implementation = task.get("implementation")
    tracks = task.get("tracks")
    plan = task.get("plan")
    if not isinstance(implementation, dict):
        raise DoctorError("current task implementation projection is malformed")
    if not isinstance(tracks, dict):
        raise DoctorError("current task tracks projection is malformed")
    if not isinstance(plan, dict):
        raise DoctorError("current task Plan projection is malformed")
    development = implementation.get("development_source")
    coding = tracks.get("coding_lifecycle")
    item = tracks.get("item_workflow")
    todo_1916 = plan.get("todo_1916")
    if not isinstance(development, dict) or not isinstance(coding, dict):
        raise DoctorError("current task coding projections are malformed")
    if not isinstance(item, dict) or not isinstance(todo_1916, dict):
        raise DoctorError("current task item/Plan projections are malformed")

    required_item = (
        "todo",
        "commit",
        "tree",
        "generation",
        "review_path",
        "operator_acceptance",
    )
    if any(not item.get(key) for key in required_item):
        raise DoctorError("current task item evidence is incomplete")
    required_plan_review = (
        "candidate_commit",
        "candidate_tree",
        "review_path",
        "review_disposition",
    )
    if any(not todo_1916.get(key) for key in required_plan_review):
        raise DoctorError("current task Plan review evidence is incomplete")
    plan_commit, plan_tree = _plan_repository_evidence_identity(paths, plan)
    item_review = _context_transition_review_binding(
        Path(str(item["review_path"])),
        commit=str(item["commit"]),
        tree=str(item["tree"]),
    )
    plan_review = _context_transition_review_binding(
        Path(str(todo_1916["review_path"])),
        commit=str(todo_1916["candidate_commit"]),
        tree=str(todo_1916["candidate_tree"]),
    )

    previous = {
        "development_source": json.loads(json.dumps(development)),
        "live_verification": json.loads(json.dumps(task.get("live_verification"))),
        "coding_lifecycle": json.loads(json.dumps(coding)),
        "review_admission": json.loads(json.dumps(task.get("review_admission"))),
        "next": json.loads(json.dumps(task.get("next"))),
        "next_binding_policy": task.get("next_binding_policy"),
        "next_bindings": json.loads(json.dumps(task.get("next_bindings"))),
        "item_workflow": json.loads(json.dumps(item)),
        "plan_todo_1916": json.loads(json.dumps(todo_1916)),
    }

    review_path = str(review["path"]) if review is not None else None
    review_sha256 = str(review["sha256"]) if review is not None else None
    review_state = (
        _CONTEXT_TRANSITION_REVIEW_STATE
        if review is not None
        else _CONTEXT_TRANSITION_REVIEW_PENDING_STATE
    )
    item["review_sha256"] = item_review["sha256"]
    todo_1916["review_sha256"] = plan_review["sha256"]
    development.update(
        {
            "candidate_worktree": str(paths.repository),
            "reviewed_candidate_commit": source_commit,
            "reviewed_candidate_tree": source_tree,
            "review": review_path,
            "review_sha256": review_sha256,
        }
    )

    live = task.get("live_verification")
    if not isinstance(live, dict):
        raise DoctorError("current task live verification projection is malformed")
    live.update(
        {
            "canonical_commit": source_commit,
            "canonical_tree": source_tree,
            "runtime_commit": source_commit,
            "runtime_tree": source_tree,
            "item_workflow_commit": item["commit"],
            "item_workflow_tree": item["tree"],
            "item_workflow_generation": item["generation"],
            "context_projection": "PENDING_ATOMIC_CONTEXT_PUBLICATION",
            "fresh_session_result": "PENDING_FRESH_SESSION_VERIFICATION",
            "operator_cli": "PENDING_POST_CUTOVER_VERIFICATION",
            "plan_render": "PASS_EXACT_ACTIVE_BOOTSTRAP_VERIFIED",
            "unit_check": "PASS_REQUIRED_UNITS_ACTIVE_BOOTSTRAP_VERIFIED",
            "todo_1921_coding_cli": (
                "INSTALLED_EXACT_LOCAL_RUNTIME_CONTEXT_COLD_VERIFICATION_PENDING"
            ),
        }
    )
    for stale_key in (
        "context_launcher_receipt",
        "context_launcher_receipt_sha256",
        "release_file_count",
    ):
        live.pop(stale_key, None)

    coding.update(
        {
            "candidate_commit": source_commit,
            "candidate_tree": source_tree,
            "candidate_installed": True,
            "integrated_commit": source_commit,
            "integrated_tree": source_tree,
            "integration_state": "COMPLETE_CLEAN_EXACT_CANONICAL_SOURCE",
            "materialization_state": "PASS_EXACT_BOOTSTRAP_VERIFIED",
            "live_verification_state": (
                "BOOTSTRAP_UNITS_PASS_CONTEXT_COLD_VERIFICATION_PENDING"
            ),
            "state": "LIVE_EXACT_LOCAL_RUNTIME_CONTEXT_PUBLICATION_PENDING",
            "review_path": review_path,
            "review_sha256": review_sha256,
            "review_state": review_state,
            "review_is_authority": False,
            "context_required": False,
            "next": (
                "Operator fresh-session CLI and Doctor confirmation; do not mark "
                "Todo, PP, or Plan complete."
            ),
            "operator_acceptance": "PENDING_FRESH_SESSION_CLI_CONFIRMATION",
        }
    )

    admission = "NOT_APPLICABLE_REVIEW_IS_DIAGNOSTIC_EVIDENCE"
    task["review_admission"] = {
        "admission": admission,
        "current": {
            "todo": coding.get("todo"),
            "candidate_commit": source_commit,
            "candidate_tree": source_tree,
            "review_path": review_path,
            "review_sha256": review_sha256,
            "review_state": review_state,
            "authority": False,
            "admission": admission,
        },
        "item": {
            "todo": item.get("todo"),
            "candidate_commit": item.get("commit"),
            "candidate_tree": item.get("tree"),
            "review_path": item.get("review_path"),
            "review_sha256": item.get("review_sha256"),
            "authority": False,
            "admission": admission,
        },
        "plan_reconciliation": {
            "todo": 1916,
            "candidate_commit": todo_1916.get("candidate_commit"),
            "candidate_tree": todo_1916.get("candidate_tree"),
            "review_path": todo_1916.get("review_path"),
            "review_sha256": todo_1916.get("review_sha256"),
            "review_state": todo_1916.get("review_disposition"),
            "authority": False,
            "admission": admission,
        },
    }
    task["next_binding_policy"] = _NEXT_BINDING_POLICY
    task["next"] = list(_CONTEXT_TRANSITION_NEXT)
    task["next_bindings"] = {
        "coding_runtime_commit": source_commit,
        "coding_runtime_tree": source_tree,
        "item_workflow_commit": item["commit"],
        "item_workflow_tree": item["tree"],
        "item_workflow_generation": item["generation"],
        "plan_evidence_commit": plan_commit,
        "plan_evidence_tree": plan_tree,
    }
    if "todo_1921" in implementation:
        implementation["todo_1921"] = (
            "INSTALLED_EXACT_LOCAL_RUNTIME_CONTEXT_COLD_VERIFICATION_PENDING"
        )

    successor = {
        "development_source": development,
        "live_verification": live,
        "coding_lifecycle": coding,
        "review_admission": task["review_admission"],
        "next": task["next"],
        "next_binding_policy": task["next_binding_policy"],
        "next_bindings": task["next_bindings"],
        "item_workflow": item,
        "plan_todo_1916": todo_1916,
    }
    evidence_bindings = tuple(
        [
            *(
                [{**dict(review), "label": "Todo 1921 coding review"}]
                if review is not None
                else []
            ),
            {**item_review, "label": "Todo 1920 item review"},
            {**plan_review, "label": "Todo 1916 Plan review"},
        ]
    )
    if previous == successor:
        return [], evidence_bindings
    now = datetime.now().astimezone().isoformat()
    record = {
        "schema": "tgw-context-explicit-projection-migration/v1",
        "recorded_at": now,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "review_path": review_path,
        "review_sha256": review_sha256,
        "review_state": review_state,
        "item_review_path": item_review["path"],
        "item_review_sha256": item_review["sha256"],
        "plan_review_path": plan_review["path"],
        "plan_review_sha256": plan_review["sha256"],
        "previous_projection_sha256": _hash(previous),
        "successor_projection_sha256": _hash(successor),
        "authority": False,
    }
    record["migration_hash"] = _hash(record)
    history = task.setdefault("explicit_projection_migration_history", [])
    if not isinstance(history, list):
        raise DoctorError("current task projection migration history is malformed")
    history.append(record)
    task["updated_at"] = now
    return [
        "implementation.development_source.review",
        "live_verification",
        "tracks.coding_lifecycle",
        "tracks.item_workflow.review_sha256",
        "plan.todo_1916.review_sha256",
        "review_admission",
        "next",
        "next_bindings",
    ], evidence_bindings


_NEXT_BINDING_POLICY = "STRUCTURED_ONLY_NO_INLINE_GENERATION_IDENTITIES"
_INLINE_NEXT_IDENTITY = re.compile(
    r"(?i)(?:\b[0-9a-f]{7,40}\b|item-workflow-[0-9a-f]{7,40}(?:-[0-9]+)?)"
)
_CONTEXT_TRANSITION_REVIEW_STATE = "NON_ADMITTING_DIAGNOSTIC_RECORDED"
_CONTEXT_TRANSITION_REVIEW_PENDING_STATE = "PENDING_DIAGNOSTIC_EVIDENCE_NON_GATING"
_CONTEXT_TRANSITION_NEXT = [
    (
        "Operator browser-check the live Todo 1920 item editor at the route "
        "recorded by tracks.item_workflow.operator_url; if a defect remains, "
        "resume Todo 1920 from next_bindings rather than creating another "
        "recovery root."
    ),
    (
        "Operator fresh-session-check the Todo 1921 coding CLI and Doctor using "
        "next_bindings; review remains diagnostic and Context remains non-gating."
    ),
    (
        "After those operator checks, execute Todo 1916 through tgw-plan to "
        "reconcile the named Plan, PP, and specification contracts into bounded "
        "parallel implementation leaves."
    ),
    (
        "Allow urgent Todo 1917 to proceed independently before the eBay "
        "retirement deadline."
    ),
    (
        "Execute Todo 1918 independently: first the read-only inventory, "
        "threat-model, and design leaf, then bounded implementation, independent "
        "review, local installation, isolated restore verification, and operator "
        "acceptance."
    ),
    (
        "Keep the current-task projection updated at terminal and generational "
        "transitions so every harness resumes the structured bindings below."
    ),
]


def _validate_current_task_projections(
    paths: DoctorPaths,
    task: Mapping[str, Any],
    cursor: Mapping[str, Any],
    *,
    source_commit: str,
    source_tree: str,
) -> None:
    """Reject divergent *current* mirrors while leaving histories untouched."""

    implementation = _required_projection(task, "implementation", "implementation")
    development = _required_projection(
        implementation, "development_source", "development source"
    )
    if development.get("commit") != source_commit:
        raise DoctorError("current task development source commit is stale")
    if development.get("tree") != source_tree:
        raise DoctorError("current task development source tree is stale")

    source = _required_projection(task, "source", "source")
    _require_projection_pair(
        "source", source, "commit", "tree", (source_commit, source_tree)
    )

    workflow = _required_projection(
        implementation, "coding_workflow", "coding workflow"
    )
    _require_projection_pair(
        "coding workflow",
        workflow,
        "commit",
        "tree",
        (source_commit, source_tree),
    )
    bootstrap_receipt = workflow.get("bootstrap_receipt")
    materialization_hash = workflow.get("materialization_receipt_hash")
    if not isinstance(bootstrap_receipt, str) or not bootstrap_receipt:
        raise DoctorError("current task coding workflow bootstrap receipt is malformed")
    if not isinstance(materialization_hash, str) or _SHA256.fullmatch(materialization_hash) is None:
        raise DoctorError("current task coding workflow materialization hash is malformed")

    deployment = _required_projection(task, "deployment", "deployment")
    if deployment.get("scope") != "tgw-lib-coding-runtime":
        raise DoctorError("current task deployment scope is stale")
    if deployment.get("bootstrap_receipt") != bootstrap_receipt:
        raise DoctorError("current task deployment bootstrap receipt is stale")

    live = _required_projection(task, "live_verification", "live verification")
    for label, first, second in (
        ("canonical live verification", "canonical_commit", "canonical_tree"),
        ("runtime live verification", "runtime_commit", "runtime_tree"),
    ):
        _require_projection_pair(
            label, live, first, second, (source_commit, source_tree)
        )

    tracks = _required_projection(task, "tracks", "tracks")
    coding = _required_projection(tracks, "coding_lifecycle", "coding lifecycle")
    item = _required_projection(tracks, "item_workflow", "item workflow")
    if (
        not isinstance(item.get("generation"), str)
        or not item.get("generation")
    ):
        raise DoctorError("current task item workflow generation is malformed")
    _require_projection_pair(
        "coding lifecycle integration",
        coding,
        "integrated_commit",
        "integrated_tree",
        (source_commit, source_tree),
    )
    reviewed_commit = development.get("reviewed_candidate_commit")
    reviewed_tree = development.get("reviewed_candidate_tree")
    review_path = development.get("review")
    review_sha256 = development.get("review_sha256")
    coding_review_state = coding.get("review_state")
    if (
        not isinstance(reviewed_commit, str)
        or _COMMIT.fullmatch(reviewed_commit) is None
        or not isinstance(reviewed_tree, str)
        or _COMMIT.fullmatch(reviewed_tree) is None
    ):
        raise DoctorError("current task reviewed candidate projection is malformed")
    if (
        isinstance(coding_review_state, str)
        and "DIAGNOSTIC" in coding_review_state
        and coding_review_state != _CONTEXT_TRANSITION_REVIEW_PENDING_STATE
    ):
        if (
            not isinstance(review_path, str)
            or not review_path
            or not isinstance(review_sha256, str)
            or _SHA256.fullmatch(review_sha256) is None
        ):
            raise DoctorError("current task coding review evidence is malformed")
        _require_review_file(
            review_path,
            review_sha256,
            label="coding",
        )
    elif coding_review_state == _CONTEXT_TRANSITION_REVIEW_PENDING_STATE:
        if review_path is not None or review_sha256 is not None:
            raise DoctorError(
                "current task pending coding review carries unbound evidence"
            )
    else:
        raise DoctorError("current task coding review state is malformed")
    _require_git_commit_tree(
        paths.repository,
        commit=reviewed_commit,
        tree=reviewed_tree,
        label="coding review candidate",
    )
    _require_projection_pair(
        "coding lifecycle reviewed candidate",
        coding,
        "candidate_commit",
        "candidate_tree",
        (reviewed_commit, reviewed_tree),
    )
    if coding.get("candidate_installed") is not True:
        raise DoctorError("current task coding candidate is not projected as installed")
    if reviewed_tree != source_tree:
        raise DoctorError("current task installed reviewed candidate tree is stale")
    for track_key, development_key in (
        ("review_path", "review"),
        ("review_sha256", "review_sha256"),
    ):
        if coding.get(track_key) != development.get(development_key):
            raise DoctorError(f"current task coding lifecycle {track_key} is stale")
    if coding.get("bootstrap_receipt") != bootstrap_receipt:
        raise DoctorError("current task coding lifecycle bootstrap receipt is stale")
    if coding.get("materialization_receipt_hash") != materialization_hash:
        raise DoctorError("current task coding lifecycle materialization receipt is stale")
    for label, projection in (
        ("coding", coding),
        ("item", item),
    ):
        acceptance = projection.get("operator_acceptance")
        if not isinstance(acceptance, str) or not acceptance:
            raise DoctorError(
                f"current task {label} operator acceptance projection is malformed"
            )

    _require_projection_pair(
        "item workflow live verification",
        live,
        "item_workflow_commit",
        "item_workflow_tree",
        (item.get("commit"), item.get("tree")),
    )
    if live.get("item_workflow_generation") != item.get("generation"):
        raise DoctorError("current task item workflow generation is stale")
    _require_git_commit_tree(
        paths.repository,
        commit=item.get("commit"),
        tree=item.get("tree"),
        label="item workflow",
    )
    _require_review_file(
        item.get("review_path"),
        item.get("review_sha256"),
        label="item workflow",
    )

    review_admission = _required_projection(
        task, "review_admission", "review/admission"
    )
    if (
        review_admission.get("admission")
        != "NOT_APPLICABLE_REVIEW_IS_DIAGNOSTIC_EVIDENCE"
    ):
        raise DoctorError("current task review/admission boundary is stale")
    current_review = _required_projection(
        review_admission, "current", "current review/admission"
    )
    expected_review = {
        "todo": coding.get("todo"),
        "candidate_commit": reviewed_commit,
        "candidate_tree": reviewed_tree,
        "review_path": review_path,
        "review_sha256": review_sha256,
        "review_state": coding.get("review_state"),
        "authority": False,
        "admission": "NOT_APPLICABLE_REVIEW_IS_DIAGNOSTIC_EVIDENCE",
    }
    if dict(current_review) != expected_review:
        raise DoctorError("current task current review/admission projection is stale")
    item_review = _required_projection(
        review_admission, "item", "item review/admission"
    )
    expected_item_review = {
        "todo": item.get("todo"),
        "candidate_commit": item.get("commit"),
        "candidate_tree": item.get("tree"),
        "review_path": item.get("review_path"),
        "review_sha256": item.get("review_sha256"),
        "authority": False,
        "admission": "NOT_APPLICABLE_REVIEW_IS_DIAGNOSTIC_EVIDENCE",
    }
    if dict(item_review) != expected_item_review:
        raise DoctorError("current task item review/admission projection is stale")

    next_actions = task.get("next")
    if next_actions is not None and (
        not isinstance(next_actions, list)
        or not next_actions
        or not all(isinstance(action, str) and action for action in next_actions)
    ):
        raise DoctorError("current task next-action projection is malformed")
    if task.get("next_binding_policy") != _NEXT_BINDING_POLICY:
        raise DoctorError("current task next-action binding policy is missing or stale")
    if any(_INLINE_NEXT_IDENTITY.search(action) for action in next_actions):
        raise DoctorError(
            "current task next actions contain inline generation identities; use next_bindings"
        )
    next_bindings = _required_projection(task, "next_bindings", "next bindings")
    _require_projection_pair(
        "next coding runtime",
        next_bindings,
        "coding_runtime_commit",
        "coding_runtime_tree",
        (source_commit, source_tree),
    )
    _require_projection_pair(
        "next item workflow",
        next_bindings,
        "item_workflow_commit",
        "item_workflow_tree",
        (item.get("commit"), item.get("tree")),
    )
    if next_bindings.get("item_workflow_generation") != item.get("generation"):
        raise DoctorError("current task next item workflow generation is stale")

    plan = _required_projection(task, "plan", "Plan")
    plan_identity = _plan_evidence_identity(paths, task)
    if plan_identity is None:
        raise DoctorError("current task Plan evidence projection is missing")
    _require_projection_pair(
        "Plan evidence",
        plan,
        "evidence_commit",
        "evidence_tree",
        plan_identity,
    )
    resolved = _required_projection(cursor, "resolved", "cursor resolution")
    _require_projection_pair(
        "cursor Plan evidence",
        resolved,
        "plan_evidence_commit",
        "plan_evidence_tree",
        plan_identity,
    )
    todo_1916 = _required_projection(plan, "todo_1916", "Todo 1916 Plan")
    _require_git_commit_tree(
        paths.plan_repository,
        commit=todo_1916.get("candidate_commit"),
        tree=todo_1916.get("candidate_tree"),
        label="Todo 1916 Plan review candidate",
    )
    _require_review_file(
        todo_1916.get("review_path"),
        todo_1916.get("review_sha256"),
        label="Todo 1916 Plan",
    )
    _require_projection_pair(
        "Todo 1916 current Plan evidence",
        todo_1916,
        "current_plan_evidence_commit",
        "current_plan_evidence_tree",
        plan_identity,
    )
    _require_projection_pair(
        "next Plan evidence",
        next_bindings,
        "plan_evidence_commit",
        "plan_evidence_tree",
        plan_identity,
    )
    plan_review = _required_projection(
        review_admission, "plan_reconciliation", "Plan review/admission"
    )
    expected_plan_review = {
        "todo": 1916,
        "candidate_commit": todo_1916.get("candidate_commit"),
        "candidate_tree": todo_1916.get("candidate_tree"),
        "review_path": todo_1916.get("review_path"),
        "review_sha256": todo_1916.get("review_sha256"),
        "review_state": todo_1916.get("review_disposition"),
        "authority": False,
        "admission": "NOT_APPLICABLE_REVIEW_IS_DIAGNOSTIC_EVIDENCE",
    }
    if dict(plan_review) != expected_plan_review:
        raise DoctorError("current task Plan review/admission projection is stale")


def check_context_snapshot(paths: DoctorPaths) -> dict[str, Any]:
    repair = _privileged_repair_action(paths, "context")
    try:
        snapshot_surface = _surface_snapshot(paths.context_snapshot)
        task_surface = _surface_snapshot(paths.context_task)
        cursor_surface = _surface_snapshot(paths.context_cursor)
        if snapshot_surface.get("kind") != "file":
            raise DoctorError("published Context snapshot is not a regular file")
        snapshot_state = snapshot_surface
        if (
            snapshot_state["nlink"] != 1
            or snapshot_state["uid"] != paths.context_install_uid
            or snapshot_state["gid"] != paths.context_install_gid
            or snapshot_state["mode"] != _CONTEXT_SNAPSHOT_MODE
        ):
            raise DoctorError("published Context snapshot metadata is not exact install uid/gid 0444")
        snapshot_raw = snapshot_surface["raw"]
        snapshot = _json_from_surface(paths.context_snapshot, snapshot_surface)
        task = _json_from_surface(paths.context_task, task_surface)
        cursor = _json_from_surface(paths.context_cursor, cursor_surface)
        _require_trusted_root_program(paths.context_launcher, paths.trusted_release_owners)
        pair = _context_pair(paths)
        launcher_text = (pair["generation"] / "tgw-context-mcp").read_text(
            encoding="utf-8"
        )
        if str(paths.context_snapshot) not in launcher_text or str(paths.context_cursor) in launcher_text:
            raise DoctorError("Context launcher does not preserve the single-snapshot runtime boundary")
        selected = _selected_context_artifacts(paths)
        expanded = _validate_snapshot(
            snapshot,
            snapshot_raw,
            parser_path=selected["modules"]["current_context_snapshot"],
        )
        if expanded.get("task") != task or expanded.get("cursor") != cursor:
            raise DoctorError("published context does not contain the current input records")
        head, tree, _status = _source_identity(paths)
        if snapshot.get("source_commit") != head or snapshot.get("source_tree") != tree:
            raise DoctorError("published context is stale relative to canonical source")
        _validate_current_task_projections(
            paths,
            task,
            cursor,
            source_commit=head,
            source_tree=tree,
        )
        final_head, final_tree, _final_status = _source_identity(paths)
        if (final_head, final_tree) != (head, tree):
            raise DoctorError("canonical source changed during Context inspection")
        if (
            _surface_snapshot(paths.context_snapshot) != snapshot_surface
            or _surface_snapshot(paths.context_task) != task_surface
            or _surface_snapshot(paths.context_cursor) != cursor_surface
        ):
            raise DoctorError("Context input surfaces changed during inspection")
        return _check(
            "context.snapshot",
            "PASS",
            f"atomic context is current at {head[:12]}",
            evidence={
                "record_sha256": snapshot_surface["sha256"],
                "snapshot_sha256": snapshot["snapshot_sha256"],
                "plan_commit": snapshot["plan_commit"],
                "source_commit": snapshot["source_commit"],
                "source_tree": snapshot["source_tree"],
                "task": task.get("id"),
                "task_updated_at": task.get("updated_at"),
                "runtime_input": "single atomic snapshot; cursor is publisher input only",
                "launcher_sha256": _file_hash(paths.context_launcher),
            },
        )
    except Exception as exc:
        return _failed("context.snapshot", exc, repair=repair)


def _validated_exact_repair_root(
    paths: DoctorPaths,
    desired_commit: str,
    source_root: Path,
) -> tuple[Path, str]:
    """Bind a privileged repair to the exact private bootstrap extraction."""
    if _COMMIT.fullmatch(desired_commit) is None:
        raise DoctorError("privileged repair commit is invalid")
    head, tree, status = _source_identity(paths)
    if status or head != desired_commit:
        raise DoctorError(
            "privileged repair requires the exact clean canonical source commit"
        )
    root = source_root.resolve(strict=True)
    module_root = Path(__file__).resolve(strict=True).parents[2]
    observed = root.stat(follow_symlinks=False)
    if (
        root != module_root
        or source_root.is_symlink()
        or not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid != os.geteuid()
        or observed.st_mode & 0o077
    ):
        raise DoctorError("privileged repair source is not the private exact extraction")
    try:
        mode, expected = read_exact_tree_file(
            paths.repository,
            commit=desired_commit,
            tree=tree,
            path="src/tgw/doctor_cli.py",
        )
    except ValueError as exc:
        raise DoctorError("privileged repair source is absent from the exact commit") from exc
    if mode != 0o644 or Path(__file__).read_bytes() != expected:
        raise DoctorError("privileged repair Doctor differs from the exact commit")
    return root, tree


def _selected_context_artifacts(
    paths: DoctorPaths,
    *,
    desired_commit: str | None = None,
    source_root: Path | None = None,
) -> dict[str, Any]:
    if desired_commit is None:
        desired, release, _task = _desired_runtime(paths)
        artifact_root = release
    else:
        if source_root is None:
            raise DoctorError("exact Context repair source is required")
        artifact_root, _tree = _validated_exact_repair_root(
            paths, desired_commit, source_root
        )
        desired = desired_commit
        release = paths.runtime_root / "releases" / desired
    release_tree = _verify_release_tree(paths, desired, release)
    for runtime_path in (release, *release.rglob("*")):
        observed = runtime_path.stat(follow_symlinks=False)
        relative = str(runtime_path.relative_to(release)) or "."
        if runtime_path.is_symlink() or observed.st_uid != paths.context_install_uid or observed.st_gid != paths.context_install_gid or observed.st_mode & 0o022:
            raise DoctorError("selected Context release is not root:root immutable: " + relative)
    launcher = artifact_root / "scripts/tgw_context_debian_stdio.py"
    publisher = artifact_root / "scripts/tgw_context_publish.py"
    runtime_source = artifact_root / "src"
    modules = {
        name: runtime_source / f"tgw/{name}.py"
        for name in (
            "context_mcp_server",
            "current_context_snapshot",
            "local_context_runtime",
        )
    }
    required = {"launcher": launcher, "publisher": publisher, **modules}
    for name, source in required.items():
        if not source.is_file() or source.is_symlink():
            raise DoctorError(f"selected immutable runtime has no exact Context {name}")
        observed = source.stat(follow_symlinks=False)
        if observed.st_uid not in paths.trusted_release_owners or observed.st_mode & 0o022:
            raise DoctorError(f"selected immutable runtime Context {name} is not immutable")
    runtime_inventory = _context_runtime_inventory(
        runtime_source,
        uid=paths.context_install_uid,
        gid=paths.context_install_gid,
    )
    return {
        "commit": desired,
        "release": release,
        "release_tree": release_tree,
        "launcher": launcher,
        "publisher": publisher,
        "runtime_source": runtime_source,
        "modules": modules,
        "hashes": {name: _file_hash(source) for name, source in required.items()},
        "runtime_inventory": runtime_inventory,
    }


def _repair_context_artifacts(
    paths: DoctorPaths,
    *,
    desired_commit: str | None,
    source_root: Path | None,
) -> dict[str, Any]:
    """Preserve the direct test/diagnostic path while binding privileged CLI repairs."""
    if desired_commit is None and source_root is None:
        return _selected_context_artifacts(paths)
    return _selected_context_artifacts(
        paths, desired_commit=desired_commit, source_root=source_root
    )


def _exact_bootstrap_workflow_projection(
    paths: DoctorPaths,
    selected: Mapping[str, Any],
) -> dict[str, Any]:
    """Recover exact installed-runtime facts from a root bootstrap receipt.

    Context is orientation, so these facts never authorize installation. The
    explicit bootstrap has already selected and verified the immutable release;
    this helper only prevents the subsequent atomic Context publication from
    retaining a predecessor runtime as its desired state.
    """

    commit = str(selected["commit"])
    tree = str(selected["release_tree"]["tree"])
    release = Path(selected["release"]).resolve(strict=True)
    current = paths.runtime_root / "current"
    if not current.is_symlink() or current.resolve(strict=True) != release:
        raise DoctorError(
            "Context runtime reconciliation requires the exact release to be selected"
        )
    for receipt_path in sorted(
        paths.receipts.glob("*-coding-bootstrap.json"), reverse=True
    ):
        observed = receipt_path.stat(follow_symlinks=False)
        if (
            receipt_path.is_symlink()
            or not stat.S_ISREG(observed.st_mode)
            or observed.st_nlink != 1
            or observed.st_uid != paths.context_install_uid
            or observed.st_mode & 0o022
        ):
            continue
        receipt = _json(receipt_path)
        claimed_hash = receipt.get("receipt_sha256")
        unsigned = {
            key: value for key, value in receipt.items() if key != "receipt_sha256"
        }
        after = receipt.get("after")
        if (
            receipt.get("schema") != "tgw-local-doctor-repair-receipt/v1"
            or receipt.get("operation") != "coding-bootstrap"
            or claimed_hash != _hash(unsigned)
            or not isinstance(after, Mapping)
            or after.get("schema") != "tgw-local-coding-bootstrap/v1"
            or after.get("ok") is not True
            or after.get("commit") != commit
            or after.get("tree") != tree
        ):
            continue
        materialization = after.get("materialization")
        if (
            not isinstance(materialization, Mapping)
            or _SHA256.fullmatch(str(after.get("configuration_sha256", "")))
            is None
        ):
            continue
        try:
            materialization = _validated_bootstrap_materialization(
                materialization, commit=commit, tree=tree
            )
        except DoctorError:
            continue
        return {
            "state": "LIVE_EXACT_LOCAL_RUNTIME",
            "commit": commit,
            "tree": tree,
            "release": str(release),
            "bootstrap_receipt": str(receipt_path),
            "materialization_receipt_hash": materialization["receipt_hash"],
            "configuration_sha256": after["configuration_sha256"],
            "context_required": False,
            "review_authority": False,
        }
    raise DoctorError(
        "Context runtime reconciliation found no exact root bootstrap receipt"
    )


def _reconcile_current_task_projections(
    paths: DoctorPaths,
    task: dict[str, Any],
    cursor: dict[str, Any],
    *,
    workflow: Mapping[str, Any],
    source_commit: str,
    source_tree: str,
) -> list[str]:
    """Converge deterministic current mirrors without rewriting history.

    This is deliberately limited to identities Doctor can prove locally.  It
    does not infer Plan intent, provider state, review disposition, or operator
    acceptance.  Historical/reviewed bindings remain byte-for-byte intact.
    """

    changed: list[str] = []

    def set_value(projection: dict[str, Any], key: str, value: Any, label: str) -> None:
        if projection.get(key) != value:
            projection[key] = value
            changed.append(label)

    implementation = task.get("implementation")
    if not isinstance(implementation, dict):
        raise DoctorError("current task implementation projection is malformed")
    source = task.get("source")
    if not isinstance(source, dict):
        raise DoctorError("current task source projection is missing or malformed")
    set_value(source, "repository", str(paths.repository), "source.repository")
    set_value(source, "commit", source_commit, "source.commit")
    set_value(source, "tree", source_tree, "source.tree")
    set_value(
        source,
        "canonical_working_tree_clean",
        True,
        "source.canonical_working_tree_clean",
    )

    deployment = task.get("deployment")
    if not isinstance(deployment, dict):
        raise DoctorError("current task deployment projection is missing or malformed")
    if deployment.get("scope") != "tgw-lib-coding-runtime":
        raise DoctorError("current task deployment scope is not the local coding runtime")
    set_value(
        deployment,
        "bootstrap_receipt",
        workflow.get("bootstrap_receipt"),
        "deployment.bootstrap_receipt",
    )
    set_value(
        deployment,
        "state",
        "TGW_LIB_LOCAL_RUNTIME_INSTALLED",
        "deployment.state",
    )

    live = task.get("live_verification")
    tracks = task.get("tracks")
    if not isinstance(live, Mapping) or not isinstance(tracks, Mapping):
        raise DoctorError("current task live/track projections are missing or malformed")
    # Live, review, provider, integration, and operator-result fields are
    # evidence, not locally derivable mirrors.  They must already name the
    # successor before publication; Doctor never relabels predecessor PASS
    # evidence merely because a release was installed.
    coding = tracks.get("coding_lifecycle")
    if not isinstance(coding, dict):
        raise DoctorError("current task coding track is missing or malformed")
    set_value(
        coding,
        "bootstrap_receipt",
        workflow.get("bootstrap_receipt"),
        "tracks.coding_lifecycle.bootstrap_receipt",
    )
    set_value(
        coding,
        "materialization_receipt_hash",
        workflow.get("materialization_receipt_hash"),
        "tracks.coding_lifecycle.materialization_receipt_hash",
    )

    plan = task.get("plan")
    if not isinstance(plan, dict):
        raise DoctorError("current task Plan projection is malformed")
    plan_identity = _plan_repository_evidence_identity(paths, plan)
    evidence_commit, evidence_tree = plan_identity
    set_value(plan, "evidence_commit", evidence_commit, "plan.evidence_commit")
    set_value(plan, "evidence_tree", evidence_tree, "plan.evidence_tree")
    set_value(
        plan,
        "evidence_descends_from_approved",
        True,
        "plan.evidence_descends_from_approved",
    )
    todo_1916 = plan.get("todo_1916")
    if isinstance(todo_1916, dict):
        set_value(
            todo_1916,
            "current_plan_evidence_commit",
            evidence_commit,
            "plan.todo_1916.current_plan_evidence_commit",
        )
        set_value(
            todo_1916,
            "current_plan_evidence_tree",
            evidence_tree,
            "plan.todo_1916.current_plan_evidence_tree",
        )
    resolved = cursor.get("resolved")
    if not isinstance(resolved, dict):
        raise DoctorError("current task cursor resolution is malformed")
    set_value(
        resolved,
        "plan_evidence_commit",
        evidence_commit,
        "cursor.resolved.plan_evidence_commit",
    )
    set_value(
        resolved,
        "plan_evidence_tree",
        evidence_tree,
        "cursor.resolved.plan_evidence_tree",
    )

    if task.get("next_binding_policy") != _NEXT_BINDING_POLICY:
        raise DoctorError(
            "current task requires an explicit one-time structured next-action migration"
        )
    next_actions = task.get("next")
    next_bindings = task.get("next_bindings")
    if (
        not isinstance(next_actions, list)
        or not next_actions
        or not all(isinstance(action, str) and action for action in next_actions)
        or any(_INLINE_NEXT_IDENTITY.search(action) for action in next_actions)
        or not isinstance(next_bindings, Mapping)
    ):
        raise DoctorError(
            "current task next actions/bindings require explicit reconciliation"
        )

    if changed:
        now = datetime.now().astimezone().isoformat()
        record = {
            "schema": "tgw-context-current-projection-reconciliation/v1",
            "recorded_at": now,
            "fields": sorted(set(changed)),
            "source_commit": source_commit,
            "source_tree": source_tree,
            "authority": False,
        }
        record["reconciliation_hash"] = _hash(record)
        history = task.setdefault("current_projection_reconciliation_history", [])
        if not isinstance(history, list):
            raise DoctorError("current task projection reconciliation history is malformed")
        history.append(record)
        task["updated_at"] = now
        cursor["updated_at"] = now
    return sorted(set(changed))


def _validated_bootstrap_materialization(
    value: Mapping[str, Any], *, commit: str, tree: str
) -> dict[str, Any]:
    """Validate evidence emitted by the ordinary db materializer."""

    receipt = dict(value)
    claimed = receipt.pop("receipt_hash", None)
    if (
        value.get("schema") != "tgw-local-coding-bootstrap-materialization/v1"
        or value.get("actor") != "db"
        or value.get("commit") != commit
        or value.get("tree") != tree
        or _SHA256.fullmatch(str(claimed or "")) is None
        or claimed != _hash(receipt)
    ):
        raise DoctorError("coding bootstrap materialization evidence is invalid")
    return dict(value)


def _selected_context_launcher(paths: DoctorPaths) -> tuple[str, Path]:
    selected = _selected_context_artifacts(paths)
    return selected["commit"], selected["launcher"]


def _context_runtime_inventory(
    root: Path, *, uid: int | None = None, gid: int | None = None
) -> list[dict[str, Any]]:
    """Describe every copied descendant using its installed identity."""
    inventory: list[dict[str, Any]] = []
    for entry in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        observed = entry.stat(follow_symlinks=False)
        relative = entry.relative_to(root).as_posix()
        if entry.is_symlink():
            raise DoctorError(f"Context runtime inventory contains a symlink: {relative}")
        if stat.S_ISDIR(observed.st_mode):
            inventory.append(
                {
                    "path": relative,
                    "type": "directory",
                    "uid": observed.st_uid if uid is None else uid,
                    "gid": observed.st_gid if gid is None else gid,
                    "mode": stat.S_IMODE(observed.st_mode) if uid is None else 0o555,
                }
            )
        elif stat.S_ISREG(observed.st_mode):
            inventory.append(
                {
                    "path": relative,
                    "type": "file",
                    "uid": observed.st_uid if uid is None else uid,
                    "gid": observed.st_gid if gid is None else gid,
                    "mode": stat.S_IMODE(observed.st_mode) if uid is None else 0o444,
                    "sha256": _file_hash(entry),
                }
            )
        else:
            raise DoctorError(f"Context runtime inventory contains an unsupported type: {relative}")
    return inventory


def _descriptor_context_tree(root: Path, *, fsync: bool = False) -> list[dict[str, Any]]:
    """Describe a generation through O_NOFOLLOW parent-relative descriptors."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    records: list[dict[str, Any]] = []
    directories: list[int] = []

    def walk(parent_fd: int, relative: str) -> None:
        for name in sorted(os.listdir(parent_fd), key=os.fsencode):
            child = f"{relative}/{name}" if relative else name
            before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            flags = os.O_RDONLY | nofollow
            if stat.S_ISDIR(before.st_mode):
                flags |= os.O_DIRECTORY
            elif not stat.S_ISREG(before.st_mode):
                raise DoctorError(f"Context generation contains an unsupported type: {child}")
            fd = os.open(name, flags, dir_fd=parent_fd)
            try:
                pinned = os.fstat(fd)
                identity = (
                    stat.S_IFMT(pinned.st_mode), pinned.st_uid, pinned.st_gid,
                    stat.S_IMODE(pinned.st_mode), pinned.st_size, pinned.st_dev,
                    pinned.st_ino, pinned.st_nlink,
                )
                path_identity = (
                    stat.S_IFMT(before.st_mode), before.st_uid, before.st_gid,
                    stat.S_IMODE(before.st_mode), before.st_size, before.st_dev,
                    before.st_ino, before.st_nlink,
                )
                if identity != path_identity or (stat.S_ISREG(pinned.st_mode) and pinned.st_nlink != 1):
                    raise DoctorError(f"Context generation descendant identity is unsafe: {child}")
                record = {
                    "path": child,
                    "type": "directory" if stat.S_ISDIR(pinned.st_mode) else "file",
                    "uid": pinned.st_uid, "gid": pinned.st_gid,
                    "mode": stat.S_IMODE(pinned.st_mode), "size": pinned.st_size,
                    "device": pinned.st_dev, "inode": pinned.st_ino,
                    "nlink": pinned.st_nlink,
                }
                if stat.S_ISDIR(pinned.st_mode):
                    walk(fd, child)
                    directories.append(os.dup(fd))
                else:
                    digest = hashlib.sha256()
                    while chunk := os.read(fd, 1024 * 1024):
                        digest.update(chunk)
                    record["sha256"] = digest.hexdigest()
                    if fsync:
                        os.fsync(fd)
                after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                final = os.fstat(fd)
                final_identity = (
                    stat.S_IFMT(final.st_mode), final.st_uid, final.st_gid,
                    stat.S_IMODE(final.st_mode), final.st_size, final.st_dev,
                    final.st_ino, final.st_nlink,
                )
                after_identity = (
                    stat.S_IFMT(after.st_mode), after.st_uid, after.st_gid,
                    stat.S_IMODE(after.st_mode), after.st_size, after.st_dev,
                    after.st_ino, after.st_nlink,
                )
                if final_identity != identity or after_identity != identity:
                    raise DoctorError(f"Context generation descendant changed: {child}")
                records.append(record)
            finally:
                os.close(fd)

    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | nofollow)
    try:
        walk(root_fd, "")
        if fsync:
            for fd in directories:
                os.fsync(fd)
            os.fsync(root_fd)
    finally:
        for fd in directories:
            os.close(fd)
        os.close(root_fd)
    return sorted(records, key=lambda row: os.fsencode(row["path"]))


def _context_generation_manifest(selected: Mapping[str, Any]) -> bytes:
    payload = {
        "schema": "tgw-context-generation/v1",
        "commit": selected["commit"],
        "selected_hashes": selected["hashes"],
        "runtime_inventory": selected["runtime_inventory"],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def _context_generation_name(selected: Mapping[str, Any]) -> str:
    return "context-" + hashlib.sha256(_context_generation_manifest(selected)).hexdigest()


_CONTEXT_DISPATCH_SHIM = b"""#!/opt/TGW/.venvs/controller/bin/python3
import os
import sys
from pathlib import Path

name = Path(__file__).name
target = Path(__file__).parent.parent / "context-entrypoints" / "current" / name
os.execv(target, [str(target), *sys.argv[1:]])
"""


def _context_repair_phase(_phase: str, _paths: DoctorPaths) -> None:
    """Test observation point for crash/replay reconstruction; never an effect gate."""


def _context_old_generation_name(artifacts: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in ("launcher", "publisher"):
        digest.update(name.encode() + b"\0" + artifacts[name])
    return "context-old-" + digest.hexdigest()


def _validate_context_parent(path: Path, paths: DoctorPaths) -> None:
    """Require the complete managed entrypoint chain to be direct and trusted."""
    stop = paths.context_generation_pointer.parent.parent
    current = path
    while True:
        observed = current.stat(follow_symlinks=False)
        if current.is_symlink() or not stat.S_ISDIR(observed.st_mode):
            raise DoctorError(f"Context entrypoint parent is not a direct directory: {current}")
        if (
            observed.st_uid != paths.context_install_uid
            or observed.st_mode & 0o022
        ):
            raise DoctorError(f"Context entrypoint parent is not trusted immutable data: {current}")
        if current == stop:
            return
        if stop not in current.parents:
            raise DoctorError("Context entrypoint parent escaped its managed root")
        current = current.parent


def _validate_context_generation(
    generation: Path,
    paths: DoctorPaths,
    hashes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    _validate_context_parent(generation.parent, paths)
    observed = generation.stat(follow_symlinks=False)
    if (
        generation.is_symlink()
        or not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid != paths.context_install_uid
        or observed.st_gid != paths.context_install_gid
        or stat.S_IMODE(observed.st_mode) != 0o555
    ):
        raise DoctorError("Context entrypoint generation is not root-owned immutable data")
    names = {entry.name for entry in generation.iterdir()}
    expected_names = {"tgw-context-mcp", "tgw-context-publish"}
    managed_names = expected_names | {"runtime", "generation-manifest.json"}
    if names not in (expected_names, managed_names):
        raise DoctorError("Context entrypoint generation has an ambiguous inventory")
    _descriptor_context_tree(generation)
    result: dict[str, Any] = {}
    for key, name in (("launcher", "tgw-context-mcp"), ("publisher", "tgw-context-publish")):
        entry = generation / name
        surface = _surface_snapshot(entry)
        if (
            surface.get("kind") != "file"
            or surface.get("uid") != paths.context_install_uid
            or surface.get("gid") != paths.context_install_gid
            or surface.get("mode") != paths.context_launcher_mode
            or (hashes is not None and surface.get("sha256") != hashes[key])
        ):
            raise DoctorError(f"Context generation {key} type, owner, mode, or hash differs")
        result[key] = surface
    if "runtime" in names:
        manifest = generation / "generation-manifest.json"
        manifest_surface = _surface_snapshot(manifest)
        if (
            manifest_surface.get("kind") != "file"
            or manifest_surface.get("uid") != paths.context_install_uid
            or manifest_surface.get("gid") != paths.context_install_gid
            or manifest_surface.get("mode") != 0o444
        ):
            raise DoctorError("Context generation manifest type, owner, or mode differs")
        try:
            manifest_payload = json.loads(manifest.read_bytes())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DoctorError("Context generation manifest is invalid") from exc
        canonical_manifest = json.dumps(
            manifest_payload, sort_keys=True, separators=(",", ":")
        ).encode() + b"\n"
        if manifest.read_bytes() != canonical_manifest:
            raise DoctorError("Context generation manifest is not canonical")
        if generation.name != "context-" + hashlib.sha256(canonical_manifest).hexdigest():
            raise DoctorError("Context generation identity does not bind its manifest")
        if manifest_payload.get("schema") != "tgw-context-generation/v1":
            raise DoctorError("Context generation manifest schema differs")
        selected_hashes = manifest_payload.get("selected_hashes")
        if not isinstance(selected_hashes, dict):
            raise DoctorError("Context generation selected hashes are missing")
        if hashes is not None and selected_hashes != dict(hashes):
            raise DoctorError("Context generation selected parser/module hashes differ")
        for key in ("launcher", "publisher"):
            if selected_hashes.get(key) != result[key].get("sha256"):
                raise DoctorError(f"Context generation manifest {key} hash differs")
        runtime = generation / "runtime"
        observed_runtime = runtime.stat(follow_symlinks=False)
        if runtime.is_symlink() or not stat.S_ISDIR(observed_runtime.st_mode):
            raise DoctorError("Context generation runtime is not a direct directory")
        if (
            observed_runtime.st_uid != paths.context_install_uid
            or observed_runtime.st_gid != paths.context_install_gid
            or observed_runtime.st_mode & 0o022
        ):
            raise DoctorError("Context generation runtime is not root-owned immutable data")
        expected_inventory = manifest_payload.get("runtime_inventory")
        if not isinstance(expected_inventory, list):
            raise DoctorError("Context generation runtime inventory is missing")
        observed_inventory = _context_runtime_inventory(runtime)
        if observed_inventory != expected_inventory:
            raise DoctorError("Context generation runtime type, owner, mode, or hash differs")
        result["manifest"] = manifest_surface
        result["runtime_inventory"] = observed_inventory
    return result


def _validate_all_context_generations(paths: DoctorPaths) -> None:
    _validate_context_parent(paths.context_generation_root, paths)
    for entry in paths.context_generation_root.iterdir():
        if entry.name.startswith(".") or not re.fullmatch(
            r"context-(?:old-)?[0-9a-f]{64}", entry.name
        ):
            raise DoctorError("stale or ambiguous Context generation staging state")
        _validate_context_generation(entry, paths)


def _fsync_context_tree(root: Path) -> None:
    """Persist every regular file, then every directory bottom-up."""
    _descriptor_context_tree(root, fsync=True)


def _resolved_context_generation(paths: DoctorPaths) -> Path:
    pointer = paths.context_generation_pointer
    if not pointer.is_symlink():
        raise DoctorError("Context entrypoint generation pointer is missing")
    target = Path(os.readlink(pointer))
    if target.is_absolute() or len(target.parts) != 2 or target.parts[0] != "generations":
        raise DoctorError("Context entrypoint generation pointer has an unsafe target")
    generation = pointer.parent / target
    _validate_context_generation(generation, paths)
    return generation


def _discard_context_staging(path: Path, paths: DoctorPaths) -> None:
    """Discard only a recognized, unreferenced transaction staging surface."""
    if not _lexists(path):
        return
    pointer = paths.context_generation_pointer
    if pointer.is_symlink() and os.readlink(pointer) == path.name:
        raise DoctorError("Context staging state is still pointer-selected")
    observed = path.stat(follow_symlinks=False)
    if path.is_symlink() or observed.st_uid != paths.context_install_uid or observed.st_gid != paths.context_install_gid:
        raise DoctorError("Context staging state is not trusted-owned direct data")
    if path.is_dir():
        descendants = list(path.rglob("*"))
        for entry in descendants:
            entry_stat = entry.stat(follow_symlinks=False)
            if (
                entry.is_symlink()
                or entry_stat.st_uid != paths.context_install_uid
                or entry_stat.st_gid != paths.context_install_gid
                or not (stat.S_ISDIR(entry_stat.st_mode) or stat.S_ISREG(entry_stat.st_mode))
            ):
                raise DoctorError("Context staging descendant is not trusted direct data")
        for directory in (path, *(entry for entry in descendants if entry.is_dir())):
            os.chmod(directory, 0o700)
        shutil.rmtree(path)
    elif path.is_file():
        path.unlink()
    else:
        raise DoctorError("Context staging state has an unsupported type")
    _fsync_parent(path)


def _context_surface_target(paths: DoctorPaths, name: str) -> str:
    return os.path.relpath(paths.context_generation_pointer / name, paths.local_bin)


def _context_pair(paths: DoctorPaths) -> dict[str, Any]:
    generation = _resolved_context_generation(paths)
    result: dict[str, Any] = {"generation": generation, "generation_name": generation.name}
    for key, surface, name in (("launcher", paths.context_launcher, "tgw-context-mcp"), ("publisher", paths.context_publisher, "tgw-context-publish")):
        shim = _surface_snapshot(surface)
        if (
            shim.get("kind") != "file"
            or shim.get("raw") != _CONTEXT_DISPATCH_SHIM
            or shim.get("uid") != paths.context_install_uid
            or shim.get("gid") != paths.context_install_gid
            or shim.get("mode") != paths.context_launcher_mode
        ):
            raise DoctorError(f"installed Context {key} does not use the audited dispatch shim")
        expected = generation / name
        result[key] = _surface_snapshot(expected)
    return result


def check_context_launcher(paths: DoctorPaths) -> dict[str, Any]:
    repair = _privileged_repair_action(paths, "context-launcher")
    try:
        selected = _selected_context_artifacts(paths)
        desired = selected["commit"]
        source = selected["launcher"]
        pair = _context_pair(paths)
        installed_records = {}
        for name in ("launcher", "publisher"):
            installed = pair[name]
            if installed["sha256"] != selected["hashes"][name]:
                raise DoctorError(f"installed Context {name} differs from selected runtime")
            if installed["uid"] != paths.context_install_uid or installed["gid"] != paths.context_install_gid or installed["mode"] != paths.context_launcher_mode:
                raise DoctorError(f"installed Context {name} owner or mode differs from root:root 0555")
            installed_records[name] = installed
        actor = _context_probe_actor(paths)
        snapshot_raw = paths.context_snapshot.read_bytes()
        snapshot_value = json.loads(snapshot_raw.decode("utf-8"))
        expected = _validate_snapshot(
            snapshot_value,
            snapshot_raw,
            parser_path=selected["modules"]["current_context_snapshot"],
        )
        # Diagnosis must exercise the operator-visible audited shim.  Probing
        # the generation child directly would leave shim dispatch untested.
        probe = _probe_context_stdio(paths.context_launcher, actor, expected)
        return _check(
            "context.launcher",
            "PASS",
            f"Context launcher is exact at runtime {desired[:12]}",
            evidence={
                "runtime_commit": desired,
                "source": str(source),
                "installed": str(paths.context_launcher),
                "sha256": _file_hash(source),
                "publisher_source": str(selected["publisher"]),
                "publisher_installed": str(paths.context_publisher),
                "publisher_sha256": selected["hashes"]["publisher"],
                "installed_uid": installed_records["launcher"]["uid"],
                "installed_gid": installed_records["launcher"]["gid"],
                "installed_mode": oct(installed_records["launcher"]["mode"]),
                "runtime_source": str(selected["runtime_source"]),
                "runtime_modules": {
                    name: {
                        "path": str(path),
                        "sha256": selected["hashes"][name],
                    }
                    for name, path in selected["modules"].items()
                },
                "release_tree": selected["release_tree"],
                "cold_stdio_probe": probe,
            },
        )
    except Exception as exc:
        return _failed("context.launcher", exc, repair=repair)


def _linux_child_subreaper() -> int:
    value = ctypes.c_int()
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_GET_CHILD_SUBREAPER, ctypes.byref(value), 0, 0, 0) != 0:
        raise DoctorError("installed Context MCP cold probe cannot read descendant reaping state")
    return value.value


def _set_linux_child_subreaper(value: int) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_CHILD_SUBREAPER, value, 0, 0, 0) != 0:
        raise DoctorError("installed Context MCP cold probe cannot set descendant reaping state")


def _restore_linux_child_subreaper(value: int) -> None:
    failure: Exception | None = None
    restored = False
    for _attempt in range(3):
        try:
            _set_linux_child_subreaper(value)
            if _linux_child_subreaper() == value:
                restored = True
                break
            failure = DoctorError(
                "installed Context MCP cold probe cannot verify restored descendant reaping state"
            )
        except Exception as exc:
            failure = exc
    if restored and failure is None:
        return
    raise DoctorError(
        "installed Context MCP cold probe cannot restore descendant reaping state"
    ) from failure


def _drop_staged_probe_privileges(target: pwd.struct_passwd, coding_gid: int) -> None:
    """Enter the harness identity with only runtime-parent traversal authority."""
    os.setgroups([coding_gid])
    os.setgid(target.pw_gid)
    os.setuid(target.pw_uid)


def _probe_context_stdio(
    launcher: Path,
    actor: str,
    expected: Mapping[str, Any],
    timeout: float = _CONTEXT_COLD_PROBE_BUDGET_SECONDS,
    *,
    staged_snapshot_descriptor: int | None = None,
    staged_snapshot_uid: int = 0,
    staged_snapshot_gid: int = 0,
) -> dict[str, Any]:
    """Serialize ownership of Linux's process-global child-subreaper state."""
    with _CONTEXT_COLD_PROBE_LOCK:
        return _probe_context_stdio_locked(
            launcher,
            actor,
            expected,
            timeout,
            staged_snapshot_descriptor=staged_snapshot_descriptor,
            staged_snapshot_uid=staged_snapshot_uid,
            staged_snapshot_gid=staged_snapshot_gid,
        )


def _probe_context_stdio_locked(
    launcher: Path,
    actor: str,
    expected: Mapping[str, Any],
    timeout: float = _CONTEXT_COLD_PROBE_BUDGET_SECONDS,
    *,
    staged_snapshot_descriptor: int | None = None,
    staged_snapshot_uid: int = 0,
    staged_snapshot_gid: int = 0,
) -> dict[str, Any]:
    """Probe a staged snapshot through the caller's already-retained descriptor."""
    if staged_snapshot_descriptor is not None:
        if os.geteuid() != 0:
            raise DoctorError("Context staged cold preflight requires root")
    return _probe_context_stdio_process(
        launcher,
        actor,
        expected,
        timeout,
        staged_snapshot_descriptor=staged_snapshot_descriptor,
        staged_snapshot_uid=staged_snapshot_uid,
        staged_snapshot_gid=staged_snapshot_gid,
    )


def _probe_context_stdio_process(
    launcher: Path,
    actor: str,
    expected: Mapping[str, Any],
    timeout: float,
    *,
    staged_snapshot_descriptor: int | None,
    staged_snapshot_uid: int,
    staged_snapshot_gid: int,
) -> dict[str, Any]:
    """Cold-probe the installed launcher through the real MCP stdio protocol."""
    current = pwd.getpwuid(os.geteuid()).pw_name
    command = [str(launcher)]
    pass_fds: tuple[int, ...] = ()
    child_setup = None
    staged_launcher_descriptor: int | None = None
    if staged_snapshot_descriptor is not None:
        snapshot_state = os.fstat(staged_snapshot_descriptor)
        if (
            not stat.S_ISREG(snapshot_state.st_mode)
            or snapshot_state.st_uid != staged_snapshot_uid
            or snapshot_state.st_gid != staged_snapshot_gid
            or stat.S_IMODE(snapshot_state.st_mode)
            != _CONTEXT_PREFLIGHT_SNAPSHOT_MODE
        ):
            raise DoctorError("Context staged cold preflight snapshot is not install-bound")
        os.lseek(staged_snapshot_descriptor, 0, os.SEEK_SET)
        pass_fds = (staged_snapshot_descriptor,)
        target = pwd.getpwnam(actor)
        if target.pw_name != actor:
            raise DoctorError("Context staged cold preflight actor identity differs")
        coding_group = grp.getgrnam(_CODING_RUNTIME_GROUP)
        if grp.getgrgid(coding_group.gr_gid).gr_name != _CODING_RUNTIME_GROUP:
            raise DoctorError("Context staged cold preflight coding group identity differs")
        try:
            actor_groups = os.getgrouplist(actor, target.pw_gid)
        except OSError as exc:
            raise DoctorError(
                "Context staged cold preflight cannot resolve actor groups"
            ) from exc
        if coding_group.gr_gid not in actor_groups:
            raise DoctorError(
                "Context staged cold preflight actor is not a tgw-coders member"
            )

        # The exact candidate is extracted beneath a root-only bootstrap
        # directory.  Pin the already-validated launcher while privileged and
        # execute that same open file after dropping to the ordinary harness
        # user; no parent-directory traversal or mutable pathname is needed.
        try:
            staged_launcher_descriptor = os.open(
                launcher, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
            )
            launcher_state = os.fstat(staged_launcher_descriptor)
            path_state = launcher.stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(launcher_state.st_mode)
                or launcher_state.st_nlink != 1
                or any(
                    getattr(launcher_state, field) != getattr(path_state, field)
                    for field in (
                        "st_dev",
                        "st_ino",
                        "st_mode",
                        "st_nlink",
                        "st_uid",
                        "st_gid",
                        "st_size",
                        "st_mtime_ns",
                        "st_ctime_ns",
                    )
                )
            ):
                raise DoctorError(
                    "Context staged cold preflight launcher identity differs"
                )
        except Exception as primary_failure:
            if staged_launcher_descriptor is not None:
                descriptor_to_close = staged_launcher_descriptor
                staged_launcher_descriptor = None
                try:
                    os.close(descriptor_to_close)
                except Exception as cleanup_failure:
                    raise _preserve_primary_with_cleanup_failure(
                        primary_failure,
                        cleanup_failure,
                        resource="staged Context launcher descriptor",
                    ).with_traceback(primary_failure.__traceback__)
            raise primary_failure.with_traceback(primary_failure.__traceback__)
        command = [f"/proc/self/fd/{staged_launcher_descriptor}"]
        pass_fds = (staged_snapshot_descriptor, staged_launcher_descriptor)

        def child_setup() -> None:
            _drop_staged_probe_privileges(target, coding_group.gr_gid)

    elif actor != current:
        command = ["sudo", "-n", "-u", actor, str(launcher)]
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "tgw-doctor", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "tgw_context_status", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "tgw_context_current_task", "arguments": {}}},
    ]
    payload = "".join(
        json.dumps(item, separators=(",", ":")) + "\n" for item in requests
    ).encode()
    process: subprocess.Popen[bytes] | None = None
    subreaper_previous: int | None = None
    stdout = bytearray()
    stderr = bytearray()
    pending = bytearray()
    responses: dict[int, dict[str, Any]] = {}
    leader_returncode: int | None = None
    primary_failure: Exception | None = None
    deadline = time.monotonic() + timeout
    work_deadline = deadline - min(0.5, max(0.1, timeout * 0.25))

    def consume_stdout(*, eof: bool = False) -> None:
        while b"\n" in pending:
            raw_line, _, remainder = pending.partition(b"\n")
            pending[:] = remainder
            try:
                value = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise DoctorError(
                    "installed Context MCP cold probe returned invalid JSON"
                ) from exc
            if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
                raise DoctorError(
                    "installed Context MCP cold probe returned a non-JSON-RPC 2.0 message"
                )
            if "id" not in value:
                if (
                    not isinstance(value.get("method"), str)
                    or "result" in value
                    or "error" in value
                    or (
                        "params" in value
                        and not isinstance(value["params"], (Mapping, list))
                    )
                ):
                    raise DoctorError(
                        "installed Context MCP cold probe returned an unexpected JSON-RPC message"
                    )
                continue
            request_id = value.get("id")
            if (
                type(request_id) is not int
                or request_id not in {1, 2, 3, 4}
                or request_id in responses
            ):
                raise DoctorError(
                    "installed Context MCP cold probe returned an inexact or duplicate response id"
                )
            responses[request_id] = value
        if eof and pending:
            raise DoctorError(
                "installed Context MCP cold probe returned incomplete trailing output"
            )

    def stop_process_group() -> int | None:
        if process is None:
            return None
        failures: list[Exception] = []
        if process.stdin is not None and not process.stdin.closed:
            try:
                process.stdin.close()
            except Exception as exc:
                failures.append(exc)
        # The probe owns a session, so group signalling cannot affect Doctor.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception as exc:
            failures.append(exc)
        grace_deadline = min(
            deadline,
            time.monotonic() + _CONTEXT_COLD_PROBE_TERMINATE_GRACE_SECONDS,
        )
        while time.monotonic() < grace_deadline:
            time.sleep(min(0.01, grace_deadline - time.monotonic()))
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception as exc:
            failures.append(exc)
        # SIGKILL is the final group operation.  Keeping the leader unreaped
        # until now reserves its pid/pgid against reuse.
        observed = None
        while observed is None and time.monotonic() < deadline:
            try:
                observed = os.waitid(
                    os.P_PID,
                    process.pid,
                    os.WEXITED | os.WNOWAIT | os.WNOHANG,
                )
            except ChildProcessError as exc:
                failures.append(exc)
                break
            if observed is None:
                time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        returncode = None
        if observed is None:
            failures.append(
                DoctorError(
                    "installed Context MCP cold probe could not observe the leader exit"
                )
            )
        else:
            try:
                _pid, status = os.waitpid(process.pid, 0)
                returncode = os.waitstatus_to_exitcode(status)
            except Exception as exc:
                failures.append(exc)
        group_empty = False
        while True:
            try:
                reaped, _status = os.waitpid(-process.pid, os.WNOHANG)
            except ChildProcessError:
                group_empty = True
                break
            except Exception as exc:
                failures.append(exc)
                break
            if reaped == 0:
                if time.monotonic() >= deadline:
                    failures.append(
                        DoctorError(
                            "installed Context MCP cold probe process group did not empty"
                        )
                    )
                    break
                time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        if not group_empty and not any(
            "process group did not empty" in str(item) for item in failures
        ):
            failures.append(
                DoctorError(
                    "installed Context MCP cold probe cannot prove the process group empty"
                )
            )
        if failures:
            raise DoctorError("; ".join(str(item) for item in failures)) from failures[0]
        return returncode

    try:
        subreaper_previous = _linux_child_subreaper()
        try:
            _set_linux_child_subreaper(1)
            if _linux_child_subreaper() != 1:
                raise DoctorError(
                    "installed Context MCP cold probe cannot verify descendant reaping"
                )
        except Exception:
            _restore_linux_child_subreaper(subreaper_previous)
            subreaper_previous = None
            raise
        environment = {
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if staged_snapshot_descriptor is not None:
            environment["TGW_CONTEXT_PREFLIGHT_SNAPSHOT_FD"] = str(
                staged_snapshot_descriptor
            )
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd="/",
            env=environment,
            pass_fds=pass_fds,
            preexec_fn=child_setup,
            start_new_session=True,
            bufsize=0,
        )
        if staged_launcher_descriptor is not None:
            descriptor_to_close = staged_launcher_descriptor
            staged_launcher_descriptor = None
            os.close(descriptor_to_close)
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        process.stdin.write(payload)
        process.stdin.flush()
        stdin_open = True
        open_streams = {"stdout", "stderr"}
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while open_streams:
                remaining = work_deadline - time.monotonic()
                if remaining <= 0:
                    raise DoctorError(
                        f"installed Context MCP cold probe timed out after {timeout:g}s and was terminated"
                    )
                events = selector.select(min(remaining, 0.05))
                for key, _mask in events:
                    chunk = os.read(key.fileobj.fileno(), 65_536)
                    target = stdout if key.data == "stdout" else stderr
                    if len(target) + len(chunk) > _CONTEXT_COLD_PROBE_STREAM_LIMIT:
                        raise DoctorError(
                            f"installed Context MCP cold probe {key.data} exceeded its bounded output"
                        )
                    target.extend(chunk)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        open_streams.remove(key.data)
                        if key.data == "stdout":
                            consume_stdout(eof=True)
                        continue
                    if key.data == "stdout":
                        pending.extend(chunk)
                        consume_stdout()
                if stdin_open and set(responses) == {1, 2, 3, 4}:
                    process.stdin.close()
                    stdin_open = False
    except Exception as exc:
        primary_failure = exc
    finally:
        cleanup_failures: list[Exception] = []
        if staged_launcher_descriptor is not None:
            descriptor_to_close = staged_launcher_descriptor
            staged_launcher_descriptor = None
            try:
                os.close(descriptor_to_close)
            except Exception as exc:
                cleanup_failures.append(exc)
        try:
            leader_returncode = stop_process_group()
        except Exception as exc:
            cleanup_failures.append(exc)
        if subreaper_previous is not None:
            try:
                _restore_linux_child_subreaper(subreaper_previous)
            except Exception as exc:
                cleanup_failures.append(exc)
        if cleanup_failures:
            cleanup_failure = DoctorError(
                "; ".join(str(item) for item in cleanup_failures)
            )
            if primary_failure is not None:
                raise _preserve_primary_with_cleanup_failure(
                    primary_failure,
                    cleanup_failure,
                    resource="installed Context MCP cold probe",
                ).with_traceback(primary_failure.__traceback__)
            raise DoctorError(
                "installed Context MCP cold probe cleanup failed: "
                + "; ".join(str(item) for item in cleanup_failures)
            ) from cleanup_failures[0]
    if primary_failure is not None:
        raise primary_failure.with_traceback(primary_failure.__traceback__)
    if leader_returncode:
        detail = stderr.decode(errors="replace").strip()
        raise DoctorError(
            f"installed Context MCP cold probe exited {leader_returncode}: "
            f"{detail or 'no diagnostic output'}"
        )
    if set(responses) != {1, 2, 3, 4}:
        detail = (
            stderr.decode(errors="replace").strip()
            or stdout.decode(errors="replace").strip()
        )
        raise DoctorError(
            "installed Context MCP cold probe exited before all responses: "
            f"{detail or 'no diagnostic output'}"
        )
    for request_id, label in ((1, "initialize"), (2, "tools/list"), (3, "tgw_context_status"), (4, "tgw_context_current_task")):
        response = responses.get(request_id)
        if not response or "error" in response or "result" not in response:
            raise DoctorError(f"installed Context MCP cold probe failed at {label}: {response or 'no response'}")
    if set(responses) != {1, 2, 3, 4}:
        raise DoctorError("installed Context MCP cold probe response ids differ")
    initialized = responses[1]["result"]
    if (
        not isinstance(initialized, Mapping)
        or initialized.get("protocolVersion") != "2025-03-26"
        or not isinstance(initialized.get("capabilities"), Mapping)
        or not isinstance(initialized.get("serverInfo"), Mapping)
        or not isinstance(initialized["serverInfo"].get("name"), str)
        or not isinstance(initialized["serverInfo"].get("version"), str)
    ):
        raise DoctorError("installed Context MCP initialize result shape differs")
    tools = responses[2]["result"].get("tools", [])
    required = {
        "tgw_context_status", "tgw_context_current_task", "tgw_context_bundle",
        "tgw_context_code_graph", "tgw_context_plan_graph", "tgw_context_plan_source",
        "tgw_context_onboarding", "tgw_context_runbooks",
    }
    if not isinstance(tools, list) or {item.get("name") for item in tools if isinstance(item, Mapping)} != required:
        raise DoctorError("installed Context MCP read-only tool set differs")
    schema_properties = {
        "tgw_context_status": set(),
        "tgw_context_current_task": set(),
        "tgw_context_bundle": {"task", "limit"},
        "tgw_context_code_graph": {"operation", "query", "limit"},
        "tgw_context_plan_graph": {"task", "receiver", "operation", "limit"},
        "tgw_context_plan_source": {"path", "start_line", "max_lines", "authority"},
        "tgw_context_onboarding": {"actor"},
        "tgw_context_runbooks": {"query", "path", "start_line", "max_lines", "limit", "authority"},
    }
    schema_required = {
        "tgw_context_plan_graph": {"task"},
        "tgw_context_plan_source": {"path"},
        "tgw_context_onboarding": {"actor"},
    }
    for item in tools:
        schema = item.get("inputSchema") if isinstance(item, Mapping) else None
        output_schema = (
            item.get("outputSchema") if isinstance(item, Mapping) else None
        )
        properties = schema.get("properties", {}) if isinstance(schema, Mapping) else None
        output_properties = (
            output_schema.get("properties", {})
            if isinstance(output_schema, Mapping)
            else None
        )
        if (
            not isinstance(item, Mapping)
            or set(item) - {
                "name", "description", "inputSchema", "outputSchema", "annotations"
            }
            or not isinstance(item.get("description"), str)
            or not isinstance(schema, Mapping)
            or schema.get("type") != "object"
            or not isinstance(properties, Mapping)
            or set(properties) != schema_properties[item["name"]]
            or set(schema.get("required", [])) != schema_required.get(item["name"], set())
            or any(
                not isinstance(value, Mapping)
                or value.get("type") != ("integer" if name in {"limit", "start_line", "max_lines"} else "string")
                for name, value in properties.items()
            )
            or not isinstance(output_schema, Mapping)
            or output_schema.get("type") != "object"
            or not isinstance(output_properties, Mapping)
            or set(output_properties) != {"result"}
            or set(output_schema.get("required", [])) != {"result"}
            or not isinstance(output_properties["result"], Mapping)
            or output_properties["result"].get("type") != "string"
        ):
            raise DoctorError("installed Context MCP tool schema differs")
    def tool_value(request_id: int, label: str) -> dict[str, Any]:
        result = responses[request_id]["result"]
        content = result.get("content", []) if isinstance(result, Mapping) else []
        if result.get("isError") or not isinstance(content, list):
            raise DoctorError(f"installed Context MCP {label} returned an MCP error")
        if any(isinstance(item, Mapping) and item.get("type") == "error" for item in content):
            raise DoctorError(f"installed Context MCP {label} returned error content")
        try:
            text = next(item["text"] for item in content if isinstance(item, Mapping) and item.get("type") == "text")
            value = json.loads(text)
        except (StopIteration, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise DoctorError(f"installed Context MCP {label} response is not actionable JSON") from exc
        if not isinstance(value, dict) or value.get("error") or value.get("ok") is False:
            raise DoctorError(f"installed Context MCP {label} returned error content")
        return value

    status = tool_value(3, "status")
    task = tool_value(4, "current-task")
    if task.get("actor") != actor or task.get("receiver") != actor:
        raise DoctorError(f"installed Context MCP cold probe returned the wrong Linux actor (expected {actor})")
    if status.get("actor") != actor or status.get("generation_status", {}).get("state") != "CURRENT":
        raise DoctorError("installed Context MCP cold probe status is not CURRENT for the expected actor")
    status_context = status.get("current_context", {})
    bindings = (
        "plan_commit", "source_commit", "source_tree", "snapshot_sha256",
        "active_capability", "active_treatment",
    )
    if any(status_context.get(key) != expected.get(key) for key in bindings):
        raise DoctorError("installed Context MCP cold probe status bindings differ")
    task_plan = task.get("plan", {})
    development = task.get("implementation", {}).get("development_source", {})
    task_context = task.get("context", {})
    if (
        task_plan.get("approved_commit") != expected.get("plan_commit")
        or development.get("commit") != expected.get("source_commit")
        or any(task_context.get(key) != expected.get(key) for key in bindings)
        or task.get("durable_history") != expected.get("task", {}).get("durable_history")
    ):
        raise DoctorError("installed Context MCP cold probe current-task bindings differ")
    return {"actor": actor, "methods": ["initialize", "tools/list", *sorted(required)], "timeout_seconds": timeout, "generation": "CURRENT", "bindings": {key: expected[key] for key in bindings}}


def _boot_time() -> float:
    for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines():
        if line.startswith("btime "):
            return float(line.split()[1])
    raise DoctorError("kernel boot time is unavailable")


def _is_context_process(argv: Sequence[str]) -> bool:
    """Match the actual launcher/module argv, not a parent shell containing its text."""
    if not argv:
        return False
    executable = Path(argv[0]).name
    if executable == "tgw-context-mcp":
        return True
    if not executable.startswith("python"):
        return False
    if len(argv) > 1 and Path(argv[1]).name == "tgw-context-mcp":
        return True
    return any(argv[index : index + 2] == ["-m", "tgw.context_mcp_server"] for index in range(max(0, len(argv) - 1)))


def _context_process_entrypoint(
    paths: DoctorPaths, argv: Sequence[str], selected_entrypoint: Path
) -> dict[str, Any]:
    """Classify the actual server entrypoint without treating module mode as installed."""

    expected_paths = {
        paths.context_launcher.absolute(),
        (paths.context_generation_pointer / "tgw-context-mcp").absolute(),
        selected_entrypoint.absolute(),
    }
    observed: Path | None = None
    for argument in argv:
        candidate = Path(argument)
        if not candidate.is_absolute() or candidate.name != "tgw-context-mcp":
            continue
        observed = candidate
        break
    if observed is None:
        return {
            "kind": "legacy-module" if any(
                argv[index : index + 2] == ["-m", "tgw.context_mcp_server"]
                for index in range(max(0, len(argv) - 1))
            ) else "unknown",
            "path": None,
            "installed": False,
        }
    try:
        resolved = observed.resolve(strict=True)
    except OSError:
        resolved = observed.resolve(strict=False)
    expected_resolved = {path.resolve(strict=True) for path in expected_paths}
    return {
        "kind": "installed-launcher" if resolved in expected_resolved else "other-launcher",
        "path": str(observed),
        "resolved": str(resolved),
        "installed": resolved in expected_resolved,
    }


def _context_process_requires_restart(process: Mapping[str, Any]) -> bool:
    return (
        process.get("installed_entrypoint") is not True
        or process.get("predates_launcher") is True
        or process.get("predates_generation") is True
        or process.get("predates_snapshot") is True
    )


def _pidfd_has_exited(pidfd: int) -> bool:
    selector = selectors.DefaultSelector()
    try:
        selector.register(pidfd, selectors.EVENT_READ)
        return bool(selector.select(0))
    finally:
        selector.close()


def _process_stat_start_ticks(
    pid: int, *, proc_root: Path = Path("/proc")
) -> int:
    """Read the start ticks for exactly the PID named by one proc stat row."""

    try:
        raw = (proc_root / str(pid) / "stat").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise DoctorError(f"process {pid} stat is unavailable: {exc}") from exc
    opening = raw.find("(")
    closing = raw.rfind(")")
    fields = raw[closing + 2 :].split() if 0 < opening < closing else []
    try:
        observed_pid = int(raw[:opening].strip())
        start_ticks = int(fields[19])
    except (IndexError, ValueError) as exc:
        raise DoctorError(f"process {pid} stat is malformed") from exc
    if observed_pid != pid or start_ticks < 0:
        raise DoctorError(f"process {pid} stat identity differs")
    return start_ticks


def _stable_process_identity(
    pid: int,
    boot_id: str,
    *,
    proc_root: Path = Path("/proc"),
) -> dict[str, Any]:
    """Return a PID-reuse-safe identity after pidfd/stat revalidation."""

    if pid <= 0 or not boot_id or ":" in boot_id or "\n" in boot_id:
        raise DoctorError("process identity inputs are invalid")
    if not hasattr(os, "pidfd_open"):
        raise DoctorError("pidfd process identity is unavailable")
    pidfd = -1
    try:
        pidfd = os.pidfd_open(pid, 0)
        if _pidfd_has_exited(pidfd):
            raise DoctorError(f"process {pid} exited before identity inspection")
        before_start_ticks = _process_stat_start_ticks(pid, proc_root=proc_root)
        after_start_ticks = _process_stat_start_ticks(pid, proc_root=proc_root)
        if (
            _pidfd_has_exited(pidfd)
            or after_start_ticks != before_start_ticks
        ):
            raise DoctorError(
                f"process {pid} changed identity during inspection; retry"
            )
    except DoctorError:
        raise
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError) as exc:
        raise DoctorError(f"process {pid} identity is unavailable: {exc}") from exc
    finally:
        if pidfd >= 0:
            os.close(pidfd)
    return {
        "pid": pid,
        "start_ticks": before_start_ticks,
        "process_identity": f"{boot_id}:{pid}:{before_start_ticks}",
    }


def _process_start_ns_lower_bound(
    boot_time_seconds: int, start_ticks: int, clock_ticks: int
) -> int:
    if boot_time_seconds < 0 or start_ticks < 0 or clock_ticks <= 0:
        raise DoctorError("process start-time inputs are invalid")
    return (
        boot_time_seconds * 1_000_000_000
        + start_ticks * 1_000_000_000 // clock_ticks
    )


def _process_not_proven_after(
    boot_time_seconds: int,
    start_ticks: int,
    clock_ticks: int,
    artifact_mtime_ns: int,
) -> bool:
    return (
        _process_start_ns_lower_bound(
            boot_time_seconds, start_ticks, clock_ticks
        )
        <= artifact_mtime_ns
    )


def _context_processes_once(paths: DoctorPaths) -> list[dict[str, Any]]:
    boot = _boot_time()
    boot_id_path = Path("/proc/sys/kernel/random/boot_id")
    boot_id = boot_id_path.read_text(
        encoding="utf-8"
    ).strip()
    if not boot_id:
        raise DoctorError("kernel boot identity is unavailable")
    ticks = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
    launcher_surface = _surface_snapshot(paths.context_launcher)
    snapshot_surface = _surface_snapshot(paths.context_snapshot)
    pointer_surface = _surface_snapshot(paths.context_generation_pointer)
    if (
        launcher_surface.get("kind") != "file"
        or snapshot_surface.get("kind") != "file"
        or pointer_surface.get("kind") != "symlink"
    ):
        raise DoctorError("Context process inventory artifact surfaces are malformed")
    launcher_mtime_ns = launcher_surface["mtime_ns"]
    snapshot_mtime_ns = snapshot_surface["mtime_ns"]
    pair = _context_pair(paths)
    selected_entrypoint = pair["generation"] / "tgw-context-mcp"
    entrypoint_surface = _surface_snapshot(selected_entrypoint)
    if entrypoint_surface.get("kind") != "file":
        raise DoctorError("selected Context entrypoint is not a regular file")
    generation_mtime_ns = max(
        pointer_surface["mtime_ns"],
        entrypoint_surface["mtime_ns"],
    )
    processes: list[dict[str, Any]] = []
    for entry in sorted(Path("/proc").iterdir(), key=lambda item: item.name):
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        context_observed = False
        pidfd = -1
        try:
            if not hasattr(os, "pidfd_open"):
                raise DoctorError("pidfd process identity is unavailable")
            pidfd = os.pidfd_open(pid, 0)
            if _pidfd_has_exited(pidfd):
                continue
            cmdline_raw = (entry / "cmdline").read_bytes()
            argv = [
                value.decode(errors="replace")
                for value in cmdline_raw.split(b"\0")
                if value
            ]
            if not _is_context_process(argv):
                continue
            context_observed = True
            raw = (entry / "stat").read_text(encoding="utf-8")
            end = raw.rfind(")")
            if end < 0:
                raise DoctorError(f"Context process {pid} has malformed /proc stat")
            fields = raw[end + 2 :].split()
            start_ticks = int(fields[19])
            ppid = int(fields[1])
            process_group = int(fields[2])
            session_id = int(fields[3])
            try:
                parent_identity = _stable_process_identity(ppid, boot_id)
            except DoctorError as exc:
                raise DoctorError(
                    f"Context process {pid} parent harness identity is unproven: {exc}"
                ) from exc
            started_at = boot + start_ticks / ticks
            started_at_ns_lower_bound = _process_start_ns_lower_bound(
                int(boot), start_ticks, ticks
            )
            stat_result = entry.stat()
            entrypoint = _context_process_entrypoint(
                paths, argv, selected_entrypoint
            )
            final_cmdline_raw = (entry / "cmdline").read_bytes()
            final_raw = (entry / "stat").read_text(encoding="utf-8")
            final_stat_result = entry.stat()
            final_end = final_raw.rfind(")")
            final_fields = final_raw[final_end + 2 :].split() if final_end >= 0 else []
            if (
                _pidfd_has_exited(pidfd)
                or final_cmdline_raw != cmdline_raw
                or len(final_fields) <= 19
                or int(final_fields[19]) != start_ticks
                or int(final_fields[1]) != ppid
                or int(final_fields[3]) != session_id
                or final_stat_result.st_ino != stat_result.st_ino
                or final_stat_result.st_uid != stat_result.st_uid
            ):
                raise DoctorError(
                    f"Context process {pid} changed identity during inventory; retry"
                )
            processes.append(
                {
                    "pid": pid,
                    "process_identity": f"{boot_id}:{pid}:{start_ticks}",
                    "boot_id": boot_id,
                    "parent_pid": ppid,
                    "parent_start_ticks": parent_identity["start_ticks"],
                    "parent_process_identity": parent_identity["process_identity"],
                    "process_group": process_group,
                    "session_id": session_id,
                    "user": pwd.getpwuid(stat_result.st_uid).pw_name,
                    "started_at": datetime.fromtimestamp(started_at, UTC).isoformat(),
                    "start_ticks": start_ticks,
                    "started_at_ns_lower_bound": started_at_ns_lower_bound,
                    "start_time_resolution_seconds": 1.0 / ticks,
                    "installed_entrypoint": entrypoint["installed"],
                    "entrypoint": entrypoint,
                    # The kernel start time is quantized down to one clock tick.
                    # Treat an overlap with publication as stale; a false restart
                    # request is safe, while accepting a process that read the
                    # predecessor snapshot is not.
                    "predates_launcher": (
                        _process_not_proven_after(
                            int(boot), start_ticks, ticks, launcher_mtime_ns
                        )
                    ),
                    "predates_generation": (
                        _process_not_proven_after(
                            int(boot), start_ticks, ticks, generation_mtime_ns
                        )
                    ),
                    "predates_snapshot": (
                        _process_not_proven_after(
                            int(boot), start_ticks, ticks, snapshot_mtime_ns
                        )
                    ),
                    "cmdline_sha256": "sha256:"
                    + hashlib.sha256(cmdline_raw).hexdigest(),
                    "command": " ".join(argv),
                }
            )
        except (FileNotFoundError, ProcessLookupError):
            # The pidfd either could not be opened or the exact process exited;
            # an exited process cannot retain a live Context snapshot.
            continue
        except PermissionError as exc:
            raise DoctorError(
                f"Context process inventory cannot inspect PID {pid}: {exc}"
            ) from exc
        except (KeyError, OSError, ValueError) as exc:
            if context_observed:
                raise DoctorError(
                    f"Context process {pid} could not be inventoried exactly: {exc}"
                ) from exc
            if isinstance(exc, OSError) and exc.errno in {
                errno.ENOENT,
                errno.ESRCH,
            }:
                continue
            raise DoctorError(
                f"process inventory could not prove PID {pid} is non-Context: {exc}"
            ) from exc
        finally:
            if pidfd >= 0:
                os.close(pidfd)
    if (
        boot_id_path.read_text(encoding="utf-8").strip() != boot_id
        or _surface_snapshot(paths.context_launcher) != launcher_surface
        or _surface_snapshot(paths.context_snapshot) != snapshot_surface
        or _surface_snapshot(paths.context_generation_pointer) != pointer_surface
        or _surface_snapshot(selected_entrypoint) != entrypoint_surface
    ):
        raise DoctorError("Context artifacts changed during process inventory; retry")
    return sorted(processes, key=lambda item: item["pid"])


def _context_processes(paths: DoctorPaths) -> list[dict[str, Any]]:
    """Return two agreeing best-effort snapshots of visible Context clients.

    Linux does not expose a global process-set fence.  Callers must therefore
    keep this diagnostic and non-gating even when both bounded scans agree.
    """

    first = _context_processes_once(paths)
    second = _context_processes_once(paths)
    if second != first:
        raise DoctorError(
            "Context process set changed during bounded inventory comparison; retry"
        )
    return second


def check_context_processes(paths: DoctorPaths) -> dict[str, Any]:
    try:
        processes = _context_processes(paths)
        stale = [item for item in processes if _context_process_requires_restart(item)]
        state = "RESTART_REQUIRED" if stale else "WARN"
        detail = (
            f"{len(stale)} visible Context MCP process(es) appear older than the "
            "current generation; start a fresh harness session"
            if stale
            else (
                f"{len(processes)} visible Context MCP process(es) appear current in "
                "a best-effort non-gating snapshot"
                if processes
                else "no live Context MCP process is visible"
            )
        )
        return _check(
            "context.clients",
            state,
            detail,
            evidence={
                "processes": processes,
                "observed_stale_process_identities": [
                    item["process_identity"] for item in stale
                ],
                "inventory_scope": (
                    "best-effort visible-process snapshot; not a global process-set "
                    "proof, authority, or repair gate"
                ),
                "restart_identities": None,
            },
            repair="start a fresh harness session; do not signal a reported PID",
        )
    except Exception as exc:
        return _check(
            "context.clients",
            "UNKNOWN",
            str(exc),
            evidence={
                "restart_identities": None,
                "restart_scope": (
                    "unknown; use a manual fresh harness session and do not signal "
                    "any reported PID"
                ),
            },
            repair=(
                "start a fresh harness session manually; process inventory is not "
                "stable enough to identify a parent restart target"
            ),
        )


def _context_restart_report(paths: DoctorPaths) -> dict[str, Any]:
    """Require a safe fresh session without claiming a global process proof."""

    try:
        clients = _context_processes(paths)
        observed_stale = [
            item["process_identity"]
            for item in clients
            if _context_process_requires_restart(item)
        ]
        error = None
    except Exception as exc:
        clients = []
        observed_stale = []
        error = str(exc)
    return {
        "client_processes_mutated": False,
        "client_restart_evidence_ok": None,
        "restart_required": [
            {
                "action": "START_FRESH_HARNESS_SESSION",
                "process_target": None,
                "authority": False,
            }
        ],
        "restart_scope": (
            "fresh harness session only; observed processes are diagnostic and "
            "no PID or parent process is an actionable target"
        ),
        "observed_context_processes": len(clients),
        "observed_stale_process_identities": observed_stale,
        "restart_detection_error": error,
    }


def _operator_actor() -> str:
    current = pwd.getpwuid(os.geteuid()).pw_name
    if os.geteuid() != 0:
        return current
    sudo_user = os.environ.get("SUDO_USER", "")
    if not sudo_user or sudo_user == "root":
        return current
    try:
        pwd.getpwnam(sudo_user)
    except KeyError as exc:
        raise DoctorError(f"sudo operator account does not exist: {sudo_user}") from exc
    return sudo_user


def _context_probe_actor(paths: DoctorPaths) -> str:
    """Resolve the read-only cold-probe identity from durable task context.

    The fixed root bootstrap deliberately starts Doctor with a sanitized
    environment, so ``SUDO_USER`` is not a durable actor binding.  The current
    task already names the active harness actor and receiver.  That value has
    no effect authority: it is accepted only when it identifies one existing
    member of the ordinary ``tgw-coders`` group, then the probe drops to it.
    Legacy task fixtures without an actor retain the interactive fallback.
    """

    task = _json(paths.context_task)
    actor = task.get("actor")
    if actor is None:
        return _operator_actor()
    receiver = task.get("receiver")
    if not isinstance(actor, str) or not actor or actor == "root":
        raise DoctorError("Context task probe actor is invalid")
    if receiver is not None and receiver != actor:
        raise DoctorError("Context task actor and receiver differ")
    try:
        target = pwd.getpwnam(actor)
        coding_group = grp.getgrnam(_CODING_RUNTIME_GROUP)
        actor_groups = os.getgrouplist(actor, target.pw_gid)
    except (KeyError, OSError) as exc:
        raise DoctorError("Context task probe actor cannot be resolved") from exc
    if target.pw_name != actor or coding_group.gr_gid not in actor_groups:
        raise DoctorError("Context task probe actor is not a tgw-coders member")
    return actor


def _ordinary_coding_probe_actor(paths: DoctorPaths) -> str:
    """Resolve the ordinary actor for coding checks.

    Interactive ``sudo`` preserves the invoking actor in ``SUDO_USER``.  The
    fixed exact bootstrap intentionally sanitizes that environment, so its
    post-repair diagnosis must instead use the already-bound durable task
    actor.  The Context helper validates that this fallback is an existing
    ordinary member of ``tgw-coders``; it grants no new authority.
    """

    actor = _operator_actor()
    if actor == "root":
        actor = _context_probe_actor(paths)
    if actor == "root":
        raise DoctorError("ordinary coding probe actor is unavailable")
    return actor


def _actor_path_access(actor: str, path: Path) -> bool:
    return _actor_path_access_flags(actor, path, ("-r", "-w", "-x"))


def _actor_path_access_flags(
    actor: str, path: Path, flags: Sequence[str]
) -> bool:
    if not flags or any(flag not in {"-r", "-w", "-x"} for flag in flags):
        raise DoctorError("Unix access probe flags are invalid")
    current = pwd.getpwuid(os.geteuid()).pw_name
    if actor == current:
        mask = {
            "-r": os.R_OK,
            "-w": os.W_OK,
            "-x": os.X_OK,
        }
        return all(os.access(path, mask[flag]) for flag in flags)
    return all(
        _run(
            ["sudo", "-n", "-u", actor, "/usr/bin/test", flag, str(path)]
        ).returncode
        == 0
        for flag in flags
    )


_ACTIVE_CODING_WORKTREES_SQL = """
SELECT COALESCE(
    json_agg(
        json_build_object(
            'job_id', job_id::text,
            'payload', payload_json,
            'entity_id', entity_id
        )
        ORDER BY job_id
    ),
    '[]'::json
)::text
FROM public.queue_jobs
WHERE state IN ('queued','leased','running')
  AND entity_type = 'coding_task'
"""


def _validate_active_coding_worktree_rows(
    paths: DoctorPaths, rows: Any
) -> list[Path]:
    """Validate every worktree reference carried by each in-flight job."""
    if not isinstance(rows, list):
        raise DoctorError("active coding worktree query returned malformed data")
    root = paths.worktrees.resolve(strict=True)
    worktrees: set[Path] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise DoctorError("active coding worktree query returned malformed data")
        job_id = row.get("job_id")
        payload = row.get("payload")
        entity_id = row.get("entity_id")
        if not isinstance(job_id, str) or not job_id or not isinstance(payload, dict):
            raise DoctorError("active coding worktree query returned malformed data")

        references: list[tuple[str, Any]] = []
        if "worktree" in payload:
            references.append(("payload.worktree", payload["worktree"]))
        task_spec = payload.get("task_spec")
        if isinstance(task_spec, dict) and "worktree" in task_spec:
            references.append(("payload.task_spec.worktree", task_spec["worktree"]))
        references.append(("entity_id", entity_id))
        if not references:
            raise DoctorError(f"active coding job {job_id} has no worktree reference")

        resolved: set[Path] = set()
        for field, value in references:
            if not isinstance(value, str) or not value:
                raise DoctorError(
                    f"active coding job {job_id} has malformed {field}"
                )
            candidate = Path(value)
            if not candidate.is_absolute() or candidate.parent != root:
                raise DoctorError(
                    f"active coding job {job_id} {field} is not a managed absolute path"
                )
            try:
                location = candidate.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise DoctorError(
                    f"active coding job {job_id} {field} cannot be resolved: {value}"
                ) from exc
            if (
                location != candidate
                or location.parent != root
                or re.fullmatch(
                    r"todo-[0-9]+-plan-[0-9a-f]+", location.name
                )
                is None
            ):
                raise DoctorError(
                    f"active coding job {job_id} {field} is not a managed worktree: {value}"
                )
            resolved.add(location)
        if len(resolved) != 1:
            raise DoctorError(
                f"active coding job {job_id} has conflicting worktree references"
            )
        worktrees.update(resolved)
    return sorted(worktrees)


def _active_coding_worktrees(paths: DoctorPaths) -> list[Path]:
    """Return only worktrees with an in-flight local queue job."""
    config = _coding_config(paths)
    actor = _ordinary_coding_probe_actor(paths)
    current = pwd.getpwuid(os.geteuid()).pw_name
    if actor != current:
        result = _run(
            [
                "sudo",
                "-n",
                "-u",
                actor,
                "/usr/bin/psql",
                f"--dbname={config['postgres_dsn']}",
                "--no-align",
                "--tuples-only",
                "--command",
                _ACTIVE_CODING_WORKTREES_SQL,
            ],
            timeout=30,
        )
        if result.returncode:
            raise DoctorError(
                result.stderr.strip() or "cannot read active coding worktrees"
            )
        raw: Any = result.stdout.strip()
    else:
        psycopg2, _extras = _postgres_driver()
        with psycopg2.connect(config["postgres_dsn"]) as connection:
            with connection.cursor() as cursor:
                cursor.execute(_ACTIVE_CODING_WORKTREES_SQL)
                raw = cursor.fetchone()[0]
    rows = json.loads(raw) if isinstance(raw, str) else raw
    return _validate_active_coding_worktree_rows(paths, rows)


def _shared_git_directory(path: Path, group_gid: int) -> dict[str, Any]:
    if path.is_symlink() or not path.is_dir():
        return {
            "path": str(path),
            "exact": False,
            "reason": "missing, symlinked, or not a directory",
        }
    state = path.stat(follow_symlinks=False)
    mode = stat.S_IMODE(state.st_mode)
    exact = state.st_gid == group_gid and bool(mode & stat.S_ISGID) and mode & stat.S_IRWXG == stat.S_IRWXG
    return {
        "path": str(path),
        "exact": exact,
        "uid": state.st_uid,
        "gid": state.st_gid,
        "mode": f"{mode:04o}",
        "reason": None if exact else "group, setgid, or group access differs",
    }


def check_unix_access(paths: DoctorPaths) -> dict[str, Any]:
    try:
        actor = _ordinary_coding_probe_actor(paths)
        group = grp.getgrnam("tgw-coders")
        git_common = paths.repository / ".git"
        actors = {}
        # Root performs the fixed bootstrap, but it is deliberately not a
        # coding-group principal.  Verify the ordinary operator and worker
        # accounts instead of treating root's non-membership as access drift.
        for name in sorted({actor, "codex", "db"} - {"root"}):
            record = pwd.getpwnam(name)
            memberships = set(os.getgrouplist(name, record.pw_gid))
            member = group.gr_gid in memberships or name in group.gr_mem
            access = {
                "git_common": _actor_path_access(name, git_common),
                "worktree_root": _actor_path_access(name, paths.worktrees),
            }
            actors[name] = {
                "member": member,
                "access": access,
                "exact": member and all(access.values()),
            }
        directories = {
            "repository": _shared_git_directory(paths.repository, group.gr_gid),
            "git_common": _shared_git_directory(git_common, group.gr_gid),
            "worktree_root": _shared_git_directory(paths.worktrees, group.gr_gid),
        }
        # Routine health proves the canonical worktree and common Git store.
        # A recursive scan of every historical linked worktree belongs to the
        # explicit inventory/repair path; doing it here made every check take
        # minutes and let old closed worktrees report the live workflow down.
        shared_trees = _inspect_shared_git_trees(
            paths, group.gr_gid, include_linked=False
        )
        protected_roots = _coding_support_roots(paths, group.gr_gid)
        active_worktrees = _active_coding_worktrees(paths)
        for name, actor_row in actors.items():
            support_access = {}
            for key, row in protected_roots.items():
                flags = (
                    ("-r", "-x")
                    if key == "root_effect_root" and name != "db"
                    else ("-r", "-w", "-x")
                )
                support_access[key] = _actor_path_access_flags(
                    name, Path(row["path"]), flags
                )
            worktree_access = {
                str(path): _actor_path_access(name, path)
                for path in active_worktrees
            }
            actor_row["support_root_access"] = support_access
            actor_row["active_worktree_access"] = worktree_access
            actor_row["exact"] = (
                actor_row["exact"]
                and all(support_access.values())
                and all(worktree_access.values())
            )
        exact = all(row["exact"] for row in actors.values()) and all(row["exact"] for row in directories.values()) and shared_trees["exact"] and all(row["exact"] for row in protected_roots.values())
        return _check(
            "access.unix-group",
            "PASS" if exact else "FAIL",
            "ordinary Unix tgw-coders core, support-root, and in-flight worktree access is exact" if exact else "ordinary Unix tgw-coders access or shared Git directories differ",
            evidence={
                "actor": actor,
                "group": "tgw-coders",
                "group_gid": group.gr_gid,
                "members": sorted(group.gr_mem),
                "actors": actors,
                "directories": directories,
                "shared_trees": shared_trees,
                "protected_coding_roots": protected_roots,
                "active_coding_worktrees": [str(path) for path in active_worktrees],
            },
            repair=None if exact else _privileged_repair_action(paths, "unix-git-access"),
        )
    except Exception as exc:
        return _failed(
            "access.unix-group",
            exc,
            repair=_privileged_repair_action(paths, "unix-git-access"),
        )


_TODO_BINDINGS_SQL = """
SELECT COALESCE(
    json_agg(json_build_object('id', id, 'agent', agent, 'status_note', status_note)),
    '[]'::json
)::text
FROM public.todo_items
WHERE status_note IS NOT NULL
"""


def _todo_binding_rows(paths: DoctorPaths) -> list[dict[str, Any]]:
    config = _coding_config(paths)
    actor = _ordinary_coding_probe_actor(paths)
    current = pwd.getpwuid(os.geteuid()).pw_name
    if actor != current:
        result = _run(
            [
                "sudo",
                "-n",
                "-u",
                actor,
                "/usr/bin/psql",
                f"--dbname={config['postgres_dsn']}",
                "--no-align",
                "--tuples-only",
                "--command",
                _TODO_BINDINGS_SQL,
            ],
            timeout=30,
        )
        if result.returncode:
            raise DoctorError(result.stderr.strip() or "cannot read Todo Plan bindings")
        raw = result.stdout.strip()
    else:
        psycopg2, _extras = _postgres_driver()
        with psycopg2.connect(config["postgres_dsn"]) as connection:
            with connection.cursor() as cursor:
                cursor.execute(_TODO_BINDINGS_SQL)
                raw = cursor.fetchone()[0]
    rows = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise DoctorError("Todo Plan binding query returned malformed data")
    return rows


def _bound_coding_states(paths: DoctorPaths, locations: Sequence[Path]) -> dict[str, dict[str, Any]]:
    from tgw.development.partial_resume import classify as classify_coding
    from tgw.development.partial_resume import source_tree
    from tgw.development.plan_binding import parse_plan_binding

    requested = {
        location.resolve() for location in locations if location.is_dir() and paths.worktrees.resolve() in location.resolve().parents and re.fullmatch(r"todo-[0-9]+-plan-[0-9a-f]+", location.name)
    }
    if not requested:
        return {}
    expected_by_path: dict[Path, dict[str, Any]] = {}
    for row in _todo_binding_rows(paths):
        todo_id = row.get("id")
        if not isinstance(todo_id, int):
            continue
        try:
            binding = parse_plan_binding(row.get("status_note"), todo_id=todo_id)
        except ValueError:
            continue
        if binding is None:
            continue
        location = Path(binding["worktree"]).resolve()
        if location not in requested:
            continue
        expected_by_path[location] = {
            "todo_id": todo_id,
            "plan_commit": binding["plan_commit"],
            "solution_hash": binding["solution_hash"],
            "source_commit": binding["source_commit"],
            "source_tree": source_tree(location, binding["source_commit"]),
            "actor": row.get("agent") or "codex",
            "worktree": str(location),
            "treatment_id": "codex-implement",
            "treatment_version": "1",
        }
    states = {}
    for location in sorted(requested):
        expected = expected_by_path.get(location)
        legacy = None
        descriptor = -1
        try:
            if expected is not None and expected.get("todo_id") in _PRE_LEDGER_PRESERVATION:
                descriptor = _open_direct_directory(location)
                legacy = _authenticate_pre_ledger_preservation(location, descriptor, grp.getgrnam("tgw-coders").gr_gid)
                fixture = _PRE_LEDGER_PRESERVATION[expected["todo_id"]]
                if legacy is not None and any(
                    (
                        expected.get("plan_commit") != _PRE_LEDGER_PLAN_COMMIT,
                        expected.get("solution_hash") != _PRE_LEDGER_SOLUTION_HASH,
                        expected.get("source_commit") != fixture["source_commit"],
                        expected.get("source_tree") != fixture["source_tree"],
                        expected.get("actor") != "codex",
                        expected.get("worktree") != str(location),
                    )
                ):
                    os.close(legacy["descriptor"])
                    os.close(legacy["preservation_descriptor"])
                    legacy = None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if legacy is not None:
            os.close(legacy["descriptor"])
            os.close(legacy["preservation_descriptor"])
            fixture = _PRE_LEDGER_PRESERVATION[expected["todo_id"]]
            classified = {
                "state": "CLOSED_CANDIDATE",
                "resumable": False,
                "history": [],
                "source": {"head": fixture["candidate_commit"], "tree": fixture["candidate_tree"], "changed_paths": []},
                "legacy_pre_ledger": True,
            }
        elif expected is not None:
            classified = classify_coding(location, expected)
        else:
            classified = {
                "state": "STALE_RECEIPT",
                "resumable": False,
                "error": "worktree has no exact current Todo Plan binding",
                "history": [],
            }
        source = classified.get("source")
        history_rows = classified.get("history")
        states[str(location)] = {
            "state": classified.get("state"),
            "resumable": classified.get("resumable", False),
            "attempts": len(history_rows) if isinstance(history_rows, list) else 0,
            "resume_of": classified.get("resume_of"),
            "predecessor": classified.get("predecessor"),
            "fingerprint": classified.get("fingerprint"),
            "source_head": source.get("head") if isinstance(source, dict) else None,
            "source_tree": source.get("tree") if isinstance(source, dict) else None,
            "changed_paths": source.get("changed_paths") if isinstance(source, dict) else None,
            "error": classified.get("error"),
            "legacy_pre_ledger": classified.get("legacy_pre_ledger", False),
        }
    return states


def _publish_reconciled_implementation(
    worktree: Path,
    attempt: Mapping[str, Any],
    receipt_path: Path,
    receipt: Mapping[str, Any],
    *,
    mode: int,
) -> Path:
    """Durably append canonical history before refreshing its projection."""
    from tgw.development.partial_resume import append_attempt

    history_path = append_attempt(worktree, attempt)
    _atomic_json(receipt_path, receipt, mode=mode)
    return history_path


def reconcile_implementation_receipt(todo_id: int, paths: DoctorPaths | None = None) -> dict[str, Any]:
    """Append exact evidence for an older runner's already-closed successor."""
    from tgw.development.partial_resume import (
        candidate_changed_paths,
        history,
        make_attempt,
        source_fingerprint,
        source_tree,
        validate_closed_candidate,
        validate_implementation_lineage,
    )
    from tgw.development.plan_binding import parse_plan_binding
    from tgw.development.worktree_lease import exclusive_worktree_lease

    paths = paths or DoctorPaths()
    # This first lookup locates the lock only.  It grants no reconciliation
    # authority; every byte is reread after the canonical lease is held.
    matches = [row for row in _todo_binding_rows(paths) if row.get("id") == todo_id]
    if len(matches) != 1:
        raise DoctorError("Todo reconciliation requires one exact durable Plan binding")
    row = matches[0]
    try:
        plan = parse_plan_binding(row.get("status_note"), todo_id=todo_id)
    except ValueError as exc:
        raise DoctorError("Todo reconciliation Plan binding is malformed") from exc
    if plan is None:
        raise DoctorError("Todo reconciliation Plan binding is absent")
    worktree = Path(plan["worktree"]).resolve(strict=True)
    if paths.worktrees.resolve() not in worktree.parents:
        raise DoctorError("Todo reconciliation worktree is outside the managed root")
    with exclusive_worktree_lease(worktree):
        locked_matches = [item for item in _todo_binding_rows(paths) if item.get("id") == todo_id]
        if len(locked_matches) != 1:
            raise DoctorError("Todo binding changed while acquiring its worktree lease")
        locked_row = locked_matches[0]
        try:
            locked_plan = parse_plan_binding(locked_row.get("status_note"), todo_id=todo_id)
        except ValueError as exc:
            raise DoctorError("Todo reconciliation Plan binding changed or is malformed") from exc
        if locked_plan != plan or Path(locked_plan["worktree"]).resolve(strict=True) != worktree:
            raise DoctorError("Todo binding changed while acquiring its worktree lease")
        attempts = history(worktree)
        if not attempts:
            raise DoctorError("Todo reconciliation has no durable implementation attempt")
        latest = attempts[-1]
        required = {
            "todo_id": todo_id,
            "plan_commit": plan["plan_commit"],
            "solution_hash": plan["solution_hash"],
            "source_commit": plan["source_commit"],
            "source_tree": source_tree(worktree, plan["source_commit"]),
            "actor": locked_row.get("agent") or "codex",
            "worktree": str(worktree),
            "treatment_id": "codex-implement",
            "treatment_version": "1",
        }
        if any(latest.get(key) != value for key, value in required.items()):
            raise DoctorError("implementation attempt contradicts the current Todo binding")
        if latest.get("outcome") != "satisfied":
            raise DoctorError("implementation reconciliation refuses a non-satisfied latest attempt")
        current = source_fingerprint(worktree)
        if current["changed_paths"] or current["head"] == required["source_commit"] or latest.get("head") != current["head"] or latest.get("tree") != current["tree"]:
            raise DoctorError("implementation reconciliation requires the attempt's exact clean closed successor")
        prior_closed = [item for item in latest.get("artifacts", []) if isinstance(item, Mapping) and item.get("kind") == "closed_candidate"]
        already_reconciled = False
        legacy_closed = (
            len(prior_closed) == 1 and set(prior_closed[0]) == {"kind", "commit", "tree"} and prior_closed[0].get("commit") == current["head"] and prior_closed[0].get("tree") == current["tree"]
        )
        if not legacy_closed:
            try:
                if len(prior_closed) == 1:
                    validate_closed_candidate(worktree, prior_closed[0], base_commit=required["source_commit"], candidate_commit=current["head"], candidate_tree=current["tree"])
                    already_reconciled = True
            except ValueError:
                pass
            if not already_reconciled:
                raise DoctorError("implementation reconciliation requires exactly one permissible legacy closed candidate")
        closed = (
            dict(prior_closed[0])
            if already_reconciled
            else {
                **dict(prior_closed[0]),
                "base_commit": required["source_commit"],
                "changed_paths": candidate_changed_paths(
                    worktree,
                    required["source_commit"],
                    current["head"],
                ),
            }
        )
        validate_closed_candidate(worktree, closed, base_commit=required["source_commit"], candidate_commit=current["head"], candidate_tree=current["tree"])
        implementation_receipt = worktree / "implementation-receipt.json"
        try:
            prior_receipt_bytes = implementation_receipt.read_bytes()
            prior_receipt = json.loads(prior_receipt_bytes)
        except (OSError, json.JSONDecodeError) as exc:
            raise DoctorError("durable implementation receipt is absent or unreadable") from exc
        prior_receipt_sha256 = "sha256:" + hashlib.sha256(prior_receipt_bytes).hexdigest()
        config = _coding_config(paths)
        psycopg2, _extras = _postgres_driver()
        with psycopg2.connect(config["postgres_dsn"]) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT job_id::text, state::text, attempt_count, queue_name, payload_json
                       FROM queue_jobs WHERE job_id = %s::uuid OR payload_json->>'worktree' = %s
                         OR payload_json#>>'{task_spec,worktree}' = %s FOR SHARE""",
                    (latest.get("job_id"), str(worktree), str(worktree)),
                )
                jobs = cursor.fetchall()
                if any(job[1] not in {"succeeded", "failed", "dead_letter", "cancelled"} for job in jobs):
                    raise DoctorError("implementation reconciliation refuses active work")
                exact = [job for job in jobs if job[0] == latest.get("job_id")]
                if len(exact) != 1 or exact[0][1] != "succeeded" or exact[0][2] != latest.get("attempt_count") or exact[0][3] != "codex-implement":
                    raise DoctorError("implementation reconciliation requires one exact succeeded implementation job")
                payload = exact[0][4]
                result = payload.get("result") if isinstance(payload, Mapping) else None
                result_matches_receipt = result == prior_receipt
                if already_reconciled and isinstance(result, Mapping):
                    result_closed = [item for item in result.get("artifacts", []) if isinstance(item, Mapping) and item.get("kind") == "closed_candidate"]
                    result_matches_receipt = (
                        len(result_closed) == 1
                        and set(result_closed[0]) == {"kind", "commit", "tree"}
                        and result_closed[0].get("commit") == current["head"]
                        and result_closed[0].get("tree") == current["tree"]
                    )
                if not isinstance(result, Mapping) or not result_matches_receipt or result.get("outcome") != "satisfied" or result.get("status") != "PASS":
                    raise DoctorError("implementation reconciliation requires the exact durable succeeded job result")
                if payload.get("todo_id") != todo_id or payload.get("worktree") != str(worktree) or payload.get("plan_binding") != prior_receipt.get("plan_binding"):
                    raise DoctorError("implementation job/result binding contradicts Todo or Plan")
                durable_prior_receipt_sha256 = (
                    "sha256:"
                    + hashlib.sha256(
                        (json.dumps(result, sort_keys=True) + "\n").encode(),
                    ).hexdigest()
                )
                # Revalidate every mutable input immediately before the append
                # while both the worktree lease and durable job row lock remain held.
                if history(worktree)[-1]["attempt_hash"] != latest["attempt_hash"] or source_fingerprint(worktree) != current or implementation_receipt.read_bytes() != prior_receipt_bytes:
                    raise DoctorError("implementation source, history, or receipt changed before append")
                if already_reconciled:
                    reconciliation_items = [item for item in latest.get("artifacts", []) if isinstance(item, Mapping) and item.get("kind") == "implementation_reconciliation"]
                    if (
                        len(reconciliation_items) != 1
                        or reconciliation_items[0].get("prior_receipt_sha256") != durable_prior_receipt_sha256
                        or reconciliation_items[0].get("prior_attempt_hash") != latest.get("predecessor")
                    ):
                        raise DoctorError("existing reconciliation receipt-byte or attempt lineage is invalid")
                    recovered_receipt = {**prior_receipt, "artifacts": list(latest["artifacts"])}
                    expected_bytes = (json.dumps(recovered_receipt, indent=2, sort_keys=True) + "\n").encode()
                    changed = prior_receipt_bytes != expected_bytes
                    if changed:
                        _atomic_json(implementation_receipt, recovered_receipt, mode=stat.S_IMODE(implementation_receipt.stat().st_mode))
                    validate_implementation_lineage(
                        worktree,
                        base_commit=required["source_commit"],
                        candidate_commit=current["head"],
                        candidate_tree=current["tree"],
                        receipt=recovered_receipt,
                        expected=required,
                    )
                    return {
                        "schema": "tgw-implementation-reconciliation/v1",
                        "ok": True,
                        "changed": changed,
                        "todo_id": todo_id,
                        "job_id": latest["job_id"],
                        "prior_attempt_hash": latest.get("predecessor"),
                        "prior_receipt_sha256": durable_prior_receipt_sha256,
                        "attempt_hash": latest["attempt_hash"],
                        "worktree": str(worktree),
                    }
                reconciliation = {
                    "kind": "implementation_reconciliation",
                    "schema": "tgw-implementation-reconciliation/v1",
                    "prior_attempt_hash": latest["attempt_hash"],
                    "prior_receipt_sha256": prior_receipt_sha256,
                    "prior_receipt_b64": base64.b64encode(prior_receipt_bytes).decode("ascii"),
                    "job_id": latest["job_id"],
                    "todo_id": todo_id,
                    "plan_commit": required["plan_commit"],
                }
                keys = ("job_id", "attempt_count", "todo_id", "plan_commit", "solution_hash", "source_commit", "source_tree", "actor", "worktree", "treatment_id", "treatment_version")
                reconciled = make_attempt({key: latest[key] for key in keys}, worktree, outcome="satisfied", predecessor=latest["attempt_hash"], artifacts=[closed, reconciliation])
                recovered_receipt = {**prior_receipt, "artifacts": [closed, reconciliation]}
                # History is canonical and durable first.  A crash or receipt
                # publication failure leaves a deterministically recoverable
                # append-only successor, never a top-level-first window.
                receipt_path = _publish_reconciled_implementation(
                    worktree,
                    reconciled,
                    implementation_receipt,
                    recovered_receipt,
                    mode=stat.S_IMODE(implementation_receipt.stat().st_mode),
                )
                validate_implementation_lineage(
                    worktree,
                    base_commit=required["source_commit"],
                    candidate_commit=current["head"],
                    candidate_tree=current["tree"],
                    receipt=recovered_receipt,
                    expected=required,
                )
        return {
            "schema": "tgw-implementation-reconciliation/v1",
            "ok": True,
            "changed": True,
            "todo_id": todo_id,
            "job_id": latest["job_id"],
            "prior_attempt_hash": latest["attempt_hash"],
            "prior_receipt_sha256": prior_receipt_sha256,
            "attempt_hash": reconciled["attempt_hash"],
            "worktree": str(worktree),
            "receipt": str(receipt_path),
        }


def check_worktrees(paths: DoctorPaths) -> dict[str, Any]:
    try:
        raw = _git(paths.repository, "worktree", "list", "--porcelain")
        rows: list[dict[str, Any]] = []
        current: dict[str, Any] = {}
        for line in [*raw.splitlines(), ""]:
            if not line:
                if current:
                    rows.append(current)
                    current = {}
                continue
            key, _, value = line.partition(" ")
            current[key] = value or True
        outside = []
        prunable = []
        repository = paths.repository.resolve()
        root = paths.worktrees.resolve()
        for row in rows:
            location = Path(str(row.get("worktree", ""))).resolve()
            if location != repository and root not in location.parents:
                outside.append(str(location))
            if "prunable" in row:
                prunable.append(str(location))
        locations = [Path(str(row.get("worktree", ""))).resolve() for row in rows]
        coding_states = _bound_coding_states(paths, locations)
        coding_counts = {
            name: sum(item.get("state") == name for item in coding_states.values())
            for name in (
                "ABANDONED_CLEAN",
                "RESUMABLE_PARTIAL",
                "CLOSED_CANDIDATE",
                "UNSAFE_DIRTY",
                "STALE_RECEIPT",
            )
        }
        same_filesystem = paths.repository.stat().st_dev == paths.worktrees.stat().st_dev
        attention = coding_counts["RESUMABLE_PARTIAL"] + coding_counts["UNSAFE_DIRTY"] + coding_counts["STALE_RECEIPT"]
        state = "PASS" if not outside and not prunable and same_filesystem and not attention else "WARN"
        return _check(
            "git.worktrees",
            state,
            f"{len(rows)} linked worktree(s); {'same' if same_filesystem else 'different'} filesystem; {len(outside)} outside root; {len(prunable)} prunable; coding states {coding_counts}",
            evidence={
                "repository": str(repository),
                "worktree_root": str(root),
                "same_filesystem": same_filesystem,
                "count": len(rows),
                "outside_root": outside,
                "prunable": prunable,
                "coding_states": coding_states,
                "coding_state_counts": coding_counts,
            },
        )
    except Exception as exc:
        return _failed("git.worktrees", exc)


def inventory(paths: DoctorPaths = DoctorPaths()) -> dict[str, Any]:
    """Describe linked work and active-path remnants without changing either."""
    canonical_head, _tree, _status = _source_identity(paths)
    raw = _git(paths.repository, "worktree", "list", "--porcelain")
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in [*raw.splitlines(), ""]:
        if not line:
            if current:
                rows.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value or True

    repository = paths.repository.resolve()
    root = paths.worktrees.resolve()
    worktrees: list[dict[str, Any]] = []
    locations = [Path(str(row.get("worktree", ""))).resolve() for row in rows]
    coding_states = _bound_coding_states(paths, locations)
    for row in rows:
        location = Path(str(row.get("worktree", ""))).resolve()
        head = str(row.get("HEAD", ""))
        branch_ref = row.get("branch")
        branch = str(branch_ref).removeprefix("refs/heads/") if isinstance(branch_ref, str) else None
        exists = location.is_dir()
        dirty: bool | None = None
        unique_commits: int | None = None
        merged_into_canonical: bool | None = None
        errors: list[str] = []
        if exists:
            result = _run(
                protected_git_command(location, "status", "--short"),
                cwd=location,
                env=protected_git_environment(),
            )
            if result.returncode:
                errors.append(result.stderr.strip() or "cannot inspect worktree status")
            else:
                dirty = bool(result.stdout.strip())
        if _COMMIT.fullmatch(head):
            merged = _run(
                protected_git_command(
                    repository,
                    "merge-base",
                    "--is-ancestor",
                    head,
                    canonical_head,
                ),
                cwd=repository,
                env=protected_git_environment(),
            )
            if merged.returncode in (0, 1):
                merged_into_canonical = merged.returncode == 0
            else:
                errors.append(merged.stderr.strip() or "cannot compare worktree ancestry")
            count = _run(
                protected_git_command(
                    repository,
                    "rev-list",
                    "--count",
                    f"{canonical_head}..{head}",
                ),
                cwd=repository,
                env=protected_git_environment(),
            )
            if count.returncode:
                errors.append(count.stderr.strip() or "cannot count unique commits")
            else:
                unique_commits = int(count.stdout.strip())
        is_canonical = location == repository
        inside_root = location == root or root in location.parents
        preservation_required = dirty is not False or unique_commits is None or unique_commits > 0 or bool(errors)
        worktrees.append(
            {
                "path": str(location),
                "head": head or None,
                "branch": branch,
                "exists": exists,
                "canonical": is_canonical,
                "inside_configured_root": inside_root,
                "prunable": "prunable" in row,
                "dirty": dirty,
                "unique_commits": unique_commits,
                "merged_into_canonical": merged_into_canonical,
                "preservation_required": preservation_required,
                "errors": errors,
                "coding_state": coding_states.get(str(location)),
            }
        )

    path_roots = {Path(value) for value in os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin").split(":") if value and Path(value).is_absolute()}
    surface_roots = [("effective-path-command", parent, "tgw*") for parent in sorted(path_roots)]
    surface_roots.extend(
        [
            ("operator-libexec", Path("/usr/local/libexec"), "tgw*"),
            ("development-command", paths.local_bin, "tgw*"),
            *(("systemd-unit", parent, "tgw-*") for parent in paths.systemd_unit_roots),
        ]
    )
    active_surfaces = []
    seen: set[Path] = set()
    for kind, parent, pattern in surface_roots:
        if not parent.is_dir():
            continue
        for path in sorted(parent.glob(pattern)):
            if path in seen or not (path.exists() or path.is_symlink()):
                continue
            seen.add(path)
            active_surfaces.append(
                {
                    "kind": kind,
                    "path": str(path),
                    "symlink": path.is_symlink(),
                    "target": str(path.resolve(strict=False)) if path.is_symlink() else None,
                    "sha256": _file_hash(path) if path.is_file() else None,
                }
            )
    try:
        coding = _coding_config(paths).get("coding", {})
        configured_commands = list(coding.get("allowed_runners", []))
        for argv in coding.get("commands", {}).values():
            if isinstance(argv, list) and argv:
                configured_commands.append(argv[0])
    except DoctorError:
        configured_commands = []
    for raw_path in sorted(set(configured_commands)):
        path = Path(str(raw_path))
        if not path.is_absolute() or path in seen:
            continue
        seen.add(path)
        active_surfaces.append(
            {
                "kind": "coding-config-runner",
                "path": str(path),
                "symlink": path.is_symlink(),
                "target": str(path.resolve(strict=False)) if path.is_symlink() else None,
                "sha256": _file_hash(path) if path.is_file() else None,
            }
        )

    try:
        catalog = _json(paths.context_catalog)
        catalog_actors = set(catalog.get("actors", {}))
    except DoctorError:
        catalog_actors = set()
    try:
        coding_group = grp.getgrnam("tgw-coders")
        unix_actors = set(coding_group.gr_mem)
        unix_actors.update(record.pw_name for record in pwd.getpwall() if record.pw_gid == coding_group.gr_gid)
    except KeyError:
        unix_actors = set()
    harness_names = sorted(catalog_actors | unix_actors)
    harnesses = []
    for name in harness_names:
        home = Path("/home") / name
        markers = [
            home / ".codex",
            home / ".claude",
            home / ".config/hermes",
            home / ".config/opencode",
            home / ".gemini",
            home / ".antigravity",
        ]
        marker_rows = []
        for path in markers:
            try:
                marker_rows.append({"path": str(path), "exists": path.exists()})
            except OSError as exc:
                marker_rows.append({"path": str(path), "exists": None, "error": type(exc).__name__})
        harnesses.append(
            {
                "name": name,
                "catalog_actor": name in catalog_actors,
                "tgw_coders_member": name in unix_actors,
                "home": str(home),
                "home_exists": home.is_dir(),
                "configuration_markers": marker_rows,
            }
        )

    archive_candidates: set[Path] = set()
    archive_names = {
        "archive",
        "archives",
        "attempts",
        "backup",
        "backups",
        "migration-verify",
        "quarantine",
    }
    archive_discovery = []
    for discovery_root in paths.archive_discovery_roots:
        row: dict[str, Any] = {
            "path": str(discovery_root),
            "exists": discovery_root.is_dir(),
            "scanned": False,
            "complete": False,
            "max_depth": paths.archive_discovery_max_depth,
            "error": None,
        }
        if discovery_root.is_dir():
            walk_errors: list[OSError] = []
            try:
                for parent, directories, _files in os.walk(discovery_root, onerror=walk_errors.append):
                    relative_depth = len(Path(parent).relative_to(discovery_root).parts)
                    for name in directories:
                        if name.lower() in archive_names:
                            archive_candidates.add(Path(parent) / name)
                    directories[:] = [name for name in directories if relative_depth < paths.archive_discovery_max_depth and name not in _ARCHIVE_DISCOVERY_PRUNE and not name.startswith(".")]
                row["scanned"] = True
                row["complete"] = not walk_errors
                if walk_errors:
                    row["error"] = "; ".join(f"{type(exc).__name__}: {exc}" for exc in walk_errors)
            except OSError as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
        archive_discovery.append(row)
    archives = []
    for path in sorted(archive_candidates):
        try:
            exists: bool | None = path.is_dir()
            error = None
        except OSError as exc:
            exists = None
            error = type(exc).__name__
        archives.append({"path": str(path), "exists": exists, "error": error})
    return {
        "schema": "tgw-local-doctor-inventory/v1",
        "ok": True,
        "host": socket.gethostname(),
        "actor": pwd.getpwuid(os.geteuid()).pw_name,
        "observed_at": datetime.now(UTC).isoformat(),
        "canonical_source": {"repository": str(repository), "commit": canonical_head},
        "worktrees": worktrees,
        "active_surfaces": active_surfaces,
        "harnesses": harnesses,
        "archive_roots": archives,
        "archive_discovery": archive_discovery,
        "counts": {
            "worktrees": len(worktrees),
            "outside_configured_root": sum(not row["canonical"] and not row["inside_configured_root"] for row in worktrees),
            "preservation_required": sum(row["preservation_required"] for row in worktrees),
            "active_surfaces": len(active_surfaces),
            "harness_homes": sum(row["home_exists"] for row in harnesses),
            "archive_roots": sum(row["exists"] is True for row in archives),
            "catalog_actors": len(catalog_actors),
            "unix_coding_actors": len(unix_actors),
            "coding_states": {
                name: sum(item.get("state") == name for item in coding_states.values())
                for name in (
                    "ABANDONED_CLEAN",
                    "RESUMABLE_PARTIAL",
                    "CLOSED_CANDIDATE",
                    "UNSAFE_DIRTY",
                    "STALE_RECEIPT",
                )
            },
        },
        "cleanup_boundary": (
            "inventory is read-only; archive dirty or unique work before unlinking any "
            "worktree, never infer inactivity from age, and classify filesystem roots "
            "outside archive_discovery as unknown rather than absent"
        ),
    }


def _coding_config(paths: DoctorPaths) -> dict[str, Any]:
    config = _json(paths.coding_config)
    if config.get("schema") != "tgw-local-coding-workflow/v1":
        raise DoctorError("local coding configuration schema is invalid")
    return config


_DATABASE_OBSERVATION_SQL = """
SELECT json_build_object(
    'actor', current_user,
    'universal_role', current_user = 'tgw_coding',
    'database_connect', has_database_privilege(current_user, current_database(), 'CONNECT'),
    'schema_usage', has_schema_privilege(current_user, 'public', 'USAGE'),
    'role_member', pg_has_role(current_user, 'tgw_coding', 'member'),
    'todo_access', has_table_privilege(current_user, 'public.todo_items', 'SELECT,INSERT,UPDATE,DELETE'),
    'queue_access', has_table_privilege(current_user, 'public.queue_jobs', 'SELECT,INSERT,UPDATE,DELETE'),
    'history_access', has_table_privilege(current_user, 'public.queue_job_history', 'SELECT,INSERT'),
    'todo_sequence_access', has_sequence_privilege(current_user, 'public.todo_items_id_seq', 'USAGE,SELECT,UPDATE'),
    'history_sequence_access', has_sequence_privilege(current_user, 'public.queue_job_history_history_id_seq', 'USAGE,SELECT,UPDATE'),
    'claim_function_access', COALESCE(has_function_privilege(current_user, to_regprocedure('public.claim_queue_jobs(text,text,integer,integer)'), 'EXECUTE'), false),
    'recovery_function_access', COALESCE(has_function_privilege(current_user, to_regprocedure('public.recover_expired_jobs()'), 'EXECUTE'), false),
    'progress_note_column', EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'todo_items'
          AND column_name = 'progress_note'
    ),
    'active_jobs', (
        SELECT count(*)::integer
        FROM public.queue_jobs
        WHERE queue_name IN ('codex-implement','controller-verify')
          AND state IN ('queued','leased','running')
    )
)::text AS observation_json
"""


def _database_observation(
    config: Mapping[str, Any], actor: str
) -> tuple[dict[str, Any], int]:
    current = pwd.getpwuid(os.geteuid()).pw_name
    if actor != current:
        result = _run(
            [
                "sudo",
                "-n",
                "-u",
                actor,
                "/usr/bin/psql",
                f"--dbname={config['postgres_dsn']}",
                "--no-align",
                "--tuples-only",
                "--command",
                _DATABASE_OBSERVATION_SQL,
            ],
            timeout=30,
        )
        if result.returncode:
            raise DoctorError(result.stderr.strip() or "operator database check failed")
        try:
            row = json.loads(result.stdout.strip())
        except json.JSONDecodeError as exc:
            raise DoctorError("operator database check returned invalid JSON") from exc
        if not isinstance(row, dict):
            raise DoctorError("operator database check returned a non-object")
        active = int(row.pop("active_jobs"))
        return row, active
    psycopg2, extras = _postgres_driver()
    with psycopg2.connect(config["postgres_dsn"]) as connection:
        with connection.cursor(cursor_factory=extras.RealDictCursor) as cursor:
            cursor.execute(_DATABASE_OBSERVATION_SQL)
            row = json.loads(cursor.fetchone()["observation_json"])
    active = int(row.pop("active_jobs"))
    return row, active


def check_database(paths: DoctorPaths) -> dict[str, Any]:
    repair = _privileged_repair_action(paths, "database")
    try:
        config = _coding_config(paths)
        actor = _ordinary_coding_probe_actor(paths)
        row, active = _database_observation(config, actor)
        required = (
            "universal_role",
            "database_connect",
            "schema_usage",
            "role_member",
            "todo_access",
            "queue_access",
            "history_access",
            "todo_sequence_access",
            "history_sequence_access",
            "claim_function_access",
            "recovery_function_access",
            "progress_note_column",
        )
        ok = all(row.get(key) is True for key in required)
        return _check(
            "database.local-coding",
            "PASS" if ok else "FAIL",
            f"peer actor {actor} via universal role {row.get('actor')} has {'all' if ok else 'incomplete'} local coding grants; {active} active job(s)",
            evidence={
                **row,
                "active_jobs": active,
                "database": config["postgres_dsn"],
                "unix_actor": actor,
            },
            repair=None if ok else repair,
        )
    except Exception as exc:
        return _failed("database.local-coding", exc, repair=repair)


_PEER_MAP_NAME = "tgw-coders"
_PEER_TARGET_ROLE = "tgw_coding"
_PEER_IDENT_RELATIVE = Path("config/environment/postgresql/pg_ident.conf")
_PEER_HBA_RELATIVE = Path("config/environment/postgresql/pg_hba.conf")
_PEER_AUTH_ENTRY = re.compile(r"^\s*(\S+)\s+(\S+)\s+(\S+)\s*(?:#.*)?$")
_PEER_HBA_LINE = re.compile(r"^local\s+\S+\s+tgw_coding\s+peer\s+map=tgw-coders\s*$")
# A tgw_coding peer line without the map would shadow the managed line under
# pg_hba first-match semantics and must be treated as a conflict.
_PEER_HBA_CONFLICT_LINE = re.compile(r"^local\s+\S+\s+tgw_coding\s+peer\b(?!\s+map=tgw-coders)")


def _canonical_peer_ident_entries(paths: DoctorPaths) -> list[tuple[str, str, str]]:
    """Parse the canonical pg_ident map into (map, system_user, pg_role) rows."""
    source = paths.repository / _PEER_IDENT_RELATIVE
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise DoctorError("canonical pg_ident materialization is unavailable") from exc
    rows: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        match = _PEER_AUTH_ENTRY.match(line)
        if match is None:
            continue
        rows.append((match.group(1), match.group(2), match.group(3)))
    return rows


def _managed_peer_hba_line(paths: DoctorPaths) -> str:
    """Return the managed tgw_coding peer line from the canonical pg_hba."""
    source = paths.repository / _PEER_HBA_RELATIVE
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise DoctorError("canonical pg_hba materialization is unavailable") from exc
    for line in text.splitlines():
        if _PEER_HBA_LINE.match(line):
            return line
    raise DoctorError("canonical pg_hba materialization has no tgw_coding peer line")


def _live_ident_entries(paths: DoctorPaths) -> list[tuple[str, str, str]]:
    path = paths.postgresql_conf_dir / "pg_ident.conf"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DoctorError("installed pg_ident.conf is unreadable") from exc
    rows: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        match = _PEER_AUTH_ENTRY.match(line)
        if match is None:
            continue
        rows.append((match.group(1), match.group(2), match.group(3)))
    return rows


def _ordinary_tgw_coders_members() -> list[str]:
    """Return tgw-coders members that can actually log in (not nologin).

    Service accounts (nologin shells) are not ordinary coding actors and are
    intentionally absent from the peer map; the map must cover every member
    with a real login shell so future harness onboarding updates the
    group/peer mapping rather than creating another PostgreSQL role.
    """
    try:
        group = grp.getgrnam(_PEER_MAP_NAME)
    except KeyError as exc:
        raise DoctorError("tgw-coders group is unavailable for peer verification") from exc
    members: list[str] = []
    for name in group.gr_mem:
        try:
            shell = pwd.getpwnam(name).pw_shell
        except KeyError:
            continue
        if shell not in ("/usr/sbin/nologin", "/sbin/nologin", "/bin/false", "/usr/bin/false"):
            members.append(name)
    return members


def _peer_auth_materialization(paths: DoctorPaths) -> dict[str, Any]:
    """Verify the installed peer-auth host materialization (root-only).

    The unprivileged functional proof is the database.local-coding check
    itself: connecting through the universal DSN as each ordinary actor can
    succeed only when pg_hba ordering and the pg_ident map are correct.  This
    file-state verification additionally guards the root bootstrap/repair path
    and enforces that the canonical map covers every ordinary tgw-coders
    member with a login shell.
    """
    if os.geteuid() != 0:
        return {
            "checked": False,
            "detail": "unprivileged; functional peer-auth proof via database.local-coding",
        }
    canonical = _canonical_peer_ident_entries(paths)
    expected_users = {
        system_user
        for map_name, system_user, _role in canonical
        if map_name == _PEER_MAP_NAME
    }
    live_ident = _live_ident_entries(paths)
    live_map = {
        (map_name, system_user): role
        for map_name, system_user, role in live_ident
        if map_name == _PEER_MAP_NAME
    }
    live_users = {system_user for map_name, system_user in live_map}
    missing = sorted(expected_users - live_users)
    unexpected = sorted(live_users - expected_users)
    wrong = sorted(
        f"{system_user}->{role}"
        for (map_name, system_user), role in live_map.items()
        if role != _PEER_TARGET_ROLE
    )
    unmapped_members = sorted(set(_ordinary_tgw_coders_members()) - expected_users)
    ident_exact = (
        not missing
        and not unexpected
        and not wrong
        and not unmapped_members
        and all(
            live_map.get((map_name, system_user)) == role
            for map_name, system_user, role in canonical
        )
    )
    hba_path = paths.postgresql_conf_dir / "pg_hba.conf"
    try:
        hba_lines = hba_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise DoctorError("installed pg_hba.conf is unreadable") from exc
    managed_line = _managed_peer_hba_line(paths)
    managed_index = next(
        (index for index, line in enumerate(hba_lines) if _PEER_HBA_LINE.match(line)),
        None,
    )
    conflict_index = next(
        (
            index
            for index, line in enumerate(hba_lines)
            if _PEER_HBA_CONFLICT_LINE.match(line)
        ),
        None,
    )
    catch_all_index = next(
        (
            index
            for index, line in enumerate(hba_lines)
            if re.match(r"^local\s+\S+\s+all\s+peer\b", line)
        ),
        None,
    )
    ordered = managed_index is not None and (
        catch_all_index is None or managed_index < catch_all_index
    )
    return {
        "checked": True,
        "pg_ident": {
            "exact": ident_exact,
            "missing": missing,
            "unexpected": unexpected,
            "unmapped_group_members": unmapped_members,
            "wrong_target": wrong,
            "canonical_entries": len(canonical),
            "live_entries": len(live_map),
        },
        "pg_hba": {
            "exact": managed_index is not None and ordered and conflict_index is None,
            "managed_line": managed_line,
            "line_present": managed_index is not None,
            "precedes_catch_all_peer": ordered,
            "conflicting_no_map_line": conflict_index is not None,
        },
    }


def check_database_peer_auth(paths: DoctorPaths) -> dict[str, Any]:
    repair = _privileged_repair_action(paths, "database")
    try:
        materialization = _peer_auth_materialization(paths)
    except Exception as exc:
        return _failed("database.local-coding-peer-auth", exc, repair=repair)
    if not materialization["checked"]:
        return _check(
            "database.local-coding-peer-auth",
            "PASS",
            materialization["detail"],
            evidence=materialization,
        )
    pg_ident = materialization["pg_ident"]
    pg_hba = materialization["pg_hba"]
    exact = pg_ident["exact"] and pg_hba["exact"]
    if exact:
        detail = "universal tgw_coding peer map and pg_hba ordering are exact"
    else:
        parts: list[str] = []
        if not pg_ident["exact"]:
            detail_parts = []
            if pg_ident["missing"]:
                detail_parts.append("missing " + ", ".join(pg_ident["missing"]))
            if pg_ident["unexpected"]:
                detail_parts.append("unexpected " + ", ".join(pg_ident["unexpected"]))
            if pg_ident["unmapped_group_members"]:
                detail_parts.append(
                    "unmapped ordinary members "
                    + ", ".join(pg_ident["unmapped_group_members"])
                )
            if pg_ident["wrong_target"]:
                detail_parts.append("wrong " + ", ".join(pg_ident["wrong_target"]))
            parts.append("pg_ident: " + "; ".join(detail_parts))
        if not pg_hba["exact"]:
            parts.append(
                "pg_hba peer line absent, conflicting no-map peer line present, "
                "or ordered after a catch-all peer line"
            )
        detail = "peer-auth host materialization differs: " + "; ".join(parts)
    return _check(
        "database.local-coding-peer-auth",
        "PASS" if exact else "FAIL",
        detail,
        evidence=materialization,
        repair=None if exact else repair,
    )


def _materialize_peer_auth(
    paths: DoctorPaths,
    *,
    ident_bytes: bytes,
    managed_line: str,
) -> None:
    """Write the universal peer map and the managed pg_hba line idempotently.

    Preserves every unrelated pg_ident map and pg_hba line byte-for-byte; only
    the ``tgw-coders`` map and the managed tgw_coding peer line are managed.
    """
    conf_dir = paths.postgresql_conf_dir
    if conf_dir.is_symlink() or not conf_dir.is_dir():
        raise DoctorError("PostgreSQL configuration directory is unsafe")
    ident_path = conf_dir / "pg_ident.conf"
    hba_path = conf_dir / "pg_hba.conf"
    try:
        postgres_gid = pwd.getpwnam("postgres").pw_gid
    except KeyError as exc:
        raise DoctorError("postgres service account is unavailable") from exc
    # Ownership hardening applies on the real root repair path; an
    # unprivileged invocation (test or misuse) still materializes the bytes.
    privileged = os.geteuid() == 0
    file_uid = 0 if privileged else None
    file_gid = postgres_gid if privileged else None
    try:
        existing_ident = ident_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        existing_ident = []
    canonical_ident_lines = [
        line
        for line in ident_bytes.decode("utf-8").splitlines()
        if _PEER_AUTH_ENTRY.match(line)
    ]
    retained = [
        line
        for line in existing_ident
        if not (
            (match := _PEER_AUTH_ENTRY.match(line))
            and match.group(1) == _PEER_MAP_NAME
        )
    ]
    new_ident = retained + [""] + canonical_ident_lines + [""]
    _atomic_bytes(
        ident_path,
        ("\n".join(new_ident) + "\n").encode("utf-8"),
        mode=0o640,
        uid=file_uid,
        gid=file_gid,
    )
    try:
        existing_hba = hba_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        existing_hba = []
    existing_hba = [
        line
        for line in existing_hba
        if not (
            _PEER_HBA_LINE.match(line) or _PEER_HBA_CONFLICT_LINE.match(line)
        )
    ]
    postgres_index = next(
        (
            index
            for index, line in enumerate(existing_hba)
            if re.match(r"^local\s+\S+\s+postgres\s+peer\b", line)
        ),
        None,
    )
    if postgres_index is not None:
        insert_at = postgres_index + 1
    else:
        insert_at = next(
            (
                index
                for index, line in enumerate(existing_hba)
                if line.lstrip().startswith("local ")
            ),
            0,
        )
    # The managed line must precede any catch-all ``local ... all ... peer``
    # line under pg_hba first-match semantics; clamp the insertion position
    # even when the postgres peer line sits behind an odd catch-all order.
    catch_all_index = next(
        (
            index
            for index, line in enumerate(existing_hba)
            if re.match(r"^local\s+\S+\s+all\s+peer\b", line)
        ),
        None,
    )
    if catch_all_index is not None and insert_at > catch_all_index:
        insert_at = catch_all_index
    existing_hba.insert(insert_at, managed_line)
    _atomic_bytes(
        hba_path,
        ("\n".join(existing_hba) + "\n").encode("utf-8"),
        mode=0o640,
        uid=file_uid,
        gid=file_gid,
    )


def _unit_state(unit: str) -> dict[str, Any]:
    result = _run(
        [
            "systemctl",
            "show",
            unit,
            "--property=LoadState,ActiveState,SubState,FragmentPath,DropInPaths,ExecStart,MainPID,InvocationID,ExecMainStartTimestampMonotonic,NeedDaemonReload",
            "--no-pager",
        ]
    )
    if result.returncode:
        raise DoctorError(result.stderr.strip() or f"cannot inspect {unit}")
    values: dict[str, str] = {"Unit": unit}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return values


def _loaded_exec_identity(raw: str) -> tuple[str, tuple[str, ...]]:
    """Return the exact path and argv represented by systemctl's ExecStart value."""
    value = raw.strip()
    if not value:
        raise DoctorError("loaded ExecStart is empty")
    if "argv[]=" not in value:
        argv = tuple(shlex.split(value))
        if not argv:
            raise DoctorError("loaded ExecStart has no argv")
        return argv[0], argv
    if value.count("argv[]=") != 1:
        raise DoctorError("loaded ExecStart contains multiple commands")
    path_match = re.search(r"(?:^|\{)\s*path=([^;]+?)\s*;", value)
    argv_match = re.search(r"argv\[\]=(.*?)(?=\s*;\s*[A-Za-z_][A-Za-z0-9_]*=)", value)
    if path_match is None or argv_match is None:
        raise DoctorError("loaded ExecStart format is not exact-parseable")
    executable = path_match.group(1).strip()
    argv = tuple(shlex.split(argv_match.group(1).strip()))
    if not executable or not argv:
        raise DoctorError("loaded ExecStart has no command identity")
    return executable, argv


def _unit_definition(
    paths: DoctorPaths,
    unit: str,
    state: Mapping[str, str],
    *,
    desired_commit: str | None = None,
) -> dict[str, Any]:
    if desired_commit is None:
        desired, release, _task = _desired_runtime(paths)
    else:
        if _COMMIT.fullmatch(desired_commit) is None:
            raise DoctorError("coding unit commit override is invalid")
        desired = desired_commit
        release = paths.runtime_root / "releases" / desired
    source = release / "systemd" / unit
    fragment_text = state.get("FragmentPath", "")
    fragment = Path(fragment_text) if fragment_text else None
    reasons: list[str] = []
    if not source.is_file():
        reasons.append("release source missing")
    if fragment is None or not fragment.is_file():
        reasons.append("installed fragment missing")
    elif not source.is_file() or not _unit_destination_exact(paths, fragment, source):
        reasons.append("installed fragment metadata or bytes differ")
    if state.get("DropInPaths"):
        reasons.append("unexpected systemd drop-in")
    if state.get("NeedDaemonReload") != "no":
        reasons.append("systemd has not loaded the installed definition")
    expected_argv = _UNIT_ARGV.get(unit)
    loaded_exec_path: str | None = None
    loaded_exec_argv: tuple[str, ...] | None = None
    if expected_argv:
        try:
            loaded_exec_path, loaded_exec_argv = _loaded_exec_identity(state.get("ExecStart", ""))
        except (DoctorError, ValueError) as exc:
            reasons.append(str(exc))
        else:
            if loaded_exec_path != expected_argv[0] or loaded_exec_argv != expected_argv:
                reasons.append("loaded ExecStart differs")
    process_argv: list[str] | None = None
    if (unit in _ACTIVE_CODING_UNITS or unit == _PLAN_RENDER_UNIT) and unit.endswith(".service"):
        try:
            pid = int(state.get("MainPID", "0"))
        except ValueError:
            pid = 0
        if pid > 0:
            try:
                process_argv = [value.decode(errors="replace") for value in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if value]
            except OSError:
                reasons.append("active process identity is unreadable")
            else:
                if tuple(process_argv) != expected_argv:
                    reasons.append("active process argv differs")
        elif state.get("ActiveState") == "active":
            reasons.append("active service has no MainPID")
    return {
        "exact": not reasons,
        "desired_commit": desired,
        "source": str(source),
        "source_sha256": _file_hash(source) if source.is_file() else None,
        "fragment": fragment_text or None,
        "fragment_sha256": _file_hash(fragment) if fragment is not None and fragment.is_file() else None,
        "drop_ins": state.get("DropInPaths") or None,
        "need_daemon_reload": state.get("NeedDaemonReload"),
        "expected_argv": list(expected_argv) if expected_argv else None,
        "loaded_exec_start": state.get("ExecStart") or None,
        "loaded_exec_path": loaded_exec_path,
        "loaded_exec_argv": list(loaded_exec_argv) if loaded_exec_argv else None,
        "process_argv": process_argv,
        "reasons": reasons,
    }


def _service_process_runtime_identity(
    state: Mapping[str, str],
    release: Path,
    *,
    proc_root: Path = Path("/proc"),
    state_reader: Callable[[str], Mapping[str, str]] | None = None,
    attempts: int = _SERVICE_RUNTIME_PROBE_ATTEMPTS,
) -> dict[str, Any]:
    """Prove one stable systemd invocation has the selected immutable cwd."""
    if attempts < 1:
        raise ValueError("service runtime probe attempts must be positive")
    selected = release.resolve(strict=True)
    unit = str(state.get("Unit", ""))
    read_state = state_reader or _unit_state
    evidence: dict[str, Any] = {
        "unit": unit or None,
        "pid": 0,
        "invocation_id": None,
        "start_timestamp_monotonic": None,
        "selected_release": str(selected),
        "loaded_release": None,
        "status": "UNREADABLE",
        "exact": False,
        "restart_safe": False,
        "attempts": [],
    }
    if not unit:
        evidence["reason"] = "service runtime identity has no unit name"
        return evidence

    def systemd_identity(value: Mapping[str, str]) -> dict[str, str]:
        return {
            "MainPID": str(value.get("MainPID", "")),
            "InvocationID": str(value.get("InvocationID", "")),
            "ExecMainStartTimestampMonotonic": str(
                value.get("ExecMainStartTimestampMonotonic", "")
            ),
        }

    snapshot: Mapping[str, str] = state
    for attempt_number in range(1, attempts + 1):
        before = systemd_identity(snapshot)
        try:
            pid = int(before["MainPID"])
        except ValueError:
            pid = 0
        invocation_id = before["InvocationID"]
        start_timestamp = before["ExecMainStartTimestampMonotonic"]
        evidence.update(
            {
                "pid": pid,
                "invocation_id": invocation_id or None,
                "start_timestamp_monotonic": start_timestamp or None,
            }
        )
        identity_complete = (
            pid > 0
            and _INVOCATION_ID.fullmatch(invocation_id) is not None
            and start_timestamp.isdigit()
            and int(start_timestamp) > 0
        )
        loaded: Path | None = None
        verification: str | None = None
        probe_error: str | None = None
        if identity_complete:
            cwd = proc_root / str(pid) / "cwd"
            try:
                loaded = cwd.resolve(strict=True)
            except OSError as exc:
                # Hardened procfs prevents an ordinary operator from
                # dereferencing the db worker's cwd. Use the existing bounded
                # non-interactive read-only probe, then still re-read systemd's
                # complete process identity before trusting the result.
                result = _run(
                    ["sudo", "-n", "/usr/bin/readlink", "-e", str(cwd)],
                    timeout=5,
                )
                raw = result.stdout.strip()
                if (
                    result.returncode
                    or not raw
                    or "\n" in raw
                    or not Path(raw).is_absolute()
                ):
                    probe_error = (
                        f"loaded process runtime is unreadable: {exc}; exact "
                        "read-only privileged cwd probe is unavailable"
                    )
                else:
                    try:
                        loaded = Path(raw).resolve(strict=True)
                    except OSError as privileged_exc:
                        probe_error = (
                            "loaded process runtime is unreadable after the "
                            f"privileged cwd probe: {privileged_exc}"
                        )
                    else:
                        verification = "privileged-read-only-proc-cwd"
        else:
            probe_error = "active service has incomplete systemd process identity"

        try:
            after_state = dict(read_state(unit))
        except Exception as exc:
            after: dict[str, str] | None = None
            reread_error = str(exc)
        else:
            after_state.setdefault("Unit", unit)
            after = systemd_identity(after_state)
            reread_error = None
        attempt_evidence: dict[str, Any] = {
            "attempt": attempt_number,
            "before": before,
            "after": after,
            "loaded_release": str(loaded) if loaded is not None else None,
        }
        if probe_error:
            attempt_evidence["probe_error"] = probe_error
        if reread_error:
            attempt_evidence["reread_error"] = reread_error
        evidence["attempts"].append(attempt_evidence)
        if after is None:
            evidence["reason"] = (
                "service runtime identity is unreadable after the cwd probe: "
                f"{reread_error}"
            )
            return evidence
        if before != after:
            if attempt_number < attempts:
                snapshot = after_state
                continue
            evidence["status"] = "RACED"
            evidence["reason"] = (
                "service process identity changed during the runtime probe"
            )
            evidence.update(
                {
                    "pid": int(after["MainPID"])
                    if after["MainPID"].isdigit()
                    else 0,
                    "invocation_id": after["InvocationID"] or None,
                    "start_timestamp_monotonic": after[
                        "ExecMainStartTimestampMonotonic"
                    ]
                    or None,
                }
            )
            return evidence
        if probe_error or loaded is None:
            evidence["reason"] = probe_error or "loaded process runtime is unreadable"
            return evidence
        evidence["loaded_release"] = str(loaded)
        if verification:
            evidence["verification"] = verification
        if loaded == selected:
            evidence.update({"status": "EXACT", "exact": True})
        else:
            evidence.update(
                {
                    "status": "STALE",
                    "restart_safe": True,
                    "reason": "loaded process predates selected immutable runtime",
                }
            )
        return evidence
    raise AssertionError("service runtime probe exhausted without evidence")


def _plan_render_process_runtime_identity(
    state: Mapping[str, str],
    release: Path,
    *,
    proc_root: Path = Path("/proc"),
    state_reader: Callable[[str], Mapping[str, str]] | None = None,
    attempts: int = _SERVICE_RUNTIME_PROBE_ATTEMPTS,
) -> dict[str, Any]:
    """Backward-compatible name for the shared service runtime check."""
    return _service_process_runtime_identity(
        state,
        release,
        proc_root=proc_root,
        state_reader=state_reader,
        attempts=attempts,
    )


def check_units(
    paths: DoctorPaths,
    *,
    desired_commit: str | None = None,
    observe_restart_obligations: bool = True,
) -> dict[str, Any]:
    repair = _privileged_repair_action(paths, "workers")
    try:
        observed = {}
        for unit in _CODING_UNITS:
            state = _unit_state(unit)
            state["restart_obligation"] = _restart_obligation_presence(
                paths, unit
            )
            state["definition"] = _unit_definition(
                paths, unit, state, desired_commit=desired_commit
            )
            if unit in _ACTIVE_CODING_UNITS and unit.endswith(".service"):
                desired = state["definition"]["desired_commit"]
                release = paths.runtime_root / "releases" / desired
                state["process_runtime"] = _service_process_runtime_identity(
                    state, release
                )
            observed[unit] = state
        unhealthy = [
            unit
            for unit, state in observed.items()
            if state.get("LoadState") != "loaded"
            or not state["definition"]["exact"]
            or (
                unit in _ACTIVE_CODING_UNITS
                and state.get("ActiveState") != "active"
            )
            or not state.get("process_runtime", {"exact": True})["exact"]
            or (
                observe_restart_obligations
                and state["restart_obligation"]["status"] != "ABSENT"
            )
        ]
        return _check(
            "services.local-coding",
            "PASS" if not unhealthy else "FAIL",
            "all local coding definitions are exact and required units are active" if not unhealthy else "inactive or missing units: " + ", ".join(unhealthy),
            evidence={"units": observed},
            repair=None if not unhealthy else repair,
        )
    except Exception as exc:
        return _check(
            "services.local-coding",
            "UNKNOWN",
            str(exc),
            repair=repair,
        )


def _runtime_selector_identity(paths: DoctorPaths, desired: str, release: Path) -> dict[str, Any]:
    current_link = paths.runtime_root / "current"
    expected_selector = str(Path("releases") / desired)
    installed_selector = os.readlink(current_link) if current_link.is_symlink() else None
    resolved: Path | None = None
    release_resolved: Path | None = None
    try:
        if installed_selector == expected_selector:
            resolved = current_link.resolve(strict=True)
            release_resolved = release.resolve(strict=True)
    except OSError:
        pass
    exact = installed_selector == expected_selector and resolved is not None and resolved == release_resolved
    return {
        "desired": desired,
        "release": str(release),
        "expected_selector": expected_selector,
        "installed_selector": installed_selector,
        "resolved": None if resolved is None else str(resolved),
        "exact": exact,
    }


def _directory_identity(path: Path, *, uid: int, gid: int, mode: int) -> dict[str, Any]:
    expected = {
        "path": str(path),
        "expected_uid": uid,
        "expected_gid": gid,
        "expected_mode": mode,
    }
    if not path.is_absolute() or path.name in ("", ".", ".."):
        return {
            **expected,
            "kind": "unsafe",
            "uid": None,
            "gid": None,
            "mode": None,
            "error": "unsafe absolute path",
            "exact": False,
        }
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    root_descriptor = -1
    descriptor = -1
    try:
        root_descriptor = os.open(path.anchor, flags)
        descriptor = _open_relative_directory(root_descriptor, path.relative_to(path.anchor))
        _verify_bound_directory(path, descriptor)
        observed = os.fstat(descriptor)
    except FileNotFoundError:
        return {
            **expected,
            "kind": "missing",
            "uid": None,
            "gid": None,
            "mode": None,
            "exact": False,
        }
    except (DoctorError, OSError, ValueError) as exc:
        return {
            **expected,
            "kind": "unsafe",
            "uid": None,
            "gid": None,
            "mode": None,
            "error": str(exc),
            "exact": False,
        }
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if root_descriptor >= 0:
            os.close(root_descriptor)
    kind = "directory" if stat.S_ISDIR(observed.st_mode) else "unsafe"
    observed_mode = stat.S_IMODE(observed.st_mode)
    exact = kind == "directory" and observed.st_uid == uid and observed.st_gid == gid and observed_mode == mode
    return {
        **expected,
        "kind": kind,
        "uid": observed.st_uid,
        "gid": observed.st_gid,
        "mode": observed_mode,
        "exact": exact,
    }


def _plan_render_storage_identity(paths: DoctorPaths) -> dict[str, Any]:
    owner = pwd.getpwnam("db")
    group = grp.getgrnam("tgw-coders")
    mode = _PLAN_RENDER_DIRECTORY_MODE
    directories = [_directory_identity(path, uid=owner.pw_uid, gid=group.gr_gid, mode=mode) for path in (paths.plan_render_root, paths.plan_render_log_root)]
    return {
        "owner": "db",
        "owner_uid": owner.pw_uid,
        "group": "tgw-coders",
        "group_gid": group.gr_gid,
        "mode": mode,
        "directories": directories,
        "exact": all(item["exact"] for item in directories),
    }


def check_plan_render_worker(
    paths: DoctorPaths,
    *,
    desired_commit: str | None = None,
    observe_restart_obligations: bool = True,
) -> dict[str, Any]:
    repair = _privileged_repair_action(paths, "plan-render-worker")
    try:
        explicit_commit = desired_commit is not None
        if desired_commit is None:
            desired, release, _task = _desired_runtime(paths)
        else:
            if _COMMIT.fullmatch(desired_commit) is None:
                raise DoctorError("plan_render commit override is invalid")
            desired = desired_commit
            release = paths.runtime_root / "releases" / desired
        runtime = _runtime_selector_identity(paths, desired, release)
        state = _unit_state(_PLAN_RENDER_UNIT)
        definition = (
            _unit_definition(
                paths, _PLAN_RENDER_UNIT, state, desired_commit=desired
            )
            if explicit_commit
            else _unit_definition(paths, _PLAN_RENDER_UNIT, state)
        )
        process_runtime = _plan_render_process_runtime_identity(state, release)
        config = _json(paths.plan_render_config)
        storage = _plan_render_storage_identity(paths)
        restart_obligation = _restart_obligation_presence(
            paths, _PLAN_RENDER_UNIT
        )
        source = release / "config/tgw-plan-render-local.json"
        exact_config = (
            not source.is_symlink()
            and source.is_file()
            and not paths.plan_render_config.is_symlink()
            and paths.plan_render_config.is_file()
            and source.read_bytes() == paths.plan_render_config.read_bytes()
        )
        healthy = (
            state.get("LoadState") == "loaded"
            and state.get("ActiveState") == "active"
            and definition["exact"]
            and process_runtime["exact"]
            and exact_config
            and runtime["exact"]
            and storage["exact"]
            and (
                not observe_restart_obligations
                or restart_obligation["status"] == "ABSENT"
            )
        )
        reasons = list(definition["reasons"])
        if not process_runtime["exact"]:
            reasons.append(process_runtime.get("reason", "loaded process runtime differs"))
        if not exact_config:
            reasons.append("immutable config path or bytes differ")
        if not runtime["exact"]:
            reasons.append("immutable runtime selector differs")
        if not storage["exact"]:
            reasons.append("plan_render output directories differ")
        if state.get("ActiveState") != "active":
            reasons.append("service is not active")
        if (
            observe_restart_obligations
            and restart_obligation["status"] != "ABSENT"
        ):
            reasons.append(
                "durable restart obligation is "
                + restart_obligation["status"].lower()
            )
        return _check(
            "services.plan-render",
            "PASS" if healthy else "FAIL",
            "local plan_render consumer is exact and active" if healthy else "; ".join(reasons),
            evidence={
                "unit": state,
                "definition": definition,
                "process_runtime": process_runtime,
                "config": config,
                "runtime": runtime,
                "storage": storage,
                "restart_obligation": restart_obligation,
            },
            repair=None if healthy else repair,
        )
    except Exception as exc:
        return _failed("services.plan-render", exc, repair=repair)


def _desired_runtime(paths: DoctorPaths) -> tuple[str, Path, dict[str, Any]]:
    task = _json(paths.context_task)
    implementation = task.get("implementation", {}).get("coding_workflow", {})
    desired = implementation.get("commit")
    if not isinstance(desired, str) or _COMMIT.fullmatch(desired) is None:
        raise DoctorError("current task does not declare an exact coding runtime commit")
    release = paths.runtime_root / "releases" / desired
    if not release.is_dir():
        raise DoctorError(f"declared immutable coding release is absent: {release}")
    return desired, release, task


def _git_blob_oid(data: bytes) -> str:
    framed = b"blob " + str(len(data)).encode("ascii") + b"\0" + data
    return hashlib.sha1(framed, usedforsecurity=False).hexdigest()


def _verify_release_tree(paths: DoctorPaths, desired: str, release: Path) -> dict[str, Any]:
    """Verify every released path and mode against the exact Git commit tree."""
    releases_root = (paths.runtime_root / "releases").resolve(strict=True)
    if release.is_symlink() or release.resolve(strict=True).parent != releases_root:
        raise DoctorError("release path escapes the immutable releases directory")
    release_before = release.stat(follow_symlinks=False)
    materializer_uid = (
        pwd.getpwnam("db").pw_uid
        if paths.coding_root_effect_uid is None
        else paths.coding_root_effect_uid
    )
    release_owners = {*paths.trusted_release_owners, materializer_uid}
    if release_before.st_uid not in release_owners or release_before.st_mode & 0o022 or not stat.S_ISDIR(release_before.st_mode):
        raise DoctorError("release root ownership or permissions are not immutable")
    result = subprocess.run(
        protected_git_command(
            paths.repository,
            "ls-tree",
            "-r",
            "-z",
            "--full-tree",
            desired,
        ),
        cwd=paths.repository,
        env=dict(protected_git_environment()),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        raise DoctorError(result.stderr.strip() or "cannot read the declared Git tree")
    expected: dict[str, tuple[str, str]] = {}
    for record in result.stdout.split("\0"):
        if not record:
            continue
        metadata, separator, relative = record.partition("\t")
        fields = metadata.split()
        if not separator or len(fields) != 3 or fields[1] != "blob":
            raise DoctorError("declared runtime tree contains an unsupported Git entry")
        mode, _kind, object_id = fields
        expected[relative] = (mode, object_id)

    release_manifest = release / ".release-manifest.json"
    if release_manifest.exists():
        try:
            from tgw.release_installer import verify as verify_release_manifest

            manifest_verification = verify_release_manifest(paths.runtime_root, desired)
        except Exception as exc:
            raise DoctorError(f"release materializer manifest is invalid: {exc}") from exc
    else:
        manifest_verification = None
    materializer_metadata = {".release-manifest.json", ".runtime-manifest.json"}
    actual = {
        str(path.relative_to(release))
        for path in release.rglob("*")
        if (path.is_file() or path.is_symlink())
        and str(path.relative_to(release)) not in materializer_metadata
    }
    unsafe_paths: list[str] = []
    for path in release.rglob("*"):
        observed = path.stat(follow_symlinks=False)
        relative = str(path.relative_to(release))
        if observed.st_uid not in release_owners:
            unsafe_paths.append(relative + ":owner")
        if not stat.S_ISLNK(observed.st_mode) and observed.st_mode & 0o022:
            unsafe_paths.append(relative + ":writable")
        if stat.S_ISREG(observed.st_mode) and observed.st_nlink != 1:
            unsafe_paths.append(relative + ":link-count")
    missing = sorted(set(expected) - actual)
    extra = sorted(actual - set(expected))
    mismatched: list[str] = []
    for relative, (mode, object_id) in expected.items():
        path = release / relative
        if relative in missing:
            continue
        if mode == "120000":
            if not path.is_symlink():
                mismatched.append(relative + ":mode")
                continue
            data = os.readlink(path).encode()
        else:
            if path.is_symlink() or not path.is_file():
                mismatched.append(relative + ":type")
                continue
            before = path.stat(follow_symlinks=False)
            if before.st_nlink != 1:
                mismatched.append(relative + ":link-count")
                continue
            data = path.read_bytes()
            after = path.stat(follow_symlinks=False)
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_nlink,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_nlink,
            ):
                mismatched.append(relative + ":changed-during-read")
            executable = bool(after.st_mode & 0o111)
            if executable != (mode == "100755"):
                mismatched.append(relative + ":mode")
        if _git_blob_oid(data) != object_id:
            mismatched.append(relative + ":content")
    release_after = release.stat(follow_symlinks=False)
    if (
        release_before.st_dev,
        release_before.st_ino,
        release_before.st_mtime_ns,
    ) != (
        release_after.st_dev,
        release_after.st_ino,
        release_after.st_mtime_ns,
    ):
        mismatched.append(".:changed-during-verification")
    if missing or extra or mismatched or unsafe_paths:
        fragments = []
        if missing:
            fragments.append(f"{len(missing)} missing")
        if extra:
            fragments.append(f"{len(extra)} extra")
        if mismatched:
            fragments.append(f"{len(mismatched)} mismatched")
        if unsafe_paths:
            fragments.append(f"{len(unsafe_paths)} unsafe ownership/mode")
        detail = ", ".join(fragments)
        if unsafe_paths:
            detail += "; unsafe=" + ",".join(unsafe_paths[:8])
        raise DoctorError("release tree differs from Git: " + detail)
    tree = _git(paths.repository, "rev-parse", f"{desired}^{{tree}}")
    return {
        "verified": True,
        "commit": desired,
        "tree": tree,
        "file_count": len(expected),
        "manifest_source": "git-ls-tree",
        "release_materializer": manifest_verification,
        "trusted_owners": sorted(release_owners),
    }


def _promote_bootstrap_release_ownership(
    release: Path, *, uid: int, gid: int
) -> None:
    """Promote verified bytes without chowning a materializer-owned file inode.

    Directory descriptors bind every lookup to the opened release even if a
    pathname is concurrently renamed.  Only the candidate release is frozen;
    the ordinary-user runtime and releases parents are never re-owned or made
    inaccessible.  Each regular file must start single-linked and is replaced
    by a new root-owned inode, so an external hard link can never receive a
    privileged ownership change.
    """

    open_directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    open_file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    allowed_source_uids = {uid, os.geteuid()}
    try:
        allowed_source_uids.add(pwd.getpwnam("db").pw_uid)
    except KeyError:
        pass

    def validate_directory(observed: os.stat_result) -> int:
        mode = stat.S_IMODE(observed.st_mode)
        if (
            not stat.S_ISDIR(observed.st_mode)
            or observed.st_uid not in allowed_source_uids
            or mode != 0o555
        ):
            raise DoctorError("bootstrap release cannot be promoted safely")
        return mode

    def validate_file(observed: os.stat_result) -> int:
        mode = stat.S_IMODE(observed.st_mode)
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_uid not in allowed_source_uids
            or observed.st_nlink != 1
            or mode not in {0o444, 0o555}
        ):
            raise DoctorError("bootstrap release cannot be promoted safely")
        return mode

    def directory_names(descriptor: int) -> list[str]:
        """Read one bound directory through a fresh file description.

        Reusing ``descriptor`` for repeated ``os.listdir`` calls is not
        portable: on some filesystems the duplicated descriptor retains the
        prior directory offset and a stable directory appears empty on the
        second read.  A fresh ``openat('.')`` keeps the lookup bound to the
        already-open inode while giving every comparison a new cursor.
        """

        scan_fd = os.open(".", open_directory_flags, dir_fd=descriptor)
        try:
            held = os.fstat(descriptor)
            scanned = os.fstat(scan_fd)
            if (held.st_dev, held.st_ino) != (scanned.st_dev, scanned.st_ino):
                raise DoctorError(
                    "bootstrap release changed during ownership promotion"
                )
            return sorted(os.listdir(scan_fd))
        finally:
            os.close(scan_fd)

    def promote_file(parent_fd: int, name: str, expected: os.stat_result) -> None:
        source_fd = os.open(name, open_file_flags, dir_fd=parent_fd)
        temporary = f".tgw-root-promote-{secrets.token_hex(16)}"
        target_fd: int | None = None
        try:
            before = os.fstat(source_fd)
            if (before.st_dev, before.st_ino) != (expected.st_dev, expected.st_ino):
                raise DoctorError("bootstrap release changed during ownership promotion")
            mode = validate_file(before)
            target_fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=parent_fd,
            )
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                view = memoryview(chunk)
                while view:
                    written = os.write(target_fd, view)
                    if written <= 0:
                        raise DoctorError(
                            "short write during bootstrap ownership promotion"
                        )
                    view = view[written:]
            after = os.fstat(source_fd)
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_nlink,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_nlink,
            ):
                raise DoctorError("bootstrap release changed during ownership promotion")
            os.fchown(target_fd, uid, gid)
            os.fchmod(target_fd, mode)
            os.fsync(target_fd)
            os.close(target_fd)
            target_fd = None
            os.replace(
                temporary,
                name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            promoted = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(promoted.st_mode)
                or promoted.st_uid != uid
                or promoted.st_gid != gid
                or promoted.st_nlink != 1
                or stat.S_IMODE(promoted.st_mode) != mode
            ):
                raise DoctorError("bootstrap release ownership promotion is incomplete")
        finally:
            if target_fd is not None:
                os.close(target_fd)
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            os.close(source_fd)

    def promote_directory(descriptor: int) -> None:
        observed = os.fstat(descriptor)
        mode = validate_directory(observed)
        os.fchown(descriptor, uid, gid)
        # Once descriptor-bound ownership is fixed, make the directory
        # temporarily owner-private so replacement inodes can be published
        # without granting the materializer any writable namespace.
        os.fchmod(descriptor, 0o700)
        try:
            names = directory_names(descriptor)
            for name in names:
                child = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if stat.S_ISDIR(child.st_mode):
                    child_fd = os.open(name, open_directory_flags, dir_fd=descriptor)
                    try:
                        bound = os.fstat(child_fd)
                        if (bound.st_dev, bound.st_ino) != (child.st_dev, child.st_ino):
                            raise DoctorError(
                                "bootstrap release changed during ownership promotion"
                            )
                        promote_directory(child_fd)
                    finally:
                        os.close(child_fd)
                elif stat.S_ISREG(child.st_mode):
                    promote_file(descriptor, name, child)
                else:
                    raise DoctorError("bootstrap release cannot be promoted safely")
            if directory_names(descriptor) != names:
                raise DoctorError("bootstrap release changed during ownership promotion")
        finally:
            os.fchmod(descriptor, mode)
        final = os.fstat(descriptor)
        if (
            final.st_uid != uid
            or final.st_gid != gid
            or stat.S_IMODE(final.st_mode) != mode
        ):
            raise DoctorError("bootstrap release ownership promotion is incomplete")
        os.fsync(descriptor)

    releases = release.parent
    runtime_root = releases.parent
    if releases.name != "releases" or release.name in {"", ".", ".."}:
        raise DoctorError("bootstrap release cannot be promoted safely")
    runtime_parent = runtime_root.parent.stat(follow_symlinks=False)
    if (
        runtime_root.parent.is_symlink()
        or not stat.S_ISDIR(runtime_parent.st_mode)
        or runtime_parent.st_uid != os.geteuid()
        or runtime_parent.st_mode & 0o022
    ):
        raise DoctorError("bootstrap runtime parent is not trusted")

    runtime_fd: int | None = None
    releases_fd: int | None = None
    release_fd: int | None = None
    try:
        runtime_fd = os.open(runtime_root, open_directory_flags)
        runtime_original = os.fstat(runtime_fd)
        if (
            not stat.S_ISDIR(runtime_original.st_mode)
            or runtime_original.st_uid not in allowed_source_uids
            or runtime_original.st_mode & 0o022
        ):
            raise DoctorError("bootstrap runtime root is unsafe")

        releases_expected = os.stat(
            "releases", dir_fd=runtime_fd, follow_symlinks=False
        )
        releases_fd = os.open("releases", open_directory_flags, dir_fd=runtime_fd)
        releases_original = os.fstat(releases_fd)
        if (
            (releases_original.st_dev, releases_original.st_ino)
            != (releases_expected.st_dev, releases_expected.st_ino)
            or not stat.S_ISDIR(releases_original.st_mode)
            or releases_original.st_uid not in allowed_source_uids
            or releases_original.st_mode & 0o022
        ):
            raise DoctorError("bootstrap releases root is unsafe")

        release_expected = os.stat(
            release.name, dir_fd=releases_fd, follow_symlinks=False
        )
        release_fd = os.open(release.name, open_directory_flags, dir_fd=releases_fd)
        release_bound = os.fstat(release_fd)
        if (release_bound.st_dev, release_bound.st_ino) != (
            release_expected.st_dev,
            release_expected.st_ino,
        ):
            raise DoctorError("bootstrap release changed before ownership promotion")
        promote_directory(release_fd)
        release_final = os.stat(
            release.name, dir_fd=releases_fd, follow_symlinks=False
        )
        if (release_final.st_dev, release_final.st_ino) != (
            release_bound.st_dev,
            release_bound.st_ino,
        ):
            raise DoctorError("bootstrap release changed during ownership promotion")
        os.fsync(releases_fd)
    except OSError as exc:
        raise DoctorError("bootstrap release ownership promotion failed") from exc
    finally:
        if release_fd is not None:
            os.close(release_fd)
        if releases_fd is not None:
            os.close(releases_fd)
        if runtime_fd is not None:
            os.close(runtime_fd)


def _launcher_links(paths: DoctorPaths) -> dict[Path, Path]:
    current = paths.runtime_root / "current/bin"
    return {
        paths.local_bin / "tgw-todo": current / "tgw-todo-local-operator",
        paths.local_bin / "tgw-coding": current / "tgw-coding-local-operator",
        paths.local_bin / "tgw-coding-mcp": current / "tgw-coding-mcp",
        paths.local_bin / "tgw-doctor": current / "tgw-doctor",
    }


def _fixed_root_launcher_identity(
    paths: DoctorPaths, destination: Path, source: Path
) -> dict[str, Any]:
    expected_hash = _file_hash(source) if source.is_file() else None
    try:
        observed = destination.stat(follow_symlinks=False)
        installed_hash = (
            _file_hash(destination)
            if stat.S_ISREG(observed.st_mode) and not destination.is_symlink()
            else None
        )
    except FileNotFoundError:
        observed = None
        installed_hash = None
    exact = bool(
        expected_hash is not None
        and observed is not None
        and stat.S_ISREG(observed.st_mode)
        and not destination.is_symlink()
        and observed.st_uid == paths.context_install_uid
        and observed.st_gid == paths.context_install_gid
        and stat.S_IMODE(observed.st_mode) == 0o555
        and observed.st_nlink == 1
        and installed_hash == expected_hash
    )
    return {
        "exact": exact,
        "destination": str(destination),
        "source": str(source),
        "source_sha256": expected_hash,
        "installed_sha256": installed_hash,
        "uid": observed.st_uid if observed is not None else None,
        "gid": observed.st_gid if observed is not None else None,
        "mode": stat.S_IMODE(observed.st_mode) if observed is not None else None,
        "link_count": observed.st_nlink if observed is not None else None,
        "kind": (
            "regular"
            if observed is not None and stat.S_ISREG(observed.st_mode)
            else "missing-or-unsupported"
        ),
    }


def _operator_launcher_identity(paths: DoctorPaths, source: Path) -> dict[str, Any]:
    return _fixed_root_launcher_identity(paths, paths.operator_cli, source)


def check_runtime(paths: DoctorPaths) -> dict[str, Any]:
    repair = _privileged_repair_action(paths, "runtime")
    try:
        desired, release, _task = _desired_runtime(paths)
        current_link = paths.runtime_root / "current"
        current = current_link.resolve(strict=True)
        head, _tree, _status = _source_identity(paths)
        mismatches: list[str] = []
        expected_selector = str(Path("releases") / desired)
        installed_selector = os.readlink(current_link) if current_link.is_symlink() else None
        if installed_selector != expected_selector:
            mismatches.append("current selector trust")
        if current != release.resolve():
            mismatches.append("current runtime selector")
        if desired != head:
            mismatches.append("task/runtime versus canonical source")
        release_tree = _verify_release_tree(paths, desired, release)
        launchers = _launcher_links(paths)
        hashes: dict[str, Any] = {}
        launcher_surface_drift = False
        for destination, target in launchers.items():
            destination_text = str(destination)
            source = release / "bin" / target.name
            if not source.is_file():
                mismatches.append(f"release source missing: {source.name}")
                launcher_surface_drift = True
                continue
            source_hash = _file_hash(source)
            destination_hash = _file_hash(destination) if destination.is_file() else None
            hashes[destination_text] = {
                "source": str(source),
                "source_sha256": source_hash,
                "installed_sha256": destination_hash,
                "expected_link": str(target),
                "installed_link": os.readlink(destination) if destination.is_symlink() else None,
            }
            if not destination.is_symlink() or os.readlink(destination) != str(target):
                mismatches.append(str(destination))
                launcher_surface_drift = True
            elif destination_hash != source_hash:
                mismatches.append(f"{destination} via current selector")
        operator_source = release / "bin/tgw-operator"
        operator = _operator_launcher_identity(paths, operator_source)
        hashes[str(paths.operator_cli)] = operator
        if not operator["exact"]:
            mismatches.append(f"{paths.operator_cli} fixed privileged boundary")
            launcher_surface_drift = True
        bootstrap_source = release / "bin/tgw-coding-bootstrap"
        bootstrap = _fixed_root_launcher_identity(
            paths, paths.coding_bootstrap, bootstrap_source
        )
        hashes[str(paths.coding_bootstrap)] = bootstrap
        if not bootstrap["exact"]:
            mismatches.append(f"{paths.coding_bootstrap} fixed privileged boundary")
            launcher_surface_drift = True
        config_text = paths.coding_config.read_text(encoding="utf-8").lower()
        forbidden = [item for item in _FORBIDDEN_CODING_DEPENDENCIES if item in config_text]
        if forbidden:
            mismatches.append("forbidden dependencies: " + ", ".join(forbidden))
        return _check(
            "runtime.local-coding",
            "PASS" if not mismatches else "FAIL",
            f"runtime {desired[:12]} and local launchers are exact" if not mismatches else "runtime drift: " + "; ".join(mismatches),
            evidence={
                "desired_commit": desired,
                "canonical_commit": head,
                "release": str(release),
                "current": str(current),
                "expected_selector": expected_selector,
                "installed_selector": installed_selector,
                "launcher_hashes": hashes,
                "release_tree": release_tree,
                "forbidden_dependencies": forbidden,
            },
            repair=None if not mismatches else ("install the exact fixed launcher links during a bounded local bootstrap" if launcher_surface_drift else repair),
        )
    except Exception as exc:
        message = str(exc)
        bootstrap = any(
            fragment in message
            for fragment in (
                "release root ownership",
                "release path escapes",
                "release tree differs",
                "declared immutable coding release is absent",
            )
        )
        return _failed(
            "runtime.local-coding",
            exc,
            repair=("materialize the exact root-protected release during bounded local bootstrap" if bootstrap else repair),
        )


_OBSOLETE_FILE_HASHES = {
    "tgw-foreman": "sha256:10152bcc0c7c72555a630d662e736ee827dc3edb5e3f3a0ad78ecf5b450d6332",
    "tgw-foreman-dispatch": "sha256:61fa8586dfc655685bfece1cbd71b7deed357d23833177bf9d0b6158825f66c5",
    "tgw-context-mcp-candidate-3fe54df8": "sha256:722dcfecebb23ee2dd71d8bfcf923a5275b8089edd03d47e632c51417bfc8699",
    "tgw-context-mcp-candidate-408ee56c": "sha256:3e9f57ad0a60597595e158dde60c36f8857e03b9902f0106332975fab5db4db6",
    "tgw-context-mcp-candidate-6813c302": "sha256:a1a1d637414e4afa881af03d0d0574e44733985a13b33f4acfff3bc443923e5b",
    "tgw-context-mcp-candidate-6865ce87": "sha256:9821db48b5205bbc06ccb6bd32697fbf25532c81b3292ddb5e0bae78fcda6009",
}
_OBSOLETE_ACTOR_TARGET = "/opt/TGW/tgw-lib/actor-runtime/releases/w18-9634e8a7-20260822/scripts/tgw_actor_startup.py"
_UNBOUND_OBSOLETE_NAMES = ("tgw-coding", "tgw-coding-helper")


def _lexists(path: Path) -> bool:
    try:
        return path.exists() or path.is_symlink()
    except OSError:
        # An unreadable parent (for example another actor's home directory)
        # makes the surface unobservable for this ordinary user.  Treat it as
        # absent here so the ordinary-user diagnosis completes; root repairs
        # still observe the full truth.
        return False


def _declared_obsolete_surfaces(paths: DoctorPaths) -> list[dict[str, Any]]:
    actor_home = paths.cleanup_actor_home
    rows = [
        {
            "path": paths.cleanup_system_bin / name,
            "kind": "file",
            "declared_sha256": _OBSOLETE_FILE_HASHES[name],
        }
        for name in ("tgw-foreman", "tgw-foreman-dispatch")
    ]
    rows.append(
        {
            "path": actor_home / ".local/bin/tgw-actor",
            "kind": "symlink",
            "declared_target": _OBSOLETE_ACTOR_TARGET,
        }
    )
    candidate_names = sorted(name for name in _OBSOLETE_FILE_HASHES if name.startswith("tgw-context-mcp-candidate-"))
    for name in candidate_names:
        rows.append(
            {
                "path": paths.local_bin / name,
                "kind": "file",
                "declared_sha256": _OBSOLETE_FILE_HASHES.get(name),
            }
        )
    return rows


def _unbound_obsolete_surfaces(paths: DoctorPaths) -> list[str]:
    unbound = [str(path) for name in _UNBOUND_OBSOLETE_NAMES if _lexists(path := paths.cleanup_system_bin / name)]
    declared_candidates = {name for name in _OBSOLETE_FILE_HASHES if name.startswith("tgw-context-mcp-candidate-")}
    if paths.local_bin.is_dir():
        unbound.extend(str(path) for path in paths.local_bin.glob("tgw-context-mcp-candidate-*") if path.name not in declared_candidates)
    return sorted(unbound)


def _surface_observation(item: Mapping[str, Any]) -> dict[str, Any]:
    path = Path(item["path"])
    row = {"path": str(path), "kind": item["kind"]}
    if path.is_symlink():
        row["target"] = os.readlink(path)
    elif path.is_file():
        row["sha256"] = _file_hash(path)
    else:
        row["observed_type"] = "unsupported"
    if "declared_sha256" in item:
        row["declared_sha256"] = item["declared_sha256"]
    if "declared_target" in item:
        row["declared_target"] = item["declared_target"]
    return row


def _surface_matches_declaration(item: Mapping[str, Any], observation: Mapping[str, Any]) -> bool:
    if item["kind"] == "symlink":
        return observation.get("target") == item.get("declared_target")
    return isinstance(item.get("declared_sha256"), str) and observation.get("sha256") == item["declared_sha256"]


def check_obsolete_surfaces(paths: DoctorPaths) -> dict[str, Any]:
    declared = _declared_obsolete_surfaces(paths)
    present = [item for item in declared if _lexists(item["path"])]
    visible = [_surface_observation(item) for item in present]
    mismatched = [observation for item, observation in zip(present, visible, strict=True) if not _surface_matches_declaration(item, observation)]
    unbound = _unbound_obsolete_surfaces(paths)
    if mismatched or unbound:
        state = "FAIL"
        detail = "obsolete active surfaces are not bound to the exact cleanup treatment"
        repair = None
    elif visible:
        state = "WARN"
        detail = "verified obsolete active surfaces remain: " + ", ".join(item["path"] for item in visible)
        repair = _privileged_repair_action(paths, "obsolete-surfaces")
    else:
        state = "PASS"
        detail = "no declared obsolete active surface remains"
        repair = None
    return _check(
        "cleanup.obsolete-active-surfaces",
        state,
        detail,
        evidence={
            "visible": visible,
            "mismatched": mismatched,
            "unbound": unbound,
            "declared_count": len(declared),
        },
        repair=repair,
    )


def diagnose(paths: DoctorPaths = DoctorPaths()) -> dict[str, Any]:
    checks = [
        check_host(paths),
        check_source(paths),
        check_context_snapshot(paths),
        check_context_launcher(paths),
        check_context_processes(paths),
        check_unix_access(paths),
        check_worktrees(paths),
        check_database(paths),
        check_database_peer_auth(paths),
        check_units(paths),
        check_plan_render_worker(paths),
        check_runtime(paths),
        check_obsolete_surfaces(paths),
    ]
    counts = {state: sum(item["state"] == state for item in checks) for state in _STATES}
    if counts["FAIL"]:
        state = "FAILED"
        exit_code = 2
    elif counts["WARN"] or counts["UNKNOWN"] or counts["RESTART_REQUIRED"]:
        state = "ATTENTION"
        exit_code = 1
    else:
        state = "HEALTHY"
        exit_code = 0
    return {
        "schema": "tgw-local-doctor/v1",
        "ok": state != "FAILED",
        "state": state,
        "host": socket.gethostname(),
        "actor": pwd.getpwuid(os.geteuid()).pw_name,
        "observed_at": datetime.now(UTC).isoformat(),
        "checks": checks,
        "counts": counts,
        "exit_code": exit_code,
        "boundaries": {
            "read_only_default": True,
            "production_effects": False,
            "business_data_effects": False,
            "provider_effects": False,
            "plan_intent_mutation": False,
        },
    }


def condition_policy_census(cache_path: Path) -> dict[str, Any]:
    """Read-only census of an observed eBay condition-policy cache."""
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        policies = raw["policies"]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DoctorError(f"condition policy cache is unavailable or invalid: {exc}") from exc
    if not isinstance(policies, dict):
        raise DoctorError("condition policy cache policies must be an object")
    flags = raw.get("item_condition_required")
    flags = flags if isinstance(flags, dict) else {}
    sets = sorted({tuple(sorted({str(row[0]) for row in rows if isinstance(row, list) and row})) for rows in policies.values() if isinstance(rows, list)})
    covered = sum(isinstance(flags.get(str(category)), bool) for category in policies)
    return {"schema": "tgw-doctor-ebay-condition-policy-census/v1", "cache_path": str(cache_path),
            "category_count": len(policies), "required_flag_coverage": covered,
            "required_flag_missing_or_invalid": len(policies) - covered,
            "expected_distinct_condition_id_sets": 26, "actual_distinct_condition_id_sets": len(sets),
            "condition_id_sets": [list(values) for values in sets], "drift": len(sets) != 26, "read_only": True}


def _require_root() -> None:
    if os.geteuid() != 0:
        raise DoctorError("repair requires the operator to rerun this exact command with sudo -n")


def _require_trusted_root_program(path: Path, trusted_owners: Sequence[int] = (0, 65534)) -> None:
    try:
        resolved = path.resolve(strict=True)
        state = resolved.stat()
    except OSError as exc:
        raise DoctorError(f"trusted repair program is unavailable: {path}") from exc
    if not resolved.is_file() or state.st_uid not in trusted_owners or state.st_mode & 0o022 or not os.access(resolved, os.X_OK):
        raise DoctorError(f"repair program is not trusted-owner immutable: {resolved}")


def _require_trusted_context_runtime(paths: DoctorPaths) -> Path:
    selected = _selected_context_artifacts(paths)
    return selected["runtime_source"]


@contextmanager
def _repair_lock(paths: DoctorPaths):
    paths.receipts.mkdir(parents=True, exist_ok=True)
    lock_path = paths.receipts / ".repair.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DoctorError("another TGW doctor repair is already running") from exc
        yield
    finally:
        os.close(descriptor)


@contextmanager
def _runtime_selector_lock(paths: DoctorPaths):
    """Share the release materializer's selector lock during dependent repairs."""
    operations = paths.runtime_root / "operations"
    directory = os.open(
        operations,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    descriptor = -1
    try:
        directory_state = os.fstat(directory)
        materializer_uid = (
            pwd.getpwnam("db").pw_uid
            if paths.coding_root_effect_uid is None
            else paths.coding_root_effect_uid
        )
        if (
            not stat.S_ISDIR(directory_state.st_mode)
            or directory_state.st_uid not in {materializer_uid, os.geteuid()}
            or directory_state.st_mode & 0o022
        ):
            raise DoctorError("runtime selector lock directory is unsafe")
        expected = os.stat(
            ".selector.lock", dir_fd=directory, follow_symlinks=False
        )
        descriptor = os.open(
            ".selector.lock",
            os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=directory,
        )
        observed = os.fstat(descriptor)
        if (
            (expected.st_dev, expected.st_ino) != (observed.st_dev, observed.st_ino)
            or not stat.S_ISREG(observed.st_mode)
            or observed.st_nlink != 1
            or observed.st_uid not in {materializer_uid, os.geteuid()}
            or stat.S_IMODE(observed.st_mode) != 0o600
        ):
            raise DoctorError("runtime selector lock is unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        final = os.stat(
            ".selector.lock", dir_fd=directory, follow_symlinks=False
        )
        if (final.st_dev, final.st_ino) != (observed.st_dev, observed.st_ino):
            raise DoctorError("runtime selector lock changed while acquiring it")
        yield
    except FileNotFoundError as exc:
        raise DoctorError("runtime selector lock is not initialized") from exc
    finally:
        if descriptor >= 0:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
        os.close(directory)


def _atomic_bytes(
    path: Path,
    value: bytes,
    *,
    mode: int,
    uid: int | None = None,
    gid: int | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_text = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    temporary = Path(temporary_text)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            if uid is not None or gid is not None:
                os.fchown(
                    stream.fileno(),
                    -1 if uid is None else uid,
                    -1 if gid is None else gid,
                )
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _surface_snapshot_at(directory_descriptor: int, name: str) -> dict[str, Any]:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=directory_descriptor,
        )
    except FileNotFoundError:
        return {"kind": "missing"}
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise DoctorError(f"transaction surface is not a regular file: {name}")
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        fields = (
            "st_dev",
            "st_ino",
            "st_size",
            "st_nlink",
            "st_mtime_ns",
            "st_ctime_ns",
            "st_mode",
            "st_uid",
            "st_gid",
        )
        if any(getattr(before, field) != getattr(after, field) for field in fields):
            raise DoctorError(f"transaction surface changed during read: {name}")
        return {
            "kind": "file",
            "raw": raw,
            "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
            "uid": after.st_uid,
            "gid": after.st_gid,
            "mode": stat.S_IMODE(after.st_mode),
            "device": after.st_dev,
            "inode": after.st_ino,
            "size": after.st_size,
            "nlink": after.st_nlink,
            "mtime_ns": after.st_mtime_ns,
            "ctime_ns": after.st_ctime_ns,
        }
    finally:
        os.close(descriptor)


def _same_regular_surface(observed: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    """Compare a file across rename-exchange while allowing rename ctime updates."""
    keys = (
        "kind",
        "raw",
        "sha256",
        "uid",
        "gid",
        "mode",
        "device",
        "inode",
        "size",
        "nlink",
        "mtime_ns",
    )
    return all(observed.get(key) == expected.get(key) for key in keys)


def _cas_regular_file(
    path: Path,
    expected: Mapping[str, Any],
    replacement: bytes,
    *,
    mode: int,
    uid: int,
    gid: int,
) -> dict[str, Any]:
    """Atomically replace one exact inode or restore it without lost updates."""
    if expected.get("kind") != "file" or path.name in {"", ".", ".."}:
        raise DoctorError(f"CAS requires an exact regular-file surface: {path}")
    parent = os.open(
        path.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    temporary = f".{path.name}.tgw-cas-{os.getpid()}-{secrets.token_hex(8)}"
    staged_descriptor = -1
    exchanged = False
    staged: dict[str, Any] | None = None
    try:
        staged_descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            mode,
            dir_fd=parent,
        )
        view = memoryview(replacement)
        while view:
            written = os.write(staged_descriptor, view)
            if written <= 0:
                raise DoctorError(f"short write while staging Context CAS: {path}")
            view = view[written:]
        os.fchown(staged_descriptor, uid, gid)
        os.fchmod(staged_descriptor, mode)
        os.fsync(staged_descriptor)
        os.close(staged_descriptor)
        staged_descriptor = -1
        staged = _surface_snapshot_at(parent, temporary)
        _rename_exchange(parent, temporary, path.name)
        exchanged = True
        displaced = _surface_snapshot_at(parent, temporary)
        current = _surface_snapshot_at(parent, path.name)
        if not _same_regular_surface(displaced, expected) or not _same_regular_surface(current, staged):
            if _same_regular_surface(current, staged):
                _rename_exchange(parent, temporary, path.name)
                exchanged = False
                restored = _surface_snapshot_at(parent, path.name)
                if not _same_regular_surface(restored, expected):
                    raise DoctorError(f"Context CAS could not restore a concurrent change: {path}")
                os.fsync(parent)
            raise DoctorError(f"Context CAS detected a concurrent change: {path}")
        os.fsync(parent)
        os.unlink(temporary, dir_fd=parent)
        exchanged = False
        return current
    except Exception as exc:
        if exchanged:
            try:
                current = _surface_snapshot_at(parent, path.name)
                displaced = _surface_snapshot_at(parent, temporary)
                if not _same_regular_surface(current, staged) or not _same_regular_surface(displaced, expected):
                    raise DoctorError(f"Context CAS rollback refused a concurrent change: {path}")
                _rename_exchange(parent, temporary, path.name)
                exchanged = False
                restored = _surface_snapshot_at(parent, path.name)
                if not _same_regular_surface(restored, expected):
                    raise DoctorError(f"Context CAS rollback did not restore the expected file: {path}")
                os.fsync(parent)
            except Exception as rollback_exc:
                raise DoctorError(f"Context CAS failed: {exc}; rollback failed: {rollback_exc}") from exc
        raise
    finally:
        if staged_descriptor >= 0:
            os.close(staged_descriptor)
        if not exchanged:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass
        os.close(parent)


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, indent=2, sort_keys=True).encode() + b"\n"


def _json_from_surface(path: Path, surface: Mapping[str, Any]) -> dict[str, Any]:
    if surface.get("kind") != "file":
        raise DoctorError(f"Context transaction input is not a regular file: {path}")
    try:
        value = json.loads(surface["raw"])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DoctorError(f"cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DoctorError(f"JSON root must be an object: {path}")
    return value


def _atomic_json(path: Path, value: Mapping[str, Any], *, mode: int = 0o444) -> None:
    _atomic_bytes(path, _json_bytes(value), mode=mode)


def _receipt(paths: DoctorPaths, operation: str, before: Any, after: Any) -> str:
    paths.receipts.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    body = {
        "schema": "tgw-local-doctor-repair-receipt/v1",
        "operation": operation,
        "performed_at": now.isoformat(),
        "actor": pwd.getpwuid(os.geteuid()).pw_name,
        "before": before,
        "after": after,
    }
    body["receipt_sha256"] = _hash(body)
    path = paths.receipts / f"{now.strftime('%Y%m%dT%H%M%S%fZ')}-{operation}.json"
    expected = _json_bytes(body)
    if _surface_snapshot(path).get("kind") != "missing":
        raise DoctorError(f"Doctor receipt destination already exists: {path}")
    try:
        _atomic_json(path, body)
    except Exception as exc:
        cleanup_error: Exception | None = None
        try:
            published = _surface_snapshot(path)
            if published.get("kind") != "missing":
                if (
                    published.get("kind") != "file"
                    or published.get("nlink") != 1
                    or published.get("raw") != expected
                ):
                    raise DoctorError(
                        "failed Doctor receipt destination changed concurrently"
                    )
                path.unlink()
                directory = os.open(
                    path.parent,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                )
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        except Exception as cleanup_exc:
            cleanup_error = cleanup_exc
        if cleanup_error is not None:
            raise _ReceiptPublicationAmbiguous(
                path,
                "Doctor success receipt publication is ambiguous after "
                f"write failure ({exc}) and cleanup failure ({cleanup_error})",
            ) from exc
        raise
    return str(path)


def _remove_exact_receipt(
    path: Path,
    expected: Mapping[str, Any],
) -> None:
    """Durably remove only the exact success receipt just published here."""

    current = _surface_snapshot(path)
    if current != expected or current.get("kind") != "file":
        raise DoctorError("Context success receipt changed before rollback cleanup")
    parent = os.open(
        path.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    try:
        os.unlink(path.name, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)
    if _surface_snapshot(path).get("kind") != "missing":
        raise DoctorError("Context success receipt remains after rollback cleanup")


def _context_artifact_signature(selected: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "commit": selected["commit"],
        "tree": selected["release_tree"]["tree"],
        "hashes": dict(selected["hashes"]),
    }


def _launcher_surface_exact(
    surface: Mapping[str, Any],
    expected: bytes,
    paths: DoctorPaths,
) -> bool:
    return (
        surface.get("kind") == "file"
        and surface.get("raw") == expected
        and surface.get("uid") == paths.context_install_uid
        and surface.get("gid") == paths.context_install_gid
        and surface.get("mode") == paths.context_launcher_mode
    )


def _selected_context_probe_expected(
    paths: DoctorPaths,
    selected: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind the live snapshot with the parser from the selected candidate."""
    snapshot_raw = paths.context_snapshot.read_bytes()
    try:
        snapshot_value = json.loads(snapshot_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DoctorError("current Context snapshot is not valid UTF-8 JSON") from exc
    return _validate_snapshot(
        snapshot_value,
        snapshot_raw,
        parser_path=selected["modules"]["current_context_snapshot"],
    )


def _rollback_context_generation_pointer(
    paths: DoctorPaths,
    selected_generation: Path,
    selected_pointer: Mapping[str, Any],
    prior_pointer: Mapping[str, Any],
    *,
    retained_prior_pointer: Path,
    pointer_parent_descriptor: int,
    selected_pointer_descriptor: int,
    prior_pointer_descriptor: int,
) -> None:
    """CAS-restore the exact displaced pointer retained by the cutover."""
    if prior_pointer.get("kind") != "symlink":
        raise DoctorError("post-cutover rollback has no captured prior generation pointer")
    expected_target = str(Path("generations") / selected_generation.name)
    if retained_prior_pointer.parent != paths.context_generation_pointer.parent:
        raise DoctorError("post-cutover rollback pointer is outside the pointer directory")
    parent = os.dup(pointer_parent_descriptor)
    quarantine = (
        f".{retained_prior_pointer.name}.rollback-"
        f"{os.getpid()}-{secrets.token_hex(8)}"
    )
    quarantine_descriptor = -1
    isolated = False
    try:
        if not _directory_descriptor_matches_path(
            parent, paths.context_generation_pointer.parent
        ):
            raise DoctorError(
                "post-cutover rollback pointer directory changed concurrently"
            )
        live_pointer = _link_surface_at(
            parent, paths.context_generation_pointer.name
        )
        retained_pointer = _link_surface_at(parent, retained_prior_pointer.name)
        if (
            selected_pointer.get("kind") != "symlink"
            or selected_pointer.get("target") != expected_target
            or not _same_link_identity(live_pointer, selected_pointer)
            or not _descriptor_matches_link_surface(
                selected_pointer_descriptor, live_pointer
            )
            or not _same_link_identity(retained_pointer, prior_pointer)
            or not _descriptor_matches_link_surface(
                prior_pointer_descriptor, retained_pointer
            )
        ):
            raise DoctorError(
                "post-cutover rollback refused a concurrent pointer change"
            )
        os.mkdir(quarantine, mode=0o700, dir_fd=parent)
        quarantine_descriptor = os.open(
            quarantine,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent,
        )
        _rename_noreplace_at(
            parent,
            retained_prior_pointer.name,
            quarantine_descriptor,
            "bound-link",
        )
        isolated = True
        quarantined_prior = _link_surface_at(quarantine_descriptor, "bound-link")
        if (
            not _same_link_identity(quarantined_prior, prior_pointer)
            or not _descriptor_matches_link_surface(
                prior_pointer_descriptor, prior_pointer
            )
        ):
            if _link_surface_at(parent, retained_prior_pointer.name).get(
                "kind"
            ) == "missing":
                _rename_noreplace_at(
                    quarantine_descriptor,
                    "bound-link",
                    parent,
                    retained_prior_pointer.name,
                )
                isolated = False
                os.fsync(parent)
            raise DoctorError(
                "post-cutover rollback retained pointer changed before isolation"
            )
        visible_selected = _link_surface_at(
            parent, paths.context_generation_pointer.name
        )
        if (
            not _same_link_identity(visible_selected, selected_pointer)
            or not _descriptor_matches_link_surface(
                selected_pointer_descriptor, selected_pointer
            )
        ):
            _rename_noreplace_at(
                quarantine_descriptor,
                "bound-link",
                parent,
                retained_prior_pointer.name,
            )
            isolated = False
            os.fsync(parent)
            raise DoctorError(
                "post-cutover rollback live pointer changed before exchange"
            )

        exchange_error: Exception | None = None
        try:
            _rename_exchange_at(
                quarantine_descriptor,
                "bound-link",
                parent,
                paths.context_generation_pointer.name,
            )
        except Exception as exc:
            exchange_error = exc
        restored = _link_surface_at(parent, paths.context_generation_pointer.name)
        displaced = _link_surface_at(quarantine_descriptor, "bound-link")
        if (
            _same_link_identity(restored, prior_pointer)
            and _descriptor_matches_link_surface(
                prior_pointer_descriptor, prior_pointer
            )
            and _same_link_identity(displaced, selected_pointer)
            and _descriptor_matches_link_surface(
                selected_pointer_descriptor, selected_pointer
            )
        ):
            if not _directory_descriptor_matches_path(
                parent, paths.context_generation_pointer.parent
            ):
                os.fsync(quarantine_descriptor)
                os.fsync(parent)
                raise DoctorError(
                    "post-cutover rollback directory changed after exchange; "
                    "both pointer identities were preserved"
                )
            os.unlink("bound-link", dir_fd=quarantine_descriptor)
            if os.fstat(selected_pointer_descriptor).st_nlink != 0:
                os.fsync(quarantine_descriptor)
                os.fsync(parent)
                raise DoctorError(
                    "post-cutover rollback selected pointer acquired another "
                    "link during cleanup"
                )
            isolated = False
            os.fsync(quarantine_descriptor)
            os.fsync(parent)
        elif (
            _same_link_identity(restored, selected_pointer)
            and _same_link_identity(displaced, prior_pointer)
        ):
            _rename_noreplace_at(
                quarantine_descriptor,
                "bound-link",
                parent,
                retained_prior_pointer.name,
            )
            isolated = False
            os.fsync(parent)
            raise DoctorError(
                "post-cutover rollback exchange did not occur"
            ) from exchange_error
        else:
            os.fsync(quarantine_descriptor)
            os.fsync(parent)
            raise DoctorError(
                "post-cutover rollback exchange was ambiguous; isolated state "
                f"preserved at {retained_prior_pointer.parent / quarantine}"
            ) from exchange_error
        final_pointer = _link_surface_at(
            parent, paths.context_generation_pointer.name
        )
        if (
            not _directory_descriptor_matches_path(
                parent, paths.context_generation_pointer.parent
            )
            or not _same_link_identity(final_pointer, prior_pointer)
        ):
            raise DoctorError(
                "post-cutover rollback lost the restored pointer identity"
            )
    finally:
        if quarantine_descriptor >= 0:
            os.close(quarantine_descriptor)
        if not isolated:
            try:
                os.rmdir(quarantine, dir_fd=parent)
            except FileNotFoundError:
                pass
            except OSError:
                pass
        os.close(parent)


def _finalize_context_generation_pointer(
    paths: DoctorPaths,
    selected_pointer: Mapping[str, Any],
    prior_pointer: Mapping[str, Any],
    *,
    retained_prior_pointer: Path,
    pointer_parent_descriptor: int,
    selected_pointer_descriptor: int,
    prior_pointer_descriptor: int,
) -> None:
    """Durably discard the displaced pointer after receipt publication."""

    parent = os.dup(pointer_parent_descriptor)
    try:
        live_pointer = _link_surface_at(
            parent, paths.context_generation_pointer.name
        )
        retained_pointer = _link_surface_at(parent, retained_prior_pointer.name)
        if (
            retained_prior_pointer.parent
            != paths.context_generation_pointer.parent
            or not _directory_descriptor_matches_path(
                parent, paths.context_generation_pointer.parent
            )
            or not _same_link_identity(live_pointer, selected_pointer)
            or not _descriptor_matches_link_surface(
                selected_pointer_descriptor, live_pointer
            )
            or not _same_link_identity(retained_pointer, prior_pointer)
            or not _descriptor_matches_link_surface(
                prior_pointer_descriptor, retained_pointer
            )
        ):
            raise DoctorError(
                "post-cutover cleanup refused a concurrent pointer change"
            )
        _durably_unlink_bound_symlink(
            retained_prior_pointer,
            prior_pointer,
            prior_pointer_descriptor,
            parent_descriptor=parent,
            guard_path=paths.context_generation_pointer,
            guard_expected=selected_pointer,
            guard_descriptor=selected_pointer_descriptor,
        )
        live_after = _link_surface_at(
            parent, paths.context_generation_pointer.name
        )
        if (
            not _directory_descriptor_matches_path(
                parent, paths.context_generation_pointer.parent
            )
            or not _same_link_identity(live_after, selected_pointer)
            or not _descriptor_matches_link_surface(
                selected_pointer_descriptor, selected_pointer
            )
        ):
            raise DoctorError(
                "post-cutover cleanup completed but the live pointer changed concurrently"
            )
    finally:
        os.close(parent)


def _surface_semantics(surface: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("kind", "raw", "target", "uid", "gid", "mode")
    return {key: surface[key] for key in keys if key in surface}


def _same_link_identity(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    keys = (
        "kind",
        "target",
        "uid",
        "gid",
        "mode",
        "device",
        "inode",
        "nlink",
    )
    return all(left.get(key) == right.get(key) for key in keys)


def _descriptor_matches_link_surface(
    descriptor: int,
    surface: Mapping[str, Any],
) -> bool:
    """Bind a symlink surface to a retained O_PATH descriptor across rename."""

    observed = os.fstat(descriptor)
    return (
        surface.get("kind") == "symlink"
        and stat.S_ISLNK(observed.st_mode)
        and observed.st_dev == surface.get("device")
        and observed.st_ino == surface.get("inode")
        and observed.st_uid == surface.get("uid")
        and observed.st_gid == surface.get("gid")
        and stat.S_IMODE(observed.st_mode) == surface.get("mode")
        and observed.st_nlink == surface.get("nlink")
        and os.readlink("", dir_fd=descriptor) == surface.get("target")
    )


def _link_surface_from_descriptor(descriptor: int) -> dict[str, Any]:
    """Capture one symlink identity from an already-open O_PATH descriptor."""

    observed = os.fstat(descriptor)
    if not stat.S_ISLNK(observed.st_mode):
        raise DoctorError("Context generation pointer descriptor is not a symlink")
    return {
        "kind": "symlink",
        "target": os.readlink("", dir_fd=descriptor),
        "uid": observed.st_uid,
        "gid": observed.st_gid,
        "mode": stat.S_IMODE(observed.st_mode),
        "device": observed.st_dev,
        "inode": observed.st_ino,
        "nlink": observed.st_nlink,
    }


def _link_surface_at(directory_descriptor: int, name: str) -> dict[str, Any]:
    try:
        observed = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return {"kind": "missing"}
    if not stat.S_ISLNK(observed.st_mode):
        return {"kind": "unsupported"}
    return {
        "kind": "symlink",
        "target": os.readlink(name, dir_fd=directory_descriptor),
        "uid": observed.st_uid,
        "gid": observed.st_gid,
        "mode": stat.S_IMODE(observed.st_mode),
        "device": observed.st_dev,
        "inode": observed.st_ino,
        "nlink": observed.st_nlink,
    }


def _directory_descriptor_matches_path(descriptor: int, path: Path) -> bool:
    """Prove that a retained directory descriptor is still the public path."""

    try:
        retained = os.fstat(descriptor)
        visible = path.stat(follow_symlinks=False)
    except (FileNotFoundError, OSError):
        return False
    return (
        stat.S_ISDIR(retained.st_mode)
        and stat.S_ISDIR(visible.st_mode)
        and (retained.st_dev, retained.st_ino) == (visible.st_dev, visible.st_ino)
    )


def _durably_unlink_bound_symlink(
    path: Path,
    expected: Mapping[str, Any],
    descriptor: int,
    *,
    parent_descriptor: int,
    guard_path: Path | None = None,
    guard_expected: Mapping[str, Any] | None = None,
    guard_descriptor: int | None = None,
) -> None:
    """Move one exact symlink into a private directory before unlinking it."""

    if path.name in {"", ".", ".."}:
        raise DoctorError("descriptor-bound symlink cleanup path is unsafe")
    parent = os.dup(parent_descriptor)
    quarantine = f".{path.name}.remove-{os.getpid()}-{secrets.token_hex(8)}"
    quarantine_descriptor = -1
    moved = False
    try:
        if not _directory_descriptor_matches_path(parent, path.parent):
            raise DoctorError(
                "descriptor-bound symlink cleanup parent changed concurrently"
            )
        os.mkdir(quarantine, mode=0o700, dir_fd=parent)
        quarantine_descriptor = os.open(
            quarantine,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent,
        )
        _rename_noreplace_at(parent, path.name, quarantine_descriptor, "bound-link")
        moved = True
        isolated = _link_surface_at(quarantine_descriptor, "bound-link")
        if (
            not _same_link_identity(isolated, expected)
            or not _descriptor_matches_link_surface(descriptor, expected)
        ):
            if _link_surface_at(parent, path.name).get("kind") != "missing":
                raise DoctorError(
                    "descriptor-bound symlink changed and its original name was reused; "
                    f"preserved at {path.parent / quarantine / 'bound-link'}"
                )
            _rename_noreplace_at(
                quarantine_descriptor,
                "bound-link",
                parent,
                path.name,
            )
            moved = False
            os.fsync(quarantine_descriptor)
            os.fsync(parent)
            raise DoctorError(
                "descriptor-bound symlink changed before cleanup; replacement restored"
            )
        guard_values = (guard_path, guard_expected, guard_descriptor)
        if any(value is not None for value in guard_values):
            if not all(value is not None for value in guard_values):
                raise DoctorError("descriptor-bound symlink cleanup guard is incomplete")
            assert guard_path is not None
            assert guard_expected is not None
            assert guard_descriptor is not None
            if guard_path.parent != path.parent:
                raise DoctorError(
                    "descriptor-bound symlink cleanup guard is outside the bound parent"
                )
            guarded = _link_surface_at(parent, guard_path.name)
            if (
                not _directory_descriptor_matches_path(parent, path.parent)
                or
                not _same_link_identity(guarded, guard_expected)
                or not _descriptor_matches_link_surface(
                    guard_descriptor, guard_expected
                )
            ):
                if _link_surface_at(parent, path.name).get("kind") != "missing":
                    raise DoctorError(
                        "descriptor-bound cleanup guard changed and the retained "
                        "pointer name was reused; exact prior preserved at "
                        f"{path.parent / quarantine / 'bound-link'}"
                    )
                _rename_noreplace_at(
                    quarantine_descriptor,
                    "bound-link",
                    parent,
                    path.name,
                )
                moved = False
                os.fsync(quarantine_descriptor)
                os.fsync(parent)
                raise DoctorError(
                    "descriptor-bound cleanup guard changed; exact prior restored"
                )
        os.unlink("bound-link", dir_fd=quarantine_descriptor)
        if os.fstat(descriptor).st_nlink != 0:
            os.fsync(quarantine_descriptor)
            os.fsync(parent)
            raise DoctorError(
                "descriptor-bound symlink acquired another link during cleanup"
            )
        moved = False
        os.fsync(quarantine_descriptor)
        os.close(quarantine_descriptor)
        quarantine_descriptor = -1
        os.rmdir(quarantine, dir_fd=parent)
        os.fsync(parent)
    finally:
        if quarantine_descriptor >= 0:
            os.close(quarantine_descriptor)
        if not moved:
            try:
                os.rmdir(quarantine, dir_fd=parent)
            except FileNotFoundError:
                pass
            except OSError:
                # A non-empty quarantine is deliberate failure evidence.
                pass
        os.close(parent)


def _context_pointer_pair_exact(
    paths: DoctorPaths,
    retained_prior_pointer: Path,
    selected_pointer: Mapping[str, Any],
    prior_pointer: Mapping[str, Any],
    pointer_parent_descriptor: int,
    selected_pointer_descriptor: int,
    prior_pointer_descriptor: int,
) -> bool:
    return (
        _directory_descriptor_matches_path(
            pointer_parent_descriptor, paths.context_generation_pointer.parent
        )
        and
        _same_link_identity(
            _link_surface_at(
                pointer_parent_descriptor,
                paths.context_generation_pointer.name,
            ),
            selected_pointer,
        )
        and _descriptor_matches_link_surface(
            selected_pointer_descriptor, selected_pointer
        )
        and _same_link_identity(
            _link_surface_at(
                pointer_parent_descriptor,
                retained_prior_pointer.name,
            ),
            prior_pointer,
        )
        and _descriptor_matches_link_surface(
            prior_pointer_descriptor, prior_pointer
        )
    )


def _restore_surface(path: Path, surface: Mapping[str, Any]) -> None:
    kind = surface.get("kind")
    if kind == "file":
        _atomic_bytes(
            path,
            surface["raw"],
            mode=surface["mode"],
            uid=surface["uid"],
            gid=surface["gid"],
        )
        return
    if kind == "symlink":
        _replace_link(path, Path(surface["target"]))
        os.lchown(path, surface["uid"], surface["gid"])
        return
    if kind == "missing":
        if path.is_file() or path.is_symlink():
            path.unlink()
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        return
    raise DoctorError(f"cannot restore unsupported surface type at {path}")


def repair_context_launcher(
    paths: DoctorPaths,
    *,
    desired_commit: str | None = None,
    source_root: Path | None = None,
) -> dict[str, Any]:
    """Migrate old semantics behind shims, then switch the pair exactly once."""
    _require_root()
    selected = _repair_context_artifacts(
        paths, desired_commit=desired_commit, source_root=source_root
    )
    desired = selected["commit"]
    signature = _context_artifact_signature(selected)
    artifacts = {"launcher": selected["launcher"].read_bytes(), "publisher": selected["publisher"].read_bytes()}
    generation_name = _context_generation_name(selected)
    generation = paths.context_generation_root / generation_name
    surfaces = (
        ("launcher", paths.context_launcher, "tgw-context-mcp"),
        ("publisher", paths.context_publisher, "tgw-context-publish"),
    )
    before = {key: _surface_snapshot(surface) for key, surface, _name in surfaces}
    regular = all(
        item.get("kind") == "file" and item.get("raw") != _CONTEXT_DISPATCH_SHIM
        for item in before.values()
    )
    shims = all(
        item.get("kind") == "file"
        and item.get("raw") == _CONTEXT_DISPATCH_SHIM
        and item.get("uid") == paths.context_install_uid
        and item.get("gid") == paths.context_install_gid
        and item.get("mode") == paths.context_launcher_mode
        for item in before.values()
    )
    mixed = {
        key: item.get("raw") == _CONTEXT_DISPATCH_SHIM
        for key, item in before.items()
        if item.get("kind") == "file"
    }
    if not regular and not shims and len(mixed) != 2:
        raise DoctorError("Context entrypoint surfaces are mixed or ambiguous")
    migration_old_generation: Path | None = None
    post_cutover_probe_failed = False
    pointer_switch_unproven = False
    pointer_cutover_active = False
    selected_pointer_descriptor: int | None = None
    prior_pointer_descriptor: int | None = None
    pointer_parent_descriptor: int | None = None
    pointer_tmp: Path | None = None
    retained_displaced_pointer: dict[str, Any] | None = None
    pointer_cleanup_warning: str | None = None
    receipt_publication_warning: str | None = None
    receipt_commit_ambiguous = False
    try:
        if _context_artifact_signature(
            _repair_context_artifacts(
                paths, desired_commit=desired_commit, source_root=source_root
            )
        ) != signature:
            raise DoctorError("selected Context runtime changed before repair")
        paths.context_generation_pointer.parent.mkdir(parents=True, exist_ok=True)
        paths.context_generation_root.mkdir(parents=True, exist_ok=True)
        pointer_parent_descriptor = os.open(
            paths.context_generation_pointer.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        if not _directory_descriptor_matches_path(
            pointer_parent_descriptor,
            paths.context_generation_pointer.parent,
        ):
            raise DoctorError("Context pointer directory changed during binding")
        os.chmod(paths.context_generation_pointer.parent, 0o755)
        os.chmod(paths.context_generation_root, 0o555)

        if not regular and not shims:
            # A killed first migration can expose one audited shim and one exact
            # legacy file.  The pointer-selected immutable old generation is the
            # durable recovery record; accept no other mixed state.
            old_generation = _resolved_context_generation(paths)
            old_pair = _validate_context_generation(old_generation, paths)
            for key, surface, _entrypoint in surfaces:
                current = before[key]
                if mixed[key]:
                    continue
                if current.get("sha256") != old_pair[key].get("sha256"):
                    raise DoctorError("mixed Context migration does not match selected legacy generation")
                _atomic_bytes(
                    surface,
                    _CONTEXT_DISPATCH_SHIM,
                    mode=paths.context_launcher_mode,
                    uid=paths.context_install_uid,
                    gid=paths.context_install_gid,
                )
            shims = True

        if regular:
            old_artifacts = {key: before[key]["raw"] for key in ("launcher", "publisher")}
            old_name = _context_old_generation_name(old_artifacts)
            old_generation = paths.context_generation_root / old_name
            migration_old_generation = old_generation
            os.chmod(paths.context_generation_root, 0o755)
            _discard_context_staging(
                paths.context_generation_root / ("." + old_name + ".staging"),
                paths,
            )
            existing = list(paths.context_generation_root.iterdir())
            if existing and existing != [old_generation]:
                raise DoctorError("legacy Context surfaces have ambiguous pre-existing generations")
            if not old_generation.exists():
                staging = paths.context_generation_root / ("." + old_name + ".staging")
                staging.mkdir(mode=0o700)
                _context_repair_phase("old-generation-created", paths)
                try:
                    for key, _surface, entrypoint in surfaces:
                        _atomic_bytes(staging / entrypoint, old_artifacts[key], mode=paths.context_launcher_mode, uid=paths.context_install_uid, gid=paths.context_install_gid)
                    _context_repair_phase("old-generation-files", paths)
                    directory = os.open(staging, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                    _context_repair_phase("old-generation-fsync", paths)
                    os.chmod(staging, 0o555)
                    _context_repair_phase("old-generation-chmod", paths)
                    os.replace(staging, old_generation)
                    _context_repair_phase("old-generation-rename", paths)
                    _fsync_parent(old_generation)
                finally:
                    if staging.exists():
                        shutil.rmtree(staging)
            _validate_context_generation(
                old_generation,
                paths,
                {key: before[key]["sha256"] for key in ("launcher", "publisher")},
            )
            if paths.context_generation_pointer.is_symlink():
                if _resolved_context_generation(paths) != old_generation:
                    raise DoctorError("legacy Context surfaces have an ambiguous pre-existing pointer")
            elif _lexists(paths.context_generation_pointer):
                raise DoctorError("legacy Context surfaces have an ambiguous pre-existing pointer")
            else:
                _replace_link(paths.context_generation_pointer, Path("generations") / old_name)
                _fsync_parent(paths.context_generation_pointer)
                _context_repair_phase("old-pointer", paths)
            captured_pointer = _surface_snapshot(paths.context_generation_pointer)
            try:
                for index, (key, surface, _entrypoint) in enumerate(surfaces, start=1):
                    if _surface_snapshot(surface) != before[key]:
                        raise DoctorError("Context surface changed concurrently before shim migration")
                    _atomic_bytes(surface, _CONTEXT_DISPATCH_SHIM, mode=paths.context_launcher_mode, uid=paths.context_install_uid, gid=paths.context_install_gid)
                    _context_repair_phase(f"shim-{index}", paths)
                    if _resolved_context_generation(paths) != old_generation:
                        raise DoctorError("Context pointer changed during shim migration")
                for _key, surface, _entrypoint in surfaces:
                    if _surface_snapshot(surface).get("raw") != _CONTEXT_DISPATCH_SHIM:
                        raise DoctorError("Context dispatch shim did not converge")
            except Exception:
                for key, surface, _entrypoint in surfaces:
                    current = _surface_snapshot(surface)
                    if current != before[key] and current.get("raw") != _CONTEXT_DISPATCH_SHIM:
                        raise DoctorError("Context shim rollback refused a concurrent surface change")
                    if current != before[key]:
                        _restore_surface(surface, before[key])
                if _surface_snapshot(paths.context_generation_pointer) != captured_pointer:
                    raise DoctorError("Context shim rollback refused a concurrent pointer change")
                paths.context_generation_pointer.unlink()
                _fsync_parent(paths.context_generation_pointer)
                raise

        os.chmod(paths.context_generation_root, 0o755)
        _discard_context_staging(
            paths.context_generation_root / ("." + generation_name + ".staging"),
            paths,
        )
        os.chmod(paths.context_generation_root, 0o555)
        _validate_all_context_generations(paths)
        captured_pointer = _surface_snapshot(paths.context_generation_pointer)
        old_generation = _resolved_context_generation(paths)
        os.chmod(paths.context_generation_root, 0o755)
        if not generation.exists():
            temporary = paths.context_generation_root / ("." + generation_name + ".staging")
            if temporary.exists():
                raise DoctorError("stale Context generation staging directory requires inspection")
            temporary.mkdir(mode=0o700)
            try:
                for key, _surface, entrypoint in surfaces:
                    _atomic_bytes(temporary / entrypoint, artifacts[key], mode=paths.context_launcher_mode, uid=paths.context_install_uid, gid=paths.context_install_gid)
                runtime = temporary / "runtime"
                shutil.copytree(selected["runtime_source"], runtime)
                _context_repair_phase("runtime-copy", paths)
                for entry in (runtime, *runtime.rglob("*")):
                    if entry.is_symlink():
                        raise DoctorError("selected Context runtime contains a symlink")
                    os.chown(entry, paths.context_install_uid, paths.context_install_gid)
                    os.chmod(entry, 0o555 if entry.is_dir() else 0o444)
                _atomic_bytes(
                    temporary / "generation-manifest.json",
                    _context_generation_manifest(selected),
                    mode=0o444,
                    uid=paths.context_install_uid,
                    gid=paths.context_install_gid,
                )
                os.chmod(temporary, 0o555)
                _fsync_context_tree(temporary)
                _context_repair_phase("runtime-fsync", paths)
                os.replace(temporary, generation)
                _context_repair_phase("generation-rename", paths)
                directory = os.open(generation, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
                _fsync_parent(generation)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        os.chmod(paths.context_generation_root, 0o555)
        _validate_all_context_generations(paths)
        _validate_context_generation(generation, paths, selected["hashes"])
        captured_generation = _descriptor_context_tree(generation)
        if _surface_snapshot(paths.context_generation_pointer) != captured_pointer:
            raise DoctorError("Context generation pointer changed concurrently before switch")
        pointer_tmp = paths.context_generation_pointer.with_name(
            "." + paths.context_generation_pointer.name + ".new"
        )
        if old_generation == generation:
            pair = _context_pair(paths)
            for name, expected in artifacts.items():
                if not _launcher_surface_exact(pair[name], expected, paths):
                    raise DoctorError(f"Context {name} generation did not converge")
            if _context_artifact_signature(
                _repair_context_artifacts(
                    paths, desired_commit=desired_commit, source_root=source_root
                )
            ) != signature:
                raise DoctorError("selected Context runtime changed during repair replay")
            if _lexists(pointer_tmp):
                if _surface_snapshot(pointer_tmp).get("kind") != "symlink":
                    raise DoctorError(
                        "interrupted Context pointer replay has an unsafe retained path"
                    )
                selected_descriptor = os.open(
                    paths.context_generation_pointer.name,
                    os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=pointer_parent_descriptor,
                )
                try:
                    selected_surface = _link_surface_from_descriptor(
                        selected_descriptor
                    )
                    prior_descriptor = os.open(
                        pointer_tmp.name,
                        os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
                        dir_fd=pointer_parent_descriptor,
                    )
                    try:
                        prior_surface = _link_surface_from_descriptor(
                            prior_descriptor
                        )
                        expected_target = str(
                            Path("generations") / generation_name
                        )
                        prior_target = Path(str(prior_surface["target"]))
                        if (
                            not _same_link_identity(
                                _surface_snapshot(
                                    paths.context_generation_pointer
                                ),
                                selected_surface,
                            )
                            or not _same_link_identity(
                                _surface_snapshot(pointer_tmp), prior_surface
                            )
                            or selected_surface.get("target") != expected_target
                            or prior_target.is_absolute()
                            or len(prior_target.parts) != 2
                            or prior_target.parts[0] != "generations"
                            or prior_target.name == generation_name
                        ):
                            raise DoctorError(
                                "interrupted Context pointer replay is not one exact exchange pair"
                            )
                        prior_generation = pointer_tmp.parent / prior_target
                        _validate_context_generation(prior_generation, paths)
                        probe = _probe_context_stdio(
                            paths.context_launcher,
                            _context_probe_actor(paths),
                            _selected_context_probe_expected(paths, selected),
                        )
                        if not _context_pointer_pair_exact(
                            paths,
                            pointer_tmp,
                            selected_surface,
                            prior_surface,
                            pointer_parent_descriptor,
                            selected_descriptor,
                            prior_descriptor,
                        ):
                            raise DoctorError(
                                "interrupted Context pointer pair changed after cold probe"
                            )
                        replay_receipt_warning: str | None = None
                        try:
                            receipt = _receipt(
                                paths,
                                "context-launcher",
                                {
                                    "generation": prior_generation.name,
                                    "recovered_interrupted_exchange": True,
                                },
                                {
                                    "generation": generation_name,
                                    "runtime_commit": desired,
                                    "runtime_hashes": selected["hashes"],
                                },
                            )
                            if not _context_pointer_pair_exact(
                                paths,
                                pointer_tmp,
                                selected_surface,
                                prior_surface,
                                pointer_parent_descriptor,
                                selected_descriptor,
                                prior_descriptor,
                            ):
                                receipt_path = Path(receipt)
                                receipt_surface = _surface_snapshot(receipt_path)
                                try:
                                    _remove_exact_receipt(
                                        receipt_path, receipt_surface
                                    )
                                except Exception as cleanup_exc:
                                    raise _ReceiptPublicationAmbiguous(
                                        receipt_path,
                                        "interrupted Context pointer pair changed "
                                        "during receipt publication; receipt cleanup "
                                        f"is ambiguous: {cleanup_exc}",
                                    ) from cleanup_exc
                                raise DoctorError(
                                    "interrupted Context pointer pair changed during "
                                    "receipt publication; success receipt removed"
                                )
                        except _ReceiptPublicationAmbiguous as receipt_exc:
                            receipt_commit_ambiguous = True
                            replay_receipt_warning = str(receipt_exc)
                            retained_displaced_pointer = {
                                "path": str(pointer_tmp),
                                "reason": (
                                    "retained because interrupted-cutover receipt "
                                    "publication is ambiguous"
                                ),
                                "identity": _surface_snapshot(pointer_tmp),
                            }
                            raise
                        except Exception as receipt_exc:
                            try:
                                _rollback_context_generation_pointer(
                                    paths,
                                    generation,
                                    selected_surface,
                                    prior_surface,
                                    retained_prior_pointer=pointer_tmp,
                                    pointer_parent_descriptor=pointer_parent_descriptor,
                                    selected_pointer_descriptor=selected_descriptor,
                                    prior_pointer_descriptor=prior_descriptor,
                                )
                            except Exception as rollback_exc:
                                raise DoctorError(
                                    "interrupted Context replay receipt failed: "
                                    f"{receipt_exc}; pointer rollback failed: "
                                    f"{rollback_exc}"
                                ) from receipt_exc
                            raise DoctorError(
                                "interrupted Context replay receipt failed; exact "
                                f"prior pointer restored: {receipt_exc}"
                            ) from receipt_exc
                        if replay_receipt_warning is None:
                            try:
                                _finalize_context_generation_pointer(
                                    paths,
                                    selected_surface,
                                    prior_surface,
                                    retained_prior_pointer=pointer_tmp,
                                    pointer_parent_descriptor=pointer_parent_descriptor,
                                    selected_pointer_descriptor=selected_descriptor,
                                    prior_pointer_descriptor=prior_descriptor,
                                )
                            except Exception as cleanup_exc:
                                pointer_cleanup_warning = str(cleanup_exc)
                                retained_displaced_pointer = {
                                    "path": str(pointer_tmp),
                                    "reason": (
                                        "retained after receipted interrupted Context "
                                        "cutover recovery because cleanup did not complete"
                                    ),
                                    "identity": _surface_snapshot(pointer_tmp),
                                }
                    finally:
                        os.close(prior_descriptor)
                finally:
                    os.close(selected_descriptor)
                restart_report = _context_restart_report(paths)
                return {
                    "ok": True,
                    "operation": "context-launcher",
                    "changed": True,
                    "recovered_interrupted_cutover": True,
                    "generation": generation_name,
                    "runtime_commit": desired,
                    "source": str(selected["launcher"]),
                    "installed": str(paths.context_launcher),
                    "sha256": selected["hashes"]["launcher"],
                    "publisher_source": str(selected["publisher"]),
                    "publisher_installed": str(paths.context_publisher),
                    "publisher_sha256": selected["hashes"]["publisher"],
                    "runtime_source": str(selected["runtime_source"]),
                    "runtime_hashes": selected["hashes"],
                    "receipt": receipt,
                    "retained_displaced_pointer": retained_displaced_pointer,
                    "pointer_cleanup_warning": pointer_cleanup_warning,
                    "receipt_publication_warning": replay_receipt_warning,
                    "post_cutover_cold_stdio_probe": probe,
                    **restart_report,
                }
            probe = _probe_context_stdio(
                paths.context_launcher,
                _context_probe_actor(paths),
                _selected_context_probe_expected(paths, selected),
            )
            restart_report = _context_restart_report(paths)
            return {
                "ok": True,
                "operation": "context-launcher",
                "changed": False,
                "generation": generation_name,
                "runtime_commit": desired,
                "source": str(selected["launcher"]),
                "installed": str(paths.context_launcher),
                "sha256": selected["hashes"]["launcher"],
                "publisher_source": str(selected["publisher"]),
                "publisher_installed": str(paths.context_publisher),
                "publisher_sha256": selected["hashes"]["publisher"],
                "runtime_source": str(selected["runtime_source"]),
                "runtime_hashes": selected["hashes"],
                "receipt": None,
                "retained_displaced_pointer": None,
                "pointer_cleanup_warning": None,
                "post_cutover_cold_stdio_probe": probe,
                **restart_report,
            }
        if pointer_tmp.exists() or pointer_tmp.is_symlink():
            staged = _surface_snapshot(pointer_tmp)
            if staged.get("kind") != "symlink":
                raise DoctorError("stale Context pointer staging state requires inspection")
            target = Path(staged["target"])
            if target.is_absolute() or len(target.parts) != 2 or target.parts[0] != "generations":
                raise DoctorError("stale Context pointer staging target is unsafe")
            _validate_context_generation(pointer_tmp.parent / target, paths)
            pointer_tmp.unlink()
            _fsync_parent(pointer_tmp)
        pointer_tmp.symlink_to(Path("generations") / generation_name)
        _context_repair_phase("staged-pointer", paths)
        if _surface_snapshot(paths.context_generation_pointer) != captured_pointer:
            pointer_tmp.unlink()
            raise DoctorError("Context generation pointer changed concurrently during switch")
        staged_pointer_descriptor = os.open(
            pointer_tmp.name,
            os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=pointer_parent_descriptor,
        )
        try:
            staged_pointer = _link_surface_from_descriptor(
                staged_pointer_descriptor
            )
            captured_pointer_descriptor = os.open(
                paths.context_generation_pointer.name,
                os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=pointer_parent_descriptor,
            )
            try:
                switch_captured_pointer = _link_surface_from_descriptor(
                    captured_pointer_descriptor
                )
                if (
                    not _same_link_identity(
                        _surface_snapshot(pointer_tmp), staged_pointer
                    )
                    or not _same_link_identity(
                        _surface_snapshot(paths.context_generation_pointer),
                        switch_captured_pointer,
                    )
                    or not _same_link_identity(
                        switch_captured_pointer, captured_pointer
                    )
                ):
                    raise DoctorError(
                        "Context generation pointer descriptors differ before switch"
                    )
                captured_pointer = switch_captured_pointer
                selected_pointer_descriptor = os.dup(staged_pointer_descriptor)
                prior_pointer_descriptor = os.dup(captured_pointer_descriptor)
                parent = os.dup(pointer_parent_descriptor)
                try:
                    # Capture surfaces from retained descriptors before the
                    # exchange, then prove both visible names against them.
                    pointer_switch_unproven = True
                    _rename_exchange(
                        parent,
                        pointer_tmp.name,
                        paths.context_generation_pointer.name,
                    )
                    _context_repair_phase("exchange", paths)
                    selected_pointer = _surface_snapshot(
                        paths.context_generation_pointer
                    )
                    displaced = _surface_snapshot(pointer_tmp)
                    if (
                        not _same_link_identity(selected_pointer, staged_pointer)
                        or not _descriptor_matches_link_surface(
                            staged_pointer_descriptor, staged_pointer
                        )
                        or not _same_link_identity(displaced, captured_pointer)
                        or not _descriptor_matches_link_surface(
                            captured_pointer_descriptor, captured_pointer
                        )
                    ):
                        raise DoctorError(
                            "Context generation pointer CAS observed an unproven "
                            "concurrent surface"
                        )
                    pointer_cutover_active = True
                    pointer_switch_unproven = False
                    os.fsync(parent)
                    _context_repair_phase("parent-fsync", paths)
                finally:
                    os.close(parent)
            finally:
                os.close(captured_pointer_descriptor)
        finally:
            os.close(staged_pointer_descriptor)
        if _resolved_context_generation(paths) != generation:
            raise DoctorError("Context pointer switch did not select the captured generation")
        _validate_context_generation(generation, paths, selected["hashes"])
        if _descriptor_context_tree(generation) != captured_generation:
            raise DoctorError("Context generation changed across pointer CAS")
        pair = _context_pair(paths)
        installed = {name: pair[name] for name in ("launcher", "publisher")}
        for name, expected in artifacts.items():
            if not _launcher_surface_exact(installed[name], expected, paths):
                raise DoctorError(f"Context {name} generation did not converge")
        if _context_artifact_signature(
            _repair_context_artifacts(
                paths, desired_commit=desired_commit, source_root=source_root
            )
        ) != signature:
            raise DoctorError("selected Context runtime changed during repair")
        try:
            probe = _probe_context_stdio(
                paths.context_launcher,
                _context_probe_actor(paths),
                _selected_context_probe_expected(paths, selected),
            )
        except Exception as probe_exc:
            post_cutover_probe_failed = True
            try:
                if (
                    pointer_tmp is None
                    or pointer_parent_descriptor is None
                    or selected_pointer_descriptor is None
                    or prior_pointer_descriptor is None
                ):
                    raise DoctorError(
                        "post-cutover rollback lacks retained pointer descriptors"
                    )
                _rollback_context_generation_pointer(
                    paths,
                    generation,
                    staged_pointer,
                    captured_pointer,
                    retained_prior_pointer=pointer_tmp,
                    pointer_parent_descriptor=pointer_parent_descriptor,
                    selected_pointer_descriptor=selected_pointer_descriptor,
                    prior_pointer_descriptor=prior_pointer_descriptor,
                )
                pointer_cutover_active = False
            except Exception as rollback_exc:
                raise DoctorError(
                    f"installed Context shim cold probe failed: {probe_exc}; "
                    f"pointer rollback failed: {rollback_exc}"
                ) from probe_exc
            raise DoctorError(
                f"installed Context shim cold probe failed after cutover; "
                f"prior generation restored: {probe_exc}"
            ) from probe_exc
        if not _context_pointer_pair_exact(
            paths,
            pointer_tmp,
            staged_pointer,
            captured_pointer,
            pointer_parent_descriptor,
            selected_pointer_descriptor,
            prior_pointer_descriptor,
        ):
            raise DoctorError(
                "Context pointer pair changed after cold probe and before receipt"
            )
        try:
            receipt = _receipt(
                paths,
                "context-launcher",
                {"generation": old_generation.name},
                {
                    "generation": generation_name,
                    "runtime_commit": desired,
                    "runtime_hashes": selected["hashes"],
                },
            )
            if not _context_pointer_pair_exact(
                paths,
                pointer_tmp,
                staged_pointer,
                captured_pointer,
                pointer_parent_descriptor,
                selected_pointer_descriptor,
                prior_pointer_descriptor,
            ):
                receipt_path = Path(receipt)
                receipt_surface = _surface_snapshot(receipt_path)
                try:
                    _remove_exact_receipt(receipt_path, receipt_surface)
                except Exception as cleanup_exc:
                    raise _ReceiptPublicationAmbiguous(
                        receipt_path,
                        "Context pointer pair changed during receipt publication; "
                        f"receipt cleanup is ambiguous: {cleanup_exc}",
                    ) from cleanup_exc
                raise DoctorError(
                    "Context pointer pair changed during receipt publication; "
                    "success receipt removed"
                )
        except _ReceiptPublicationAmbiguous as receipt_exc:
            # Exact success bytes may already be durable.  Preserve the
            # selected live pointer and both descriptor-bound identities for a
            # deterministic replay; never pair that receipt with a rollback or
            # report a terminal success whose receipt visibility is unknown.
            receipt_commit_ambiguous = True
            receipt_publication_warning = str(receipt_exc)
            pointer_cutover_active = False
            retained_displaced_pointer = {
                "path": str(pointer_tmp),
                "reason": (
                    "retained because Context receipt publication is ambiguous; "
                    "selected generation remains live"
                ),
                "identity": _surface_snapshot(pointer_tmp),
            }
            raise
        except Exception as receipt_exc:
            # A cutover without durable success evidence is not a completed
            # transaction. Restore the exact prior pointer for both the normal
            # shim path and the one-time legacy migration path.
            post_cutover_probe_failed = True
            try:
                if (
                    pointer_tmp is None
                    or pointer_parent_descriptor is None
                    or selected_pointer_descriptor is None
                    or prior_pointer_descriptor is None
                ):
                    raise DoctorError(
                        "post-cutover rollback lacks retained pointer descriptors"
                    )
                _rollback_context_generation_pointer(
                    paths,
                    generation,
                    staged_pointer,
                    captured_pointer,
                    retained_prior_pointer=pointer_tmp,
                    pointer_parent_descriptor=pointer_parent_descriptor,
                    selected_pointer_descriptor=selected_pointer_descriptor,
                    prior_pointer_descriptor=prior_pointer_descriptor,
                )
                pointer_cutover_active = False
            except Exception as rollback_exc:
                raise DoctorError(
                    "Context launcher receipt publication failed after cutover: "
                    f"{receipt_exc}; pointer rollback failed: {rollback_exc}"
                ) from receipt_exc
            raise DoctorError(
                "Context launcher receipt publication failed after cutover; "
                f"prior generation restored: {receipt_exc}"
            ) from receipt_exc
        # Receipt publication is the durable commit point.  Never restore the
        # prior pointer after this line: that would leave success evidence for
        # a cutover that is no longer live.  Cleanup is separately observable
        # and replayable if it cannot finish now.
        pointer_cutover_active = False
        if (
            pointer_tmp is None
            or pointer_parent_descriptor is None
            or selected_pointer_descriptor is None
            or prior_pointer_descriptor is None
        ):
            raise DoctorError(
                "post-cutover cleanup lacks retained pointer descriptors"
            )
        if receipt_publication_warning is None:
            try:
                _finalize_context_generation_pointer(
                    paths,
                    staged_pointer,
                    captured_pointer,
                    retained_prior_pointer=pointer_tmp,
                    pointer_parent_descriptor=pointer_parent_descriptor,
                    selected_pointer_descriptor=selected_pointer_descriptor,
                    prior_pointer_descriptor=prior_pointer_descriptor,
                )
                _context_repair_phase("displaced-pointer-unlink", paths)
            except Exception as cleanup_exc:
                pointer_cleanup_warning = str(cleanup_exc)
                retained_displaced_pointer = {
                    "path": str(pointer_tmp),
                    "reason": (
                        "retained after committed Context cutover because durable "
                        "pointer cleanup did not complete"
                    ),
                    "identity": _surface_snapshot(pointer_tmp),
                }
    except Exception as exc:
        if pointer_cutover_active:
            try:
                if (
                    pointer_tmp is None
                    or pointer_parent_descriptor is None
                    or selected_pointer_descriptor is None
                    or prior_pointer_descriptor is None
                ):
                    raise DoctorError(
                        "failure rollback lacks retained pointer descriptors"
                    )
                _rollback_context_generation_pointer(
                    paths,
                    generation,
                    staged_pointer,
                    captured_pointer,
                    retained_prior_pointer=pointer_tmp,
                    pointer_parent_descriptor=pointer_parent_descriptor,
                    selected_pointer_descriptor=selected_pointer_descriptor,
                    prior_pointer_descriptor=prior_pointer_descriptor,
                )
                pointer_cutover_active = False
            except Exception as rollback_exc:
                raise DoctorError(
                    f"Context launcher repair failed: {exc}; "
                    f"pointer rollback failed: {rollback_exc}"
                ) from exc
        if receipt_commit_ambiguous:
            # The success receipt may be durable.  The inner transaction has
            # deliberately retained both pointer identities and disabled
            # rollback; preserve the typed outcome for deterministic replay.
            raise
        if (
            regular
            and migration_old_generation is not None
            and not post_cutover_probe_failed
            and not pointer_switch_unproven
            and not receipt_commit_ambiguous
        ):
            try:
                for key, surface, _entrypoint in surfaces:
                    current = _surface_snapshot(surface)
                    if current != before[key] and current.get("raw") != _CONTEXT_DISPATCH_SHIM:
                        raise DoctorError("Context failure rollback refused a concurrent surface change")
                    if current != before[key]:
                        _restore_surface(surface, before[key])
                pointer = _surface_snapshot(paths.context_generation_pointer)
                permitted = {
                    str(Path("generations") / migration_old_generation.name),
                    str(Path("generations") / generation_name),
                }
                if pointer.get("kind") == "symlink":
                    if pointer.get("target") not in permitted:
                        raise DoctorError("Context failure rollback refused a concurrent pointer change")
                    paths.context_generation_pointer.unlink()
                    _fsync_parent(paths.context_generation_pointer)
                elif pointer.get("kind") != "missing":
                    raise DoctorError("Context failure rollback found an ambiguous pointer")
                os.chmod(paths.context_generation_root, 0o755)
                for candidate in (generation,):
                    if candidate.exists():
                        _validate_context_generation(candidate, paths)
                        shutil.rmtree(candidate)
                _fsync_parent(paths.context_generation_root)
            except Exception as rollback_exc:
                raise DoctorError(
                    f"Context launcher repair failed: {exc}; rollback failed: {rollback_exc}"
                ) from exc
        raise DoctorError(f"Context launcher repair failed before a proven generation switch: {exc}") from exc
    finally:
        if prior_pointer_descriptor is not None:
            os.close(prior_pointer_descriptor)
        if selected_pointer_descriptor is not None:
            os.close(selected_pointer_descriptor)
        if pointer_parent_descriptor is not None:
            os.close(pointer_parent_descriptor)
    restart_report = _context_restart_report(paths)
    return {
        "ok": True,
        "operation": "context-launcher",
        "changed": True,
        "generation": generation_name,
        "runtime_commit": desired,
        "source": str(selected["launcher"]),
        "installed": str(paths.context_launcher),
        "sha256": selected["hashes"]["launcher"],
        "publisher_source": str(selected["publisher"]),
        "publisher_installed": str(paths.context_publisher),
        "publisher_sha256": selected["hashes"]["publisher"],
        "runtime_source": str(selected["runtime_source"]),
        "runtime_hashes": selected["hashes"],
        "receipt": receipt,
        "retained_displaced_pointer": retained_displaced_pointer,
        "pointer_cleanup_warning": pointer_cleanup_warning,
        "receipt_publication_warning": receipt_publication_warning,
        "post_cutover_cold_stdio_probe": probe,
        **restart_report,
    }

def repair_context(
    paths: DoctorPaths,
    *,
    desired_commit: str | None = None,
    source_root: Path | None = None,
    review_evidence: Path | None = None,
) -> dict[str, Any]:
    _require_root()
    explicit_source_reconciliation = desired_commit is not None
    selected = _repair_context_artifacts(
        paths, desired_commit=desired_commit, source_root=source_root
    )
    # Context publication is the convergence bootstrap: the live generation may
    # legitimately still contain the predecessor publisher/launcher.  Bind both
    # halves of preflight directly to the selected root-owned immutable release;
    # context-launcher repair may switch the live generation only after this
    # transaction has made its snapshot CURRENT.
    selected_publisher = selected["publisher"]
    selected_launcher = selected["launcher"]
    _require_trusted_root_program(
        selected_publisher, paths.trusted_release_owners
    )
    _require_trusted_root_program(
        selected_launcher, paths.trusted_release_owners
    )
    context_runtime = selected["runtime_source"]
    task_surface = _surface_snapshot(paths.context_task)
    cursor_surface = _surface_snapshot(paths.context_cursor)
    snapshot_surface = _surface_snapshot(paths.context_snapshot)
    before = {
        "task": _json_from_surface(paths.context_task, task_surface),
        "cursor": _json_from_surface(paths.context_cursor, cursor_surface),
        "snapshot": _json_from_surface(paths.context_snapshot, snapshot_surface),
    }
    head, tree, status = _source_identity(paths)
    if status:
        raise DoctorError("context repair refuses a dirty canonical source")
    review_binding = (
        _context_transition_review_binding(
            review_evidence,
            commit=head,
            tree=tree,
        )
        if review_evidence is not None
        else None
    )
    if review_binding is not None and not explicit_source_reconciliation:
        raise DoctorError(
            "Context transition review evidence requires an exact commit repair"
        )
    # The receipt retains ``before`` verbatim.  Reconciliation mutates nested
    # source/cursor structures, so both working projections must be detached
    # recursively rather than with a shallow dict copy.
    task = json.loads(json.dumps(before["task"]))
    cursor = json.loads(json.dumps(before["cursor"]))
    task_source = task.get("implementation", {}).get("development_source", {}).get("commit")
    if task_source != head:
        if not explicit_source_reconciliation or desired_commit != head:
            raise DoctorError("current task and canonical source disagree; rerun the bounded repair with the exact clean canonical commit")
        # An exact commit supplied to the privileged bootstrap is the operator's
        # source disposition. Context is orientation, not an authority surface.
        now = datetime.now().astimezone().isoformat()
        previous = {
            "commit": task_source,
            "tree": task.get("implementation", {}).get("development_source", {}).get("tree"),
            "state": task.get("implementation", {}).get("development_source", {}).get("state"),
            # History must retain predecessor bytes after the live projection
            # is advanced below; never retain a mutable alias into ``task``.
            "task_source": json.loads(json.dumps(task.get("source"))),
        }
        implementation = task.setdefault("implementation", {})
        if not isinstance(implementation, dict):
            raise DoctorError("current task implementation projection is malformed")
        development_source = implementation.setdefault("development_source", {})
        if not isinstance(development_source, dict):
            raise DoctorError("current task development source projection is malformed")
        history = task.setdefault("source_reconciliation_history", [])
        if not isinstance(history, list):
            raise DoctorError("current task source reconciliation history is malformed")
        reconciliation = {
            "schema": "tgw-context-source-reconciliation/v1",
            "recorded_at": now,
            "disposition": "EXPLICIT_EXACT_CANONICAL_SOURCE",
            "previous": previous,
            "successor": {"commit": head, "tree": tree},
            "semantic_reconciliation": "PENDING",
            "authority": False,
        }
        reconciliation["reconciliation_hash"] = _hash(reconciliation)
        history.append(reconciliation)
        development_source.update(
            {
                "state": "CANONICAL_SOURCE_ADVANCED_RECONCILIATION_PENDING",
                "commit": head,
                "tree": tree,
            }
        )
        source_projection = task.setdefault("source", {})
        if not isinstance(source_projection, dict):
            raise DoctorError("current task source projection is malformed")
        source_projection.update(
            {
                "repository": str(paths.repository),
                "commit": head,
                "tree": tree,
                "canonical_working_tree_clean": True,
            }
        )
        task["updated_at"] = now
    if explicit_source_reconciliation:
        # Bootstrap selects and verifies the immutable runtime before this
        # repair. Reconcile its semantic projection in the same
        # task/cursor/snapshot CAS so a fresh Doctor never treats the
        # predecessor release as desired state.
        workflow = _exact_bootstrap_workflow_projection(paths, selected)
        implementation = task.setdefault("implementation", {})
        if not isinstance(implementation, dict):
            raise DoctorError("current task implementation projection is malformed")
        previous_workflow = implementation.get("coding_workflow")
        if not isinstance(previous_workflow, Mapping):
            raise DoctorError("current task coding workflow projection is malformed")
        if dict(previous_workflow) != workflow:
            now = datetime.now().astimezone().isoformat()
            workflow_history = task.setdefault(
                "coding_workflow_reconciliation_history", []
            )
            if not isinstance(workflow_history, list):
                raise DoctorError(
                    "current task coding workflow reconciliation history is malformed"
                )
            reconciliation = {
                "schema": "tgw-context-coding-workflow-reconciliation/v1",
                "recorded_at": now,
                "disposition": "EXPLICIT_EXACT_INSTALLED_RUNTIME",
                "previous": json.loads(json.dumps(previous_workflow)),
                "successor": json.loads(json.dumps(workflow)),
                "authority": False,
            }
            reconciliation["reconciliation_hash"] = _hash(reconciliation)
            workflow_history.append(reconciliation)
            implementation["coding_workflow"] = workflow
            task["updated_at"] = now
        development_source = implementation.get("development_source")
        if isinstance(development_source, dict) and (
            development_source.get("commit"), development_source.get("tree")
        ) == (head, tree):
            development_source["state"] = "CANONICAL_SOURCE_CURRENT"
        source_history = task.get("source_reconciliation_history")
        if isinstance(source_history, list) and source_history:
            latest = source_history[-1]
            if (
                isinstance(latest, dict)
                and latest.get("successor") == {"commit": head, "tree": tree}
                and latest.get("semantic_reconciliation")
                != "CODING_RUNTIME_EXACT"
            ):
                latest["semantic_reconciliation"] = "CODING_RUNTIME_EXACT"
                latest.pop("reconciliation_hash", None)
                latest["reconciliation_hash"] = _hash(latest)
    migration_changes: list[str] = []
    transition_review_bindings: tuple[dict[str, Any], ...] = ()
    review_admission_projection = task.get("review_admission")
    transition_projection_required = (
        task_source != head
        or not isinstance(review_admission_projection, Mapping)
        or not isinstance(review_admission_projection.get("current"), Mapping)
        or task.get("next_binding_policy") != _NEXT_BINDING_POLICY
        or not isinstance(task.get("next_bindings"), Mapping)
    )
    if review_binding is not None or transition_projection_required:
        migration_changes, _migration_review_bindings = (
            _stage_context_transition_projections(
                paths,
                task,
                source_commit=head,
                source_tree=tree,
                review=review_binding,
            )
        )
    workflow_projection = task.get("implementation", {}).get("coding_workflow")
    if not isinstance(workflow_projection, Mapping):
        raise DoctorError("current task coding workflow projection is malformed")
    projection_changes = _reconcile_current_task_projections(
        paths,
        task,
        cursor,
        workflow=workflow_projection,
        source_commit=head,
        source_tree=tree,
    )
    if cursor.get("plan_commit") != task.get("plan", {}).get("approved_commit"):
        raise DoctorError("task and cursor Plan commits disagree; source-only repair is unsafe")
    capability = task.get("implementation", {}).get("development_source", {}).get("next_leaf")
    treatment = cursor.get("resolved", {}).get("next_treatment")
    if not isinstance(capability, str) or not isinstance(treatment, str) or treatment.rsplit(":", 1)[-1] != capability:
        raise DoctorError("task capability and cursor treatment disagree; repair is ambiguous")
    source_changed = cursor.get("source_commit") != head or cursor.get("source_tree") != tree
    if source_changed:
        cursor["source_commit"] = head
        cursor["source_tree"] = tree
        cursor["updated_at"] = datetime.now().astimezone().isoformat()
    _validate_current_task_projections(
        paths,
        task,
        cursor,
        source_commit=head,
        source_tree=tree,
    )
    # Publication always fences the complete successor projection.  Migration
    # is only one way these review paths can enter the task; an already-current
    # task must receive the same byte-level transactional protection.
    transition_review_bindings = _context_projection_review_bindings(task)
    paths.context_cursor.parent.mkdir(parents=True, exist_ok=True)
    task_fd, task_text = tempfile.mkstemp(
        prefix=paths.context_task.name + ".doctor-stage.",
        dir=paths.context_task.parent,
    )
    cursor_fd, cursor_text = tempfile.mkstemp(
        prefix=paths.context_cursor.name + ".doctor-stage.",
        dir=paths.context_cursor.parent,
    )
    snapshot_fd, snapshot_text = tempfile.mkstemp(
        prefix=paths.context_snapshot.name + ".doctor-stage.",
        dir=paths.context_snapshot.parent,
    )
    os.close(task_fd)
    os.close(cursor_fd)
    os.close(snapshot_fd)
    staged_task = Path(task_text)
    staged_cursor = Path(cursor_text)
    staged_snapshot = Path(snapshot_text)
    try:
        _atomic_json(staged_task, task)
        _atomic_json(staged_cursor, cursor)
        staged_snapshot.unlink(missing_ok=True)
        result = _run(
            [
                str(selected_publisher),
                "--task",
                str(staged_task),
                "--cursor",
                str(staged_cursor),
                "--output",
                str(staged_snapshot),
            ],
            env={
                "LANG": "C.UTF-8",
                "PATH": "/usr/bin:/bin",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(context_runtime),
            },
        )
        if result.returncode:
            raise DoctorError(result.stderr.strip() or "context publisher failed")
        staged_descriptor = os.open(
            staged_snapshot, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        primary_failure: Exception | None = None
        try:
            staged_state = os.fstat(staged_descriptor)
            if not stat.S_ISREG(staged_state.st_mode) or staged_state.st_nlink != 1:
                raise DoctorError(
                    "staged context publisher output is not a single-link regular file"
                )
            chunks: list[bytes] = []
            while True:
                chunk = os.read(staged_descriptor, 65_536)
                if not chunk:
                    break
                chunks.append(chunk)
            after_raw = b"".join(chunks)
            try:
                after = json.loads(after_raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise DoctorError("staged context publisher output is invalid JSON") from exc
            expanded_after = _validate_snapshot(
                after,
                after_raw,
                parser_path=selected["modules"]["current_context_snapshot"],
            )
            if expanded_after.get("task") != task or expanded_after.get("cursor") != cursor:
                raise DoctorError("staged context publisher output differs from exact inputs")
            os.fchown(
                staged_descriptor,
                paths.context_install_uid,
                paths.context_install_gid,
            )
            # The inherited descriptor is a root-only preflight capability.  It
            # is deliberately stricter than the separately installed live
            # snapshot, which is published as root:root 0444 below.
            os.fchmod(staged_descriptor, _CONTEXT_PREFLIGHT_SNAPSHOT_MODE)
            retained_state = os.fstat(staged_descriptor)
            if (
                not stat.S_ISREG(retained_state.st_mode)
                or retained_state.st_uid != paths.context_install_uid
                or retained_state.st_gid != paths.context_install_gid
                or stat.S_IMODE(retained_state.st_mode)
                != _CONTEXT_PREFLIGHT_SNAPSHOT_MODE
            ):
                raise DoctorError("staged context publisher output metadata did not retain")
            # Cold-start against this same retained open-file description.  The
            # probe rewinds it immediately before inheritance; no pathname is
            # consulted again for bytes or metadata.
            staged_probe = _probe_context_stdio(
                selected_launcher,
                _context_probe_actor(paths),
                expanded_after,
                staged_snapshot_descriptor=staged_descriptor,
                staged_snapshot_uid=paths.context_install_uid,
                staged_snapshot_gid=paths.context_install_gid,
            )
        except Exception as exc:
            primary_failure = exc
        descriptor_to_close = staged_descriptor
        staged_descriptor = -1
        try:
            # A failed close is never retried: POSIX leaves descriptor state
            # unspecified, and Linux has already released it for non-EBADF
            # close errors.  Invalidating our ownership first prevents reuse.
            os.close(descriptor_to_close)
        except Exception as close_exc:
            if primary_failure is not None:
                raise _preserve_primary_with_cleanup_failure(
                    primary_failure,
                    close_exc,
                    resource="staged Context snapshot descriptor",
                ).with_traceback(primary_failure.__traceback__)
            raise DoctorError(
                f"staged Context snapshot descriptor cleanup failed: {close_exc}"
            ) from close_exc
        if primary_failure is not None:
            raise primary_failure.with_traceback(primary_failure.__traceback__)
        changed = (
            before["task"] != task
            or before["cursor"] != cursor
            or before["snapshot"] != after
            or snapshot_surface["uid"] != paths.context_install_uid
            or snapshot_surface["gid"] != paths.context_install_gid
            or snapshot_surface["mode"] != _CONTEXT_SNAPSHOT_MODE
        )
        current_head, current_tree, current_status = _source_identity(paths)
        if current_status or (current_head, current_tree) != (head, tree):
            raise DoctorError("canonical source changed during context repair")
        current_plan_evidence = _plan_evidence_identity(paths, task)
        task_plan = task.get("plan", {})
        if current_plan_evidence is not None and current_plan_evidence != (
            task_plan.get("evidence_commit"),
            task_plan.get("evidence_tree"),
        ):
            raise DoctorError("current Plan evidence changed during context repair")
        if _surface_snapshot(paths.context_task) != task_surface or _surface_snapshot(paths.context_cursor) != cursor_surface or _surface_snapshot(paths.context_snapshot) != snapshot_surface:
            raise DoctorError("context inputs changed concurrently; no live file was replaced")
        for binding in transition_review_bindings:
            if _surface_snapshot(Path(str(binding["path"]))) != binding["surface"]:
                raise DoctorError(
                    f"{binding['label']} changed during Context repair"
                )
        committed_task: dict[str, Any] | None = None
        committed_cursor: dict[str, Any] | None = None
        committed_snapshot: dict[str, Any] | None = None
        try:
            # Task and cursor are non-live publisher inputs. Commit them first;
            # the one snapshot CAS below remains the sole MCP-visible cutover.
            if before["task"] == task:
                committed_task = task_surface
            else:
                committed_task = _cas_regular_file(
                    paths.context_task,
                    task_surface,
                    _json_bytes(task),
                    mode=task_surface["mode"],
                    uid=task_surface["uid"],
                    gid=task_surface["gid"],
                )
            if before["cursor"] == cursor:
                committed_cursor = cursor_surface
            else:
                committed_cursor = _cas_regular_file(
                    paths.context_cursor,
                    cursor_surface,
                    _json_bytes(cursor),
                    mode=cursor_surface["mode"],
                    uid=cursor_surface["uid"],
                    gid=cursor_surface["gid"],
                )
            if _surface_snapshot(paths.context_task) != committed_task or _surface_snapshot(paths.context_cursor) != committed_cursor:
                raise DoctorError("Context inputs changed before atomic snapshot cutover")
            snapshot_metadata_exact = (
                snapshot_surface["uid"] == paths.context_install_uid
                and snapshot_surface["gid"] == paths.context_install_gid
                and snapshot_surface["mode"] == _CONTEXT_SNAPSHOT_MODE
            )
            if before["snapshot"] == after and snapshot_metadata_exact:
                committed_snapshot = snapshot_surface
            else:
                committed_snapshot = _cas_regular_file(
                    paths.context_snapshot,
                    snapshot_surface,
                    after_raw,
                    mode=_CONTEXT_SNAPSHOT_MODE,
                    uid=paths.context_install_uid,
                    gid=paths.context_install_gid,
                )
            if (
                _surface_snapshot(paths.context_task) != committed_task
                or _surface_snapshot(paths.context_cursor) != committed_cursor
                or _surface_snapshot(paths.context_snapshot) != committed_snapshot
            ):
                raise DoctorError("final Context transaction verification failed")
            published_head, published_tree, published_status = _source_identity(paths)
            if published_status or (published_head, published_tree) != (head, tree):
                raise DoctorError(
                    "canonical source changed across atomic Context publication"
                )
            published_plan_evidence = _plan_evidence_identity(paths, task)
            if published_plan_evidence != current_plan_evidence:
                raise DoctorError(
                    "current Plan evidence changed across atomic Context publication"
                )
            for binding in transition_review_bindings:
                if (
                    _surface_snapshot(Path(str(binding["path"])))
                    != binding["surface"]
                ):
                    raise DoctorError(
                        f"{binding['label']} changed across atomic Context publication"
                    )
            receipt = _receipt(
                paths,
                "context",
                before,
                {"task": task, "cursor": cursor, "snapshot": after},
            )
            receipt_path = Path(receipt)
            receipt_surface = _surface_snapshot(receipt_path)
            try:
                if (
                    _surface_snapshot(paths.context_task) != committed_task
                    or _surface_snapshot(paths.context_cursor) != committed_cursor
                    or _surface_snapshot(paths.context_snapshot) != committed_snapshot
                ):
                    raise DoctorError(
                        "Context transaction changed during receipt publication"
                    )
                receipted_head, receipted_tree, receipted_status = _source_identity(
                    paths
                )
                if receipted_status or (receipted_head, receipted_tree) != (
                    head,
                    tree,
                ):
                    raise DoctorError(
                        "canonical source changed during Context receipt publication"
                    )
                if _plan_evidence_identity(paths, task) != current_plan_evidence:
                    raise DoctorError(
                        "current Plan evidence changed during Context receipt publication"
                    )
                for binding in transition_review_bindings:
                    if (
                        _surface_snapshot(Path(str(binding["path"])))
                        != binding["surface"]
                    ):
                        raise DoctorError(
                            f"{binding['label']} changed during Context receipt publication"
                        )
            except Exception as evidence_exc:
                try:
                    _remove_exact_receipt(receipt_path, receipt_surface)
                except Exception as cleanup_exc:
                    raise _ReceiptPublicationAmbiguous(
                        receipt_path,
                        "Context changed during success receipt publication "
                        f"({evidence_exc}); exact receipt cleanup is ambiguous "
                        f"({cleanup_exc})",
                    ) from evidence_exc
                raise
        except Exception as exc:
            if isinstance(exc, _ReceiptPublicationAmbiguous):
                # Success bytes may already be durable.  Keep the exact
                # task/cursor/snapshot transaction paired with those bytes and
                # fail explicitly so replay can establish a terminal result.
                raise
            rollback_errors: list[str] = []
            rollback_states: list[str] = []
            for label, path, committed, original in (
                (
                    "snapshot",
                    paths.context_snapshot,
                    committed_snapshot,
                    snapshot_surface,
                ),
                ("cursor", paths.context_cursor, committed_cursor, cursor_surface),
                ("task", paths.context_task, committed_task, task_surface),
            ):
                if committed is None:
                    continue
                try:
                    current = _surface_snapshot(path)
                    if _same_regular_surface(current, original):
                        rollback_states.append(f"{label} already original")
                        continue
                    if current != committed:
                        raise DoctorError(f"{label} changed concurrently; refused rollback overwrite")
                    restored = _cas_regular_file(
                        path,
                        current,
                        original["raw"],
                        mode=original["mode"],
                        uid=original["uid"],
                        gid=original["gid"],
                    )
                    expected_semantics = _surface_semantics(original)
                    if _surface_semantics(restored) != expected_semantics:
                        raise DoctorError(f"{label} rollback did not restore original semantics")
                    rollback_states.append(f"{label} restored")
                except Exception as rollback_exc:
                    rollback_errors.append(f"{label}: {rollback_exc}")
            suffix = "; rollback errors: " + "; ".join(rollback_errors) if rollback_errors else "; " + ", ".join(rollback_states) if rollback_states else "; no live file was replaced"
            raise DoctorError(f"context commit failed: {exc}{suffix}") from exc
    finally:
        staged_task.unlink(missing_ok=True)
        staged_cursor.unlink(missing_ok=True)
        staged_snapshot.unlink(missing_ok=True)
    restart_report = _context_restart_report(paths)
    return {
        "ok": True,
        "operation": "context",
        "changed": changed,
        "receipt": receipt,
        "staged_cold_stdio_probe": staged_probe,
        "projection_changes": sorted(set(projection_changes + migration_changes)),
        "review_evidence": (
            {
                "path": review_binding["path"],
                "sha256": review_binding["sha256"],
                "authority": False,
            }
            if review_binding is not None
            else None
        ),
        **restart_report,
    }


def _replace_link(path: Path, target: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_text = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    os.close(descriptor)
    temporary = Path(temporary_text)
    temporary.unlink()
    try:
        os.symlink(str(target), temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def repair_runtime(
    paths: DoctorPaths, *, desired_commit: str | None = None
) -> dict[str, Any]:
    _require_root()
    if desired_commit is None:
        desired, release, _task = _desired_runtime(paths)
    else:
        if _COMMIT.fullmatch(desired_commit) is None:
            raise DoctorError("runtime repair commit is invalid")
        desired = desired_commit
        release = paths.runtime_root / "releases" / desired
    head, _tree, status = _source_identity(paths)
    if status or head != desired:
        raise DoctorError("runtime repair requires clean canonical source at the declared commit")
    release_tree = _verify_release_tree(paths, desired, release)
    current_link = paths.runtime_root / "current"
    if current_link.exists() and not current_link.is_symlink():
        raise DoctorError("runtime selector is not a symlink; automatic replacement is unsafe")
    launcher_links = _launcher_links(paths)
    for destination, target in launcher_links.items():
        source = release / "bin" / target.name
        if not source.is_file():
            raise DoctorError(f"declared release lacks launcher {source}")
        if not destination.is_symlink() or os.readlink(destination) != str(target):
            raise DoctorError(f"fixed launcher drift requires bounded bootstrap repair: {destination}")
    operator_source = release / "bin/tgw-operator"
    operator_before = _operator_launcher_identity(paths, operator_source)
    if not operator_before["exact"]:
        raise DoctorError(
            f"fixed launcher drift requires bounded bootstrap repair: {paths.operator_cli}"
        )
    bootstrap_source = release / "bin/tgw-coding-bootstrap"
    bootstrap_before = _fixed_root_launcher_identity(
        paths, paths.coding_bootstrap, bootstrap_source
    )
    if not bootstrap_before["exact"]:
        raise DoctorError(
            f"fixed launcher drift requires bounded bootstrap repair: {paths.coding_bootstrap}"
        )
    previous_selector = os.readlink(current_link) if current_link.is_symlink() else None
    before = {
        "current_link": previous_selector,
        "current": str(current_link.resolve(strict=False)),
        "launchers": {
            str(path): {
                "kind": "symlink",
                "target": os.readlink(path),
                "sha256": _file_hash(path) if path.is_file() else None,
            }
            for path in launcher_links
        },
        "operator_launcher": operator_before,
        "bootstrap_launcher": bootstrap_before,
    }
    changed = current_link.resolve(strict=False) != release.resolve()
    try:
        if changed:
            _replace_link(current_link, Path("releases") / desired)
        _verify_release_tree(paths, desired, release)
    except Exception as exc:
        rollback_errors = []
        installed_selector = str(Path("releases") / desired)
        selector_still_ours = (
            current_link.is_symlink()
            and os.readlink(current_link) == installed_selector
        )
        if not changed:
            rollback_errors.append(
                "selector was not changed by this repair; no rollback attempted"
            )
        elif not selector_still_ours:
            rollback_errors.append(
                "concurrent selector change preserved; automatic rollback refused"
            )
        elif previous_selector is not None:
            try:
                _replace_link(current_link, Path(previous_selector))
            except Exception as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        elif current_link.is_symlink():
            try:
                current_link.unlink()
            except Exception as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        suffix = "; rollback errors: " + "; ".join(rollback_errors) if rollback_errors else "; original selector restored"
        raise DoctorError(f"runtime commit failed: {exc}{suffix}") from exc
    if (
        not current_link.is_symlink()
        or os.readlink(current_link) != str(Path("releases") / desired)
        or current_link.resolve(strict=True) != release.resolve(strict=True)
    ):
        raise DoctorError("runtime selector changed before repair completion")
    after = {
        "current_link": os.readlink(current_link),
        "current": str(current_link.resolve(strict=True)),
        "launchers": {str(path): {"target": os.readlink(path), "sha256": _file_hash(path)} for path in launcher_links},
        "operator_launcher": _operator_launcher_identity(paths, operator_source),
        "bootstrap_launcher": _fixed_root_launcher_identity(
            paths, paths.coding_bootstrap, bootstrap_source
        ),
        "release_tree": release_tree,
    }
    receipt = _receipt(paths, "runtime", before, after)
    return {"ok": True, "operation": "runtime", "changed": changed, "receipt": receipt}


def repair_database(
    paths: DoctorPaths, *, desired_commit: str | None = None
) -> dict[str, Any]:
    _require_root()
    if desired_commit is None:
        desired, release, _task = _desired_runtime(paths)
    else:
        if _COMMIT.fullmatch(desired_commit) is None:
            raise DoctorError("database repair commit is invalid")
        desired = desired_commit
        release = paths.runtime_root / "releases" / desired
    verification = _verify_release_tree(paths, desired, release)
    tree = str(verification.get("tree", ""))
    if _COMMIT.fullmatch(tree) is None:
        raise DoctorError("verified coding release has no exact Git tree identity")
    try:
        sql_mode, migration_bytes = read_exact_tree_file(
            paths.repository,
            commit=desired,
            tree=tree,
            path="config/tgw-coding-local-roles.sql",
        )
        if sql_mode != 0o644:
            raise DoctorError("exact coding role SQL mode differs")
        migration = migration_bytes.decode("utf-8")
        ident_mode, ident_bytes = read_exact_tree_file(
            paths.repository,
            commit=desired,
            tree=tree,
            path="config/environment/postgresql/pg_ident.conf",
        )
        if ident_mode != 0o644:
            raise DoctorError("exact pg_ident materialization mode differs")
        hba_mode, hba_bytes = read_exact_tree_file(
            paths.repository,
            commit=desired,
            tree=tree,
            path="config/environment/postgresql/pg_hba.conf",
        )
        if hba_mode != 0o644:
            raise DoctorError("exact pg_hba materialization mode differs")
        managed_line = next(
            (
                line
                for line in hba_bytes.decode("utf-8").splitlines()
                if _PEER_HBA_LINE.match(line)
            ),
            None,
        )
        if managed_line is None:
            raise DoctorError("exact pg_hba materialization has no tgw_coding peer line")
    except (ValueError, UnicodeDecodeError) as exc:
        raise DoctorError(f"cannot read the verified coding role SQL: {exc}") from exc
    before = {
        "database": check_database(paths),
        "peer_auth": check_database_peer_auth(paths),
    }
    result = _run(
        [
            "sudo",
            "-n",
            "-u",
            "postgres",
            "psql",
            "--dbname=tgw_lib_dev_state_machine",
        ],
        timeout=60,
        input=migration,
    )
    if result.returncode:
        raise DoctorError(result.stderr.strip() or "database role repair failed")
    _materialize_peer_auth(
        paths,
        ident_bytes=ident_bytes,
        managed_line=managed_line,
    )
    reload_result = _run(
        [
            "sudo",
            "-n",
            "-u",
            "postgres",
            "psql",
            "--dbname=tgw_lib_dev_state_machine",
            "--no-align",
            "--tuples-only",
            "--command",
            "SELECT pg_reload_conf();",
        ],
        timeout=30,
    )
    if reload_result.returncode or reload_result.stdout.strip() != "t":
        raise DoctorError("PostgreSQL peer-auth configuration reload failed")
    after = {
        "database": check_database(paths),
        "peer_auth": check_database_peer_auth(paths),
    }
    if after["database"]["state"] != "PASS":
        raise DoctorError("database schema or grants remain incomplete after repair")
    if after["peer_auth"]["state"] != "PASS":
        raise DoctorError("peer-auth host materialization remains incomplete after repair")
    receipt = _receipt(paths, "database", before, after)
    return {"ok": True, "operation": "database", "changed": before != after, "receipt": receipt}


def _set_shared_fd(descriptor: int, group_gid: int, *, directory: bool) -> None:
    state = os.fstat(descriptor)
    mode = stat.S_IMODE(state.st_mode)
    os.fchown(descriptor, -1, group_gid)
    if directory:
        os.fchmod(descriptor, mode | stat.S_ISGID | stat.S_IRWXG)
    else:
        os.fchmod(descriptor, mode | stat.S_IRGRP | stat.S_IWGRP)


def _set_shared_directory(path: Path, group_gid: int) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open(path, flags)
    try:
        _set_shared_fd(descriptor, group_gid, directory=True)
    finally:
        os.close(descriptor)


_PACK_COMPONENT = re.compile(r"pack-[0-9a-f]{40}(?:[0-9a-f]{24})?\.(?:pack|idx|rev|bitmap)\Z")
_RENAME_EXCHANGE = 2

_PRE_LEDGER_PLAN_COMMIT = "058e2f980201cc78245358e4901cf007063f2c29"
_PRE_LEDGER_SOLUTION_HASH = "sha256:ecce15aad2699492c0c5577bff1af7005ffbbec6ae6166b325b34c1cc7e70e9f"
_PRE_LEDGER_PRESERVATION = {
    1744: {
        "worktree": "/opt/TGW/var/worktrees/todo-1744-plan-4bc1dcdb201d08f581311b1a",
        "source_commit": "c29d29d9844b0c37a1a68f6951a62c1f88c9db60",
        "source_tree": "a67b2f180baeb1d20a4be024ebd385191d47be4e",
        "candidate_commit": "14753ce93bfc5d29253611719377a717112db750",
        "candidate_tree": "25ce0a73657dfe8d89569149178113e0d1affa32",
        "receipt_sha256": "976d6497be352fa71af1706f9e0addae3caaa6306f4b677d7263679428c2dcd3",
        "manifest": "77aa72359e34ca25dbbc7e0822b7a787851d7c00d5286813e1dc7b12629a5b78.json",
    },
    1752: {
        "worktree": "/opt/TGW/var/worktrees/todo-1752-plan-e34c4dec66cae45bc97a2d77",
        "source_commit": "14753ce93bfc5d29253611719377a717112db750",
        "source_tree": "25ce0a73657dfe8d89569149178113e0d1affa32",
        "candidate_commit": "529f05e2205a3c83036874f58a03a9842807e463",
        "candidate_tree": "c5a85c30304a72652dee38cea883d186a954eae2",
        "receipt_sha256": "6ad3106c43ba0d510b5d90b593bf6bc5bd7d1c75760c85d9680ec28289aa4598",
        "manifest": "0b8262d95ad0d57df9173b4a3c56f86c8475c19d06b6b17dcf2f3e09b1ce026e.json",
    },
}

_FICLONE = 0x40049409
_STABLE_FILE_FIELDS = (
    "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
    "st_size", "st_atime_ns", "st_mtime_ns", "st_ctime_ns",
)
_STABLE_DIRECTORY_FIELDS = (
    "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
    "st_size", "st_mtime_ns", "st_ctime_ns",
)


def _trusted_preservation_directory(state: os.stat_result, *, db_uid: int, group_gid: int) -> bool:
    return (
        stat.S_ISDIR(state.st_mode)
        and state.st_nlink == 1
        and state.st_uid == db_uid
        and state.st_gid == group_gid
        and stat.S_IMODE(state.st_mode) == 0o2775
    )


def _revalidate_preservation_directory(
    worktree_descriptor: int,
    preservation_descriptor: int,
    expected: os.stat_result,
    *,
    todo_id: int,
) -> None:
    """Prove the authenticated preservation directory and path are unchanged."""
    current = os.fstat(preservation_descriptor)
    # Directory enumeration can legitimately advance atime on filesystems
    # where O_NOATIME is unavailable.  Every trust-bearing metadata mutation
    # still changes one of mode/ownership/size/mtime/ctime below.
    fields = _STABLE_DIRECTORY_FIELDS
    if any(getattr(expected, field) != getattr(current, field) for field in fields):
        raise DoctorError(f"Todo {todo_id} preservation directory metadata changed")
    visible = os.stat(
        ".tgw-coding-preservation",
        dir_fd=worktree_descriptor,
        follow_symlinks=False,
    )
    if any(getattr(expected, field) != getattr(visible, field) for field in fields):
        raise DoctorError(f"Todo {todo_id} preservation directory binding changed")


def _stable_descriptor_bytes(
    descriptor: int,
    *,
    identity_descriptor: int | None = None,
    source_generation: os.stat_result | None = None,
    maximum: int = 128 * 1024 * 1024,
) -> tuple[bytes, os.stat_result, os.stat_result]:
    """Read a pinned regular file twice and prove metadata/content stability."""
    identity_descriptor = descriptor if identity_descriptor is None else identity_descriptor
    before = os.fstat(identity_descriptor)
    if source_generation is not None and any(
        getattr(source_generation, field) != getattr(before, field)
        for field in _STABLE_FILE_FIELDS
    ):
        raise DoctorError("preservation source changed before stable read")
    if not stat.S_ISREG(before.st_mode) or before.st_size < 0 or before.st_size > maximum:
        raise DoctorError("preservation evidence is not a bounded regular file")
    offset = os.lseek(descriptor, 0, os.SEEK_CUR)
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        reads: list[bytes] = []
        for _pass in range(2):
            os.lseek(descriptor, 0, os.SEEK_SET)
            raw = bytearray()
            while len(raw) < before.st_size:
                chunk = os.read(descriptor, min(1024 * 1024, before.st_size - len(raw)))
                if not chunk:
                    raise DoctorError("preservation evidence was truncated while pinned")
                raw.extend(chunk)
            if os.read(descriptor, 1):
                raise DoctorError("preservation evidence grew while pinned")
            reads.append(bytes(raw))
        after = os.fstat(identity_descriptor)
        if any(getattr(before, field) != getattr(after, field) for field in _STABLE_FILE_FIELDS):
            raise DoctorError("preservation evidence changed while pinned")
        if reads[0] != reads[1] or hashlib.sha256(reads[0]).digest() != hashlib.sha256(reads[1]).digest():
            raise DoctorError("preservation evidence content changed while pinned")
        return reads[0], before, after
    finally:
        os.lseek(descriptor, offset, os.SEEK_SET)


def _open_preservation_file(
    parent: int,
    name: str,
    *,
    snapshot_parent: int,
    snapshot_group_gid: int,
) -> tuple[int, int, os.stat_result]:
    """Open evidence without changing source metadata.

    When the kernel denies O_NOATIME, pin the source descriptor and reflink it
    into private, same-filesystem disposable storage.  The unlinked clone is the
    read descriptor and the original descriptor remains pinned for complete
    before/after identity and metadata checks.
    """
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | getattr(os, "O_NOATIME", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent)
        return descriptor, descriptor, os.fstat(descriptor)
    except PermissionError:
        source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    snapshot = -1
    try:
        source_state = os.fstat(source)  # immediately before FICLONE
        directory_state = os.fstat(snapshot_parent)
        if not stat.S_ISREG(source_state.st_mode) or directory_state.st_dev != source_state.st_dev:
            raise DoctorError("pinned preservation snapshot directory is not on the source filesystem")
        anonymous_flags = os.O_RDWR | os.O_CLOEXEC | getattr(os, "O_TMPFILE", 0)
        if not getattr(os, "O_TMPFILE", 0):
            raise DoctorError("anonymous preservation snapshots are unsupported")
        try:
            snapshot = os.open(".", anonymous_flags, 0o600, dir_fd=snapshot_parent)
        except OSError as exc:
            raise DoctorError("anonymous preservation snapshots are unsupported") from exc
        clone_before = os.fstat(snapshot)
        if (
            clone_before.st_dev != source_state.st_dev
            or not stat.S_ISREG(clone_before.st_mode)
            or stat.S_IMODE(clone_before.st_mode) != 0o600
            or clone_before.st_nlink != 0
            or clone_before.st_uid != os.geteuid()
            or clone_before.st_gid != snapshot_group_gid
        ):
            raise DoctorError("anonymous preservation snapshot identity is untrusted")
        try:
            fcntl.ioctl(snapshot, _FICLONE, source)
        except OSError as exc:
            raise DoctorError("permission-safe preservation reflink is unsupported") from exc
        source_after = os.fstat(source)  # immediately after FICLONE
        if any(getattr(source_state, field) != getattr(source_after, field) for field in _STABLE_FILE_FIELDS):
            raise DoctorError("preservation source changed inside the reflink window")
        clone_state = os.fstat(snapshot)
        if (
            clone_state.st_dev != clone_before.st_dev
            or clone_state.st_ino != clone_before.st_ino
            or clone_state.st_mode != clone_before.st_mode
            or clone_state.st_nlink != 0
            or clone_state.st_uid != clone_before.st_uid
            or clone_state.st_gid != clone_before.st_gid
            or clone_state.st_size != source_state.st_size
        ):
            raise DoctorError("preservation reflink identity is ambiguous")
        # Carry the exact source generation captured at the clone boundary to
        # the later stable read.  Reflink bytes are not trusted if the pinned
        # source changes after FICLONE, even when it remains the same inode.
        return snapshot, source, source_after
    except BaseException:
        if snapshot >= 0:
            os.close(snapshot)
        os.close(source)
        raise


def _git_at_descriptor(descriptor: int, *arguments: str) -> str:
    location = f"/proc/self/fd/{descriptor}"
    result = subprocess.run(
        [
            GIT_EXECUTABLE,
            "--no-replace-objects",
            "-c",
            f"safe.directory={location}",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.attributesFile=/dev/null",
            "-C",
            location,
            *arguments,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        pass_fds=(descriptor,),
        env=dict(protected_git_environment()),
    )
    if result.returncode:
        raise DoctorError(result.stderr.strip() or "descriptor-bound Git identity failed")
    return result.stdout.strip()


def _authenticate_pre_ledger_preservation(worktree: Path, descriptor: int, group_gid: int) -> dict[str, Any] | None:
    fixture_row = next(((todo, row) for todo, row in _PRE_LEDGER_PRESERVATION.items() if row["worktree"] == str(worktree)), None)
    if fixture_row is None:
        return None
    todo_id, expected = fixture_row
    preservation_fd = -1
    receipt_fd = -1
    receipt_identity_fd = -1
    manifest_fd = -1
    manifest_identity_fd = -1
    try:
        preservation_fd = os.open(
            ".tgw-coding-preservation",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=descriptor,
        )
        preservation_state = os.fstat(preservation_fd)
        if not _trusted_preservation_directory(
            preservation_state,
            db_uid=pwd.getpwnam("db").pw_uid,
            group_gid=group_gid,
        ):
            raise DoctorError(f"Todo {todo_id} preservation directory is untrusted")
        try:
            history = os.stat(".tgw-coding-history", dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            history = None
        if history is not None:
            return None
        if (_git_at_descriptor(descriptor, "rev-parse", "HEAD"), _git_at_descriptor(descriptor, "rev-parse", "HEAD^{tree}")) != (expected["candidate_commit"], expected["candidate_tree"]):
            raise DoctorError(f"Todo {todo_id} pre-ledger Git candidate identity differs")
        receipt_fd, receipt_identity_fd, receipt_generation = _open_preservation_file(
            descriptor,
            "implementation-receipt.json",
            snapshot_parent=preservation_fd,
            snapshot_group_gid=group_gid,
        )
        names = os.listdir(preservation_fd)
        if len(names) != 1 or names[0] != expected["manifest"]:
            raise DoctorError(f"Todo {todo_id} pre-ledger preservation set differs")
        manifest_fd, manifest_identity_fd, manifest_generation = _open_preservation_file(
            preservation_fd,
            expected["manifest"],
            snapshot_parent=preservation_fd,
            snapshot_group_gid=group_gid,
        )
        receipt_raw, receipt_before, receipt_state = _stable_descriptor_bytes(
            receipt_fd,
            identity_descriptor=receipt_identity_fd,
            source_generation=receipt_generation,
        )
        manifest_raw, manifest_before, manifest_state = _stable_descriptor_bytes(
            manifest_fd,
            identity_descriptor=manifest_identity_fd,
            source_generation=manifest_generation,
        )
        try:
            receipt, manifest = json.loads(receipt_raw), json.loads(manifest_raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DoctorError(f"Todo {todo_id} pre-ledger evidence is malformed") from exc
        binding = receipt.get("plan_binding") if isinstance(receipt, dict) else None
        identity = binding.get("worktree_identity") if isinstance(binding, dict) else None
        root = binding.get("execution_root") if isinstance(binding, dict) else None
        closed = [row for row in receipt.get("artifacts", []) if isinstance(row, dict) and row.get("kind") == "closed_candidate"] if isinstance(receipt, dict) else []
        manifest_binding = manifest.get("binding") if isinstance(manifest, dict) else None
        unsigned = dict(manifest) if isinstance(manifest, dict) else {}
        claimed_hash = unsigned.pop("manifest_hash", None)
        actual_hash = "sha256:" + hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        exact = (
            isinstance(receipt, dict)
            and receipt.get("receipt_schema_id") == "receipt/tgw-development/v1"
            and receipt.get("status") == "PASS"
            and receipt.get("outcome") == "satisfied"
            and receipt.get("treatment_id") == "codex-implement"
            and receipt.get("treatment_version") == "1"
            and isinstance(binding, dict)
            and binding.get("plan_commit") == _PRE_LEDGER_PLAN_COMMIT
            and binding.get("solution_hash") == _PRE_LEDGER_SOLUTION_HASH
            and binding.get("source_commit") == expected["source_commit"]
            and binding.get("worktree") == str(worktree)
            and binding.get("requested_worktree_identity") == "unix:codex"
            and isinstance(root, dict)
            and root.get("schema") == "tgw-execution-root/v1"
            and root.get("kind") == "todo"
            and root.get("todo_id") == todo_id
            and set(root) == {"identity_hash", "kind", "schema", "todo_id"}
            and isinstance(identity, dict)
            and identity.get("actor") == "codex"
            and identity.get("todo_id") == todo_id
            and identity.get("worktree") == str(worktree)
            and identity.get("head") == expected["source_commit"]
            and identity.get("repository_root") == "/opt/TGW/tgw-lib/src/trader-grims-warehouse"
            and identity.get("branch") == f"coding/codex/{worktree.name}"
            and closed == [{"kind": "closed_candidate", "commit": expected["candidate_commit"], "tree": expected["candidate_tree"]}]
            and manifest.get("schema") == "tgw-coding-preservation-manifest/v2"
            and manifest_binding
            == {
                "actor": "codex",
                "plan_commit": _PRE_LEDGER_PLAN_COMMIT,
                "solution_hash": _PRE_LEDGER_SOLUTION_HASH,
                "source_commit": expected["source_commit"],
                "source_tree": expected["source_tree"],
                "todo_id": todo_id,
                "treatment_id": "codex-implement",
                "treatment_version": "1",
                "worktree": str(worktree),
            }
            and manifest.get("source", {}).get("head") == expected["candidate_commit"]
            and manifest.get("source", {}).get("tree") == expected["candidate_tree"]
            and hashlib.sha256(receipt_raw).hexdigest() == expected["receipt_sha256"]
            and manifest_raw == (json.dumps(manifest, sort_keys=True) + "\n").encode()
            and claimed_hash == actual_hash
            and expected["manifest"] == actual_hash.removeprefix("sha256:") + ".json"
        )
        db_uid, codex_uid = pwd.getpwnam("db").pw_uid, pwd.getpwnam("codex").pw_uid
        if (
            not exact
            or receipt_state.st_nlink != 1
            or receipt_state.st_uid != codex_uid
            or receipt_state.st_gid != group_gid
            or manifest_state.st_nlink != 1
            or manifest_state.st_uid != db_uid
            or manifest_state.st_gid != group_gid
            or stat.S_IMODE(manifest_state.st_mode) not in {0o440, 0o460}
        ):
            raise DoctorError(f"Todo {todo_id} pre-ledger receipt ownership or binding differs")
        # Close every post-read race while all evidence descriptors remain pinned.
        # A newly-created modern ledger, ref movement, directory-entry replacement,
        # or preservation-set addition makes the legacy exception inapplicable.
        try:
            os.stat(".tgw-coding-history", dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise DoctorError(f"Todo {todo_id} acquired coding history during authentication")
        if sorted(os.listdir(preservation_fd)) != [expected["manifest"]]:
            raise DoctorError(f"Todo {todo_id} pre-ledger preservation set changed")
        receipt_path_state = os.stat("implementation-receipt.json", dir_fd=descriptor, follow_symlinks=False)
        manifest_path_state = os.stat(expected["manifest"], dir_fd=preservation_fd, follow_symlinks=False)
        if (receipt_path_state.st_dev, receipt_path_state.st_ino) != (receipt_state.st_dev, receipt_state.st_ino):
            raise DoctorError(f"Todo {todo_id} pre-ledger receipt path changed")
        if (manifest_path_state.st_dev, manifest_path_state.st_ino) != (manifest_state.st_dev, manifest_state.st_ino):
            raise DoctorError(f"Todo {todo_id} pre-ledger manifest path changed")
        if (_git_at_descriptor(descriptor, "rev-parse", "HEAD"), _git_at_descriptor(descriptor, "rev-parse", "HEAD^{tree}")) != (
            expected["candidate_commit"],
            expected["candidate_tree"],
        ):
            raise DoctorError(f"Todo {todo_id} pre-ledger Git candidate changed during authentication")
        _revalidate_preservation_directory(
            descriptor,
            preservation_fd,
            preservation_state,
            todo_id=todo_id,
        )
        return {
            "todo_id": todo_id,
            "relative": Path(".tgw-coding-preservation") / expected["manifest"],
            "descriptor": os.dup(manifest_fd),
            "before": manifest_state,
            "preservation_descriptor": os.dup(preservation_fd),
            "preservation_before": preservation_state,
            "stable_read": {
                "receipt_before": receipt_before,
                "receipt_after": receipt_state,
                "manifest_before": manifest_before,
                "manifest_after": manifest_state,
                "receipt_sha256": "sha256:" + hashlib.sha256(receipt_raw).hexdigest(),
                "manifest_sha256": "sha256:" + hashlib.sha256(manifest_raw).hexdigest(),
            },
            "receipt_sha256": "sha256:" + hashlib.sha256(receipt_raw).hexdigest(),
            "manifest_hash": actual_hash,
        }
    finally:
        if manifest_fd >= 0:
            os.close(manifest_fd)
            if manifest_identity_fd >= 0 and manifest_identity_fd != manifest_fd:
                os.close(manifest_identity_fd)
        if preservation_fd >= 0:
            os.close(preservation_fd)
        if receipt_fd >= 0:
            os.close(receipt_fd)
        if receipt_identity_fd >= 0 and receipt_identity_fd != receipt_fd:
            os.close(receipt_identity_fd)


def _rename_exchange_at(
    first_directory_descriptor: int,
    first: str,
    second_directory_descriptor: int,
    second: str,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise DoctorError("renameat2 is unavailable; atomic file exchange is unsafe")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        first_directory_descriptor,
        os.fsencode(first),
        second_directory_descriptor,
        os.fsencode(second),
        _RENAME_EXCHANGE,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise DoctorError(f"cannot atomically exchange files: {os.strerror(error)}")


def _rename_exchange(directory_descriptor: int, first: str, second: str) -> None:
    _rename_exchange_at(
        directory_descriptor,
        first,
        directory_descriptor,
        second,
    )


def _detach_pack_hardlink(
    parent_descriptor: int,
    name: str,
    source_descriptor: int,
    group_gid: int,
    journal: list[dict[str, Any]] | None = None,
) -> None:
    """Replace one canonical pack alias without changing its external hardlink."""
    before = os.fstat(source_descriptor)
    temporary = f".{name}.tgw-doctor-{os.getpid()}-{secrets.token_hex(8)}"
    destination = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o400,
        dir_fd=parent_descriptor,
    )
    committed = False
    exchanged = False
    try:
        while True:
            chunk = os.read(source_descriptor, 1024 * 1024)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(destination, view)
                if written <= 0:
                    raise DoctorError("short write while detaching Git pack hardlink")
                view = view[written:]
        after = os.fstat(source_descriptor)
        stable = all(
            getattr(before, field) == getattr(after, field)
            for field in (
                "st_dev",
                "st_ino",
                "st_size",
                "st_mode",
                "st_uid",
                "st_gid",
                "st_mtime_ns",
                "st_ctime_ns",
            )
        )
        if not stable:
            raise DoctorError(f"Git pack hardlink changed while detaching: {name}")
        current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
            raise DoctorError(f"Git pack path changed while detaching: {name}")
        os.fchown(destination, -1, group_gid)
        os.fchmod(destination, (stat.S_IMODE(before.st_mode) | stat.S_IRGRP) & ~0o222)
        os.fsync(destination)
        _rename_exchange(parent_descriptor, temporary, name)
        exchanged = True
        displaced = os.stat(temporary, dir_fd=parent_descriptor, follow_symlinks=False)
        if (displaced.st_dev, displaced.st_ino) != (before.st_dev, before.st_ino):
            try:
                _rename_exchange(parent_descriptor, temporary, name)
                exchanged = False
            except DoctorError as rollback_error:
                raise DoctorError(f"Git pack path raced and exchange rollback failed: {name}; {rollback_error}") from rollback_error
            raise DoctorError(f"Git pack path raced during detachment: {name}")
        if journal is not None:
            journal.append({"kind": "exchange", "parent": os.dup(parent_descriptor), "name": name, "backup": temporary})
            exchanged = False
        else:
            try:
                os.unlink(temporary, dir_fd=parent_descriptor)
                exchanged = False
            except OSError as unlink_error:
                try:
                    _rename_exchange(parent_descriptor, temporary, name)
                    exchanged = False
                except DoctorError as rollback_error:
                    raise DoctorError(f"cannot remove detached Git pack alias or roll back: {name}; {rollback_error}") from unlink_error
                raise DoctorError(f"cannot remove detached Git pack alias; original restored: {name}") from unlink_error
        committed = True
        os.fsync(parent_descriptor)
    finally:
        os.close(destination)
        if not committed and not exchanged:
            try:
                os.unlink(temporary, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass


def _scan_shared_git_tree(
    root: Path | int,
    group_gid: int,
    *,
    mutate: bool,
    excluded_root_entries: Sequence[str] = (),
    immutable_files: Sequence[Path] = (),
    immutable_directories: Sequence[Path] = (),
    journal: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Preflight or repair descriptor-pinned shared Git/worktree entries."""
    def in_immutable_directory(relative: Path) -> bool:
        return any(relative == protected or protected in relative.parents for protected in immutable_directories)

    root_descriptor = os.dup(root) if isinstance(root, int) else _open_direct_directory(root)
    inventory = hashlib.sha256()
    content_inventory = hashlib.sha256()
    pending = [(root_descriptor, Path())]
    counts = {
        "directories": 0,
        "directories_inexact": 0,
        "files": 0,
        "files_inexact": 0,
        "pack_components": 0,
        "pack_components_inexact": 0,
        "loose_objects": 0,
        "loose_objects_inexact": 0,
        "symlinks_untouched": 0,
        "pack_hardlinks_seen": 0,
        "pack_hardlinks_detached": 0,
        "excluded_root_entries": 0,
        "immutable_files": 0,
    }
    try:
        while pending:
            directory_descriptor, relative_parent = pending.pop()
            try:
                directory_state = os.fstat(directory_descriptor)
                inventory.update(
                    json.dumps(
                        [str(relative_parent), "d", directory_state.st_dev, directory_state.st_ino, directory_state.st_mode, directory_state.st_nlink, directory_state.st_uid, directory_state.st_gid],
                        separators=(",", ":"),
                    ).encode()
                )
                content_inventory.update(("d\0" + str(relative_parent) + "\0").encode())
                directory_mode = stat.S_IMODE(directory_state.st_mode)
                protected_directory = in_immutable_directory(relative_parent)
                if not protected_directory and not (directory_state.st_gid == group_gid and bool(directory_mode & stat.S_ISGID) and directory_mode & stat.S_IRWXG == stat.S_IRWXG):
                    counts["directories_inexact"] += 1
                if mutate and not protected_directory:
                    if journal is not None:
                        journal.append({"kind": "metadata", "descriptor": os.dup(directory_descriptor), "before": directory_state})
                    _set_shared_fd(directory_descriptor, group_gid, directory=True)
                counts["directories"] += 1
                os.lseek(directory_descriptor, 0, os.SEEK_SET)
                for name in sorted(os.listdir(directory_descriptor)):
                    if not relative_parent.parts and name in excluded_root_entries:
                        counts["excluded_root_entries"] += 1
                        continue
                    relative = relative_parent / name
                    try:
                        descriptor = os.open(
                            name,
                            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK | getattr(os, "O_NOATIME", 0),
                            dir_fd=directory_descriptor,
                        )
                    except PermissionError:
                        if mutate and in_immutable_directory(relative):
                            raise DoctorError(
                                f"cannot safely inventory immutable directory entry without O_NOATIME: {relative}"
                            )
                        # Read-only diagnosis may fall back when the ordinary
                        # coder does not own a protected preservation inode.
                        # Mutation still fails closed above; inventory grants
                        # no write or cleanup authority.
                        descriptor = os.open(
                            name,
                            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                            dir_fd=directory_descriptor,
                        )
                    except OSError as exc:
                        if exc.errno == errno.ELOOP:
                            counts["symlinks_untouched"] += 1
                            continue
                        raise
                    state = os.fstat(descriptor)
                    inventory.update(
                        json.dumps(
                            [
                                str(relative),
                                "d" if stat.S_ISDIR(state.st_mode) else "f",
                                state.st_dev,
                                state.st_ino,
                                state.st_mode,
                                state.st_nlink,
                                state.st_uid,
                                state.st_gid,
                                state.st_size,
                                state.st_mtime_ns,
                            ],
                            separators=(",", ":"),
                        ).encode()
                    )
                    if stat.S_ISDIR(state.st_mode):
                        pending.append((descriptor, relative))
                        continue
                    try:
                        if not stat.S_ISREG(state.st_mode):
                            raise DoctorError(f"unsupported entry in canonical Git directory: {relative}")
                        content = hashlib.sha256()
                        offset = 0
                        while offset < state.st_size:
                            chunk = os.pread(descriptor, min(1024 * 1024, state.st_size - offset), offset)
                            if not chunk:
                                raise DoctorError(f"file truncated during immutable inventory: {relative}")
                            content.update(chunk)
                            offset += len(chunk)
                        stable = os.fstat(descriptor)
                        if any(getattr(state, field) != getattr(stable, field) for field in ("st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid", "st_size", "st_mtime_ns", "st_ctime_ns")):
                            raise DoctorError(f"file changed during immutable inventory: {relative}")
                        inventory.update(content.digest())
                        content_inventory.update(("f\0" + str(relative) + "\0").encode() + content.digest())
                        if in_immutable_directory(relative):
                            # Authenticated preservation is readable inventory, never
                            # shared-tree repair surface.  This applies to every
                            # descendant, including an entry introduced after
                            # authentication; transaction-level inventory and
                            # directory-generation checks then fail closed.
                            counts["immutable_files"] += 1
                            counts["files"] += 1
                            continue
                        if relative in immutable_files:
                            if state.st_nlink != 1 or stat.S_IMODE(state.st_mode) != 0o440:
                                counts["files_inexact"] += 1
                            counts["immutable_files"] += 1
                            counts["files"] += 1
                            continue
                        parts = relative.parts
                        pack_component = len(parts) == 3 and parts[:2] == ("objects", "pack") and _PACK_COMPONENT.fullmatch(parts[2]) is not None
                        loose_object = len(parts) == 3 and parts[0] == "objects" and _LOOSE_OBJECT_DIRECTORY.fullmatch(parts[1]) is not None and _LOOSE_OBJECT_NAME.fullmatch(parts[2]) is not None
                        if pack_component:
                            if not bool(state.st_mode & stat.S_IROTH) or bool(state.st_mode & 0o111):
                                raise DoctorError(f"unreadable or executable pack component in canonical Git directory: {relative}")
                            counts["pack_components"] += 1
                            pack_exact = state.st_nlink == 1 and state.st_gid == group_gid and not bool(state.st_mode & 0o222) and bool(state.st_mode & stat.S_IRGRP)
                            if not pack_exact:
                                counts["pack_components_inexact"] += 1
                            if state.st_nlink > 1:
                                counts["pack_hardlinks_seen"] += 1
                            if mutate and state.st_nlink > 1:
                                _detach_pack_hardlink(
                                    directory_descriptor,
                                    name,
                                    descriptor,
                                    group_gid,
                                    journal,
                                )
                                counts["pack_hardlinks_detached"] += 1
                            elif mutate:
                                if journal is not None:
                                    journal.append({"kind": "metadata", "descriptor": os.dup(descriptor), "before": state})
                                os.fchown(descriptor, -1, group_gid)
                                os.fchmod(
                                    descriptor,
                                    (stat.S_IMODE(state.st_mode) | stat.S_IRGRP | stat.S_IROTH) & ~0o222,
                                )
                            continue
                        if loose_object:
                            if state.st_nlink > 1:
                                raise DoctorError(f"hardlinked loose object in canonical Git directory: {relative}")
                            mode = stat.S_IMODE(state.st_mode)
                            counts["loose_objects"] += 1
                            loose_exact = state.st_gid == group_gid and bool(mode & stat.S_IRGRP) and not bool(mode & 0o111)
                            if not loose_exact:
                                counts["loose_objects_inexact"] += 1
                            if mutate:
                                if journal is not None:
                                    journal.append({"kind": "metadata", "descriptor": os.dup(descriptor), "before": state})
                                os.fchown(descriptor, -1, group_gid)
                                os.fchmod(
                                    descriptor,
                                    (mode | stat.S_IRGRP | stat.S_IROTH) & ~0o111,
                                )
                            continue
                        if state.st_nlink > 1:
                            raise DoctorError(f"mutable or unreadable hardlink in canonical Git directory: {relative}")
                        mode = stat.S_IMODE(state.st_mode)
                        if not (state.st_gid == group_gid and mode & (stat.S_IRGRP | stat.S_IWGRP) == (stat.S_IRGRP | stat.S_IWGRP)):
                            counts["files_inexact"] += 1
                        if mutate:
                            if journal is not None:
                                journal.append({"kind": "metadata", "descriptor": os.dup(descriptor), "before": state})
                            _set_shared_fd(descriptor, group_gid, directory=False)
                        counts["files"] += 1
                    finally:
                        os.close(descriptor)
            finally:
                os.close(directory_descriptor)
    finally:
        for descriptor, _relative in pending:
            os.close(descriptor)
    counts["inventory_sha256"] = "sha256:" + inventory.hexdigest()
    counts["content_sha256"] = "sha256:" + content_inventory.hexdigest()
    return counts


def _close_mutation_journal(journal: Sequence[Mapping[str, Any]], *, rollback: bool) -> list[str]:
    """Commit or reverse every descriptor-pinned mutation, returning failures."""
    errors: list[str] = []
    for item in reversed(journal):
        descriptors = [
            value
            for key in ("descriptor", "parent")
            if isinstance((value := item.get(key)), int)
        ]
        try:
            if item["kind"] == "metadata":
                descriptor, before = item["descriptor"], item["before"]
                if rollback:
                    os.fchown(descriptor, before.st_uid, before.st_gid)
                    os.fchmod(descriptor, stat.S_IMODE(before.st_mode))
                    os.utime(descriptor, ns=(before.st_atime_ns, before.st_mtime_ns))
                    after = os.fstat(descriptor)
                    fields = ("st_dev", "st_ino", "st_nlink", "st_uid", "st_gid", "st_size", "st_atime_ns", "st_mtime_ns")
                    if any(getattr(before, field) != getattr(after, field) for field in fields) or stat.S_IMODE(before.st_mode) != stat.S_IMODE(after.st_mode):
                        raise DoctorError("metadata rollback evidence differs")
            elif item["kind"] == "exchange":
                parent, name, backup = item["parent"], item["name"], item["backup"]
                if rollback:
                    _rename_exchange(parent, backup, name)
                os.unlink(backup, dir_fd=parent)
                os.fsync(parent)
            elif item["kind"] == "created_directory":
                parent, descriptor = item["parent"], item["descriptor"]
                if rollback:
                    if descriptor is None:
                        raise DoctorError(
                            "created support directory identity was never bound; retained for recovery"
                        )
                    bound = os.fstat(descriptor)
                    names = (item.get("staging_name"), item.get("published_name"))
                    if any(not isinstance(name, str) for name in names):
                        raise DoctorError("created support directory journal name is invalid")
                    matches: list[str] = []
                    for name in dict.fromkeys(names):
                        try:
                            visible = os.stat(name, dir_fd=parent, follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        if stat.S_ISDIR(visible.st_mode) and (
                            visible.st_dev, visible.st_ino
                        ) == (bound.st_dev, bound.st_ino):
                            matches.append(name)
                    if len(matches) != 1:
                        raise DoctorError(
                            "created support directory has no unique bound rollback name"
                        )
                    os.rmdir(matches[0], dir_fd=parent)
                    os.fsync(parent)
            else:
                raise DoctorError("mutation journal entry kind is invalid")
        except Exception as exc:
            errors.append(str(exc))
        finally:
            # Rollback is best-effort, descriptor disposal is unconditional.
            # Each journal entry owns its duplicated descriptor and attempts
            # exactly one close even if rename/chown/chmod/unlink/fsync fails.
            for descriptor in dict.fromkeys(descriptors):
                try:
                    os.close(descriptor)
                except Exception as exc:
                    errors.append(str(exc))
    return errors


def _configured_worktree_locations(
    paths: DoctorPaths,
) -> tuple[list[tuple[Path, Path]], list[str]]:
    repository = paths.repository.absolute()
    worktree_root = paths.worktrees.absolute()
    raw = _git(repository, "worktree", "list", "--porcelain")
    local: list[tuple[Path, Path]] = []
    outside: list[str] = []
    seen: set[Path] = set()
    for line in raw.splitlines():
        if not line.startswith("worktree "):
            continue
        location = Path(line.removeprefix("worktree "))
        if not location.is_absolute():
            raise DoctorError(f"Git returned a non-absolute worktree: {location}")
        if location == repository:
            continue
        try:
            relative = location.relative_to(worktree_root)
        except ValueError:
            outside.append(str(location))
            continue
        if not relative.parts or any(part in ("", ".", "..") for part in relative.parts):
            raise DoctorError(f"linked worktree path is unsafe: {location}")
        if relative in seen:
            raise DoctorError(f"duplicate linked worktree path: {location}")
        seen.add(relative)
        local.append((location, relative))
    return local, outside


def _shared_tree_exact(counts: Mapping[str, int]) -> bool:
    return not any(
        counts.get(name, 0)
        for name in (
            "directories_inexact",
            "files_inexact",
            "pack_components_inexact",
            "loose_objects_inexact",
        )
    )


def _inspect_shared_git_trees(
    paths: DoctorPaths, group_gid: int, *, include_linked: bool = True
) -> dict[str, Any]:
    local, outside = _configured_worktree_locations(paths)
    trees: dict[str, dict[str, int]] = {
        "canonical_worktree": _scan_shared_git_tree(
            paths.repository,
            group_gid,
            mutate=False,
            excluded_root_entries=(".git",),
        ),
        "git_common": _scan_shared_git_tree(paths.repository / ".git", group_gid, mutate=False),
    }
    for location, relative in local if include_linked else ():
        descriptor = _open_direct_directory(location)
        try:
            authenticated = _authenticate_pre_ledger_preservation(location, descriptor, group_gid)
            try:
                protected = [authenticated["relative"]] if authenticated is not None else []
                trees[f"linked:{relative}"] = _scan_shared_git_tree(
                    descriptor,
                    group_gid,
                    mutate=False,
                    immutable_files=protected,
                    immutable_directories=[Path(".tgw-coding-preservation")] if authenticated is not None else [],
                )
            finally:
                if authenticated is not None:
                    os.close(authenticated["descriptor"])
                    os.close(authenticated["preservation_descriptor"])
        finally:
            os.close(descriptor)
    return {
        "exact": all(_shared_tree_exact(counts) for counts in trees.values()),
        "trees": trees,
        "outside_configured_root_untouched": outside,
        "linked_worktrees_inspected": include_linked,
        "linked_worktree_count": len(local),
    }


def _open_relative_directory(root_descriptor: int, relative: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.dup(root_descriptor)
    try:
        for component in relative.parts:
            if component in ("", ".", ".."):
                raise DoctorError(f"unsafe relative directory path: {relative}")
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _verify_bound_directory(path: Path, descriptor: int) -> None:
    bound = os.fstat(descriptor)
    if not path.is_absolute():
        raise DoctorError(f"bound shared directory path is not absolute: {path}")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    root_descriptor = -1
    visible_descriptor = -1
    try:
        root_descriptor = os.open(path.anchor, flags)
        visible_descriptor = _open_relative_directory(root_descriptor, path.relative_to(path.anchor))
        visible = os.fstat(visible_descriptor)
    except OSError as exc:
        raise DoctorError(f"bound shared directory is no longer visible: {path}") from exc
    finally:
        if visible_descriptor >= 0:
            os.close(visible_descriptor)
        if root_descriptor >= 0:
            os.close(root_descriptor)
    if not stat.S_ISDIR(visible.st_mode) or (visible.st_dev, visible.st_ino) != (bound.st_dev, bound.st_ino):
        raise DoctorError(f"bound shared directory changed before repair: {path}")


_QUIESCENCE_DROPIN = "90-tgw-doctor-unix-git-access.conf"
_QUIESCENCE_MARKER = "unix-git-access.active"
_QUIESCENCE_STATE = "unix-git-access.state.json"
_QUIESCENCE_SCHEMA = "tgw-doctor-quiescence/v1"


class _RetainQuiescenceError(DoctorError):
    """An activation failure whose exact guards must remain for recovery."""


def _secure_runtime_directory(path: Path, *, uid: int, gid: int) -> bool:
    """Create one trusted runtime directory without following a replaced parent."""
    parent = path.parent
    parent_state = parent.stat(follow_symlinks=False)
    if parent.is_symlink() or not stat.S_ISDIR(parent_state.st_mode) or parent_state.st_uid != uid or parent_state.st_gid != gid or parent_state.st_mode & 0o022:
        raise DoctorError(f"unsafe quiescence parent directory: {parent}")
    if os.path.lexists(path):
        state = path.stat(follow_symlinks=False)
        if path.is_symlink() or not stat.S_ISDIR(state.st_mode) or state.st_uid != uid or state.st_gid != gid or state.st_mode & 0o022:
            raise DoctorError(f"unsafe quiescence directory: {path}")
        return False
    os.mkdir(path, 0o755)
    os.chown(path, uid, gid, follow_symlinks=False)
    os.chmod(path, 0o755, follow_symlinks=False)
    return True


def _create_quiescence_file(path: Path, value: bytes, *, mode: int, uid: int, gid: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        stream = os.fdopen(descriptor, "wb")
        descriptor = -1
        with stream:
            stream.write(value)
            stream.flush()
            os.fchown(stream.fileno(), uid, gid)
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        parent = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        path.unlink(missing_ok=True)
        raise


def _quiescence_file_exact(path: Path, value: bytes, *, mode: int, uid: int, gid: int) -> bool:
    if not os.path.lexists(path) or path.is_symlink() or not path.is_file():
        return False
    before = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_uid != uid or before.st_gid != gid or stat.S_IMODE(before.st_mode) != mode or before.st_nlink != 1:
        return False
    observed = path.read_bytes()
    after = path.stat(follow_symlinks=False)
    return observed == value and (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_nlink,
        before.st_mtime_ns,
    ) == (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_nlink,
        after.st_mtime_ns,
    )


def _fsync_parent(path: Path) -> None:
    descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _unlink_quiescence_file(path: Path, value: bytes, *, mode: int, uid: int, gid: int) -> None:
    if not _quiescence_file_exact(path, value, mode=mode, uid=uid, gid=gid):
        raise DoctorError(f"quiescence guard changed; refusing removal: {path}")
    path.unlink()
    _fsync_parent(path)


def _process_start_ticks(pid: int) -> str | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    _prefix, separator, tail = raw.rpartition(") ")
    fields = tail.split() if separator else []
    return fields[19] if len(fields) > 19 else None


def _boot_id() -> str:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise DoctorError(f"cannot read the local boot identity: {exc}") from exc
    if not value or "\n" in value:
        raise DoctorError("local boot identity is malformed")
    return value


def _quiescence_layout(paths: DoctorPaths, units: Sequence[str]) -> tuple[Path, Path, dict[str, Path], bytes]:
    state_path = paths.quiescence_root / _QUIESCENCE_STATE
    marker = paths.quiescence_root / _QUIESCENCE_MARKER
    dropins = {unit: paths.systemd_runtime_root / f"{unit}.d" / _QUIESCENCE_DROPIN for unit in units}
    dropin_value = f"[Unit]\nConditionPathExists=!{marker}\n".encode()
    return state_path, marker, dropins, dropin_value


def _unexpected_quiescence_entries(
    paths: DoctorPaths,
    *,
    state_path: Path,
    marker: Path,
    dropins: Mapping[str, Path],
) -> list[Path]:
    """Return entries outside the exact Doctor-owned quiescence layout."""
    unexpected: list[Path] = []
    directories = {
        paths.quiescence_root: {state_path.name, marker.name},
        **{dropin.parent: {dropin.name} for dropin in dropins.values()},
    }
    for directory, allowed_names in directories.items():
        if not os.path.lexists(directory):
            continue
        directory_state = directory.stat(follow_symlinks=False)
        if directory.is_symlink() or not stat.S_ISDIR(directory_state.st_mode):
            raise DoctorError(f"unsafe coding quiescence directory while inspecting: {directory}")
        try:
            entries = list(directory.iterdir())
        except OSError as exc:
            raise DoctorError(f"cannot inspect coding quiescence directory {directory}: {exc}") from exc
        unexpected.extend(entry for entry in entries if entry.name not in allowed_names)
    return sorted(unexpected, key=lambda path: str(path))


def _assert_known_quiescence_layout(
    paths: DoctorPaths,
    *,
    state_path: Path,
    marker: Path,
    dropins: Mapping[str, Path],
) -> None:
    unexpected = _unexpected_quiescence_entries(
        paths,
        state_path=state_path,
        marker=marker,
        dropins=dropins,
    )
    if unexpected:
        raise DoctorError("unexpected coding quiescence remnants; refusing alteration: " + ", ".join(str(path) for path in unexpected))


def _assert_quiescence_units_safe(states: Mapping[str, Mapping[str, str]]) -> None:
    preexisting_masks = [unit for unit, state in states.items() if state.get("LoadState") == "masked"]
    if preexisting_masks:
        raise DoctorError("refusing to alter pre-existing coding unit masks: " + ", ".join(preexisting_masks))
    transient_active = [unit for unit, state in states.items() if unit not in _ACTIVE_CODING_UNITS and state.get("ActiveState") == "active"]
    if transient_active:
        raise DoctorError("local coding one-shot is active; retry after it exits: " + ", ".join(transient_active))


def _prove_guarded_stopped_units(
    units: Sequence[str],
    *,
    marker: Path,
    dropins: Mapping[str, Path],
    dropin_value: bytes,
    uid: int,
    gid: int,
    allow_failed: bool,
) -> dict[str, dict[str, Any]]:
    """Prove the complete stopped set and exact guards before latch mutation."""
    marker_value = b"tgw doctor unix-git-access active\n"
    states = {unit: _unit_state(unit) for unit in units}
    stopped_states = {"inactive", "failed"} if allow_failed else {"inactive"}
    unsettled = [
        unit
        for unit, state in states.items()
        if state.get("ActiveState") not in stopped_states
        or str(dropins[unit]) not in state.get("DropInPaths", "").split()
        or not _quiescence_file_exact(
            dropins[unit], dropin_value, mode=0o444, uid=uid, gid=gid
        )
    ]
    if not _quiescence_file_exact(marker, marker_value, mode=0o400, uid=uid, gid=gid):
        unsettled.append("quiescence-marker")
    if unsettled:
        raise DoctorError("local coding units did not reach guarded/stopped state: " + ", ".join(unsettled))
    return states


def _reset_proven_failed_units(states: Mapping[str, Mapping[str, Any]]) -> None:
    failed = [unit for unit, state in states.items() if state.get("ActiveState") == "failed"]
    if not failed:
        return
    reset = _run(["systemctl", "reset-failed", *failed], timeout=30)
    if reset.returncode:
        raise _RetainQuiescenceError(
            reset.stderr.strip() or "cannot reset failed local coding units"
        )


def _restoration_barrier_wrong(
    units: Sequence[str],
    initially_active: Sequence[str],
    *,
    timer: str,
    foreman: str,
) -> list[str]:
    """Read and prove timer inactivity plus the complete non-timer layout."""
    states = {unit: _unit_state(unit) for unit in units}
    return [
        unit
        for unit in units
        if states[unit].get("ActiveState")
        != (
            "inactive"
            if unit in {timer, foreman}
            else ("active" if unit in initially_active else "inactive")
        )
    ]


def _new_quiescence_state(
    units: Sequence[str],
    initially_active: Sequence[str],
    state_path: Path,
    marker: Path,
    dropins: Mapping[str, Path],
) -> tuple[dict[str, Any], bytes]:
    pid = os.getpid()
    start_ticks = _process_start_ticks(pid)
    if start_ticks is None:
        raise DoctorError("cannot bind the local coding quiescence owner process")
    value = {
        "schema": _QUIESCENCE_SCHEMA,
        "boot_id": _boot_id(),
        "owner_pid": pid,
        "owner_start_ticks": start_ticks,
        "units": list(units),
        "initially_active": list(initially_active),
        "state_path": str(state_path),
        "marker": str(marker),
        "dropins": {unit: str(dropins[unit]) for unit in units},
    }
    return value, _canonical(value) + b"\n"


def _read_quiescence_state(
    state_path: Path,
    *,
    units: Sequence[str],
    marker: Path,
    dropins: Mapping[str, Path],
    uid: int,
    gid: int,
) -> tuple[dict[str, Any], bytes]:
    root = state_path.parent
    root_state = root.stat(follow_symlinks=False)
    if root.is_symlink() or not stat.S_ISDIR(root_state.st_mode) or root_state.st_uid != uid or root_state.st_gid != gid or root_state.st_mode & 0o022:
        raise DoctorError("pre-existing coding quiescence directory is unsafe")
    if not os.path.lexists(state_path) or state_path.is_symlink() or not state_path.is_file():
        raise DoctorError("pre-existing coding quiescence has no trusted state file")
    before = state_path.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_uid != uid or before.st_gid != gid or stat.S_IMODE(before.st_mode) != 0o400 or before.st_nlink != 1:
        raise DoctorError("pre-existing coding quiescence state metadata is unsafe")
    raw = state_path.read_bytes()
    after = state_path.stat(follow_symlinks=False)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise DoctorError("pre-existing coding quiescence state changed while reading")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DoctorError("pre-existing coding quiescence state is malformed") from exc
    expected_dropins = {unit: str(dropins[unit]) for unit in units}
    required = {
        "schema": _QUIESCENCE_SCHEMA,
        "units": list(units),
        "state_path": str(state_path),
        "marker": str(marker),
        "dropins": expected_dropins,
    }
    if not isinstance(value, dict) or any(value.get(key) != item for key, item in required.items()):
        raise DoctorError("pre-existing coding quiescence state has the wrong binding")
    active = value.get("initially_active")
    pid = value.get("owner_pid")
    start_ticks = value.get("owner_start_ticks")
    boot_id = value.get("boot_id")
    if (
        not isinstance(active, list)
        or any(not isinstance(unit, str) for unit in active)
        or len(active) != len(set(active))
        or any(
            unit not in _ACTIVE_CODING_UNITS
            and unit != "tgw-coding-local-foreman.service"
            for unit in active
        )
        or not isinstance(pid, int)
        or pid <= 0
        or not isinstance(start_ticks, str)
        or not start_ticks.isdigit()
        or not isinstance(boot_id, str)
        or not boot_id
    ):
        raise DoctorError("pre-existing coding quiescence state fields are unsafe")
    if raw != _canonical(value) + b"\n":
        raise DoctorError("pre-existing coding quiescence state is not canonical")
    return value, raw


def _quiescence_owner_active(state: Mapping[str, Any]) -> bool:
    return state.get("boot_id") == _boot_id() and _process_start_ticks(int(state["owner_pid"])) == state.get("owner_start_ticks")


def _validate_systemd_runtime(paths: DoctorPaths, *, uid: int, gid: int) -> None:
    state = paths.systemd_runtime_root.stat(follow_symlinks=False)
    if paths.systemd_runtime_root.is_symlink() or not stat.S_ISDIR(state.st_mode) or state.st_uid != uid or state.st_gid != gid or state.st_mode & 0o022:
        raise DoctorError(f"unsafe systemd runtime directory: {paths.systemd_runtime_root}")


def _activate_quiescence(
    paths: DoctorPaths,
    *,
    units: Sequence[str],
    marker: Path,
    dropins: Mapping[str, Path],
    dropin_value: bytes,
    uid: int,
    gid: int,
) -> None:
    marker_value = b"tgw doctor unix-git-access active\n"
    _validate_systemd_runtime(paths, uid=uid, gid=gid)
    _secure_runtime_directory(paths.quiescence_root, uid=uid, gid=gid)
    if os.path.lexists(marker):
        if not _quiescence_file_exact(marker, marker_value, mode=0o400, uid=uid, gid=gid):
            raise DoctorError("pre-existing coding quiescence marker is unsafe")
    else:
        _create_quiescence_file(marker, marker_value, mode=0o400, uid=uid, gid=gid)
    for dropin in dropins.values():
        _secure_runtime_directory(dropin.parent, uid=uid, gid=gid)
        if os.path.lexists(dropin):
            if not _quiescence_file_exact(dropin, dropin_value, mode=0o444, uid=uid, gid=gid):
                raise DoctorError(f"pre-existing coding quiescence drop-in is unsafe: {dropin}")
        else:
            _create_quiescence_file(dropin, dropin_value, mode=0o444, uid=uid, gid=gid)
    loaded = _run(["systemctl", "daemon-reload"], timeout=30)
    if loaded.returncode:
        raise DoctorError(loaded.stderr.strip() or "cannot load local coding quiescence guards")
    timer = "tgw-coding-local-foreman.timer"
    stopped = _run(["systemctl", "stop", timer], timeout=30)
    if stopped.returncode:
        raise DoctorError(stopped.stderr.strip() or "cannot stop local coding Foreman timer")
    timer_state = _unit_state(timer)
    if timer_state.get("ActiveState") != "inactive":
        raise DoctorError("local coding Foreman timer did not reach guarded/inactive state")
    remaining = [unit for unit in units if unit != timer]
    stopped = _run(["systemctl", "stop", *remaining], timeout=30)
    if stopped.returncode:
        raise DoctorError(stopped.stderr.strip() or "cannot stop remaining local coding units")
    stopped_states = _prove_guarded_stopped_units(
        units, marker=marker, dropins=dropins, dropin_value=dropin_value,
        uid=uid, gid=gid, allow_failed=True,
    )
    _reset_proven_failed_units(stopped_states)
    _prove_guarded_stopped_units(
        units, marker=marker, dropins=dropins, dropin_value=dropin_value,
        uid=uid, gid=gid, allow_failed=False,
    )


def _release_quiescence(
    paths: DoctorPaths,
    *,
    units: Sequence[str],
    initially_active: Sequence[str],
    state_path: Path,
    state_raw: bytes,
    marker: Path,
    dropins: Mapping[str, Path],
    dropin_value: bytes,
    uid: int,
    gid: int,
) -> dict[str, Any]:
    errors: list[str] = []
    marker_value = b"tgw doctor unix-git-access active\n"
    if os.path.lexists(marker):
        try:
            _unlink_quiescence_file(marker, marker_value, mode=0o400, uid=uid, gid=gid)
        except (OSError, DoctorError) as exc:
            errors.append(str(exc))
    for dropin in reversed(list(dropins.values())):
        if not os.path.lexists(dropin):
            continue
        try:
            _unlink_quiescence_file(dropin, dropin_value, mode=0o444, uid=uid, gid=gid)
        except (OSError, DoctorError) as exc:
            errors.append(str(exc))
    reloaded = _run(["systemctl", "daemon-reload"], timeout=30)
    if reloaded.returncode:
        errors.append(reloaded.stderr.strip() or "cannot reload systemd after local coding quiescence")

    marker_absent = not os.path.lexists(marker)
    timer = "tgw-coding-local-foreman.timer"
    foreman = "tgw-coding-local-foreman.service"
    worker_restoration_failed = False
    pre_foreman_barrier_failed = False
    foreman_restoration_failed = False
    post_foreman_barrier_failed = False
    timer_restored = False
    if marker_absent:
        undesired = [
            unit
            for unit in units
            if unit not in initially_active
            and _unit_state(unit).get("ActiveState") != "inactive"
        ]
        if undesired:
            stopped = _run(["systemctl", "stop", *undesired], timeout=30)
            if stopped.returncode:
                worker_restoration_failed = True
                errors.append(stopped.stderr.strip() or "cannot restore initially inactive local coding units")
        active_non_timer = [unit for unit in initially_active if unit not in {timer, foreman}]
        if active_non_timer and not worker_restoration_failed:
            started = _run(["systemctl", "start", *active_non_timer], timeout=30)
            if started.returncode:
                worker_restoration_failed = True
                errors.append(started.stderr.strip() or "cannot restore initially active local coding units")
        pre_foreman_wrong = _restoration_barrier_wrong(
            units, initially_active, timer=timer, foreman=foreman
        )
        if pre_foreman_wrong:
            pre_foreman_barrier_failed = True
            errors.append(
                "local coding units did not reach the pre-Foreman restoration barrier: "
                + ", ".join(pre_foreman_wrong)
            )
        if (
            foreman in initially_active
            and not worker_restoration_failed
            and not pre_foreman_barrier_failed
        ):
            started = _run(["systemctl", "start", foreman], timeout=30)
            if started.returncode:
                foreman_restoration_failed = True
                errors.append(started.stderr.strip() or "cannot restore interrupted local coding Foreman run")
        post_foreman_wrong = _restoration_barrier_wrong(
            units, initially_active, timer=timer, foreman=foreman
        )
        if post_foreman_wrong:
            post_foreman_barrier_failed = True
            errors.append(
                "local coding units did not reach the post-Foreman restoration barrier: "
                + ", ".join(post_foreman_wrong)
            )
        if (
            timer in initially_active
            and not worker_restoration_failed
            and not pre_foreman_barrier_failed
            and not foreman_restoration_failed
            and not post_foreman_barrier_failed
        ):
            started = _run(["systemctl", "start", timer], timeout=30)
            if started.returncode:
                errors.append(started.stderr.strip() or "cannot restore local coding Foreman timer")
            else:
                timer_restored = True
    else:
        errors.append("quiescence marker remains; refusing to start local coding units")

    final_states = {unit: _unit_state(unit).get("ActiveState") for unit in units}
    wrong = []
    for unit in units:
        expected = "active" if unit in initially_active and unit in _ACTIVE_CODING_UNITS else "inactive"
        accepted = {expected}
        if unit == foreman and timer_restored:
            accepted.add("activating")
        if final_states[unit] not in accepted:
            wrong.append(unit)
    if wrong:
        errors.append("local coding units did not return to their initial state: " + ", ".join(wrong))

    remaining_guards = [str(path) for path in (marker, *dropins.values()) if os.path.lexists(path)]
    if remaining_guards:
        errors.append("quiescence guards remain: " + ", ".join(remaining_guards))

    unexpected = _unexpected_quiescence_entries(
        paths,
        state_path=state_path,
        marker=marker,
        dropins=dropins,
    )
    if unexpected:
        errors.append("unexpected coding quiescence remnants remain: " + ", ".join(str(path) for path in unexpected))

    if not errors:
        dropin_directories = sorted({dropin.parent for dropin in dropins.values()}, key=lambda path: str(path))
        for directory in reversed(dropin_directories):
            try:
                directory.rmdir()
                _fsync_parent(directory)
            except OSError as exc:
                if exc.errno != errno.ENOENT:
                    errors.append(f"cannot remove quiescence directory {directory}: {exc}")
    if not errors:
        try:
            _unlink_quiescence_file(state_path, state_raw, mode=0o400, uid=uid, gid=gid)
        except (OSError, DoctorError) as exc:
            errors.append(str(exc))
    if not errors:
        try:
            paths.quiescence_root.rmdir()
            _fsync_parent(paths.quiescence_root)
        except OSError as exc:
            if exc.errno != errno.ENOENT:
                errors.append(f"cannot remove quiescence directory {paths.quiescence_root}: {exc}")
    result = {
        "initially_active": list(initially_active),
        "restored": not wrong and marker_absent,
        "guards_remaining": remaining_guards,
        "state_retained": os.path.lexists(state_path),
    }
    if errors:
        raise DoctorError("; ".join(errors))
    return result


def _recover_stale_quiescence(paths: DoctorPaths, *, units: Sequence[str], uid: int, gid: int) -> dict[str, Any] | None:
    state_path, marker, dropins, dropin_value = _quiescence_layout(paths, units)
    _assert_known_quiescence_layout(
        paths,
        state_path=state_path,
        marker=marker,
        dropins=dropins,
    )
    existing = [str(path) for path in (state_path, marker, *dropins.values()) if os.path.lexists(path)]
    if not existing:
        return None
    state, state_raw = _read_quiescence_state(
        state_path,
        units=units,
        marker=marker,
        dropins=dropins,
        uid=uid,
        gid=gid,
    )
    if _quiescence_owner_active(state):
        raise DoctorError("another local coding quiescence owner is still active")
    for path, value, mode in (
        (marker, b"tgw doctor unix-git-access active\n", 0o400),
        *((dropin, dropin_value, 0o444) for dropin in dropins.values()),
    ):
        if os.path.lexists(path) and not _quiescence_file_exact(path, value, mode=mode, uid=uid, gid=gid):
            raise DoctorError(f"stale coding quiescence guard is unsafe: {path}")
    initially_active = list(state["initially_active"])
    try:
        _activate_quiescence(
            paths,
            units=units,
            marker=marker,
            dropins=dropins,
            dropin_value=dropin_value,
            uid=uid,
            gid=gid,
        )
    except Exception as activation_error:
        if isinstance(activation_error, _RetainQuiescenceError):
            raise
        try:
            _release_quiescence(
                paths,
                units=units,
                initially_active=initially_active,
                state_path=state_path,
                state_raw=state_raw,
                marker=marker,
                dropins=dropins,
                dropin_value=dropin_value,
                uid=uid,
                gid=gid,
            )
        except DoctorError as release_error:
            raise DoctorError(f"stale quiescence activation failed: {activation_error}; release failed: {release_error}") from release_error
        raise
    release = _release_quiescence(
        paths,
        units=units,
        initially_active=initially_active,
        state_path=state_path,
        state_raw=state_raw,
        marker=marker,
        dropins=dropins,
        dropin_value=dropin_value,
        uid=uid,
        gid=gid,
    )
    return {
        "recovered": True,
        "stale_owner_pid": state["owner_pid"],
        "stale_boot_id": state["boot_id"],
        "release": release,
    }


@contextmanager
def _coding_quiescence(paths: DoctorPaths):
    """Block starts with exact runtime conditions while shared Git data changes.

    A runtime mask cannot shadow these units because their fragments live in
    /etc/systemd/system, which precedes /run/systemd/system in systemd's load
    path.  Root-owned runtime drop-ins instead add a false condition while the
    marker exists.  The repair stops and verifies every unit after activating
    that condition, then removes only its exact guards and restores the initial
    active set.
    """
    units = list(_CODING_UNITS)
    uid = paths.systemd_unit_uid
    gid = paths.systemd_unit_gid
    state_path, marker, dropins, dropin_value = _quiescence_layout(paths, units)
    _assert_known_quiescence_layout(
        paths,
        state_path=state_path,
        marker=marker,
        dropins=dropins,
    )
    _assert_quiescence_units_safe({unit: _unit_state(unit) for unit in units})
    recovered = _recover_stale_quiescence(paths, units=units, uid=uid, gid=gid)
    initial_states = {unit: _unit_state(unit) for unit in units}
    _assert_quiescence_units_safe(initial_states)
    initially_active = [
        unit for unit, state in initial_states.items()
        if (unit in _ACTIVE_CODING_UNITS and state.get("ActiveState") == "active")
        or (unit == "tgw-coding-local-foreman.service" and state.get("ActiveState") == "activating")
    ]
    _secure_runtime_directory(paths.quiescence_root, uid=uid, gid=gid)
    state, state_raw = _new_quiescence_state(units, initially_active, state_path, marker, dropins)
    _create_quiescence_file(state_path, state_raw, mode=0o400, uid=uid, gid=gid)
    evidence = {
        "recovered_stale_quiescence": recovered,
        "owner_pid": state["owner_pid"],
        "boot_id": state["boot_id"],
        "initially_active": list(initially_active),
    }
    activation_error: Exception | None = None
    try:
        _activate_quiescence(
            paths,
            units=units,
            marker=marker,
            dropins=dropins,
            dropin_value=dropin_value,
            uid=uid,
            gid=gid,
        )
        yield evidence
    except Exception as exc:
        activation_error = exc
        raise
    finally:
        if not isinstance(activation_error, _RetainQuiescenceError):
            try:
                evidence["release"] = _release_quiescence(
                    paths,
                    units=units,
                    initially_active=initially_active,
                    state_path=state_path,
                    state_raw=state_raw,
                    marker=marker,
                    dropins=dropins,
                    dropin_value=dropin_value,
                    uid=uid,
                    gid=gid,
                )
            except DoctorError as release_error:
                if activation_error is not None:
                    raise DoctorError(
                        f"quiescence activation failed: {activation_error}; release failed: {release_error}"
                    ) from release_error
                raise


def repair_unix_git_access(paths: DoctorPaths) -> dict[str, Any]:
    """Restore only the shared local Git directories to tgw-coders access."""
    from tgw.development.worktree_lease import exclusive_worktree_lease

    _require_root()
    group_gid = grp.getgrnam("tgw-coders").gr_gid
    repository = paths.repository.absolute()
    worktree_root = paths.worktrees.absolute()
    git_common = repository / ".git"
    # Discovery grants no mutation authority.  Quiescence and every cooperating
    # Git lease are acquired before the authoritative inventory/preflight.
    discovered_worktrees, _discovered_outside = _configured_worktree_locations(paths)
    with _coding_quiescence(paths) as quiescence, ExitStack() as stack:
        stack.enter_context(exclusive_worktree_lease(repository))
        for location, _relative in discovered_worktrees:
            stack.enter_context(exclusive_worktree_lease(location))
        local_worktrees, outside_untouched = _configured_worktree_locations(paths)
        if local_worktrees != discovered_worktrees:
            raise DoctorError("linked worktree inventory changed while acquiring leases")
        before = check_unix_access(paths)
        repository_fd = _open_direct_directory(repository)
        stack.callback(os.close, repository_fd)
        worktree_root_fd = _open_direct_directory(worktree_root)
        stack.callback(os.close, worktree_root_fd)
        git_common_fd = os.open(
            ".git",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=repository_fd,
        )
        stack.callback(os.close, git_common_fd)
        linked_descriptors: list[tuple[Path, Path, int]] = []
        for location, relative in local_worktrees:
            descriptor = _open_relative_directory(worktree_root_fd, relative)
            stack.callback(os.close, descriptor)
            linked_descriptors.append((location, relative, descriptor))

        bound = [
            (repository, repository_fd),
            (worktree_root, worktree_root_fd),
            (git_common, git_common_fd),
            *((location, descriptor) for location, _relative, descriptor in linked_descriptors),
        ]
        for path, descriptor in bound:
            _verify_bound_directory(path, descriptor)

        pre_ledger: dict[str, dict[str, Any]] = {}
        for location, relative, descriptor in linked_descriptors:
            authenticated = _authenticate_pre_ledger_preservation(location, descriptor, group_gid)
            if authenticated is not None:
                stack.callback(os.close, authenticated["descriptor"])
                stack.callback(os.close, authenticated["preservation_descriptor"])
                pre_ledger[str(relative)] = authenticated

        linked_by_relative = {str(relative): descriptor for _location, relative, descriptor in linked_descriptors}

        def revalidate_preservation_directories() -> None:
            for relative, item in pre_ledger.items():
                _revalidate_preservation_directory(
                    linked_by_relative[relative],
                    item["preservation_descriptor"],
                    item["preservation_before"],
                    todo_id=item["todo_id"],
                )

        revalidate_preservation_directories()

        preflight: dict[str, dict[str, int]] = {
            "canonical_worktree": _scan_shared_git_tree(
                repository_fd,
                group_gid,
                mutate=False,
                excluded_root_entries=(".git",),
            ),
            "git_common": _scan_shared_git_tree(git_common_fd, group_gid, mutate=False),
        }
        for _location, relative, descriptor in linked_descriptors:
            protected = [pre_ledger[str(relative)]["relative"]] if str(relative) in pre_ledger else []
            preflight[f"linked:{relative}"] = _scan_shared_git_tree(
                descriptor,
                group_gid,
                mutate=False,
                immutable_files=protected,
                immutable_directories=[Path(".tgw-coding-preservation")] if protected else [],
            )
        revalidate_preservation_directories()

        # Rewalk once without mutation and require an identical immutable inventory
        # summary; additions or replacements after preflight fail closed.
        repeated = {
            "canonical_worktree": _scan_shared_git_tree(repository_fd, group_gid, mutate=False, excluded_root_entries=(".git",)),
            "git_common": _scan_shared_git_tree(git_common_fd, group_gid, mutate=False),
        }
        for _location, relative, descriptor in linked_descriptors:
            protected = [pre_ledger[str(relative)]["relative"]] if str(relative) in pre_ledger else []
            repeated[f"linked:{relative}"] = _scan_shared_git_tree(
                descriptor,
                group_gid,
                mutate=False,
                immutable_files=protected,
                immutable_directories=[Path(".tgw-coding-preservation")] if protected else [],
            )
        revalidate_preservation_directories()
        if repeated != preflight:
            raise DoctorError("shared Git inventory changed after preflight")

        journal: list[dict[str, Any]] = []
        repaired_preservation: list[dict[str, Any]] = []
        try:
            for path, descriptor in bound:
                _verify_bound_directory(path, descriptor)
            revalidate_preservation_directories()
            stable_fields = ("st_dev", "st_ino", "st_nlink", "st_uid", "st_gid", "st_size", "st_atime_ns", "st_mtime_ns")
            for item in pre_ledger.values():
                current = os.fstat(item["descriptor"])
                if any(getattr(item["before"], field) != getattr(current, field) for field in stable_fields):
                    raise DoctorError(f"Todo {item['todo_id']} preservation manifest changed after preflight")
            revalidate_preservation_directories()
            root_before = os.fstat(worktree_root_fd)
            journal.append({"kind": "metadata", "descriptor": os.dup(worktree_root_fd), "before": root_before})
            _set_shared_fd(worktree_root_fd, group_gid, directory=True)
            tree_changes: dict[str, dict[str, int]] = {
                "canonical_worktree": _scan_shared_git_tree(
                    repository_fd,
                    group_gid,
                    mutate=True,
                    excluded_root_entries=(".git",),
                    journal=journal,
                ),
                "git_common": _scan_shared_git_tree(git_common_fd, group_gid, mutate=True, journal=journal),
            }
            for _location, relative, descriptor in linked_descriptors:
                protected = [pre_ledger[str(relative)]["relative"]] if str(relative) in pre_ledger else []
                tree_changes[f"linked:{relative}"] = _scan_shared_git_tree(
                    descriptor,
                    group_gid,
                    mutate=True,
                    immutable_files=protected,
                    immutable_directories=[Path(".tgw-coding-preservation")] if protected else [],
                    journal=journal,
                )
            revalidate_preservation_directories()
            if any(tree_changes[name][field] != preflight[name][field] for name in preflight for field in ("inventory_sha256", "content_sha256")):
                raise DoctorError("shared Git inventory or content changed after immutable preflight")
            # The authenticated legacy 0460 mode is part of the descriptor-pinned
            # preflight inventory.  Compare that exact tree before performing the
            # one explicitly-authorized preservation-manifest transition.
            for item in pre_ledger.values():
                current = os.fstat(item["descriptor"])
                if any(getattr(item["before"], field) != getattr(current, field) for field in stable_fields):
                    raise DoctorError(f"Todo {item['todo_id']} preservation manifest changed after preflight")
                if stat.S_IMODE(current.st_mode) == 0o460:
                    journal.append({"kind": "metadata", "descriptor": os.dup(item["descriptor"]), "before": current})
                    os.fchmod(item["descriptor"], 0o440)
                    repaired_preservation.append(item)
            revalidate_preservation_directories()
            for item in repaired_preservation:
                after_state = os.fstat(item["descriptor"])
                if stat.S_IMODE(after_state.st_mode) != 0o440 or any(getattr(item["before"], field) != getattr(after_state, field) for field in stable_fields):
                    raise DoctorError(f"Todo {item['todo_id']} preservation manifest attributes changed")
            for path, descriptor in bound:
                _verify_bound_directory(path, descriptor)
            revalidate_preservation_directories()
            support_roots_changed = _provision_coding_support_roots(paths, group_gid, journal)
            after = check_unix_access(paths)
            if after["state"] != "PASS":
                raise DoctorError("ordinary Unix Git access remains incomplete after repair")
            for path, descriptor in bound:
                _verify_bound_directory(path, descriptor)
            revalidate_preservation_directories()
        except Exception as exc:
            rollback_errors = _close_mutation_journal(journal, rollback=True)
            try:
                revalidate_preservation_directories()
            except Exception as preservation_exc:
                rollback_errors.append(str(preservation_exc))
            if rollback_errors:
                raise DoctorError("unix Git access rollback incomplete: " + "; ".join(rollback_errors)) from exc
            raise
        commit_errors = _close_mutation_journal(journal, rollback=False)
        if commit_errors:
            raise DoctorError("unix Git access transaction cleanup incomplete: " + "; ".join(commit_errors))
        revalidate_preservation_directories()
        # A success receipt is durable evidence that the entire transaction,
        # including disposal of rollback aliases and journal descriptors, has
        # committed.  Publishing it earlier can contradict a cleanup failure.
        receipt = _receipt(
            paths,
            "unix-git-access",
            before,
            {"access": after, "quiescence": quiescence, "support_roots_created": support_roots_changed},
        )
    return {
        "ok": True,
        "operation": "unix-git-access",
        "changed": before != after,
        "preflight": preflight,
        "git_tree_changes": tree_changes,
        "pre_ledger_preservation": {
            name: {"todo_id": item["todo_id"], "manifest_hash": item["manifest_hash"], "receipt_sha256": item["receipt_sha256"], "mode_repaired": stat.S_IMODE(item["before"].st_mode) == 0o460}
            for name, item in pre_ledger.items()
        },
        "quiescence": quiescence,
        "outside_configured_root_untouched": outside_untouched,
        "receipt": receipt,
    }


def _unit_destination_bytes_exact(
    paths: DoctorPaths, destination: Path, expected: bytes
) -> bool:
    if destination.is_symlink() or not destination.is_file():
        return False
    state = destination.stat(follow_symlinks=False)
    return (
        stat.S_ISREG(state.st_mode)
        and state.st_uid == paths.systemd_unit_uid
        and state.st_gid == paths.systemd_unit_gid
        and stat.S_IMODE(state.st_mode) == paths.systemd_unit_mode
        and state.st_nlink == 1
        and destination.read_bytes() == expected
    )


def _unit_destination_exact(paths: DoctorPaths, destination: Path, source: Path) -> bool:
    return source.is_file() and _unit_destination_bytes_exact(
        paths, destination, source.read_bytes()
    )


_RESTART_OBLIGATION_SCHEMA = "tgw-doctor-service-restart-obligation/v2"
_RESTART_OBLIGATION_MODE = 0o400
_RESTART_OBLIGATION_DIRECTORY_MODE = 0o711


def _restart_obligation_root(paths: DoctorPaths) -> Path:
    return paths.receipts / ".restart-obligations"


def _restart_obligation_path(paths: DoctorPaths, unit: str) -> Path:
    if unit not in {*_CODING_UNITS, _PLAN_RENDER_UNIT}:
        raise DoctorError(f"restart obligation unit is not declared: {unit}")
    return _restart_obligation_root(paths) / f"{unit}.json"


def _ensure_restart_obligation_root(paths: DoctorPaths) -> Path:
    root = _restart_obligation_root(paths)
    _repair_managed_directory(
        root,
        uid=paths.systemd_unit_uid,
        gid=paths.systemd_unit_gid,
        mode=_RESTART_OBLIGATION_DIRECTORY_MODE,
    )
    identity = _directory_identity(
        root,
        uid=paths.systemd_unit_uid,
        gid=paths.systemd_unit_gid,
        mode=_RESTART_OBLIGATION_DIRECTORY_MODE,
    )
    if not identity["exact"]:
        raise DoctorError("restart obligation directory is unsafe")
    return root


def _restart_obligation_presence(
    paths: DoctorPaths, unit: str
) -> dict[str, Any]:
    """Expose restart debt to an ordinary Doctor without exposing its bytes."""
    path = _restart_obligation_path(paths, unit)
    root = _restart_obligation_root(paths)
    expected = {
        "path": str(path),
        "expected_uid": paths.systemd_unit_uid,
        "expected_gid": paths.systemd_unit_gid,
        "expected_mode": _RESTART_OBLIGATION_MODE,
    }
    try:
        root_state = root.stat(follow_symlinks=False)
    except FileNotFoundError:
        return {**expected, "status": "ABSENT"}
    except OSError as exc:
        return {**expected, "status": "UNREADABLE", "error": str(exc)}
    if (
        root.is_symlink()
        or not stat.S_ISDIR(root_state.st_mode)
        or root_state.st_uid != paths.systemd_unit_uid
        or root_state.st_gid != paths.systemd_unit_gid
        or stat.S_IMODE(root_state.st_mode) != _RESTART_OBLIGATION_DIRECTORY_MODE
    ):
        return {
            **expected,
            "status": "UNSAFE",
            "error": "unsafe restart obligation directory",
        }
    try:
        observed = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return {**expected, "status": "ABSENT"}
    except OSError as exc:
        return {**expected, "status": "UNREADABLE", "error": str(exc)}
    exact = (
        not path.is_symlink()
        and stat.S_ISREG(observed.st_mode)
        and observed.st_nlink == 1
        and observed.st_uid == paths.systemd_unit_uid
        and observed.st_gid == paths.systemd_unit_gid
        and stat.S_IMODE(observed.st_mode) == _RESTART_OBLIGATION_MODE
    )
    return {
        **expected,
        "status": "PRESENT" if exact else "UNSAFE",
        "uid": observed.st_uid,
        "gid": observed.st_gid,
        "mode": stat.S_IMODE(observed.st_mode),
    }


def _restart_obligation_identity(state: Mapping[str, Any]) -> dict[str, str]:
    return {
        key: str(state.get(key, ""))
        for key in (
            "MainPID",
            "InvocationID",
            "ExecMainStartTimestampMonotonic",
            "ActiveState",
            "SubState",
        )
    }


def _restart_obligation_service_identity_exact(
    unit: str, identity: Mapping[str, Any]
) -> bool:
    expected_keys = {
        "MainPID",
        "InvocationID",
        "ExecMainStartTimestampMonotonic",
        "ActiveState",
        "SubState",
    }
    if set(identity) != expected_keys or not all(
        isinstance(value, str) for value in identity.values()
    ):
        return False
    if identity["ActiveState"] != "active":
        return True
    if re.fullmatch(r"[0-9a-f]{32}", identity["InvocationID"]) is None:
        return False
    if unit.endswith(".service"):
        return (
            identity["MainPID"].isdigit()
            and int(identity["MainPID"]) > 0
            and identity["ExecMainStartTimestampMonotonic"].isdigit()
            and int(identity["ExecMainStartTimestampMonotonic"]) > 0
        )
    return True


def _validated_restart_obligation(
    unit: str,
    value: object,
    *,
    commit: str | None = None,
    tree: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DoctorError(f"restart obligation is invalid: {unit}")
    record = dict(value)
    digest = record.pop("obligation_hash", None)
    if (
        record.get("schema") != _RESTART_OBLIGATION_SCHEMA
        or not isinstance(record.get("commit"), str)
        or _COMMIT.fullmatch(record["commit"]) is None
        or not isinstance(record.get("tree"), str)
        or _COMMIT.fullmatch(record["tree"]) is None
        or (commit is not None and record.get("commit") != commit)
        or (tree is not None and record.get("tree") != tree)
        or record.get("unit") != unit
        or not isinstance(record.get("reasons"), list)
        or not record["reasons"]
        or not all(isinstance(reason, str) and reason for reason in record["reasons"])
        or record["reasons"] != sorted(set(record["reasons"]))
        or not isinstance(
            record.get("predecessor_obligation_hash"), (str, type(None))
        )
        or (
            record.get("predecessor_obligation_hash") is not None
            and re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                record["predecessor_obligation_hash"],
            )
            is None
        )
        or not isinstance(record.get("prechange_service"), Mapping)
        or not _restart_obligation_service_identity_exact(
            unit, record["prechange_service"]
        )
        or digest != _hash(record)
    ):
        raise DoctorError(f"restart obligation binding is invalid: {unit}")
    return {**record, "obligation_hash": digest}


def _read_restart_obligation(
    paths: DoctorPaths,
    unit: str,
    *,
    commit: str,
    tree: str,
    allow_predecessor: bool = False,
) -> dict[str, Any] | None:
    root = _ensure_restart_obligation_root(paths)
    path = _restart_obligation_path(paths, unit)
    directory = os.open(
        root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    descriptor = -1
    try:
        try:
            descriptor = os.open(
                path.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=directory,
            )
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise DoctorError(f"restart obligation is unreadable: {unit}") from exc
        observed = os.fstat(descriptor)
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_nlink != 1
            or observed.st_uid != paths.systemd_unit_uid
            or observed.st_gid != paths.systemd_unit_gid
            or stat.S_IMODE(observed.st_mode) != _RESTART_OBLIGATION_MODE
        ):
            raise DoctorError(f"restart obligation metadata is unsafe: {unit}")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        raw = b"".join(chunks)
        final = os.fstat(descriptor)
        if (
            observed.st_dev,
            observed.st_ino,
            observed.st_size,
            observed.st_mtime_ns,
            observed.st_ctime_ns,
        ) != (
            final.st_dev,
            final.st_ino,
            final.st_size,
            final.st_mtime_ns,
            final.st_ctime_ns,
        ):
            raise DoctorError(f"restart obligation changed while reading: {unit}")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DoctorError(f"restart obligation JSON is invalid: {unit}") from exc
        if raw != _json_bytes(value):
            raise DoctorError(f"restart obligation encoding is noncanonical: {unit}")
        return _validated_restart_obligation(
            unit,
            value,
            commit=None if allow_predecessor else commit,
            tree=None if allow_predecessor else tree,
        )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory)


def _write_restart_obligation(
    paths: DoctorPaths,
    unit: str,
    *,
    commit: str,
    tree: str,
    reasons: Sequence[str],
    state: Mapping[str, Any],
) -> dict[str, Any]:
    existing = _read_restart_obligation(
        paths,
        unit,
        commit=commit,
        tree=tree,
        allow_predecessor=True,
    )
    combined_reasons = set(reasons)
    predecessor_hash = None
    prechange_service = _restart_obligation_identity(state)
    if existing is not None:
        combined_reasons.update(existing["reasons"])
        if existing["commit"] == commit and existing["tree"] == tree:
            prechange_service = dict(existing["prechange_service"])
            predecessor_hash = existing["predecessor_obligation_hash"]
            if sorted(combined_reasons) == existing["reasons"]:
                return existing
        else:
            combined_reasons.add("carried-forward-restart-debt")
            predecessor_hash = existing["obligation_hash"]
    unsigned = {
        "schema": _RESTART_OBLIGATION_SCHEMA,
        "commit": commit,
        "tree": tree,
        "unit": unit,
        "reasons": sorted(combined_reasons),
        "prechange_service": prechange_service,
        "predecessor_obligation_hash": predecessor_hash,
    }
    value = {**unsigned, "obligation_hash": _hash(unsigned)}
    path = _restart_obligation_path(paths, unit)
    _atomic_bytes(
        path,
        _json_bytes(value),
        mode=_RESTART_OBLIGATION_MODE,
        uid=paths.systemd_unit_uid,
        gid=paths.systemd_unit_gid,
    )
    observed = _read_restart_obligation(
        paths, unit, commit=commit, tree=tree
    )
    if observed != value:
        raise DoctorError(f"restart obligation publication failed: {unit}")
    return value


def _restart_obligation_transition(
    unit: str,
    obligation: Mapping[str, Any],
    action_state: Mapping[str, Any],
    state: Mapping[str, Any],
) -> dict[str, Any]:
    marker_before = dict(obligation["prechange_service"])
    action_before = _restart_obligation_identity(action_state)
    after = _restart_obligation_identity(state)
    action_before_exact = _restart_obligation_service_identity_exact(
        unit, action_before
    )
    after_exact = _restart_obligation_service_identity_exact(unit, after)
    action_active_state = action_before["ActiveState"]
    before_active = action_active_state == "active"
    stable_action_state = action_active_state in {"active", "inactive", "failed"}
    invocation_changed = (
        not before_active
        or (
            after["InvocationID"] != action_before["InvocationID"]
            and (
                not unit.endswith(".service")
                or (
                    after["MainPID"] != action_before["MainPID"]
                    and after["ExecMainStartTimestampMonotonic"]
                    != action_before["ExecMainStartTimestampMonotonic"]
                )
            )
        )
    )
    exact = (
        stable_action_state
        and action_before_exact
        and after["ActiveState"] == "active"
        and after_exact
        and invocation_changed
    )
    return {
        "exact": exact,
        "marker_before": marker_before,
        "action_before": action_before,
        "after": after,
        "invocation_changed": invocation_changed,
    }


def _restart_obligation_operation(unit: str, state: Mapping[str, Any]) -> str:
    active_state = str(state.get("ActiveState", ""))
    if active_state == "active":
        return "restart"
    if active_state in {"inactive", "failed"}:
        return "start"
    raise DoctorError(
        f"refusing restart-obligation action for {unit}: "
        f"service state is {active_state or 'unreadable'}"
    )


def _clear_restart_obligation(
    paths: DoctorPaths,
    unit: str,
    *,
    commit: str,
    tree: str,
) -> None:
    expected = _read_restart_obligation(
        paths, unit, commit=commit, tree=tree
    )
    if expected is None:
        return
    root = _ensure_restart_obligation_root(paths)
    path = _restart_obligation_path(paths, unit)
    directory = os.open(
        root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    descriptor = -1
    try:
        descriptor = os.open(
            path.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=directory,
        )
        observed = os.fstat(descriptor)
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        current = json.loads(b"".join(chunks))
        if current != expected:
            raise DoctorError(f"restart obligation changed before clear: {unit}")
        final = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        if (observed.st_dev, observed.st_ino) != (final.st_dev, final.st_ino):
            raise DoctorError(f"restart obligation was replaced before clear: {unit}")
        os.unlink(path.name, dir_fd=directory)
        os.fsync(directory)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DoctorError(f"restart obligation clear failed: {unit}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory)


def repair_workers(
    paths: DoctorPaths, *, desired_commit: str | None = None
) -> dict[str, Any]:
    _require_root()
    if desired_commit is None:
        desired, release, _task = _desired_runtime(paths)
    else:
        if _COMMIT.fullmatch(desired_commit) is None:
            raise DoctorError("lifecycle worker repair commit is invalid")
        desired = desired_commit
        release = paths.runtime_root / "releases" / desired
        head, tree, status = _source_identity(paths)
        if status or head != desired or tree != _git(
            paths.repository, "rev-parse", f"{desired}^{{tree}}"
        ):
            raise DoctorError(
                "lifecycle worker repair requires the exact clean canonical candidate"
            )
    verification = _verify_release_tree(paths, desired, release)
    tree = str(verification.get("tree", ""))
    if _COMMIT.fullmatch(tree) is None:
        raise DoctorError("verified coding release has no exact Git tree identity")
    before = check_units(paths, desired_commit=desired)
    candidate_units: dict[str, bytes] = {}
    installed: list[str] = []
    for unit in _CODING_UNITS:
        destination = paths.systemd_install_root / unit
        try:
            source_mode, source_bytes = read_exact_tree_file(
                paths.repository,
                commit=desired,
                tree=tree,
                path=f"systemd/{unit}",
            )
        except ValueError as exc:
            raise DoctorError(f"exact candidate lacks coding unit: {unit}") from exc
        if source_mode != 0o644:
            raise DoctorError(f"exact candidate coding unit mode differs: {unit}")
        candidate_units[unit] = source_bytes
        if destination.is_symlink() or (destination.exists() and not destination.is_file()):
            raise DoctorError(f"refusing unsafe coding unit destination: {destination}")
        if not _unit_destination_bytes_exact(paths, destination, source_bytes):
            installed.append(unit)
    obligations: dict[str, dict[str, Any]] = {}
    for unit in _CODING_UNITS:
        existing = _read_restart_obligation(
            paths,
            unit,
            commit=desired,
            tree=tree,
            allow_predecessor=True,
        )
        if existing is not None:
            obligations[unit] = _write_restart_obligation(
                paths,
                unit,
                commit=desired,
                tree=tree,
                reasons=existing["reasons"],
                state=_unit_state(unit),
            )
    for unit in installed:
        obligations[unit] = _write_restart_obligation(
            paths,
            unit,
            commit=desired,
            tree=tree,
            reasons=["unit-definition-change"],
            state=_unit_state(unit),
        )
    for unit in installed:
        destination = paths.systemd_install_root / unit
        source_bytes = candidate_units[unit]
        if not _unit_destination_bytes_exact(paths, destination, source_bytes):
            _atomic_bytes(
                destination,
                source_bytes,
                mode=paths.systemd_unit_mode,
                uid=paths.systemd_unit_uid,
                gid=paths.systemd_unit_gid,
            )
    if installed or obligations:
        result = _run(["systemctl", "daemon-reload"], timeout=30)
        if result.returncode:
            raise DoctorError(result.stderr.strip() or "systemd daemon reload failed")
    actions: list[str] = []
    action_states: dict[str, dict[str, Any]] = {}
    for unit in _ACTIVE_CODING_UNITS:
        state = _unit_state(unit)
        definition = _unit_definition(
            paths, unit, state, desired_commit=desired
        )
        if not definition["exact"]:
            raise DoctorError(f"installed coding unit is not exact: {unit}")
        active = state.get("ActiveState") == "active"
        if unit in obligations:
            operation = "restart" if active else "start"
        elif unit.endswith(".service") and active:
            process_runtime = _service_process_runtime_identity(state, release)
            status = process_runtime["status"]
            if status == "EXACT":
                continue
            if status == "STALE" and process_runtime["restart_safe"] is True:
                operation = "restart"
            else:
                raise DoctorError(
                    f"refusing to restart {unit}: process runtime is {status.lower()}"
                )
        elif active:
            continue
        else:
            operation = "start"
        result = _run(["systemctl", "enable", unit], timeout=30)
        if result.returncode:
            raise DoctorError(result.stderr.strip() or f"failed to enable {unit}")
        if unit in obligations:
            action_state = _unit_state(unit)
            action_states[unit] = dict(action_state)
            operation = _restart_obligation_operation(
                unit, action_state
            )
        result = _run(["systemctl", operation, unit], timeout=30)
        if result.returncode:
            raise DoctorError(result.stderr.strip() or f"failed to {operation} {unit}")
        actions.append(f"{operation}:{unit}")
    after = check_units(
        paths,
        desired_commit=desired,
        observe_restart_obligations=False,
    )
    if after["state"] != "PASS":
        raise DoctorError("local coding units remain unhealthy after repair")
    transitions: dict[str, dict[str, Any]] = {}
    for unit, obligation in sorted(obligations.items()):
        state = _unit_state(unit)
        if unit in _ACTIVE_CODING_UNITS:
            transition = _restart_obligation_transition(
                unit,
                obligation,
                action_states[unit],
                state,
            )
            transitions[unit] = transition
            if not transition["exact"]:
                raise DoctorError(
                    f"{unit} did not load a new invocation after restart"
                )
        elif state.get("ActiveState") not in {"inactive", "failed"}:
            raise DoctorError(
                f"transient coding unit remains active after reload: {unit}"
            )
    for unit in sorted(obligations):
        _clear_restart_obligation(
            paths, unit, commit=desired, tree=tree
        )
    if obligations:
        after = check_units(paths, desired_commit=desired)
        if after["state"] != "PASS":
            raise DoctorError("local coding restart obligations did not clear")
    receipt = _receipt(paths, "workers", before, after)
    return {
        "ok": True,
        "operation": "workers",
        "changed": bool(installed or actions),
        "installed": installed,
        "restart_obligations": sorted(obligations),
        "restart_transitions": transitions,
        "service_actions": actions,
        "receipt": receipt,
    }


def _repair_managed_directory(path: Path, *, uid: int, gid: int, mode: int) -> bool:
    if not path.is_absolute() or path.name in ("", ".", ".."):
        raise DoctorError(f"unsafe managed directory path: {path}")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    root_descriptor = -1
    try:
        root_descriptor = os.open(path.anchor, flags)
        parent_descriptor = _open_relative_directory(root_descriptor, path.parent.relative_to(path.anchor))
    except OSError as exc:
        raise DoctorError(f"unsafe managed directory parent: {path.parent}") from exc
    finally:
        if root_descriptor >= 0:
            os.close(root_descriptor)
    descriptor = -1
    created = False
    changed = False
    try:
        try:
            os.mkdir(path.name, mode=mode, dir_fd=parent_descriptor)
            created = True
            changed = True
        except FileExistsError:
            pass
        try:
            descriptor = os.open(path.name, flags, dir_fd=parent_descriptor)
        except OSError as exc:
            raise DoctorError(f"unsafe managed directory destination: {path}") from exc
        observed = os.fstat(descriptor)
        observed_mode = stat.S_IMODE(observed.st_mode)
        if not stat.S_ISDIR(observed.st_mode):
            raise DoctorError(f"unsafe managed directory destination: {path}")
        if observed.st_uid != uid or observed.st_gid != gid:
            os.fchown(descriptor, uid, gid)
            changed = True
        if observed_mode != mode or changed:
            os.fchmod(descriptor, mode)
            changed = True
        os.fsync(descriptor)
        os.fsync(parent_descriptor)
    except Exception:
        if created:
            try:
                os.rmdir(path.name, dir_fd=parent_descriptor)
            except OSError:
                pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)
    return changed


def _repair_plan_render_storage(paths: DoctorPaths) -> bool:
    owner = pwd.getpwnam("db")
    group = grp.getgrnam("tgw-coders")
    changed = False
    for path in (paths.plan_render_root, paths.plan_render_log_root):
        changed = (
            _repair_managed_directory(
                path,
                uid=owner.pw_uid,
                gid=group.gr_gid,
                mode=_PLAN_RENDER_DIRECTORY_MODE,
            )
            or changed
        )
    after = _plan_render_storage_identity(paths)
    if not after["exact"]:
        raise DoctorError("plan_render output directories remain unsafe after repair")
    return changed


def repair_plan_render_worker(
    paths: DoctorPaths, *, desired_commit: str | None = None
) -> dict[str, Any]:
    _require_root()
    explicit_commit = desired_commit is not None
    if desired_commit is None:
        desired, release, _task = _desired_runtime(paths)
    else:
        if _COMMIT.fullmatch(desired_commit) is None:
            raise DoctorError("plan_render repair commit is invalid")
        desired = desired_commit
        release = paths.runtime_root / "releases" / desired
        head, tree, status = _source_identity(paths)
        if status or head != desired or tree != _git(
            paths.repository, "rev-parse", f"{desired}^{{tree}}"
        ):
            raise DoctorError(
                "plan_render repair requires the exact clean canonical candidate"
            )
    verification = _verify_release_tree(paths, desired, release)
    runtime = _runtime_selector_identity(paths, desired, release)
    if not runtime["exact"]:
        raise DoctorError("immutable runtime selector differs; repair runtime before plan_render")
    tree = str(verification.get("tree", ""))
    if _COMMIT.fullmatch(tree) is None:
        raise DoctorError("verified plan_render release has no exact Git tree identity")
    before = (
        check_plan_render_worker(paths, desired_commit=desired)
        if explicit_commit
        else check_plan_render_worker(paths)
    )
    try:
        config_mode, config_bytes = read_exact_tree_file(
            paths.repository,
            commit=desired,
            tree=tree,
            path="config/tgw-plan-render-local.json",
        )
        unit_mode, unit_bytes = read_exact_tree_file(
            paths.repository,
            commit=desired,
            tree=tree,
            path=f"systemd/{_PLAN_RENDER_UNIT}",
        )
    except ValueError as exc:
        raise DoctorError("exact candidate lacks plan_render config or unit") from exc
    if config_mode != 0o644 or unit_mode != 0o644:
        raise DoctorError("exact candidate plan_render file mode differs")
    if paths.plan_render_config.is_symlink() or (paths.plan_render_config.exists() and not paths.plan_render_config.is_file()):
        raise DoctorError("refusing unsafe plan_render config destination")
    destination = paths.systemd_install_root / _PLAN_RENDER_UNIT
    if destination.is_symlink() or (destination.exists() and not destination.is_file()):
        raise DoctorError("refusing unsafe plan_render unit destination")
    storage_before = _plan_render_storage_identity(paths)
    config_changed = (
        not paths.plan_render_config.is_file()
        or paths.plan_render_config.read_bytes() != config_bytes
    )
    unit_changed = not _unit_destination_bytes_exact(
        paths, destination, unit_bytes
    )
    storage_changed = not storage_before["exact"]
    existing_obligation = _read_restart_obligation(
        paths,
        _PLAN_RENDER_UNIT,
        commit=desired,
        tree=tree,
        allow_predecessor=True,
    )
    obligation_preexisting = existing_obligation is not None
    obligation = existing_obligation
    if obligation is not None or config_changed or unit_changed or storage_changed:
        reasons = [] if obligation is None else list(obligation["reasons"])
        if config_changed:
            reasons.append("config-change")
        if unit_changed:
            reasons.append("unit-definition-change")
        if storage_changed:
            reasons.append("storage-change")
        obligation = _write_restart_obligation(
            paths,
            _PLAN_RENDER_UNIT,
            commit=desired,
            tree=tree,
            reasons=reasons,
            state=_unit_state(_PLAN_RENDER_UNIT),
        )
    if config_changed:
        _atomic_bytes(paths.plan_render_config, config_bytes, mode=0o444, uid=paths.systemd_unit_uid, gid=paths.systemd_unit_gid)
    if unit_changed:
        _atomic_bytes(destination, unit_bytes, mode=paths.systemd_unit_mode, uid=paths.systemd_unit_uid, gid=paths.systemd_unit_gid)
    if unit_changed or obligation is not None:
        result = _run(["systemctl", "daemon-reload"], timeout=30)
        if result.returncode:
            raise DoctorError(result.stderr.strip() or "systemd daemon reload failed")
    changed = config_changed or unit_changed
    state = _unit_state(_PLAN_RENDER_UNIT)
    definition = (
        _unit_definition(
            paths, _PLAN_RENDER_UNIT, state, desired_commit=desired
        )
        if explicit_commit
        else _unit_definition(paths, _PLAN_RENDER_UNIT, state)
    )
    if not definition["exact"]:
        raise DoctorError("installed plan_render unit is not exact")
    process_runtime = _plan_render_process_runtime_identity(state, release)
    storage_started_receipt = _receipt(
        paths,
        "plan-render-storage-started",
        storage_before,
        {"state": "STARTED"},
    )
    try:
        changed = _repair_plan_render_storage(paths) or changed
        storage_after = _plan_render_storage_identity(paths)
        storage_receipt = _receipt(paths, "plan-render-storage", storage_before, storage_after)
    except Exception as exc:
        try:
            storage_failed = _plan_render_storage_identity(paths)
        except Exception as identity_exc:
            storage_failed = {"identity_error": str(identity_exc)}
        storage_failure_receipt = _receipt(
            paths,
            "plan-render-storage-failed",
            storage_before,
            {"error": str(exc), "storage": storage_failed},
        )
        raise DoctorError(f"plan_render storage repair failed: {exc}; started receipt: {storage_started_receipt}; failure receipt: {storage_failure_receipt}") from exc
    action = None
    action_reason = None
    active = state.get("ActiveState") == "active"
    runtime_status = str(process_runtime.get("status", "UNREADABLE"))
    if obligation is not None:
        action = "restart" if active else "start"
        action_reason = (
            "durable-restart-obligation"
            if obligation_preexisting
            else "materialization-changed"
        )
    elif not active:
        action = "start"
        action_reason = "inactive"
    elif runtime_status == "EXACT" and process_runtime.get("exact") is True:
        pass
    elif (
        runtime_status == "STALE"
        and process_runtime.get("exact") is False
        and process_runtime.get("restart_safe") is True
    ):
        action = "restart"
        action_reason = "stale-runtime"
    else:
        raise DoctorError(
            "refusing to restart plan_render service: process runtime is "
            f"{runtime_status.lower()}"
        )
    if action is not None:
        result = _run(["systemctl", "enable", _PLAN_RENDER_UNIT], timeout=30)
        if result.returncode:
            raise DoctorError(
                result.stderr.strip() or "plan_render service repair failed"
            )
        action_state = state
        if obligation is not None:
            action_state = _unit_state(_PLAN_RENDER_UNIT)
            action = _restart_obligation_operation(
                _PLAN_RENDER_UNIT, action_state
            )
        result = _run(["systemctl", action, _PLAN_RENDER_UNIT], timeout=30)
        if result.returncode:
            raise DoctorError(
                result.stderr.strip() or "plan_render service repair failed"
            )
    after = check_plan_render_worker(
        paths,
        desired_commit=desired if explicit_commit else None,
        observe_restart_obligations=False,
    )
    if after["state"] != "PASS":
        raise DoctorError("plan_render unit remains unhealthy after repair")
    transition = None
    if obligation is not None:
        transition = _restart_obligation_transition(
            _PLAN_RENDER_UNIT,
            obligation,
            action_state,
            _unit_state(_PLAN_RENDER_UNIT),
        )
        if not transition["exact"]:
            raise DoctorError(
                "plan_render service did not load a new invocation after restart"
            )
        _clear_restart_obligation(
            paths,
            _PLAN_RENDER_UNIT,
            commit=desired,
            tree=tree,
        )
    if obligation is not None:
        after = (
            check_plan_render_worker(paths, desired_commit=desired)
            if explicit_commit
            else check_plan_render_worker(paths)
        )
        if after["state"] != "PASS":
            raise DoctorError("plan_render restart obligation did not clear")
    return {
        "ok": True,
        "operation": "plan-render-worker",
        "changed": changed or action is not None,
        "service_action": action,
        "service_action_reason": action_reason,
        "process_runtime_status": runtime_status,
        "restart_obligation": (
            None if obligation is None else obligation["obligation_hash"]
        ),
        "restart_transition": transition,
        "storage_started_receipt": storage_started_receipt,
        "storage_receipt": storage_receipt,
        "receipt": _receipt(paths, "plan-render-worker", before, after),
    }


def _cleanup_references(paths: DoctorPaths, surfaces: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    needles = {
        value
        for item in surfaces
        for value in (
            str(item["path"]),
            Path(item["path"]).name,
            item.get("declared_target"),
        )
        if isinstance(value, str) and value
    }
    references: list[dict[str, str]] = []
    actor_home = paths.cleanup_actor_home
    actor_configuration = (
        actor_home / ".bash_profile",
        actor_home / ".bashrc",
        actor_home / ".profile",
        actor_home / ".zshrc",
        actor_home / ".codex/config.toml",
        actor_home / ".claude/settings.json",
        actor_home / ".config/hermes/config.toml",
        actor_home / ".config/opencode/opencode.json",
        actor_home / ".gemini/settings.json",
        actor_home / ".antigravity/settings.json",
    )
    evidence_records = {path.resolve(strict=False) for path in (paths.context_snapshot, paths.context_task, paths.context_cursor)}
    roots = tuple(dict.fromkeys((*paths.cleanup_reference_roots, *actor_configuration)))
    for root in roots:
        if root.resolve(strict=False) in evidence_records:
            continue
        if root.is_file() or root.is_symlink():
            candidates = [root]
        elif root.is_dir():
            candidates = _configuration_candidates(root)
        else:
            continue
        for candidate in candidates:
            if candidate.resolve(strict=False) in evidence_records:
                continue
            try:
                if candidate.is_symlink():
                    raw = os.readlink(candidate).encode()
                elif candidate.is_file():
                    raw = candidate.read_bytes()
                else:
                    continue
            except OSError as exc:
                raise DoctorError(f"cannot completely inspect active configuration: {candidate}") from exc
            for needle in sorted(needles):
                if needle.encode() in raw:
                    references.append({"path": str(candidate), "reference": needle})
    return references


def _configuration_candidates(root: Path) -> list[Path]:
    """Enumerate every entry without pathlib's permission-error suppression."""
    candidates: list[Path] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    candidate = Path(entry.path)
                    candidates.append(candidate)
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(candidate)
        except OSError as exc:
            raise DoctorError(f"cannot completely scan active configuration: {directory}") from exc
    return candidates


def _cleanup_process_references(surfaces: Sequence[Mapping[str, Any]], proc_root: Path = Path("/proc")) -> list[dict[str, Any]]:
    needles = {
        value
        for item in surfaces
        for value in (
            str(item["path"]),
            Path(item["path"]).name,
            item.get("declared_target"),
            Path(item["declared_target"]).name if isinstance(item.get("declared_target"), str) else None,
        )
        if isinstance(value, str) and value
    }
    references = []
    if not proc_root.is_dir():
        raise DoctorError("process inventory is unavailable; cleanup refuses unknown activity")
    for entry in proc_root.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            argv = [value.decode(errors="replace") for value in (entry / "cmdline").read_bytes().split(b"\0") if value]
        except OSError as exc:
            if exc.errno in (errno.ENOENT, errno.ESRCH):
                continue
            raise DoctorError(f"cannot completely inspect process activity: {entry}") from exc
        matched = sorted(needle for needle in needles if any(argument == needle or argument.startswith(needle + " ") for argument in argv))
        if matched:
            references.append({"pid": int(entry.name), "command": " ".join(argv), "references": matched})
    return references


@dataclass
class _CleanupBinding:
    item: Mapping[str, Any]
    path: Path
    parent_fd: int
    source_fd: int | None
    parent_dev: int
    parent_ino: int
    source_dev: int
    source_ino: int


def _open_direct_directory(path: Path) -> int:
    if not path.is_absolute():
        raise DoctorError(f"cleanup path is not absolute: {path}")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path.anchor, flags)
    traversed = Path(path.anchor)
    try:
        for component in path.parts[1:]:
            if component in ("", ".", ".."):
                raise DoctorError(f"cleanup path has an unsafe component: {path}")
            traversed /= component
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except OSError as exc:
                raise DoctorError(f"cleanup parent path is not a direct directory: {traversed}") from exc
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


@contextmanager
def _bind_cleanup_parent(path: Path, item: Mapping[str, Any]) -> Iterator[_CleanupBinding]:
    parent_fd = _open_direct_directory(path.parent)
    try:
        bound_parent = os.fstat(parent_fd)
        try:
            visible_parent = path.parent.stat(follow_symlinks=False)
        except OSError as exc:
            raise DoctorError(f"cleanup parent path is unavailable: {path.parent}") from exc
        if (visible_parent.st_dev, visible_parent.st_ino) != (
            bound_parent.st_dev,
            bound_parent.st_ino,
        ):
            raise DoctorError(f"cleanup parent changed while binding: {path.parent}")
        yield _CleanupBinding(
            item=item,
            path=path,
            parent_fd=parent_fd,
            source_fd=None,
            parent_dev=bound_parent.st_dev,
            parent_ino=bound_parent.st_ino,
            source_dev=0,
            source_ino=0,
        )
    finally:
        os.close(parent_fd)


@contextmanager
def _bind_cleanup_surface(item: Mapping[str, Any]) -> Iterator[_CleanupBinding]:
    path = Path(item["path"])
    parent_fd = _open_direct_directory(path.parent)
    source_fd: int | None = None
    try:
        path_parent = path.parent.stat(follow_symlinks=False)
        bound_parent = os.fstat(parent_fd)
        if (path_parent.st_dev, path_parent.st_ino) != (
            bound_parent.st_dev,
            bound_parent.st_ino,
        ):
            raise DoctorError(f"cleanup parent changed while binding: {path.parent}")
        source_state = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if item["kind"] == "file":
            if not stat.S_ISREG(source_state.st_mode):
                raise DoctorError(f"obsolete surface is not the declared regular file: {path}")
            source_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                source_fd = os.open(
                    path.name,
                    source_flags | getattr(os, "O_NOATIME", 0),
                    dir_fd=parent_fd,
                )
            except PermissionError:
                source_fd = os.open(path.name, source_flags, dir_fd=parent_fd)
            opened_state = os.fstat(source_fd)
            if (opened_state.st_dev, opened_state.st_ino) != (
                source_state.st_dev,
                source_state.st_ino,
            ):
                raise DoctorError(f"obsolete surface changed while binding: {path}")
        elif item["kind"] == "symlink":
            if not stat.S_ISLNK(source_state.st_mode):
                raise DoctorError(f"obsolete surface is not the declared symlink: {path}")
        else:
            raise DoctorError(f"unsupported obsolete surface type: {item['kind']}")
        yield _CleanupBinding(
            item=item,
            path=path,
            parent_fd=parent_fd,
            source_fd=source_fd,
            parent_dev=bound_parent.st_dev,
            parent_ino=bound_parent.st_ino,
            source_dev=source_state.st_dev,
            source_ino=source_state.st_ino,
        )
    finally:
        if source_fd is not None:
            os.close(source_fd)
        os.close(parent_fd)


def _read_xattrs(path_or_fd: Path | int, *, follow_symlinks: bool = True) -> dict[str, str]:
    if isinstance(path_or_fd, int):
        names = os.listxattr(path_or_fd)
        return {name: base64.b64encode(os.getxattr(path_or_fd, name)).decode("ascii") for name in sorted(names)}
    names = os.listxattr(path_or_fd, follow_symlinks=follow_symlinks)
    return {name: base64.b64encode(os.getxattr(path_or_fd, name, follow_symlinks=follow_symlinks)).decode("ascii") for name in sorted(names)}


def _metadata(state: os.stat_result, xattrs: Mapping[str, str]) -> dict[str, Any]:
    return {
        "uid": state.st_uid,
        "gid": state.st_gid,
        "mode": stat.S_IMODE(state.st_mode),
        "atime_ns": state.st_atime_ns,
        "mtime_ns": state.st_mtime_ns,
        "xattrs": dict(xattrs),
    }


def _hash_fd(descriptor: int, path: Path) -> str:
    before = os.fstat(descriptor)
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    for chunk in iter(lambda: os.read(descriptor, 1024 * 1024), b""):
        digest.update(chunk)
    after = os.fstat(descriptor)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise DoctorError(f"obsolete surface changed while hashing: {path}")
    return "sha256:" + digest.hexdigest()


def _assert_parent_binding(binding: _CleanupBinding) -> None:
    bound = os.fstat(binding.parent_fd)
    try:
        visible = binding.path.parent.stat(follow_symlinks=False)
    except OSError as exc:
        raise DoctorError(f"cleanup parent disappeared: {binding.path.parent}") from exc
    expected = (binding.parent_dev, binding.parent_ino)
    if (bound.st_dev, bound.st_ino) != expected or (visible.st_dev, visible.st_ino) != expected:
        raise DoctorError(f"cleanup parent changed after binding: {binding.path.parent}")


def _verify_bound_cleanup_surface(binding: _CleanupBinding) -> dict[str, Any]:
    _assert_parent_binding(binding)
    try:
        visible = os.stat(binding.path.name, dir_fd=binding.parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise DoctorError(f"obsolete surface disappeared after binding: {binding.path}") from exc
    if (visible.st_dev, visible.st_ino) != (binding.source_dev, binding.source_ino):
        raise DoctorError(f"obsolete surface changed after binding: {binding.path}")
    if binding.item["kind"] == "symlink":
        if not stat.S_ISLNK(visible.st_mode):
            raise DoctorError(f"obsolete surface is not the declared symlink: {binding.path}")
        target = os.readlink(binding.path.name, dir_fd=binding.parent_fd)
        if target != binding.item["declared_target"]:
            raise DoctorError(f"obsolete symlink target changed; refusing cleanup: {binding.path}")
        xattrs = _read_xattrs(binding.path, follow_symlinks=False)
        after = os.stat(binding.path.name, dir_fd=binding.parent_fd, follow_symlinks=False)
        if (after.st_dev, after.st_ino, after.st_ctime_ns) != (
            visible.st_dev,
            visible.st_ino,
            visible.st_ctime_ns,
        ):
            raise DoctorError(f"obsolete symlink changed during inspection: {binding.path}")
        identity: dict[str, Any] = {
            "path": str(binding.path),
            "kind": "symlink",
            "target": target,
            "metadata": _metadata(after, xattrs),
        }
    else:
        if binding.source_fd is None or not stat.S_ISREG(visible.st_mode):
            raise DoctorError(f"obsolete surface is not the declared regular file: {binding.path}")
        digest = _hash_fd(binding.source_fd, binding.path)
        if not binding.item.get("declared_sha256"):
            raise DoctorError(f"obsolete surface has no declared file hash; refusing cleanup: {binding.path}")
        if digest != binding.item["declared_sha256"]:
            raise DoctorError(f"obsolete surface bytes changed; refusing cleanup: {binding.path}")
        opened = os.fstat(binding.source_fd)
        after = os.stat(binding.path.name, dir_fd=binding.parent_fd, follow_symlinks=False)
        if (opened.st_dev, opened.st_ino, opened.st_ctime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_ctime_ns,
        ):
            raise DoctorError(f"obsolete surface changed during inspection: {binding.path}")
        identity = {
            "path": str(binding.path),
            "kind": "file",
            "sha256": digest,
            "metadata": _metadata(opened, _read_xattrs(binding.source_fd)),
        }
    identity["binding"] = {
        "parent_dev": binding.parent_dev,
        "parent_ino": binding.parent_ino,
        "source_dev": binding.source_dev,
        "source_ino": binding.source_ino,
        "source_ctime_ns": visible.st_ctime_ns,
    }
    return identity


def _verify_cleanup_surface(item: Mapping[str, Any]) -> dict[str, Any]:
    with _bind_cleanup_surface(item) as binding:
        return _verify_bound_cleanup_surface(binding)


def _archive_relative(path: Path) -> Path:
    return Path(str(path).lstrip("/"))


def _fsync_directory_fd(descriptor: int) -> None:
    os.fsync(descriptor)


def _durable_mkdir(path: Path, *, mode: int = 0o755, require_new: bool = False) -> None:
    if not path.is_absolute():
        raise DoctorError(f"archive path is not absolute: {path}")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path.anchor, flags)
    traversed = Path(path.anchor)
    try:
        components = path.parts[1:]
        for index, component in enumerate(components):
            if component in ("", ".", ".."):
                raise DoctorError(f"archive path has an unsafe component: {path}")
            traversed /= component
            final = index == len(components) - 1
            created = False
            if require_new and final:
                try:
                    os.mkdir(component, mode=mode, dir_fd=descriptor)
                except FileExistsError as exc:
                    raise DoctorError(f"cleanup archive name collision: {path}") from exc
                created = True
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                try:
                    os.mkdir(component, mode=mode, dir_fd=descriptor)
                except OSError as exc:
                    raise DoctorError(f"cannot durably create archive directory: {traversed}") from exc
                created = True
                child = os.open(component, flags, dir_fd=descriptor)
            except OSError as exc:
                raise DoctorError(f"archive directory is not direct: {traversed}") from exc
            if created:
                os.fsync(descriptor)
                os.fsync(child)
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)


def _replace_xattrs(path_or_fd: Path | int, values: Mapping[str, str], *, follow: bool = True) -> None:
    if isinstance(path_or_fd, int):
        existing = set(os.listxattr(path_or_fd))
        for name in existing - set(values):
            os.removexattr(path_or_fd, name)
        for name, value in values.items():
            os.setxattr(path_or_fd, name, base64.b64decode(value))
        return
    existing = set(os.listxattr(path_or_fd, follow_symlinks=follow))
    for name in existing - set(values):
        os.removexattr(path_or_fd, name, follow_symlinks=follow)
    for name, value in values.items():
        os.setxattr(
            path_or_fd,
            name,
            base64.b64decode(value),
            follow_symlinks=follow,
        )


def _apply_fd_metadata(descriptor: int, metadata: Mapping[str, Any]) -> None:
    os.fchown(descriptor, int(metadata["uid"]), int(metadata["gid"]))
    os.fchmod(descriptor, int(metadata["mode"]))
    _replace_xattrs(descriptor, metadata.get("xattrs", {}))
    os.utime(
        descriptor,
        ns=(int(metadata["atime_ns"]), int(metadata["mtime_ns"])),
    )
    os.fsync(descriptor)


def _open_path_noatime(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(path, flags | getattr(os, "O_NOATIME", 0))
    except PermissionError:
        return os.open(path, flags)


def _path_recovery_identity(path: Path, kind: str) -> dict[str, Any]:
    if kind == "symlink":
        state = path.stat(follow_symlinks=False)
        return {
            "path": str(path),
            "kind": kind,
            "target": os.readlink(path),
            "metadata": _metadata(state, _read_xattrs(path, follow_symlinks=False)),
        }
    descriptor = _open_path_noatime(path)
    try:
        digest = _hash_fd(descriptor, path)
        state = os.fstat(descriptor)
        return {
            "path": str(path),
            "kind": kind,
            "sha256": digest,
            "metadata": _metadata(state, _read_xattrs(descriptor)),
        }
    finally:
        os.close(descriptor)


def _restorable_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("kind", "target", "sha256", "metadata")
    return {key: identity[key] for key in keys if key in identity}


def _stable_bound_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    """Return security-relevant identity, excluding inspection-induced atime."""
    value = json.loads(json.dumps(identity))
    metadata = value.get("metadata")
    if isinstance(metadata, dict):
        metadata.pop("atime_ns", None)
    return value


def _stable_restorable_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    return _stable_bound_identity(_restorable_identity(identity))


def _verify_archived_surface(path: Path, identity: Mapping[str, Any]) -> None:
    if not _lexists(path):
        raise DoctorError(f"recovery archive surface is missing: {path}")
    observed = _path_recovery_identity(path, str(identity["kind"]))
    if _stable_restorable_identity(observed) != _stable_restorable_identity(identity):
        raise DoctorError(f"recovery archive identity differs: {path}")


def _apply_bound_symlink_metadata(parent_fd: int, name: str, metadata: Mapping[str, Any]) -> None:
    os.chown(
        name,
        int(metadata["uid"]),
        int(metadata["gid"]),
        dir_fd=parent_fd,
        follow_symlinks=False,
    )
    proc_path = Path(f"/proc/self/fd/{parent_fd}") / name
    _replace_xattrs(proc_path, metadata.get("xattrs", {}), follow=False)
    os.utime(
        name,
        ns=(int(metadata["atime_ns"]), int(metadata["mtime_ns"])),
        dir_fd=parent_fd,
        follow_symlinks=False,
    )


def _copy_cleanup_surface(binding: _CleanupBinding, destination: Path) -> dict[str, Any]:
    before = _verify_bound_cleanup_surface(binding)
    _durable_mkdir(destination.parent)
    archive_parent_fd = _open_direct_directory(destination.parent)
    try:
        parent_state = os.fstat(archive_parent_fd)
        visible_parent = destination.parent.stat(follow_symlinks=False)
        if (parent_state.st_dev, parent_state.st_ino) != (
            visible_parent.st_dev,
            visible_parent.st_ino,
        ):
            raise DoctorError(f"archive parent changed while binding: {destination.parent}")
        if before["kind"] == "symlink":
            os.symlink(before["target"], destination.name, dir_fd=archive_parent_fd)
            final = _verify_bound_cleanup_surface(binding)
            _apply_bound_symlink_metadata(archive_parent_fd, destination.name, final["metadata"])
        else:
            if binding.source_fd is None:
                raise DoctorError(f"obsolete file binding is unavailable: {binding.path}")
            destination_fd = os.open(
                destination.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=archive_parent_fd,
            )
            try:
                os.lseek(binding.source_fd, 0, os.SEEK_SET)
                digest = hashlib.sha256()
                while chunk := os.read(binding.source_fd, 1024 * 1024):
                    digest.update(chunk)
                    view = memoryview(chunk)
                    while view:
                        written = os.write(destination_fd, view)
                        view = view[written:]
                os.fsync(destination_fd)
                if "sha256:" + digest.hexdigest() != before["sha256"]:
                    raise DoctorError(f"obsolete surface changed while archiving: {binding.path}")
                final = _verify_bound_cleanup_surface(binding)
                if final["sha256"] != before["sha256"]:
                    raise DoctorError(f"obsolete surface changed while archiving: {binding.path}")
                _apply_fd_metadata(destination_fd, final["metadata"])
            finally:
                os.close(destination_fd)
        archived_state = os.stat(destination.name, dir_fd=archive_parent_fd, follow_symlinks=False)
        archived_binding = _CleanupBinding(
            item=final,
            path=destination,
            parent_fd=archive_parent_fd,
            source_fd=None,
            parent_dev=parent_state.st_dev,
            parent_ino=parent_state.st_ino,
            source_dev=archived_state.st_dev,
            source_ino=archived_state.st_ino,
        )
        observed = _bound_recovery_identity(archived_binding)
        if _stable_restorable_identity(observed) != _stable_restorable_identity(final):
            raise DoctorError(f"recovery archive identity differs: {destination}")
        _fsync_directory_fd(archive_parent_fd)
        _assert_parent_binding(archived_binding)
        return final
    finally:
        os.close(archive_parent_fd)


def _restore_cleanup_surface(destination: Path, archived: Path, identity: Mapping[str, Any]) -> None:
    with _bind_cleanup_parent(destination, identity) as binding:
        _restore_bound_cleanup_surface(binding, archived, identity)
        _assert_parent_binding(binding)


def _bound_recovery_identity(binding: _CleanupBinding) -> dict[str, Any]:
    state = os.stat(binding.path.name, dir_fd=binding.parent_fd, follow_symlinks=False)
    if stat.S_ISLNK(state.st_mode):
        proc_path = Path(f"/proc/self/fd/{binding.parent_fd}") / binding.path.name
        return {
            "path": str(binding.path),
            "kind": "symlink",
            "target": os.readlink(binding.path.name, dir_fd=binding.parent_fd),
            "metadata": _metadata(state, _read_xattrs(proc_path, follow_symlinks=False)),
        }
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(
            binding.path.name,
            flags | getattr(os, "O_NOATIME", 0),
            dir_fd=binding.parent_fd,
        )
    except PermissionError:
        descriptor = os.open(binding.path.name, flags, dir_fd=binding.parent_fd)
    try:
        return {
            "path": str(binding.path),
            "kind": "file",
            "sha256": _hash_fd(descriptor, binding.path),
            "metadata": _metadata(os.fstat(descriptor), _read_xattrs(descriptor)),
        }
    finally:
        os.close(descriptor)


def _restore_bound_cleanup_surface(binding: _CleanupBinding, archived: Path, identity: Mapping[str, Any]) -> None:
    try:
        os.stat(binding.path.name, dir_fd=binding.parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        observed = _bound_recovery_identity(binding)
        if _stable_restorable_identity(observed) != _stable_restorable_identity(identity):
            raise DoctorError(f"bound active surface differs during recovery: {binding.path}")
        return
    _verify_archived_surface(archived, identity)
    if identity["kind"] == "symlink":
        os.symlink(identity["target"], binding.path.name, dir_fd=binding.parent_fd)
        _apply_bound_symlink_metadata(binding.parent_fd, binding.path.name, identity["metadata"])
    else:
        source_fd = _open_path_noatime(archived)
        destination_fd = os.open(
            binding.path.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=binding.parent_fd,
        )
        try:
            while chunk := os.read(source_fd, 1024 * 1024):
                view = memoryview(chunk)
                while view:
                    written = os.write(destination_fd, view)
                    view = view[written:]
            os.fsync(destination_fd)
            _apply_fd_metadata(destination_fd, identity["metadata"])
        finally:
            os.close(source_fd)
            os.close(destination_fd)
    observed = _bound_recovery_identity(binding)
    if _stable_restorable_identity(observed) != _stable_restorable_identity(identity):
        raise DoctorError(f"bound restored surface identity differs: {binding.path}")
    _fsync_directory_fd(binding.parent_fd)


def _unlink_bound_surface(binding: _CleanupBinding, identity: Mapping[str, Any]) -> None:
    if _stable_bound_identity(_verify_bound_cleanup_surface(binding)) != _stable_bound_identity(identity):
        raise DoctorError(f"obsolete surface changed at removal: {binding.path}")
    os.unlink(binding.path.name, dir_fd=binding.parent_fd)


def _validate_cleanup_archive(paths: DoctorPaths, archive: Path) -> dict[str, Any]:
    root = paths.cleanup_archive_root.resolve(strict=True)
    resolved_archive = archive.resolve(strict=True)
    if not resolved_archive.is_relative_to(root):
        raise DoctorError(f"cleanup archive escapes the canonical root: {archive}")
    manifest_path = archive / "manifest.json"
    manifest = _json(manifest_path)
    if manifest.get("schema") != "tgw-doctor-obsolete-surface-archive/v1":
        raise DoctorError(f"cleanup archive manifest schema differs: {manifest_path}")
    claimed = manifest.get("manifest_sha256")
    body = dict(manifest)
    body.pop("manifest_sha256", None)
    if not isinstance(claimed, str) or claimed != _hash(body):
        raise DoctorError(f"cleanup archive manifest hash differs: {manifest_path}")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise DoctorError(f"cleanup archive manifest has no entries: {manifest_path}")
    seen = set()
    for identity in entries:
        if not isinstance(identity, Mapping) or not isinstance(identity.get("path"), str):
            raise DoctorError(f"cleanup archive entry is malformed: {manifest_path}")
        source = Path(identity["path"])
        if not source.is_absolute() or str(source) in seen:
            raise DoctorError(f"cleanup archive source identity is ambiguous: {source}")
        seen.add(str(source))
        expected = archive / "active-root" / _archive_relative(source)
        if Path(identity.get("archive_path", "")) != expected:
            raise DoctorError(f"cleanup archive path binding differs: {source}")
        if not expected.parent.resolve(strict=True).is_relative_to(resolved_archive):
            raise DoctorError(f"cleanup archive entry escapes its archive: {expected}")
        _verify_archived_surface(expected, identity)
    return manifest


def _incomplete_cleanup_archives(paths: DoctorPaths) -> list[Path]:
    root = paths.cleanup_archive_root
    if not root.is_dir():
        return []
    incomplete = []
    for manifest in root.rglob("manifest.json"):
        archive = manifest.parent
        if (archive / "COMPLETED.json").is_file() or (archive / "ROLLED_BACK.json").is_file():
            continue
        incomplete.append(archive)
    return sorted(incomplete)


def _write_archive_state(archive: Path, name: str, value: Mapping[str, Any]) -> str:
    path = archive / f"{name}.json"
    body = {
        "schema": "tgw-doctor-obsolete-surface-state/v1",
        "state": name,
        **dict(value),
    }
    body["state_sha256"] = _hash(body)
    _atomic_json(path, body, mode=0o400)
    return str(path)


def _reconcile_incomplete_cleanup(paths: DoctorPaths, declared: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    incomplete = _incomplete_cleanup_archives(paths)
    if not incomplete:
        return None
    if len(incomplete) != 1:
        raise DoctorError("multiple incomplete obsolete-surface archives require bounded recovery: " + ", ".join(str(path) for path in incomplete))
    archive = incomplete[0]
    manifest = _validate_cleanup_archive(paths, archive)
    declaration_by_path = {str(item["path"]): item for item in declared}
    entries = manifest["entries"]
    if any(identity["path"] not in declaration_by_path for identity in entries):
        raise DoctorError(f"incomplete cleanup archive contains an undeclared path: {archive}")
    present = [identity for identity in entries if _lexists(Path(identity["path"]))]
    for identity in present:
        observed = _verify_cleanup_surface(declaration_by_path[identity["path"]])
        if _stable_restorable_identity(observed) != _stable_restorable_identity(identity):
            raise DoctorError(f"active surface differs from incomplete archive: {identity['path']}")
    if not present:
        receipt = _receipt(
            paths,
            "obsolete-surfaces",
            {"archive": str(archive), "state": "PREPARED_OR_INTERRUPTED"},
            {"state": "COMPLETED", "removed": entries, "reconciled": True},
        )
        marker = _write_archive_state(
            archive,
            "COMPLETED",
            {"receipt": receipt, "manifest_sha256": manifest["manifest_sha256"]},
        )
        return {
            "ok": True,
            "operation": "obsolete-surfaces",
            "changed": True,
            "reconciled": True,
            "archive": str(archive),
            "manifest": str(archive / "manifest.json"),
            "receipt": receipt,
            "completion_state": marker,
        }
    restored = []
    for identity in entries:
        destination = Path(identity["path"])
        if _lexists(destination):
            continue
        archived = Path(identity["archive_path"])
        _restore_cleanup_surface(destination, archived, identity)
        restored.append(str(destination))
    receipt = _receipt(
        paths,
        "obsolete-surfaces-rolled-back",
        {"archive": str(archive), "state": "PREPARED_OR_INTERRUPTED"},
        {"state": "ROLLED_BACK", "restored": restored},
    )
    _write_archive_state(
        archive,
        "ROLLED_BACK",
        {"receipt": receipt, "manifest_sha256": manifest["manifest_sha256"]},
    )
    return None


def repair_obsolete_surfaces(paths: DoctorPaths) -> dict[str, Any]:
    """Archive and remove only the exact recovery-Todo obsolete surfaces."""
    _require_root()
    declared = _declared_obsolete_surfaces(paths)
    reconciled = _reconcile_incomplete_cleanup(paths, declared)
    if reconciled is not None:
        return reconciled
    unbound = _unbound_obsolete_surfaces(paths)
    if unbound:
        raise DoctorError("obsolete cleanup refuses unbound active surfaces: " + ", ".join(unbound))
    present = [item for item in declared if _lexists(Path(item["path"]))]
    if not present:
        receipt = _receipt(paths, "obsolete-surfaces", [], {"state": "ALREADY_ABSENT"})
        return {
            "ok": True,
            "operation": "obsolete-surfaces",
            "changed": False,
            "archive": None,
            "receipt": receipt,
        }
    references = _cleanup_references(paths, present)
    processes = _cleanup_process_references(present)
    if references or processes:
        raise DoctorError("obsolete cleanup refused because active references remain: " + json.dumps({"configuration": references, "processes": processes}, sort_keys=True))

    now = datetime.now(UTC)
    archive = paths.cleanup_archive_root / now.strftime("%Y-%m-%d") / now.strftime("%Y%m%dT%H%M%S%fZ")
    archived_rows = []
    with ExitStack() as stack:
        bindings = [stack.enter_context(_bind_cleanup_surface(item)) for item in present]
        # Bind and validate the entire source set before creating any archive entry.
        for binding in bindings:
            _verify_bound_cleanup_surface(binding)
        _durable_mkdir(archive, mode=0o700, require_new=True)
        for binding in bindings:
            destination = archive / "active-root" / _archive_relative(binding.path)
            identity = _copy_cleanup_surface(binding, destination)
            archived_rows.append({**identity, "archive_path": str(destination)})
        identities = [{key: value for key, value in row.items() if key != "archive_path"} for row in archived_rows]
        manifest = {
            "schema": "tgw-doctor-obsolete-surface-archive/v1",
            "created_at": now.isoformat(),
            "operator_actor": _operator_actor(),
            "executor_actor": pwd.getpwuid(os.geteuid()).pw_name,
            "scope": "todo-1733-recovery-obsolete-active-surfaces-only",
            "entries": archived_rows,
            "boundaries": {
                "production_effects": False,
                "provider_effects": False,
                "plan_effects": False,
                "business_data_effects": False,
                "git_worktree_effects": False,
            },
        }
        manifest["manifest_sha256"] = _hash(manifest)
        manifest_path = archive / "manifest.json"
        _atomic_json(manifest_path, manifest, mode=0o400)

        # Re-verify the bound live identities after the copies and before the
        # durable PREPARED receipt. No active path is removed before both exist.
        for binding, identity in zip(bindings, identities, strict=True):
            if _stable_bound_identity(_verify_bound_cleanup_surface(binding)) != _stable_bound_identity(identity):
                raise DoctorError(f"obsolete surface changed before removal: {binding.path}")
        prepared_receipt = _receipt(
            paths,
            "obsolete-surfaces-prepared",
            identities,
            {
                "state": "PREPARED",
                "archive": str(archive),
                "manifest": str(manifest_path),
                "manifest_sha256": manifest["manifest_sha256"],
            },
        )
        prepared_state = _write_archive_state(
            archive,
            "PREPARED",
            {
                "receipt": prepared_receipt,
                "manifest_sha256": manifest["manifest_sha256"],
            },
        )
        removed: list[tuple[Path, Path, Mapping[str, Any]]] = []
        try:
            for binding, identity in zip(bindings, identities, strict=True):
                if _stable_bound_identity(_verify_bound_cleanup_surface(binding)) != _stable_bound_identity(identity):
                    raise DoctorError(f"obsolete surface changed before removal: {binding.path}")
                archived = Path(next(row["archive_path"] for row in archived_rows if row["path"] == str(binding.path)))
                _unlink_bound_surface(binding, identity)
                # Track deletion before the durability operation so an fsync
                # failure necessarily restores the already-unlinked source.
                removed.append((binding.path, archived, identity))
                _fsync_directory_fd(binding.parent_fd)
                _assert_parent_binding(binding)
            if any(_lexists(Path(identity["path"])) for identity in identities):
                raise DoctorError("an obsolete surface remains after removal")
        except Exception as exc:
            rollback_errors = []
            binding_by_path = {binding.path: binding for binding in bindings}
            for source, archived, identity in reversed(removed):
                try:
                    binding = binding_by_path[source]
                    parent_visible = True
                    try:
                        _assert_parent_binding(binding)
                    except DoctorError:
                        parent_visible = False
                    _restore_bound_cleanup_surface(binding, archived, identity)
                    if not parent_visible:
                        rollback_errors.append(f"bound parent no longer visible at {binding.path.parent}; surface restored only to its original directory inode")
                except Exception as rollback_exc:
                    rollback_errors.append(str(rollback_exc))
            rollback_receipt = _receipt(
                paths,
                "obsolete-surfaces-rolled-back",
                {"prepared_receipt": prepared_receipt},
                {"error": str(exc), "rollback_errors": rollback_errors},
            )
            if not rollback_errors:
                _write_archive_state(
                    archive,
                    "ROLLED_BACK",
                    {
                        "receipt": rollback_receipt,
                        "manifest_sha256": manifest["manifest_sha256"],
                    },
                )
            suffix = "original active view restored" if not rollback_errors else "rollback incomplete"
            raise DoctorError(f"obsolete cleanup failed; {suffix}; receipt: {rollback_receipt}") from exc

    # Once every source removal is durable, failure to record completion must
    # leave the PREPARED archive intact for deterministic next-run reconciliation.
    receipt = _receipt(
        paths,
        "obsolete-surfaces",
        {"prepared_receipt": prepared_receipt},
        {"state": "COMPLETED", "removed": identities, "archive": str(archive)},
    )
    completion_state = _write_archive_state(
        archive,
        "COMPLETED",
        {"receipt": receipt, "manifest_sha256": manifest["manifest_sha256"]},
    )
    return {
        "ok": True,
        "operation": "obsolete-surfaces",
        "changed": True,
        "archive": str(archive),
        "manifest": str(manifest_path),
        "prepared_receipt": prepared_receipt,
        "prepared_state": prepared_state,
        "receipt": receipt,
        "completion_state": completion_state,
    }


_REPAIRS: dict[str, Callable[[DoctorPaths], dict[str, Any]]] = {
    "context": repair_context,
    "context-launcher": repair_context_launcher,
    "runtime": repair_runtime,
    "database": repair_database,
    "unix-git-access": repair_unix_git_access,
    "workers": repair_workers,
    "plan-render-worker": repair_plan_render_worker,
    "obsolete-surfaces": repair_obsolete_surfaces,
}

_REPAIR_POSTCONDITIONS: dict[str, tuple[str, ...]] = {
    "context": ("context.snapshot",),
    "context-launcher": ("context.launcher",),
    "runtime": ("runtime.local-coding",),
    "database": ("database.local-coding",),
    "unix-git-access": ("access.unix-group",),
    "workers": ("services.local-coding",),
    "plan-render-worker": ("services.plan-render",),
    "obsolete-surfaces": ("cleanup.obsolete-active-surfaces",),
}


def repair(
    operation: str,
    paths: DoctorPaths = DoctorPaths(),
    *,
    desired_commit: str | None = None,
    source_root: Path | None = None,
    review_evidence: Path | None = None,
) -> dict[str, Any]:
    function = _REPAIRS.get(operation)
    if function is None:
        raise DoctorError(f"unknown repair: {operation}")
    _require_root()
    with ExitStack() as locks:
        locks.enter_context(_repair_lock(paths))
        if desired_commit is not None and operation in {
            "context",
            "context-launcher",
            "runtime",
            "database",
            "workers",
            "plan-render-worker",
        }:
            locks.enter_context(_runtime_selector_lock(paths))
        exact_root: Path | None = None
        if desired_commit is not None:
            if source_root is None:
                raise DoctorError("privileged repair requires its private exact source")
            exact_root, _tree = _validated_exact_repair_root(
                paths, desired_commit, source_root
            )
            if operation in {
                "context",
                "context-launcher",
                "database",
                "workers",
                "plan-render-worker",
            }:
                release = paths.runtime_root / "releases" / desired_commit
                _verify_release_tree(paths, desired_commit, release)
                if not _runtime_selector_identity(
                    paths, desired_commit, release
                )["exact"]:
                    raise DoctorError(
                        "privileged repair runtime selector differs from the exact commit"
                    )
        started_receipt = _receipt(
            paths,
            operation + "-started",
            {"operation": operation},
            {"state": "STARTED"},
        )
        try:
            if operation in {"context", "context-launcher"}:
                context_arguments: dict[str, Any] = {
                    "desired_commit": desired_commit,
                    "source_root": exact_root,
                }
                if operation == "context":
                    context_arguments["review_evidence"] = review_evidence
                elif review_evidence is not None:
                    raise DoctorError(
                        "review evidence is valid only for Context publication"
                    )
                result = function(paths, **context_arguments)
            elif operation in {
                "runtime",
                "database",
                "workers",
                "plan-render-worker",
            }:
                result = function(paths, desired_commit=desired_commit)
            else:
                result = function(paths)
            result = {**result, "started_receipt": started_receipt}
        except _ReceiptPublicationAmbiguous as exc:
            # This is neither terminal success nor terminal failure: exact
            # success bytes may already be durable, so a generic failure
            # receipt would contradict them. Preserve the typed state and let
            # the next locked replay reconcile it.
            detail = (
                f"{exc}; started receipt: {started_receipt}; "
                f"possible success receipt: {exc.path}; state preserved for replay"
            )
            exc.args = (detail, *exc.args[1:])
            exc.add_note("generic failure receipt intentionally not emitted")
            raise
        except Exception as exc:
            try:
                receipt = _receipt(
                    paths,
                    operation + "-failed",
                    {"operation": operation},
                    {"ok": False, "error": str(exc)},
                )
            except Exception as receipt_exc:
                receipt = f"unavailable ({receipt_exc})"
            raise DoctorError(f"{exc}; started receipt: {started_receipt}; failure receipt: {receipt}") from exc
    diagnosis = diagnose(paths)
    context_client_check = next(
        (
            item
            for item in diagnosis.get("checks", [])
            if item.get("id") == "context.clients"
        ),
        None,
    )
    context_client_inventory_state = (
        context_client_check.get("state")
        if isinstance(context_client_check, Mapping)
        else "UNKNOWN"
    )
    checks_by_id = {
        str(item.get("id")): item
        for item in diagnosis.get("checks", [])
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    target_postconditions = {
        check_id: (
            checks_by_id[check_id].get("state")
            if check_id in checks_by_id
            else "UNKNOWN"
        )
        for check_id in _REPAIR_POSTCONDITIONS[operation]
    }
    # Only the selected repair's own postconditions are completion-relevant.
    # Every unrelated diagnostic—including best-effort /proc Context client
    # inventory—remains advisory and cannot become an implicit global gate.
    verification_complete = result.get("ok") is True and all(
        state == "PASS" for state in target_postconditions.values()
    )
    return {
        "schema": "tgw-local-doctor-repair/v1",
        "ok": verification_complete,
        "operation": operation,
        "mutation_applied": result.get("changed") is True,
        "verification_complete": verification_complete,
        "context_client_inventory_state": context_client_inventory_state,
        "client_restart_evidence_ok": (
            result.get("client_restart_evidence_ok")
            if operation in {"context", "context-launcher"}
            else None
        ),
        "context_client_inventory_non_gating": True,
        "target_postconditions": target_postconditions,
        "results": [result],
        "diagnosis": diagnosis,
    }


def _format(report: Mapping[str, Any]) -> str:
    lines = [
        f"TGW doctor: {report['state']} "
        f"({report['counts'].get('PASS', 0)} pass, "
        f"{report['counts'].get('WARN', 0)} warn, "
        f"{report['counts'].get('RESTART_REQUIRED', 0)} restart, "
        f"{report['counts'].get('UNKNOWN', 0)} unknown, "
        f"{report['counts'].get('FAIL', 0)} fail)"
    ]
    for item in report["checks"]:
        lines.append(f"{item['state']:<16} {item['id']}: {item['detail']}")
        if item.get("operator_action"):
            lines.append(f"  action: {item['operator_action']}")
    return "\n".join(lines)


def _compact_diagnosis(report: Mapping[str, Any]) -> dict[str, Any]:
    """Keep operator JSON bounded; evidence remains available with --full."""
    return {
        key: value
        for key, value in report.items()
        if key not in {"checks"}
    } | {
        "checks": [
            {
                key: value
                for key, value in item.items()
                if key in {"id", "state", "detail", "repairable", "operator_action"}
            }
            for item in report.get("checks", [])
        ]
    }


def _compact_inventory(report: Mapping[str, Any]) -> dict[str, Any]:
    worktrees = report.get("worktrees", [])
    safe = [
        item
        for item in worktrees
        if not item.get("canonical")
        and item.get("exists") is True
        and item.get("dirty") is False
        and item.get("unique_commits") == 0
        and item.get("merged_into_canonical") is True
        and not item.get("errors")
    ]
    attention = [item for item in worktrees if item.get("preservation_required")]
    return {
        "schema": "tgw-local-doctor-inventory-summary/v1",
        "ok": report.get("ok"),
        "host": report.get("host"),
        "actor": report.get("actor"),
        "observed_at": report.get("observed_at"),
        "canonical_source": report.get("canonical_source"),
        "counts": report.get("counts"),
        "safe_cleanup_candidates": {
            "count": len(safe),
            "sample": [item.get("path") for item in safe[:20]],
        },
        "preservation_required": {
            "count": len(attention),
            "sample": [item.get("path") for item in attention[:20]],
        },
        "cleanup_boundary": report.get("cleanup_boundary"),
        "full_inventory": "tgw doctor inventory --full",
    }


def repair_coding_bootstrap(
    commit: str, paths: DoctorPaths = DoctorPaths()
) -> dict[str, Any]:
    """Install one exact local coding runtime without consulting Context.

    This is an explicit operator repair for the otherwise circular first
    transition. Root installs fixed configuration and systemd definitions;
    all repository reading, release creation, and selection run as the
    ordinary ``db:tgw-coders`` materializer.
    """

    _require_root()
    if _COMMIT.fullmatch(commit) is None:
        raise DoctorError("coding bootstrap commit is invalid")
    head, tree, status = _source_identity(paths)
    if status or head != commit:
        raise DoctorError(
            "coding bootstrap requires the exact clean canonical source commit"
        )
    source_config = paths.repository / "config/tgw-coding-local.json"
    if source_config.is_symlink() or not source_config.is_file():
        raise DoctorError("candidate coding configuration is absent")
    raw_config = source_config.read_bytes()
    config_entry = _git(
        paths.repository,
        "ls-tree",
        commit,
        "--",
        "config/tgw-coding-local.json",
    ).split()
    if (
        len(config_entry) != 4
        or config_entry[:2] != ["100644", "blob"]
        or _git_blob_oid(raw_config) != config_entry[2]
    ):
        raise DoctorError("candidate coding configuration differs from exact Git bytes")
    try:
        parsed_config = json.loads(raw_config)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DoctorError("candidate coding configuration is invalid") from exc
    if (
        not isinstance(parsed_config, Mapping)
        or parsed_config.get("schema") != "tgw-local-coding-workflow/v1"
    ):
        raise DoctorError("candidate coding configuration schema is invalid")
    coding = parsed_config.get("coding")
    exact_paths = {
        "repository_root": paths.repository,
        "worktree_root": paths.worktrees,
        "runtime_root": paths.runtime_root,
    }
    if not isinstance(coding, Mapping) or any(
        coding.get(key) != str(expected)
        for key, expected in exact_paths.items()
    ):
        raise DoctorError("candidate coding configuration paths differ from this host")
    lowered = raw_config.decode("utf-8").lower()
    forbidden = [item for item in _FORBIDDEN_CODING_DEPENDENCIES if item in lowered]
    if forbidden:
        raise DoctorError(
            "candidate coding configuration contains forbidden dependencies: "
            + ", ".join(forbidden)
        )
    group = grp.getgrnam(_CODING_RUNTIME_GROUP)
    db = pwd.getpwnam("db")
    if db.pw_uid == 0 or db.pw_name != "db":
        raise DoctorError("ordinary db materializer account is unavailable")
    final_head, final_tree, final_status = _source_identity(paths)
    if final_status or (final_head, final_tree) != (commit, tree):
        raise DoctorError("canonical source changed during coding bootstrap preflight")
    paths.coding_config.parent.mkdir(parents=True, exist_ok=True)
    if paths.coding_config.is_symlink() or (
        paths.coding_config.exists() and not paths.coding_config.is_file()
    ):
        raise DoctorError("coding configuration destination is unsafe")
    _atomic_bytes(
        paths.coding_config,
        raw_config,
        mode=0o640,
        uid=0,
        gid=group.gr_gid,
    )
    journal: list[dict[str, Any]] = []
    try:
        support_roots = _provision_coding_support_roots(
            paths, group.gr_gid, journal
        )
    except Exception:
        rollback_errors = _close_mutation_journal(journal, rollback=True)
        if rollback_errors:
            raise DoctorError(
                "coding bootstrap support-root rollback incomplete: "
                + "; ".join(rollback_errors)
            )
        raise
    commit_errors = _close_mutation_journal(journal, rollback=False)
    if commit_errors:
        raise DoctorError(
            "coding bootstrap support-root transaction incomplete: "
            + "; ".join(commit_errors)
        )
    command = [
        "/usr/sbin/runuser",
        "-u",
        "db",
        "-g",
        _CODING_RUNTIME_GROUP,
        "--",
        "/usr/bin/env",
        "HOME=/home/db",
        "PYTHONDONTWRITEBYTECODE=1",
        f"PYTHONPATH={paths.repository / 'src'}",
        "/opt/TGW/.venvs/controller/bin/python3",
        "-m",
        "tgw.development.coding_root_effect",
        "--config",
        str(paths.coding_config),
        "--bootstrap-commit",
        commit,
        "--bootstrap-tree",
        tree,
    ]
    materialized = _run(command, timeout=300)
    if materialized.returncode:
        raise DoctorError(
            materialized.stderr.strip()
            or "ordinary db materializer failed during coding bootstrap"
        )
    try:
        materialization = json.loads(materialized.stdout)
    except json.JSONDecodeError as exc:
        raise DoctorError("coding bootstrap materializer returned invalid evidence") from exc
    if not isinstance(materialization, Mapping):
        raise DoctorError("coding bootstrap materializer returned invalid evidence")
    materialization = _validated_bootstrap_materialization(
        materialization, commit=commit, tree=tree
    )
    release = paths.runtime_root / "releases" / commit
    # The ordinary db:tgw-coders materializer is deliberately unable to
    # create root-owned bytes.  Root first validates the exact Git-bound
    # immutable release, then performs the narrow ownership promotion needed
    # by the cold Context launcher and validates the same tree again.
    _verify_release_tree(paths, commit, release)
    _promote_bootstrap_release_ownership(
        release,
        uid=paths.context_install_uid,
        gid=paths.context_install_gid,
    )
    _verify_release_tree(paths, commit, release)
    current = paths.runtime_root / "current"
    if not current.is_symlink() or current.resolve(strict=True) != release.resolve():
        raise DoctorError("coding bootstrap did not select the exact release")
    context_input = paths.context_task.parent
    _repair_managed_directory(
        context_input,
        uid=db.pw_uid,
        gid=group.gr_gid,
        mode=0o2750,
    )
    context_inputs: list[str] = []
    for context_path in (paths.context_task, paths.context_cursor):
        observed = context_path.stat(follow_symlinks=False)
        if (
            context_path.is_symlink()
            or not stat.S_ISREG(observed.st_mode)
            or observed.st_nlink != 1
        ):
            raise DoctorError(f"Context input is unsafe: {context_path}")
        if (
            observed.st_uid != db.pw_uid
            or observed.st_gid != group.gr_gid
            or stat.S_IMODE(observed.st_mode) != 0o440
        ):
            os.chown(context_path, db.pw_uid, group.gr_gid)
            os.chmod(context_path, 0o440)
            context_inputs.append(str(context_path))
    launcher_changes: list[str] = []
    for destination, target in _launcher_links(paths).items():
        source = release / "bin" / target.name
        if not source.is_file():
            raise DoctorError(f"candidate release lacks launcher: {source.name}")
        if destination.exists() and not destination.is_symlink():
            raise DoctorError(
                f"coding launcher requires manual preservation before replacement: {destination}"
            )
        if not destination.is_symlink() or os.readlink(destination) != str(target):
            _replace_link(destination, target)
            launcher_changes.append(str(destination))
    fixed_launchers = (
        ("bin/tgw-operator", paths.operator_cli),
        ("bin/tgw-coding-bootstrap", paths.coding_bootstrap),
    )
    for relative, destination in fixed_launchers:
        try:
            launcher_mode, launcher_bytes = read_exact_tree_file(
                paths.repository,
                commit=commit,
                tree=tree,
                path=relative,
            )
        except ValueError as exc:
            raise DoctorError(f"candidate lacks fixed launcher {relative}") from exc
        if launcher_mode != 0o755:
            raise DoctorError(f"candidate fixed launcher mode differs: {relative}")
        source = release / relative
        if not _fixed_root_launcher_identity(paths, destination, source)["exact"]:
            _atomic_bytes(
                destination,
                launcher_bytes,
                mode=0o555,
                uid=paths.context_install_uid,
                gid=paths.context_install_gid,
            )
            launcher_changes.append(str(destination))
        if not _fixed_root_launcher_identity(paths, destination, source)["exact"]:
            raise DoctorError(f"fixed launcher installation is incomplete: {relative}")
    plan_render = repair_plan_render_worker(paths, desired_commit=commit)
    workers = repair_workers(paths, desired_commit=commit)
    if paths.coding_config.read_bytes() != raw_config:
        raise DoctorError("installed coding configuration changed during bootstrap")
    units = check_units(paths, desired_commit=commit)
    rendered = check_plan_render_worker(paths, desired_commit=commit)
    if units["state"] != "PASS" or rendered["state"] != "PASS":
        raise DoctorError("coding bootstrap services did not reach exact live state")
    result = {
        "schema": "tgw-local-coding-bootstrap/v1",
        "ok": True,
        "context_required": False,
        "review_authority": False,
        "materializer": {"actor": "db", "group": _CODING_RUNTIME_GROUP},
        "commit": commit,
        "tree": tree,
        "configuration_sha256": _file_hash(paths.coding_config),
        "support_roots_changed": support_roots,
        "launcher_changes": launcher_changes,
        "context_inputs_migrated": context_inputs,
        "materialization": materialization,
        "plan_render": plan_render,
        "workers": workers,
        "unit_check": units,
        "plan_render_check": rendered,
    }
    return {**result, "receipt": _receipt(paths, "coding-bootstrap", {}, result)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tgw doctor")
    parser.add_argument("--json", action="store_true", dest="json_output")
    sub = parser.add_subparsers(dest="operation")
    check_parser = sub.add_parser("check", help="run read-only diagnosis (default)")
    check_parser.add_argument("--full", action="store_true", help="include complete evidence")
    inventory_parser = sub.add_parser("inventory", help="inventory linked and active-path remnants read-only")
    inventory_parser.add_argument("--full", action="store_true", help="include every discovered row")
    census_parser = sub.add_parser("condition-policy-census", help="census cached condition policies read-only")
    census_parser.add_argument("--cache", required=True, type=Path)
    resume_parser = sub.add_parser("coding-resume", help="resume one exact local RESUMABLE_PARTIAL Todo")
    resume_parser.add_argument("todo_id", type=int)
    reconcile_parser = sub.add_parser("coding-reconcile", help="reconcile one older-runner closed implementation receipt")
    reconcile_parser.add_argument("todo_id", type=int)
    bootstrap_parser = sub.add_parser(
        "coding-bootstrap",
        help=(
            "install one exact local coding runtime without Context; initial cutover "
            "uses the root-owned /usr/local/sbin/tgw-coding-bootstrap"
        ),
    )
    bootstrap_parser.add_argument("--commit", required=True)
    repair_parser = sub.add_parser("repair", help="restore an exact declared local state")
    repair_parser.add_argument("target", choices=[*_REPAIRS])
    repair_parser.add_argument("--commit", required=True)
    repair_parser.add_argument(
        "--review-evidence",
        type=Path,
        help=(
            "bind exact non-authoritative diagnostic review bytes during an "
            "explicit Context source transition"
        ),
    )
    repair_parser.add_argument("--json", action="store_true", dest="repair_json_output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.operation == "repair":
            result = repair(
                args.target,
                desired_commit=args.commit,
                source_root=Path(__file__).resolve().parents[2],
                review_evidence=args.review_evidence,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            # Each repair is bounded to its selected target.  The complete
            # diagnosis remains visible evidence, but an unrelated failing
            # check must not turn Context (or any other subsystem) into a
            # global repair gate.  Context repairs separately fail closed when
            # their own client verification is incomplete.
            return 0 if result.get("ok") else 2
        if args.operation == "inventory":
            result = inventory()
            if not args.full:
                result = _compact_inventory(result)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.operation == "condition-policy-census":
            print(json.dumps(condition_policy_census(args.cache), indent=2, sort_keys=True))
            return 0
        if args.operation == "coding-resume":
            from tgw.coding_cli import resume

            result = resume(args.todo_id)
            print(json.dumps(result, indent=2, sort_keys=True, default=str))
            return 0 if result.get("ok", True) else 1
        if args.operation == "coding-reconcile":
            result = reconcile_implementation_receipt(args.todo_id)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.operation == "coding-bootstrap":
            result = repair_coding_bootstrap(args.commit)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        result = diagnose()
        if args.json_output:
            if not getattr(args, "full", False):
                result = _compact_diagnosis(result)
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(_format(result))
        return int(result["exit_code"])
    except DoctorError as exc:
        print(
            json.dumps(
                {
                    "schema": "tgw-local-doctor-error/v1",
                    "ok": False,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


def _coding_support_roots(paths: DoctorPaths, group_gid: int) -> dict[str, dict[str, Any]]:
    coding = _coding_config(paths).get("coding", {})
    result: dict[str, dict[str, Any]] = {}
    worktree_device = paths.worktrees.stat(follow_symlinks=False).st_dev
    configured = [coding.get(key) for key in _CODING_SUPPORT_ROOT_KEYS]
    invalid_configuration = any(not isinstance(raw, str) or not raw.strip() or not Path(raw).is_absolute() for raw in configured)
    duplicate = not invalid_configuration and len({str(Path(raw)) for raw in configured}) != len(_CODING_SUPPORT_ROOT_KEYS)
    for key, raw in zip(_CODING_SUPPORT_ROOT_KEYS, configured, strict=True):
        if invalid_configuration or duplicate:
            result[key] = {"path": str(raw or ""), "exact": False, "reason": "all distinct non-empty absolute support roots are required"}
            continue
        path = Path(str(raw))
        exact = False
        reason = None
        try:
            observed = path.lstat()
            resolved = path.resolve(strict=True)
            owner = pwd.getpwuid(observed.st_uid)
            group = grp.getgrgid(group_gid)
            db_managed = key == "root_effect_root"
            db_uid = (
                pwd.getpwnam("db").pw_uid
                if paths.coding_root_effect_uid is None
                else paths.coding_root_effect_uid
            )
            owner_ok = (
                observed.st_uid == db_uid
                if db_managed
                else observed.st_uid == 0
                or owner.pw_gid == group_gid
                or owner.pw_name in group.gr_mem
            )
            expected_mode = 0o2750 if key == "root_effect_root" else 0o2770
            exact = (path.is_absolute() and resolved == path and not path.is_symlink()
                     and stat.S_ISDIR(observed.st_mode) and observed.st_dev == worktree_device
                     and observed.st_gid == group_gid and owner_ok
                     and stat.S_IMODE(observed.st_mode) == expected_mode
                     and not resolved.is_relative_to(paths.worktrees.resolve(strict=True)))
            if not exact:
                reason = "owner/group/mode/type/symlink/filesystem boundary differs"
        except (KeyError, OSError, RuntimeError, ValueError) as exc:
            reason = str(exc)
        result[key] = {"path": str(path), "exact": exact, "reason": reason}
    return result


def _provision_support_root(
    path: Path, *, group_gid: int, worktree_device: int,
    journal: list[dict[str, Any]],
    target_uid: int | None = None,
    target_mode: int = 0o2770,
) -> bool:
    """Provision one absolute root using only no-follow directory descriptors."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    components = path.relative_to(path.anchor).parts
    parent = os.open(path.anchor, flags)
    target_created = False
    try:
        for index, component in enumerate(components):
            if component in {"", ".", ".."}:
                raise DoctorError(f"coding support root has unsafe component: {path}")
            final = index == len(components) - 1
            try:
                child = os.open(component, flags, dir_fd=parent)
            except FileNotFoundError:
                # Build under an unguessable private name.  The requested final
                # component is never opened or mutated until no-replace publish
                # has made our already pinned inode visible there.
                rollback_parent = os.dup(parent)
                staging = f".{component}.tgw-stage-{secrets.token_hex(16)}"
                try:
                    os.mkdir(staging, 0o700, dir_fd=parent)
                except Exception:
                    os.close(rollback_parent)
                    raise
                creation = {
                    "kind": "created_directory", "parent": rollback_parent,
                    "descriptor": None, "staging_name": staging,
                    "published_name": component, "phase": "staging",
                }
                journal.append(creation)
                created = os.stat(staging, dir_fd=parent, follow_symlinks=False)
                _support_root_checkpoint("creation-to-bind")
                try:
                    child = os.open(staging, flags, dir_fd=parent)
                except Exception:
                    raise
                try:
                    bound = os.fstat(child)
                    if not stat.S_ISDIR(created.st_mode) or (
                        created.st_dev, created.st_ino
                    ) != (bound.st_dev, bound.st_ino):
                        raise DoctorError("staged support directory changed before bind")
                    creation["descriptor"] = os.dup(child)
                    os.fchown(
                        child,
                        target_uid if final and target_uid is not None else -1,
                        group_gid,
                    )
                    os.fchmod(child, target_mode if final else 0o2770)
                    os.fsync(child)
                    _support_root_checkpoint("bind-to-publish")
                    _rename_noreplace_at(parent, staging, parent, component)
                    _support_root_checkpoint("rename-to-phase")
                    creation["phase"] = "published"
                    _support_root_checkpoint("after-publish")
                    os.fsync(parent)
                except Exception:
                    os.close(child)
                    raise
                target_created |= final
            os.close(parent)
            parent = child
        observed = os.fstat(parent)
        if not stat.S_ISDIR(observed.st_mode) or observed.st_dev != worktree_device:
            raise DoctorError(f"coding support root crosses the worktree filesystem: {path}")
        _verify_bound_directory(path, parent)
        if not target_created:
            journal.append({"kind": "metadata", "descriptor": os.dup(parent), "before": observed})
        os.fchown(parent, -1 if target_uid is None else target_uid, group_gid)
        os.fchmod(parent, target_mode)
        os.fsync(parent)
        _verify_bound_directory(path, parent)
        return target_created
    finally:
        os.close(parent)


def _support_root_checkpoint(_phase: str) -> None:
    """Deterministic test seam at otherwise non-fallible transaction boundaries."""


def _rename_noreplace_at(source_fd: int, source: str, destination_fd: int, destination: str) -> None:
    """Atomically publish one descriptor-relative name without replacement."""
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise DoctorError("renameat2 is unavailable; atomic directory publication is unsafe")
    if renameat2(source_fd, os.fsencode(source), destination_fd, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise DoctorError("support directory appeared before no-replace publication")
        raise DoctorError("atomic support directory publication failed") from OSError(
            error, os.strerror(error), destination,
        )


def _provision_coding_support_roots(
    paths: DoctorPaths, group_gid: int, journal: list[dict[str, Any]] | None = None,
) -> list[str]:
    coding = _coding_config(paths).get("coding", {})
    changed: list[str] = []
    configured = [coding.get(key) for key in _CODING_SUPPORT_ROOT_KEYS]
    if (any(not isinstance(raw, str) or not raw.strip() or not Path(raw).is_absolute() for raw in configured)
            or len({str(Path(raw)) for raw in configured}) != len(_CODING_SUPPORT_ROOT_KEYS)):
        raise DoctorError("all distinct non-empty absolute coding support roots are required")
    if journal is None:
        raise DoctorError("coding support-root provisioning requires a rollback journal")
    worktrees = paths.worktrees.resolve(strict=True)
    worktree_device = paths.worktrees.stat(follow_symlinks=False).st_dev
    db_uid = (
        pwd.getpwnam("db").pw_uid
        if paths.coding_root_effect_uid is None
        else paths.coding_root_effect_uid
    )
    for key, raw in zip(_CODING_SUPPORT_ROOT_KEYS, configured, strict=True):
        path = Path(str(raw))
        if not path.is_absolute() or path == Path(path.anchor) or path.is_relative_to(worktrees):
            raise DoctorError(f"coding {key} is not an absolute outside-worktree path")
        try:
            created = _provision_support_root(
                path, group_gid=group_gid, worktree_device=worktree_device, journal=journal,
                target_uid=db_uid if key == "root_effect_root" else None,
                target_mode=0o2750 if key == "root_effect_root" else 0o2770,
            )
        except OSError as exc:
            raise DoctorError(f"coding {key} cannot be provisioned safely: {exc}") from exc
        if created:
            changed.append(str(path))
    runtime_created = _provision_support_root(
        paths.runtime_root,
        group_gid=group_gid,
        worktree_device=worktree_device,
        journal=journal,
        target_uid=db_uid,
        target_mode=0o2750,
    )
    if runtime_created:
        changed.append(str(paths.runtime_root))
    for child_name in ("releases", "operations", "receipts", "refusals"):
        child = paths.runtime_root / child_name
        if _repair_managed_directory(
            child,
            uid=db_uid,
            gid=group_gid,
            mode=0o2750,
        ):
            changed.append(str(child))
    checked = _coding_support_roots(paths, group_gid)
    if not all(row["exact"] for row in checked.values()):
        raise DoctorError("protected coding support roots remain incomplete after repair")
    return changed


if __name__ == "__main__":
    raise SystemExit(main())
