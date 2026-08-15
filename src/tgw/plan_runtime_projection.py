"""Immutable runtime projection of one approved Plan and complete solution.

Production does not host a Plan checkout.  It consumes this small, hash-bound
projection from the selected immutable application release instead.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Mapping

from tgw.plan_solver import validate_for_dispatch

SCHEMA = "tgw-plan-runtime-projection/v1"
MAX_PROJECTION_BYTES = 2 * 1024 * 1024
_KEYS = {
    "schema",
    "plan_id",
    "plan_commit",
    "plan_files",
    "solution",
    "solution_sha256",
    "projection_sha256",
}


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def validate_projection(
    value: Mapping[str, Any], *, expected_plan_commit: str | None = None
) -> dict[str, Any]:
    if set(value) != _KEYS or value.get("schema") != SCHEMA:
        raise ValueError("Plan runtime projection schema is not exact")
    if value.get("plan_id") != "PLAN-GOVERNED-EXECUTION-PLATFORM":
        raise ValueError("Plan runtime projection target is invalid")
    plan_commit = value.get("plan_commit")
    if not isinstance(plan_commit, str) or len(plan_commit) != 40 or any(
        char not in "0123456789abcdef" for char in plan_commit
    ):
        raise ValueError("Plan runtime projection commit is invalid")
    if expected_plan_commit is not None and plan_commit != expected_plan_commit:
        raise ValueError("Plan runtime projection differs from the approved commit")
    plan_files = value.get("plan_files")
    required_paths = {
        "plan/PLAN-governed-execution-platform-build.md",
        "plan/execution/GOVERNED-EXECUTION-PLATFORM-v1.yaml",
    }
    if not isinstance(plan_files, list) or {
        item.get("path") for item in plan_files if isinstance(item, Mapping)
    } != required_paths:
        raise ValueError("Plan runtime projection file authority is incomplete")
    for item in plan_files:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"path", "sha256"}
            or not isinstance(item.get("sha256"), str)
            or len(item["sha256"]) != 71
            or not item["sha256"].startswith("sha256:")
        ):
            raise ValueError("Plan runtime projection file identity is invalid")
    solution = value.get("solution")
    if not isinstance(solution, Mapping):
        raise ValueError("Plan runtime projection solution is absent")
    validate_for_dispatch(solution, current_plan_commit=plan_commit)
    if value.get("solution_sha256") != sha256(canonical(solution)):
        raise ValueError("Plan runtime projection solution bytes do not match")
    unsigned = dict(value)
    claimed = unsigned.pop("projection_sha256", None)
    if claimed != sha256(canonical(unsigned)):
        raise ValueError("Plan runtime projection self-hash does not match")
    return dict(value)


def _trusted_chain(path: Path, *, trusted_uid: int, trusted_root: Path) -> None:
    resolved = path.resolve(strict=True)
    root = trusted_root.resolve(strict=True)
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("Plan runtime projection is outside its protected root") from exc
    current = root
    for component in (Path("."), *relative.parts[:-1]):
        current /= component
        metadata = current.stat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != trusted_uid
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise ValueError("Plan runtime projection parent chain is not protected")


def load_projection(
    path: str | Path,
    *,
    expected_plan_commit: str | None = None,
    trusted_uid: int = 0,
    trusted_root: str | Path = "/opt/TGW/releases",
) -> dict[str, Any]:
    """Load one protected projection while detecting replacement and mutation."""

    named = Path(path)
    _trusted_chain(named, trusted_uid=trusted_uid, trusted_root=Path(trusted_root))
    descriptor = os.open(named, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != trusted_uid
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o222
            or before.st_size > MAX_PROJECTION_BYTES
        ):
            raise ValueError("Plan runtime projection file is not immutable")
        chunks: list[bytes] = []
        remaining = MAX_PROJECTION_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > MAX_PROJECTION_BYTES or len(raw) != before.st_size:
            raise ValueError("Plan runtime projection size is invalid")
        after = os.fstat(descriptor)
        named_after = named.stat()

        def identity(item: os.stat_result) -> tuple[int, ...]:
            return (
                item.st_dev,
                item.st_ino,
                item.st_uid,
                item.st_gid,
                item.st_mode,
                item.st_nlink,
                item.st_size,
            )

        if identity(before) != identity(after) or identity(after) != identity(named_after):
            raise ValueError("Plan runtime projection changed while held")
    finally:
        os.close(descriptor)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Plan runtime projection is not canonical JSON") from exc
    if not isinstance(value, Mapping) or raw != json.dumps(value, indent=2, sort_keys=True).encode() + b"\n":
        raise ValueError("Plan runtime projection encoding is not canonical")
    return validate_projection(value, expected_plan_commit=expected_plan_commit)
