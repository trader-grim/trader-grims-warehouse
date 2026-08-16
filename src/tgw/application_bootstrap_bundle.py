"""Deterministic protected controller-bundle producer for W09.

The producer is deliberately post-commit: it reads one exact controller source
commit through the held Git authority, retains that archive, and creates one
source-only zip application.  The resulting receipt explicitly distinguishes
the controller source from the application candidate it is authorized to
install.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import tarfile
import zipfile
from pathlib import Path
from typing import Any, Mapping

from tgw.application_deployment_contract import (
    PROJECTION_PATH,
    ProtectedGitObjectReader,
)

SCHEMA = "tgw-w09-controller-bundle-receipt/v1"
_GIT = re.compile(r"[0-9a-f]{40}")
_SHA = re.compile(r"sha256:[0-9a-f]{64}")
_MAIN = b"from tgw.application_bootstrap_entrypoint import main\nraise SystemExit(main())\n"


class ControllerBundleError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _exact_application_candidate(value: Mapping[str, Any]) -> dict[str, str]:
    fields = {"commit", "tree", "archive_sha256", "projection_sha256"}
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ControllerBundleError("application candidate binding is invalid")
    result = {name: str(value[name]) for name in fields}
    if _GIT.fullmatch(result["commit"]) is None or _GIT.fullmatch(result["tree"]) is None or _SHA.fullmatch(result["archive_sha256"]) is None or _SHA.fullmatch(result["projection_sha256"]) is None:
        raise ControllerBundleError("application candidate identity is invalid")
    return result


def _bundle_from_archive(archive: bytes) -> tuple[bytes, str, str, bytes]:
    if not archive or len(archive) > 64 * 1024 * 1024:
        raise ControllerBundleError("controller source archive exceeds its bound")
    files: dict[str, bytes] = {}
    projection: bytes | None = None
    launcher_source: bytes | None = None
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as source:
            members = source.getmembers()
            if len(members) > 100_000:
                raise ControllerBundleError("controller source archive has too many entries")
            for member in members:
                path = Path(member.name)
                if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
                    raise ControllerBundleError("controller source archive contains an unsafe entry")
                if path.suffix in {".pyc", ".pyo"} or "__pycache__" in path.parts:
                    raise ControllerBundleError("controller source archive contains bytecode")
                if member.name == PROJECTION_PATH:
                    extracted = source.extractfile(member)
                    if extracted is None:
                        raise ControllerBundleError("controller Plan projection is not a file")
                    projection = extracted.read(4 * 1024 * 1024 + 1)
                if member.name == "src/tgw/w09_controller_launcher.c":
                    extracted = source.extractfile(member)
                    if extracted is None:
                        raise ControllerBundleError("controller launcher source is not a file")
                    launcher_source = extracted.read(1024 * 1024 + 1)
                if not member.isfile():
                    continue
                if member.name.startswith("src/tgw/"):
                    bundle_name = member.name.removeprefix("src/")
                elif member.name in {
                    "agent-services/providers/promptcraft/promptcraft/__init__.py",
                    "agent-services/providers/promptcraft/promptcraft/core.py",
                    "agent-services/providers/promptcraft/promptcraft/handoff.py",
                }:
                    bundle_name = member.name.removeprefix("agent-services/providers/promptcraft/")
                else:
                    continue
                extracted = source.extractfile(member)
                if extracted is None:
                    raise ControllerBundleError("controller module is not readable")
                raw = extracted.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise ControllerBundleError("controller module exceeds its bound")
                files[bundle_name] = raw
    except (tarfile.TarError, OSError) as exc:
        raise ControllerBundleError("controller source archive is invalid") from exc
    if projection is None or len(projection) > 4 * 1024 * 1024:
        raise ControllerBundleError("controller source Plan projection is absent")
    if launcher_source is None or len(launcher_source) > 1024 * 1024:
        raise ControllerBundleError("controller native launcher source is absent")
    required = {
        "tgw/application_bootstrap_entrypoint.py",
        "tgw/application_deployment_contract.py",
        "tgw/application_release_provider.py",
        "tgw/bootstrap_authority.py",
        "tgw/candidate_receipt_sink.py",
        "tgw/deployment_runtime.py",
        "tgw/effect_completion_store.py",
        "tgw/effect_handlers.py",
        "promptcraft/__init__.py",
        "promptcraft/core.py",
        "promptcraft/handoff.py",
    }
    if not required.issubset(files):
        raise ControllerBundleError("controller bundle source closure is incomplete")
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_STORED) as bundle:
        for name, raw in sorted({"__main__.py": _MAIN, **files}.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100444 << 16
            bundle.writestr(info, raw)
    return output.getvalue(), _digest(projection), _digest(launcher_source), launcher_source


def _open_protected_root(root: Path, trusted_uid: int) -> tuple[int, tuple[int, ...]]:
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in root.parts[1:]:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=fd,
            )
            metadata = os.fstat(child)
            if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid not in {0, trusted_uid} or stat.S_IMODE(metadata.st_mode) & 0o022:
                os.close(child)
                raise ControllerBundleError("controller output ancestor is mutable")
            os.close(fd)
            fd = child
        held = os.fstat(fd)
        identity = (
            held.st_dev,
            held.st_ino,
            held.st_uid,
            held.st_gid,
            held.st_mode,
        )
        if held.st_uid != trusted_uid or stat.S_IMODE(held.st_mode) != 0o700:
            raise ControllerBundleError("controller bundle root is not protected")
        return fd, identity
    except Exception:
        os.close(fd)
        raise


def _write_once(
    root_fd: int,
    name: str,
    raw: bytes,
    mode: int,
) -> tuple[int, ...]:
    if "/" in name or not name:
        raise ControllerBundleError("controller artifact name is invalid")
    created = False
    fd = os.open(
        name,
        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        mode,
        dir_fd=root_fd,
    )
    created = True
    try:
        offset = 0
        while offset < len(raw):
            count = os.write(fd, raw[offset:])
            if count <= 0:
                raise OSError("short controller artifact write")
            offset += count
        os.fchmod(fd, mode)
        os.fsync(fd)
        held = os.fstat(fd)
        observed = os.pread(fd, len(raw) + 1, 0)
        named = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        if observed != raw or held.st_nlink != 1 or stat.S_IMODE(held.st_mode) != mode or (held.st_dev, held.st_ino) != (named.st_dev, named.st_ino):
            raise OSError("controller artifact readback/identity differs")
    except Exception:
        os.close(fd)
        if created:
            os.unlink(name, dir_fd=root_fd)
            os.fsync(root_fd)
        raise
    else:
        os.close(fd)
    os.fsync(root_fd)
    return (
        held.st_dev,
        held.st_ino,
        held.st_uid,
        held.st_gid,
        held.st_mode,
        held.st_nlink,
        held.st_size,
    )


def produce_controller_bundle(
    *,
    source: ProtectedGitObjectReader,
    commit: str,
    output_root: Path,
    application_candidate: Mapping[str, Any],
    trusted_uid: int = 0,
) -> dict[str, Any]:
    """Produce immutable controller archive/bundle/receipt from exact Git."""

    if type(source) is not ProtectedGitObjectReader or _GIT.fullmatch(commit) is None:
        raise ControllerBundleError("protected controller Git authority is unavailable")
    root = Path(output_root)
    if not root.is_absolute():
        raise ControllerBundleError("controller bundle root is not absolute")
    root_fd, root_identity = _open_protected_root(root, trusted_uid)
    created: list[str] = []
    try:
        resolved_commit, tree = source.identity(commit)
        if resolved_commit != commit:
            raise ControllerBundleError("controller source commit did not resolve exactly")
        archive = source.run_git("archive", "--format=tar", commit)
        bundle, projection_sha256, launcher_source_sha256, launcher_source = _bundle_from_archive(archive)
        app_candidate = _exact_application_candidate(application_candidate)
        archive_sha256 = _digest(archive)
        bundle_sha256 = _digest(bundle)
        stem = f"controller-{commit}-{bundle_sha256.removeprefix('sha256:')}"
        archive_path = root / f"{stem}.tar"
        bundle_path = root / f"{stem}.pyz"
        launcher_source_path = root / f"{stem}.launcher.c"
        archive_identity = _write_once(root_fd, archive_path.name, archive, 0o400)
        created.append(archive_path.name)
        bundle_identity = _write_once(root_fd, bundle_path.name, bundle, 0o400)
        created.append(bundle_path.name)
        launcher_source_identity = _write_once(
            root_fd,
            launcher_source_path.name,
            launcher_source,
            0o400,
        )
        created.append(launcher_source_path.name)
        unsigned = {
            "schema": SCHEMA,
            "controller_source": {
                "commit": commit,
                "tree": tree,
                "archive_path": str(archive_path),
                "archive_sha256": archive_sha256,
                "archive_size": len(archive),
                "projection_sha256": projection_sha256,
            },
            "controller_bundle": {
                "path": str(bundle_path),
                "sha256": bundle_sha256,
                "size": len(bundle),
                "identity": list(bundle_identity),
                "bytecode_policy": "-B-source-only-zip",
            },
            "controller_launcher_source": {
                "archive_path": "src/tgw/w09_controller_launcher.c",
                "materialized_path": str(launcher_source_path),
                "sha256": launcher_source_sha256,
                "size": len(launcher_source),
                "identity": list(launcher_source_identity),
                "build_contract": "static-elf-no-interp-no-needed@1",
            },
            "application_candidate": app_candidate,
            "materialization": {
                "producer": "protected-git-controller-bundle@1",
                "archive_identity": list(archive_identity),
                "root": str(root),
            },
        }
        receipt = {**unsigned, "receipt_sha256": _digest(_canonical(unsigned))}
        receipt_path = root / f"{stem}.receipt.json"
        _write_once(root_fd, receipt_path.name, _canonical(receipt), 0o400)
        created.append(receipt_path.name)
        named_root = root.lstat()
        if root_identity != (
            named_root.st_dev,
            named_root.st_ino,
            named_root.st_uid,
            named_root.st_gid,
            named_root.st_mode,
        ):
            raise ControllerBundleError("controller output root changed during materialization")
        source.postcheck()
        return {**receipt, "receipt_path": str(receipt_path)}
    except Exception:
        cleanup_error = None
        for name in reversed(created):
            try:
                os.unlink(name, dir_fd=root_fd)
            except OSError as exc:
                cleanup_error = exc
        try:
            os.fsync(root_fd)
        except OSError as exc:
            cleanup_error = exc
        if cleanup_error is not None:
            raise ControllerBundleError("controller bundle cleanup is ambiguous") from cleanup_error
        raise
    finally:
        os.close(root_fd)
