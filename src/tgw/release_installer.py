"""Immutable TGW source-release materializer and selector.

All state changes are confined beneath an explicit TGW root.  Selection is a
compare-and-swap against the exact current generation and is recorded by a
durable intent before the atomic symlink replacement.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import pwd
import re
import secrets
import shutil
import stat
import tarfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator, Mapping

SCHEMA = "tgw-release-manifest-v1"
RECEIPT_SCHEMA = "tgw-immutable-release-selection-v1"
REFUSAL_SCHEMA = "tgw-immutable-release-refusal-v1"
RUNTIME_SCHEMA = "tgw-release-runtime-files-v1"
OWNERSHIP_PROMOTION_SCHEMA = "tgw-immutable-release-ownership-promotion-v1"
_RELEASE_DIR_MODE = 0o555
_RELEASE_FILE_MODES = frozenset({0o444, 0o555})
_HEX = re.compile(r"^[0-9a-f]+$")
_GENERATION = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
_RESERVED_GENERATIONS = frozenset({"current", "releases", "operations", "receipts", "refusals"})
_RESERVED_GENERATION_PREFIXES = (".stage-", ".current-")


class ReleaseError(RuntimeError):
    """A release could not be safely materialized or selected."""


class OwnerDirectEvidenceError(ValueError):
    """An owner-direct integrity receipt is malformed or mismatched."""


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _canonical_object(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode()


def _object_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_object(value)).hexdigest()


def _digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, body: bytes) -> None:
    view = memoryview(body)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise ReleaseError("short write while materializing release")
        view = view[written:]


def _fsync_tree(root: Path) -> None:
    directories = [root, *(path for path in root.rglob("*") if path.is_dir())]
    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        _fsync_dir(path)


def _discard_stage(stage: Path) -> None:
    if not stage.exists():
        return
    for path in stage.rglob("*"):
        if path.is_symlink():
            continue
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
    os.chmod(stage, 0o700)
    shutil.rmtree(stage)


def _atomic_json(path: Path, value: dict[str, Any], mode: int = 0o444) -> None:
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        _write_all(descriptor, _canonical(value))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(temporary, mode)
    os.replace(temporary, path)
    _fsync_dir(path.parent)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"invalid JSON at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseError(f"JSON object required at {path}")
    return value


def _read_trusted_public_key(path: Path) -> bytes:
    """Read an exact root-owned raw Ed25519 authority key."""
    try:
        metadata = path.lstat()
        raw = path.read_bytes()
    except OSError as exc:
        raise ReleaseError("trusted receipt public key is unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_mode & 0o022
        or len(raw) != 32
    ):
        raise ReleaseError("trusted receipt public key is not root-owned immutable authority")
    return raw


def _identity(name: str, value: str, length: int) -> str:
    if len(value) != length or not _HEX.fullmatch(value):
        raise ReleaseError(f"{name} must be {length} lowercase hexadecimal characters")
    return value


def _generation(value: str) -> str:
    if (
        not _GENERATION.fullmatch(value)
        or value in _RESERVED_GENERATIONS
        or value.startswith(_RESERVED_GENERATION_PREFIXES)
    ):
        raise ReleaseError("unsafe generation name")
    return value


def runtime_manifest_identity(
    generation: str,
    files: Mapping[str, str],
) -> dict[str, Any]:
    """Return the exact composite overlay manifest bound to one generation."""
    generation = _generation(generation)
    normalized: dict[str, str] = {}
    for relative, digest in files.items():
        path = PurePosixPath(str(relative))
        if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "config" or len(str(digest)) != 64 or not _HEX.fullmatch(str(digest)):
            raise ReleaseError("runtime manifest input is invalid")
        normalized[path.as_posix()] = str(digest)
    if not normalized:
        raise ReleaseError("runtime manifest input is absent")
    unsigned = {
        "schema": RUNTIME_SCHEMA,
        "generation": generation,
        "files": dict(sorted(normalized.items())),
    }
    return {
        **unsigned,
        "manifest_sha256": hashlib.sha256(_canonical(unsigned)).hexdigest(),
    }


def _layout(root: Path) -> None:
    if root.is_symlink():
        raise ReleaseError("TGW root must not be a symlink")
    root_created = not root.exists()
    root.mkdir(parents=True, exist_ok=True)
    if root_created:
        os.chmod(root, 0o750)
    expected_uid = os.geteuid()
    expected_gid = os.getegid()
    for name in ("releases", "operations", "receipts", "refusals"):
        path = root / name
        created = not path.exists()
        path.mkdir(exist_ok=True)
        if created:
            os.chmod(path, 0o750)
    for path in (root, *(root / name for name in ("releases", "operations", "receipts", "refusals"))):
        state = path.stat(follow_symlinks=False)
        if (
            path.is_symlink()
            or not stat.S_ISDIR(state.st_mode)
            or state.st_uid != expected_uid
            or state.st_gid != expected_gid
            or state.st_mode & 0o022
        ):
            raise ReleaseError(f"unsafe release layout path: {path}")


@contextmanager
def _lock(root: Path) -> Iterator[None]:
    _layout(root)
    descriptor = os.open(
        root / "operations" / ".selector.lock",
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def current_generation(root: Path) -> str | None:
    current = root / "current"
    if not current.exists() and not current.is_symlink():
        return None
    if not current.is_symlink():
        raise ReleaseError("current selector is not a symlink")
    target = PurePosixPath(os.readlink(current))
    if target.is_absolute() or len(target.parts) != 2 or target.parts[0] != "releases":
        raise ReleaseError("current selector escapes releases")
    generation = _generation(target.parts[1])
    release = (root / "releases" / generation).resolve(strict=True)
    if release.parent != (root / "releases").resolve(strict=True):
        raise ReleaseError("current selector escapes release root")
    return generation


def _safe_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    result: list[tarfile.TarInfo] = []
    seen: set[str] = set()
    for member in archive:
        path = PurePosixPath(member.name)
        if not member.name or path.is_absolute() or any(part in ("", ".", "..") for part in path.parts) or member.name in seen or not (member.isdir() or member.isreg()):
            raise ReleaseError(f"unsafe archive member: {member.name!r}")
        seen.add(member.name)
        result.append(member)
    if not result:
        raise ReleaseError("empty release archive")
    return result


def materialize(
    root: Path,
    archive_path: Path,
    *,
    generation: str,
    commit: str,
    tree: str,
    archive_sha256: str,
) -> dict[str, Any]:
    """Materialize one exact archive as an immutable, unselected release."""
    generation = _generation(generation)
    _identity("commit", commit, 40)
    _identity("tree", tree, 40)
    _identity("archive_sha256", archive_sha256, 64)
    if _digest(archive_path) != archive_sha256:
        raise ReleaseError("archive digest mismatch")
    _layout(root)
    final = root / "releases" / generation
    if final.exists():
        if final.is_symlink() or not final.is_dir():
            raise ReleaseError("unsafe existing release path")
        manifest = _read_json(final / ".release-manifest.json")
        if manifest.get("commit") != commit or manifest.get("git_tree") != tree or manifest.get("archive_sha256") != archive_sha256:
            raise ReleaseError("generation identity collision")
        verify(root, generation)
        return manifest
    stage = root / "releases" / f".stage-{generation}-{uuid.uuid4().hex}"
    stage.mkdir(mode=0o700)
    try:
        with tarfile.open(archive_path, "r:*") as archive:
            if archive.pax_headers.get("comment") != commit:
                raise ReleaseError("archive Git commit identity mismatch")
            members = _safe_members(archive)
            for member in members:
                target = stage.joinpath(*PurePosixPath(member.name).parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                source = archive.extractfile(member)
                if source is None:
                    raise ReleaseError(f"missing archive body: {member.name}")
                descriptor = os.open(
                    target,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                )
                try:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        _write_all(descriptor, chunk)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                os.chmod(target, 0o555 if member.mode & 0o111 else 0o444)
        files = {path.relative_to(stage).as_posix(): _digest(path) for path in sorted(stage.rglob("*")) if path.is_file()}
        if not files:
            raise ReleaseError("release archive contains no files")
        content_hash = hashlib.sha256(_canonical(dict(sorted(files.items())))).hexdigest()
        manifest = {
            "schema": SCHEMA,
            "generation": generation,
            "commit": commit,
            "tree": f"exact-git-archive:{commit}",
            "git_tree": tree,
            "src_root": "src",
            "archive_sha256": archive_sha256,
            "content_manifest_sha256": content_hash,
            "file_count": len(files),
            "files": files,
        }
        _atomic_json(stage / ".release-manifest.json", manifest)
        _fsync_tree(stage)
        for path in sorted(stage.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise ReleaseError("special release content rejected")
            if path.is_dir():
                os.chmod(path, 0o555)
        os.chmod(stage, 0o555)
        os.rename(stage, final)
        _fsync_dir(root / "releases")
        return manifest
    except Exception:
        _discard_stage(stage)
        raise


def verify(root: Path, generation: str) -> dict[str, Any]:
    generation = _generation(generation)
    release = root / "releases" / generation
    if release.is_symlink() or not release.is_dir():
        raise ReleaseError("release path is not an immutable directory")
    manifest = _read_json(release / ".release-manifest.json")
    if manifest.get("schema") != SCHEMA or manifest.get("generation") != generation:
        raise ReleaseError("release manifest identity is invalid")
    commit = manifest.get("commit")
    tree = manifest.get("git_tree")
    archive_sha256 = manifest.get("archive_sha256")
    if not isinstance(commit, str) or not isinstance(tree, str) or not isinstance(archive_sha256, str):
        raise ReleaseError("release manifest source identity is invalid")
    _identity("commit", commit, 40)
    _identity("tree", tree, 40)
    _identity("archive_sha256", archive_sha256, 64)
    expected = manifest.get("files")
    if not isinstance(expected, dict):
        raise ReleaseError("release files manifest is invalid")
    runtime_manifest_path = release / ".runtime-manifest.json"
    runtime_files: dict[str, str] = {}
    runtime_manifest_hash: str | None = None
    if runtime_manifest_path.exists():
        runtime = _read_json(runtime_manifest_path)
        if (
            set(runtime) != {"schema", "generation", "files", "manifest_sha256"}
            or runtime.get("schema") != RUNTIME_SCHEMA
            or runtime.get("generation") != generation
            or not isinstance(runtime.get("files"), dict)
        ):
            raise ReleaseError("release runtime manifest is invalid")
        unsigned_runtime = dict(runtime)
        runtime_manifest_hash = unsigned_runtime.pop("manifest_sha256")
        if runtime_manifest_hash != hashlib.sha256(_canonical(unsigned_runtime)).hexdigest():
            raise ReleaseError("release runtime manifest hash mismatch")
        for relative, digest in runtime["files"].items():
            runtime_path = PurePosixPath(str(relative))
            if runtime_path.is_absolute() or ".." in runtime_path.parts or not runtime_path.parts or not isinstance(digest, str) or len(digest) != 64 or not _HEX.fullmatch(digest):
                raise ReleaseError("release runtime file binding is invalid")
        runtime_files = dict(runtime["files"])
    actual: dict[str, str] = {}
    actual_runtime: dict[str, str] = {}
    for path in release.rglob("*"):
        relative = path.relative_to(release).as_posix()
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise ReleaseError(f"mutable or linked release path: {relative}")
        if path.is_file() and relative not in {".release-manifest.json", ".runtime-manifest.json"}:
            if relative in runtime_files:
                actual_runtime[relative] = _digest(path)
            else:
                actual[relative] = _digest(path)
    if actual != expected:
        raise ReleaseError("release content does not match manifest")
    content_hash = hashlib.sha256(_canonical(dict(sorted(actual.items())))).hexdigest()
    if content_hash != manifest.get("content_manifest_sha256"):
        raise ReleaseError("release content hash mismatch")
    if actual_runtime != runtime_files:
        raise ReleaseError("release runtime content does not match manifest")
    return {
        "generation": generation,
        "file_count": len(actual),
        "status": "PASS",
        "runtime_manifest_sha256": runtime_manifest_hash,
    }


_PROMOTE_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
_PROMOTE_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


def _release_tree_ownership(release: Path) -> tuple[bool, list[str]]:
    """Return whether every path under ``release`` is a non-writable owned inode.

    The first element is ``True`` only when the release root and every
    descendant is a directory (0555) or single-linked regular file (0444/0555)
    with no group/other write bit; the second lists any offending relatives.
    """
    unsafe: list[str] = []
    seen_root = False
    for path in (release, *sorted(release.rglob("*"))):
        relative = "." if path == release else str(path.relative_to(release))
        observed = path.stat(follow_symlinks=False)
        mode = stat.S_IMODE(observed.st_mode)
        if path.is_symlink():
            unsafe.append(relative + ":symlink")
        elif stat.S_ISDIR(observed.st_mode):
            if mode != _RELEASE_DIR_MODE:
                unsafe.append(relative + ":dir-mode")
        elif stat.S_ISREG(observed.st_mode):
            if mode not in _RELEASE_FILE_MODES:
                unsafe.append(relative + ":file-mode")
            if observed.st_nlink != 1:
                unsafe.append(relative + ":link-count")
        else:
            unsafe.append(relative + ":special")
        if observed.st_mode & 0o022 and not path.is_symlink():
            unsafe.append(relative + ":writable")
        if path == release:
            seen_root = stat.S_ISDIR(observed.st_mode)
    return (seen_root and not unsafe), unsafe


def _tree_is_owned_by(release: Path, *, uid: int, gid: int) -> bool:
    for path in (release, *release.rglob("*")):
        observed = path.stat(follow_symlinks=False)
        if path.is_symlink() or observed.st_uid != uid or observed.st_gid != gid:
            return False
    return True


def _promote_owner_recursive(
    descriptor: int,
    *,
    uid: int,
    gid: int,
    allowed_source_uids: frozenset[int],
) -> int:
    """Re-own one already-open release directory and its descendants.

    The directory descriptor binds every lookup to the opened inode even if a
    pathname is concurrently renamed.  Regular files are validated as
    single-linked immutable release content and replaced by a fresh
    ``uid:gid`` inode rather than path-chowned, so an external hard link can
    never receive a privileged ownership change.  Directory modes are made
    owner-private only while replacement inodes are published, then restored.
    """
    observed = os.fstat(descriptor)
    mode = stat.S_IMODE(observed.st_mode)
    if (
        not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid not in allowed_source_uids
        or mode != _RELEASE_DIR_MODE
    ):
        raise ReleaseError("release directory cannot be promoted safely")
    promoted = 0
    os.fchown(descriptor, uid, gid)
    os.fchmod(descriptor, 0o700)
    try:
        scan_fd = os.open(".", _PROMOTE_DIR_FLAGS, dir_fd=descriptor)
        try:
            held = os.fstat(descriptor)
            scanned = os.fstat(scan_fd)
            if (held.st_dev, held.st_ino) != (scanned.st_dev, scanned.st_ino):
                raise ReleaseError("release changed during ownership promotion")
            names = sorted(os.listdir(scan_fd))
        finally:
            os.close(scan_fd)
        for name in names:
            child = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(child.st_mode):
                child_fd = os.open(name, _PROMOTE_DIR_FLAGS, dir_fd=descriptor)
                try:
                    bound = os.fstat(child_fd)
                    if (bound.st_dev, bound.st_ino) != (child.st_dev, child.st_ino):
                        raise ReleaseError("release changed during ownership promotion")
                    promoted += _promote_owner_recursive(
                        child_fd,
                        uid=uid,
                        gid=gid,
                        allowed_source_uids=allowed_source_uids,
                    )
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(child.st_mode):
                promoted += _promote_owner_file(
                    descriptor,
                    name,
                    child,
                    uid=uid,
                    gid=gid,
                    allowed_source_uids=allowed_source_uids,
                )
            else:
                raise ReleaseError("release cannot be promoted safely")
    finally:
        os.fchmod(descriptor, mode)
    final = os.fstat(descriptor)
    if final.st_uid != uid or final.st_gid != gid or stat.S_IMODE(final.st_mode) != mode:
        raise ReleaseError("release ownership promotion is incomplete")
    os.fsync(descriptor)
    return promoted


def _promote_owner_file(
    parent_fd: int,
    name: str,
    expected: os.stat_result,
    *,
    uid: int,
    gid: int,
    allowed_source_uids: frozenset[int],
) -> int:
    mode = stat.S_IMODE(expected.st_mode)
    if (
        not stat.S_ISREG(expected.st_mode)
        or expected.st_uid not in allowed_source_uids
        or expected.st_nlink != 1
        or mode not in _RELEASE_FILE_MODES
    ):
        raise ReleaseError("release file cannot be promoted safely")
    source_fd = os.open(name, _PROMOTE_FILE_FLAGS, dir_fd=parent_fd)
    temporary = f".tgw-release-promote-{secrets.token_hex(16)}"
    target_fd: int | None = None
    try:
        before = os.fstat(source_fd)
        if (before.st_dev, before.st_ino) != (expected.st_dev, expected.st_ino):
            raise ReleaseError("release changed during ownership promotion")
        target_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=parent_fd,
        )
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            _write_all(target_fd, chunk)
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
            raise ReleaseError("release changed during ownership promotion")
        os.fchown(target_fd, uid, gid)
        os.fchmod(target_fd, mode)
        os.fsync(target_fd)
        os.close(target_fd)
        target_fd = None
        os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        promoted = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(promoted.st_mode)
            or promoted.st_uid != uid
            or promoted.st_gid != gid
            or promoted.st_nlink != 1
            or stat.S_IMODE(promoted.st_mode) != mode
        ):
            raise ReleaseError("release ownership promotion is incomplete")
        return 1
    finally:
        if target_fd is not None:
            os.close(target_fd)
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        os.close(source_fd)


def _default_source_uids(uid: int) -> frozenset[int]:
    """Accounts a materialized-but-unpromoted release may currently be owned by."""
    candidates = {uid, os.geteuid()}
    for name in ("db",):
        try:
            candidates.add(pwd.getpwnam(name).pw_uid)
        except KeyError:
            pass
    return frozenset(candidates)


def promote_release_ownership(
    root: Path,
    generation: str,
    *,
    uid: int = 0,
    gid: int = 0,
    source_uids: frozenset[int] | None = None,
) -> dict[str, Any]:
    """Re-own an already materialized immutable release to ``uid:gid``.

    ``materialize`` already lands 0555 directories and 0444/0555 files, but an
    unprivileged materializer leaves them owned by its own account.  A cold
    Doctor/Context launcher requires the selected release tree to be
    ``root:root`` immutable, so this promotion re-owns every inode without
    widening any mode and re-verifies the exact-tree and manifest invariants
    before and after.  It is idempotent: a release already owned by ``uid:gid``
    is verified and returned untouched.
    """
    generation = _generation(generation)
    releases_root = root / "releases"
    resolved_releases = releases_root.resolve(strict=True)
    release = releases_root / generation
    if release.is_symlink() or release.resolve(strict=True).parent != resolved_releases:
        raise ReleaseError("release path escapes the immutable releases directory")
    safe, unsafe = _release_tree_ownership(release)
    if not safe:
        raise ReleaseError("release tree is not immutable: " + ",".join(unsafe[:8]))
    verify(root, generation)
    if _tree_is_owned_by(release, uid=uid, gid=gid):
        return {
            "schema": OWNERSHIP_PROMOTION_SCHEMA,
            "generation": generation,
            "uid": uid,
            "gid": gid,
            "promoted_inodes": 0,
            "already_owned": True,
            "verification": verify(root, generation),
        }
    allowed_source_uids = source_uids or _default_source_uids(uid)
    release_fd = os.open(release, _PROMOTE_DIR_FLAGS)
    try:
        bound = os.fstat(release_fd)
        visible = release.stat(follow_symlinks=False)
        if (bound.st_dev, bound.st_ino) != (visible.st_dev, visible.st_ino):
            raise ReleaseError("release changed before ownership promotion")
        promoted = _promote_owner_recursive(
            release_fd,
            uid=uid,
            gid=gid,
            allowed_source_uids=allowed_source_uids,
        )
    finally:
        os.close(release_fd)
    final = release.stat(follow_symlinks=False)
    if (
        final.st_uid != uid
        or final.st_gid != gid
        or stat.S_IMODE(final.st_mode) != _RELEASE_DIR_MODE
        or not _tree_is_owned_by(release, uid=uid, gid=gid)
    ):
        raise ReleaseError("release ownership promotion is incomplete")
    return {
        "schema": OWNERSHIP_PROMOTION_SCHEMA,
        "generation": generation,
        "uid": uid,
        "gid": gid,
        "promoted_inodes": promoted,
        "already_owned": False,
        "verification": verify(root, generation),
    }


def install_runtime_files(root: Path, generation: str, files: Mapping[str, bytes]) -> dict[str, Any]:
    """Install exact host-owned config before an immutable generation is selected."""
    generation = _generation(generation)
    if not isinstance(files, Mapping) or not files:
        raise ReleaseError("runtime files are absent")
    normalized: dict[str, bytes] = {}
    for relative, content in files.items():
        path = PurePosixPath(str(relative))
        if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "config" or not isinstance(content, bytes) or not content or len(content) > 1024 * 1024:
            raise ReleaseError("runtime file is outside the exact config namespace")
        normalized[path.as_posix()] = content
    with _lock(root):
        if current_generation(root) == generation:
            raise ReleaseError("runtime files cannot be changed after generation selection")
        verify(root, generation)
        release = root / "releases" / generation
        manifest_path = release / ".runtime-manifest.json"
        expected_files = {path: hashlib.sha256(content).hexdigest() for path, content in sorted(normalized.items())}
        runtime_manifest = runtime_manifest_identity(generation, expected_files)
        if manifest_path.exists():
            if _read_json(manifest_path) != runtime_manifest:
                raise ReleaseError("runtime generation identity collision")
            verification = verify(root, generation)
            return {**verification, "verification_status": verification["status"], "status": "already-installed"}
        created: list[Path] = []
        created_directories: list[Path] = []
        original_directory_modes: dict[Path, int] = {}
        try:
            original_directory_modes[release] = stat.S_IMODE(release.stat().st_mode)
            os.chmod(release, 0o755)
            for relative, content in normalized.items():
                target = release.joinpath(*PurePosixPath(relative).parts)
                current = release
                for component in PurePosixPath(relative).parts[:-1]:
                    current = current / component
                    if not current.exists():
                        current.mkdir(mode=0o700)
                        created_directories.append(current)
                    elif current.is_symlink() or not current.is_dir():
                        raise ReleaseError("runtime file parent is unsafe")
                    else:
                        original_directory_modes.setdefault(
                            current,
                            stat.S_IMODE(current.stat().st_mode),
                        )
                    os.chmod(current, 0o700)
                descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
                try:
                    _write_all(descriptor, content)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                os.chmod(target, 0o400)
                created.append(target)
                for directory in reversed(created_directories):
                    os.chmod(directory, 0o555)
                for directory, mode in original_directory_modes.items():
                    if directory != release:
                        os.chmod(directory, mode)
            _atomic_json(manifest_path, runtime_manifest, mode=0o400)
            os.chmod(release, original_directory_modes[release])
            verification = verify(root, generation)
        except Exception:
            for target in reversed(created):
                try:
                    target.unlink()
                except OSError:
                    pass
            try:
                manifest_path.unlink()
            except OSError:
                pass
            for directory in reversed(created_directories):
                try:
                    os.chmod(directory, 0o700)
                    directory.rmdir()
                except OSError:
                    pass
            for directory, mode in reversed(tuple(original_directory_modes.items())):
                try:
                    os.chmod(directory, mode)
                except OSError:
                    pass
            raise
        return {**verification, "verification_status": verification["status"], "status": "installed"}


def _select(
    root: Path,
    generation: str,
    *,
    expected_current: str | None,
    operation_id: str,
    rollback_of: str | None = None,
    evidence_validator: Callable[[Mapping[str, Any]], None] | None = None,
    evidence_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """CAS-select an already materialized release and durably receipt it."""
    generation = _generation(generation)
    if expected_current is not None:
        expected_current = _generation(expected_current)
    if not _GENERATION.fullmatch(operation_id):
        raise ReleaseError("unsafe operation id")
    with _lock(root):
        if (root / "refusals" / f"{operation_id}.json").exists():
            raise ReleaseError("operation id was previously refused")
        operation_path = root / "operations" / f"{operation_id}.json"
        receipt_path = root / "receipts" / f"{operation_id}.json"
        if operation_path.exists():
            previous = _read_json(operation_path)
            if previous.get("state") == "completed":
                if (
                    previous.get("previous_generation") == expected_current
                    and previous.get("selected_generation") == generation
                    and previous.get("rollback_of") == rollback_of
                    and previous.get("evidence_identity")
                    == (dict(evidence_identity) if evidence_identity is not None else None)
                    and receipt_path.exists()
                    and _read_json(receipt_path) == previous
                    and current_generation(root) == generation
                ):
                    return previous
                raise ReleaseError("completed operation does not match selector state")
        observed = current_generation(root)
        if observed != expected_current:
            raise ReleaseError(f"current generation changed: expected {expected_current}, got {observed}")
        verify(root, generation)
        manifest = _read_json(root / "releases" / generation / ".release-manifest.json")
        # The final evidence check belongs inside the same selector lock as
        # the CAS observation and symlink swap. Validation before materialize
        # remains useful, but cannot authorize a later selection by itself.
        if evidence_validator is not None:
            evidence_validator(manifest)
        intent = {
            "schema": RECEIPT_SCHEMA,
            "state": "prepared",
            "operation_id": operation_id,
            "previous_generation": expected_current,
            "selected_generation": generation,
            "selected_commit": manifest.get("commit"),
            "selected_archive_sha256": manifest.get("archive_sha256"),
            "selected_content_manifest_sha256": manifest.get("content_manifest_sha256"),
            "selected_manifest_sha256": hashlib.sha256(_canonical(manifest)).hexdigest(),
        }
        if rollback_of is not None:
            intent["rollback_of"] = rollback_of
        if evidence_identity is not None:
            intent["evidence_identity"] = dict(evidence_identity)
        if operation_path.exists() and _read_json(operation_path) != intent:
            raise ReleaseError("operation id collision")
        if not operation_path.exists():
            _atomic_json(operation_path, intent, mode=0o600)
        temporary = root / f".current-{operation_id}-{uuid.uuid4().hex}"
        os.symlink(f"releases/{generation}", temporary)
        _fsync_dir(root)
        os.replace(temporary, root / "current")
        _fsync_dir(root)
        receipt = {**intent, "state": "completed"}
        _atomic_json(root / "receipts" / f"{operation_id}.json", receipt)
        _atomic_json(operation_path, receipt, mode=0o600)
    return receipt


def record_refusal(
    root: Path,
    generation: str,
    operation_id: str,
    *,
    reason: str,
) -> dict[str, Any]:
    """Durably bind a failed selection attempt to its exact operation id."""
    generation = _generation(generation)
    if not _GENERATION.fullmatch(operation_id) or not _GENERATION.fullmatch(reason):
        raise ReleaseError("unsafe refusal identity")
    with _lock(root):
        path = root / "refusals" / f"{operation_id}.json"
        unsigned = {
            "schema": REFUSAL_SCHEMA,
            "state": "REFUSED",
            "operation_id": operation_id,
            "generation": generation,
            "observed_current": current_generation(root),
            "reasons": [reason],
        }
        receipt = {**unsigned, "refusal_hash": "sha256:" + hashlib.sha256(_canonical(unsigned)[:-1]).hexdigest()}
        if path.exists():
            if _read_json(path) != receipt:
                raise ReleaseError("refusal operation id collision")
            return receipt
        _atomic_json(path, receipt)
        return receipt


def select(
    root: Path,
    generation: str,
    *,
    expected_current: str | None,
    operation_id: str,
    admission_receipt: Mapping[str, Any] | None = None,
    environment_preflight_receipt: Mapping[str, Any] | None = None,
    admission_public_key: bytes | None = None,
    environment_public_key: bytes | None = None,
    current_plan_commit: str | None = None,
    current_solution_hash: str | None = None,
    current_time: str | Callable[[], str] | None = None,
) -> dict[str, Any]:
    """Select only after revalidating exact W15/W16 evidence at this boundary."""
    from tgw.admission_recovery import (
        AdmissionRecoveryError,
        validate_environment_preflight_for_admission,
        validate_release_admission,
    )

    if (
        admission_receipt is None
        or environment_preflight_receipt is None
        or admission_public_key is None
        or environment_public_key is None
        or current_plan_commit is None
        or current_solution_hash is None
        or current_time is None
    ):
        record_refusal(root, generation, operation_id, reason="missing-admission-evidence")
        raise ReleaseError("release selection requires admission and environment-preflight receipts")
    def validate_evidence(manifest: Mapping[str, Any]) -> None:
        evidence_time = current_time() if callable(current_time) else current_time
        assert evidence_time is not None
        admission = validate_release_admission(
            admission_receipt,
            candidate_commit=manifest["commit"],
            candidate_tree=manifest["git_tree"],
            trusted_public_key=admission_public_key,
            current_time=evidence_time,
            current_plan_commit=current_plan_commit,
            current_solution_hash=current_solution_hash,
        )
        validate_environment_preflight_for_admission(
            environment_preflight_receipt,
            catalog_hash=admission["environment"]["catalog_hash"],
            receipt_hash=admission["environment"]["receipt_hash"],
            trusted_public_key=environment_public_key,
            current_time=evidence_time,
        )

    try:
        manifest = _read_json(root / "releases" / _generation(generation) / ".release-manifest.json")
        validate_evidence(manifest)
        return _select(
            root,
            generation,
            expected_current=expected_current,
            operation_id=operation_id,
            evidence_validator=validate_evidence,
        )
    except (AdmissionRecoveryError, KeyError) as exc:
        record_refusal(root, generation, operation_id, reason="invalid-admission-evidence")
        raise ReleaseError(f"release admission refused: {exc}") from exc


def select_owner_directed(
    root: Path,
    generation: str,
    *,
    expected_current: str | None,
    operation_id: str,
    admission_receipt: Mapping[str, Any],
    environment_preflight_receipt: Mapping[str, Any],
    admission_public_key: bytes,
    environment_public_key: bytes,
    current_plan_commit: str,
    current_solution_hash: str,
    current_time: str | Callable[[], str],
) -> dict[str, Any]:
    """CAS-select one exact owner-directed candidate without review masquerade."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    from tgw.admission_recovery import (
        AdmissionRecoveryError,
        validate_environment_preflight_for_admission,
    )

    def validate_evidence(manifest: Mapping[str, Any]) -> None:
        evidence_time = current_time() if callable(current_time) else current_time
        fields = {
            "schema", "request_id", "authority_mode", "authority_evidence",
            "candidate", "plan", "environment", "status", "activation",
            "operator_authentication", "issued_at", "expires_at",
            "signer_key_id", "receipt_hash", "signature",
        }
        authority_fields = {
            "schema", "authority_mode", "source_label", "observed_at",
            "directive_sha256", "review_disposition", "integrity_semantics",
            "authority_evidence_sha256",
        }
        if (
            set(admission_receipt) != fields
            or admission_receipt.get("schema")
            != "tgw-context-owner-directed-admission/v1"
            or admission_receipt.get("authority_mode") != "OWNER_DIRECT"
            or admission_receipt.get("status") != "ADMITTED_OWNER_DIRECT"
            or admission_receipt.get("activation") != "declarative-only"
            or admission_receipt.get("operator_authentication")
            != "NOT_PERFORMED_NOT_REQUIRED"
            or admission_receipt.get("signer_key_id")
            != "tgw-release-admission"
            or admission_receipt.get("candidate")
            != {"commit": manifest.get("commit"), "tree": manifest.get("git_tree")}
            or admission_receipt.get("plan")
            != {
                "commit": current_plan_commit,
                "solution_hash": current_solution_hash,
            }
            or not isinstance(admission_receipt.get("authority_evidence"), Mapping)
            or set(admission_receipt["authority_evidence"]) != authority_fields
        ):
            raise OwnerDirectEvidenceError("owner-directed admission binding differs")
        authority = dict(admission_receipt["authority_evidence"])
        authority_hash = authority.pop("authority_evidence_sha256", None)
        disposition = authority.get("review_disposition")
        disposition_unsigned = (
            dict(disposition) if isinstance(disposition, Mapping) else {}
        )
        disposition_hash = disposition_unsigned.pop("disposition_sha256", None)
        if (
            authority.get("schema")
            != "tgw-context-owner-directive-summary/v1"
            or authority.get("authority_mode") != "OWNER_DIRECT"
            or authority.get("integrity_semantics")
            != "PLATFORM_SIGNATURE_IS_NOT_OPERATOR_AUTHENTICATION"
            or not isinstance(authority.get("source_label"), str)
            or not re.fullmatch(r"operator-(?:conversation|console)", authority["source_label"])
            or not isinstance(authority.get("observed_at"), str)
            or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", str(authority.get("directive_sha256", ""))
            )
            or disposition_unsigned
            != {
                "schema": "tgw-context-review-disposition/v1",
                "authority_mode": "OWNER_DIRECT",
                "disposition": "NOT_APPLICABLE_OWNER_DIRECT",
                "directive_sha256": authority.get("directive_sha256"),
                "candidate": admission_receipt.get("candidate"),
                "plan": admission_receipt.get("plan"),
                "basis": "EXPLICIT_OWNER_DIRECTIVE",
            }
            or disposition_hash != _object_hash(disposition_unsigned)
            or authority_hash != _object_hash(authority)
        ):
            raise OwnerDirectEvidenceError("owner directive summary differs")
        signed = dict(admission_receipt)
        signature_text = signed.pop("signature", None)
        unsigned = dict(signed)
        receipt_hash = unsigned.pop("receipt_hash", None)
        try:
            issued = datetime.fromisoformat(
                str(admission_receipt["issued_at"]).replace("Z", "+00:00")
            )
            expires = datetime.fromisoformat(
                str(admission_receipt["expires_at"]).replace("Z", "+00:00")
            )
            observed = datetime.fromisoformat(str(evidence_time).replace("Z", "+00:00"))
            signature = base64.b64decode(str(signature_text), validate=True)
            key = Ed25519PublicKey.from_public_bytes(admission_public_key)
            key.verify(signature, _canonical_object(signed))
        except (TypeError, ValueError, KeyError) as exc:
            raise OwnerDirectEvidenceError(
                "owner-directed admission signature differs"
            ) from exc
        if (
            any(item.tzinfo is None or item.utcoffset() is None for item in (issued, expires, observed))
            or not issued <= observed < expires
            or receipt_hash != _object_hash(unsigned)
        ):
            raise OwnerDirectEvidenceError("owner-directed admission identity differs")
        environment = admission_receipt.get("environment")
        if not isinstance(environment, Mapping):
            raise OwnerDirectEvidenceError("owner-directed environment binding differs")
        validate_environment_preflight_for_admission(
            environment_preflight_receipt,
            catalog_hash=str(environment.get("catalog_hash", "")),
            receipt_hash=str(environment.get("receipt_hash", "")),
            trusted_public_key=environment_public_key,
            current_time=str(evidence_time),
        )

    try:
        manifest = _read_json(
            root / "releases" / _generation(generation) / ".release-manifest.json"
        )
        validate_evidence(manifest)
        authority = admission_receipt["authority_evidence"]
        return _select(
            root,
            generation,
            expected_current=expected_current,
            operation_id=operation_id,
            evidence_validator=validate_evidence,
            evidence_identity={
                "authority_mode": "OWNER_DIRECT",
                "authority_evidence_sha256": authority[
                    "authority_evidence_sha256"
                ],
                "admission_receipt_sha256": admission_receipt["receipt_hash"],
            },
        )
    except (AdmissionRecoveryError, KeyError, OwnerDirectEvidenceError) as exc:
        record_refusal(
            root, generation, operation_id, reason="invalid-owner-direct-evidence"
        )
        raise ReleaseError(f"owner-directed release selection refused: {exc}") from exc


