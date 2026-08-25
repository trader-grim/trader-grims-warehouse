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
import errno
import fcntl
import grp
import hashlib
import json
import os
import pwd
import re
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


class DoctorError(RuntimeError):
    """The requested diagnosis or repair cannot be performed safely."""


@dataclass(frozen=True)
class DoctorPaths:
    repository: Path = Path("/opt/TGW/tgw-lib/src/trader-grims-warehouse")
    worktrees: Path = Path("/opt/TGW/var/worktrees")
    coding_config: Path = Path("/opt/TGW/tgw-lib/config/tgw-coding-local.json")
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
    systemd_unit_roots: tuple[Path, ...] = _SYSTEMD_UNIT_ROOTS
    archive_discovery_roots: tuple[Path, ...] = _ARCHIVE_DISCOVERY_ROOTS
    archive_discovery_max_depth: int = _ARCHIVE_DISCOVERY_MAX_DEPTH


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


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
        _require_trusted_root_program(
            paths.context_launcher, paths.trusted_release_owners
        )
        launcher_text = paths.context_launcher.read_text(encoding="utf-8")
        if (
            str(paths.context_snapshot) not in launcher_text
            or str(paths.context_cursor) in launcher_text
        ):
            raise DoctorError(
                "Context launcher does not preserve the single-snapshot runtime boundary"
            )
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
    return any(
        argv[index : index + 2] == ["-m", "tgw.context_mcp_server"]
        for index in range(max(0, len(argv) - 1))
    )


def _context_processes(paths: DoctorPaths) -> list[dict[str, Any]]:
    boot = _boot_time()
    ticks = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
    launcher_mtime = paths.context_launcher.stat().st_mtime
    processes: list[dict[str, Any]] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = [
                value.decode(errors="replace")
                for value in (entry / "cmdline").read_bytes().split(b"\0")
                if value
            ]
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
    return all(
        _run(["sudo", "-n", "-u", actor, "/usr/bin/test", flag, str(path)]).returncode
        == 0
        for flag in ("-r", "-w", "-x")
    )


