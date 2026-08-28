"""Deterministic Git reads for the shared local development repository.

The source repository is intentionally writable by ``tgw-coders``. Service
accounts therefore ignore ambient Git configuration, replacement refs, hooks,
and fsmonitor commands when they verify or archive a candidate.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import stat
import subprocess
import tarfile
from pathlib import Path
from typing import Mapping

GIT_EXECUTABLE = "/usr/bin/git"
_OBJECT = re.compile(r"[0-9a-f]{40}\Z")


def protected_git_environment() -> Mapping[str, str]:
    """Return the minimal deterministic environment for service-side Git."""

    return {
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": "/nonexistent",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
    }


def protected_git_command(repository: Path, *arguments: str) -> list[str]:
    """Build a no-replacement, no-hook command bound to one worktree."""

    resolved = repository.resolve(strict=True)
    return [
        GIT_EXECUTABLE,
        "--no-replace-objects",
        "-c",
        f"safe.directory={resolved}",
        "-c",
        f"core.worktree={resolved}",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.attributesFile=/dev/null",
        *arguments,
    ]


def _object(repository: Path, kind: str, oid: str) -> bytes:
    if kind not in {"commit", "tree", "blob"} or _OBJECT.fullmatch(oid) is None:
        raise ValueError("invalid exact Git object request")
    completed = subprocess.run(
        protected_git_command(repository, "cat-file", kind, oid),
        cwd=repository,
        env=dict(protected_git_environment()),
        check=False,
        capture_output=True,
    )
    if completed.returncode:
        raise ValueError("exact Git object is unavailable")
    body = completed.stdout
    framed = kind.encode() + b" " + str(len(body)).encode() + b"\0" + body
    if hashlib.sha1(framed, usedforsecurity=False).hexdigest() != oid:
        raise ValueError("exact Git object content does not match its identity")
    return body


def _tree_files(
    repository: Path, tree: str, prefix: tuple[str, ...] = ()
) -> list[tuple[str, int, bytes]]:
    body = _object(repository, "tree", tree)
    offset = 0
    files: list[tuple[str, int, bytes]] = []
    while offset < len(body):
        separator = body.find(b" ", offset)
        terminator = body.find(b"\0", separator + 1)
        if separator < 0 or terminator < 0 or terminator + 21 > len(body):
            raise ValueError("exact Git tree encoding is invalid")
        try:
            mode = body[offset:separator].decode("ascii")
            name = body[separator + 1 : terminator].decode("utf-8", "strict")
        except UnicodeError as exc:
            raise ValueError("exact Git tree path is not UTF-8") from exc
        oid = body[terminator + 1 : terminator + 21].hex()
        offset = terminator + 21
        if not name or name in {".", ".."} or "/" in name:
            raise ValueError("exact Git tree path is unsafe")
        path = (*prefix, name)
        if mode == "40000":
            files.extend(_tree_files(repository, oid, path))
        elif mode in {"100644", "100755"}:
            files.append(("/".join(path), 0o755 if mode == "100755" else 0o644, _object(repository, "blob", oid)))
        else:
            raise ValueError("exact Git tree contains a link or unsupported entry")
    return files


def write_exact_tree_archive(
    repository: Path,
    *,
    commit: str,
    tree: str,
    destination: Path,
) -> None:
    """Write a deterministic tar from verified Git objects, ignoring Git config.

    Git is used only as an object decompressor. Every returned commit, tree,
    subtree, and blob is re-hashed before use, and archive membership is parsed
    directly from tree objects. Repository-local config and info attributes
    therefore cannot add, omit, filter, or change released bytes.
    """

    if _OBJECT.fullmatch(commit) is None or _OBJECT.fullmatch(tree) is None:
        raise ValueError("exact Git commit/tree identity is invalid")
    commit_body = _object(repository, "commit", commit)
    first = commit_body.partition(b"\n")[0]
    if first != f"tree {tree}".encode():
        raise ValueError("exact Git commit does not bind the requested tree")
    files = _tree_files(repository, tree)
    descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    try:
        state = os.fstat(descriptor)
        if (
            not stat.S_ISREG(state.st_mode)
            or state.st_nlink != 1
            or state.st_uid != os.geteuid()
        ):
            raise ValueError("exact Git archive destination is unsafe")
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            with tarfile.open(
                fileobj=stream,
                mode="w",
                format=tarfile.PAX_FORMAT,
                pax_headers={"comment": commit},
            ) as archive:
                for name, mode, body in files:
                    info = tarfile.TarInfo(name)
                    info.size = len(body)
                    info.mode = mode
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    archive.addfile(info, io.BytesIO(body))
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
