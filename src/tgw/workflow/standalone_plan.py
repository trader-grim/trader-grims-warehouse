"""Strict, side-effect-free ``tgw-plan/v1`` compiler and evidence evaluator.

Plan prose is deliberately never returned by the parser.  The only executable
projection is data from the single ``Workflow contract`` YAML fence, validated
against an explicit registry snapshot.  This module does not dispatch work or
change a canonical plan/Todo document.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml


class PlanValidationError(ValueError):
    """The canonical plan or one of its bindings is invalid."""


_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")
_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_CONTRACT = re.compile(
    r"^## Workflow contract\s*\n+```ya?ml\s*\n(.*?)\n```\s*$",
    re.MULTILINE | re.DOTALL,
)
_STATUSES = {
    "proposed", "approved", "active", "held", "completion_candidate",
    "complete", "superseded", "abandoned",
}
_RESULTS = {"true", "false", "unknown", "stale", "contradictory"}
_COMPILABLE_STATUSES = {"approved", "active"}
_FRESHNESS = {"same-plan-version", "same-registry-revision"}
_TOP_KEYS = {
    "schema", "plan_id", "version", "status", "owner", "authority_class",
    "created_at", "supersedes", "registry_revision", "scope_hash", "tracks",
    "dependencies",
}
_CONTRACT_KEYS = {"work_units", "plan_acceptance", "rollback", "exclusions"}
_UNIT_KEYS = {
    "id", "title", "kind", "requires", "owns", "effect_class", "authority",
    "treatment_id", "treatment_version", "inputs", "outputs", "acceptance",
    "on_conflict", "rollback",
}
_ACCEPT_KEYS = {
    "id", "verifier", "assertion", "evidence_schema", "freshness",
}
_EVIDENCE_KEYS = {
    "schema", "receipt_id", "plan_id", "plan_version", "scope_hash",
    "registry_revision", "graph_id", "condition_hash", "work_unit_id",
    "acceptance_id", "entity", "verifier", "verifier_version", "result",
    "observed_at", "evidence_hash", "effect_receipts", "rollback_receipts",
}


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PlanValidationError("value is not canonical JSON") from exc


def canonical_hash(value: Any) -> str:
    """Return a stable, type-preserving JSON hash."""
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _timestamp(value: Any, label: str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise PlanValidationError(f"{label} must be a canonical timezone-aware timestamp")
        return value.astimezone(timezone.utc)
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise PlanValidationError(f"{label} must be a canonical timezone-aware timestamp")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise PlanValidationError(f"{label} must be a canonical timezone-aware timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PlanValidationError(f"{label} must be a canonical timezone-aware timestamp")
    return parsed.astimezone(timezone.utc)


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise PlanValidationError(f"{label} must be a non-empty canonical string")
    return value


def _version(value: Any, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise PlanValidationError(f"{label} must be a positive integer or canonical string")
    if isinstance(value, int):
        if value < 1:
            raise PlanValidationError(f"{label} must be positive")
        return str(value)
    return _string(value, label)


def _string_list(value: Any, label: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        raise PlanValidationError(f"{label} must be a{' non-empty' if nonempty else ''} string list")
    if not all(isinstance(item, str) and item.strip() and item == item.strip() for item in value):
        raise PlanValidationError(f"{label} must be a string list")
    if len(value) != len(set(value)):
        raise PlanValidationError(f"{label} contains duplicates")
    return value


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise PlanValidationError(f"{label} must be a string-keyed mapping")
    return value


def _keys(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise PlanValidationError(f"{label} has unknown keys: {', '.join(sorted(unknown))}")


def _yaml(fragment: str, label: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(fragment)
    except yaml.YAMLError as exc:
        raise PlanValidationError(f"invalid {label} YAML: {exc}") from exc
    return _mapping(value, label)


@dataclass(frozen=True)
class PlanDocument:
    metadata: dict[str, Any]
    contract: dict[str, Any]
    source_hash: str

    @property
    def scope_hash(self) -> str:
        return canonical_hash({
            "tracks": self.metadata["tracks"],
            "dependencies": self.metadata["dependencies"],
            "exclusions": self.contract["exclusions"],
            "work_units": self.contract["work_units"],
            "plan_acceptance": self.contract["plan_acceptance"],
            "rollback": self.contract["rollback"],
        })


def parse_plan(text: str) -> PlanDocument:
    """Parse only metadata and the machine fence; narrative remains inert."""
    front = _FRONT.search(text)
    matches = list(_CONTRACT.finditer(text))
    if front is None or len(matches) != 1:
        raise PlanValidationError("plan requires front matter and exactly one Workflow contract YAML fence")
    metadata = _yaml(front.group(1), "front matter")
    contract = _yaml(matches[0].group(1), "workflow contract")
    return PlanDocument(metadata, contract, canonical_hash(text))


def load_plan(path: Path) -> PlanDocument:
    return parse_plan(path.read_text(encoding="utf-8"))


def validate_plan(plan: PlanDocument, registry: Mapping[str, Any]) -> PlanDocument:
    """Validate schema, graph, registry enums/IDs, and the declared scope hash."""
    meta, contract = plan.metadata, plan.contract
    registry = _mapping(registry, "registry")
    if registry.get("schema") != "tgw-plan-registry/v1":
        raise PlanValidationError("registry has an unsupported schema")
    _keys(meta, _TOP_KEYS, "front matter")
    _keys(contract, _CONTRACT_KEYS, "workflow contract")
    required_meta = _TOP_KEYS - {"supersedes"}
    required_contract = _CONTRACT_KEYS
    missing = required_meta - set(meta)
    if missing or required_contract - set(contract):
        names = sorted(missing | (required_contract - set(contract)))
        raise PlanValidationError(f"missing required fields: {', '.join(names)}")
    if meta["schema"] != "tgw-plan/v1" or isinstance(meta["version"], bool) or not isinstance(meta["version"], int) or meta["version"] < 1:
        raise PlanValidationError("schema must be tgw-plan/v1 and version a positive integer")
    if not isinstance(meta["plan_id"], str) or not _ID.fullmatch(meta["plan_id"]):
        raise PlanValidationError("invalid plan_id")
    if meta["status"] not in _STATUSES:
        raise PlanValidationError("invalid plan status")
    for key in ("owner", "authority_class"):
        if not isinstance(meta[key], str) or not meta[key].strip():
            raise PlanValidationError(f"{key} must be non-empty")
    if not _HASH.fullmatch(str(meta["registry_revision"])) or not _HASH.fullmatch(str(meta["scope_hash"])):
        raise PlanValidationError("registry_revision and scope_hash must be canonical sha256 values")
    if canonical_hash(registry) != meta["registry_revision"]:
        raise PlanValidationError("registry snapshot does not match registry_revision")
    _timestamp(meta["created_at"], "created_at")
    _string_list(meta["tracks"], "tracks", nonempty=True)
    _string_list(meta["dependencies"], "dependencies")
    _string_list(contract["exclusions"], "exclusions", nonempty=True)
    if not isinstance(contract["rollback"], str) or not contract["rollback"].strip():
        raise PlanValidationError("rollback must be non-empty")
    units = contract["work_units"]
    if not isinstance(units, list) or not units:
        raise PlanValidationError("work_units must be a non-empty list")
    registered = _mapping(registry.get("treatments"), "registry treatments")
    verifiers = _mapping(registry.get("verifiers"), "registry verifiers")
    enums = _mapping(registry.get("enums"), "registry enums")
    for enum_name in ("kind", "effect_class", "authority"):
        _string_list(enums.get(enum_name), f"registry enum {enum_name}", nonempty=True)
    by_id: dict[str, dict[str, Any]] = {}
    acceptance_ids: set[str] = set()
    for raw in units:
        unit = _mapping(raw, "work unit")
        _keys(unit, _UNIT_KEYS, f"work unit {unit.get('id', '?')}")
        missing_unit = _UNIT_KEYS - set(unit)
        if missing_unit:
            raise PlanValidationError(f"work unit missing fields: {', '.join(sorted(missing_unit))}")
        uid = unit["id"]
        if not isinstance(uid, str) or not _ID.fullmatch(uid) or uid in by_id:
            raise PlanValidationError(f"invalid or duplicate work unit id: {uid!r}")
        by_id[uid] = unit
        _string(unit["treatment_id"], f"work unit {uid} treatment_id")
        treatment_version = _version(unit["treatment_version"], f"work unit {uid} treatment_version")
        treatment = registered.get(unit["treatment_id"])
        if not isinstance(treatment, dict) or _version(treatment.get("version"), f"treatment {unit['treatment_id']} version") != treatment_version:
            raise PlanValidationError(f"unregistered treatment: {unit['treatment_id']}@{unit['treatment_version']}")
        for key in ("kind", "effect_class", "authority"):
            if unit[key] not in enums.get(key, []):
                raise PlanValidationError(f"unregistered {key}: {unit[key]}")
        _string(unit["title"], f"work unit {uid} title")
        _string_list(unit["requires"], f"work unit {uid} requires")
        _string_list(unit["owns"], f"work unit {uid} owns", nonempty=True)
        if not isinstance(unit["inputs"], dict) or not all(isinstance(key, str) for key in unit["inputs"]):
            raise PlanValidationError(f"work unit {uid} inputs must be a string-keyed mapping")
        if not isinstance(unit["outputs"], list) or not unit["outputs"] or not all(
            isinstance(output, dict)
            and set(output) == {"id", "schema"}
            and isinstance(output["id"], str)
            and _ID.fullmatch(output["id"])
            and isinstance(output["schema"], str)
            and output["schema"].strip()
            for output in unit["outputs"]
        ):
            raise PlanValidationError(f"work unit {uid} outputs are invalid")
        _string(unit["on_conflict"], f"work unit {uid} on_conflict")
        _string(unit["rollback"], f"work unit {uid} rollback")
        if not isinstance(unit["acceptance"], list) or not unit["acceptance"]:
            raise PlanValidationError(f"work unit {uid} requires acceptance")
        for raw_acceptance in unit["acceptance"]:
            acceptance = _mapping(raw_acceptance, "acceptance")
            _keys(acceptance, _ACCEPT_KEYS, f"acceptance {acceptance.get('id', '?')}")
            if set(acceptance) != _ACCEPT_KEYS:
                raise PlanValidationError("acceptance fields are incomplete")
            aid = acceptance["id"]
            if not isinstance(aid, str) or not _ID.fullmatch(aid) or aid in acceptance_ids:
                raise PlanValidationError(f"invalid or duplicate acceptance id: {aid!r}")
            acceptance_ids.add(aid)
            if acceptance["verifier"] not in verifiers:
                raise PlanValidationError(f"unregistered verifier: {acceptance['verifier']}")
            verifier = verifiers[acceptance["verifier"]]
            if not isinstance(verifier, dict):
                raise PlanValidationError(f"verifier has no version: {acceptance['verifier']}")
            _version(verifier.get("version"), f"verifier {acceptance['verifier']} version")
            for key in ("assertion", "evidence_schema"):
                _string(acceptance[key], f"acceptance {aid} {key}")
            if acceptance["evidence_schema"] != "tgw-plan-evidence/v1":
                raise PlanValidationError(f"acceptance {aid} has unsupported evidence schema")
            if acceptance["freshness"] not in _FRESHNESS:
                raise PlanValidationError(f"acceptance {aid} has unsupported freshness rule")
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(uid: str) -> None:
        if uid in visiting:
            raise PlanValidationError("work unit dependency cycle")
        if uid in visited:
            return
        if uid not in by_id:
            raise PlanValidationError(f"unknown work unit dependency: {uid}")
        visiting.add(uid)
        for dependency in by_id[uid]["requires"]:
            visit(dependency)
        visiting.remove(uid)
        visited.add(uid)
    for uid in by_id:
        visit(uid)
    if (
        not isinstance(contract["plan_acceptance"], list)
        or not all(isinstance(item, str) for item in contract["plan_acceptance"])
        or len(contract["plan_acceptance"]) != len(set(contract["plan_acceptance"]))
        or set(contract["plan_acceptance"])
        != {f"{unit['id']}:{item['id']}" for unit in units for item in unit["acceptance"]}
    ):
        raise PlanValidationError("plan_acceptance must exactly name every work-unit acceptance condition")
    if plan.scope_hash != meta["scope_hash"]:
        raise PlanValidationError("declared scope_hash does not match canonical scope")
    canonical_hash(contract)
    return plan


def compile_plan(plan: PlanDocument, registry: Mapping[str, Any], repository: Mapping[str, str]) -> dict[str, Any]:
    """Compile bounded units to a hash-addressed governed-workflow projection."""
    validate_plan(plan, registry)
    if plan.metadata["status"] not in _COMPILABLE_STATUSES:
        raise PlanValidationError("only approved or active plans may be compiled")
    if set(repository) != {"repository_id", "canonical_root", "source_commit"}:
        raise PlanValidationError("repository binding must contain repository_id, canonical_root, and source_commit")
    if not all(isinstance(value, str) and value for value in repository.values()):
        raise PlanValidationError("repository binding values must be non-empty strings")
    if not Path(repository["canonical_root"]).is_absolute() or re.fullmatch(r"[0-9a-f]{40}", repository["source_commit"]) is None:
        raise PlanValidationError("repository binding requires an absolute root and exact lowercase commit")
    conditions = []
    units = []
    for unit in plan.contract["work_units"]:
        acceptance = []
        for condition in unit["acceptance"]:
            condition_id = f"{unit['id']}:{condition['id']}"
            conditions.append(condition_id)
            acceptance.append({
                **condition,
                "condition_id": condition_id,
                "verifier_version": str(registry["verifiers"][condition["verifier"]]["version"]),
            })
        units.append({key: unit[key] for key in _UNIT_KEYS if key != "acceptance"} | {"acceptance": acceptance})
    binding = {
        "plan_id": plan.metadata["plan_id"], "plan_version": plan.metadata["version"],
        "source_hash": plan.source_hash, "scope_hash": plan.scope_hash,
        "registry_revision": plan.metadata["registry_revision"], "repository": dict(repository),
    }
    condition_hash = canonical_hash(sorted(conditions))
    return {
        "schema": "tgw-plan-compiled-graph/v1", **binding,
        "graph_id": canonical_hash({**binding, "condition_hash": condition_hash}),
        "condition_hash": condition_hash, "work_units": units,
        "required_conditions": sorted(conditions),
        "plan_acceptance": plan.contract["plan_acceptance"],
    }


def validate_evidence(receipt: Mapping[str, Any], graph: Mapping[str, Any]) -> dict[str, Any]:
    """Reject evidence that is stale, incomplete, self-hashed incorrectly, or unbound."""
    data = dict(receipt)
    _keys(data, _EVIDENCE_KEYS, "evidence receipt")
    required = _EVIDENCE_KEYS - {"effect_receipts", "rollback_receipts"}
    if required - set(data):
        raise PlanValidationError("evidence receipt is incomplete")
    bindings = {
        "plan_id": graph["plan_id"], "plan_version": graph["plan_version"],
        "scope_hash": graph["scope_hash"], "registry_revision": graph["registry_revision"],
        "graph_id": graph["graph_id"], "condition_hash": graph["condition_hash"],
    }
    for key, expected in bindings.items():
        if data.get(key) != expected:
            raise PlanValidationError(f"stale evidence: {key} binding mismatch")
    if data["schema"] != "tgw-plan-evidence/v1" or data["result"] not in _RESULTS:
        raise PlanValidationError("invalid evidence schema or result")
    for key in ("receipt_id", "work_unit_id", "acceptance_id", "entity"):
        _string(data[key], f"evidence {key}")
    _timestamp(data["observed_at"], "evidence observed_at")
    for key in ("effect_receipts", "rollback_receipts"):
        if key in data:
            _string_list(data[key], f"evidence {key}")
    condition_id = f"{data['work_unit_id']}:{data['acceptance_id']}"
    if condition_id not in graph["required_conditions"]:
        raise PlanValidationError("evidence references an unknown acceptance condition")
    expected_condition = next(
        condition
        for unit in graph["work_units"]
        for condition in unit["acceptance"]
        if condition["condition_id"] == condition_id
    )
    expected_unit = next(unit for unit in graph["work_units"] if unit["id"] == data["work_unit_id"])
    if data["entity"] not in expected_unit["owns"]:
        raise PlanValidationError("evidence entity is outside work-unit ownership")
    if data["verifier"] != expected_condition["verifier"] or str(data["verifier_version"]) != expected_condition["verifier_version"]:
        raise PlanValidationError("evidence verifier binding mismatch")
    unhashed = {key: value for key, value in data.items() if key != "evidence_hash"}
    if data["evidence_hash"] != canonical_hash(unhashed):
        raise PlanValidationError("evidence_hash mismatch")
    return data


def status(graph: Mapping[str, Any], receipts: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Produce a read-only status projection; invalid receipts fail closed."""
    by_condition: dict[str, list[dict[str, Any]]] = {}
    receipt_ids: set[str] = set()
    for raw in receipts:
        receipt = validate_evidence(raw, graph)
        if receipt["receipt_id"] in receipt_ids:
            raise PlanValidationError("duplicate evidence receipt_id")
        receipt_ids.add(receipt["receipt_id"])
        key = f"{receipt['work_unit_id']}:{receipt['acceptance_id']}"
        by_condition.setdefault(key, []).append(receipt)
    conditions = []
    for condition_id in graph["required_conditions"]:
        values = by_condition.get(condition_id, [])
        results = {item["result"] for item in values}
        result = "unknown" if not results else (next(iter(results)) if len(results) == 1 else "contradictory")
        conditions.append({"condition_id": condition_id, "result": result, "receipt_ids": sorted(item["receipt_id"] for item in values)})
    ready = bool(conditions) and all(item["result"] == "true" for item in conditions)
    return {"schema": "tgw-plan-status/v1", "graph_id": graph["graph_id"], "ready_for_candidate": ready, "conditions": conditions}


