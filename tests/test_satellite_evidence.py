from __future__ import annotations

from copy import deepcopy

import pytest

from tgw.satellite_evidence import (
    SatelliteEvidenceError,
    package_identity,
    validate_satellite_evidence_package,
)


def _package():
    package = {
        "schema": "tgw-satellite-evidence-package/v1",
        "package_id": "pending",
        "source_host": "helicrew",
        "acquired_at": "2026-08-11T10:00:00-07:00",
        "acquisition_method": "offline-read-only-image",
        "custody_reference": "custody:example-not-production-evidence",
        "quarantine": True,
        "append_only": True,
        "network_contact": False,
        "raw_artifacts": [
            {"artifact_id": "hindsight-export", "path": "raw/hindsight.jsonl", "sha256": "sha256:" + "1" * 64, "size_bytes": 42},
        ],
        "records": [
            {
                "record_id": "event-1",
                "source_artifact_id": "hindsight-export",
                "source_locator": "line:1",
                "normalized_path": "normalized/events/event-1.json",
                "sha256": "sha256:" + "2" * 64,
                "classification": "operational-event",
                "disposition": "historical-index",
                "historical": True,
                "current_authority": False,
                "executable": False,
                "review_status": "unreviewed",
                "conflicts_with": [],
            },
        ],
    }
    package["package_id"] = package_identity(package)
    return package


def test_valid_package_is_append_only_historical_and_non_authoritative():
    package = validate_satellite_evidence_package(_package())
    assert package["quarantine"] is True
    assert package["records"][0]["historical"] is True
    assert package["records"][0]["current_authority"] is False


@pytest.mark.parametrize(
    ("path", "value", "match"),
    [
        (("source_host",), "tgw-prod", "source host"),
        (("quarantine",), False, "quarantined"),
        (("append_only",), False, "append-only"),
        (("network_contact",), True, "network contact"),
        (("raw_artifacts", 0, "path"), "../escape", "contained"),
        (("records", 0, "current_authority"), True, "authority"),
        (("records", 0, "historical"), False, "historical"),
        (("records", 0, "executable"), True, "executable"),
    ],
)
def test_package_fails_closed_on_identity_quarantine_or_authority_drift(path, value, match):
    package = deepcopy(_package())
    target = package
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    package["package_id"] = package_identity(package)
    with pytest.raises(SatelliteEvidenceError, match=match):
        validate_satellite_evidence_package(package)


def test_secret_is_identifier_only_and_executable_instruction_never_imports():
    package = _package()
    package["records"] = [
        {
            "record_id": "secret-1", "source_artifact_id": "hindsight-export",
            "source_locator": "row:credential-id", "normalized_path": None,
            "sha256": "sha256:" + "3" * 64, "classification": "secret",
            "disposition": "restricted-rotate-revoke", "historical": True,
            "current_authority": False, "executable": False,
            "review_status": "unreviewed", "conflicts_with": [],
        },
        {
            "record_id": "prompt-1", "source_artifact_id": "hindsight-export",
            "source_locator": "line:9", "normalized_path": "normalized/quarantine/prompt-1.json",
            "sha256": "sha256:" + "4" * 64, "classification": "executable-instruction",
            "disposition": "quarantine-never-import", "historical": True,
            "current_authority": False, "executable": False,
            "review_status": "unreviewed", "conflicts_with": [],
        },
    ]
    package["package_id"] = package_identity(package)
    validate_satellite_evidence_package(package)

    bad = deepcopy(package)
    bad["records"][0]["normalized_path"] = "normalized/secrets/token.txt"
    bad["package_id"] = package_identity(bad)
    with pytest.raises(SatelliteEvidenceError, match="secret material"):
        validate_satellite_evidence_package(bad)


def test_conflicts_must_reference_records_in_same_package():
    package = _package()
    package["records"][0]["conflicts_with"] = ["missing"]
    package["package_id"] = package_identity(package)
    with pytest.raises(SatelliteEvidenceError, match="conflict target"):
        validate_satellite_evidence_package(package)
