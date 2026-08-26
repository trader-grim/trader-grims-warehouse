from __future__ import annotations

import hashlib
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


def test_fingerprint_excludes_every_workflow_receipt(tmp_path: Path) -> None:
    from tgw.development.partial_resume import RECEIPT_FILES

    root, _head, _tree = _repo(tmp_path)
    before = source_fingerprint(root)
    for name in RECEIPT_FILES:
        (root / name).write_text(f"{name}\n")
    after = source_fingerprint(root)

    assert after["fingerprint"] == before["fingerprint"]
    assert after["changed_paths"] == []


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


def test_lineage_validation_requires_expected_source_tree(tmp_path: Path) -> None:
    root, head, tree = _repo(tmp_path)
    (root / "partial.py").write_text("one\n")
    binding = _binding(root, head, tree)
    append_attempt(root, make_attempt(binding, root, outcome="partial"))
    incomplete = {**binding, "job_id": None, "attempt_count": None}
    incomplete.pop("source_tree")

    state = classify(root, incomplete)

    assert state["state"] == "STALE_RECEIPT"
    assert "source_tree" in state["error"]


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
    binding = {**_binding(root, head, tree), "todo_id": 1747}
    monkeypatch.setattr(partial_resume, "LEGACY_1747", root.resolve())
    monkeypatch.setattr(partial_resume, "LEGACY_1747_SOURCE_COMMIT", head)
    monkeypatch.setattr(partial_resume, "LEGACY_1747_SOURCE_TREE", tree)
    monkeypatch.setattr(partial_resume, "LEGACY_1747_PLAN_COMMIT", binding["plan_commit"])
    monkeypatch.setattr(partial_resume, "LEGACY_1747_SOLUTION_HASH", binding["solution_hash"])
    monkeypatch.setattr(
        partial_resume,
        "LEGACY_1747_FINGERPRINT",
        partial_resume.source_fingerprint(root)["fingerprint"],
    )
    monkeypatch.setattr(
        partial_resume,
        "LEGACY_1747_RECEIPT_SHA256",
        __import__("hashlib").sha256(
            (root / "implementation-receipt.json").read_bytes()
        ).hexdigest(),
    )

    def job(job_id: str, outcome: str, count: int) -> dict:
        return {
            "job_id": job_id,
            "outcome": outcome,
            "attempt_count": count,
            "state": "dead_letter",
            "error_code": "HARD_FAILURE",
            "error_detail": f"HardFailure('coding treatment reported {outcome}')",
            "payload": {
                "todo_id": 1747,
                "todo_agent": "codex",
                "worktree": str(root),
                "object_id": str(root),
                "treatment_id": "codex-implement",
                "plan_binding": {
                    **{key: binding[key] for key in ("plan_commit", "solution_hash", "source_commit")},
                    "worktree": str(root),
                    "worktree_identity": {
                        "actor": "codex", "worktree": str(root), "head": head,
                    },
                },
            },
        }

    jobs = [job("dfdfd643-312e-46ef-a33c-1542340e9b9c", "partial", 1), job("2b1f9f04-a09f-489e-aade-f21ab1e4aaa9", "failed", 1)]
    manifest = partial_resume.migrate_todo_1747(root, binding, jobs)
    assert manifest.is_file()
    assert [item["job_id"] for item in history(root)] == [jobs[0]["job_id"], jobs[1]["job_id"]]
    assert (root / "implementation-receipt.json").read_text() == '{"outcome":"partial"}\n'
    assert partial_resume.migrate_todo_1747(root, binding, jobs) == manifest
    missing_count = json.loads(json.dumps(jobs))
    missing_count[1].pop("attempt_count")
    with pytest.raises(Exception):
        partial_resume.migrate_todo_1747(root, binding, missing_count)
    with pytest.raises(Exception):
        partial_resume.migrate_todo_1747(root, binding, [jobs[0], jobs[0]])
    with pytest.raises(Exception):
        partial_resume.migrate_todo_1747(
            root, {**binding, "source_tree": "f" * 40}, jobs
        )
    jobs[1]["payload"]["todo_agent"] = "claude"
    with pytest.raises(Exception):
        partial_resume.migrate_todo_1747(root, binding, jobs)


