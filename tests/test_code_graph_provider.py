import subprocess
from pathlib import Path

import pytest

from tgw.code_graph import (
    AgentRunTraceReader,
    CodeGraphError,
    CodeGraphService,
    build_snapshot,
    service_call,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, text=True, capture_output=True).stdout.strip()


def _fixture(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "Fixture")
    (repo / "pkg").mkdir()
    (repo / "pkg" / "__init__.py").write_text("from .core import Worker\n")
    (repo / "pkg" / "core.py").write_text(
        "import json\nfrom pathlib import Path\n\n"
        "class Worker:\n    def run(self):\n        return helper()\n\n"
        "def helper():\n    return json.dumps({'path': str(Path('.'))})\n"
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_invariant_c12_boundary.py").write_text(
        '"""Invariant C12 detector."""\n\ndef test_c12():\n    assert True\n'
    )
    receipts = repo / "agent-services" / "receipts"
    receipts.mkdir(parents=True)
    (receipts / "focused.json").write_text(
        '{"schema":"fixture-receipt/v1","source_commit":"abc","status":"passed"}\n'
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")
    return repo


def test_snapshot_is_exact_commit_tree_and_deterministic(tmp_path: Path):
    repo = _fixture(tmp_path)
    first = build_snapshot(repo)
    (repo / "pkg" / "core.py").write_text("dirty and invalid python !!!")
    second = build_snapshot(repo)
    assert first == second
    assert first["commit"] == _git(repo, "rev-parse", "HEAD")
    assert first["tree"] == _git(repo, "rev-parse", "HEAD^{tree}")
    assert first["freshness_hash"].startswith("sha256:")


def test_python_graph_has_symbols_imports_dependencies_and_local_references(tmp_path: Path):
    graph = build_snapshot(_fixture(tmp_path))
    assert {item["qualname"] for item in graph["symbols"]} >= {"Worker", "Worker.run", "helper"}
    assert {item["target"] for item in graph["imports"]} >= {"json", "pathlib"}
    helper = next(item for item in graph["references"] if item["name"] == "helper")
    assert helper["resolution"] == "module-local-exact"
    assert graph["dependencies"] == graph["imports"]


def test_invariant_and_receipt_evidence_is_typed_and_snapshot_bound(tmp_path: Path):
    graph = build_snapshot(_fixture(tmp_path))
    capabilities = graph["capabilities"]
    assert capabilities["references"].startswith("partial:")
    assert capabilities["invariants"].startswith("partial:")
    assert capabilities["runtime_traces"].startswith("unavailable:")
    c12 = next(item for item in graph["invariants"] if item["id"] == "C12")
    assert c12["status"] == "detector-test-present"
    assert c12["binding"] == {"commit": graph["commit"], "tree": graph["tree"]}
    assert graph["execution_receipts"][0]["parse_status"] == "valid-json"
    assert graph["execution_receipts"][0]["binding"]["snapshot_commit"] == graph["commit"]


def test_bounded_pure_service_query(tmp_path: Path):
    graph = build_snapshot(_fixture(tmp_path))
    response = service_call(graph, {"operation": "symbols", "query": "helper", "limit": 1})
    assert response["result"][0]["name"] == "helper"
    assert CodeGraphService(graph).query("status")["result"]["freshness_hash"] == graph["freshness_hash"]
    with pytest.raises(CodeGraphError, match="limit"):
        service_call(graph, {"operation": "symbols", "limit": 101})
    assert service_call(graph, {"operation": "invariants", "query": "C12"})["result"][0]["id"] == "C12"


def test_runtime_trace_reader_is_injected_bounded_and_snapshot_bound(tmp_path: Path):
    graph = build_snapshot(_fixture(tmp_path))
    unavailable = CodeGraphService(graph).query("traces", limit=1)["result"]
    assert unavailable == {"status": "unavailable", "reason": "no-trace-reader-injected", "objects": []}

    calls = []

    def load(limit):
        calls.append(limit)
        return [{
            "run_id": "run-1", "parent_run_id": None, "agent_type": "codex",
            "status": "completed", "transcript_path": "/bounded/ref",
        }]

    service = CodeGraphService(graph, AgentRunTraceReader(load))
    result = service.query("traces", limit=1)["result"]
    assert calls == [1]
    assert result["objects"][0]["schema"] == "tgw-code-graph-execution-trace/v1"
    assert result["objects"][0]["code_snapshot_hash"] == graph["freshness_hash"]
    assert service.query("status")["result"]["capabilities"]["runtime_traces"].startswith("available:")
