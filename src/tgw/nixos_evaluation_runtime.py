"""Exact runtime composition for the approved reviewed Nix evaluation artifacts."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from tgw.nixos_reviewed_evaluation import ImmutableFailureReceiptStore, SshReviewedEvaluationProvider

SOURCE_REF = "artifact:sha256:a52a7f06885379d6e835b05f3918d19601e1b7fde063afbc5d2bad0e61f022ed"
SOURCE_PATH = Path("/opt/TGW/tgw-lib/actors/codex/artifacts/sha256/a52a7f06885379d6e835b05f3918d19601e1b7fde063afbc5d2bad0e61f022ed.tar")
KNOWN_HOSTS_REF = "artifact:sha256:2efd6fc4243b15b6d0b16a8da723911614198620cabf31bc822cf12520715cdf"
KNOWN_HOSTS_PATH = Path("/opt/TGW/tgw-lib/actors/codex/artifacts/sha256/2efd6fc4243b15b6d0b16a8da723911614198620cabf31bc822cf12520715cdf.known_hosts")
FAILURE_RECEIPT_ROOT = Path("/opt/TGW/tgw-lib/actors/codex/nixos-reviewed-evaluation-failures")


class RuntimeCompositionError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


@dataclass(frozen=True)
class ExactArtifactResolver:
    source_ref: str = SOURCE_REF
    source_path: Path = SOURCE_PATH

    def __call__(self, artifact_ref: str) -> Path:
        if artifact_ref != self.source_ref:
            raise RuntimeCompositionError("source artifact identity is not registered")
        return self.source_path


def preflight_reviewed_evaluation(parameters: Mapping[str, str]) -> dict[str, Any]:
    if parameters.get("artifact_ref") != SOURCE_REF or parameters.get("source_archive_sha256") != "sha256:" + SOURCE_REF.rsplit(":", 1)[1]:
        raise RuntimeCompositionError("request source artifact binding mismatch")
    if parameters.get("known_hosts_sha256") != "sha256:" + KNOWN_HOSTS_REF.rsplit(":", 1)[1]:
        raise RuntimeCompositionError("request known-hosts binding mismatch")
    artifacts = (
        ("source_archive", SOURCE_PATH, 0o444, 8_888_320, parameters["source_archive_sha256"]),
        ("known_hosts", KNOWN_HOSTS_PATH, 0o444, 95, parameters["known_hosts_sha256"]),
    )
    result = {}
    for name, path, mode, size, digest in artifacts:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink() or stat.S_IMODE(metadata.st_mode) != mode or metadata.st_uid != os.geteuid() or metadata.st_size != size or _sha256(path) != digest:
            raise RuntimeCompositionError(f"{name} stable artifact identity mismatch")
        result[name] = {"path": str(path), "mode": f"{mode:04o}", "owner_uid": metadata.st_uid, "size": size, "sha256": digest, "symlink": False}
    return {
        "schema": "tgw-nixos-reviewed-evaluation-runtime-preflight/v1",
        "request_hash": "sha256:316a75010db690946e2566a82ab049882ee2c9a76632c7ed2bcd7c742f5e7406",
        "resolver_id": "nixos-reviewed-evaluation-exact-artifacts@1",
        "artifacts": result,
        "ssh_started": False,
    }


def compose_reviewed_evaluation_provider(effect: Mapping[str, Any], *, invoke=None) -> tuple[SshReviewedEvaluationProvider, Mapping[str, Any]]:
    if effect.get("kind") != "nixos-reviewed-evaluation" or not isinstance(effect.get("generation"), str) or not isinstance(effect.get("parameters"), Mapping):
        raise RuntimeCompositionError("exact typed evaluation effect envelope is required")
    parameters = effect["parameters"]
    if "generation" in parameters:
        raise RuntimeCompositionError("effect generation must not be duplicated in parameters")
    preflight = preflight_reviewed_evaluation(parameters)
    failure_store = ImmutableFailureReceiptStore(FAILURE_RECEIPT_ROOT)
    provider = SshReviewedEvaluationProvider(
        ExactArtifactResolver(),
        known_hosts=KNOWN_HOSTS_PATH,
        request_hash=preflight["request_hash"],
        failure_store=failure_store,
        invoke=invoke,
    )
    return provider, {**preflight, "failure_store": failure_store.readiness}
