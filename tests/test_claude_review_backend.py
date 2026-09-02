from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tgw.claude_review_backend import ClaudeReviewBackendError, health, run


def executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def test_health_requires_dedicated_executable(tmp_path):
    claude = executable(tmp_path / "claude")

    observed = health(claude_bin=claude)

    assert observed["available"] is True
    assert observed["executable"] == str(claude)
    assert observed["reasons"] == []


def test_health_fails_closed_without_executable(tmp_path):
    observed = health(claude_bin=tmp_path / "missing-claude")

    assert observed["available"] is False
    assert observed["reasons"]


def _request(snapshot_root: Path, snapshot_hash: str = "sha256:" + "a" * 64) -> dict:
    return {
        "schema": "tgw-code-review-request/v1",
        "handoff_hash": "sha256:" + "b" * 64,
        "card_hash": "card",
        "snapshot_hash": snapshot_hash,
        "snapshot_root": str(snapshot_root),
        "output_contract": "tgw-code-review/v1",
    }


def test_run_invokes_claude_print_mode_and_parses_jsonl_result(tmp_path):
    claude = executable(tmp_path / "claude")
    request = _request(tmp_path)
    report = {
        "schema": "tgw-code-review/v1",
        "verdict": "PASS",
        "snapshot_hash": request["snapshot_hash"],
        "summary": "no findings",
        "findings": [],
    }
    observed = {}

    def invoke(command, *, cwd, input, **kwargs):
        observed["command"] = command
        observed["cwd"] = cwd
        observed["input"] = input
        out = json.dumps({"type": "result", "result": json.dumps(report)}) + "\n"
        return subprocess.CompletedProcess(command, 0, out, "")

    result = run(request, tmp_path, claude_bin=claude, invoke=invoke)

    assert result == report
    assert observed["command"][0] == str(claude)
    assert "-p" in observed["command"]
    assert observed["command"][observed["command"].index("--output-format") + 1] == "json"
    assert (
        observed["command"][observed["command"].index("--permission-mode") + 1]
        == "bypassPermissions"
    )
    assert observed["cwd"] == tmp_path
    assert request["snapshot_hash"] in observed["input"]


def test_run_fails_closed_without_executable(tmp_path):
    request = _request(tmp_path)

    with pytest.raises(ClaudeReviewBackendError, match="unavailable"):
        run(request, tmp_path, claude_bin=tmp_path / "missing-claude")


def test_run_rejects_nonzero_exit(tmp_path):
    claude = executable(tmp_path / "claude")
    request = _request(tmp_path)

    def invoke(command, **kwargs):
        return subprocess.CompletedProcess(command, 2, "", "boom")

    with pytest.raises(ClaudeReviewBackendError, match="exited 2"):
        run(request, tmp_path, claude_bin=claude, invoke=invoke)


def test_run_rejects_unparseable_report(tmp_path):
    claude = executable(tmp_path / "claude")
    request = _request(tmp_path)

    def invoke(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "garbage output\n", "")

    with pytest.raises(ClaudeReviewBackendError, match="invalid report"):
        run(request, tmp_path, claude_bin=claude, invoke=invoke)


def test_run_stamps_request_snapshot_hash_over_model_echo(tmp_path, caplog):
    claude = executable(tmp_path / "claude")
    request = _request(tmp_path)
    report = {
        "schema": "tgw-code-review/v1",
        "verdict": "PASS",
        "snapshot_hash": "sha256:" + "0" * 64,
        "summary": "no findings",
        "findings": [],
    }

    def invoke(command, **kwargs):
        out = json.dumps({"type": "result", "result": json.dumps(report)}) + "\n"
        return subprocess.CompletedProcess(command, 0, out, "")

    with caplog.at_level("WARNING"):
        result = run(request, tmp_path, claude_bin=claude, invoke=invoke)

    assert result["snapshot_hash"] == request["snapshot_hash"]
    assert result["verdict"] == "PASS"
    assert any("snapshot_hash" in message for message in caplog.messages)


