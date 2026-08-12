"""Closed request/result contract for non-deploying observer artifact rendering."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = "tgw-nix-observer-render-evaluation-request/v1"
RESULT_SCHEMA = "tgw-nix-observer-render-evaluation-receipt/v1"
TARGET = "nix-input-observer-rendered-artifacts"
STORE_ROOT = Path("/nix/store")
OUTPUTS = (
    "etc/nix-input-observer-launcher.conf",
    "etc/nix-input-observer-transport.json",
    "launcher",
    "observer.py",
    "tools/git",
    "tools/ip",
    "tools/nix",
    "tools/nix-store",
    "tools/python",
    "units/tgw-nix-input-observer.slice",
    "units/tgw-nix-input-observer.socket",
    "units/tgw-nix-input-observer@.service",
    "verifier-metadata.json",
)
DIGEST_FIELDS = {
    "archive_sha256",
    "flake_lock_sha256",
    "flake_sha256",
    "module_sha256",
    "launcher_source_sha256",
    "observer_source_sha256",
    "provider_sha256",
    "host_identity_receipt_sha256",
    "systemd_analyze_sha256",
    "input_closure_manifest_sha256",
}


class RenderEvaluationError(ValueError):
    pass


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def validate_request(value: Mapping[str, object]) -> dict[str, object]:
    fields = {
        "schema",
        "plan_commit",
        "source_commit",
        "source_tree",
        "artifact_ref",
        *DIGEST_FIELDS,
        "target",
        "system",
        "network_policy",
        "allow_ifd",
        "activate",
        "profile_write",
        "home_db_write",
        "expected_outputs",
        "expected_metadata_status",
        "input_closure_manifest",
        "input_closure_path_count",
        "systemd_analyze_version",
        "max_duration_seconds",
        "max_output_bytes",
        "request_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise RenderEvaluationError("render request schema is not closed")
    request = dict(value)
    claimed = request.pop("request_sha256")
    if claimed != "sha256:" + hashlib.sha256(canonical(request)).hexdigest():
        raise RenderEvaluationError("render request self-hash mismatch")
    if request["schema"] != SCHEMA or request["target"] != TARGET or request["system"] != "x86_64-linux":
        raise RenderEvaluationError("render target identity mismatch")
    if request["network_policy"] != "offline-no-substituters" or request["allow_ifd"] is not False:
        raise RenderEvaluationError("render purity boundary mismatch")
    if any(request[key] is not False for key in ("activate", "profile_write", "home_db_write")):
        raise RenderEvaluationError("render request contains a forbidden effect")
    if request["expected_outputs"] != list(OUTPUTS) or request["expected_metadata_status"] != "NON_DEPLOYABLE_RENDER_FIXTURE":
        raise RenderEvaluationError("render output contract mismatch")
    if not all(isinstance(request[key], str) and re.fullmatch(r"sha256:[0-9a-f]{64}", request[key]) for key in DIGEST_FIELDS):
        raise RenderEvaluationError("render digest binding invalid")
    if not all(isinstance(request[key], str) and re.fullmatch(r"[0-9a-f]{40}", request[key]) for key in ("plan_commit", "source_commit", "source_tree")):
        raise RenderEvaluationError("render Git identity invalid")
    if request["artifact_ref"] != "artifact:" + request["archive_sha256"]:
        raise RenderEvaluationError("render artifact reference mismatch")
    manifest = request["input_closure_manifest"]
    if not isinstance(manifest, list) or len(manifest) != 1 or request["input_closure_path_count"] != 1:
        raise RenderEvaluationError("render input closure is not exact")
    entry = manifest[0]
    if (
        not isinstance(entry, dict)
        or set(entry) != {"node", "rev", "lock_nar_hash", "store_path", "nar_sha256"}
        or entry["node"] != "nixpkgs"
        or entry["rev"] != "ac62194c3917d5f474c1a844b6fd6da2db95077d"
        or entry["lock_nar_hash"] != "sha256-16KkgfdYqjaeRGBaYsNrhPRRENs0qzkQVUooNHtoy2w="
        or not re.fullmatch(r"/nix/store/[0-9a-df-np-sv-z]{32}-[A-Za-z0-9+._?=-]+", entry["store_path"])
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", entry["nar_sha256"])
        or request["input_closure_manifest_sha256"] != "sha256:" + hashlib.sha256(canonical(manifest)).hexdigest()
    ):
        raise RenderEvaluationError("render input closure binding mismatch")
    if request["systemd_analyze_version"] != "systemd 257 (257.10)":
        raise RenderEvaluationError("render verifier version mismatch")
    if not isinstance(request["max_duration_seconds"], int) or not 1 <= request["max_duration_seconds"] <= 900:
        raise RenderEvaluationError("render timeout invalid")
    if not isinstance(request["max_output_bytes"], int) or not 1 <= request["max_output_bytes"] <= 16 * 1024 * 1024:
        raise RenderEvaluationError("render output bound invalid")
    return dict(value)


def validate_result(value: Mapping[str, object], *, request: Mapping[str, object], now: datetime | None = None) -> dict[str, object]:
    fields = {
        "schema",
        "request_sha256",
        "outcome",
        "metadata_status",
        "files",
        "output_root",
        "evaluated_drv",
        "drv_output",
        "output_manifest_sha256",
        "systemd_verify",
        "cleanup",
        "effects",
        "receipt_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise RenderEvaluationError("render receipt schema is not closed")
    result = dict(value)
    claimed = result.pop("receipt_sha256")
    if claimed != "sha256:" + hashlib.sha256(canonical(result)).hexdigest():
        raise RenderEvaluationError("render receipt self-hash mismatch")
    if result["schema"] != RESULT_SCHEMA or result["request_sha256"] != request["request_sha256"] or result["outcome"] != "VERIFIED":
        raise RenderEvaluationError("render receipt binding mismatch")
    if result["metadata_status"] != "NON_DEPLOYABLE_RENDER_FIXTURE" or result["cleanup"] != "removed":
        raise RenderEvaluationError("render receipt status mismatch")
    if result["effects"] != {"build": True, "activation": False, "deployment": False, "profile_write": False, "home_db_write": False, "live_flake_write": False, "network": False}:
        raise RenderEvaluationError("render receipt forbidden-effect evidence mismatch")
    verify = result["systemd_verify"]
    if (
        not isinstance(verify, dict)
        or set(verify)
        != {"executable_sha256", "version", "argv", "exit_code", "stdout_bytes", "stdout_sha256", "stderr_bytes", "stderr_sha256", "units_sha256", "observed_at", "host_identity_receipt_sha256"}
        or verify["executable_sha256"] != request["systemd_analyze_sha256"]
        or verify["version"] != request["systemd_analyze_version"]
        or verify["argv"] != ["systemd-analyze", "verify", *OUTPUTS[9:12]]
        or verify["exit_code"] != 0
        or verify["host_identity_receipt_sha256"] != request["host_identity_receipt_sha256"]
        or not isinstance(verify["observed_at"], str)
        or any(not isinstance(verify[key], int) or not 0 <= verify[key] <= request["max_output_bytes"] for key in ("stdout_bytes", "stderr_bytes"))
        or any(not re.fullmatch(r"sha256:[0-9a-f]{64}", verify[key]) for key in ("stdout_sha256", "stderr_sha256", "units_sha256"))
    ):
        raise RenderEvaluationError("render systemd verification mismatch")
    files = result["files"]
    if (
        not isinstance(files, list)
        or [item.get("path") for item in files] != list(OUTPUTS)
        or any(set(item) != {"path", "sha256", "size"} or not re.fullmatch(r"sha256:[0-9a-f]{64}", item["sha256"]) or not isinstance(item["size"], int) or item["size"] < 0 for item in files)
    ):
        raise RenderEvaluationError("render output manifest mismatch")
    output_path = Path(result["output_root"]) if isinstance(result["output_root"], str) else Path()
    if output_path.parent != STORE_ROOT or not re.fullmatch(r"[0-9a-df-np-sv-z]{32}-[A-Za-z0-9+._?=-]+", output_path.name):
        raise RenderEvaluationError("render output root invalid")
    if not isinstance(result["evaluated_drv"], str) or not re.fullmatch(r"/nix/store/[0-9a-df-np-sv-z]{32}-[A-Za-z0-9+._?=-]+\.drv", result["evaluated_drv"]):
        raise RenderEvaluationError("render derivation identity invalid")
    if result["drv_output"] != {"drv": result["evaluated_drv"], "output": result["output_root"]}:
        raise RenderEvaluationError("render derivation output binding mismatch")
    if result["output_manifest_sha256"] != "sha256:" + hashlib.sha256(canonical(files)).hexdigest():
        raise RenderEvaluationError("render output manifest hash mismatch")
    units = [item for item in files if item["path"] in OUTPUTS[9:12]]
    if verify["units_sha256"] != "sha256:" + hashlib.sha256(canonical(units)).hexdigest():
        raise RenderEvaluationError("render unit aggregate mismatch")
    try:
        observed = datetime.strptime(verify["observed_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise RenderEvaluationError("render observation timestamp invalid") from exc
    current = now or datetime.now(timezone.utc)
    if observed > current + timedelta(minutes=1) or current - observed > timedelta(hours=1):
        raise RenderEvaluationError("render observation timestamp stale")
    return dict(value)


def produce_result(
    *,
    request: Mapping[str, object],
    output_root: Path,
    evaluated_drv: str,
    nix: Path,
    nix_sha256: str,
    systemd_analyze: Path,
    now: datetime,
    scratch_root: Path,
    run: object = subprocess.run,
) -> dict[str, object]:
    """Derive one receipt from held output/tool inodes and actual subprocesses."""
    validate_request(request)
    if output_root.parent != STORE_ROOT or not re.fullmatch(r"[0-9a-df-np-sv-z]{32}-[A-Za-z0-9+._?=-]+", output_root.name):
        raise RenderEvaluationError("render output root is not canonical")
    identity = str(output_root)

    def held(path: Path, expected: str | None = None) -> tuple[int, str, int]:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        stat = os.fstat(fd)
        if not os.path.isfile(f"/proc/self/fd/{fd}"):
            os.close(fd)
            raise RenderEvaluationError("render evidence path is not regular")
        chunks = []
        while chunk := os.read(fd, 1024 * 1024):
            chunks.append(chunk)
        raw = b"".join(chunks)
        os.lseek(fd, 0, os.SEEK_SET)
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        if expected is not None and digest != expected:
            os.close(fd)
            raise RenderEvaluationError("render tool identity mismatch")
        return fd, digest, stat.st_size

    nix_fd, _, _ = held(nix, nix_sha256)
    verify_fd, verify_digest, _ = held(systemd_analyze, request["systemd_analyze_sha256"])
    file_fds: list[int] = []
    store_fd = os.open(STORE_ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    output_fd = os.open(output_root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=store_fd)
    try:
        files = []
        raw_files = {}
        for name in OUTPUTS:
            parts = name.split("/")
            parent_fd = output_fd
            owned_parent = None
            if len(parts) == 2:
                owned_parent = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=output_fd)
                parent_fd = owned_parent
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
            if owned_parent is not None:
                os.close(owned_parent)
            stat = os.fstat(fd)
            chunks = []
            while chunk := os.read(fd, 1024 * 1024):
                chunks.append(chunk)
            raw = b"".join(chunks)
            os.lseek(fd, 0, os.SEEK_SET)
            if len(raw) != stat.st_size:
                raise RenderEvaluationError("render file changed while held")
            digest = "sha256:" + hashlib.sha256(raw).hexdigest()
            size = stat.st_size
            file_fds.append(fd)
            files.append({"path": name, "sha256": digest, "size": size})
            raw_files[name] = raw
        nix_result = run(
            [f"/proc/self/fd/{nix_fd}", "derivation", "show", evaluated_drv],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            close_fds=True,
            pass_fds=(nix_fd,),
        )
        if nix_result.returncode or max(len(nix_result.stdout), len(nix_result.stderr)) > request["max_output_bytes"]:
            raise RenderEvaluationError("render derivation observation failed")
        derivation = json.loads(nix_result.stdout)
        if set(derivation) != {evaluated_drv} or set(derivation[evaluated_drv]) < {"outputs"}:
            raise RenderEvaluationError("render derivation schema mismatch")
        outputs = derivation.get(evaluated_drv, {}).get("outputs", {})
        if outputs != {"out": {"path": identity}}:
            raise RenderEvaluationError("render derivation output observation mismatch")
        metadata = json.loads(raw_files["verifier-metadata.json"])
        if (
            metadata.get("schema") != "tgw-nix-input-observer-render/v1"
            or metadata.get("descriptor_status") != "NON_DEPLOYABLE_RENDER_FIXTURE"
            or metadata.get("activation") is not False
            or metadata.get("units") != list(OUTPUTS[9:12])
        ):
            raise RenderEvaluationError("render held metadata contract mismatch")
        versioned = run([f"/proc/self/fd/{verify_fd}", "--version"], capture_output=True, check=False, timeout=30, close_fds=True, pass_fds=(verify_fd,))
        version_out = versioned.stdout if isinstance(versioned.stdout, bytes) else versioned.stdout.encode()
        if versioned.returncode or not version_out.startswith(request["systemd_analyze_version"].encode() + b"\n"):
            raise RenderEvaluationError("render held verifier version mismatch")
        unit_fds = file_fds[9:12]
        argv = [f"/proc/self/fd/{verify_fd}", "verify", *[f"/proc/self/fd/{fd}" for fd in unit_fds]]
        verified = run(argv, capture_output=True, check=False, timeout=60, close_fds=True, pass_fds=(verify_fd, *unit_fds))
        stdout = verified.stdout if isinstance(verified.stdout, bytes) else verified.stdout.encode()
        stderr = verified.stderr if isinstance(verified.stderr, bytes) else verified.stderr.encode()
        if verified.returncode or max(len(stdout), len(stderr)) > request["max_output_bytes"]:
            raise RenderEvaluationError("render systemd verification failed")
        unit_manifest = files[9:12]
        shutil.rmtree(scratch_root)
        if scratch_root.exists():
            raise RenderEvaluationError("render scratch cleanup ambiguous")
        result = {
            "schema": RESULT_SCHEMA,
            "request_sha256": request["request_sha256"],
            "outcome": "VERIFIED",
            "metadata_status": "NON_DEPLOYABLE_RENDER_FIXTURE",
            "files": files,
            "output_root": identity,
            "evaluated_drv": evaluated_drv,
            "drv_output": {"drv": evaluated_drv, "output": identity},
            "output_manifest_sha256": "sha256:" + hashlib.sha256(canonical(files)).hexdigest(),
            "systemd_verify": {
                "executable_sha256": verify_digest,
                "version": request["systemd_analyze_version"],
                "argv": ["systemd-analyze", "verify", *OUTPUTS[9:12]],
                "exit_code": verified.returncode,
                "stdout_bytes": len(stdout),
                "stdout_sha256": "sha256:" + hashlib.sha256(stdout).hexdigest(),
                "stderr_bytes": len(stderr),
                "stderr_sha256": "sha256:" + hashlib.sha256(stderr).hexdigest(),
                "units_sha256": "sha256:" + hashlib.sha256(canonical(unit_manifest)).hexdigest(),
                "observed_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "host_identity_receipt_sha256": request["host_identity_receipt_sha256"],
            },
            "cleanup": "removed",
            "effects": {"build": True, "activation": False, "deployment": False, "profile_write": False, "home_db_write": False, "live_flake_write": False, "network": False},
        }
        result["receipt_sha256"] = "sha256:" + hashlib.sha256(canonical(result)).hexdigest()
        return validate_result(result, request=request, now=now)
    finally:
        for fd in [nix_fd, verify_fd, output_fd, store_fd, *file_fds]:
            os.close(fd)
