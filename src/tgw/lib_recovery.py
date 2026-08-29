"""Fail-closed manifests for independent tgw-lib recovery generations."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key

SCHEMA = "tgw-lib-recovery-generation/v2"
REQUIRED_TIERS = ("plan_git", "source_git", "postgresql", "library", "tgw_lib_state", "master_media", "unix_identities", "encrypted_secrets")
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
OID = re.compile(r"[0-9a-f]{40,64}\Z")
LSN = re.compile(r"[0-9A-F]+/[0-9A-F]+\Z", re.I)


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


def _acl(path: Path) -> str:
    command = shutil.which("getfacl")
    if not command:
        raise ManifestError("getfacl is required to capture recovery metadata")
    try:
        result = subprocess.run([command, "-cp", "--", str(path)], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ManifestError(f"ACL collection failed: {path}") from exc
    if result.returncode != 0:
        raise ManifestError(f"ACL collection failed: {path}: {result.stderr.strip()}")
    return result.stdout


def _xattrs(path: Path) -> dict[str, str]:
    try:
        return {key: base64.b64encode(os.getxattr(path, key, follow_symlinks=False)).decode() for key in sorted(os.listxattr(path, follow_symlinks=False))}
    except (AttributeError, OSError) as exc:
        raise ManifestError(f"xattr collection failed: {path}") from exc


def object_record(path: Path, root: Path) -> dict[str, Any]:
    resolved, base = path.resolve(), root.resolve()
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
        "uid": info.st_uid,
        "gid": info.st_gid,
        "inode": info.st_ino,
        "hardlinks": info.st_nlink,
        "hardlink_group": f"{info.st_dev}:{info.st_ino}",
        "acl": _acl(resolved),
        "xattrs": _xattrs(resolved),
    }


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"missing {field}")
    return value


def _time(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(_text(value, field).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ManifestError(f"invalid {field}") from exc
    if parsed.tzinfo is None:
        raise ManifestError(f"invalid {field}")
    return parsed


def _validate_barriers(value: Any) -> None:
    if not isinstance(value, dict):
        raise ManifestError("missing per-store barriers")
    git = value.get("git_refs")
    if not isinstance(git, dict):
        raise ManifestError("missing barrier git_refs")
    for repo in ("plan", "source"):
        item = git.get(repo)
        if not isinstance(item, dict) or not OID.fullmatch(str(item.get("commit", ""))) or not OID.fullmatch(str(item.get("tree", ""))):
            raise ManifestError(f"invalid git barrier {repo}")
        refs = item.get("refs")
        if (
            not isinstance(refs, dict)
            or not refs
            or any(not isinstance(ref, str) or not ref.startswith("refs/") or not OID.fullmatch(str(oid)) for ref, oid in refs.items())
            or item["commit"] not in refs.values()
        ):
            raise ManifestError(f"invalid git ref map {repo}")
        _time(item.get("captured_at"), f"git_refs.{repo}.captured_at")
    pg = value.get("postgresql")
    if not isinstance(pg, dict) or not LSN.fullmatch(str(pg.get("start_lsn", ""))) or not LSN.fullmatch(str(pg.get("stop_lsn", ""))):
        raise ManifestError("invalid PostgreSQL LSN barrier")
    if not isinstance(pg.get("timeline"), int) or pg["timeline"] < 1 or pg.get("wal_contiguous") is not True:
        raise ManifestError("missing PostgreSQL timeline/WAL continuity")
    if not SHA256.fullmatch(str(pg.get("schema_sha256", ""))):
        raise ManifestError("invalid PostgreSQL schema identity")
    _text(pg.get("migration_identity"), "postgresql.migration_identity")
    _time(pg.get("captured_at"), "postgresql.captured_at")
    filesystems = value.get("filesystems")
    if not isinstance(filesystems, dict) or not filesystems:
        raise ManifestError("missing barrier filesystems")
    for name, item in filesystems.items():
        if not isinstance(item, dict) or item.get("method") not in {"snapshot", "bounded-walk"} or not SHA256.fullmatch(str(item.get("manifest_sha256", ""))):
            raise ManifestError(f"invalid filesystem barrier {name}")
        _text(item.get("barrier_id"), f"filesystems.{name}.barrier_id")
        _time(item.get("captured_at"), f"filesystems.{name}.captured_at")


def validate_generation(manifest: dict[str, Any], root: Path, verify_objects: bool = True) -> None:
    if manifest.get("schema") != SCHEMA or manifest.get("state") != "complete":
        raise ManifestError("unsupported or incomplete generation")
    generation = _text(manifest.get("generation"), "generation")
    if not SAFE_ID.fullmatch(generation) or generation in {".", ".."}:
        raise ManifestError("generation must be a safe basename")
    if _time(manifest.get("completed_at"), "completed_at") < _time(manifest.get("started_at"), "started_at"):
        raise ManifestError("generation completes before it starts")
    _text(manifest.get("retention_class"), "retention_class")
    if not isinstance(manifest.get("tools"), dict) or not manifest["tools"]:
        raise ManifestError("missing tool versions")
    _validate_barriers(manifest.get("barriers"))
    object_manifest_sha256 = "sha256:" + hashlib.sha256(
        canonical_bytes({"generation": manifest.get("generation"), "barriers": manifest.get("barriers"), "tiers": manifest.get("tiers")})
    ).hexdigest()
    replicas = manifest.get("replicas")
    if not isinstance(replicas, dict):
        raise ManifestError("missing replica evidence")
    for name in ("local_fast", "off_host_encrypted"):
        replica = replicas.get(name)
        if (
            not isinstance(replica, dict)
            or replica.get("state") != "verified"
            or replica.get("readback_verified") is not True
            or not SHA256.fullmatch(str(replica.get("manifest_sha256", "")))
            or not replica.get("failure_domain")
        ):
            raise ManifestError(f"missing verified replica: {name}")
    if replicas["local_fast"]["failure_domain"] == replicas["off_host_encrypted"]["failure_domain"]:
        raise ManifestError("replicas share a failure domain")
    offhost = replicas["off_host_encrypted"]
    if offhost.get("encryption") not in {"age", "restic", "borg-repokey", "aes-256-gcm"} or offhost.get("key_custody") != "operator-held-offline":
        raise ManifestError("off-host replica lacks encryption/key-custody evidence")
    receipt = manifest.get("receipt")
    if not isinstance(receipt, dict) or receipt.get("storage") not in {"object-lock", "append-only-remote", "worm"} or receipt.get("immutability_verified") is not True:
        raise ManifestError("missing immutable receipt storage evidence")
    tiers = manifest.get("tiers")
    if not isinstance(tiers, dict):
        raise ManifestError("missing tiers")
    root = root.resolve()
    seen: set[str] = set()
    hardlink_groups: dict[str, tuple[int, int]] = {}
    for name in REQUIRED_TIERS:
        tier = tiers.get(name)
        if not isinstance(tier, dict) or tier.get("state") != "complete" or not tier.get("objects"):
            raise ManifestError(f"required tier is incomplete or empty: {name}")
        if name == "encrypted_secrets" and (tier.get("encryption") not in {"age", "aes-256-gcm"} or tier.get("key_custody") != "operator-held-offline" or tier.get("plaintext_excluded") is not True):
            raise ManifestError("encrypted_secrets lacks encryption/key-custody evidence")
        for obj in tier["objects"]:
            rel = _text(obj.get("path"), f"{name}.object.path")
            if rel in seen:
                raise ManifestError(f"object appears in multiple tiers: {rel}")
            seen.add(rel)
            for field in ("mode", "uid", "gid", "inode", "hardlinks", "hardlink_group", "acl", "xattrs"):
                if field not in obj:
                    raise ManifestError(f"object lacks recovery metadata: {rel}.{field}")
            candidate = (root / rel).resolve()
            try:
                candidate.relative_to(root)
            except ValueError as exc:
                raise ManifestError(f"object escapes generation root: {rel}") from exc
            if verify_objects:
                if not candidate.is_file():
                    raise ManifestError(f"object missing: {rel}")
                if candidate.stat().st_size != obj.get("size"):
                    raise ManifestError(f"object size differs: {rel}")
                if sha256_file(candidate) != obj.get("sha256"):
                    raise ManifestError(f"object hash differs: {rel}")
                info = candidate.stat()
                if stat.S_IMODE(info.st_mode) != obj["mode"] or info.st_uid != obj["uid"] or info.st_gid != obj["gid"]:
                    raise ManifestError(f"object ownership/mode differs: {rel}")
                if _xattrs(candidate) != obj["xattrs"] or _acl(candidate) != obj["acl"]:
                    raise ManifestError(f"object ACL/xattrs differ: {rel}")
                identity = (info.st_dev, info.st_ino)
                prior = hardlink_groups.setdefault(obj["hardlink_group"], identity)
                if prior != identity or info.st_nlink < obj["hardlinks"]:
                    raise ManifestError(f"object hard-link relationship differs: {rel}")
    if manifest.get("object_manifest_sha256") != object_manifest_sha256:
        raise ManifestError("object manifest hash differs")
    for name in ("local_fast", "off_host_encrypted"):
        if replicas[name]["manifest_sha256"] != object_manifest_sha256:
            raise ManifestError(f"replica does not bind object manifest: {name}")


def _beneath(base: Path, candidate: Path) -> Path:
    base, candidate = base.resolve(), candidate.resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ManifestError("receipt path escapes destination") from exc
    return candidate


def seal_generation(staging: Path, manifest: dict[str, Any], destination: Path, signing_key: Ed25519PrivateKey, key_id: str) -> Path:
    """Sign and atomically publish a receipt; durable WORM evidence is mandatory."""
    validate_generation(manifest, staging, verify_objects=True)
    destination.mkdir(parents=True, exist_ok=True)
    target = _beneath(destination, destination / f"{manifest['generation']}.json")
    temporary = _beneath(destination, destination / f".{manifest['generation']}.{os.getpid()}.tmp")
    if target.exists():
        raise ManifestError(f"immutable receipt already exists: {target}")
    payload = dict(manifest)
    digest = hashlib.sha256(canonical_bytes(manifest)).digest()
    payload["manifest_sha256"] = "sha256:" + digest.hex()
    payload["signature"] = {"algorithm": "ed25519", "key_id": _text(key_id, "key_id"), "value": base64.b64encode(signing_key.sign(digest)).decode()}
    try:
        with temporary.open("xb") as stream:
            stream.write(canonical_bytes(payload))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o440)
        os.link(temporary, target)
        temporary.unlink()
        descriptor = os.open(destination, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def verify_receipt(path: Path, object_root: Path, trusted_key: Ed25519PublicKey, expected_key_id: str) -> None:
    receipt = json.loads(path.read_text())
    signature = receipt.pop("signature", None)
    recorded = receipt.pop("manifest_sha256", None)
    digest = hashlib.sha256(canonical_bytes(receipt)).digest()
    if recorded != "sha256:" + digest.hex():
        raise ManifestError("manifest hash differs")
    if not isinstance(signature, dict) or signature.get("algorithm") != "ed25519" or signature.get("key_id") != expected_key_id:
        raise ManifestError("receipt signer differs")
    try:
        trusted_key.verify(base64.b64decode(signature["value"], validate=True), digest)
    except (InvalidSignature, ValueError, KeyError) as exc:
        raise ManifestError("receipt signature differs") from exc
    validate_generation(receipt, object_root, verify_objects=True)


def _run(argv: list[str]) -> str | None:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _mount(path: Path) -> dict[str, Any]:
    raw = _run(["findmnt", "-J", "-T", str(path), "-o", "TARGET,SOURCE,FSTYPE,OPTIONS"])
    if not raw:
        return {"detected": False, "snapshot_capability": None}
    item = json.loads(raw)["filesystems"][0]
    fstype = item.get("fstype")
    return {
        "detected": True,
        "target": item.get("target"),
        "source": item.get("source"),
        "fstype": fstype,
        "options": item.get("options"),
        "snapshot_capability": fstype if fstype in {"btrfs", "zfs"} else None,
    }


def _git(path: Path) -> dict[str, Any] | None:
    if not (path / ".git").exists() and not (path / "HEAD").exists():
        return None
    annex = _run(["git", "-C", str(path), "annex", "info", "--json"])
    return {"object_sizes": _run(["git", "-C", str(path), "count-objects", "-v"]), "annex_state": json.loads(annex) if annex else None}


def inventory(paths: Iterable[Path], projected_generation_bytes: int = 0, replicas: Iterable[Path] = ()) -> dict[str, Any]:
    """Read-only topology, capacity, source-store and replica inventory."""
    entries = []
    for path in paths:
        entry: dict[str, Any] = {"path": str(path), "exists": path.exists()}
        if path.exists():
            usage = shutil.disk_usage(path)
            entry.update(
                {
                    "device": os.stat(path).st_dev,
                    "capacity": usage.total,
                    "free": usage.free,
                    "projected_generation_bytes": projected_generation_bytes,
                    "projected_generations_headroom": usage.free // projected_generation_bytes if projected_generation_bytes else None,
                    "mount": _mount(path),
                    "git": _git(path),
                }
            )
        entries.append(entry)
    now = datetime.now(UTC).timestamp()
    replica_state = []
    for path in replicas:
        receipts = list(path.glob("*.json")) if path.is_dir() else []
        newest = max((item.stat().st_mtime for item in receipts), default=None)
        replica_state.append({"path": str(path), "exists": path.exists(), "receipt_count": len(receipts), "age_seconds": now - newest if newest else None})
    schema = _run(["pg_dump", "--schema-only", "--no-owner", "--no-privileges"])
    postgresql = {
        "database_size": _run(["psql", "-Atqc", "SELECT pg_database_size(current_database())"]),
        "server_version": _run(["psql", "-Atqc", "SHOW server_version"]),
        "schema_identity": "sha256:" + hashlib.sha256(schema.encode()).hexdigest() if schema else None,
        "migration_identity": _run(["psql", "-Atqc", "SELECT version_num FROM alembic_version ORDER BY version_num"]),
    }
    plan_repo = Path("/opt/TGW/library/Plan")
    source_repo = Path("/opt/TGW/tgw-lib/src/trader-grims-warehouse")
    surface_specs = (
        ("standalone_plan_git", "authoritative", plan_repo, "approved/evidence refs and off-host readback"),
        ("canonical_source_git", "authoritative", source_repo, "canonical source refs and clean readback"),
        ("todo_branches_worktrees", "durable", source_repo, "all branches/worktrees plus dirty/untracked preservation"),
        ("postgresql_todo_queue_history", "authoritative", Path("/var/lib/postgresql"), "consistent database, schema, migrations and WAL barrier"),
        ("library_plan_materializations_runbooks_archive", "durable", Path("/opt/TGW/library"), "Plan materializations, runbooks and archive manifests"),
        ("tgw_lib_context_doctor_coding_queue", "durable", Path("/opt/TGW/tgw-lib"), "configuration, context inputs/projection, lifecycle receipts and queue evidence"),
        ("master_itemdata_media_history_annex", "authoritative", Path("/opt/TGW/data"), "original ItemData/media/history and annex/GDrive/archive manifests"),
        ("unix_users_groups_ownership", "authoritative", Path("/etc"), "passwd/group identities plus ownership, ACL and xattr requirements"),
        ("encrypted_secrets_operator_custody", "authoritative-protected", Path("/opt/TGW/tgw-lib"), "separate encryption; operator-held offline key; no plaintext in generation"),
        ("backup_health_receipts_alerts", "durable", Path("/opt/TGW/tgw-lib"), "generation age/capacity/health, immutable receipts and alerts"),
        ("reproducible_projections", "regenerable", Path("/opt/TGW/tgw-lib"), "exclude only under an explicit regeneration contract"),
    )
    surfaces = []
    for name, classification, path, requirement in surface_specs:
        item: dict[str, Any] = {"name": name, "classification": classification, "path": str(path), "exists": path.exists(), "recovery_requirement": requirement}
        if name in {"standalone_plan_git", "canonical_source_git"}:
            item["git"] = _git(path)
            item["refs"] = _run(["git", "-C", str(path), "for-each-ref", "--format=%(refname) %(objectname)"])
            item["head_commit"] = _run(["git", "-C", str(path), "rev-parse", "HEAD"])
            item["head_tree"] = _run(["git", "-C", str(path), "rev-parse", "HEAD^{tree}"])
        if name == "todo_branches_worktrees":
            item["worktrees"] = _run(["git", "-C", str(path), "worktree", "list", "--porcelain"])
            item["dirty_state"] = _run(["git", "-C", str(path), "status", "--porcelain=v1", "--untracked-files=all"])
        if name == "unix_users_groups_ownership":
            item["identity_sources"] = ["/etc/passwd", "/etc/group"]
            item["metadata_required"] = ["uid", "gid", "mode", "ACL", "xattr", "hardlinks"]
        if name == "encrypted_secrets_operator_custody":
            item["custody"] = "operator-held-offline"
            item["plaintext_must_be_excluded"] = True
        surfaces.append(item)
    return {
        "schema": "tgw-lib-recovery-inventory/v2",
        "observed_at": datetime.now(UTC).isoformat(),
        "host": {"system": platform.system(), "release": platform.release(), "machine": platform.machine()},
        "paths": entries,
        "postgresql": postgresql,
        "replicas": replica_state,
        "surfaces": surfaces,
        "tools": {name: _run([name, "--version"]) for name in ("git", "pg_dump", "pg_basebackup", "age", "restic", "borg", "btrfs", "zfs", "getfacl")},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only tgw-lib recovery inventory")
    sub = parser.add_subparsers(dest="action", required=True)
    inv = sub.add_parser("inventory")
    inv.add_argument("paths", nargs="*", type=Path)
    inv.add_argument("--projected-generation-bytes", type=int, default=0)
    inv.add_argument("--replica", action="append", type=Path, default=[])
    verify = sub.add_parser("verify")
    verify.add_argument("receipt", type=Path)
    verify.add_argument("object_root", type=Path)
    verify.add_argument("trusted_public_key", type=Path)
    verify.add_argument("key_id")
    args = parser.parse_args(argv)
    try:
        if args.action == "verify":
            key = load_pem_public_key(args.trusted_public_key.read_bytes())
            if not isinstance(key, Ed25519PublicKey):
                raise ManifestError("trusted key is not Ed25519")
            verify_receipt(args.receipt, args.object_root, key, args.key_id)
            print("complete")
            return 0
        paths = args.paths or [Path("/opt/TGW/library"), Path("/opt/TGW/tgw-lib"), Path("/opt/TGW/data")]
        print(json.dumps(inventory(paths, args.projected_generation_bytes, args.replica), sort_keys=True, indent=2))
        return 0
    except (ManifestError, OSError, json.JSONDecodeError) as exc:
        print(f"incomplete: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
