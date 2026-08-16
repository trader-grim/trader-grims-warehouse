"""Held-root immutable sink for terminal bootstrap execution receipts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError("terminal receipt is not canonical JSON data") from exc


def completion_store_descriptor_hash(
    root: Path,
    *,
    sink_id: str,
    trusted_uid: int,
) -> str:
    """Bind a sink descriptor to its exact named root and inode policy."""
    named = Path(root)
    metadata = named.lstat()
    descriptor = {
        "schema": "tgw-immutable-effect-completion-store/v1",
        "root": str(named),
        "sink_id": sink_id,
        "trusted_uid": trusted_uid,
        "root_device": metadata.st_dev,
        "root_inode": metadata.st_ino,
        "root_mode": stat.S_IMODE(metadata.st_mode),
    }
    return "sha256:" + hashlib.sha256(_canonical(descriptor)).hexdigest()


class ImmutableEffectCompletionStore:
    """Persist controller outcomes with held-root, same-inode verification."""

    def __init__(
        self,
        root: Path,
        *,
        sink_id: str,
        descriptor_hash: str,
        trusted_uid: int | None = None,
    ) -> None:
        self.root = Path(root)
        uid = os.getuid() if trusted_uid is None else trusted_uid
        if (
            not self.root.is_absolute()
            or self.root.parent == self.root
            or ".." in self.root.parts
            or re.fullmatch(r"[a-z][a-z0-9-]{0,63}", sink_id) is None
            or re.fullmatch(r"sha256:[0-9a-f]{64}", descriptor_hash) is None
        ):
            raise ValueError("terminal receipt sink descriptor is invalid")
        self.sink_id, self.descriptor_hash = sink_id, descriptor_hash
        self._parent_fd = self._root_fd = -1
        parent_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for component in self.root.parent.parts[1:]:
                before = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
                next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                held = os.fstat(next_fd)
                after = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
                mode = stat.S_IMODE(held.st_mode)
                sticky_root = held.st_uid == 0 and bool(mode & stat.S_ISVTX)
                if (
                    not stat.S_ISDIR(held.st_mode)
                    or held.st_uid not in {0, uid}
                    or (mode & 0o022 and not sticky_root)
                    or (before.st_dev, before.st_ino) != (held.st_dev, held.st_ino)
                    or (after.st_dev, after.st_ino) != (held.st_dev, held.st_ino)
                ):
                    os.close(next_fd)
                    raise ValueError("terminal receipt sink has an unsafe ancestor")
                os.close(parent_fd)
                parent_fd = next_fd
            self._parent_fd, parent_fd = parent_fd, -1
        finally:
            if parent_fd >= 0:
                os.close(parent_fd)
        parent = os.fstat(self._parent_fd)
        self._parent_identity = (parent.st_dev, parent.st_ino, parent.st_uid, stat.S_IMODE(parent.st_mode))
        try:
            before = os.stat(self.root.name, dir_fd=self._parent_fd, follow_symlinks=False)
            self._root_fd = os.open(self.root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._parent_fd)
            held = os.fstat(self._root_fd)
            after = os.stat(self.root.name, dir_fd=self._parent_fd, follow_symlinks=False)
        except Exception:
            self.close()
            raise
        if (
            not stat.S_ISDIR(held.st_mode)
            or held.st_uid != uid
            or stat.S_IMODE(held.st_mode) != 0o700
            or (before.st_dev, before.st_ino) != (held.st_dev, held.st_ino)
            or (after.st_dev, after.st_ino) != (held.st_dev, held.st_ino)
        ):
            self.close()
            raise ValueError("terminal receipt root must be one held trusted mode-0700 directory")
        self._root_identity = (held.st_dev, held.st_ino, held.st_uid, stat.S_IMODE(held.st_mode))
        expected_descriptor = completion_store_descriptor_hash(
            self.root,
            sink_id=sink_id,
            trusted_uid=uid,
        )
        if descriptor_hash != expected_descriptor:
            self.close()
            raise ValueError("terminal receipt sink descriptor differs from its held root")

    def close(self) -> None:
        for name in ("_root_fd", "_parent_fd"):
            fd = getattr(self, name, -1)
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
                setattr(self, name, -1)

    def __del__(self) -> None:
        self.close()

    def _verify_root(self) -> None:
        parent, held = os.fstat(self._parent_fd), os.fstat(self._root_fd)
        named = os.stat(self.root.name, dir_fd=self._parent_fd, follow_symlinks=False)
        observed = (held.st_dev, held.st_ino, held.st_uid, stat.S_IMODE(held.st_mode))
        if (parent.st_dev, parent.st_ino, parent.st_uid, stat.S_IMODE(parent.st_mode)) != self._parent_identity or observed != self._root_identity or (named.st_dev, named.st_ino) != observed[:2]:
            raise OSError("terminal receipt root identity changed")

    @staticmethod
    def _read(fd: int, maximum: int) -> bytes:
        os.lseek(fd, 0, os.SEEK_SET)
        content = bytearray()
        while len(content) <= maximum:
            block = os.read(fd, min(64 * 1024, maximum + 1 - len(content)))
            if not block:
                break
            content.extend(block)
        if len(content) > maximum:
            raise OSError("terminal receipt exceeds its exact size")
        return bytes(content)

    def _verify_named(self, name: str, raw: bytes, inode: tuple[int, int] | None = None) -> tuple[int, int]:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self._root_fd)
        try:
            metadata = os.fstat(fd)
            identity = (metadata.st_dev, metadata.st_ino)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != self._root_identity[2]
                or stat.S_IMODE(metadata.st_mode) != 0o400
                or metadata.st_nlink != 1
                or metadata.st_size != len(raw)
                or (inode is not None and identity != inode)
                or self._read(fd, len(raw)) != raw
            ):
                raise OSError("terminal receipt named readback mismatch")
            return identity
        finally:
            os.close(fd)

    def persist(self, receipt: Mapping[str, Any]) -> dict[str, str]:
        if not isinstance(receipt, Mapping):
            raise ValueError("terminal execution receipt is invalid")
        unsigned = dict(receipt)
        claimed = unsigned.pop("receipt_hash", None)
        expected = "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest()
        if claimed != expected:
            raise ValueError("terminal execution receipt self-hash is invalid")
        raw = _canonical(dict(receipt)) + b"\n"
        name = expected.removeprefix("sha256:") + ".json"
        self._verify_root()
        try:
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=self._root_fd)
        except FileExistsError:
            self._verify_named(name, raw)
            return {"receipt": f"effect-terminal:{self.sink_id}:{expected}", "receipt_hash": expected}
        cleanup = True
        try:
            try:
                os.fchmod(fd, 0o400)
                offset = 0
                while offset < len(raw):
                    written = os.write(fd, raw[offset:])
                    if written <= 0:
                        raise OSError("terminal receipt short write")
                    offset += written
                os.fsync(fd)
                held = os.fstat(fd)
                if self._read(fd, len(raw)) != raw or held.st_nlink != 1:
                    raise OSError("terminal receipt held readback mismatch")
            finally:
                os.close(fd)
            inode = self._verify_named(name, raw, (held.st_dev, held.st_ino))
            os.fsync(self._root_fd)
            self._verify_root()
            self._verify_named(name, raw, inode)
            cleanup = False
        finally:
            if cleanup:
                try:
                    os.unlink(name, dir_fd=self._root_fd)
                    os.fsync(self._root_fd)
                except OSError as exc:
                    raise OSError("terminal receipt cleanup is ambiguous") from exc
        return {"receipt": f"effect-terminal:{self.sink_id}:{expected}", "receipt_hash": expected}
