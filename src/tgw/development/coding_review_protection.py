"""Root-owned preparation for the existing governed local-review request.

The ordinary coding lifecycle can name only an already recorded candidate.
All paths, provider material, evidence bindings, snapshot bytes, and the final
request are reconstructed below from fixed protected configuration.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib import error as urllib_error
from urllib import request as urllib_request

from tgw.candidate_receipt_sink import (
    CANDIDATE_EVIDENCE_CARD_BINDING_SCHEMA,
    PinnedCandidateEvidenceDescriptor,
)
from tgw.candidate_review import build_executable_review_packet
from tgw.code_graph.provider import build_snapshot as build_codegraph_snapshot
from tgw.execution_resources import (
    card_resource_receipt,
    resource_service_catalog_hash,
    resource_service_descriptor_hash,
    validate_resource_service_catalog,
    validate_resource_service_descriptor,
    verify_resource_service_registration,
)
from tgw.governed_resource_service import (
    CARD_RESOURCE_NAMES,
    MAX_RESOURCE_BYTES,
    RESOURCE_GENERATION_SCHEMA,
    load_registered_resource_generation,
)
from tgw.governed_review_adapter import (
    _validate_evidence_sink_descriptor,
    snapshot_hash,
)
from tgw.governed_review_context_broker import FileReviewContextGrantStore
from tgw.review_contract import ReviewRunnerError

PROTECTED_REVIEW_ROOT = Path("/var/lib/tgw/coding-protected-review")
PROTECTED_REVIEW_CONFIG = PROTECTED_REVIEW_ROOT / "config.json"
PROTECTED_REVIEW_PROFILE = PROTECTED_REVIEW_ROOT / "request-profile.json"
PROTECTED_REVIEW_REQUEST_ROOT = PROTECTED_REVIEW_ROOT / "requests"
PROTECTED_REVIEW_SNAPSHOT_ROOT = PROTECTED_REVIEW_ROOT / "snapshots"
PROTECTED_CANDIDATE_DESCRIPTOR = (
    PROTECTED_REVIEW_ROOT / "candidate-evidence-descriptor.json"
)
PROTECTED_EXECUTION_SINK = PROTECTED_REVIEW_ROOT / "execution-evidence-sink.json"
PROTECTED_EXECUTION_PIN_SOURCE = (
    PROTECTED_REVIEW_ROOT / "execution-evidence-published.json"
)

PROFILE_SCHEMA = "tgw-local-coding-protected-review-profile/v2"
CREDENTIAL_SCHEMA = "tgw-protected-service-credential/v1"
CONTEXT_CREDENTIAL_ENV = "TGW_REVIEW_CONTEXT_CREDENTIAL"
EVIDENCE_CREDENTIAL_ENV = "TGW_REVIEW_EVIDENCE_CREDENTIAL"
RESOURCE_CREDENTIAL_ENV = "TGW_REVIEW_RESOURCE_CREDENTIAL"
BROKER_CREDENTIAL_ENV = "TGW_CONTEXT_BROKER_REQUEST_CREDENTIAL"
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100_000
MAX_ARCHIVE_FILE_BYTES = 64 * 1024 * 1024
MAX_EXTRACTED_BYTES = 512 * 1024 * 1024
MIN_AVAILABLE_BYTES = 512 * 1024 * 1024
_BASE_RESOURCE_NAMES = frozenset({
    "plan_input", "plan_commit", "plan_graph", "execution_environment",
    "authority_conditions",
})
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _git(repository: Path, *arguments: str, binary: bool = False) -> bytes | str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={repository.resolve()}", *arguments],
        cwd=repository,
        check=False,
        capture_output=True,
        text=not binary,
    )
    if result.returncode:
        detail = result.stderr if isinstance(result.stderr, str) else result.stderr.decode()
        raise ReviewRunnerError(detail[-500:] or "protected review Git probe failed")
    return result.stdout


def _available(path: Path, required: int) -> None:
    if required < 0 or shutil.disk_usage(path).free - required < MIN_AVAILABLE_BYTES:
        raise ReviewRunnerError("protected governed-review storage space is insufficient")


def _write_all(descriptor: int, value: bytes) -> None:
    view = memoryview(value)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise ReviewRunnerError("protected governed-review write failed")
        view = view[written:]


def _stream_git_archive(repository: Path, revision: str, destination: Path) -> str:
    """Stream one archive to disk with an enforced byte and space ceiling."""

    process = subprocess.Popen(
        [
            "git", "-c", f"safe.directory={repository.resolve()}",
            "archive", "--format=tar", revision,
        ],
        cwd=repository,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    digest = hashlib.sha256()
    written = 0
    try:
        assert process.stdout is not None
        descriptor = os.open(
            destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400,
        )
        try:
            while True:
                block = process.stdout.read(1024 * 1024)
                if not block:
                    break
                written += len(block)
                if written > MAX_ARCHIVE_BYTES:
                    raise ReviewRunnerError("candidate Git archive exceeds its bound")
                _available(destination.parent, len(block))
                _write_all(descriptor, block)
                digest.update(block)
            os.fsync(descriptor)
            os.fchmod(descriptor, 0o400)
        finally:
            os.close(descriptor)
        stderr = process.stderr.read(4096) if process.stderr is not None else b""
        returncode = process.wait(timeout=30)
        if returncode:
            raise ReviewRunnerError(
                stderr.decode(errors="replace")[-500:] or "protected review Git archive failed"
            )
        return "sha256:" + digest.hexdigest()
    except Exception:
        process.kill()
        process.wait(timeout=5)
        raise


def _extract_snapshot(archive: Path, destination: Path) -> None:
    destination.mkdir(mode=0o700)
    members = 0
    extracted = 0
    with tarfile.open(archive, mode="r|") as stream:
        for member in stream:
            members += 1
            if members > MAX_ARCHIVE_MEMBERS:
                raise ReviewRunnerError("candidate archive member count exceeds its bound")
            relative = Path(member.name)
            if (
                relative.is_absolute()
                or not relative.parts
                or any(part in ("", ".", "..") for part in relative.parts)
                or not (member.isdir() or member.isfile())
            ):
                raise ReviewRunnerError("candidate archive contains an unsafe entry")
            target = destination / relative
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
                continue
            if member.size < 0 or member.size > MAX_ARCHIVE_FILE_BYTES:
                raise ReviewRunnerError("candidate archive file exceeds its bound")
            extracted += member.size
            if extracted > MAX_EXTRACTED_BYTES:
                raise ReviewRunnerError("candidate archive extracted size exceeds its bound")
            _available(destination.parent, member.size)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            source = stream.extractfile(member)
            if source is None:
                raise ReviewRunnerError("candidate archive entry is unreadable")
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o400,
            )
            try:
                with os.fdopen(descriptor, "wb", closefd=False) as output:
                    remaining = member.size
                    while remaining:
                        block = source.read(min(1024 * 1024, remaining))
                        if not block:
                            raise ReviewRunnerError("candidate archive entry is truncated")
                        output.write(block)
                        remaining -= len(block)
                    if source.read(1):
                        raise ReviewRunnerError("candidate archive entry exceeds its header")
                    output.flush()
                    os.fsync(descriptor)
                os.fchmod(descriptor, 0o444)
            finally:
                os.close(descriptor)
    directories = [destination, *(item for item in destination.rglob("*") if item.is_dir())]
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        directory.chmod(0o555)


def _write_snapshot_preimage(snapshot: Path, destination: Path) -> None:
    entries: list[tuple[str, Path, int]] = []
    aggregate = 0
    for path in sorted(snapshot.rglob("*"), key=lambda item: item.relative_to(snapshot).as_posix()):
        if path.is_symlink():
            raise ReviewRunnerError("candidate snapshot contains a symbolic link")
        if not path.is_file():
            continue
        observed = path.stat(follow_symlinks=False)
        if observed.st_size > MAX_ARCHIVE_FILE_BYTES:
            raise ReviewRunnerError("candidate snapshot file exceeds its bound")
        aggregate += observed.st_size
        if len(entries) >= MAX_ARCHIVE_MEMBERS or aggregate > MAX_EXTRACTED_BYTES:
            raise ReviewRunnerError("candidate snapshot exceeds its aggregate bound")
        entries.append((path.relative_to(snapshot).as_posix(), path, observed.st_size))
    _available(destination.parent, aggregate)
    descriptor = os.open(
        destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400,
    )
    try:
        _write_all(descriptor, b"tgw-review-snapshot/v2\0")
        for relative, path, size in entries:
            encoded = relative.encode("utf-8")
            _write_all(descriptor, len(encoded).to_bytes(8, "big"))
            _write_all(descriptor, encoded)
            _write_all(descriptor, size.to_bytes(8, "big"))
            with path.open("rb") as source:
                remaining = size
                while remaining:
                    block = source.read(min(1024 * 1024, remaining))
                    if not block:
                        raise ReviewRunnerError("candidate snapshot changed while registered")
                    _write_all(descriptor, block)
                    remaining -= len(block)
                if source.read(1):
                    raise ReviewRunnerError("candidate snapshot changed while registered")
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o400)
    finally:
        os.close(descriptor)


def _root_owned_snapshot(path: Path) -> None:
    entries = [path, *path.rglob("*")]
    for entry in entries:
        observed = entry.stat(follow_symlinks=False)
        if (
            entry.is_symlink()
            or observed.st_uid != 0
            or observed.st_gid != 0
            or observed.st_mode & 0o022
            or not (stat.S_ISDIR(observed.st_mode) or stat.S_ISREG(observed.st_mode))
        ):
            raise ReviewRunnerError(
                "protected governed-review snapshot is writable or substituted"
            )


def _discard_root_owned_snapshot(path: Path) -> None:
    _root_owned_snapshot(path)
    directories = [path, *(entry for entry in path.rglob("*") if entry.is_dir())]
    for directory in directories:
        directory.chmod(0o700)
    shutil.rmtree(path)


def _atomic_root_json(path: Path, value: Mapping[str, Any], *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        payload = _canonical(value) + b"\n"
        os.write(descriptor, payload)
        os.fsync(descriptor)
        os.fchown(descriptor, 0, 0)
        os.fchmod(descriptor, mode)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary_path, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _protected_json(path: Path, label: str, trusted_uid: int) -> dict[str, Any]:
    from tgw import context_generation_status

    try:
        context_generation_status._protected_directory(
            path.parent, f"{label} parent", trusted_uid
        )
        return context_generation_status._protected_json(
            path, label, trusted_uid
        )
    except context_generation_status.ContextGenerationStatusError as exc:
        raise ReviewRunnerError(f"{label} is unavailable") from exc


def validate_profile(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate every protected profile field used to construct a request."""

    required = {
        "schema",
        "provider_identity",
        "environment",
        "evidence_sink",
        "resource_service",
        "resource_service_catalog",
        "registered_resource_service",
        "registered_resource_service_catalog",
        "registered_resource_inputs",
        "credential_generations",
        "receiver_profile",
        "environment_preflight_receipt",
        "skill_contract_hash",
        "timeout_seconds",
        "output_limit",
    }
    if set(value) != required or value.get("schema") != PROFILE_SCHEMA:
        raise ReviewRunnerError("coding protected-review profile is invalid")
    provider_identity = value.get("provider_identity")
    environment = value.get("environment")
    evidence_sink = value.get("evidence_sink")
    resource_service = value.get("resource_service")
    resource_catalog = value.get("resource_service_catalog")
    registered_service = value.get("registered_resource_service")
    registered_catalog = value.get("registered_resource_service_catalog")
    registered_inputs = value.get("registered_resource_inputs")
    credential_generations = value.get("credential_generations")
    receiver_profile = value.get("receiver_profile")
    preflight = value.get("environment_preflight_receipt")
    if (
        not isinstance(provider_identity, Mapping)
        or not isinstance(environment, Mapping)
        or not all(
            isinstance(name, str) and isinstance(item, str)
            for name, item in environment.items()
        )
        or not isinstance(evidence_sink, Mapping)
        or not isinstance(resource_service, Mapping)
        or not isinstance(resource_catalog, Mapping)
        or not isinstance(registered_service, Mapping)
        or not isinstance(registered_catalog, Mapping)
        or not isinstance(registered_inputs, Mapping)
        or set(registered_inputs) != _BASE_RESOURCE_NAMES
        or not isinstance(credential_generations, Mapping)
        or set(credential_generations) != {"context", "evidence", "resource", "broker"}
        or not all(
            isinstance(item, str) and item
            for item in credential_generations.values()
        )
        or not isinstance(receiver_profile, Mapping)
        or not receiver_profile
        or not isinstance(preflight, Mapping)
        or not isinstance(value.get("skill_contract_hash"), str)
        or _SHA256.fullmatch(value["skill_contract_hash"]) is None
        or not isinstance(value.get("timeout_seconds"), int)
        or isinstance(value.get("timeout_seconds"), bool)
        or not 1 <= value["timeout_seconds"] <= 900
        or not isinstance(value.get("output_limit"), int)
        or isinstance(value.get("output_limit"), bool)
        or not 1024 <= value["output_limit"] <= 64 * 1024 * 1024
    ):
        raise ReviewRunnerError("coding protected-review profile bindings are invalid")
    try:
        provider = provider_identity["provider"]
        provider_argv = provider_identity["argv_template"]
        execution_environment = provider_identity["artifacts"][
            "execution_environment"
        ]
        command_policy = provider_identity["command_policy"]
        service_identity = provider_identity["context_bundle_service"]
        sandbox_identity = provider_identity["sandbox_identity"]
        execution_path = Path(execution_environment["resolved_path"])
        hashes = (
            execution_environment["content_sha256"],
            service_identity["resource_service_descriptor_hash"],
            service_identity["resource_service_catalog_hash"],
            evidence_sink["descriptor_hash"],
        )
        strings = (
            provider,
            evidence_sink["sink_ref"],
            resource_service["id"],
            resource_service["client_id"],
            service_identity["client_id"],
            service_identity["resource_service_catalog_ref"],
        )
        if (
            not all(isinstance(item, str) and item for item in strings)
            or not all(isinstance(item, str) and _SHA256.fullmatch(item) for item in hashes)
            or not isinstance(provider_argv, list)
            or not all(isinstance(item, str) for item in provider_argv)
            or provider_argv.count("{prompt}") != 1
            or provider_argv.count("{snapshot}") != 1
            or provider_argv.count("{mcp_config}") != 1
            or not execution_path.is_absolute()
            or not isinstance(command_policy, Mapping)
            or not isinstance(service_identity, Mapping)
            or not isinstance(sandbox_identity, Mapping)
            or not isinstance(sandbox_identity.get("uid"), int)
            or isinstance(sandbox_identity.get("uid"), bool)
            or not isinstance(sandbox_identity.get("gid"), int)
            or isinstance(sandbox_identity.get("gid"), bool)
        ):
            raise ReviewRunnerError(
                "coding protected-review provider profile is invalid"
            )
        normalized_service = validate_resource_service_descriptor(resource_service)
        normalized_catalog = validate_resource_service_catalog(resource_catalog)
        verify_resource_service_registration(
            normalized_catalog, normalized_service,
        )
        normalized_registered_service = validate_resource_service_descriptor(
            registered_service
        )
        normalized_registered_catalog = validate_resource_service_catalog(
            registered_catalog
        )
        verify_resource_service_registration(
            normalized_registered_catalog, normalized_registered_service,
        )
        _validate_evidence_sink_descriptor(evidence_sink)
        if (
            normalized_service["id"] != service_identity["service_id"]
            or normalized_service["client_id"] != service_identity["client_id"]
            or resource_service_descriptor_hash(normalized_service)
            != service_identity["resource_service_descriptor_hash"]
            or normalized_catalog["catalog_ref"]
            != service_identity["resource_service_catalog_ref"]
            or resource_service_catalog_hash(normalized_catalog)
            != service_identity["resource_service_catalog_hash"]
            or service_identity.get("credential_env") != CONTEXT_CREDENTIAL_ENV
            or evidence_sink.get("credential_env") != EVIDENCE_CREDENTIAL_ENV
            or normalized_registered_service.get("credential_env")
            != RESOURCE_CREDENTIAL_ENV
        ):
            raise ReviewRunnerError(
                "coding protected-review resource service profile is invalid"
            )
        for name, binding in registered_inputs.items():
            if (
                not isinstance(binding, Mapping)
                or set(binding) != {"ref", "hash", "path"}
                or not isinstance(binding.get("ref"), str)
                or not binding["ref"].startswith("mcp:tgw-context/")
                or not isinstance(binding.get("hash"), str)
                or _SHA256.fullmatch(binding["hash"]) is None
                or not isinstance(binding.get("path"), str)
                or not Path(binding["path"]).is_absolute()
            ):
                raise ReviewRunnerError(
                    f"coding protected-review registered input is invalid: {name}"
                )
    except (KeyError, TypeError, ValueError) as exc:
        raise ReviewRunnerError(
            "coding protected-review provider profile is invalid"
        ) from exc
    return dict(value)