def completion_candidate(
    graph: Mapping[str, Any],
    receipts: Iterable[Mapping[str, Any]],
    *,
    created_at: str,
    expires_at: str,
    active_attempts: Iterable[str] = (),
    ownership_conflicts: Iterable[str] = (),
    reconciliation_gates: Iterable[str] = (),
    explicit_requirements: Iterable[str] = (),
) -> dict[str, Any]:
    validated_receipts = [validate_evidence(receipt, graph) for receipt in receipts]
    projection = status(graph, validated_receipts)
    if not projection["ready_for_candidate"]:
        raise PlanValidationError("current evidence does not satisfy every condition")
    created = _timestamp(created_at, "candidate created_at")
    expires = _timestamp(expires_at, "candidate expires_at")
    if expires <= created:
        raise PlanValidationError("candidate expiry must follow creation")
    gates = {
        "active_attempts": _string_list(list(active_attempts), "active_attempts"),
        "ownership_conflicts": _string_list(list(ownership_conflicts), "ownership_conflicts"),
        "reconciliation_gates": _string_list(list(reconciliation_gates), "reconciliation_gates"),
        "explicit_requirements": _string_list(list(explicit_requirements), "explicit_requirements"),
    }
    if any(gates.values()):
        raise PlanValidationError("completion candidate has unresolved attempts or gates")
    evidence = {item["condition_id"]: item["receipt_ids"][0] for item in projection["conditions"]}
    effect_receipts = sorted({item for receipt in validated_receipts for item in receipt.get("effect_receipts", [])})
    rollback_receipts = sorted({item for receipt in validated_receipts for item in receipt.get("rollback_receipts", [])})
    candidate = {
        "schema": "plan-completion-candidate/v1", "plan_id": graph["plan_id"],
        "plan_version": graph["plan_version"], "scope_hash": graph["scope_hash"],
        "registry_revision": graph["registry_revision"], "graph_id": graph["graph_id"],
        "condition_hash": graph["condition_hash"], "repository": graph["repository"],
        "acceptance_receipts": evidence, **gates,
        "effect_receipts": effect_receipts, "rollback_receipts": rollback_receipts,
        "created_at": created_at, "expires_at": expires_at,
    }
    return {**candidate, "candidate_id": canonical_hash(candidate)}


def persist_artifact(directory: Path, artifact: Mapping[str, Any]) -> Path:
    """Persist a hash-addressed immutable artifact without changing plan source."""
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise PlanValidationError("artifact directory must be a real directory")
    digest = canonical_hash(artifact).split(":", 1)[1]
    target = directory / f"{digest}.json"
    payload = json.dumps(artifact, sort_keys=True, indent=2) + "\n"
    if target.is_symlink():
        raise PlanValidationError("immutable artifact target must not be a symlink")
    if target.exists():
        if target.read_text(encoding="utf-8") != payload:
            raise PlanValidationError("immutable artifact path collision")
        return target
    temporary = directory / f".{digest}.{os.getpid()}.{os.urandom(8).hex()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.is_symlink() or target.read_text(encoding="utf-8") != payload:
                raise PlanValidationError("immutable artifact path collision")
        directory_descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target
