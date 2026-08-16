"""One canonical, unambiguous review-snapshot digest."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Mapping

_DOMAIN = b"tgw-review-snapshot/v2\0"
_PREFIX = "sha256:"


def snapshot_preimage_entries(entries: Mapping[str, bytes]) -> bytes:
    """Return the canonical byte representation used for a snapshot digest."""

    framed = bytearray(_DOMAIN)
    for relative in sorted(entries):
        if not isinstance(relative, str) or not relative or "\0" in relative:
            raise ValueError("review snapshot path is invalid")
        content = entries[relative]
        if not isinstance(content, bytes):
            raise ValueError("review snapshot content is invalid")
        encoded = relative.encode("utf-8")
        framed.extend(len(encoded).to_bytes(8, "big"))
        framed.extend(encoded)
        framed.extend(len(content).to_bytes(8, "big"))
        framed.extend(content)
    return bytes(framed)


def snapshot_hash_entries(entries: Mapping[str, bytes]) -> str:
    """Digest exact regular-file bytes with length-delimited path framing."""

    return _PREFIX + hashlib.sha256(snapshot_preimage_entries(entries)).hexdigest()


def snapshot_hash(root: Path) -> str:
    entries: dict[str, bytes] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ValueError("review snapshot cannot contain symlinks")
        if not path.is_file():
            continue
        entries[path.relative_to(root).as_posix()] = path.read_bytes()
    return snapshot_hash_entries(entries)


def snapshot_preimage(root: Path) -> bytes:
    """Return the card-bound source-tree resource bytes for a snapshot path."""

    entries: dict[str, bytes] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ValueError("review snapshot cannot contain symlinks")
        if not path.is_file():
            continue
        entries[path.relative_to(root).as_posix()] = path.read_bytes()
    return snapshot_preimage_entries(entries)