def check_unix_access(paths: DoctorPaths) -> dict[str, Any]:
    try:
        actor = _operator_actor()
        actor_record = pwd.getpwnam(actor)
        group = grp.getgrnam("tgw-coders")
        memberships = set(os.getgrouplist(actor, actor_record.pw_gid))
        member = group.gr_gid in memberships or actor in group.gr_mem
        writable = {
            "repository": _actor_path_access(actor, paths.repository),
            "worktree_root": _actor_path_access(actor, paths.worktrees),
        }
        state = "PASS" if member and all(writable.values()) else "FAIL"
        return _check(
            "access.unix-group",
            state,
            f"{actor} {'is' if member else 'is not'} in tgw-coders; local paths "
            f"are {'accessible' if all(writable.values()) else 'not writable'}",
            evidence={
                "actor": actor,
                "group": "tgw-coders",
                "group_gid": group.gr_gid,
                "members": sorted(group.gr_mem),
                "access": writable,
            },
        )
    except Exception as exc:
        return _failed("access.unix-group", exc)


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
        same_filesystem = paths.repository.stat().st_dev == paths.worktrees.stat().st_dev
        state = "PASS" if not outside and not prunable and same_filesystem else "WARN"
        return _check(
            "git.worktrees",
            state,
            f"{len(rows)} linked worktree(s); "
            f"{'same' if same_filesystem else 'different'} filesystem; "
            f"{len(outside)} outside root; {len(prunable)} prunable",
            evidence={
                "repository": str(repository),
                "worktree_root": str(root),
                "same_filesystem": same_filesystem,
                "count": len(rows),
                "outside_root": outside,
                "prunable": prunable,
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
    for row in rows:
        location = Path(str(row.get("worktree", ""))).resolve()
        head = str(row.get("HEAD", ""))
        branch_ref = row.get("branch")
        branch = (
            str(branch_ref).removeprefix("refs/heads/")
            if isinstance(branch_ref, str)
            else None
        )
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
        preservation_required = (
            dirty is not False
            or unique_commits is None
            or unique_commits > 0
            or bool(errors)
        )
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
            }
        )

    path_roots = {
        Path(value)
        for value in os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin").split(":")
        if value and Path(value).is_absolute()
    }
    surface_roots = [
        ("effective-path-command", parent, "tgw*") for parent in sorted(path_roots)
    ]
    surface_roots.extend(
        [
            ("operator-libexec", Path("/usr/local/libexec"), "tgw*"),
            ("development-command", paths.local_bin, "tgw*"),
            *(
                ("systemd-unit", parent, "tgw-*")
                for parent in paths.systemd_unit_roots
            ),
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
                    "target": str(path.resolve(strict=False))
                    if path.is_symlink()
                    else None,
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
        unix_actors.update(
            record.pw_name
            for record in pwd.getpwall()
            if record.pw_gid == coding_group.gr_gid
        )
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
                marker_rows.append(
                    {"path": str(path), "exists": None, "error": type(exc).__name__}
                )
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
                for parent, directories, _files in os.walk(
                    discovery_root, onerror=walk_errors.append
                ):
                    relative_depth = len(
                        Path(parent).relative_to(discovery_root).parts
                    )
                    for name in directories:
                        if name.lower() in archive_names:
                            archive_candidates.add(Path(parent) / name)
                    directories[:] = [
                        name
                        for name in directories
                        if relative_depth < paths.archive_discovery_max_depth
                        and name not in _ARCHIVE_DISCOVERY_PRUNE
                        and not name.startswith(".")
                    ]
                row["scanned"] = True
                row["complete"] = not walk_errors
                if walk_errors:
                    row["error"] = "; ".join(
                        f"{type(exc).__name__}: {exc}" for exc in walk_errors
                    )
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
            "outside_configured_root": sum(
                not row["canonical"] and not row["inside_configured_root"]
                for row in worktrees
            ),
            "preservation_required": sum(
                row["preservation_required"] for row in worktrees
            ),
            "active_surfaces": len(active_surfaces),
            "harness_homes": sum(row["home_exists"] for row in harnesses),
            "archive_roots": sum(row["exists"] is True for row in archives),
            "catalog_actors": len(catalog_actors),
            "unix_coding_actors": len(unix_actors),
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
            f"peer actor {row.get('actor')} has "
            f"{'all' if ok else 'incomplete'} local coding grants; {active} active job(s)",
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
    else:
        fragment_state = fragment.stat(follow_symlinks=False)
        if (
            fragment.is_symlink()
            or fragment_state.st_uid not in paths.trusted_release_owners
            or fragment_state.st_mode & 0o022
        ):
            reasons.append("installed fragment is not trusted-owner immutable")
        if source.is_file() and _file_hash(fragment) != _file_hash(source):
            reasons.append("installed fragment differs")
    if state.get("DropInPaths"):
        reasons.append("unexpected systemd drop-in")
    if state.get("NeedDaemonReload") != "no":
        reasons.append("systemd has not loaded the installed definition")
    expected_argv = _UNIT_ARGV.get(unit)
    loaded_exec_path: str | None = None
    loaded_exec_argv: tuple[str, ...] | None = None
    if expected_argv:
        try:
            loaded_exec_path, loaded_exec_argv = _loaded_exec_identity(
                state.get("ExecStart", "")
            )
        except (DoctorError, ValueError) as exc:
            reasons.append(str(exc))
        else:
            if loaded_exec_path != expected_argv[0] or loaded_exec_argv != expected_argv:
                reasons.append("loaded ExecStart differs")
    process_argv: list[str] | None = None
    if unit in _ACTIVE_CODING_UNITS and unit.endswith(".service"):
        try:
            pid = int(state.get("MainPID", "0"))
        except ValueError:
            pid = 0
        if pid > 0:
            try:
                process_argv = [
                    value.decode(errors="replace")
                    for value in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
                    if value
                ]
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
        "fragment_sha256": _file_hash(fragment)
        if fragment is not None and fragment.is_file()
        else None,
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
            if state.get("LoadState") != "loaded"
            or not state["definition"]["exact"]
            or (
                unit in _ACTIVE_CODING_UNITS
                and state.get("ActiveState") != "active"
            )
        ]
        return _check(
            "services.local-coding",
            "PASS" if not unhealthy else "FAIL",
            "all local coding definitions are exact and required units are active"
            if not unhealthy
            else "inactive or missing units: " + ", ".join(unhealthy),
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


def _verify_release_tree(
    paths: DoctorPaths, desired: str, release: Path
) -> dict[str, Any]:
    """Verify every released path and mode against the exact Git commit tree."""
    releases_root = (paths.runtime_root / "releases").resolve(strict=True)
    if release.is_symlink() or release.resolve(strict=True).parent != releases_root:
        raise DoctorError("release path escapes the immutable releases directory")
    release_before = release.stat(follow_symlinks=False)
    if (
        release_before.st_uid not in paths.trusted_release_owners
        or release_before.st_mode & 0o022
        or not stat.S_ISDIR(release_before.st_mode)
    ):
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

    actual = {
        str(path.relative_to(release))
        for path in release.rglob("*")
        if path.is_file() or path.is_symlink()
    }
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
                "installed_link": os.readlink(destination)
                if destination.is_symlink()
                else None,
            }
            if (
                not destination.is_symlink()
                or os.readlink(destination) != str(target)
            ):
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
            f"runtime {desired[:12]} and local launchers are exact"
            if not mismatches
            else "runtime drift: " + "; ".join(mismatches),
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
            repair=None
            if not mismatches
            else (
                "install the exact fixed launcher links during a bounded local bootstrap"
                if launcher_surface_drift
                else repair
            ),
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
            repair=(
                "materialize the exact root-protected release during bounded local bootstrap"
                if bootstrap
                else repair
            ),
        )


