"""Closed request/result contract for non-deploying observer artifact rendering."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

SCHEMA = "tgw-nix-observer-render-evaluation-request/v1"
RESULT_SCHEMA = "tgw-nix-observer-render-evaluation-receipt/v1"
TARGET = "nix-input-observer-rendered-artifacts"
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
    if not isinstance(request["input_closure_manifest"], list) or len(request["input_closure_manifest"]) != 1:
        raise RenderEvaluationError("render input closure is not exact")
    if not isinstance(request["max_duration_seconds"], int) or not 1 <= request["max_duration_seconds"] <= 900:
        raise RenderEvaluationError("render timeout invalid")
    if not isinstance(request["max_output_bytes"], int) or not 1 <= request["max_output_bytes"] <= 16 * 1024 * 1024:
        raise RenderEvaluationError("render output bound invalid")
    return dict(value)


def validate_result(value: Mapping[str, object], *, request: Mapping[str, object]) -> dict[str, object]:
    fields = {"schema", "request_sha256", "outcome", "metadata_status", "files", "systemd_verify", "cleanup", "forbidden_effects", "receipt_sha256"}
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
    if result["forbidden_effects"] != {"activation": False, "profile_write": False, "home_db_write": False, "network": False}:
        raise RenderEvaluationError("render receipt forbidden-effect evidence mismatch")
    if result["systemd_verify"] != {"exit_code": 0, "units": list(OUTPUTS[9:12])}:
        raise RenderEvaluationError("render systemd verification mismatch")
    files = result["files"]
    if (
        not isinstance(files, list)
        or [item.get("path") for item in files] != list(OUTPUTS)
        or any(set(item) != {"path", "sha256"} or not re.fullmatch(r"sha256:[0-9a-f]{64}", item["sha256"]) for item in files)
    ):
        raise RenderEvaluationError("render output manifest mismatch")
    return dict(value)
