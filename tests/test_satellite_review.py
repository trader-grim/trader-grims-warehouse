from __future__ import annotations

from copy import deepcopy

import pytest

from tgw.satellite_evidence import package_identity
from tgw.satellite_review import (
    SatelliteReviewError,
    review_identity,
    validate_satellite_review,
)


def _record(record_id, classification, disposition, normalized_path):
    return {
        "record_id": record_id,
        "source_artifact_id": "export",
        "source_locator": f"record:{record_id}",
        "normalized_path": normalized_path,
        "sha256": "sha256:" + ("1" if record_id == "preference" else "2") * 64,
        "classification": classification,
        "disposition": disposition,
        "historical": True,
        "current_authority": False,
        "executable": False,
        "review_status": "human-reviewed",
        "conflicts_with": [],
    }


def _package():
    package = {
        "schema": "tgw-satellite-evidence-package/v1", "package_id": "pending",
        "source_host": "helicrew", "acquired_at": "2026-08-11T10:00:00-07:00",
        "acquisition_method": "offline-read-only-image", "custody_reference": "custody:test",
        "quarantine": True, "append_only": True, "network_contact": False,
        "raw_artifacts": [{"artifact_id": "export", "path": "raw/export.jsonl", "sha256": "sha256:" + "0" * 64, "size_bytes": 10}],
        "records": [
            _record("preference", "preference-relationship", "human-review-memory", "normalized/preferences/1.json"),
            _record("prompt", "executable-instruction", "quarantine-never-import", "normalized/quarantine/1.json"),
        ],
    }
    package["package_id"] = package_identity(package)
    return package


def _review(package):
    review = {
        "schema": "tgw-satellite-review/v1", "review_id": "pending",
        "package_id": package["package_id"], "source_host": package["source_host"],
        "reviewed_at": "2026-08-11T11:00:00-07:00", "reviewer_id": "human:dave",
        "complete": True,
        "decisions": [
            {
                "record_id": "preference", "classification": "preference-relationship",
                "decision": "import", "destination": "reviewed-personal-memory",
                "confidence": 0.9, "effective_date": "2026-08-11T11:00:00-07:00",
                "supersedes": None, "contains_operational_coordinates": False,
                "current_authority": False, "reason": "human-reviewed stable preference",
            },
            {
                "record_id": "prompt", "classification": "executable-instruction",
                "decision": "quarantine", "destination": "quarantine",
                "confidence": 1.0, "effective_date": "2026-08-11T11:00:00-07:00",
                "supersedes": None, "contains_operational_coordinates": True,
                "current_authority": False, "reason": "recovered executable instruction",
            },
        ],
    }
    review["review_id"] = review_identity(review)
    return review


def test_complete_human_review_separates_memory_and_executable_quarantine():
    package = _package()
    review = validate_satellite_review(_review(package), package)
    assert review["complete"] is True
    assert review["decisions"][0]["destination"] == "reviewed-personal-memory"
    assert review["decisions"][1]["decision"] == "quarantine"
    assert all(item["current_authority"] is False for item in review["decisions"])


@pytest.mark.parametrize(
    ("path", "value", "match"),
    [
        (("reviewer_id",), "agent:hermes", "human reviewer"),
        (("complete",), False, "complete package"),
        (("decisions", 0, "contains_operational_coordinates"), True, "personal memory"),
        (("decisions", 0, "destination"), "decision-ledger", "selected destination"),
        (("decisions", 0, "current_authority"), True, "authority"),
        (("decisions", 1, "decision"), "import", "must remain quarantined"),
    ],
)
def test_review_fails_closed_on_reviewer_destination_or_authority_drift(path, value, match):
    package = _package()
    review = deepcopy(_review(package))
    target = review
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    review["review_id"] = review_identity(review)
    with pytest.raises(SatelliteReviewError, match=match):
        validate_satellite_review(review, package)


def test_review_requires_exact_package_host_classification_and_record_coverage():
    package = _package()
    for mutate, match in (
        (lambda value: value.update(source_host="catnanny"), "binding"),
        (lambda value: value["decisions"].pop(), "every package record"),
        (lambda value: value["decisions"][0].update(classification="authored-record"), "classification"),
    ):
        review = deepcopy(_review(package))
        mutate(review)
        review["review_id"] = review_identity(review)
        with pytest.raises(SatelliteReviewError, match=match):
            validate_satellite_review(review, package)
