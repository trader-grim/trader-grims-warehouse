"""Validation for neutral, append-only quarantined satellite evidence packages."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Mapping


class SatelliteEvidenceError(ValueError):
    """A satellite evidence package is unsafe, ambiguous, or malformed."""


_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_HOSTS = {"catnanny", "helicrew"}
_CLASSES = {
    "sourced-fact", "authored-record", "operational-event",
    "preference-relationship", "inferred-memory", "obsolete-procedure",
    "executable-instruction", "secret",
}
_DISPOSITIONS = {
    "sourced-fact": {"candidate-current-knowledge", "human-review"},
    "authored-record": {"human-review"},
    "operational-event": {"historical-index"},
    "preference-relationship": {"human-review-memory"},
    "inferred-memory": {"historical-low-confidence"},
    "obsolete-procedure": {"preserve-retired-label"},
    "executable-instruction": {"quarantine-never-import"},
    "secret": {"restricted-rotate-revoke"},
}
_TOP = {
    "schema", "package_id", "source_host", "acquired_at", "acquisition_method",
    "custody_reference", "quarantine", "append_only", "network_contact",
    "raw_artifacts", "records",
}
_RAW = {"artifact_id", "path", "sha256", "size_bytes"}
_RECORD = {
    "record_id", "source_artifact_id", "source_locator", "normalized_path",
    "sha256", "classification", "disposition", "historical", "current_authority",
    "executable", "review_status", "conflicts_with",
}


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise SatelliteEvidenceError("evidence package is not canonical JSON data") from exc


def package_identity(package: Mapping[str, Any]) -> str:
    body = {key: value for key, value in package.items() if key != "package_id"}
    return "sha256:" + hashlib.sha256(_canonical(body)).hexdigest()


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise SatelliteEvidenceError(f"{label} must be a canonical non-empty string")
    return value


def _timestamp(value: Any, label: str) -> None:
    raw = _string(value, label)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SatelliteEvidenceError(f"{label} must be a timezone-aware timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SatelliteEvidenceError(f"{label} must be a timezone-aware timestamp")


def _relative(value: Any, label: str) -> str:
    raw = _string(value, label)
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or str(path) != raw:
        raise SatelliteEvidenceError(f"{label} must be a canonical contained relative path")
    return raw


def validate_satellite_evidence_package(raw: Mapping[str, Any]) -> dict[str, Any]:
    package = dict(raw)
    if set(package) != _TOP or package.get("schema") != "tgw-satellite-evidence-package/v1":
        raise SatelliteEvidenceError("evidence package top-level contract is invalid")
    if package.get("source_host") not in _HOSTS:
        raise SatelliteEvidenceError("source host is not an admitted quarantined satellite")
    _timestamp(package.get("acquired_at"), "acquired_at")
    for key in ("acquisition_method", "custody_reference"):
        _string(package.get(key), key)
    if package.get("quarantine") is not True or package.get("append_only") is not True:
        raise SatelliteEvidenceError("satellite packages must remain quarantined and append-only")
    if package.get("network_contact") not in {False, "restricted-recovery-network"}:
        raise SatelliteEvidenceError("network contact classification is unsafe")

    artifacts = package.get("raw_artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise SatelliteEvidenceError("raw artifact manifest is required")
    artifact_ids: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != _RAW:
            raise SatelliteEvidenceError("raw artifact fields are invalid")
        artifact_id = _string(artifact["artifact_id"], "artifact_id")
        if artifact_id in artifact_ids:
            raise SatelliteEvidenceError("duplicate raw artifact identity")
        artifact_ids.add(artifact_id)
        _relative(artifact["path"], "raw artifact path")
        if not _HASH.fullmatch(str(artifact["sha256"])):
            raise SatelliteEvidenceError("raw artifact digest is invalid")
        if isinstance(artifact["size_bytes"], bool) or not isinstance(artifact["size_bytes"], int) or artifact["size_bytes"] < 0:
            raise SatelliteEvidenceError("raw artifact size is invalid")

    records = package.get("records")
    if not isinstance(records, list):
        raise SatelliteEvidenceError("records must be a list")
    record_ids: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != _RECORD:
            raise SatelliteEvidenceError("record fields are invalid")
        record_id = _string(record["record_id"], "record_id")
        if record_id in record_ids:
            raise SatelliteEvidenceError("duplicate record identity")
        record_ids.add(record_id)
        if record["source_artifact_id"] not in artifact_ids:
            raise SatelliteEvidenceError("record source artifact is unknown")
        _string(record["source_locator"], "source_locator")
        classification = record["classification"]
        if classification not in _CLASSES or record["disposition"] not in _DISPOSITIONS[classification]:
            raise SatelliteEvidenceError("record classification or disposition is invalid")
        if not _HASH.fullmatch(str(record["sha256"])):
            raise SatelliteEvidenceError("record digest is invalid")
        if record["current_authority"] is not False:
            raise SatelliteEvidenceError("recovered records cannot grant current authority")
        if record["historical"] is not True:
            raise SatelliteEvidenceError("recovered records must remain explicitly historical")
        if record["executable"] is not False:
            raise SatelliteEvidenceError("recovered content cannot be executable")
        if record["review_status"] not in {"unreviewed", "human-reviewed"}:
            raise SatelliteEvidenceError("record review status is invalid")
        if not isinstance(record["conflicts_with"], list) or not all(
            isinstance(item, str) and item.strip() and item == item.strip()
            for item in record["conflicts_with"]
        ):
            raise SatelliteEvidenceError("record conflict references are invalid")
        normalized = record["normalized_path"]
        if classification == "secret":
            if normalized is not None:
                raise SatelliteEvidenceError("secret material cannot enter normalized review data")
        else:
            _relative(normalized, "normalized record path")

    for record in records:
        if any(target not in record_ids for target in record["conflicts_with"]):
            raise SatelliteEvidenceError("record conflict target is unknown")
    if package.get("package_id") != package_identity(package):
        raise SatelliteEvidenceError("evidence package identity mismatch")
    return package