def load_profile(path: Path, *, trusted_uid: int = 0) -> dict[str, Any]:
    value = _protected_json(path, "coding protected-review profile", trusted_uid)
    return validate_profile(value)


def load_service_credential(
    path: Path, *, purpose: str, generation: str, trusted_uid: int = 0,
) -> str:
    value = _protected_json(path, f"coding protected-review {purpose} credential", trusted_uid)
    if (
        set(value) != {"schema", "purpose", "generation", "bearer"}
        or value.get("schema") != CREDENTIAL_SCHEMA
        or value.get("purpose") != purpose
        or value.get("generation") != generation
        or not isinstance(value.get("bearer"), str)
        or len(value["bearer"]) < 32
        or any(character.isspace() for character in value["bearer"])
    ):
        raise ReviewRunnerError(f"coding protected-review {purpose} credential is stale or invalid")
    return str(value["bearer"])


def _protected_resource(path: Path, expected_hash: str) -> None:
    from tgw import context_generation_status

    try:
        context_generation_status._protected_directory(
            path.parent, "registered review resource parent", 0,
        )
        named = path.lstat()
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except (OSError, context_generation_status.ContextGenerationStatusError) as exc:
        raise ReviewRunnerError("registered review resource is unavailable") from exc
    try:
        observed = os.fstat(descriptor)
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_uid != 0
            or observed.st_gid != 0
            or stat.S_IMODE(observed.st_mode) & 0o022
            or observed.st_nlink != 1
            or observed.st_size > MAX_RESOURCE_BYTES
            or (named.st_dev, named.st_ino) != (observed.st_dev, observed.st_ino)
        ):
            raise ReviewRunnerError("registered review resource is not protected")
        digest = hashlib.sha256()
        offset = 0
        while offset < observed.st_size:
            block = os.pread(descriptor, min(1024 * 1024, observed.st_size - offset), offset)
            if not block:
                raise ReviewRunnerError("registered review resource changed while held")
            digest.update(block)
            offset += len(block)
        after = os.fstat(descriptor)
        if (
            "sha256:" + digest.hexdigest() != expected_hash
            or any(
                getattr(after, field) != getattr(observed, field)
                for field in (
                    "st_dev", "st_ino", "st_mode", "st_uid", "st_gid",
                    "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns",
                )
            )
        ):
            raise ReviewRunnerError("registered review resource content differs")
    finally:
        os.close(descriptor)


