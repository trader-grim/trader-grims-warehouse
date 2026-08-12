import subprocess
from pathlib import Path

import pytest

from tgw.code_graph import CodeGraphError, CodeGraphService, build_snapshot, service_call


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


def test_capability_truthfully_marks_partial_and_unavailable_stack(tmp_path: Path):
    capabilities = build_snapshot(_fixture(tmp_path))["capabilities"]
    assert capabilities["references"].startswith("partial:")
    assert capabilities["invariants"].startswith("unavailable:")
    assert capabilities["runtime_traces"].startswith("unavailable:")


def test_bounded_pure_service_query(tmp_path: Path):
    graph = build_snapshot(_fixture(tmp_path))
    response = service_call(graph, {"operation": "symbols", "query": "helper", "limit": 1})
    assert response["result"][0]["name"] == "helper"
    assert CodeGraphService(graph).query("status")["result"]["freshness_hash"] == graph["freshness_hash"]
    with pytest.raises(CodeGraphError, match="limit"):
        service_call(graph, {"operation": "symbols", "limit": 101})
    with pytest.raises(CodeGraphError, match="unsupported"):
        service_call(graph, {"operation": "invariants"})