def test_run_rejects_snapshot_root_mismatch(tmp_path):
    claude = executable(tmp_path / "claude")
    request = _request(tmp_path / "elsewhere")

    with pytest.raises(ClaudeReviewBackendError, match="snapshot root mismatch"):
        run(request, tmp_path, claude_bin=claude)


def _invoke_returning(report: dict):
    def invoke(command, **kwargs):
        out = json.dumps({"type": "result", "result": json.dumps(report)}) + "\n"
        return subprocess.CompletedProcess(command, 0, out, "")

    return invoke


def test_run_normalizes_noncanonical_finding_shapes(tmp_path):
    claude = executable(tmp_path / "claude")
    (tmp_path / "feature.py").write_text("x = 1\n", encoding="utf-8")
    request = _request(tmp_path)
    # description-vs-message, file-vs-path, missing line, severity alias, extra key.
    model_report = {
        "schema": "tgw-code-review/v1",
        "verdict": "PASS",  # contradicts the cited finding; derived verdict wins
        "snapshot_hash": request["snapshot_hash"],
        "summary": "one issue",
        "findings": [
            {
                "level": "warning",
                "file": "feature.py",
                "description": "unchecked return value",
                "confidence": 0.9,
            }
        ],
    }

    result = run(
        request, tmp_path, claude_bin=claude, invoke=_invoke_returning(model_report)
    )

    assert result["verdict"] == "FAIL"
    assert result["findings"] == [
        {
            "severity": "medium",
            "path": "feature.py",
            "line": 1,
            "message": "unchecked return value",
        }
    ]
    assert result["snapshot_hash"] == request["snapshot_hash"]


def test_run_derives_pass_when_verdict_missing_and_no_findings(tmp_path):
    claude = executable(tmp_path / "claude")
    request = _request(tmp_path)
    model_report = {
        "snapshot_hash": request["snapshot_hash"],
        "summary": "looks fine",
        "findings": [],
    }

    result = run(
        request, tmp_path, claude_bin=claude, invoke=_invoke_returning(model_report)
    )

    assert result["verdict"] == "PASS"
    assert result["schema"] == "tgw-code-review/v1"


def test_run_errors_with_raw_report_on_unmappable_finding(tmp_path):
    claude = executable(tmp_path / "claude")
    request = _request(tmp_path)
    model_report = {
        "verdict": "FAIL",
        "snapshot_hash": request["snapshot_hash"],
        "summary": "bad",
        "findings": [{"severity": "high", "message": "no path here"}],
    }

    with pytest.raises(ClaudeReviewBackendError) as excinfo:
        run(request, tmp_path, claude_bin=claude, invoke=_invoke_returning(model_report))

    assert "snapshot-relative path" in str(excinfo.value)
    assert excinfo.value.raw_report == model_report
    assert excinfo.value.raw_stdout and "no path here" in excinfo.value.raw_stdout


@pytest.mark.parametrize(
    ("finding", "match"),
    [
        (
            {"severity": "high", "path": "/etc/passwd", "line": 1, "message": "abs"},
            "not snapshot-relative",
        ),
        (
            {"severity": "high", "path": "../outside.py", "line": 1, "message": "esc"},
            "not snapshot-relative",
        ),
        (
            {"severity": "high", "path": "ghost.py", "line": 1, "message": "missing"},
            "absent from the snapshot",
        ),
        (
            {"severity": "high", "path": "feature.py", "line": 99, "message": "far"},
            "outside 'feature.py'",
        ),
    ],
)
def test_run_errors_with_raw_report_on_out_of_snapshot_finding(tmp_path, finding, match):
    claude = executable(tmp_path / "claude")
    (tmp_path / "feature.py").write_text("x = 1\n", encoding="utf-8")
    request = _request(tmp_path)
    model_report = {
        "schema": "tgw-code-review/v1",
        "verdict": "FAIL",
        "snapshot_hash": request["snapshot_hash"],
        "summary": "bad",
        "findings": [finding],
    }

    with pytest.raises(ClaudeReviewBackendError, match=match) as excinfo:
        run(request, tmp_path, claude_bin=claude, invoke=_invoke_returning(model_report))

    assert excinfo.value.raw_report == model_report
    assert excinfo.value.raw_stdout and "result" in excinfo.value.raw_stdout


