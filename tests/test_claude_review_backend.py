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
