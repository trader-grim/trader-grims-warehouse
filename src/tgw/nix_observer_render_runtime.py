"""Closed controller composition for one observer-render evaluation request."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from tgw.nix_observer_render_evaluation import validate_request, validate_result
from tgw.nixos_reviewed_evaluation import ImmutableFailureReceiptStore

SOURCE_REF = "artifact:sha256:0ca98c4d32a2ffb99af355c768d48d7c1024efab76d78edf6644ed821aff68ad"
SOURCE_PATH = Path("/opt/TGW/tgw-lib/actors/codex/artifacts/sha256/0ca98c4d32a2ffb99af355c768d48d7c1024efab76d78edf6644ed821aff68ad.tar")
KNOWN_HOSTS_PATH = Path("/opt/TGW/tgw-lib/actors/codex/artifacts/sha256/2efd6fc4243b15b6d0b16a8da723911614198620cabf31bc822cf12520715cdf.known_hosts")
FAILURE_ROOT = Path("/opt/TGW/tgw-lib/actors/codex/nix-observer-render-failures")


class RenderRuntimeError(ValueError):
    def __init__(self, message: str, *, receipt: Mapping[str, object] | None = None):
        super().__init__(message)
        self.receipt = receipt


FAILURE_SCHEMA = "tgw-nix-observer-render-evaluation-failure/v1"
FAILURE_STAGES = {"request", "archive", "source", "input-closure", "nix-eval", "nix-build", "output", "systemd-verify", "cleanup", "internal"}
FAILURE_CODES = {"VALIDATION_REFUSED", "IDENTITY_MISMATCH", "BOUND_EXCEEDED", "SUBPROCESS_FAILED", "CLEANUP_FAILED", "INTERNAL_ERROR"}
FAILURE_EFFECTS = {"build_attempted", "activation", "deployment", "profile_write", "home_db_write", "live_flake_write", "network"}
STAGE_CODES = {
    "request": {"VALIDATION_REFUSED", "IDENTITY_MISMATCH", "BOUND_EXCEEDED"},
    "archive": {"VALIDATION_REFUSED", "IDENTITY_MISMATCH", "BOUND_EXCEEDED"},
    "source": {"VALIDATION_REFUSED", "IDENTITY_MISMATCH", "SUBPROCESS_FAILED", "BOUND_EXCEEDED"},
    "input-closure": {"VALIDATION_REFUSED", "IDENTITY_MISMATCH"},
    "nix-eval": {"SUBPROCESS_FAILED", "BOUND_EXCEEDED"},
    "nix-build": {"SUBPROCESS_FAILED", "BOUND_EXCEEDED"},
    "output": {"VALIDATION_REFUSED", "IDENTITY_MISMATCH", "BOUND_EXCEEDED"},
    "systemd-verify": {"SUBPROCESS_FAILED", "BOUND_EXCEEDED"},
    "internal": {"INTERNAL_ERROR"},
}


def validate_failure(value: Mapping[str, object], *, request: Mapping[str, object]) -> dict[str, object]:
    fields = {
        "schema",
        "request_sha256",
        "source_commit",
        "source_tree",
        "archive_sha256",
        "provider_sha256",
        "host_identity_receipt_sha256",
        "outcome",
        "stage",
        "diagnostic_code",
        "cleanup",
        "effects",
        "return_code",
        "original_stage",
        "original_diagnostic_code",
        "original_return_code",
        "stdout_bytes",
        "stdout_sha256",
        "stderr_bytes",
        "stderr_sha256",
        "receipt_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise RenderRuntimeError("render failure schema is not closed")
    result = dict(value)
    claimed = result.pop("receipt_sha256")
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > 8192 or claimed != "sha256:" + hashlib.sha256(encoded).hexdigest():
        raise RenderRuntimeError("render failure self-hash or size mismatch")
    for field in ("request_sha256", "source_commit", "source_tree", "archive_sha256", "provider_sha256", "host_identity_receipt_sha256"):
        if result[field] != request[field]:
            raise RenderRuntimeError("render failure identity binding mismatch")
    if result["schema"] != FAILURE_SCHEMA or result["stage"] not in FAILURE_STAGES or result["diagnostic_code"] not in FAILURE_CODES:
        raise RenderRuntimeError("render failure classification invalid")
    if result["outcome"] == "FAILED":
        if result["cleanup"] != "removed" or result["stage"] == "cleanup":
            raise RenderRuntimeError("FAILED requires verified cleanup")
        if (result["original_stage"], result["original_diagnostic_code"], result["original_return_code"]) != (result["stage"], result["diagnostic_code"], result["return_code"]):
            raise RenderRuntimeError("FAILED original failure binding mismatch")
    elif result["outcome"] == "AMBIGUOUS":
        if result["cleanup"] not in {"failed", "unknown"} or result["stage"] != "cleanup" or result["diagnostic_code"] != "CLEANUP_FAILED":
            raise RenderRuntimeError("AMBIGUOUS requires cleanup uncertainty")
        if result["return_code"] is not None:
            raise RenderRuntimeError("cleanup ambiguity cannot claim subprocess return code")
        if result["original_stage"] == "complete":
            if result["original_diagnostic_code"] != "NONE" or result["original_return_code"] is not None:
                raise RenderRuntimeError("completed pre-cleanup state invalid")
        elif result["original_stage"] not in STAGE_CODES:
            raise RenderRuntimeError("AMBIGUOUS original failure is invalid")
    else:
        raise RenderRuntimeError("render failure outcome invalid")
    effects = result["effects"]
    if (
        not isinstance(effects, dict)
        or set(effects) != FAILURE_EFFECTS
        or not isinstance(effects["build_attempted"], bool)
        or any(effects[key] is not False for key in FAILURE_EFFECTS - {"build_attempted"})
    ):
        raise RenderRuntimeError("render failure effects invalid")
    for code in (result["return_code"], result["original_return_code"]):
        if code is not None and (isinstance(code, bool) or not isinstance(code, int) or not -255 <= code <= 255):
            raise RenderRuntimeError("render failure return code invalid")
    original_stage, original_code, original_rc = result["original_stage"], result["original_diagnostic_code"], result["original_return_code"]
    if original_stage != "complete":
        if original_code not in STAGE_CODES[original_stage]:
            raise RenderRuntimeError("failure stage/code tuple invalid")
        needs_rc = original_code == "SUBPROCESS_FAILED"
        if needs_rc != (original_rc is not None and original_rc != 0):
            raise RenderRuntimeError("failure return-code tuple invalid")
    expected_build = original_stage in {"nix-build", "output", "systemd-verify", "complete"}
    if effects["build_attempted"] is not expected_build:
        raise RenderRuntimeError("failure build-attempt tuple invalid")
    for prefix in ("stdout", "stderr"):
        if isinstance(result[prefix + "_bytes"], bool) or not isinstance(result[prefix + "_bytes"], int) or not 0 <= result[prefix + "_bytes"] <= request["max_output_bytes"]:
            raise RenderRuntimeError("render failure diagnostic bound invalid")
        if not isinstance(result[prefix + "_sha256"], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", result[prefix + "_sha256"]):
            raise RenderRuntimeError("render failure diagnostic hash invalid")
        if result[prefix + "_bytes"] == 0 and result[prefix + "_sha256"] != "sha256:" + hashlib.sha256(b"").hexdigest():
            raise RenderRuntimeError("empty diagnostic hash mismatch")
    return dict(value)


class RenderTransport(Protocol):
    def __call__(self, *, request: Mapping[str, object], archive_fd: int, known_hosts_fd: int) -> Mapping[str, object]: ...


def _held(path: Path, *, digest: str, mode: int, size: int | None = None) -> tuple[int, dict[str, object]]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    metadata = os.fstat(fd)
    try:
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid not in {0, os.geteuid()} or stat.S_IMODE(metadata.st_mode) != mode:
            raise RenderRuntimeError("render runtime artifact trust mismatch")
        content = bytearray()
        while block := os.read(fd, 1024 * 1024):
            content.extend(block)
        observed = "sha256:" + hashlib.sha256(content).hexdigest()
        if observed != digest or (size is not None and metadata.st_size != size):
            raise RenderRuntimeError("render runtime artifact identity mismatch")
        os.lseek(fd, 0, os.SEEK_SET)
        return fd, {"path": str(path), "sha256": observed, "size": metadata.st_size, "mode": f"{mode:04o}", "owner_uid": metadata.st_uid}
    except BaseException:
        os.close(fd)
        raise


@dataclass
class ClosedRenderProvider:
    transport: RenderTransport
    failure_store: ImmutableFailureReceiptStore
    source_path: Path = SOURCE_PATH
    known_hosts_path: Path = KNOWN_HOSTS_PATH

    def execute(self, request: Mapping[str, object]) -> Mapping[str, object]:
        validated = validate_request(request)
        if validated["artifact_ref"] != SOURCE_REF:
            raise RenderRuntimeError("render runtime source ref is not admitted")
        archive_fd = hosts_fd = -1
        try:
            archive_fd, _ = _held(self.source_path, digest=validated["archive_sha256"], mode=0o444, size=9_041_920)
            hosts_fd, _ = _held(
                self.known_hosts_path,
                digest="sha256:2efd6fc4243b15b6d0b16a8da723911614198620cabf31bc822cf12520715cdf",
                mode=0o444,
                size=95,
            )
            untrusted = self.transport(request=validated, archive_fd=archive_fd, known_hosts_fd=hosts_fd)
            if untrusted.get("schema") == "tgw-nix-observer-render-evaluation-receipt/v1":
                return validate_result(untrusted, request=validated)
            if untrusted.get("schema") != FAILURE_SCHEMA:
                raise RenderRuntimeError("render transport returned an unknown terminal schema")
            failure = validate_failure(untrusted, request=validated)
            try:
                reference = self.failure_store.persist(failure)
            except Exception as exc:
                raise RenderRuntimeError("validated failure persistence is ambiguous", receipt=failure) from exc
            raise RenderRuntimeError("render evaluation terminated " + str(failure["outcome"]) + " at " + str(reference["artifact_ref"]))
        finally:
            for fd in (archive_fd, hosts_fd):
                if fd >= 0:
                    os.close(fd)


def compose_render_provider(*, transport: RenderTransport, failure_root: Path = FAILURE_ROOT) -> tuple[ClosedRenderProvider, dict[str, object]]:
    store = ImmutableFailureReceiptStore(failure_root)
    provider = ClosedRenderProvider(transport=transport, failure_store=store)
    return provider, {
        "schema": "tgw-nix-observer-render-runtime-composition/v1",
        "source_ref": SOURCE_REF,
        "source_path": str(SOURCE_PATH),
        "known_hosts_path": str(KNOWN_HOSTS_PATH),
        "failure_store": store.readiness,
        "ambient_fallback": False,
    }


def execute_request(path: Path, *, transport: RenderTransport, failure_root: Path = FAILURE_ROOT) -> Mapping[str, object]:
    request = json.loads(path.read_text())
    provider, _ = compose_render_provider(transport=transport, failure_root=failure_root)
    return provider.execute(request)
