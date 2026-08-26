from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tgw.development import worktree_lease
from tgw.errors import HardFailure


def _stat(*, mode: int = stat.S_IFDIR | 0o750, uid: int = 1001, gid: int = 2001):
    return SimpleNamespace(st_mode=mode, st_uid=uid, st_gid=gid)


def _git_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    metadata_stat=None,
    worktree_stat=None,
    gitdir: Path | None = None,
    common: Path | None = None,
) -> tuple[Path, Path]:
    worktree = tmp_path / "checkout"
    worktree.mkdir()
    gitdir = gitdir or (tmp_path / "repository.git")
    common = common or gitdir
    gitdir.mkdir(parents=True, exist_ok=True)
    common.mkdir(parents=True, exist_ok=True)
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=f"{worktree}\n{gitdir}\n{common}\n",
        stderr="",
    )
    monkeypatch.setattr(worktree_lease.subprocess, "run", lambda *args, **kwargs: completed)
    stats = {
        os.fspath(worktree.resolve()): worktree_stat or _stat(),
        os.fspath(gitdir.resolve()): metadata_stat or _stat(),
    }
    original_stat = os.stat

    def fake_stat(path, *, follow_symlinks=False):
        if result := stats.get(os.fspath(path)):
            return result
        return original_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(
        worktree_lease.os,
        "stat",
        fake_stat,
    )
    return worktree, gitdir


def test_root_may_use_already_validated_metadata_with_only_root_group(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    worktree, gitdir = _git_metadata(monkeypatch, tmp_path)
    monkeypatch.setattr(worktree_lease.os, "geteuid", lambda: 0)
    monkeypatch.setattr(worktree_lease.os, "getgroups", lambda: [0])

    assert worktree_lease._metadata_directory(worktree) == gitdir


def test_unrelated_ordinary_actor_still_cannot_use_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    worktree, _ = _git_metadata(monkeypatch, tmp_path)
    monkeypatch.setattr(worktree_lease.os, "geteuid", lambda: 3001)
    monkeypatch.setattr(worktree_lease.os, "getgroups", lambda: [3001])

    with pytest.raises(HardFailure, match="not shared with this Unix actor"):
        worktree_lease._metadata_directory(worktree)


def test_metadata_group_actor_still_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    worktree, gitdir = _git_metadata(monkeypatch, tmp_path)
    monkeypatch.setattr(worktree_lease.os, "geteuid", lambda: 3001)
    monkeypatch.setattr(worktree_lease.os, "getgroups", lambda: [2001, 3001])

    assert worktree_lease._metadata_directory(worktree) == gitdir


@pytest.mark.parametrize(
    ("metadata_stat", "worktree_stat", "message"),
    [
        (_stat(gid=2002), _stat(gid=2001), "group differs from its worktree"),
        (_stat(mode=stat.S_IFDIR | 0o752), _stat(), "metadata is world-writable"),
    ],
)
def test_root_does_not_bypass_validated_metadata_invariants(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    metadata_stat,
    worktree_stat,
    message: str,
) -> None:
    worktree, _ = _git_metadata(
        monkeypatch,
        tmp_path,
        metadata_stat=metadata_stat,
        worktree_stat=worktree_stat,
    )
    monkeypatch.setattr(worktree_lease.os, "geteuid", lambda: 0)
    monkeypatch.setattr(worktree_lease.os, "getgroups", lambda: [0])

    with pytest.raises(HardFailure, match=message):
        worktree_lease._metadata_directory(worktree)


def test_root_does_not_bypass_symlink_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "actual.git"
    target.mkdir()
    symlink = tmp_path / "linked.git"
    symlink.symlink_to(target, target_is_directory=True)
    worktree, _ = _git_metadata(monkeypatch, tmp_path, gitdir=symlink, common=symlink)
    monkeypatch.setattr(worktree_lease.os, "geteuid", lambda: 0)
    monkeypatch.setattr(worktree_lease.os, "getgroups", lambda: [0])

    with pytest.raises(HardFailure, match="refuses symlinked Git metadata"):
        worktree_lease._metadata_directory(worktree)


def test_root_does_not_bypass_linked_worktree_containment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gitdir = tmp_path / "escaped" / "entry"
    common = tmp_path / "repository.git"
    worktree, _ = _git_metadata(
        monkeypatch, tmp_path, gitdir=gitdir, common=common
    )
    monkeypatch.setattr(worktree_lease.os, "geteuid", lambda: 0)
    monkeypatch.setattr(worktree_lease.os, "getgroups", lambda: [0])

    with pytest.raises(HardFailure, match="metadata escapes the repository"):
        worktree_lease._metadata_directory(worktree)