def test_1747_manifest_survives_closed_receipt_and_rejects_tampering(tmp_path: Path, monkeypatch) -> None:
    """Exercise the real filesystem/Git migration, closure, and repeat transition."""
    from tgw.development import partial_resume

    root, head, tree = _repo(tmp_path)
    changed = [
        "src/tgw/coding_cli.py",
        "src/tgw/pp_workflow_reconcile.py",
        "tests/test_pp_workflow_reconcile.py",
    ]
    for relative in changed:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative + "\n")
    receipt = root / "implementation-receipt.json"
    receipt.write_bytes(b'{"outcome":"partial"}\n')
    binding = {**_binding(root, head, tree), "todo_id": 1747}
    monkeypatch.setattr(partial_resume, "LEGACY_1747", root.resolve())
    monkeypatch.setattr(partial_resume, "LEGACY_1747_SOURCE_COMMIT", head)
    monkeypatch.setattr(partial_resume, "LEGACY_1747_SOURCE_TREE", tree)
    monkeypatch.setattr(partial_resume, "LEGACY_1747_PLAN_COMMIT", binding["plan_commit"])
    monkeypatch.setattr(partial_resume, "LEGACY_1747_SOLUTION_HASH", binding["solution_hash"])
    monkeypatch.setattr(partial_resume, "LEGACY_1747_FINGERPRINT", partial_resume.source_fingerprint(root)["fingerprint"])
    monkeypatch.setattr(partial_resume, "LEGACY_1747_RECEIPT_SHA256", hashlib.sha256(receipt.read_bytes()).hexdigest())

    def job(job_id: str, outcome: str) -> dict:
        return {
            "job_id": job_id, "outcome": outcome, "attempt_count": 1,
            "state": "dead_letter", "error_code": "HARD_FAILURE",
            "error_detail": f"HardFailure('coding treatment reported {outcome}')",
            "payload": {
                "todo_id": 1747, "todo_agent": "codex", "worktree": str(root),
                "object_id": str(root), "treatment_id": "codex-implement",
                "plan_binding": {
                    **{key: binding[key] for key in ("plan_commit", "solution_hash", "source_commit")},
                    "worktree": str(root),
                    "worktree_identity": {"actor": "codex", "worktree": str(root), "head": head},
                },
            },
        }

    jobs = [job("dfdfd643-312e-46ef-a33c-1542340e9b9c", "partial"), job("2b1f9f04-a09f-489e-aade-f21ab1e4aaa9", "failed")]
    manifest = partial_resume.migrate_todo_1747(root, binding, jobs)
    initial = {path: path.read_bytes() for path in [manifest, *sorted((root / partial_resume.HISTORY).glob("*.json"))]}
    subprocess.run(["git", "add", *changed], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qm", "candidate"], cwd=root, check=True)
    candidate = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    candidate_tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True).strip()
    satisfied = partial_resume.make_attempt(
        {**binding, "job_id": "resume-job", "attempt_count": 1}, root,
        outcome="satisfied", predecessor=partial_resume.history(root)[-1]["attempt_hash"],
        artifacts=[{"kind": "closed_candidate", "commit": candidate, "tree": candidate_tree}],
    )
    partial_resume.append_attempt(root, satisfied)
    closed_receipt = b'{"outcome":"satisfied","candidate":"closed"}\n'
    receipt.write_bytes(closed_receipt)

    assert partial_resume.classify(root, {**binding, "job_id": None, "attempt_count": None})["state"] == "CLOSED_CANDIDATE"
    assert partial_resume.migrate_todo_1747(root, binding, jobs) == manifest
    assert receipt.read_bytes() == closed_receipt
    assert all(path.read_bytes() == content for path, content in initial.items())

    pristine = manifest.read_bytes()
    value = json.loads(pristine)
    value["source"]["nodes"] = []
    unsigned = dict(value)
    unsigned.pop("manifest_hash")
    value["manifest_hash"] = "sha256:" + hashlib.sha256(partial_resume._canonical(unsigned)).hexdigest()
    manifest.chmod(0o640)
    manifest.write_text(json.dumps(value, sort_keys=True) + "\n")
    with pytest.raises(Exception, match="manifest differs"):
        partial_resume.migrate_todo_1747(root, binding, jobs)
    manifest.write_bytes(pristine)
    history_path = sorted((root / partial_resume.HISTORY).glob("*.json"))[0]
    history_path.chmod(0o640)
    history_path.write_bytes(
        history_path.read_bytes().replace(b'"partial"', b'"failed"', 1)
    )
    with pytest.raises(Exception, match="lineage"):
        partial_resume.migrate_todo_1747(root, binding, jobs)


