"""Idempotent Debian host bootstrap for the tgw-lib W18 actor provider.

Application release selection remains the signed W16 installer boundary.  This
module installs only the stable host-owned systemd and tmpfiles declarations
from that selected immutable release and records enough prior state for a human
owner to restore those declarations without a working controller or MCP.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

_IDENTITY = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")


class ActorHostBootstrapError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ActorHostBootstrapError(f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise ActorHostBootstrapError(f"{label} is invalid")
    return value


@dataclass(frozen=True)
class HostPaths:
    current: Path = Path("/opt/TGW/tgw-lib/actor-runtime/current")
    systemd_unit: Path = Path("/etc/systemd/system/tgw-actor-fleet-provider.service")
    tmpfiles_config: Path = Path("/etc/tmpfiles.d/tgw-actor-host.conf")
    receipt_root: Path = Path("/opt/TGW/tgw-lib/var/host-bootstrap-receipts")
    systemctl: Path = Path("/usr/bin/systemctl")
    systemd_tmpfiles: Path = Path("/usr/bin/systemd-tmpfiles")


def _source(paths: HostPaths) -> tuple[Path, dict[str, Any], dict[Path, Path]]:
    if not paths.current.is_symlink():
        raise ActorHostBootstrapError("selected actor release is unavailable")
    release = paths.current.resolve(strict=True)
    if release.is_symlink() or not release.is_dir():
        raise ActorHostBootstrapError("selected actor release is unsafe")
    observed = release.stat()
    if os.geteuid() == 0 and observed.st_uid != 0:
        raise ActorHostBootstrapError("selected actor release is not root owned")
    if stat.S_IMODE(observed.st_mode) & 0o022:
        raise ActorHostBootstrapError("selected actor release is writable")
    manifest = _read_json(release / ".release-manifest.json", "selected actor release manifest")
    if (
        manifest.get("schema") != "tgw-release-manifest-v1"
        or _COMMIT.fullmatch(str(manifest.get("commit"))) is None
        or _COMMIT.fullmatch(str(manifest.get("git_tree"))) is None
        or not isinstance(manifest.get("files"), Mapping)
    ):
        raise ActorHostBootstrapError("selected actor release manifest is incomplete")
    sources = {
        paths.systemd_unit: release / "config/environment/systemd/tgw-actor-fleet-provider.service",
        paths.tmpfiles_config: release / "config/environment/tmpfiles.d/tgw-actor-host.conf",
    }
    for source in sources.values():
        relative = source.relative_to(release).as_posix()
        if source.is_symlink() or not source.is_file() or manifest["files"].get(relative) != hashlib.sha256(source.read_bytes()).hexdigest():
            raise ActorHostBootstrapError(f"selected actor host artifact is not manifest-bound: {relative}")
    return release, manifest, sources


def _snapshot(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        return {"kind": "symlink", "target": os.readlink(path)}
    if path.exists():
        if not path.is_file():
            raise ActorHostBootstrapError(f"host bootstrap target is not a regular file: {path}")
        state = path.stat(follow_symlinks=False)
        return {
            "kind": "file",
            "content": base64.b64encode(path.read_bytes()).decode("ascii"),
            "mode": stat.S_IMODE(state.st_mode),
            "uid": state.st_uid,
            "gid": state.st_gid,
        }
    return {"kind": "absent"}


def _atomic_file(path: Path, content: bytes, *, mode: int = 0o644) -> None:
    stage = path.with_name(f".{path.name}.next")
    if stage.exists() or stage.is_symlink():
        raise ActorHostBootstrapError(f"stale host bootstrap stage exists: {stage}")
    descriptor = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(stage, mode)
        if os.geteuid() == 0:
            os.chown(stage, 0, 0)
        os.replace(stage, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if stage.exists() and not stage.is_symlink():
            stage.unlink()


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)


def install_actor_host(
    operation_id: str,
    *,
    paths: HostPaths = HostPaths(),
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] = _run,
    require_root: bool = True,
) -> dict[str, Any]:
    if require_root and os.geteuid() != 0:
        raise ActorHostBootstrapError("actor host bootstrap requires root")
    if _IDENTITY.fullmatch(operation_id) is None:
        raise ActorHostBootstrapError("actor host bootstrap operation id is invalid")
    release, manifest, sources = _source(paths)
    paths.receipt_root.mkdir(parents=True, exist_ok=True, mode=0o750)
    receipt_path = paths.receipt_root / f"{operation_id}.json"
    if receipt_path.exists() and not receipt_path.is_symlink():
        existing = _read_json(receipt_path, "actor host bootstrap receipt")
        installed = {str(target): _file_hash(source) for target, source in sources.items()}
        unsigned_existing = dict(existing)
        claimed_existing = unsigned_existing.pop("receipt_hash", None)
        if (
            existing.get("schema") != "tgw-actor-host-bootstrap-receipt/v1"
            or existing.get("status") != "INSTALLED"
            or existing.get("operation_id") != operation_id
            or existing.get("release") != str(release)
            or existing.get("commit") != manifest["commit"]
            or existing.get("tree") != manifest["git_tree"]
            or existing.get("installed") != installed
            or claimed_existing != _hash(unsigned_existing)
            or any(not target.is_file() or target.is_symlink() or _file_hash(target) != installed[str(target)] for target in sources)
        ):
            raise ActorHostBootstrapError("actor host bootstrap operation id collision")
        return existing
    before = {str(target): _snapshot(target) for target in sources}
    intended = {
        "schema": "tgw-actor-host-bootstrap-receipt/v1",
        "operation_id": operation_id,
        "status": "INSTALLED",
        "release": str(release),
        "commit": manifest["commit"],
        "tree": manifest["git_tree"],
        "before": before,
        "installed": {str(target): _file_hash(source) for target, source in sources.items()},
    }
    receipt = {**intended, "receipt_hash": _hash(intended)}
    for target, source in sources.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_file(target, source.read_bytes())
    for command in (
        [str(paths.systemd_tmpfiles), "--create", str(paths.tmpfiles_config)],
        [str(paths.systemctl), "daemon-reload"],
        [str(paths.systemctl), "enable", "tgw-actor-fleet-provider.service"],
    ):
        result = runner(command)
        if result.returncode != 0:
            raise ActorHostBootstrapError(f"actor host bootstrap command failed: {command[1]}")
    _atomic_file(receipt_path, _canonical(receipt) + b"\n", mode=0o640)
    return receipt


def rollback_actor_host(
    receipt_path: Path,
    *,
    paths: HostPaths = HostPaths(),
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] = _run,
    require_root: bool = True,
) -> dict[str, Any]:
    if require_root and os.geteuid() != 0:
        raise ActorHostBootstrapError("actor host rollback requires root")
    root = paths.receipt_root.resolve(strict=True)
    receipt = _read_json(receipt_path, "actor host bootstrap receipt")
    if receipt_path.is_symlink() or receipt_path.resolve(strict=True).parent != root:
        raise ActorHostBootstrapError("actor host rollback receipt is outside the receipt root")
    unsigned = dict(receipt)
    claimed = unsigned.pop("receipt_hash", None)
    if receipt.get("schema") != "tgw-actor-host-bootstrap-receipt/v1" or receipt.get("status") != "INSTALLED" or claimed != _hash(unsigned):
        raise ActorHostBootstrapError("actor host rollback receipt is invalid")
    for raw, expected in receipt["installed"].items():
        target = Path(raw)
        if not target.is_file() or target.is_symlink() or _file_hash(target) != expected:
            raise ActorHostBootstrapError("actor host artifact changed before rollback")
    stopped = runner([str(paths.systemctl), "stop", "tgw-actor-fleet-provider.service"])
    if stopped.returncode != 0:
        raise ActorHostBootstrapError("actor host service did not stop for rollback")
    for raw, prior in receipt["before"].items():
        target = Path(raw)
        if prior["kind"] == "absent":
            target.unlink()
        elif prior["kind"] == "symlink":
            target.unlink()
            target.symlink_to(prior["target"])
        elif prior["kind"] == "file":
            _atomic_file(target, base64.b64decode(prior["content"], validate=True), mode=int(prior["mode"]))
            os.chown(target, int(prior["uid"]), int(prior["gid"]))
        else:
            raise ActorHostBootstrapError("actor host rollback state is invalid")
    reloaded = runner([str(paths.systemctl), "daemon-reload"])
    if reloaded.returncode != 0:
        raise ActorHostBootstrapError("actor host systemd reload failed after rollback")
    body = {
        "schema": "tgw-actor-host-bootstrap-rollback/v1",
        "status": "ROLLED_BACK",
        "operation_id": receipt["operation_id"],
        "source_receipt_hash": receipt["receipt_hash"],
    }
    return {**body, "receipt_hash": _hash(body)}


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-install-actor-host")
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install")
    install.add_argument("--operation-id", required=True)
    rollback = commands.add_parser("rollback")
    rollback.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = install_actor_host(args.operation_id) if args.command == "install" else rollback_actor_host(args.receipt)
    except (OSError, TypeError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"schema": "tgw-actor-host-bootstrap-result/v1", "status": "HOLD", "reason": str(exc)}, sort_keys=True))
        return 73
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