def _copy_registered_resource(source: Path, destination: Path) -> str:
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    target_fd = os.open(
        destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400,
    )
    digest = hashlib.sha256()
    total = 0
    try:
        while True:
            block = os.read(source_fd, 1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > MAX_RESOURCE_BYTES:
                raise ReviewRunnerError("registered review resource exceeds its bound")
            _write_all(target_fd, block)
            digest.update(block)
        os.fsync(target_fd)
        os.fchown(target_fd, 0, 0)
        os.fchmod(target_fd, 0o444)
    finally:
        os.close(source_fd)
        os.close(target_fd)
    return "sha256:" + digest.hexdigest()


def _discard_registration(path: Path) -> None:
    if not path.exists():
        return
    for entry in [path, *path.rglob("*")]:
        observed = entry.stat(follow_symlinks=False)
        if entry.is_symlink() or observed.st_uid != 0 or observed.st_gid != 0:
            raise ReviewRunnerError("registered review resource staging is unsafe")
    for directory in [path, *(entry for entry in path.rglob("*") if entry.is_dir())]:
        directory.chmod(0o700)
    shutil.rmtree(path)


def _register_resource_generation(
    registry_root: Path, *, candidate_commit: str, candidate_tree: str,
    sources: Mapping[str, tuple[str, Path, str | None]],
) -> dict[str, Any]:
    if set(sources) != CARD_RESOURCE_NAMES:
        raise ReviewRunnerError("registered review resource coverage is incomplete")
    protected_identity(registry_root, uid=0, mode=0o755)
    final = registry_root / candidate_commit
    staging = registry_root / f".{candidate_commit}.{secrets.token_hex(8)}"
    try:
        staging.mkdir(mode=0o700)
        resources_root = staging / "resources"
        resources_root.mkdir(mode=0o700)
        manifest_resources = []
        for index, name in enumerate(sorted(sources)):
            ref, source, expected_hash = sources[name]
            if expected_hash is not None:
                _protected_resource(source, expected_hash)
            target = resources_root / f"{index:02d}-{name}.bin"
            observed_hash = _copy_registered_resource(source, target)
            if expected_hash is not None and observed_hash != expected_hash:
                raise ReviewRunnerError(f"registered review resource changed: {name}")
            manifest_resources.append({
                "name": name, "ref": ref,
                "path": f"resources/{target.name}", "content_hash": observed_hash,
            })
        manifest = {
            "schema": RESOURCE_GENERATION_SCHEMA,
            "generation": candidate_commit,
            "source": {
                "commit": candidate_commit, "tree": candidate_tree,
                "canonical_installed": False,
            },
            "resources": manifest_resources,
        }
        _atomic_root_json(staging / "manifest.json", manifest, mode=0o444)
        resources_root.chmod(0o555)
        staging.chmod(0o555)
        if final.exists():
            existing = load_registered_resource_generation(registry_root, candidate_commit)
            desired = {
                item["name"]: {"ref": item["ref"], "hash": item["content_hash"]}
                for item in manifest_resources
            }
            if existing.source != manifest["source"] or existing.bindings != desired:
                raise ReviewRunnerError("registered review candidate generation differs")
            _discard_registration(staging)
        else:
            os.replace(staging, final)
        registered = load_registered_resource_generation(registry_root, candidate_commit)
        return {
            "generation": registered.generation,
            "source": dict(registered.source),
            "resources": {name: dict(binding) for name, binding in registered.bindings.items()},
        }
    except Exception:
        if staging.exists():
            _discard_registration(staging)
        raise


def _service_registered_generation(
    service: Mapping[str, Any], *, credential: str, generation: str,
) -> dict[str, Any]:
    endpoint = str(service["endpoint"]).rstrip("/")
    request = urllib_request.Request(
        endpoint + "/v1/registered-generations/" + generation,
        method="GET", headers={"Authorization": "Bearer " + credential},
    )
    try:
        with urllib_request.urlopen(request, timeout=float(service["timeout_seconds"])) as response:
            raw = response.read(256 * 1024 + 1)
    except (OSError, urllib_error.URLError) as exc:
        raise ReviewRunnerError("registered review resource service is unavailable") from exc
    if len(raw) > 256 * 1024:
        raise ReviewRunnerError("registered review resource service response exceeds its bound")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewRunnerError("registered review resource service response is invalid") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "service_id", "generation", "source", "resources"}
        or value.get("schema") != RESOURCE_GENERATION_SCHEMA
        or value.get("service_id") != service["id"]
        or value.get("generation") != generation
        or not isinstance(value.get("resources"), Mapping)
        or set(value["resources"]) != CARD_RESOURCE_NAMES
    ):
        raise ReviewRunnerError("registered review resource service binding is invalid")
    return value


