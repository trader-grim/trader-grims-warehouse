"""Protection and identity checks for retained actor Context source.

The Context server executes and reads one retained Git materialization for a long
time.  A clean ``git status`` alone is not an immutability boundary: an actor
that owns the worktree, its index, or its object database can change what later
Git commands mean.  This module therefore accepts only root-owned, non-writable
materializations below the dedicated root-readable retained-source store and
runs Git with a closed environment.  Root-private transaction state is a
separate sibling and can never be selected as executable source.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_RETAINED_SOURCE_ROOT = Path(
    "/var/lib/tgw/context-update/retained-sources"
)
_PROTECTED_STATE_ROOT = Path("/var/lib/tgw")
_SCRATCH_ROOT = Path("/opt/TGW/var/tmp")


class ContextSourceGuardError(ValueError):
    """The retained Context source is mutable, ambiguous, or not exact."""


def closed_git_environment(git: str | Path) -> dict[str, str]:
    """Return an environment with no inherited Git configuration selectors."""

    executable = Path(git)
    if not executable.is_absolute():
        raise ContextSourceGuardError("Context Git executable must be absolute")
    return {
        "PATH": f"{executable.parent}:/usr/bin:/bin",
        "HOME": "/var/empty",
        "XDG_CONFIG_HOME": "/var/empty",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_LAZY_FETCH": "1",
    }


def _protected(
    path: Path,
    label: str,
    *,
    directory: bool | None = None,
    single_link: bool = True,
) -> None:
    try:
        observed = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ContextSourceGuardError(f"{label} is unavailable") from exc
    if path.is_symlink() or observed.st_uid != 0 or observed.st_mode & 0o022:
        raise ContextSourceGuardError(f"{label} is not root-owned immutable material")
    if directory is True and not stat.S_ISDIR(observed.st_mode):
        raise ContextSourceGuardError(f"{label} is not a directory")
    if directory is False and not stat.S_ISREG(observed.st_mode):
        raise ContextSourceGuardError(f"{label} is not a regular file")
    if directory is None and not (stat.S_ISDIR(observed.st_mode) or stat.S_ISREG(observed.st_mode)):
        raise ContextSourceGuardError(f"{label} has an unsafe file type")
    if stat.S_ISDIR(observed.st_mode) and observed.st_mode & 0o005 != 0o005:
        raise ContextSourceGuardError(f"{label} is not actor-readable material")
    if stat.S_ISREG(observed.st_mode) and observed.st_mode & 0o004 != 0o004:
        raise ContextSourceGuardError(f"{label} is not actor-readable material")
    if single_link and stat.S_ISREG(observed.st_mode) and observed.st_nlink != 1:
        raise ContextSourceGuardError(f"{label} contains a hard-linked regular file")


def _protected_ancestry(path: Path, label: str) -> None:
    if (
        _RETAINED_SOURCE_ROOT
        != _PROTECTED_STATE_ROOT / "context-update" / "retained-sources"
        or path != _RETAINED_SOURCE_ROOT
        and _RETAINED_SOURCE_ROOT not in path.parents
    ):
        raise ContextSourceGuardError(
            f"{label} escapes the retained Context source store"
        )
    current = path
    while True:
        _protected(current, label, directory=True)
        if current == Path("/"):
            return
        if current.parent == current:
            raise ContextSourceGuardError(
                f"{label} escapes the retained Context source store"
            )
        current = current.parent


def _scan_tree(root: Path, label: str, *, skip_git_entry: bool = False) -> None:
    """Reject writable owners and indirections anywhere in an authority tree."""

    _protected(root, label, directory=True)
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        if skip_git_entry and current_path == root:
            directories[:] = [name for name in directories if name != ".git"]
            files[:] = [name for name in files if name != ".git"]
        for name in directories:
            _protected(current_path / name, label, directory=True)
        for name in files:
            _protected(current_path / name, label, directory=False)


def _git_directory(source: Path) -> tuple[Path, Path]:
    marker = source / ".git"
    if marker.is_dir() and not marker.is_symlink():
        git_dir = marker.resolve(strict=True)
    else:
        _protected(marker, "Context Git worktree marker", directory=False)
        try:
            line = marker.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ContextSourceGuardError("Context Git worktree marker is invalid") from exc
        if not line.startswith("gitdir: "):
            raise ContextSourceGuardError("Context Git worktree marker is invalid")
        raw = Path(line.removeprefix("gitdir: "))
        git_dir = (raw if raw.is_absolute() else marker.parent / raw).resolve(strict=True)
    _protected_ancestry(git_dir, "Context Git directory")
    common_marker = git_dir / "commondir"
    if common_marker.exists() or common_marker.is_symlink():
        _protected(common_marker, "Context Git common-directory marker", directory=False)
        raw_common = Path(common_marker.read_text(encoding="utf-8").strip())
        common = (raw_common if raw_common.is_absolute() else git_dir / raw_common).resolve(strict=True)
    else:
        common = git_dir
    _protected_ancestry(common, "Context Git common directory")
    return git_dir, common


def _git(git: Path, source: Path, *arguments: str, bytes_output: bool = False) -> str | bytes:
    completed = subprocess.run(
        [
            str(git),
            "-c", f"safe.directory={source}",
            "-c", "core.fsmonitor=false",
            "-c", "core.hooksPath=/dev/null",
            "-C", str(source),
            *arguments,
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        env=closed_git_environment(git),
    )
    if completed.returncode:
        reason = completed.stderr.decode(errors="replace").strip()
        raise ContextSourceGuardError(reason or "Context Git identity check failed")
    return completed.stdout if bytes_output else completed.stdout.decode().strip()


def validate_context_source(
    source: str | Path,
    git: str | Path,
    *,
    expected_commit: str | None = None,
    expected_tree: str | None = None,
) -> tuple[Path, str, str]:
    """Validate one clean, exact, actor-immutable retained Git materialization."""

    raw_source = Path(source)
    if (
        not raw_source.is_absolute()
        or raw_source == Path("/tmp")
        or Path("/tmp") in raw_source.parents
        or raw_source == _SCRATCH_ROOT
        or _SCRATCH_ROOT in raw_source.parents
    ):
        raise ContextSourceGuardError("Context source must be retained outside scratch storage")
    try:
        resolved = raw_source.resolve(strict=True)
        executable = Path(git).resolve(strict=True)
    except OSError as exc:
        raise ContextSourceGuardError("Context source or Git executable is unavailable") from exc
    if (
        resolved != raw_source
        or resolved == _RETAINED_SOURCE_ROOT
        or _RETAINED_SOURCE_ROOT not in resolved.parents
    ):
        raise ContextSourceGuardError(
            "Context source must be below the canonical retained-source root"
        )
    _protected_ancestry(resolved, "Context source ancestry")
    # Nix-store executables can legitimately be hard-linked by optimization;
    # they are hash-checked by the catalog caller and are not Git authority.
    _protected(executable, "Context Git executable", directory=False, single_link=False)
    git_dir, common = _git_directory(resolved)
    if (common / "objects" / "info" / "alternates").exists():
        raise ContextSourceGuardError("Context Git alternate object databases are forbidden")
    _scan_tree(resolved, "Context tracked material", skip_git_entry=True)
    _scan_tree(git_dir, "Context Git metadata")
    if common != git_dir:
        _scan_tree(common, "Context Git common metadata")

    top = Path(str(_git(executable, resolved, "rev-parse", "--show-toplevel"))).resolve(strict=True)
    observed_git = Path(str(_git(executable, resolved, "rev-parse", "--git-dir")))
    if not observed_git.is_absolute():
        observed_git = resolved / observed_git
    observed_common = Path(str(_git(executable, resolved, "rev-parse", "--git-common-dir")))
    if not observed_common.is_absolute():
        observed_common = resolved / observed_common
    commit = str(_git(executable, resolved, "rev-parse", "HEAD^{commit}"))
    tree = str(_git(executable, resolved, "rev-parse", "HEAD^{tree}"))
    status_value = str(_git(executable, resolved, "status", "--porcelain=v1", "--untracked-files=all"))
    staged = bytes(_git(executable, resolved, "ls-files", "-z", "--stage", bytes_output=True))
    if (
        top != resolved
        or observed_git.resolve(strict=True) != git_dir
        or observed_common.resolve(strict=True) != common
        or _COMMIT.fullmatch(commit) is None
        or _COMMIT.fullmatch(tree) is None
        or status_value
        or (expected_commit is not None and commit != expected_commit)
        or (expected_tree is not None and tree != expected_tree)
    ):
        raise ContextSourceGuardError("Context source is stale, dirty, or ambiguously materialized")
    for row in staged.split(b"\0"):
        if not row:
            continue
        try:
            metadata, raw_path = row.split(b"\t", 1)
            mode = metadata.split(b" ", 1)[0]
            relative = Path(raw_path.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ContextSourceGuardError("Context tracked-file index is invalid") from exc
        target = resolved / relative
        if (
            mode in {b"120000", b"160000"}
            or relative.is_absolute()
            or ".." in relative.parts
            or target.is_symlink()
            or not target.is_file()
        ):
            raise ContextSourceGuardError("Context tracked material contains an unsafe indirection")
    return resolved, commit, tree
