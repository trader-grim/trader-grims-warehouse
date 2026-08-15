"""Human-reviewed import decisions for quarantined satellite evidence records."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Mapping

from tgw.satellite_evidence import validate_satellite_evidence_package


class SatelliteReviewError(ValueError):
    """A satellite review ledger is incomplete, unsafe, or incorrectly bound."""


_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_TOP = {
    "schema", "review_id", "package_id", "source_host", "reviewed_at",
    "reviewer_id", "complete", "decisions",
}
_DECISION = {
    "record_id", "classification", "decision", "destination", "confidence",
    "effective_date", "supersedes", "contains_operational_coordinates",
    "current_authority", "reason",
}
_DESTINATIONS = {
    "sourced-fact": {"reviewed-library"},
    "authored-record": {"decision-ledger", "commitments", "reviewed-library", "reviewed-issues", "state-history"},
    "operational-event": {"historical-index"},
    "preference-relationship": {"reviewed-personal-memory"},
    "inferred-memory": {"historical-index"},
    "obsolete-procedure": {"provenance-index"},
    "executable-instruction": {"quarantine"},
    "secret": {"restricted-secret-ledger"},
}


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise SatelliteReviewError("review ledger is not canonical JSON data") from exc


def review_identity(review: Mapping[str, Any]) -> str:
    body = {key: value for key, value in review.items() if key != "review_id"}
    return "sha256:" + hashlib.sha256(_canonical(body)).hexdigest()


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise SatelliteReviewError(f"{label} must be a canonical non-empty string")
    return value


def _timestamp(value: Any, label: str) -> None:
    raw = _string(value, label)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SatelliteReviewError(f"{label} must be a timezone-aware timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SatelliteReviewError(f"{label} must be a timezone-aware timestamp")


def validate_satellite_review(
    review_raw: Mapping[str, Any],
    package_raw: Mapping[str, Any],
) -> dict[str, Any]:
    package = validate_satellite_evidence_package(package_raw)
    review = dict(review_raw)
    if set(review) != _TOP or review.get("schema") != "tgw-satellite-review/v1":
        raise SatelliteReviewError("review ledger top-level contract is invalid")
    if review.get("package_id") != package["package_id"] or review.get("source_host") != package["source_host"]:
        raise SatelliteReviewError("review ledger package or source-host binding mismatch")
    _timestamp(review.get("reviewed_at"), "reviewed_at")
    reviewer = _string(review.get("reviewer_id"), "reviewer_id")
    if not reviewer.startswith("human:"):
        raise SatelliteReviewError("initial satellite review requires a human reviewer")
    if review.get("complete") is not True:
        raise SatelliteReviewError("review ledger must explicitly cover the complete package")
    decisions = review.get("decisions")
    if not isinstance(decisions, list):
        raise SatelliteReviewError("review decisions must be a list")
    records = {record["record_id"]: record for record in package["records"]}
    seen: set[str] = set()
    for decision in decisions:
        if not isinstance(decision, dict) or set(decision) != _DECISION:
            raise SatelliteReviewError("review decision fields are invalid")
        record_id = _string(decision["record_id"], "record_id")
        if record_id in seen or record_id not in records:
            raise SatelliteReviewError("review record identity is duplicate or unknown")
        seen.add(record_id)
        record = records[record_id]
        classification = decision["classification"]
        if classification != record["classification"]:
            raise SatelliteReviewError("review classification does not match evidence")
        if decision["decision"] not in {"import", "retain-historical", "quarantine", "exclude"}:
            raise SatelliteReviewError("review decision is invalid")
        destination = decision["destination"]
        if decision["decision"] == "exclude":
            if destination is not None:
                raise SatelliteReviewError("excluded records cannot have an import destination")
        elif destination not in _DESTINATIONS[classification]:
            raise SatelliteReviewError("record class cannot enter the selected destination")
        if classification in {"executable-instruction", "secret"} and decision["decision"] != "quarantine":
            raise SatelliteReviewError("executable instructions and secrets must remain quarantined")
        if classification == "preference-relationship" and decision["decision"] == "import":
            if destination != "reviewed-personal-memory" or decision["contains_operational_coordinates"] is not False:
                raise SatelliteReviewError("personal memory cannot contain machine paths permissions or procedures")
        if not isinstance(decision["contains_operational_coordinates"], bool):
            raise SatelliteReviewError("operational-coordinate classification must be explicit")
        if decision["current_authority"] is not False:
            raise SatelliteReviewError("review decisions cannot grant current authority")
        confidence = decision["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise SatelliteReviewError("review confidence must be between zero and one")
        _timestamp(decision["effective_date"], "effective_date")
        supersedes = decision["supersedes"]
        if supersedes is not None and (not isinstance(supersedes, str) or not _HASH.fullmatch(supersedes)):
            raise SatelliteReviewError("supersession identity is invalid")
        _string(decision["reason"], "review reason")
    if seen != set(records):
        raise SatelliteReviewError("review ledger does not cover every package record")
    if review.get("review_id") != review_identity(review):
        raise SatelliteReviewError("review ledger identity mismatch")
    return review
