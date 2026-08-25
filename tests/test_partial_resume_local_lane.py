from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tgw.development.partial_resume import (
    append_attempt,
    classify,
    history,
    make_attempt,
    preservation_manifest,
    source_fingerprint,
)


def _repo(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "todo-1752-plan-test"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "base").write_bytes(b"base\n")
    subprocess.run(["git", "add", "base"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qm", "base"], cwd=root, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True).strip()
    return root, head, tree


def _binding(root: Path, head: str, tree: str, job: str = "job-1", count: int = 1) -> dict:
    return {
        "job_id": job,
        "attempt_count": count,
        "todo_id": 1752,
        "plan_commit": "a" * 40,
        "solution_hash": "sha256:" + "b" * 64,
        "source_commit": head,
        "source_tree": tree,
        "actor": "codex",
        "worktree": str(root),
        "treatment_id": "codex-implement",
        "treatment_version": "1",
    }


def test_partial_then_failure_remains_resumable_and_append_only(tmp_path: Path) -> None:
    root, head, tree = _repo(tmp_path)
    (root / "partial.py").write_bytes(b"PARTIAL = 1\n")
    first = make_attempt(_binding(root, head, tree), root, outcome="partial")
    first_path = append_attempt(root, first)
    before = first_path.read_bytes()
    second = make_attempt(_binding(root, head, tree, "job-2", 2), root, outcome="failed", predecessor=first["attempt_hash"])
    append_attempt(root, second)
    state = classify(root, {**_binding(root, head, tree), "job_id": None, "attempt_count": None})
    assert state["state"] == "RESUMABLE_PARTIAL"
    assert state["resume_of"] == first["attempt_hash"]
    assert state["predecessor"] == second["attempt_hash"]
    assert first_path.read_bytes() == before
    assert len(history(root)) == 2


def test_fingerprint_covers_index_binary_mode_rename_and_symlinks(tmp_path: Path) -> None:
    root, _head, _tree = _repo(tmp_path)
    (root / "base").write_bytes(b"\x00\xffchanged")
    subprocess.run(["git", "add", "base"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qm", "binary"], cwd=root, check=True)
    subprocess.run(["git", "mv", "base", "renamed"], cwd=root, check=True)
    os.chmod(root / "renamed", 0o755)
    os.symlink("missing-target", root / "dangling")
    state = source_fingerprint(root)
    assert state["status_nul_b64"] and state["index_delta_b64"]
    assert state["worktree_binary_delta_b64"] is not None
    assert any(item["type"] == "symlink" and item["target"] == "missing-target" for item in state["nodes"])
    assert any(item["mode"] == "0755" for item in state["nodes"] if item["type"] == "file")
    assert any("R" in item["xy"] and item.get("original_path") for item in state["status_entries"])


def test_tamper_refuses_resume_and_writes_bound_preservation(tmp_path: Path) -> None:
    root, head, tree = _repo(tmp_path)
    (root / "partial.py").write_text("one\n")
    binding = _binding(root, head, tree)
    append_attempt(root, make_attempt(binding, root, outcome="partial"))
    (root / "partial.py").write_text("tampered\n")
    state = classify(root, {**binding, "job_id": None, "attempt_count": None})
    assert state["state"] == "UNSAFE_DIRTY"
    manifest = preservation_manifest(root, state, binding)
    assert manifest.is_file() and (root / "partial.py").read_text() == "tampered\n"


def test_runner_requires_exact_resume_hash_and_fingerprint(tmp_path: Path) -> None:
    from tgw.workers import codex_implement

    root, head, tree = _repo(tmp_path)
    (root / "partial.py").write_text("one\n")
    binding = _binding(root, head, tree)
    attempt = make_attempt(binding, root, outcome="partial")
    append_attempt(root, attempt)
    job = {
        "treatment_id": "codex-implement",
        "treatment_version": "1",
        "todo_id": 1752,
        "todo_agent": "codex",
        "job_id": "job-2",
        "attempt_count": 2,
        "plan_binding": {"plan_commit": "a" * 40, "solution_hash": "sha256:" + "b" * 64, "source_commit": head},
        "task_spec": {"schema": "coding-task/v1", "todo_id": 1752, "agent": "codex", "body": "continue"},
        "resume_of": "sha256:wrong",
        "resume_fingerprint": attempt["fingerprint"],
    }
    with pytest.raises(Exception, match="fingerprint are exact"):
        codex_implement._run_with_lease(job, root, invoke=lambda *a, **k: None)


def test_runner_accepts_exact_resume_preserves_bytes_and_excludes_evidence(tmp_path: Path, monkeypatch) -> None:
    from tgw.workers import codex_implement

    root, head, tree = _repo(tmp_path)
    partial = root / "partial.py"
    partial.write_text("preserve = True\n")
    binding = _binding(root, head, tree)
    attempt = make_attempt(binding, root, outcome="partial")
    append_attempt(root, attempt)
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    monkeypatch.setattr(codex_implement, "_codex_auth_path", lambda: auth)
    monkeypatch.setattr(codex_implement, "_codex_binary", lambda: "/bin/true")
    monkeypatch.setattr(codex_implement, "_write_isolated_config", lambda _home: None)
    job = {
        "treatment_id": "codex-implement",
        "treatment_version": "1",
        "todo_id": 1752,
        "todo_agent": "codex",
        "job_id": "job-2",
        "attempt_count": 2,
        "plan_binding": {"plan_commit": "a" * 40, "solution_hash": "sha256:" + "b" * 64, "source_commit": head},
        "task_spec": {"schema": "coding-task/v1", "todo_id": 1752, "agent": "codex", "body": "continue"},
        "resume_of": attempt["attempt_hash"],
        "resume_fingerprint": attempt["fingerprint"],
    }

    def invoke(command, **kwargs):
        assert "exact bounded continuation" in kwargs["input"]
        assert partial.read_text() == "preserve = True\n"
        (root / "finished.py").write_text("finished = True\n")
        output = Path(command[command.index("-o") + 1])
        output.write_text(json.dumps({"status": "implemented", "summary": "done", "tests": ["offline"]}))
        return subprocess.CompletedProcess(command, 0, "", "")

    result = codex_implement._run_with_lease(job, root, invoke=invoke)
    assert result["outcome"] == "satisfied"
    assert partial.read_text() == "preserve = True\n"
    assert subprocess.check_output(["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=root, text=True).splitlines() == ["base", "finished.py", "partial.py"]


def test_local_coding_worker_persists_partial_before_compatibility_and_failure(tmp_path: Path, monkeypatch) -> None:
    from tgw.workers.coding import CodingWorker

    root, head, _tree = _repo(tmp_path)
    plan_binding = {"plan_commit": "a" * 40, "solution_hash": "sha256:" + "b" * 64, "source_commit": head, "worktree": str(root), "worktree_identity": {}}
    payload = {
        "treatment_id": "codex-implement",
        "treatment_version": "1",
        "todo_id": 1752,
        "todo_agent": "codex",
        "graph_id": "graph",
        "object_generation": "generation",
        "worktree": str(root),
        "object_id": str(root),
        "plan_binding": plan_binding,
    }
    config = {"coding": {"worktree_root": str(root.parent), "repository_root": str(root)}}

    def partial(_treatment, _payload, _worktree):
        (root / "partial.py").write_text("partial = True\n")
        return {"outcome": "partial", "established_conditions": [], "artifacts": [{"kind": "crash"}]}

    worker = CodingWorker("codex-implement", config, launcher=partial)
    monkeypatch.setattr(worker, "_validated_plan_binding", lambda _payload, _worktree: plan_binding)
    with pytest.raises(Exception, match="reported partial"):
        worker.handle({"job_id": "job-1", "attempt_count": 1, "payload_json": payload})
    fixed = (root / "implementation-receipt.json").read_bytes()
    state = classify(root, {**_binding(root, head, subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True).strip()), "job_id": None, "attempt_count": None})

    resumed = {**payload, "resume_of": state["resume_of"], "resume_fingerprint": state["fingerprint"]}
    failing = CodingWorker("codex-implement", config, launcher=lambda *_args: (_ for _ in ()).throw(RuntimeError("later crash")))
    monkeypatch.setattr(failing, "_validated_plan_binding", lambda _payload, _worktree: plan_binding)
    with pytest.raises(Exception, match="later crash"):
        failing.handle({"job_id": "job-2", "attempt_count": 2, "payload_json": resumed})
    assert (root / "implementation-receipt.json").read_bytes() == fixed
    assert [item["outcome"] for item in history(root)] == ["partial", "failed"]


def test_exact_1747_migration_uses_copy_and_binds_both_jobs(tmp_path: Path, monkeypatch) -> None:
    from tgw.development import partial_resume

    root, head, tree = _repo(tmp_path)
    for relative in ("src/tgw/coding_cli.py", "src/tgw/pp_workflow_reconcile.py", "tests/test_pp_workflow_reconcile.py"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative + "\n")
    (root / "implementation-receipt.json").write_text('{"outcome":"partial"}\n')
    monkeypatch.setattr(partial_resume, "LEGACY_1747", root.resolve())
    binding = {**_binding(root, head, tree), "todo_id": 1747}

    def job(job_id: str, outcome: str, count: int) -> dict:
        return {
            "job_id": job_id,
            "outcome": outcome,
            "payload": {
                "todo_id": 1747,
                "todo_agent": "codex",
                "worktree": str(root),
                "treatment_id": "codex-implement",
                "attempt_count": count,
                "plan_binding": {key: binding[key] for key in ("plan_commit", "solution_hash", "source_commit")},
            },
        }

    jobs = [job("dfdfd643-312e-46ef-a33c-1542340e9b9c", "partial", 1), job("2b1f9f04-a09f-489e-aade-f21ab1e4aaa9", "failed", 2)]
    manifest = partial_resume.migrate_todo_1747(root, binding, jobs)
    assert manifest.is_file()
    assert [item["job_id"] for item in history(root)] == [jobs[0]["job_id"], jobs[1]["job_id"]]
    assert (root / "implementation-receipt.json").read_text() == '{"outcome":"partial"}\n'
    jobs[1]["payload"]["todo_agent"] = "claude"
    with pytest.raises(Exception):
        partial_resume.migrate_todo_1747(root, binding, jobs)
