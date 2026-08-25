"""Operator-facing diagnosis and bounded repair for the local TGW substrate.

``tgw doctor`` is deliberately independent of tgw-prod and provider effects.  It
compares the local machine with the identities already declared by the current
task, context snapshot, coding configuration, and immutable runtime.  Repair mode
may only restore those declared bytes and identities; it cannot invent Plan intent,
delete work, change application data, or widen an actor's authority.
"""

from __future__ import annotations

import argparse
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
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

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
    context_catalog: Path = Path("/opt/TGW/tgw-lib/config/tgw-context-debian-v1.json")
    receipts: Path = Path("/opt/TGW/tgw-lib/doctor-receipts")
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
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=cwd,
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


def check_unix_access(paths: DoctorPaths) -> dict[str, Any]:
    try:
        actor = pwd.getpwuid(os.geteuid()).pw_name
        group = grp.getgrnam("tgw-coders")
        memberships = set(os.getgroups()) | {os.getegid()}
        member = group.gr_gid in memberships or actor in group.gr_mem
        writable = {
            "repository": os.access(paths.repository, os.R_OK | os.W_OK | os.X_OK),
            "worktree_root": os.access(paths.worktrees, os.R_OK | os.W_OK | os.X_OK),
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


def check_database(paths: DoctorPaths) -> dict[str, Any]:
    repair = "sudo -n tgw doctor repair database"
    try:
        config = _coding_config(paths)
        with psycopg2.connect(config["postgres_dsn"]) as connection:
            with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT current_user AS actor,
                           has_database_privilege(current_user, current_database(), 'CONNECT') AS database_connect,
                           has_schema_privilege(current_user, 'public', 'USAGE') AS schema_usage,
                           pg_has_role(current_user, 'tgw_coding', 'member') AS role_member,
                           has_table_privilege(current_user, 'public.todo_items', 'SELECT,INSERT,UPDATE,DELETE') AS todo_access,
                           has_table_privilege(current_user, 'public.queue_jobs', 'SELECT,INSERT,UPDATE,DELETE') AS queue_access,
                           has_table_privilege(current_user, 'public.queue_job_history', 'SELECT,INSERT') AS history_access,
                           has_sequence_privilege(current_user, 'public.todo_items_id_seq', 'USAGE,SELECT,UPDATE') AS todo_sequence_access,
                           has_sequence_privilege(current_user, 'public.queue_job_history_history_id_seq', 'USAGE,SELECT,UPDATE') AS history_sequence_access,
                           COALESCE(has_function_privilege(current_user, to_regprocedure('public.claim_queue_jobs(text,text,integer,integer)'), 'EXECUTE'), false) AS claim_function_access,
                           COALESCE(has_function_privilege(current_user, to_regprocedure('public.recover_expired_jobs()'), 'EXECUTE'), false) AS recovery_function_access
                    """
                )
                row = dict(cursor.fetchone())
                cursor.execute(
                    "SELECT count(*) AS active FROM public.queue_jobs "
                    "WHERE queue_name IN ('codex-implement','controller-verify') "
                    "AND state IN ('queued','leased','running')"
                )
                active = int(cursor.fetchone()["active"])
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


def check_obsolete_surfaces(paths: DoctorPaths) -> dict[str, Any]:
    known = (
        Path("/usr/local/bin/tgw-coding"),
        Path("/usr/local/bin/tgw-coding-helper"),
    )
    visible = [str(path) for path in known if path.exists() or path.is_symlink()]
    return _check(
        "cleanup.obsolete-active-surfaces",
        "WARN" if visible else "PASS",
        "obsolete coding commands remain in active PATH: " + ", ".join(visible)
        if visible
        else "no known obsolete coding command remains in active PATH",
        evidence={"visible": visible},
        repair="tgw doctor inventory, then move verified obsolete commands to the recovery archive"
        if visible
        else None,
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
            ]
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


_REPAIRS: dict[str, Callable[[DoctorPaths], dict[str, Any]]] = {
    "context": repair_context,
    "runtime": repair_runtime,
    "database": repair_database,
    "workers": repair_workers,
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