_OBSOLETE_FILE_HASHES = {
    "tgw-foreman": "sha256:10152bcc0c7c72555a630d662e736ee827dc3edb5e3f3a0ad78ecf5b450d6332",
    "tgw-foreman-dispatch": "sha256:61fa8586dfc655685bfece1cbd71b7deed357d23833177bf9d0b6158825f66c5",
    "tgw-context-mcp-candidate-3fe54df8": "sha256:722dcfecebb23ee2dd71d8bfcf923a5275b8089edd03d47e632c51417bfc8699",
    "tgw-context-mcp-candidate-408ee56c": "sha256:3e9f57ad0a60597595e158dde60c36f8857e03b9902f0106332975fab5db4db6",
    "tgw-context-mcp-candidate-6813c302": "sha256:a1a1d637414e4afa881af03d0d0574e44733985a13b33f4acfff3bc443923e5b",
    "tgw-context-mcp-candidate-6865ce87": "sha256:9821db48b5205bbc06ccb6bd32697fbf25532c81b3292ddb5e0bae78fcda6009",
}
_OBSOLETE_ACTOR_TARGET = (
    "/opt/TGW/tgw-lib/actor-runtime/releases/"
    "w18-9634e8a7-20260822/scripts/tgw_actor_startup.py"
)
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
    candidate_names = sorted(
        name
        for name in _OBSOLETE_FILE_HASHES
        if name.startswith("tgw-context-mcp-candidate-")
    )
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
    unbound = [
        str(path)
        for name in _UNBOUND_OBSOLETE_NAMES
        if _lexists(path := paths.cleanup_system_bin / name)
    ]
    declared_candidates = {
        name
        for name in _OBSOLETE_FILE_HASHES
        if name.startswith("tgw-context-mcp-candidate-")
    }
    if paths.local_bin.is_dir():
        unbound.extend(
            str(path)
            for path in paths.local_bin.glob("tgw-context-mcp-candidate-*")
            if path.name not in declared_candidates
        )
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


def _surface_matches_declaration(
    item: Mapping[str, Any], observation: Mapping[str, Any]
) -> bool:
    if item["kind"] == "symlink":
        return observation.get("target") == item.get("declared_target")
    return (
        isinstance(item.get("declared_sha256"), str)
        and observation.get("sha256") == item["declared_sha256"]
    )


def check_obsolete_surfaces(paths: DoctorPaths) -> dict[str, Any]:
    declared = _declared_obsolete_surfaces(paths)
    present = [item for item in declared if _lexists(item["path"])]
    visible = [_surface_observation(item) for item in present]
    mismatched = [
        observation
        for item, observation in zip(present, visible, strict=True)
        if not _surface_matches_declaration(item, observation)
    ]
    unbound = _unbound_obsolete_surfaces(paths)
    if mismatched or unbound:
        state = "FAIL"
        detail = "obsolete active surfaces are not bound to the exact cleanup treatment"
        repair = None
    elif visible:
        state = "WARN"
        detail = "verified obsolete active surfaces remain: " + ", ".join(
            item["path"] for item in visible
        )
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
        check_context_processes(paths),
        check_unix_access(paths),
        check_worktrees(paths),
        check_database(paths),
        check_units(paths),
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


def _require_trusted_root_program(
    path: Path, trusted_owners: Sequence[int] = (0, 65534)
) -> None:
    try:
        resolved = path.resolve(strict=True)
        state = resolved.stat()
    except OSError as exc:
        raise DoctorError(f"trusted repair program is unavailable: {path}") from exc
    if (
        not resolved.is_file()
        or state.st_uid not in trusted_owners
        or state.st_mode & 0o022
        or not os.access(resolved, os.X_OK)
    ):
        raise DoctorError(f"repair program is not trusted-owner immutable: {resolved}")


