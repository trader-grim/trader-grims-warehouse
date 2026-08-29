from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from tgw.development import coding_review


def _hex(n: int) -> str:
    return hashlib.sha256(str(n).encode()).hexdigest()


def _repo(tmp_path: Path) -> tuple[Path, str, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()
    (repo / "feature.py").write_text("X = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "candidate"], cwd=repo, check=True, capture_output=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()
    return repo, base, commit, tree


def _payload(base: str, commit: str, tree: str, todo_id: int = 1923) -> dict:
    root = "coding:" + "0" * 64
    return {
        "todo_id": todo_id,
        "job_id": "job-1",
        "coding_lifecycle": {
            "root_id": root,
            "binding_hash": "sha256:" + _hex(1),
            "job_binding_hash": "sha256:" + _hex(2),
            "card_idempotency_key": "card",
        },
        "coding_candidate": {
            "commit": commit,
            "tree": tree,
            "candidate_binding_hash": "sha256:" + _hex(3),
            "root_id": root,
            "job_binding_hash": "sha256:" + _hex(2),
        },
        "plan_binding": {
            "source_commit": base,
            "plan_commit": "plan",
            "solution_hash": "sol",
            "closure_hash": "closure",
        },
        "task_spec": {
            "schema": "coding-task/v1",
            "todo_id": todo_id,
            "agent": "codex",
            "body": "implement the bounded feature",
        },
    }


def _backend(commit: str, tree: str):
    def fake(request: dict, worktree: Path) -> dict:
        assert request["snapshot_hash"] == coding_review._candidate_snapshot_hash(commit, tree)
        return {
            "schema": "tgw-code-review/v1",
            "verdict": "PASS",
            "snapshot_hash": request["snapshot_hash"],
            "summary": "no findings",
            "findings": [],
        }

    return fake


def test_review_ignores_workflow_evidence_files(tmp_path):
    repo, base, commit, tree = _repo(tmp_path)
    (repo / "implementation-receipt.json").write_text("{}", encoding="utf-8")
    (repo / "controller-harness-receipt.json").write_text("{}", encoding="utf-8")
    (repo / "review-receipt.json").write_text("{}", encoding="utf-8")
    history = repo / ".tgw-coding-history" / "implementation"
    history.mkdir(parents=True)
    (history / "state.json").write_text("{}", encoding="utf-8")
    preservation = repo / ".tgw-coding-preservation"
    preservation.mkdir()
    (preservation / "manifest.json").write_text("{}", encoding="utf-8")

    result = coding_review.run_local_review(
        _payload(base, commit, tree), repo, semantic_backend=_backend(commit, tree)
    )
    assert result["outcome"] == "satisfied"
    assert result["established_conditions"] == ["reviewed"]


def test_review_still_rejects_real_source_mutation(tmp_path):
    repo, base, commit, tree = _repo(tmp_path)
    (repo / "extra.py").write_text("X = 2\n", encoding="utf-8")
    with pytest.raises(coding_review.ReviewRunnerError, match="mutated the exact candidate"):
        coding_review.run_local_review(
            _payload(base, commit, tree), repo, semantic_backend=_backend(commit, tree)
        )


def test_manual_review_executor_handshake(tmp_path, monkeypatch):
    import threading
    import time as _time
    repo, base, commit, tree = _repo(tmp_path)
    monkeypatch.setenv("TGW_REVIEW_EXECUTOR", "manual")
    monkeypatch.setattr(coding_review, "_review_poll_seconds", lambda: 0.01)
    monkeypatch.setattr(coding_review, "_review_timeout_seconds", lambda: 10)
    holder: dict[str, object] = {}

    def worker() -> None:
        holder["result"] = coding_review.run_local_review(_payload(base, commit, tree), repo)

    thread = threading.Thread(target=worker)
    thread.start()
    card = repo / ".tgw-coding-history/implementation/review-manual/request.json"
    deadline = _time.monotonic() + 5
    while not card.is_file() and _time.monotonic() < deadline:
        _time.sleep(0.01)
    assert card.is_file()
    payload = json.loads(card.read_text(encoding="utf-8"))
    assert payload["schema"] == "tgw-manual-review-request/v1"
    snapshot = coding_review._candidate_snapshot_hash(commit, tree)
    (card.parent / "done.json").write_text(
        json.dumps({"schema": "tgw-code-review/v1", "verdict": "PASS",
                    "snapshot_hash": snapshot, "summary": "clean", "findings": []}),
        encoding="utf-8",
    )
    thread.join(10)
    assert not thread.is_alive()
    result = holder["result"]
    assert result["outcome"] == "satisfied"
    assert result["established_conditions"] == ["reviewed"]


def test_manual_review_executor_invalid_report_fails(tmp_path, monkeypatch):
    repo, base, commit, tree = _repo(tmp_path)
    monkeypatch.setenv("TGW_REVIEW_EXECUTOR", "manual")
    monkeypatch.setattr(coding_review, "_review_poll_seconds", lambda: 0.01)
    monkeypatch.setattr(coding_review, "_review_timeout_seconds", lambda: 10)
    done = repo / ".tgw-coding-history/implementation/review-manual/done.json"
    done.parent.mkdir(parents=True)
    done.write_text("not json", encoding="utf-8")
    with pytest.raises(coding_review.ReviewRunnerError, match="backend failed"):
        coding_review.run_local_review(_payload(base, commit, tree), repo)


def test_manual_review_executor_times_out(tmp_path, monkeypatch):
    repo, base, commit, tree = _repo(tmp_path)
    monkeypatch.setenv("TGW_REVIEW_EXECUTOR", "manual")
    monkeypatch.setattr(coding_review, "_review_poll_seconds", lambda: 0.01)
    monkeypatch.setattr(coding_review, "_review_timeout_seconds", lambda: 0.3)
    with pytest.raises(coding_review.ReviewRunnerError, match="timed out"):
        coding_review.run_local_review(_payload(base, commit, tree), repo)
