"""Exact runtime composition for the approved reviewed Nix evaluation artifacts."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from tgw.nixos_reviewed_evaluation import ImmutableFailureReceiptStore, SshReviewedEvaluationProvider

SOURCE_REF = "artifact:sha256:c288e2514b12bad292e6c712280bda1e071effe74deb7f095ad23be698a94fbe"
SOURCE_PATH = Path("/opt/TGW/tgw-lib/actors/codex/artifacts/sha256/c288e2514b12bad292e6c712280bda1e071effe74deb7f095ad23be698a94fbe.tar")
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
        ("source_archive", SOURCE_PATH, 0o444, 8_734_720, parameters["source_archive_sha256"]),
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
        "request_hash": "sha256:5004cc18766a79958485cb30a75382f2b9d7a36a60bbb27f0bbd302b30796fcb",
        "resolver_id": "nixos-reviewed-evaluation-exact-artifacts@1",
        "artifacts": result,
        "ssh_started": False,
    }


def compose_reviewed_evaluation_provider(parameters: Mapping[str, str], *, invoke=None) -> tuple[SshReviewedEvaluationProvider, Mapping[str, Any]]:
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
