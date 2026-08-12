"""Literal replayability and independent closure tests for freeze evidence."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


def _verifier():
    spec = importlib.util.spec_from_file_location(
        "verify_freeze_evidence", HERE / "verify_freeze_evidence.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_closed_structure_and_filesystem_metadata():
    result = _verifier().verify(REPO, Path("/opt/TGW/evidence/codex/sha256"))
    assert result["status"] == "PASS"
    assert result["gate_count"] >= 17


def test_literal_argv_and_environment_are_replayable_without_shell_parsing():
    catalog = json.loads(
        (REPO / "agent-services/catalogs/f3cefe5-closed-freeze-evidence.json").read_text()
    )
    for ref in catalog["gate_records"].values():
        record = json.loads((REPO / ref["path"]).read_text())
        assert record["environment"]["clear_inherited"] is True
        assert record["argv"][0] == record["executable"]["path"]
        assert record["executable"]["path"].startswith("/")
        assert record["cwd"].startswith("/")
        assert all(isinstance(item, str) and "\x00" not in item for item in record["argv"])
        assert set(record["environment"]["values"]) == {
            "HOME", "LC_ALL", "NO_COLOR", "PATH", "PYTHONDONTWRITEBYTECODE",
            "PYTHONHASHSEED", "PYTHONPATH", "TMPDIR",
        } or set(record["environment"]["values"]) == {
            "HOME", "LC_ALL", "NO_COLOR", "PATH", "PYTHONDONTWRITEBYTECODE",
            "PYTHONHASHSEED", "PYTHONPATH", "TMPDIR", "TGW_RENDER_TEST_PARSE_ONLY",
            "ASAN_OPTIONS", "UBSAN_OPTIONS",
        }


def test_closed_documents_contain_no_private_key_or_grant_material():
    paths = [
        REPO / "agent-services/catalogs/f3cefe5-closed-freeze-evidence.json",
        REPO / "agent-services/candidates/integrated-f3cefe5-CLOSED-FREEZE.json",
        REPO / "agent-services/candidates/platform-bootstrap-prerequisite-f3cefe5-CLOSED-NOT-EXECUTABLE.json",
        REPO / "agent-services/receipts/source-audit-f3cefe5-closed-freeze.json",
        REPO / "agent-services/receipts/f3cefe5-closed-store-readiness.json",
        *sorted((HERE / "records").glob("*.json")),
    ]
    forbidden = (b"BEGIN PRIVATE KEY", b"BEGIN OPENSSH PRIVATE KEY", b'"grant": {', b'"request": {')
    for path in paths:
        raw = path.read_bytes()
        assert not any(marker in raw for marker in forbidden), path