def test_run_errors_on_fail_verdict_without_findings(tmp_path):
    claude = executable(tmp_path / "claude")
    request = _request(tmp_path)
    model_report = {
        "schema": "tgw-code-review/v1",
        "verdict": "FAIL",
        "snapshot_hash": request["snapshot_hash"],
        "summary": "something is wrong but no line",
        "findings": [],
    }

    with pytest.raises(ClaudeReviewBackendError, match="FAIL with no findings") as excinfo:
        run(request, tmp_path, claude_bin=claude, invoke=_invoke_returning(model_report))

    assert excinfo.value.raw_report == model_report


def test_run_normalizes_aliased_top_level_findings_and_verdict(tmp_path):
    claude = executable(tmp_path / "claude")
    (tmp_path / "feature.py").write_text("x = 1\n", encoding="utf-8")
    request = _request(tmp_path)
    # 'status'/'defects' instead of 'verdict'/'findings' -- the dialect that
    # was silently normalized to PASS/0-findings before.
    model_report = {
        "schema": "tgw-code-review/v1",
        "status": "FAIL",
        "snapshot_hash": request["snapshot_hash"],
        "summary": "buffer overflow in feature.py",
        "defects": [
            {"severity": "high", "file": "feature.py", "line": 1, "message": "oob write"}
        ],
    }

    result = run(
        request, tmp_path, claude_bin=claude, invoke=_invoke_returning(model_report)
    )

    assert result["verdict"] == "FAIL"
    assert result["findings"] == [
        {"severity": "high", "path": "feature.py", "line": 1, "message": "oob write"}
    ]


def test_run_errors_when_neither_verdict_nor_findings_recognizable(tmp_path):
    claude = executable(tmp_path / "claude")
    request = _request(tmp_path)
    # No findings array under any alias, no recognizable verdict token.
    model_report = {
        "schema": "tgw-code-review/v1",
        "snapshot_hash": request["snapshot_hash"],
        "summary": "everything looks fine to me",
    }

    with pytest.raises(ClaudeReviewBackendError, match="neither a recognizable") as excinfo:
        run(request, tmp_path, claude_bin=claude, invoke=_invoke_returning(model_report))

    assert excinfo.value.raw_report == model_report
    assert excinfo.value.raw_stdout


def test_run_errors_on_fail_status_alias_without_findings(tmp_path):
    claude = executable(tmp_path / "claude")
    request = _request(tmp_path)
    model_report = {
        "schema": "tgw-code-review/v1",
        "status": "rejected",
        "snapshot_hash": request["snapshot_hash"],
        "summary": "bad but uncited",
        "defects": [],
    }

    with pytest.raises(ClaudeReviewBackendError, match="FAIL with no findings") as excinfo:
        run(request, tmp_path, claude_bin=claude, invoke=_invoke_returning(model_report))

    assert excinfo.value.raw_report == model_report


def test_run_accepts_recognizable_pass_verdict_alias_without_findings_array(tmp_path):
    claude = executable(tmp_path / "claude")
    request = _request(tmp_path)
    model_report = {
        "schema": "tgw-code-review/v1",
        "outcome": "clean",
        "snapshot_hash": request["snapshot_hash"],
        "summary": "no material findings",
    }

    result = run(
        request, tmp_path, claude_bin=claude, invoke=_invoke_returning(model_report)
    )

    assert result["verdict"] == "PASS"
    assert result["findings"] == []


def test_run_attaches_stdout_on_unparseable_report(tmp_path):
    claude = executable(tmp_path / "claude")
    request = _request(tmp_path)

    def invoke(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "not json at all\n", "")

    with pytest.raises(ClaudeReviewBackendError, match="invalid report") as excinfo:
        run(request, tmp_path, claude_bin=claude, invoke=invoke)

    assert excinfo.value.raw_stdout == "not json at all\n"
