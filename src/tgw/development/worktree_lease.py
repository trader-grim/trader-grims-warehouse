"""One ordinary-Unix-user lease for a request-bound TGW Git worktree."""

from __future__ import annotations

import fcntl
import os
import stat
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from tgw.errors import HardFailure


class WorktreeLeaseBusy(HardFailure):
    """Another cooperating TGW actor currently owns this worktree."""


def _metadata_directory(worktree: Path) -> Path:
    """Return a verified, non-symlinked Git metadata directory for worktree."""
    resolved_worktree = worktree.resolve()
    completed = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={resolved_worktree}",
            "rev-parse",
            "--show-toplevel",
            "--absolute-git-dir",
            "--git-common-dir",
        ],
        cwd=resolved_worktree,
        check=False,
        text=True,
        capture_output=True,
    )
    values = completed.stdout.splitlines()
    if completed.returncode or len(values) != 3:
        raise HardFailure("coding worktree lease cannot resolve exact Git metadata")
    top = Path(values[0]).resolve()
    raw_gitdir = Path(values[1])
    raw_common = Path(values[2])
    if not raw_gitdir.is_absolute():
        raise HardFailure("coding worktree lease received a relative Git metadata path")
    if not raw_common.is_absolute():
        raw_common = resolved_worktree / raw_common
    gitdir = raw_gitdir.resolve()
    common = raw_common.resolve()
    if top != resolved_worktree:
        raise HardFailure("coding worktree lease target is not the Git top-level")
    if raw_gitdir != gitdir or raw_common.absolute() != common:
        raise HardFailure("coding worktree lease refuses symlinked Git metadata")
    if gitdir != common and gitdir.parent != common / "worktrees":
        raise HardFailure("coding worktree lease metadata escapes the repository")

    worktree_stat = os.stat(resolved_worktree, follow_symlinks=False)
    metadata_stat = os.stat(gitdir, follow_symlinks=False)
    if not stat.S_ISDIR(metadata_stat.st_mode):
        raise HardFailure("coding worktree lease metadata is not a directory")
    if metadata_stat.st_gid != worktree_stat.st_gid:
        raise HardFailure("coding worktree lease group differs from its worktree")
    if metadata_stat.st_mode & stat.S_IWOTH:
        raise HardFailure("coding worktree lease metadata is world-writable")
    if (
        metadata_stat.st_uid != os.geteuid()
        and metadata_stat.st_gid not in os.getgroups()
    ):
        raise HardFailure("coding worktree lease metadata is not shared with this Unix actor")
    return gitdir


@contextmanager
def exclusive_worktree_lease(worktree: Path) -> Iterator[None]:
    """Lock the verified Git worktree-metadata inode, not a replaceable file."""
    gitdir = _metadata_directory(worktree)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(gitdir, flags)
    except OSError as exc:
        raise HardFailure(f"coding worktree lease cannot open Git metadata: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        current = os.stat(gitdir, follow_symlinks=False)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise HardFailure("coding worktree lease metadata changed during open")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WorktreeLeaseBusy("coding implementation worktree is already leased") from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
