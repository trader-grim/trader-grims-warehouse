"""Pure W16 admission and W17 recovery boundary compilers.

These functions validate and hash declarative decisions only.  They neither
select a release nor invoke a broker, shell, installer, service, or provider.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_IDENTITY = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_RECOVERY_EFFECTS = frozenset({"diagnose-platform", "rollback-platform", "repair-tool-environment"})


class AdmissionRecoveryError(ValueError):
    """An untrusted W16/W17 request cannot cross the declarative boundary."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _exact_hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise AdmissionRecoveryError(f"{label} must be an exact sha256 hash")
    return value


def _commit(value: Any, label: str) -> str:
    if not isinstance(value, str) or _COMMIT.fullmatch(value) is None:
        raise AdmissionRecoveryError(f"{label} must be an exact commit")
    return value


def _identity(value: Any, label: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        raise AdmissionRecoveryError(f"{label} is invalid")
    return value


def _mapping(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise AdmissionRecoveryError(f"{label} fields are not exact")
    return dict(value)


def _utc(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise AdmissionRecoveryError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AdmissionRecoveryError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AdmissionRecoveryError(f"{label} must include timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def compile_release_admission(*, request: Mapping[str, Any]) -> dict[str, Any]:
    """Return a W16 allow/refusal receipt without selecting any release."""
    value = _mapping(request, {"schema", "request_id", "candidate", "plan", "environment", "review", "admission"}, "admission request")
    if value["schema"] != "tgw-w16-release-admission-request/v1":
        raise AdmissionRecoveryError("admission request schema is invalid")
    _identity(value["request_id"], "request id")
    candidate = _mapping(value["candidate"], {"commit", "tree"}, "candidate")
    plan = _mapping(value["plan"], {"commit", "solution_hash"}, "Plan")
    environment = _mapping(value["environment"], {"catalog_hash", "receipt_hash"}, "environment")
    review = _mapping(value["review"], {"status", "candidate_commit", "solution_hash", "receipt_hash"}, "review")
    admission = _mapping(value["admission"], {"status", "candidate_commit", "solution_hash", "receipt_hash"}, "admission")
    _commit(candidate["commit"], "candidate commit")
    _commit(candidate["tree"], "candidate tree")
    _commit(plan["commit"], "Plan commit")
    solution = _exact_hash(plan["solution_hash"], "Plan solution")
    bindings = (
        ("environment catalog", environment["catalog_hash"]),
        ("environment receipt", environment["receipt_hash"]),
        ("review receipt", review["receipt_hash"]),
        ("admission receipt", admission["receipt_hash"]),
    )
    for label, binding in bindings:
        _exact_hash(binding, label)
    reasons: list[str] = []
    for name, receipt in (("review", review), ("admission", admission)):
        if receipt["status"] != "PASS":
            reasons.append(f"{name}-not-passed")
        if receipt["candidate_commit"] != candidate["commit"]:
            reasons.append(f"{name}-candidate-mismatch")
        if receipt["solution_hash"] != solution:
            reasons.append(f"{name}-solution-mismatch")
    unsigned = {
        "schema": "tgw-w16-release-admission-receipt/v1",
        "request_id": value["request_id"],
        "candidate": candidate,
        "plan": plan,
        "environment": environment,
        "status": "REFUSED" if reasons else "ADMITTED",
        "reasons": sorted(set(reasons)),
        "activation": "declarative-only",
    }
    return {**unsigned, "receipt_hash": _hash(unsigned)}


def compile_recovery_invocation(*, request: Mapping[str, Any], observed_at: str) -> dict[str, Any]:
    """Return a W17 platform-only recovery decision, never an effect."""
    value = _mapping(request, {"schema", "recovery_id", "operator", "plan", "expiry", "effects", "receipt_sink", "candidate_commit"}, "recovery request")
    if value["schema"] != "tgw-w17-recovery-request/v1":
        raise AdmissionRecoveryError("recovery request schema is invalid")
    _identity(value["recovery_id"], "recovery id")
    _identity(value["operator"], "operator")
    plan = _mapping(value["plan"], {"commit", "solution_hash"}, "recovery Plan")
    _commit(plan["commit"], "recovery Plan commit")
    _exact_hash(plan["solution_hash"], "recovery Plan solution")
    _commit(value["candidate_commit"], "recovery candidate")
    _exact_hash(value["receipt_sink"], "recovery receipt sink")
    expiry, observed = _utc(value["expiry"], "recovery expiry"), _utc(observed_at, "recovery observation")
    if not isinstance(value["effects"], list) or not value["effects"] or not all(isinstance(item, str) for item in value["effects"]):
        raise AdmissionRecoveryError("recovery effects are invalid")
    reasons = []
    if len(value["effects"]) != len(set(value["effects"])):
        reasons.append("duplicate-effects")
    if not set(value["effects"]) <= _RECOVERY_EFFECTS:
        reasons.append("effect-outside-platform-recovery")
    if datetime.fromisoformat(expiry.replace("Z", "+00:00")) <= datetime.fromisoformat(observed.replace("Z", "+00:00")):
        reasons.append("recovery-expired")
    unsigned = {
        "schema": "tgw-w17-recovery-invocation-receipt/v1",
        "recovery_id": value["recovery_id"],
        "operator": value["operator"],
        "plan": plan,
        "candidate_commit": value["candidate_commit"],
        "effects": list(value["effects"]),
        "expiry": expiry,
        "receipt_sink": value["receipt_sink"],
        "status": "REFUSED" if reasons else "PREPARED",
        "reasons": sorted(set(reasons)),
        "activation": "declarative-only",
    }
    return {**unsigned, "receipt_hash": _hash(unsigned)}
