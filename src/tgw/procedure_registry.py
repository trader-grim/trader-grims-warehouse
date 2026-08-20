"""Strict, non-executing registry for effectful TGW operational procedures."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping


class ProcedureRegistryError(ValueError):
    """The procedure registry or requested procedure is invalid."""


_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9.-]{2,95}/v[1-9][0-9]*$")
_SAFE_ARG = re.compile(r"^[A-Za-z0-9_./:#@+=,-]+$")
_TOP_KEYS = {"schema", "revision", "procedures"}
_PROCEDURE_KEYS = {
    "status", "effect_class", "host_role", "repository_id", "working_directory",
    "run_as", "argv", "authority_gate", "execution_policy",
    "direct_invocation_allowed", "rollback_procedure", "preconditions",
    "postconditions", "evidence_schema",
}
_FIXED_ARGV = {
    "app-release-install/v1": [
        "/opt/tgw-installer/current/bin/tgw-release-install", "--root", "/opt/TGW", "install",
        "--archive", ":archive", "--generation", ":generation", "--commit", ":commit",
        "--tree", ":tree", "--archive-sha256", ":archive_sha256",
        "--expected-current", ":expected_current", "--operation-id", ":operation_id",
        "--admission-receipt", ":admission_receipt",
        "--environment-preflight-receipt", ":environment_preflight_receipt",
    ],
    "app-release-rollback/v1": [
        "/opt/tgw-installer/current/bin/tgw-release-install", "--root", "/opt/TGW", "rollback",
        "--receipt", ":receipt", "--expected-current", ":expected_current",
        "--operation-id", ":operation_id",
    ],
    "nixos-prod-rollback/v1": ["nixos-rebuild", "switch", "--rollback"],
    "nixos-prod-switch/v1": [
        "nixos-rebuild", "switch", "--flake", "path:/home/db/tgw-flake#tgw-prod",
    ],
}


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise ProcedureRegistryError("procedure registry is not canonical JSON data") from exc


def procedures_revision(procedures: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(procedures)).hexdigest()


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ProcedureRegistryError(f"{label} must be a canonical non-empty string")
    return value


def _strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ProcedureRegistryError(f"{label} must be a non-empty string list")
    result = [_string(item, label) for item in value]
    if len(result) != len(set(result)):
        raise ProcedureRegistryError(f"{label} contains duplicates")
    return result


def validate_procedure_registry(raw: Mapping[str, Any]) -> dict[str, Any]:
    registry = dict(raw)
    if set(registry) != _TOP_KEYS or registry.get("schema") != "tgw-procedure-registry/v1":
        raise ProcedureRegistryError("procedure registry top-level contract is invalid")
    procedures = registry.get("procedures")
    if not isinstance(procedures, dict) or not procedures:
        raise ProcedureRegistryError("procedure registry requires procedures")
    if registry.get("revision") != procedures_revision(procedures):
        raise ProcedureRegistryError("procedure registry revision mismatch")
    if not _HASH.fullmatch(str(registry["revision"])):
        raise ProcedureRegistryError("procedure registry revision is invalid")

    for procedure_id, raw_procedure in procedures.items():
        if not isinstance(procedure_id, str) or not _ID.fullmatch(procedure_id):
            raise ProcedureRegistryError(f"invalid procedure id: {procedure_id!r}")
        if not isinstance(raw_procedure, dict) or set(raw_procedure) != _PROCEDURE_KEYS:
            raise ProcedureRegistryError(f"procedure fields are invalid: {procedure_id}")
        procedure = raw_procedure
        if procedure["status"] not in {"active", "held"}:
            raise ProcedureRegistryError("procedure status is invalid")
        if procedure["effect_class"] != "infrastructure-mutation":
            raise ProcedureRegistryError("effectful Nix procedures must be infrastructure mutations")
        for key in ("host_role", "repository_id", "working_directory", "run_as"):
            _string(procedure[key], f"{procedure_id} {key}")
        if not procedure["working_directory"].startswith("/") or ".." in Path(procedure["working_directory"]).parts:
            raise ProcedureRegistryError("procedure working directory must be absolute and contained")
        argv = _strings(procedure["argv"], f"{procedure_id} argv")
        if any(not _SAFE_ARG.fullmatch(arg) for arg in argv):
            raise ProcedureRegistryError("procedure argv contains shell syntax or unsafe characters")
        if procedure_id not in _FIXED_ARGV or argv != _FIXED_ARGV[procedure_id]:
            raise ProcedureRegistryError("registered procedures must use their exact fixed argv")
        if procedure["authority_gate"] != "explicit-deployment-approval":
            raise ProcedureRegistryError("procedure must require explicit deployment approval")
        expected_policy = "registered-runner-only" if procedure["status"] == "active" else "held-pending-independent-installer"
        if procedure["execution_policy"] != expected_policy or procedure["direct_invocation_allowed"] is not False:
            raise ProcedureRegistryError("procedure status and execution policy must forbid direct instruction execution")
        rollback = procedure["rollback_procedure"]
        if rollback is not None and (not isinstance(rollback, str) or not _ID.fullmatch(rollback)):
            raise ProcedureRegistryError("rollback procedure identity is invalid")
        _strings(procedure["preconditions"], f"{procedure_id} preconditions")
        _strings(procedure["postconditions"], f"{procedure_id} postconditions")
        if procedure["evidence_schema"] != "tgw-procedure-receipt/v1":
            raise ProcedureRegistryError("procedure evidence schema is invalid")

    for procedure_id, procedure in procedures.items():
        rollback = procedure["rollback_procedure"]
        if rollback is not None and rollback not in procedures:
            raise ProcedureRegistryError(f"unknown rollback procedure for {procedure_id}")
    return registry


def load_procedure_registry(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProcedureRegistryError(f"cannot load procedure registry: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProcedureRegistryError("procedure registry must be a mapping")
    return validate_procedure_registry(raw)


def resolve_procedure(registry: Mapping[str, Any], procedure_id: str) -> dict[str, Any]:
    validated = validate_procedure_registry(registry)
    try:
        procedure = validated["procedures"][procedure_id]
    except KeyError as exc:
        raise ProcedureRegistryError(f"unknown procedure identity: {procedure_id}") from exc
    if procedure["status"] != "active":
        raise ProcedureRegistryError(f"procedure is held and cannot execute: {procedure_id}")
    return {"procedure_id": procedure_id, "registry_revision": validated["revision"], **procedure}
