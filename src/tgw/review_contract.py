"""Dependency-free validation for provider-neutral code-review reports."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class ReviewRunnerError(ValueError):
    """A review execution or report violated its governed contract."""


def validate_review_report(
    report: Any, expected_snapshot: str, snapshot_root: Path
) -> dict[str, Any]:
    if not isinstance(report, dict) or set(report) != {
        "schema",
        "verdict",
        "snapshot_hash",
        "summary",
        "findings",
    }:
        raise ReviewRunnerError("review report fields are invalid")
    if report["schema"] != "tgw-code-review/v1" or report["verdict"] not in {
        "PASS",
        "FAIL",
    }:
        raise ReviewRunnerError("review report contract is invalid")
    if report["snapshot_hash"] != expected_snapshot:
        raise ReviewRunnerError("review report snapshot binding mismatch")
    if not isinstance(report["summary"], str) or not report["summary"].strip():
        raise ReviewRunnerError("review report summary is required")
    findings = report["findings"]
    if not isinstance(findings, list):
        raise ReviewRunnerError("review findings must be a list")
    for finding in findings:
        if not isinstance(finding, dict) or set(finding) != {
            "severity",
            "path",
            "line",
            "message",
        }:
            raise ReviewRunnerError("review finding fields are invalid")
        if finding["severity"] not in {"critical", "high", "medium", "low"}:
            raise ReviewRunnerError("review finding severity is invalid")
        relative = Path(str(finding["path"]))
        if (
            not isinstance(finding["path"], str)
            or not finding["path"]
            or relative.is_absolute()
            or ".." in relative.parts
        ):
            raise ReviewRunnerError("review finding path must be snapshot-relative")
        if not isinstance(finding["line"], int) or finding["line"] < 1:
            raise ReviewRunnerError("review finding line is invalid")
        if not isinstance(finding["message"], str) or not finding["message"].strip():
            raise ReviewRunnerError("review finding message is required")
        source = snapshot_root / relative
        if not source.is_file():
            raise ReviewRunnerError("review finding path is absent from the snapshot")
        if finding["line"] > len(
            source.read_text(encoding="utf-8", errors="replace").splitlines()
        ):
            raise ReviewRunnerError("review finding line is outside the snapshot source")
    if report["verdict"] == "PASS" and findings:
        raise ReviewRunnerError("passing review cannot contain unresolved findings")
    if report["verdict"] == "FAIL" and not findings:
        raise ReviewRunnerError("failed review must identify at least one finding")
    return report
