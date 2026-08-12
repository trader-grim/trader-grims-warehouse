"""Closed request/result contract for non-deploying observer artifact rendering."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

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
    if not isinstance(result["output_root"], str) or not re.fullmatch(r"/nix/store/[0-9a-df-np-sv-z]{32}-[A-Za-z0-9+._?=-]+", result["output_root"]):
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
