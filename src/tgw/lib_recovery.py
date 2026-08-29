"""Independent, fail-closed recovery manifests for the tgw-lib authority domain.

This module deliberately performs no remote writes, pruning, or snapshot creation.
Store-specific collectors stage objects; this code verifies and seals one generation
only after every required recovery tier is complete and content-addressed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

SCHEMA = "tgw-lib-recovery-generation/v1"
REQUIRED_TIERS = (
    "plan_git",
    "source_git",
    "postgresql",
    "library",
    "tgw_lib_state",
    "master_media",
    "unix_identities",
    "encrypted_secrets",
)


class ManifestError(ValueError):
    """A generation cannot truthfully be called complete."""


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def object_record(path: Path, root: Path) -> dict[str, Any]:
    resolved = path.resolve()
    base = root.resolve()
    try:
        relative = resolved.relative_to(base)
    except ValueError as exc:
        raise ManifestError(f"object escapes generation root: {path}") from exc
    info = resolved.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ManifestError(f"object is not a regular file: {relative}")
    return {
        "path": relative.as_posix(),
        "sha256": sha256_file(resolved),
        "size": info.st_size,
        "mode": stat.S_IMODE(info.st_mode),
    }


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"missing {field}")
    return value


def validate_generation(manifest: dict[str, Any], root: Path, verify_objects: bool = True) -> None:
    if manifest.get("schema") != SCHEMA:
        raise ManifestError("unsupported generation schema")
    if manifest.get("state") != "complete":
        raise ManifestError("generation state is not complete")
    _require_text(manifest.get("generation"), "generation")
    _require_text(manifest.get("started_at"), "started_at")
    _require_text(manifest.get("completed_at"), "completed_at")
    _require_text(manifest.get("retention_class"), "retention_class")
    tools = manifest.get("tools")
    if not isinstance(tools, dict) or not tools:
        raise ManifestError("missing tool versions")
    barriers = manifest.get("barriers")
    if not isinstance(barriers, dict):
        raise ManifestError("missing per-store barriers")
    for field in ("git_refs", "postgresql", "filesystems"):
        if not barriers.get(field):
            raise ManifestError(f"missing barrier {field}")
    tiers = manifest.get("tiers")
    if not isinstance(tiers, dict):
        raise ManifestError("missing tiers")
    for name in REQUIRED_TIERS:
        tier = tiers.get(name)
        if not isinstance(tier, dict) or tier.get("state") != "complete":
            raise ManifestError(f"required tier is not complete: {name}")
        if not tier.get("objects"):
            raise ManifestError(f"required tier has no objects: {name}")
    seen: set[str] = set()
    for name, tier in tiers.items():
        for obj in tier.get("objects", []):
            rel = _require_text(obj.get("path"), f"{name}.object.path")
            if rel in seen:
                raise ManifestError(f"object appears in multiple tiers: {rel}")
            seen.add(rel)
            candidate = (root / rel).resolve()
            try:
                candidate.relative_to(root.resolve())
            except ValueError as exc:
                raise ManifestError(f"object escapes generation root: {rel}") from exc
            if verify_objects:
                if not candidate.is_file():
                    raise ManifestError(f"object missing: {rel}")
                if candidate.stat().st_size != obj.get("size"):
                    raise ManifestError(f"object size differs: {rel}")
                if sha256_file(candidate) != obj.get("sha256"):
                    raise ManifestError(f"object hash differs: {rel}")


def seal_generation(staging: Path, manifest: dict[str, Any], destination: Path) -> Path:
    """Atomically publish a verified generation; never overwrite a receipt."""
    validate_generation(manifest, staging, verify_objects=True)
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"{manifest['generation']}.json"
    if target.exists():
        raise ManifestError(f"immutable receipt already exists: {target}")
    temporary = destination / f".{manifest['generation']}.{os.getpid()}.tmp"
    payload = dict(manifest)
    payload["manifest_sha256"] = "sha256:" + hashlib.sha256(canonical_bytes(manifest)).hexdigest()
    try:
        with temporary.open("xb") as stream:
            stream.write(canonical_bytes(payload))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o440)
        os.link(temporary, target)
        temporary.unlink()
        directory_fd = os.open(destination, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _command_version(command: str) -> str | None:
    executable = shutil.which(command)
    if not executable:
        return None
    result = subprocess.run([executable, "--version"], text=True, capture_output=True, timeout=5, check=False)
    line = (result.stdout or result.stderr).splitlines()
    return line[0].strip() if line else executable


def inventory(paths: Iterable[Path]) -> dict[str, Any]:
    """Read-only topology/capacity inventory; absence remains explicit."""
    mounts: list[dict[str, Any]] = []
    for path in paths:
        exists = path.exists()
        entry: dict[str, Any] = {"path": str(path), "exists": exists}
        if exists:
            usage = shutil.disk_usage(path)
            entry.update({"device": os.stat(path).st_dev, "capacity": usage.total, "free": usage.free})
        mounts.append(entry)
    return {
        "schema": "tgw-lib-recovery-inventory/v1",
        "observed_at": datetime.now(UTC).isoformat(),
        "host": {"system": platform.system(), "release": platform.release(), "machine": platform.machine()},
        "paths": mounts,
        "tools": {name: _command_version(name) for name in ("git", "pg_dump", "pg_basebackup", "age", "restic", "borg", "btrfs", "zfs")},
    }


def verify_receipt(path: Path, object_root: Path) -> None:
    receipt = json.loads(path.read_text())
    recorded = receipt.pop("manifest_sha256", None)
    expected = "sha256:" + hashlib.sha256(canonical_bytes(receipt)).hexdigest()
    if recorded != expected:
        raise ManifestError("manifest hash differs")
    validate_generation(receipt, object_root, verify_objects=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only tgw-lib recovery inventory and receipt verification")
    sub = parser.add_subparsers(dest="action", required=True)
    inv = sub.add_parser("inventory")
    inv.add_argument("paths", nargs="*", type=Path, default=[Path("/opt/TGW/library"), Path("/opt/TGW/tgw-lib"), Path("/opt/TGW/data")])
    verify = sub.add_parser("verify")
    verify.add_argument("receipt", type=Path)
    verify.add_argument("object_root", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "inventory":
            print(json.dumps(inventory(args.paths), sort_keys=True, indent=2))
        else:
            verify_receipt(args.receipt, args.object_root)
            print("complete")
        return 0
    except (ManifestError, OSError, json.JSONDecodeError) as exc:
        print(f"incomplete: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