def _write_generated_resource(path: Path, value: bytes) -> None:
    if len(value) > MAX_RESOURCE_BYTES:
        raise ReviewRunnerError("generated registered review resource exceeds its bound")
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400,
    )
    try:
        _write_all(descriptor, value)
        os.fsync(descriptor)
        os.fchown(descriptor, 0, 0)
        os.fchmod(descriptor, 0o400)
    finally:
        os.close(descriptor)


def prepare_governed_request(
    *,
    repository: Path,
    candidate_commit: str,
    candidate_tree: str,
    plan_commit: str,
    solution_hash: str,
    closure_hash: str,
    profile_path: Path,
    candidate_descriptor_path: Path,
    request_root: Path,
    snapshot_root: Path,
    resource_registry_root: Path,
    broker_grant_root: Path,
    credential_paths: Mapping[str, Path],
    trusted_uid: int = 0,
) -> dict[str, Any]:
    """Prepare one exact root-owned governed request and immutable Git snapshot."""

    if os.geteuid() != 0 or trusted_uid != 0:
        raise ReviewRunnerError("protected governed-review preparation requires root")
    if (
        any(
            _COMMIT.fullmatch(value) is None
            for value in (candidate_commit, candidate_tree, plan_commit)
        )
        or any(
            _SHA256.fullmatch(value) is None
            for value in (solution_hash, closure_hash)
        )
    ):
        raise ReviewRunnerError("protected governed-review identity is invalid")
    protected_identity(request_root, uid=0, mode=0o755)
    protected_identity(snapshot_root, uid=0, mode=0o755)
    protected_identity(resource_registry_root, uid=0, mode=0o755)
    protected_identity(broker_grant_root, uid=0, mode=0o755)
    observed_commit = str(_git(repository, "rev-parse", candidate_commit)).strip()
    observed_tree = str(_git(repository, "rev-parse", f"{candidate_commit}^{{tree}}")).strip()
    if (observed_commit, observed_tree) != (candidate_commit, candidate_tree):
        raise ReviewRunnerError("protected governed-review candidate Git binding differs")
    profile = load_profile(profile_path, trusted_uid=trusted_uid)
    descriptor_value = _protected_json(
        candidate_descriptor_path,
        "coding protected-review candidate evidence descriptor",
        trusted_uid,
    )
    descriptor = PinnedCandidateEvidenceDescriptor(
        descriptor_value, candidate_repository=repository
    )

    snapshot = snapshot_root / candidate_commit
    staging = snapshot_root / f".{candidate_commit}.{secrets.token_hex(8)}"
    archive = snapshot_root / f".{candidate_commit}.{secrets.token_hex(8)}.tar"
    try:
        _available(snapshot_root, MIN_AVAILABLE_BYTES)
        archive_hash = _stream_git_archive(repository, candidate_commit, archive)
        _extract_snapshot(archive, staging)
        expected_snapshot = snapshot_hash(staging)
        if snapshot.exists():
            if snapshot.is_symlink() or not snapshot.is_dir():
                raise ReviewRunnerError(
                    "protected governed-review snapshot destination is unsafe"
                )
            _root_owned_snapshot(snapshot)
            if snapshot_hash(snapshot) == expected_snapshot:
                _discard_root_owned_snapshot(staging)
            else:
                displaced = snapshot_root / (
                    f".{candidate_commit}.replaced.{secrets.token_hex(8)}"
                )
                os.replace(snapshot, displaced)
                try:
                    os.replace(staging, snapshot)
                except Exception:
                    os.replace(displaced, snapshot)
                    raise
                _discard_root_owned_snapshot(displaced)
        else:
            os.replace(staging, snapshot)
    finally:
        if staging.exists():
            _discard_root_owned_snapshot(staging)
        archive.unlink(missing_ok=True)
    _root_owned_snapshot(snapshot)
    existing = snapshot_hash(snapshot)
    if existing != expected_snapshot:
        raise ReviewRunnerError("protected governed-review snapshot differs from Git")

    evidence_sink = dict(profile["evidence_sink"])
    service = dict(profile["resource_service"])
    catalog = dict(profile["resource_service_catalog"])
    registered_service = dict(profile["registered_resource_service"])
    registered_catalog = dict(profile["registered_resource_service_catalog"])
    if catalog.get("plan_commit") != plan_commit:
        raise ReviewRunnerError(
            "protected governed-review resource catalog Plan binding differs"
        )
    if registered_catalog.get("plan_commit") != plan_commit:
        raise ReviewRunnerError(
            "protected governed-review registered service Plan binding differs"
        )
    if set(credential_paths) != {"context", "evidence", "resource", "broker"}:
        raise ReviewRunnerError("protected governed-review credential paths are incomplete")
    generations = profile["credential_generations"]
    credentials = {
        name: load_service_credential(
            credential_paths[name], purpose=name, generation=generations[name],
            trusted_uid=trusted_uid,
        )
        for name in sorted(credential_paths)
    }
    environment = dict(profile["environment"])
    provider_identity = json.loads(json.dumps(profile["provider_identity"]))
    provider = str(provider_identity.get("provider", ""))
    provider_argv = list(provider_identity.get("argv_template", []))
    generated_root = Path(tempfile.mkdtemp(
        prefix=f".{candidate_commit}.resources.", dir=resource_registry_root,
    ))
    try:
        codegraph = build_codegraph_snapshot(repository, candidate_commit)
        if (codegraph.get("commit"), codegraph.get("tree")) != (
            candidate_commit, candidate_tree,
        ):
            raise ReviewRunnerError("candidate CodeGraph Git binding differs")
        codegraph_path = generated_root / "codegraph.json"
        source_tree_path = generated_root / "source-tree.bin"
        candidate_evidence_path = generated_root / "candidate-evidence.json"
        receipt_sink_path = generated_root / "receipt-sink.json"
        _write_generated_resource(codegraph_path, _canonical(codegraph))
        _write_snapshot_preimage(snapshot, source_tree_path)
        candidate_evidence_content = {
            "schema": CANDIDATE_EVIDENCE_CARD_BINDING_SCHEMA,
            "descriptor": descriptor._value,
        }
        _write_generated_resource(
            candidate_evidence_path, _canonical(candidate_evidence_content),
        )
        sink_unsigned = dict(evidence_sink)
        sink_unsigned.pop("descriptor_hash", None)
        _write_generated_resource(receipt_sink_path, _canonical(sink_unsigned))
        sources: dict[str, tuple[str, Path, str | None]] = {
            name: (
                str(binding["ref"]), Path(str(binding["path"])), str(binding["hash"]),
            )
            for name, binding in profile["registered_resource_inputs"].items()
        }
        sources.update({
            "codegraph_snapshot": (
                f"mcp:tgw-context/review-candidate-codegraph/{candidate_commit}",
                codegraph_path, None,
            ),
            "source_tree": (
                f"git:tree:{candidate_tree}",
                source_tree_path, None,
            ),
            "candidate_evidence": (
                descriptor.card_binding()["ref"], candidate_evidence_path, None,
            ),
            "receipt_sink": (
                evidence_sink["sink_ref"], receipt_sink_path, None,
            ),
        })
        locally_registered = _register_resource_generation(
            resource_registry_root,
            candidate_commit=candidate_commit, candidate_tree=candidate_tree,
            sources=sources,
        )
    finally:
        _discard_registration(generated_root)
    service_registered = _service_registered_generation(
        registered_service, credential=credentials["resource"],
        generation=candidate_commit,
    )
    if service_registered != {
        "schema": RESOURCE_GENERATION_SCHEMA,
        "service_id": registered_service["id"],
        **locally_registered,
    }:
        raise ReviewRunnerError("registered review resource service differs")
    bindings = {
        name: dict(binding)
        for name, binding in service_registered["resources"].items()
    }
    if (
        bindings["source_tree"]["hash"] != existing
        or bindings["candidate_evidence"] != descriptor.card_binding()
        or bindings["receipt_sink"]
        != {"ref": evidence_sink["sink_ref"], "hash": evidence_sink["descriptor_hash"]}
        or bindings["execution_environment"]["hash"]
        != provider_identity["artifacts"]["execution_environment"]["content_sha256"]
    ):
        raise ReviewRunnerError("registered review candidate resource binding differs")
    provider_identity["command_policy"]["context_bindings"] = {
        name: bindings[name]
        for name in (
            "plan_input",
            "plan_commit",
            "plan_graph",
            "codegraph_snapshot",
            "source_tree",
            "execution_environment",
        )
    }
    service_binding = {
        "id": service["id"],
        "client_id": service["client_id"],
        "descriptor_hash": provider_identity["context_bundle_service"][
            "resource_service_descriptor_hash"
        ],
        "catalog_ref": provider_identity["context_bundle_service"][
            "resource_service_catalog_ref"
        ],
        "catalog_hash": provider_identity["context_bundle_service"][
            "resource_service_catalog_hash"
        ],
    }
    now = datetime.now(timezone.utc)
    # The broker permits a maximum 900-second window including the one-second
    # not-before skew used below.
    expires = now + timedelta(minutes=14, seconds=59)
    from promptcraft.handoff import ExecutionCard, craft_handoff

    card = ExecutionCard.create(
        {
            "card_id": f"candidate-review-{candidate_commit[:12]}",
            "solution_id": solution_hash,
            "role": "independent-review",
            "selected_provider": provider,
            "plan_commit": plan_commit,
            "bindings": bindings,
            "authority": ["read-only semantic and security review of the bound snapshot"],
            "exclusions": [
                "source mutation",
                "deployment",
                "installation",
                "authority broadening",
            ],
            "acceptance": [
                "validated semantic and security verdict bound to the candidate"
            ],
            "receiver_profile": dict(profile["receiver_profile"]),
            "lease": {
                "id": f"review:{candidate_commit}:{secrets.token_hex(6)}",
                "expires_at": expires.isoformat(),
                "stop_policy": "hold",
            },
            "resource_service": service_binding,
        }
    )
    resource_receipt = card_resource_receipt(card.value)
    handoff = craft_handoff(
        {
            "card": card.value,
            "resource_receipt": resource_receipt,
            "resource_service": service,
        },
        receiver_identity=f"{provider}:tgw-review",
    )
    manifest = {
        "schema": "tgw-integrated-candidate-manifest/v1",
        "source": {
            "commit": candidate_commit,
            "tree": candidate_tree,
            "archive_sha256": archive_hash,
        },
        "plan": {
            "commit": plan_commit,
            "solution_hash": solution_hash,
            "closure_hash": closure_hash,
        },
        "candidate_closed": True,
        "installed": False,
    }
    packet = build_executable_review_packet(
        manifest,
        snapshot_ref=snapshot.resolve().as_uri(),
        snapshot_hash=existing,
        selected_provider=provider,
        receiver_profile=profile["receiver_profile"],
        runner_argv=provider_argv,
    )
    challenge = secrets.token_hex(32)
    service_identity = provider_identity["context_bundle_service"]
    sandbox_identity = provider_identity["sandbox_identity"]
    issued = now - timedelta(seconds=1)
    grant_request = {
        "schema": "tgw-context-review-broker-request/v2",
        "client_id": service_identity["client_id"],
        "challenge": challenge,
        "skill_contract_hash": profile["skill_contract_hash"],
        "card_hash": card.hash,
        "role": "independent-review",
        "execution_identity": (
            f"governed-review:{challenge}:uid={sandbox_identity['uid']}:"
            f"gid={sandbox_identity['gid']}"
        ),
        "handoff_hash": handoff["handoff_hash"],
        "resource_receipt_hash": resource_receipt["receipt_hash"],
        "resource_service_catalog_ref": service_identity[
            "resource_service_catalog_ref"
        ],
        "resource_service_catalog_hash": service_identity[
            "resource_service_catalog_hash"
        ],
        "resources": {name: bindings[name] for name in sorted(bindings)},
        "issued_at": issued.isoformat(),
        "not_before": issued.isoformat(),
        "expires_at": expires.isoformat(),
    }
    context_grant = {
        "schema": "tgw-governed-review-context-grant/v1",
        "request": grant_request,
        "request_hash": _hash(grant_request),
    }
    grant_store = FileReviewContextGrantStore(
        broker_grant_root,
        master_credential=credentials["broker"],
        client_id=str(service_identity["client_id"]),
        catalog_ref=str(service_identity["resource_service_catalog_ref"]),
        catalog_hash=str(service_identity["resource_service_catalog_hash"]),
    )
    issued_grant = grant_store.issue(grant_request)
    if issued_grant["request_hash"] != context_grant["request_hash"]:
        raise ReviewRunnerError("review context broker grant issuance differs")
    request = {
        "schema": "tgw-governed-review-request/v1",
        "handoff": handoff,
        "snapshot": str(snapshot),
        "source_commit": candidate_commit,
        "source_tree": candidate_tree,
        "plan_commit": plan_commit,
        "provider": provider,
        "provider_identity": provider_identity,
        "provider_argv": provider_argv,
        "environment": environment,
        "trusted_uid": 0,
        "trusted_gid": 0,
        "timeout_seconds": profile["timeout_seconds"],
        "output_limit": profile["output_limit"],
        "evidence_sink": evidence_sink,
        "review_packet": packet,
        "resource_service_catalog": catalog,
        "context_grant": context_grant,
        "environment_preflight_receipt": dict(
            profile["environment_preflight_receipt"]
        ),
    }
    request_path = request_root / f"{candidate_commit}.request.json"
    _atomic_root_json(request_path, request, mode=0o444)
    return {
        "request_path": str(request_path),
        "request_sha256": "sha256:" + hashlib.sha256(_canonical(request)).hexdigest(),
        "snapshot": str(snapshot),
        "snapshot_hash": existing,
        "expires_at": expires.isoformat(),
        "card_hash": card.hash,
        "packet_hash": packet["packet_hash"],
    }


def protected_identity(path: Path, *, uid: int = 0, mode: int | None = None) -> None:
    """Require one direct root-owned, non-writable protected surface."""

    observed = path.stat(follow_symlinks=False)
    if (
        path.is_symlink()
        or observed.st_uid != uid
        or observed.st_gid != 0
        or observed.st_mode & 0o022
        or (mode is not None and stat.S_IMODE(observed.st_mode) != mode)
    ):
        raise ReviewRunnerError("protected governed-review surface is writable or substituted")