def rollback(
    root: Path,
    receipt_path: Path,
    *,
    expected_current: str,
    operation_id: str,
) -> dict[str, Any]:
    """Select the previous generation recorded by a completed receipt."""
    receipt_root = (root / "receipts").resolve(strict=True)
    try:
        resolved_receipt = receipt_path.resolve(strict=True)
    except OSError as exc:
        raise ReleaseError(f"rollback receipt is unavailable: {exc}") from exc
    if resolved_receipt.parent != receipt_root or receipt_path.is_symlink():
        raise ReleaseError("rollback receipt must be an exact local selection receipt")
    source = _read_json(resolved_receipt)
    if source.get("schema") != RECEIPT_SCHEMA or source.get("state") != "completed":
        raise ReleaseError("rollback source is not a completed selection receipt")
    if source.get("selected_generation") != expected_current:
        raise ReleaseError("rollback source does not describe expected current generation")
    previous = source.get("previous_generation")
    source_operation = source.get("operation_id")
    if not isinstance(previous, str) or not isinstance(source_operation, str):
        raise ReleaseError("rollback receipt has no previous generation")
    _generation(source_operation)
    return _select(
        root,
        previous,
        expected_current=expected_current,
        operation_id=operation_id,
        rollback_of=source_operation,
    )


def recover(root: Path) -> list[dict[str, Any]]:
    """Finish receipts for prepared selections whose selector swap landed."""
    completed = []
    with _lock(root):
        current = current_generation(root)
        for path in sorted((root / "operations").glob("*.json")):
            operation = _read_json(path)
            if operation.get("state") == "completed":
                continue
            if operation.get("schema") != RECEIPT_SCHEMA or operation.get("state") != "prepared":
                raise ReleaseError(f"invalid prepared selection: {path.name}")
            selected = operation.get("selected_generation")
            previous = operation.get("previous_generation")
            if not isinstance(selected, str) or (previous is not None and not isinstance(previous, str)):
                raise ReleaseError(f"invalid selection generations: {path.name}")
            _generation(selected)
            if previous is not None:
                _generation(previous)
            if current == operation.get("selected_generation"):
                verify(root, selected)
                manifest = _read_json(root / "releases" / selected / ".release-manifest.json")
                if (
                    operation.get("selected_manifest_sha256")
                    != hashlib.sha256(
                        _canonical(manifest),
                    ).hexdigest()
                ):
                    raise ReleaseError(f"selected manifest changed during recovery: {path.name}")
                receipt = {**operation, "state": "completed"}
                _atomic_json(root / "receipts" / path.name, receipt)
                _atomic_json(path, receipt, mode=0o600)
                completed.append(receipt)
            elif current != operation.get("previous_generation"):
                raise ReleaseError(f"ambiguous selector recovery: {path.name}")
    return completed


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-release-install")
    parser.add_argument("--root", type=Path, default=Path("/opt/TGW"))
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install")
    install.add_argument("--archive", type=Path, required=True)
    for name in ("generation", "commit", "tree", "archive-sha256", "expected-current", "operation-id"):
        install.add_argument(f"--{name}", required=True)
    install.add_argument("--admission-receipt", type=Path)
    install.add_argument("--environment-preflight-receipt", type=Path)
    install.add_argument("--admission-public-key", type=Path)
    install.add_argument("--environment-public-key", type=Path)
    install.add_argument("--current-plan-commit")
    install.add_argument("--current-solution-hash")
    check = commands.add_parser("verify")
    check.add_argument("generation")
    commands.add_parser("recover")
    rollback_command = commands.add_parser("rollback")
    rollback_command.add_argument("--receipt", type=Path, required=True)
    rollback_command.add_argument("--expected-current", required=True)
    rollback_command.add_argument("--operation-id", required=True)
    promote = commands.add_parser("promote-selected")
    promote.add_argument("--uid", type=int, default=0)
    promote.add_argument("--gid", type=int, default=0)
    promote.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "install":
            from tgw.admission_recovery import (
                AdmissionRecoveryError,
                validate_environment_preflight_for_admission,
                validate_release_admission,
            )

            if (
                args.admission_receipt is None
                or args.environment_preflight_receipt is None
                or args.admission_public_key is None
                or args.environment_public_key is None
                or args.current_plan_commit is None
                or args.current_solution_hash is None
            ):
                record_refusal(
                    args.root,
                    args.generation,
                    args.operation_id,
                    reason="missing-admission-evidence",
                )
                raise ReleaseError("release selection requires admission and environment-preflight receipts")
            try:
                admission = json.loads(args.admission_receipt.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                record_refusal(
                    args.root,
                    args.generation,
                    args.operation_id,
                    reason="invalid-admission-evidence",
                )
                raise ReleaseError("admission receipt is unavailable or invalid") from exc
            try:
                admission_public_key = _read_trusted_public_key(args.admission_public_key)
                environment_public_key = _read_trusted_public_key(args.environment_public_key)
                def current_time() -> str:
                    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

                prevalidation_time = current_time()
                validate_release_admission(
                    admission,
                    candidate_commit=args.commit,
                    candidate_tree=args.tree,
                    trusted_public_key=admission_public_key,
                    current_time=prevalidation_time,
                    current_plan_commit=args.current_plan_commit,
                    current_solution_hash=args.current_solution_hash,
                )
                preflight = json.loads(args.environment_preflight_receipt.read_text(encoding="utf-8"))
                validate_environment_preflight_for_admission(
                    preflight,
                    catalog_hash=admission["environment"]["catalog_hash"],
                    receipt_hash=admission["environment"]["receipt_hash"],
                    trusted_public_key=environment_public_key,
                    current_time=prevalidation_time,
                )
            except AdmissionRecoveryError as exc:
                record_refusal(
                    args.root,
                    args.generation,
                    args.operation_id,
                    reason="invalid-admission-evidence",
                )
                raise ReleaseError(f"release admission refused: {exc}") from exc
            except (OSError, json.JSONDecodeError) as exc:
                record_refusal(
                    args.root,
                    args.generation,
                    args.operation_id,
                    reason="invalid-admission-evidence",
                )
                raise ReleaseError("environment preflight receipt is unavailable or invalid") from exc
            manifest = materialize(
                args.root,
                args.archive,
                generation=args.generation,
                commit=args.commit,
                tree=args.tree,
                archive_sha256=args.archive_sha256,
            )
            expected_current = None if args.expected_current == "none" else args.expected_current
            receipt = select(
                args.root,
                args.generation,
                expected_current=expected_current,
                operation_id=args.operation_id,
                admission_receipt=admission,
                environment_preflight_receipt=preflight,
                admission_public_key=admission_public_key,
                environment_public_key=environment_public_key,
                current_plan_commit=args.current_plan_commit,
                current_solution_hash=args.current_solution_hash,
                current_time=current_time,
            )
            result = {"manifest": manifest, "receipt": receipt}
        elif args.command == "verify":
            result = verify(args.root, args.generation)
        elif args.command == "rollback":
            result = rollback(
                args.root,
                args.receipt,
                expected_current=args.expected_current,
                operation_id=args.operation_id,
            )
        elif args.command == "promote-selected":
            selected = current_generation(args.root)
            if selected is None:
                if args.allow_missing:
                    result = {"status": "no-selected-release"}
                else:
                    raise ReleaseError("no release is currently selected")
            else:
                result = promote_release_ownership(
                    args.root, selected, uid=args.uid, gid=args.gid
                )
        else:
            result = {"completed": recover(args.root), "current": current_generation(args.root)}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except ReleaseError as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
