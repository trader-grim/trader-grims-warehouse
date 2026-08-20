"""Immutable TGW source-release materializer and selector.

All state changes are confined beneath an explicit TGW root.  Selection is a
compare-and-swap against the exact current generation and is recorded by a
durable intent before the atomic symlink replacement.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import tarfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping

from tgw.admission_recovery import (
    AdmissionRecoveryError,
    validate_environment_preflight_for_admission,
    validate_release_admission,
)

SCHEMA = "tgw-release-manifest-v1"
RECEIPT_SCHEMA = "tgw-immutable-release-selection-v1"
REFUSAL_SCHEMA = "tgw-immutable-release-refusal-v1"
RUNTIME_SCHEMA = "tgw-release-runtime-files-v1"
_HEX = re.compile(r"^[0-9a-f]+$")
_GENERATION = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


class ReleaseError(RuntimeError):
    """A release could not be safely materialized or selected."""


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


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
    if not _GENERATION.fullmatch(value):
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
    root.mkdir(parents=True, exist_ok=True)
    for name in ("releases", "operations", "receipts", "refusals"):
        path = root / name
        path.mkdir(exist_ok=True)
        if path.is_symlink() or not path.is_dir():
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
    current_time: str | None = None,
) -> dict[str, Any]:
    """Select only after revalidating exact W15/W16 evidence at this boundary."""
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
    try:
        manifest = _read_json(root / "releases" / _generation(generation) / ".release-manifest.json")
        admission = validate_release_admission(
            admission_receipt,
            candidate_commit=manifest["commit"],
            candidate_tree=manifest["git_tree"],
            trusted_public_key=admission_public_key,
            current_time=current_time,
            current_plan_commit=current_plan_commit,
            current_solution_hash=current_solution_hash,
        )
        validate_environment_preflight_for_admission(
            environment_preflight_receipt,
            catalog_hash=admission["environment"]["catalog_hash"],
            receipt_hash=admission["environment"]["receipt_hash"],
            trusted_public_key=environment_public_key,
            current_time=current_time,
        )
    except (AdmissionRecoveryError, KeyError) as exc:
        record_refusal(root, generation, operation_id, reason="invalid-admission-evidence")
        raise ReleaseError(f"release admission refused: {exc}") from exc
    return _select(root, generation, expected_current=expected_current, operation_id=operation_id)


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
    parser.add_argument("--root", type=Path, default=Path("/opt/tgw-releases"))
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
    args = parser.parse_args()
    try:
        if args.command == "install":
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
                raise ReleaseError("admission receipt is unavailable or invalid") from exc
            try:
                admission_public_key = _read_trusted_public_key(args.admission_public_key)
                environment_public_key = _read_trusted_public_key(args.environment_public_key)
                current_time = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
                validate_release_admission(
                    admission,
                    candidate_commit=args.commit,
                    candidate_tree=args.tree,
                    trusted_public_key=admission_public_key,
                    current_time=current_time,
                    current_plan_commit=args.current_plan_commit,
                    current_solution_hash=args.current_solution_hash,
                )
                preflight = json.loads(args.environment_preflight_receipt.read_text(encoding="utf-8"))
                validate_environment_preflight_for_admission(
                    preflight,
                    catalog_hash=admission["environment"]["catalog_hash"],
                    receipt_hash=admission["environment"]["receipt_hash"],
                    trusted_public_key=environment_public_key,
                    current_time=current_time,
                )
            except AdmissionRecoveryError as exc:
                raise ReleaseError(f"release admission refused: {exc}") from exc
            except (OSError, json.JSONDecodeError) as exc:
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
        else:
            result = {"completed": recover(args.root), "current": current_generation(args.root)}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except ReleaseError as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