def _require_trusted_context_runtime(paths: DoctorPaths) -> Path:
    configured_source = paths.context_runtime_source
    if configured_source.is_symlink():
        raise DoctorError(
            f"Context runtime path is not trusted-owner immutable: {configured_source}"
        )
    source = configured_source.resolve(strict=True)
    module = source / "tgw/current_context_snapshot.py"
    for path, expected_kind in (
        (source, "directory"),
        (source / "tgw", "directory"),
        (module, "file"),
    ):
        try:
            observed = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise DoctorError(f"Context runtime path is unavailable: {path}") from exc
        correct_kind = (
            stat.S_ISDIR(observed.st_mode)
            if expected_kind == "directory"
            else stat.S_ISREG(observed.st_mode)
        )
        if (
            path.is_symlink()
            or not correct_kind
            or observed.st_uid not in paths.trusted_release_owners
            or observed.st_mode & 0o022
        ):
            raise DoctorError(f"Context runtime path is not trusted-owner immutable: {path}")
    return source


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


def _atomic_bytes(path: Path, value: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_text = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    temporary = Path(temporary_text)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(path: Path, value: Mapping[str, Any], *, mode: int = 0o444) -> None:
    raw = json.dumps(value, indent=2, sort_keys=True).encode() + b"\n"
    _atomic_bytes(path, raw, mode=mode)


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


def repair_context(paths: DoctorPaths) -> dict[str, Any]:
    _require_root()
    _require_trusted_root_program(paths.context_publisher)
    context_runtime = _require_trusted_context_runtime(paths)
    cursor_raw = paths.context_cursor.read_bytes()
    snapshot_raw = paths.context_snapshot.read_bytes()
    cursor_mode = paths.context_cursor.stat().st_mode & 0o777
    snapshot_mode = paths.context_snapshot.stat().st_mode & 0o777
    before = {
        "task": _json(paths.context_task),
        "cursor": _json(paths.context_cursor),
        "snapshot": _json(paths.context_snapshot),
    }
    head, tree, status = _source_identity(paths)
    if status:
        raise DoctorError("context repair refuses a dirty canonical source")
    task = dict(before["task"])
    cursor = dict(before["cursor"])
    task_source = task.get("implementation", {}).get("development_source", {}).get("commit")
    if task_source != head:
        raise DoctorError(
            "current task and canonical source disagree; explicit operator disposition is required"
        )
    if cursor.get("plan_commit") != task.get("plan", {}).get("approved_commit"):
        raise DoctorError("task and cursor Plan commits disagree; source-only repair is unsafe")
    capability = task.get("implementation", {}).get("development_source", {}).get("next_leaf")
    treatment = cursor.get("resolved", {}).get("next_treatment")
    if not isinstance(capability, str) or not isinstance(treatment, str) or treatment.rsplit(":", 1)[-1] != capability:
        raise DoctorError("task capability and cursor treatment disagree; repair is ambiguous")
    changed = cursor.get("source_commit") != head or cursor.get("source_tree") != tree
    if changed:
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
        current_head, current_tree, current_status = _source_identity(paths)
        if current_status or (current_head, current_tree) != (head, tree):
            raise DoctorError("canonical source changed during context repair")
        if (
            _json(paths.context_task) != before["task"]
            or _json(paths.context_cursor) != before["cursor"]
            or _json(paths.context_snapshot) != before["snapshot"]
        ):
            raise DoctorError("context inputs changed concurrently; no live file was replaced")
        try:
            # The cursor is non-live publisher input. Commit it first; the one
            # atomic snapshot rename below is the sole MCP-visible cutover.
            _atomic_json(paths.context_cursor, cursor)
            if _json(paths.context_snapshot) != before["snapshot"]:
                raise DoctorError("live Context snapshot changed before atomic cutover")
            _atomic_json(paths.context_snapshot, after)
            if (
                _json(paths.context_cursor) != cursor
                or _json(paths.context_snapshot) != after
            ):
                raise DoctorError("final Context transaction verification failed")
        except Exception as exc:
            rollback_errors = []
            for path, raw, mode in (
                (paths.context_snapshot, snapshot_raw, snapshot_mode),
                (paths.context_cursor, cursor_raw, cursor_mode),
            ):
                try:
                    _atomic_bytes(path, raw, mode=mode)
                except Exception as rollback_exc:
                    rollback_errors.append(str(rollback_exc))
            suffix = (
                "; rollback errors: " + "; ".join(rollback_errors)
                if rollback_errors
                else "; original inputs restored"
            )
            raise DoctorError(f"context commit failed: {exc}{suffix}") from exc
    finally:
        staged_cursor.unlink(missing_ok=True)
        staged_snapshot.unlink(missing_ok=True)
    receipt = _receipt(paths, "context", before, {"cursor": cursor, "snapshot": after})
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
            raise DoctorError(
                f"fixed launcher drift requires bounded bootstrap repair: {destination}"
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
        suffix = (
            "; rollback errors: " + "; ".join(rollback_errors)
            if rollback_errors
            else "; original selector restored"
        )
        raise DoctorError(f"runtime commit failed: {exc}{suffix}") from exc
    after = {
        "current_link": os.readlink(current_link),
        "current": str(current_link.resolve(strict=True)),
        "launchers": {
            str(path): {"target": os.readlink(path), "sha256": _file_hash(path)}
            for path in launcher_links
        },
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


def repair_workers(paths: DoctorPaths) -> dict[str, Any]:
    _require_root()
    desired, release, _task = _desired_runtime(paths)
    _verify_release_tree(paths, desired, release)
    before = check_units(paths)
    definition_failures = [
        unit
        for unit, state in before.get("evidence", {}).get("units", {}).items()
        if not state.get("definition", {}).get("exact")
    ]
    if definition_failures:
        raise DoctorError(
            "worker repair refuses non-exact unit definitions: "
            + ", ".join(definition_failures)
        )
    actions: list[str] = []
    for unit in _ACTIVE_CODING_UNITS:
        state = _unit_state(unit)
        definition = _unit_definition(paths, unit, state)
        if not definition["exact"]:
            raise DoctorError(f"unit definition changed before start: {unit}")
        if state.get("ActiveState") == "active":
            continue
        result = _run(["systemctl", "start", unit], timeout=30)
        if result.returncode:
            raise DoctorError(result.stderr.strip() or f"failed to start {unit}")
        actions.append(unit)
    after = check_units(paths)
    if after["state"] != "PASS":
        raise DoctorError("local coding units remain unhealthy after repair")
    receipt = _receipt(paths, "workers", before, after)
    return {
        "ok": True,
        "operation": "workers",
        "changed": bool(actions),
        "started": actions,
        "receipt": receipt,
    }


def _cleanup_references(
    paths: DoctorPaths, surfaces: Sequence[Mapping[str, Any]]
) -> list[dict[str, str]]:
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
    evidence_records = {
        path.resolve(strict=False)
        for path in (paths.context_snapshot, paths.context_task, paths.context_cursor)
    }
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
                raise DoctorError(
                    f"cannot completely inspect active configuration: {candidate}"
                ) from exc
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
            raise DoctorError(
                f"cannot completely scan active configuration: {directory}"
            ) from exc
    return candidates


def _cleanup_process_references(
    surfaces: Sequence[Mapping[str, Any]], proc_root: Path = Path("/proc")
) -> list[dict[str, Any]]:
    needles = {
        value
        for item in surfaces
        for value in (
            str(item["path"]),
            Path(item["path"]).name,
            item.get("declared_target"),
            Path(item["declared_target"]).name
            if isinstance(item.get("declared_target"), str)
            else None,
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
            argv = [
                value.decode(errors="replace")
                for value in (entry / "cmdline").read_bytes().split(b"\0")
                if value
            ]
        except OSError as exc:
            if exc.errno in (errno.ENOENT, errno.ESRCH):
                continue
            raise DoctorError(
                f"cannot completely inspect process activity: {entry}"
            ) from exc
        matched = sorted(
            needle
            for needle in needles
            if any(argument == needle or argument.startswith(needle + " ") for argument in argv)
        )
        if matched:
            references.append(
                {"pid": int(entry.name), "command": " ".join(argv), "references": matched}
            )
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
                raise DoctorError(
                    f"cleanup parent path is not a direct directory: {traversed}"
                ) from exc
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


@contextmanager
def _bind_cleanup_parent(
    path: Path, item: Mapping[str, Any]
) -> Iterator[_CleanupBinding]:
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
        return {
            name: base64.b64encode(os.getxattr(path_or_fd, name)).decode("ascii")
            for name in sorted(names)
        }
    names = os.listxattr(path_or_fd, follow_symlinks=follow_symlinks)
    return {
        name: base64.b64encode(
            os.getxattr(path_or_fd, name, follow_symlinks=follow_symlinks)
        ).decode("ascii")
        for name in sorted(names)
    }


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
            raise DoctorError(
                f"obsolete symlink target changed; refusing cleanup: {binding.path}"
            )
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
            raise DoctorError(
                f"obsolete surface has no declared file hash; refusing cleanup: {binding.path}"
            )
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


def _durable_mkdir(
    path: Path, *, mode: int = 0o755, require_new: bool = False
) -> None:
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
                    raise DoctorError(
                        f"cannot durably create archive directory: {traversed}"
                    ) from exc
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


def _apply_bound_symlink_metadata(
    parent_fd: int, name: str, metadata: Mapping[str, Any]
) -> None:
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


def _copy_cleanup_surface(
    binding: _CleanupBinding, destination: Path
) -> dict[str, Any]:
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
            _apply_bound_symlink_metadata(
                archive_parent_fd, destination.name, final["metadata"]
            )
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
                    raise DoctorError(
                        f"obsolete surface changed while archiving: {binding.path}"
                    )
                final = _verify_bound_cleanup_surface(binding)
                if final["sha256"] != before["sha256"]:
                    raise DoctorError(
                        f"obsolete surface changed while archiving: {binding.path}"
                    )
                _apply_fd_metadata(destination_fd, final["metadata"])
            finally:
                os.close(destination_fd)
        archived_state = os.stat(
            destination.name, dir_fd=archive_parent_fd, follow_symlinks=False
        )
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


def _restore_cleanup_surface(
    destination: Path, archived: Path, identity: Mapping[str, Any]
) -> None:
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
            "metadata": _metadata(
                state, _read_xattrs(proc_path, follow_symlinks=False)
            ),
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


def _restore_bound_cleanup_surface(
    binding: _CleanupBinding, archived: Path, identity: Mapping[str, Any]
) -> None:
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
        _apply_bound_symlink_metadata(
            binding.parent_fd, binding.path.name, identity["metadata"]
        )
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


def _unlink_bound_surface(
    binding: _CleanupBinding, identity: Mapping[str, Any]
) -> None:
    if _stable_bound_identity(
        _verify_bound_cleanup_surface(binding)
    ) != _stable_bound_identity(identity):
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


def _write_archive_state(
    archive: Path, name: str, value: Mapping[str, Any]
) -> str:
    path = archive / f"{name}.json"
    body = {
        "schema": "tgw-doctor-obsolete-surface-state/v1",
        "state": name,
        **dict(value),
    }
    body["state_sha256"] = _hash(body)
    _atomic_json(path, body, mode=0o400)
    return str(path)


def _reconcile_incomplete_cleanup(
    paths: DoctorPaths, declared: Sequence[Mapping[str, Any]]
) -> dict[str, Any] | None:
    incomplete = _incomplete_cleanup_archives(paths)
    if not incomplete:
        return None
    if len(incomplete) != 1:
        raise DoctorError(
            "multiple incomplete obsolete-surface archives require bounded recovery: "
            + ", ".join(str(path) for path in incomplete)
        )
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
            raise DoctorError(
                f"active surface differs from incomplete archive: {identity['path']}"
            )
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
        raise DoctorError(
            "obsolete cleanup refuses unbound active surfaces: " + ", ".join(unbound)
        )
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
        raise DoctorError(
            "obsolete cleanup refused because active references remain: "
            + json.dumps({"configuration": references, "processes": processes}, sort_keys=True)
        )

    now = datetime.now(UTC)
    archive = paths.cleanup_archive_root / now.strftime("%Y-%m-%d") / now.strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
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
        identities = [
            {key: value for key, value in row.items() if key != "archive_path"}
            for row in archived_rows
        ]
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
                        rollback_errors.append(
                            f"bound parent no longer visible at {binding.path.parent}; "
                            "surface restored only to its original directory inode"
                        )
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
            suffix = (
                "original active view restored" if not rollback_errors else "rollback incomplete"
            )
            raise DoctorError(
                f"obsolete cleanup failed; {suffix}; receipt: {rollback_receipt}"
            ) from exc

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
    "runtime": repair_runtime,
    "database": repair_database,
    "workers": repair_workers,
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
            raise DoctorError(
                f"{exc}; started receipt: {started_receipt}; failure receipt: {receipt}"
            ) from exc
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
