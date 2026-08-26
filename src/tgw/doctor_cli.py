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
import json
import os
import pwd
import re
import secrets
import shlex
import socket
import stat
import subprocess
import sys
import tempfile
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

import psycopg2
import psycopg2.extras

_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_LOOSE_OBJECT_DIRECTORY = re.compile(r"[0-9a-f]{2}\Z")
_LOOSE_OBJECT_NAME = re.compile(r"[0-9a-f]{38}\Z")
_STATES = {"PASS", "WARN", "FAIL", "UNKNOWN", "RESTART_REQUIRED"}
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
    "tgw-controller-verify-worker.service",
    "tgw-coding-local-foreman.timer",
)
_CODING_UNITS = (*_ACTIVE_CODING_UNITS, "tgw-coding-local-foreman.service")
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


@dataclass(frozen=True)
class DoctorPaths:
    repository: Path = Path("/opt/TGW/tgw-lib/src/trader-grims-warehouse")
    worktrees: Path = Path("/opt/TGW/var/worktrees")
    coding_config: Path = Path("/opt/TGW/tgw-lib/config/tgw-coding-local.json")
    plan_render_config: Path = Path("/opt/TGW/tgw-lib/config/tgw-plan-render-local.json")
    plan_render_root: Path = Path("/opt/TGW/var/plan-render")
    plan_render_log_root: Path = Path("/opt/TGW/var/plan-render/log")
    runtime_root: Path = Path("/opt/TGW/tgw-lib/coding-runtime")
    local_bin: Path = Path("/opt/TGW/tgw-lib/bin")
    operator_cli: Path = Path("/usr/local/bin/tgw")
    context_snapshot: Path = Path("/opt/TGW/tgw-lib/config/tgw-context-current.json")
    context_task: Path = Path("/opt/TGW/tgw-lib/context-input/current-task.json")
    context_cursor: Path = Path("/opt/TGW/tgw-lib/context-input/plan-cycle-cursor.json")
    context_launcher: Path = Path("/opt/TGW/tgw-lib/bin/tgw-context-mcp")
    context_publisher: Path = Path("/opt/TGW/tgw-lib/bin/tgw-context-publish")
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
    """Capture one path without following it and reject a raced read."""
    try:
        before = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        if path.is_symlink():
            raise DoctorError(f"surface disappeared during inspection: {path}")
        return {"kind": "missing"}
    common = {
        "uid": before.st_uid,
        "gid": before.st_gid,
        "mode": stat.S_IMODE(before.st_mode),
        "device": before.st_dev,
        "inode": before.st_ino,
        "size": before.st_size,
        "mtime_ns": before.st_mtime_ns,
        "ctime_ns": before.st_ctime_ns,
    }
    if stat.S_ISLNK(before.st_mode):
        target = os.readlink(path)
        after = path.stat(follow_symlinks=False)
        result = {"kind": "symlink", "target": target, **common}
    elif stat.S_ISREG(before.st_mode):
        raw = path.read_bytes()
        after = path.stat(follow_symlinks=False)
        result = {
            "kind": "file",
            "raw": raw,
            "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
            **common,
        }
    else:
        raise DoctorError(f"surface has an unsupported type: {path}")
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
        stat.S_IMODE(after.st_mode),
        after.st_uid,
        after.st_gid,
    )
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
        stat.S_IMODE(before.st_mode),
        before.st_uid,
        before.st_gid,
    )
    if after_identity != before_identity:
        raise DoctorError(f"surface changed during inspection: {path}")
    return result


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
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        env={**os.environ, **env} if env is not None else None,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _git(repository: Path, *args: str) -> str:
    result = _run(
        ["git", "-c", f"safe.directory={repository.resolve()}", *args],
        cwd=repository,
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
    status = _git(paths.repository, "status", "--short")
    if _COMMIT.fullmatch(head) is None or _COMMIT.fullmatch(tree) is None:
        raise DoctorError("canonical source has an invalid Git identity")
    return head, tree, status


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


def _validate_snapshot(value: Mapping[str, Any]) -> None:
    if value.get("schema") != "tgw-current-context-snapshot/v1":
        raise DoctorError("published context snapshot schema is invalid")
    claimed = value.get("snapshot_sha256")
    body = dict(value)
    body.pop("snapshot_sha256", None)
    if not isinstance(claimed, str) or claimed != _hash(body):
        raise DoctorError("published context snapshot hash differs")
    task = value.get("task")
    cursor = value.get("cursor")
    if not isinstance(task, Mapping) or not isinstance(cursor, Mapping):
        raise DoctorError("published context task/cursor is missing")
    development = task.get("implementation", {}).get("development_source", {})
    if (
        value.get("source_commit") != development.get("commit")
        or value.get("source_commit") != cursor.get("source_commit")
        or value.get("source_tree") != cursor.get("source_tree")
        or value.get("plan_commit") != task.get("plan", {}).get("approved_commit")
        or value.get("plan_commit") != cursor.get("plan_commit")
    ):
        raise DoctorError("published task, cursor, Plan, and source identities diverge")


def check_context_snapshot(paths: DoctorPaths) -> dict[str, Any]:
    repair = "sudo -n tgw doctor repair context"
    try:
        snapshot = _json(paths.context_snapshot)
        task = _json(paths.context_task)
        cursor = _json(paths.context_cursor)
        _require_trusted_root_program(paths.context_launcher, paths.trusted_release_owners)
        launcher_text = paths.context_launcher.read_text(encoding="utf-8")
        if str(paths.context_snapshot) not in launcher_text or str(paths.context_cursor) in launcher_text:
            raise DoctorError("Context launcher does not preserve the single-snapshot runtime boundary")
        _validate_snapshot(snapshot)
        if snapshot.get("task") != task or snapshot.get("cursor") != cursor:
            raise DoctorError("published context does not contain the current input records")
        head, tree, _status = _source_identity(paths)
        if snapshot.get("source_commit") != head or snapshot.get("source_tree") != tree:
            raise DoctorError("published context is stale relative to canonical source")
        return _check(
            "context.snapshot",
            "PASS",
            f"atomic context is current at {head[:12]}",
            evidence={
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


def _selected_context_artifacts(paths: DoctorPaths) -> dict[str, Any]:
    desired, release, _task = _desired_runtime(paths)
    release_tree = _verify_release_tree(paths, desired, release)
    for runtime_path in (release, *release.rglob("*")):
        observed = runtime_path.stat(follow_symlinks=False)
        relative = str(runtime_path.relative_to(release)) or "."
        if runtime_path.is_symlink() or observed.st_uid != paths.context_install_uid or observed.st_gid != paths.context_install_gid or observed.st_mode & 0o022:
            raise DoctorError("selected Context release is not root:root immutable: " + relative)
    launcher = release / "scripts/tgw_context_debian_stdio.py"
    runtime_source = release / "src"
    modules = {
        name: runtime_source / f"tgw/{name}.py"
        for name in (
            "context_mcp_server",
            "current_context_snapshot",
            "local_context_runtime",
        )
    }
    required = {"launcher": launcher, **modules}
    for name, source in required.items():
        if not source.is_file() or source.is_symlink():
            raise DoctorError(f"selected immutable runtime has no exact Context {name}")
        observed = source.stat(follow_symlinks=False)
        if observed.st_uid not in paths.trusted_release_owners or observed.st_mode & 0o022:
            raise DoctorError(f"selected immutable runtime Context {name} is not immutable")
    return {
        "commit": desired,
        "release": release,
        "release_tree": release_tree,
        "launcher": launcher,
        "runtime_source": runtime_source,
        "modules": modules,
        "hashes": {name: _file_hash(source) for name, source in required.items()},
    }


def _selected_context_launcher(paths: DoctorPaths) -> tuple[str, Path]:
    selected = _selected_context_artifacts(paths)
    return selected["commit"], selected["launcher"]


def check_context_launcher(paths: DoctorPaths) -> dict[str, Any]:
    repair = "sudo -n tgw doctor repair context-launcher"
    try:
        selected = _selected_context_artifacts(paths)
        desired = selected["commit"]
        source = selected["launcher"]
        installed = _surface_snapshot(paths.context_launcher)
        if installed["kind"] != "file":
            raise DoctorError("installed Context launcher is missing or indirect")
        if installed["sha256"] != selected["hashes"]["launcher"]:
            raise DoctorError("installed Context launcher differs from selected runtime")
        if installed["uid"] != paths.context_install_uid or installed["gid"] != paths.context_install_gid or installed["mode"] != paths.context_launcher_mode:
            raise DoctorError("installed Context launcher owner or mode differs from root:root 0555")
        return _check(
            "context.launcher",
            "PASS",
            f"Context launcher is exact at runtime {desired[:12]}",
            evidence={
                "runtime_commit": desired,
                "source": str(source),
                "installed": str(paths.context_launcher),
                "sha256": _file_hash(source),
                "installed_uid": installed["uid"],
                "installed_gid": installed["gid"],
                "installed_mode": oct(installed["mode"]),
                "runtime_source": str(selected["runtime_source"]),
                "runtime_modules": {
                    name: {
                        "path": str(path),
                        "sha256": selected["hashes"][name],
                    }
                    for name, path in selected["modules"].items()
                },
                "release_tree": selected["release_tree"],
            },
        )
    except Exception as exc:
        return _failed("context.launcher", exc, repair=repair)


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


def _context_processes(paths: DoctorPaths) -> list[dict[str, Any]]:
    boot = _boot_time()
    ticks = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
    launcher_mtime = paths.context_launcher.stat().st_mtime
    processes: list[dict[str, Any]] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = [value.decode(errors="replace") for value in (entry / "cmdline").read_bytes().split(b"\0") if value]
            if not _is_context_process(argv):
                continue
            raw = (entry / "stat").read_text(encoding="utf-8")
            end = raw.rfind(")")
            fields = raw[end + 2 :].split()
            started_at = boot + float(fields[19]) / ticks
            stat_result = entry.stat()
            processes.append(
                {
                    "pid": int(entry.name),
                    "user": pwd.getpwuid(stat_result.st_uid).pw_name,
                    "started_at": datetime.fromtimestamp(started_at, UTC).isoformat(),
                    "predates_launcher": started_at < launcher_mtime,
                    "command": " ".join(argv),
                }
            )
        except (OSError, KeyError, ValueError):
            continue
    return sorted(processes, key=lambda item: item["pid"])


def check_context_processes(paths: DoctorPaths) -> dict[str, Any]:
    try:
        processes = _context_processes(paths)
        stale = [item for item in processes if item["predates_launcher"]]
        if stale:
            return _check(
                "context.clients",
                "RESTART_REQUIRED",
                f"{len(stale)} Context MCP process(es) predate the installed launcher",
                evidence={"processes": processes, "stale_pids": [item["pid"] for item in stale]},
                repair="restart only the affected parent harness session; do not restart other clients",
            )
        if not processes:
            return _check(
                "context.clients",
                "WARN",
                "no live Context MCP process is visible; cold-start verification is required",
                evidence={"processes": []},
            )
        return _check(
            "context.clients",
            "PASS",
            f"{len(processes)} visible Context MCP process(es) use the installed generation",
            evidence={"processes": processes},
        )
    except Exception as exc:
        return _check("context.clients", "UNKNOWN", str(exc))


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


def _actor_path_access(actor: str, path: Path) -> bool:
    current = pwd.getpwuid(os.geteuid()).pw_name
    if actor == current:
        return os.access(path, os.R_OK | os.W_OK | os.X_OK)
    return all(_run(["sudo", "-n", "-u", actor, "/usr/bin/test", flag, str(path)]).returncode == 0 for flag in ("-r", "-w", "-x"))


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
        actor = _operator_actor()
        group = grp.getgrnam("tgw-coders")
        git_common = paths.repository / ".git"
        actors = {}
        for name in sorted({actor, "codex", "db"}):
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
        shared_trees = _inspect_shared_git_trees(paths, group.gr_gid)
        exact = all(row["exact"] for row in actors.values()) and all(row["exact"] for row in directories.values()) and shared_trees["exact"]
        return _check(
            "access.unix-group",
            "PASS" if exact else "FAIL",
            "ordinary Unix tgw-coders access is exact for operator and workers" if exact else "ordinary Unix tgw-coders access or shared Git directories differ",
            evidence={
                "actor": actor,
                "group": "tgw-coders",
                "group_gid": group.gr_gid,
                "members": sorted(group.gr_mem),
                "actors": actors,
                "directories": directories,
                "shared_trees": shared_trees,
            },
            repair=None if exact else "sudo -n tgw doctor repair unix-git-access",
        )
    except Exception as exc:
        return _failed(
            "access.unix-group",
            exc,
            repair="sudo -n tgw doctor repair unix-git-access",
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
    actor = _operator_actor()
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
                ["git", "-c", f"safe.directory={location}", "status", "--short"],
                cwd=location,
            )
            if result.returncode:
                errors.append(result.stderr.strip() or "cannot inspect worktree status")
            else:
                dirty = bool(result.stdout.strip())
        if _COMMIT.fullmatch(head):
            merged = _run(
                [
                    "git",
                    "-c",
                    f"safe.directory={repository}",
                    "merge-base",
                    "--is-ancestor",
                    head,
                    canonical_head,
                ],
                cwd=repository,
            )
            if merged.returncode in (0, 1):
                merged_into_canonical = merged.returncode == 0
            else:
                errors.append(merged.stderr.strip() or "cannot compare worktree ancestry")
            count = _run(
                [
                    "git",
                    "-c",
                    f"safe.directory={repository}",
                    "rev-list",
                    "--count",
                    f"{canonical_head}..{head}",
                ],
                cwd=repository,
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
    'active_jobs', (
        SELECT count(*)::integer
        FROM public.queue_jobs
        WHERE queue_name IN ('codex-implement','controller-verify')
          AND state IN ('queued','leased','running')
    )
)::text AS observation_json
"""


def _database_observation(config: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    actor = _operator_actor()
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
    with psycopg2.connect(config["postgres_dsn"]) as connection:
        with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(_DATABASE_OBSERVATION_SQL)
            row = json.loads(cursor.fetchone()["observation_json"])
    active = int(row.pop("active_jobs"))
    return row, active


def check_database(paths: DoctorPaths) -> dict[str, Any]:
    repair = "sudo -n tgw doctor repair database"
    try:
        config = _coding_config(paths)
        row, active = _database_observation(config)
        required = (
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
        )
        ok = all(row.get(key) is True for key in required)
        return _check(
            "database.local-coding",
            "PASS" if ok else "FAIL",
            f"peer actor {row.get('actor')} has {'all' if ok else 'incomplete'} local coding grants; {active} active job(s)",
            evidence={**row, "active_jobs": active, "database": config["postgres_dsn"]},
            repair=None if ok else repair,
        )
    except Exception as exc:
        return _failed("database.local-coding", exc, repair=repair)


def _unit_state(unit: str) -> dict[str, Any]:
    result = _run(
        [
            "systemctl",
            "show",
            unit,
            "--property=LoadState,ActiveState,SubState,FragmentPath,DropInPaths,ExecStart,MainPID,NeedDaemonReload",
            "--no-pager",
        ]
    )
    if result.returncode:
        raise DoctorError(result.stderr.strip() or f"cannot inspect {unit}")
    values: dict[str, str] = {}
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


def _unit_definition(paths: DoctorPaths, unit: str, state: Mapping[str, str]) -> dict[str, Any]:
    desired, release, _task = _desired_runtime(paths)
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


def check_units(paths: DoctorPaths) -> dict[str, Any]:
    repair = "sudo -n tgw doctor repair workers"
    try:
        observed = {}
        for unit in _CODING_UNITS:
            state = _unit_state(unit)
            state["definition"] = _unit_definition(paths, unit, state)
            observed[unit] = state
        unhealthy = [
            unit
            for unit, state in observed.items()
            if state.get("LoadState") != "loaded" or not state["definition"]["exact"] or (unit in _ACTIVE_CODING_UNITS and state.get("ActiveState") != "active")
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


def check_plan_render_worker(paths: DoctorPaths) -> dict[str, Any]:
    repair = "sudo -n tgw doctor repair plan-render-worker"
    try:
        desired, release, _task = _desired_runtime(paths)
        runtime = _runtime_selector_identity(paths, desired, release)
        state = _unit_state(_PLAN_RENDER_UNIT)
        definition = _unit_definition(paths, _PLAN_RENDER_UNIT, state)
        config = _json(paths.plan_render_config)
        storage = _plan_render_storage_identity(paths)
        source = release / "config/tgw-plan-render-local.json"
        exact_config = (
            not source.is_symlink()
            and source.is_file()
            and not paths.plan_render_config.is_symlink()
            and paths.plan_render_config.is_file()
            and source.read_bytes() == paths.plan_render_config.read_bytes()
        )
        healthy = state.get("LoadState") == "loaded" and state.get("ActiveState") == "active" and definition["exact"] and exact_config and runtime["exact"] and storage["exact"]
        reasons = list(definition["reasons"])
        if not exact_config:
            reasons.append("immutable config path or bytes differ")
        if not runtime["exact"]:
            reasons.append("immutable runtime selector differs")
        if not storage["exact"]:
            reasons.append("plan_render output directories differ")
        if state.get("ActiveState") != "active":
            reasons.append("service is not active")
        return _check(
            "services.plan-render",
            "PASS" if healthy else "FAIL",
            "local plan_render consumer is exact and active" if healthy else "; ".join(reasons),
            evidence={
                "unit": state,
                "definition": definition,
                "config": config,
                "runtime": runtime,
                "storage": storage,
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
    if release_before.st_uid not in paths.trusted_release_owners or release_before.st_mode & 0o022 or not stat.S_ISDIR(release_before.st_mode):
        raise DoctorError("release root ownership or permissions are not immutable")
    result = _run(
        [
            "git",
            "-c",
            f"safe.directory={paths.repository.resolve()}",
            "ls-tree",
            "-r",
            "-z",
            "--full-tree",
            desired,
        ],
        cwd=paths.repository,
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

    actual = {str(path.relative_to(release)) for path in release.rglob("*") if path.is_file() or path.is_symlink()}
    unsafe_paths: list[str] = []
    for path in release.rglob("*"):
        observed = path.stat(follow_symlinks=False)
        relative = str(path.relative_to(release))
        if observed.st_uid not in paths.trusted_release_owners:
            unsafe_paths.append(relative + ":owner")
        if not stat.S_ISLNK(observed.st_mode) and observed.st_mode & 0o022:
            unsafe_paths.append(relative + ":writable")
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
            data = path.read_bytes()
            after = path.stat(follow_symlinks=False)
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
        "trusted_owners": list(paths.trusted_release_owners),
    }


def _launcher_links(paths: DoctorPaths) -> dict[Path, Path]:
    current = paths.runtime_root / "current/bin"
    return {
        paths.local_bin / "tgw-todo": current / "tgw-todo-local-operator",
        paths.local_bin / "tgw-coding": current / "tgw-coding-local-operator",
        paths.local_bin / "tgw-coding-mcp": current / "tgw-coding-mcp",
        paths.local_bin / "tgw-doctor": current / "tgw-doctor",
        paths.operator_cli: current / "tgw-operator",
    }


def check_runtime(paths: DoctorPaths) -> dict[str, Any]:
    repair = "sudo -n tgw doctor repair runtime"
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
    return path.exists() or path.is_symlink()


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
        repair = "sudo -n tgw doctor repair obsolete-surfaces"
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
    _atomic_json(path, body)
    return str(path)


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


def _surface_semantics(surface: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("kind", "raw", "target", "uid", "gid", "mode")
    return {key: surface[key] for key in keys if key in surface}


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


def repair_context_launcher(paths: DoctorPaths) -> dict[str, Any]:
    """Install one root-owned launcher bound to the exact immutable release."""
    _require_root()
    selected = _selected_context_artifacts(paths)
    desired = selected["commit"]
    source = selected["launcher"]
    expected = source.read_bytes()
    signature = _context_artifact_signature(selected)
    before = _surface_snapshot(paths.context_launcher)
    changed = not _launcher_surface_exact(before, expected, paths)
    installed_snapshot: dict[str, Any] | None = None
    replacement_attempted = False
    try:
        selected_before_commit = _selected_context_artifacts(paths)
        if _context_artifact_signature(selected_before_commit) != signature:
            raise DoctorError("selected Context runtime changed before repair; no live file was replaced")
        if _surface_snapshot(paths.context_launcher) != before:
            raise DoctorError("installed Context launcher changed concurrently; no live file was replaced")
        if changed:
            replacement_attempted = True
            _atomic_bytes(
                paths.context_launcher,
                expected,
                mode=paths.context_launcher_mode,
                uid=paths.context_install_uid,
                gid=paths.context_install_gid,
            )
        installed_snapshot = _surface_snapshot(paths.context_launcher)
        if not _launcher_surface_exact(installed_snapshot, expected, paths):
            raise DoctorError("Context launcher atomic replacement did not converge")
        selected_after = _selected_context_artifacts(paths)
        if _context_artifact_signature(selected_after) != signature:
            raise DoctorError("selected Context runtime changed during repair")
        after_record = {
            "launcher": _surface_record(installed_snapshot),
            "runtime_commit": desired,
            "runtime_source": str(selected["runtime_source"]),
            "runtime_hashes": selected["hashes"],
        }
        receipt = _receipt(
            paths,
            "context-launcher",
            {"launcher": _surface_record(before)},
            after_record,
        )
    except Exception as exc:
        rollback_errors: list[str] = []
        rollback_state: str | None = None
        if changed and replacement_attempted:
            try:
                current = _surface_snapshot(paths.context_launcher)
                if current == before:
                    rollback_state = "original launcher unchanged"
                elif installed_snapshot is not None and current != installed_snapshot:
                    raise DoctorError("launcher changed concurrently after replacement; refused rollback overwrite")
                elif installed_snapshot is None and not _launcher_surface_exact(current, expected, paths):
                    raise DoctorError("launcher changed unexpectedly during replacement; refused rollback overwrite")
                else:
                    _restore_surface(paths.context_launcher, before)
                    restored = _surface_snapshot(paths.context_launcher)
                    if _surface_semantics(restored) != _surface_semantics(before):
                        raise DoctorError("original launcher surface was not restored exactly")
                    rollback_state = "original launcher restored"
            except Exception as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        suffix = "; rollback errors: " + "; ".join(rollback_errors) if rollback_errors else f"; {rollback_state}" if rollback_state else ""
        raise DoctorError(f"Context launcher repair failed: {exc}{suffix}") from exc
    try:
        clients = _context_processes(paths)
        process_error = None
    except Exception as exc:
        clients = []
        process_error = str(exc)
    affected = [item for item in clients if item["predates_launcher"]]
    return {
        "ok": True,
        "operation": "context-launcher",
        "changed": changed,
        "runtime_commit": desired,
        "source": str(source),
        "installed": str(paths.context_launcher),
        "sha256": _file_hash(source),
        "runtime_source": str(selected["runtime_source"]),
        "runtime_hashes": selected["hashes"],
        "receipt": receipt,
        "client_processes_mutated": False,
        "restart_required": [item["pid"] for item in affected],
        "restart_scope": "affected parent harness sessions only",
        "restart_detection_error": process_error,
    }


def repair_context(paths: DoctorPaths) -> dict[str, Any]:
    _require_root()
    _require_trusted_root_program(paths.context_publisher)
    context_runtime = _require_trusted_context_runtime(paths)
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
    task = dict(before["task"])
    cursor = dict(before["cursor"])
    task_source = task.get("implementation", {}).get("development_source", {}).get("commit")
    if task_source != head:
        raise DoctorError("current task and canonical source disagree; explicit operator disposition is required")
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
    paths.context_cursor.parent.mkdir(parents=True, exist_ok=True)
    cursor_fd, cursor_text = tempfile.mkstemp(
        prefix=paths.context_cursor.name + ".doctor-stage.",
        dir=paths.context_cursor.parent,
    )
    snapshot_fd, snapshot_text = tempfile.mkstemp(
        prefix=paths.context_snapshot.name + ".doctor-stage.",
        dir=paths.context_snapshot.parent,
    )
    os.close(cursor_fd)
    os.close(snapshot_fd)
    staged_cursor = Path(cursor_text)
    staged_snapshot = Path(snapshot_text)
    try:
        _atomic_json(staged_cursor, cursor)
        staged_snapshot.unlink(missing_ok=True)
        result = _run(
            [
                str(paths.context_publisher),
                "--task",
                str(paths.context_task),
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
        after = _json(staged_snapshot)
        _validate_snapshot(after)
        if after.get("task") != task or after.get("cursor") != cursor:
            raise DoctorError("staged context publisher output differs from exact inputs")
        changed = before["cursor"] != cursor or before["snapshot"] != after
        current_head, current_tree, current_status = _source_identity(paths)
        if current_status or (current_head, current_tree) != (head, tree):
            raise DoctorError("canonical source changed during context repair")
        if _surface_snapshot(paths.context_task) != task_surface or _surface_snapshot(paths.context_cursor) != cursor_surface or _surface_snapshot(paths.context_snapshot) != snapshot_surface:
            raise DoctorError("context inputs changed concurrently; no live file was replaced")
        committed_cursor: dict[str, Any] | None = None
        committed_snapshot: dict[str, Any] | None = None
        try:
            # The cursor is non-live publisher input. Commit it first; the one
            # snapshot CAS below remains the sole MCP-visible cutover.
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
            if _surface_snapshot(paths.context_task) != task_surface or _surface_snapshot(paths.context_cursor) != committed_cursor:
                raise DoctorError("Context inputs changed before atomic snapshot cutover")
            if before["snapshot"] == after:
                committed_snapshot = snapshot_surface
            else:
                committed_snapshot = _cas_regular_file(
                    paths.context_snapshot,
                    snapshot_surface,
                    _json_bytes(after),
                    mode=snapshot_surface["mode"],
                    uid=snapshot_surface["uid"],
                    gid=snapshot_surface["gid"],
                )
            if _surface_snapshot(paths.context_task) != task_surface or _surface_snapshot(paths.context_cursor) != committed_cursor or _surface_snapshot(paths.context_snapshot) != committed_snapshot:
                raise DoctorError("final Context transaction verification failed")
            receipt = _receipt(
                paths,
                "context",
                before,
                {"cursor": cursor, "snapshot": after},
            )
        except Exception as exc:
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
        staged_cursor.unlink(missing_ok=True)
        staged_snapshot.unlink(missing_ok=True)
    return {"ok": True, "operation": "context", "changed": changed, "receipt": receipt}


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


def repair_runtime(paths: DoctorPaths) -> dict[str, Any]:
    _require_root()
    desired, release, _task = _desired_runtime(paths)
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
    }
    changed = current_link.resolve(strict=False) != release.resolve()
    try:
        if changed:
            _replace_link(current_link, Path("releases") / desired)
        _verify_release_tree(paths, desired, release)
    except Exception as exc:
        rollback_errors = []
        if previous_selector is not None:
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
    after = {
        "current_link": os.readlink(current_link),
        "current": str(current_link.resolve(strict=True)),
        "launchers": {str(path): {"target": os.readlink(path), "sha256": _file_hash(path)} for path in launcher_links},
        "release_tree": release_tree,
    }
    receipt = _receipt(paths, "runtime", before, after)
    return {"ok": True, "operation": "runtime", "changed": changed, "receipt": receipt}


def repair_database(paths: DoctorPaths) -> dict[str, Any]:
    _require_root()
    desired, release, _task = _desired_runtime(paths)
    _verify_release_tree(paths, desired, release)
    sql = release / "config/tgw-coding-local-roles.sql"
    if not sql.is_file():
        raise DoctorError(f"runtime {desired} lacks the coding role SQL")
    before = check_database(paths)
    result = _run(
        [
            "sudo",
            "-n",
            "-u",
            "postgres",
            "psql",
            "--dbname=tgw_lib_dev_state_machine",
            "--file",
            str(sql),
        ],
        timeout=30,
    )
    if result.returncode:
        raise DoctorError(result.stderr.strip() or "database role repair failed")
    after = check_database(paths)
    if after["state"] != "PASS":
        raise DoctorError("database grants remain incomplete after repair")
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
        ["git", "-c", f"safe.directory={location}", "-C", location, *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        pass_fds=(descriptor,),
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


def _rename_exchange(directory_descriptor: int, first: str, second: str) -> None:
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
        directory_descriptor,
        os.fsencode(first),
        directory_descriptor,
        os.fsencode(second),
        _RENAME_EXCHANGE,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise DoctorError(f"cannot atomically exchange files: {os.strerror(error)}")


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
                        if in_immutable_directory(relative):
                            raise DoctorError(
                                f"cannot safely inventory immutable directory entry without O_NOATIME: {relative}"
                            )
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


def _inspect_shared_git_trees(paths: DoctorPaths, group_gid: int) -> dict[str, Any]:
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
    for location, relative in local:
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
        before.st_mtime_ns,
    ) == (
        after.st_dev,
        after.st_ino,
        after.st_size,
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
        or any(unit not in _ACTIVE_CODING_UNITS for unit in active)
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
    stopped = _run(["systemctl", "stop", *units], timeout=30)
    if stopped.returncode:
        raise DoctorError(stopped.stderr.strip() or "cannot stop local coding units")
    unsettled = []
    for unit, dropin in dropins.items():
        state = _unit_state(unit)
        loaded_dropins = state.get("DropInPaths", "").split()
        if state.get("ActiveState") != "inactive" or str(dropin) not in loaded_dropins or not _quiescence_file_exact(dropin, dropin_value, mode=0o444, uid=uid, gid=gid):
            unsettled.append(unit)
    if not _quiescence_file_exact(marker, marker_value, mode=0o400, uid=uid, gid=gid):
        unsettled.append("quiescence-marker")
    if unsettled:
        raise DoctorError("local coding units did not reach guarded/inactive state: " + ", ".join(unsettled))


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
    if marker_absent:
        undesired = [unit for unit in units if unit not in initially_active and _unit_state(unit).get("ActiveState") == "active"]
        if undesired:
            stopped = _run(["systemctl", "stop", *undesired], timeout=30)
            if stopped.returncode:
                errors.append(stopped.stderr.strip() or "cannot restore initially inactive local coding units")
        if initially_active:
            started = _run(["systemctl", "start", *initially_active], timeout=30)
            if started.returncode:
                errors.append(started.stderr.strip() or "cannot restore initially active local coding units")
    else:
        errors.append("quiescence marker remains; refusing to start local coding units")

    wrong = [unit for unit in units if (_unit_state(unit).get("ActiveState") == "active") != (unit in initially_active)]
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
    initially_active = [unit for unit, state in initial_states.items() if unit in _ACTIVE_CODING_UNITS and state.get("ActiveState") == "active"]
    _secure_runtime_directory(paths.quiescence_root, uid=uid, gid=gid)
    state, state_raw = _new_quiescence_state(units, initially_active, state_path, marker, dropins)
    _create_quiescence_file(state_path, state_raw, mode=0o400, uid=uid, gid=gid)
    evidence = {
        "recovered_stale_quiescence": recovered,
        "owner_pid": state["owner_pid"],
        "boot_id": state["boot_id"],
        "initially_active": list(initially_active),
    }
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
    finally:
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
                if stat.S_IMODE(current.st_mode) == 0o460:
                    journal.append({"kind": "metadata", "descriptor": os.dup(item["descriptor"]), "before": current})
                    os.fchmod(item["descriptor"], 0o440)
                    repaired_preservation.append(item)
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
            for item in repaired_preservation:
                after_state = os.fstat(item["descriptor"])
                if stat.S_IMODE(after_state.st_mode) != 0o440 or any(getattr(item["before"], field) != getattr(after_state, field) for field in stable_fields):
                    raise DoctorError(f"Todo {item['todo_id']} preservation manifest attributes changed")
            for path, descriptor in bound:
                _verify_bound_directory(path, descriptor)
            revalidate_preservation_directories()
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
            {"access": after, "quiescence": quiescence},
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


def _unit_destination_exact(paths: DoctorPaths, destination: Path, source: Path) -> bool:
    if destination.is_symlink() or not destination.is_file():
        return False
    state = destination.stat(follow_symlinks=False)
    return (
        stat.S_ISREG(state.st_mode)
        and state.st_uid == paths.systemd_unit_uid
        and state.st_gid == paths.systemd_unit_gid
        and stat.S_IMODE(state.st_mode) == paths.systemd_unit_mode
        and state.st_nlink == 1
        and destination.read_bytes() == source.read_bytes()
    )


def repair_workers(paths: DoctorPaths) -> dict[str, Any]:
    _require_root()
    desired, release, _task = _desired_runtime(paths)
    _verify_release_tree(paths, desired, release)
    before = check_units(paths)
    installed: list[str] = []
    for unit in _CODING_UNITS:
        source = release / "systemd" / unit
        destination = paths.systemd_install_root / unit
        if not source.is_file():
            raise DoctorError(f"immutable runtime lacks coding unit: {unit}")
        if destination.is_symlink() or (destination.exists() and not destination.is_file()):
            raise DoctorError(f"refusing unsafe coding unit destination: {destination}")
        if not _unit_destination_exact(paths, destination, source):
            _atomic_bytes(
                destination,
                source.read_bytes(),
                mode=paths.systemd_unit_mode,
                uid=paths.systemd_unit_uid,
                gid=paths.systemd_unit_gid,
            )
            installed.append(unit)
    if installed:
        result = _run(["systemctl", "daemon-reload"], timeout=30)
        if result.returncode:
            raise DoctorError(result.stderr.strip() or "systemd daemon reload failed")
    actions: list[str] = []
    for unit in _ACTIVE_CODING_UNITS:
        state = _unit_state(unit)
        definition = _unit_definition(paths, unit, state)
        if not definition["exact"]:
            raise DoctorError(f"installed coding unit is not exact: {unit}")
        operation = "restart" if unit in installed else "start"
        if operation == "start" and state.get("ActiveState") == "active":
            continue
        result = _run(["systemctl", "enable", unit], timeout=30)
        if result.returncode:
            raise DoctorError(result.stderr.strip() or f"failed to enable {unit}")
        result = _run(["systemctl", operation, unit], timeout=30)
        if result.returncode:
            raise DoctorError(result.stderr.strip() or f"failed to {operation} {unit}")
        actions.append(f"{operation}:{unit}")
    after = check_units(paths)
    if after["state"] != "PASS":
        raise DoctorError("local coding units remain unhealthy after repair")
    receipt = _receipt(paths, "workers", before, after)
    return {
        "ok": True,
        "operation": "workers",
        "changed": bool(installed or actions),
        "installed": installed,
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


def repair_plan_render_worker(paths: DoctorPaths) -> dict[str, Any]:
    _require_root()
    desired, release, _task = _desired_runtime(paths)
    _verify_release_tree(paths, desired, release)
    runtime = _runtime_selector_identity(paths, desired, release)
    if not runtime["exact"]:
        raise DoctorError("immutable runtime selector differs; repair runtime before plan_render")
    before = check_plan_render_worker(paths)
    config_source = release / "config/tgw-plan-render-local.json"
    unit_source = release / "systemd" / _PLAN_RENDER_UNIT
    if config_source.is_symlink() or not config_source.is_file() or unit_source.is_symlink() or not unit_source.is_file():
        raise DoctorError("immutable runtime lacks plan_render config or unit")
    if paths.plan_render_config.is_symlink() or (paths.plan_render_config.exists() and not paths.plan_render_config.is_file()):
        raise DoctorError("refusing unsafe plan_render config destination")
    destination = paths.systemd_install_root / _PLAN_RENDER_UNIT
    if destination.is_symlink() or (destination.exists() and not destination.is_file()):
        raise DoctorError("refusing unsafe plan_render unit destination")
    changed = False
    if not paths.plan_render_config.is_file() or paths.plan_render_config.read_bytes() != config_source.read_bytes():
        _atomic_bytes(paths.plan_render_config, config_source.read_bytes(), mode=0o444, uid=paths.systemd_unit_uid, gid=paths.systemd_unit_gid)
        changed = True
    if not _unit_destination_exact(paths, destination, unit_source):
        _atomic_bytes(destination, unit_source.read_bytes(), mode=paths.systemd_unit_mode, uid=paths.systemd_unit_uid, gid=paths.systemd_unit_gid)
        result = _run(["systemctl", "daemon-reload"], timeout=30)
        if result.returncode:
            raise DoctorError(result.stderr.strip() or "systemd daemon reload failed")
        changed = True
    state = _unit_state(_PLAN_RENDER_UNIT)
    if not _unit_definition(paths, _PLAN_RENDER_UNIT, state)["exact"]:
        raise DoctorError("installed plan_render unit is not exact")
    storage_before = _plan_render_storage_identity(paths)
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
    if changed or state.get("ActiveState") != "active":
        for command in (["systemctl", "enable", _PLAN_RENDER_UNIT], ["systemctl", "restart" if state.get("ActiveState") == "active" else "start", _PLAN_RENDER_UNIT]):
            result = _run(command, timeout=30)
            if result.returncode:
                raise DoctorError(result.stderr.strip() or "plan_render service repair failed")
        action = command[1]
    after = check_plan_render_worker(paths)
    if after["state"] != "PASS":
        raise DoctorError("plan_render unit remains unhealthy after repair")
    return {
        "ok": True,
        "operation": "plan-render-worker",
        "changed": changed or action is not None,
        "service_action": action,
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


def repair(operation: str, paths: DoctorPaths = DoctorPaths()) -> dict[str, Any]:
    function = _REPAIRS.get(operation)
    if function is None:
        raise DoctorError(f"unknown repair: {operation}")
    _require_root()
    with _repair_lock(paths):
        started_receipt = _receipt(
            paths,
            operation + "-started",
            {"operation": operation},
            {"state": "STARTED"},
        )
        try:
            result = function(paths)
            result = {**result, "started_receipt": started_receipt}
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
    return {
        "schema": "tgw-local-doctor-repair/v1",
        "ok": True,
        "operation": operation,
        "results": [result],
        "diagnosis": diagnose(paths),
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tgw doctor")
    parser.add_argument("--json", action="store_true", dest="json_output")
    sub = parser.add_subparsers(dest="operation")
    sub.add_parser("check", help="run read-only diagnosis (default)")
    sub.add_parser("inventory", help="inventory linked and active-path remnants read-only")
    resume_parser = sub.add_parser("coding-resume", help="resume one exact local RESUMABLE_PARTIAL Todo")
    resume_parser.add_argument("todo_id", type=int)
    reconcile_parser = sub.add_parser("coding-reconcile", help="reconcile one older-runner closed implementation receipt")
    reconcile_parser.add_argument("todo_id", type=int)
    repair_parser = sub.add_parser("repair", help="restore an exact declared local state")
    repair_parser.add_argument("target", choices=[*_REPAIRS])
    repair_parser.add_argument("--json", action="store_true", dest="repair_json_output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.operation == "repair":
            result = repair(args.target)
            print(json.dumps(result, indent=2, sort_keys=True))
            return int(result["diagnosis"]["exit_code"])
        if args.operation == "inventory":
            print(json.dumps(inventory(), indent=2, sort_keys=True))
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
        result = diagnose()
        if args.json_output:
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


if __name__ == "__main__":
    raise SystemExit(main())
