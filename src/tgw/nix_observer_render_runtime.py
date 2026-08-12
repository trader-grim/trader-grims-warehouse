"""Closed controller composition for one observer-render evaluation request."""

from __future__ import annotations

import hashlib
import json
import os
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
    pass


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
            if untrusted.get("schema") != "tgw-nix-observer-render-evaluation-failure/v1":
                raise RenderRuntimeError("render transport returned an unknown terminal schema")
            failure = dict(untrusted)
            if failure.get("request_sha256") != validated["request_sha256"] or failure.get("outcome") not in {"FAILED", "AMBIGUOUS"}:
                raise RenderRuntimeError("render failure receipt binding mismatch")
            unsigned = dict(failure)
            claimed = unsigned.pop("receipt_sha256", None)
            encoded = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
            if claimed != "sha256:" + hashlib.sha256(encoded).hexdigest():
                raise RenderRuntimeError("render failure receipt self-hash mismatch")
            reference = self.failure_store.persist(failure)
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