def test_satisfied_attempt_closes_only_its_exact_candidate(tmp_path: Path) -> None:
    root, baseline, tree = _repo(tmp_path)
    (root / "candidate.py").write_text("candidate = 1\n")
    subprocess.run(["git", "add", "candidate.py"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qm", "candidate"],
        cwd=root,
        check=True,
    )
    candidate = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    candidate_tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True).strip()
    binding = _binding(root, baseline, tree)
    attempt = make_attempt(
        binding,
        root,
        outcome="satisfied",
        artifacts=[{"kind": "closed_candidate", "commit": candidate, "tree": candidate_tree}],
    )
    append_attempt(root, attempt)
    expected = {**binding, "job_id": None, "attempt_count": None}

    assert classify(root, expected)["state"] == "CLOSED_CANDIDATE"
    (root / "candidate.py").write_text("tampered = 2\n")
    assert classify(root, expected)["state"] == "UNSAFE_DIRTY"
    subprocess.run(["git", "restore", "candidate.py"], cwd=root, check=True)
    (root / "unrelated.py").write_text("unrelated = True\n")
    subprocess.run(["git", "add", "unrelated.py"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qm", "unrelated"],
        cwd=root,
        check=True,
    )
    assert classify(root, expected)["state"] == "STALE_RECEIPT"


def test_worker_recovers_exact_closed_candidate_without_launcher(tmp_path: Path, monkeypatch) -> None:
    from tgw.workers.coding import CodingWorker

    root, baseline, tree = _repo(tmp_path)
    (root / "candidate.py").write_text("candidate = True\n")
    subprocess.run(["git", "add", "candidate.py"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qm", "candidate"],
        cwd=root,
        check=True,
    )
    candidate = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    candidate_tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True).strip()
    binding = _binding(root, baseline, tree)
    append_attempt(
        root,
        make_attempt(
            binding,
            root,
            outcome="satisfied",
            artifacts=[{"kind": "closed_candidate", "commit": candidate, "tree": candidate_tree}],
        ),
    )
    plan_binding = {
        "plan_commit": binding["plan_commit"],
        "solution_hash": binding["solution_hash"],
        "source_commit": baseline,
        "worktree": str(root),
        "worktree_identity": {},
    }
    launched = False

    def launcher(*_args):
        nonlocal launched
        launched = True
        raise AssertionError("closed candidate reran launcher")

    worker = CodingWorker(
        "codex-implement",
        {"coding": {"worktree_root": str(root.parent), "repository_root": str(root)}},
        launcher=launcher,
    )
    monkeypatch.setattr(worker, "_validated_plan_binding", lambda *_args: plan_binding)
    receipt = worker.handle(
        {
            "job_id": "stale-retry",
            "attempt_count": 2,
            "payload_json": {
                "treatment_id": "codex-implement",
                "treatment_version": "1",
                "todo_id": 1752,
                "todo_agent": "codex",
                "graph_id": "graph",
                "object_generation": "generation",
                "worktree": str(root),
                "object_id": str(root),
                "plan_binding": plan_binding,
            },
        }
    )

    assert not launched
    assert receipt["outcome"] == "satisfied"
    assert len(history(root)) == 1


def test_worker_holds_lease_through_attempt_append(tmp_path: Path, monkeypatch) -> None:
    from tgw.workers.coding import CodingWorker

    root, head, _tree = _repo(tmp_path)
    plan_binding = {
        "plan_commit": "a" * 40,
        "solution_hash": "sha256:" + "b" * 64,
        "source_commit": head,
        "worktree": str(root),
        "worktree_identity": {},
    }
    script = tmp_path / "cooperating-writer.py"
    script.write_text(
        "import pathlib,sys\n"
        "from tgw.development.worktree_lease import exclusive_worktree_lease\n"
        "try:\n"
        "  with exclusive_worktree_lease(pathlib.Path(sys.argv[1])):\n"
        "    (pathlib.Path(sys.argv[1])/'concurrent.py').write_text('bad\\n')\n"
        "except Exception:\n"
        "  raise SystemExit(17)\n"
    )

    def partial(_treatment, _payload, _worktree):
        environment = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
        blocked = subprocess.run(
            [__import__("sys").executable, str(script), str(root)],
            env=environment,
            check=False,
        )
        assert blocked.returncode == 17
        assert not (root / "concurrent.py").exists()
        (root / "partial.py").write_text("partial = True\n")
        return {"outcome": "partial", "established_conditions": [], "artifacts": []}

    worker = CodingWorker(
        "codex-implement",
        {"coding": {"worktree_root": str(root.parent), "repository_root": str(root)}},
        launcher=partial,
    )
    monkeypatch.setattr(worker, "_validated_plan_binding", lambda *_args: plan_binding)
    with pytest.raises(Exception, match="reported partial"):
        worker.handle(
            {
                "job_id": "job-1",
                "attempt_count": 1,
                "payload_json": {
                    "treatment_id": "codex-implement",
                    "treatment_version": "1",
                    "todo_id": 1752,
                    "todo_agent": "codex",
                    "graph_id": "graph",
                    "object_generation": "generation",
                    "worktree": str(root),
                    "object_id": str(root),
                    "plan_binding": plan_binding,
                },
            }
        )
    assert history(root)[0]["fingerprint"] == source_fingerprint(root)["fingerprint"]


def test_worker_rejects_satisfied_result_without_exact_closed_candidate(
    tmp_path: Path, monkeypatch
) -> None:
    from tgw.workers.coding import CodingWorker

    root, head, _tree = _repo(tmp_path)
    plan_binding = {
        "plan_commit": "a" * 40,
        "solution_hash": "sha256:" + "b" * 64,
        "source_commit": head,
        "worktree": str(root),
        "worktree_identity": {},
    }
    worker = CodingWorker(
        "codex-implement",
        {"coding": {"worktree_root": str(root.parent), "repository_root": str(root)}},
        launcher=lambda *_args: {
            "outcome": "satisfied",
            "established_conditions": ["implemented"],
            "artifacts": [],
        },
    )
    monkeypatch.setattr(worker, "_validated_plan_binding", lambda *_args: plan_binding)

    with pytest.raises(Exception, match="not the exact closed source descendant"):
        worker.handle(
            {
                "job_id": "job-1",
                "attempt_count": 1,
                "payload_json": {
                    "treatment_id": "codex-implement",
                    "treatment_version": "1",
                    "todo_id": 1752,
                    "todo_agent": "codex",
                    "graph_id": "graph",
                    "object_generation": "generation",
                    "worktree": str(root),
                    "object_id": str(root),
                    "plan_binding": plan_binding,
                },
            }
        )
    assert [item["outcome"] for item in history(root)] == ["failed"]


def test_configured_subprocess_runner_and_worker_share_one_lease(tmp_path: Path, monkeypatch) -> None:
    from tgw.workers.coding import CodingWorker

    root, head, _tree = _repo(tmp_path)
    runner = tmp_path / "lease-aware-runner.py"
    runner.write_text(
        "import fcntl,json,os,pathlib\n"
        "fd=int(os.environ['TGW_CODING_WORKTREE_LEASE_FD'])\n"
        "os.fstat(fd)\n"
        "fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
        "(pathlib.Path.cwd()/'partial.py').write_text('from_child = True\\n')\n"
        "print(json.dumps({'outcome':'partial','established_conditions':[],'artifacts':[{'kind':'child'}]}))\n"
    )
    executable = __import__("sys").executable
    plan_binding = {
        "plan_commit": "a" * 40,
        "solution_hash": "sha256:" + "b" * 64,
        "source_commit": head,
        "worktree": str(root),
        "worktree_identity": {},
    }
    worker = CodingWorker(
        "codex-implement",
        {
            "coding": {
                "worktree_root": str(root.parent),
                "repository_root": str(root),
                "commands": {"codex-implement": [executable, str(runner)]},
                "allowed_runners": [executable],
                "timeout_s": 30,
            }
        },
    )
    monkeypatch.setattr(worker, "_validated_plan_binding", lambda *_args: plan_binding)

    with pytest.raises(Exception, match="reported partial"):
        worker.handle(
            {
                "job_id": "job-1",
                "attempt_count": 1,
                "payload_json": {
                    "treatment_id": "codex-implement",
                    "treatment_version": "1",
                    "todo_id": 1752,
                    "todo_agent": "codex",
                    "graph_id": "graph",
                    "object_generation": "generation",
                    "worktree": str(root),
                    "object_id": str(root),
                    "plan_binding": plan_binding,
                },
            }
        )

    recorded = history(root)
    assert [item["outcome"] for item in recorded] == ["partial"]
    assert recorded[0]["fingerprint"] == source_fingerprint(root)["fingerprint"]


def test_owner_resume_queues_exactly_one_resume_identity(tmp_path: Path, monkeypatch) -> None:
    from tgw.development.foreman import ForemanConfig, TodoRecord, tick
    from tgw.development.plan_binding import execution_root_hash
    from tgw.workflow_kernel.contracts import RuntimeWorkGraph, TreatmentDisposition

    root, head, tree = _repo(tmp_path)
    (root / "partial.py").write_text("partial = True\n")
    attempt_binding = _binding(root, head, tree)
    attempt = make_attempt(attempt_binding, root, outcome="partial")
    append_attempt(root, attempt)
    plan_commit = attempt_binding["plan_commit"]
    execution_root = {
        "schema": "tgw-execution-root/v1",
        "kind": "todo",
        "todo_id": 1752,
    }
    execution_root["identity_hash"] = execution_root_hash(execution_root)
    plan_binding = {
        "schema": "tgw-plan-coding-todo/v1",
        "plan_commit": plan_commit,
        "solution_hash": attempt_binding["solution_hash"],
        "closure_hash": "sha256:" + "c" * 64,
        "capability": "workflow.condition-derived-convergence@1",
        "treatment_id": "establish:workflow.condition-derived-convergence@1",
        "source_commit": head,
        "idempotency_key": "sha256:" + "d" * 64,
        "worktree": str(root),
        "worktree_identity": {"worktree": str(root)},
        "execution_root": execution_root,
    }
    todo = TodoRecord(1752, "codex", 1, "continue exact partial", str(root), plan_binding)
    snapshot = type("Snapshot", (), {"generation": "generation"})()
    disposition = TreatmentDisposition("codex-implement", "1", ("implemented=false",))
    graph = RuntimeWorkGraph(
        schema_version="runtime-work-graph/v1",
        graph_id="graph",
        object_id=str(root),
        object_generation="generation",
        goal_profile_id="coding.ready_for_implementation",
        goal_profile_version="1",
        evaluator_version="foreman/v1",
        evidence_set_hash="evidence",
        condition_hash="condition",
        treatment_registry_hash="registry",
        fingerprints=(),
        satisfied_requirements=(),
        unmet_requirements=(),
        explicit_requirements=(),
        eligible_treatments=(disposition,),
        waiting_treatments=(),
        ownership_conflicts=(),
        reconciliation_gates=(),
        next_event_classes=(),
    )
    monkeypatch.setattr("tgw.development.foreman.build_coding_snapshot", lambda *_args, **_kwargs: snapshot)
    monkeypatch.setattr("tgw.development.foreman.evaluate", lambda **_kwargs: graph)
    queued = []

    def enqueue(**kwargs):
        queued.append(kwargs)
        return "resume-job"

    config = ForemanConfig(
        coding_config={
            "worktree_root": str(root.parent),
            "repository_root": str(root),
        },
        resume_bindings={
            1752: {
                "resume_of": attempt["attempt_hash"],
                "resume_fingerprint": attempt["fingerprint"],
            }
        },
    )
    first = tick(
        config,
        fetch_todos=lambda: [todo],
        check_active_fn=lambda _key: False,
        check_worktree_active_fn=lambda _path: False,
        check_terminal_fn=lambda key: any(row["dedupe_key"] == key for row in queued),
        enqueue_fn=enqueue,
    )
    second = tick(
        config,
        fetch_todos=lambda: [todo],
        check_active_fn=lambda _key: False,
        check_worktree_active_fn=lambda _path: False,
        check_terminal_fn=lambda key: any(row["dedupe_key"] == key for row in queued),
        enqueue_fn=enqueue,
    )

    assert first.dispatched == 1
    assert second.dispatched == 0 and second.skipped_terminal == 1
    assert len(queued) == 1
    assert ":resume:" in queued[0]["dedupe_key"]
    assert queued[0]["payload"]["resume_of"] == attempt["attempt_hash"]
