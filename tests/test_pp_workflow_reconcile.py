import hashlib
import json
import shutil
import subprocess

import pytest

from tgw import coding_cli
from tgw import pp_workflow_reconcile as workflow_reconcile
from tgw.development.foreman import TickResult
from tgw.pp_workflow_reconcile import (
    CATALOG,
    PP_REF,
    PPWorkflowReconcileError,
    load_catalog,
    reconcile,
    verify_selected_runtime,
)


def test_real_pinned_luet_executes_and_leaves_unreceipted_capabilities_as_work(monkeypatch):
    def verified(**kwargs):
        return {"verified": True, **kwargs}

    monkeypatch.setattr(workflow_reconcile, "_repository_bytes",
                        lambda _repo, _commit, relative: (CATALOG.parents[2] / relative).read_bytes())
    first = reconcile(runtime_verifier=verified)
    second = reconcile(runtime_verifier=verified)
    assert first == second
    assert not first["ok"] and first["unmet_capabilities"]
    assert first["solution"]["conformance_verified"]
    assert first["solution"]["dispatchable"]
    assert first["resolver_binding"]["agreement"] == "verified"
    assert first["resolver_binding"]["version"] == "0.9.26"
    assert all(item["state"] == "IMPLEMENTED_UNVERIFIED" for item in first["providers"])
    assert all(item["evidence"] for item in first["providers"])
    assert all(not capability.startswith("listing.") for capability in first["unmet_capabilities"])


def test_catalog_and_plan_content_tampering_fail_closed(tmp_path):
    catalog = json.loads(CATALOG.read_text())
    catalog["identity"]["review_sha256"] = "sha256:" + "0" * 64
    bad_catalog = tmp_path / "catalog.json"
    bad_catalog.write_text(json.dumps(catalog))
    with pytest.raises(PPWorkflowReconcileError, match="whole PP capability catalog hash drift"):
        load_catalog(bad_catalog)

    plan = tmp_path / "plan/pp"
    plan.mkdir(parents=True)
    (plan / "PP-WORKFLOW-001.md").write_text("tampered")
    with pytest.raises(PPWorkflowReconcileError, match="source content hash drift"):
        reconcile(plan_root=tmp_path)


def test_source_tree_tampering_fails_closed(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(PPWorkflowReconcileError, match="selected runtime Git identity"):
        reconcile(source_root=source)


def test_external_canonical_source_and_gitless_immutable_release_modes(tmp_path):
    repository = tmp_path / "canonical"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repository, check=True)
    (repository / "tracked.txt").write_bytes(b"exact tracked bytes\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repository, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=repository, text=True).strip()

    source = tmp_path / "source"
    subprocess.run(["git", "worktree", "add", "-q", str(source), commit], cwd=repository, check=True)
    scratch = source / ".tgw-codex-implement-fixture"
    scratch.mkdir()
    (scratch / "worker-state").write_text("ignored scratch")
    assert verify_selected_runtime(repository=repository, source_root=source,
                                   commit=commit, tree=tree, mode="source-worktree")["verified"]
    (source / "tracked.txt").write_text("tampered")
    with pytest.raises(PPWorkflowReconcileError, match="exact clean commit"):
        verify_selected_runtime(repository=repository, source_root=source,
                                commit=commit, tree=tree, mode="source-worktree")
    subprocess.run(["git", "restore", "tracked.txt"], cwd=source, check=True)

    release = tmp_path / "runtime" / "releases" / commit
    release.mkdir(parents=True)
    shutil.copy2(repository / "tracked.txt", release / "tracked.txt")
    release.chmod(0o755)
    (release / "tracked.txt").chmod(0o644)
    assert not (release / ".git").exists()
    immutable = verify_selected_runtime(repository=repository, source_root=release,
                                        commit=commit, tree=tree, mode="immutable-release")
    assert immutable["verified"] and immutable["manifest_source"] == "git-ls-tree"


def test_solver_disagreement_never_claims_metadata_agreement(monkeypatch):
    def disagree(*_args, **_kwargs):
        return {
            "provider_id": "luet-pinned-0.9.26@1", "available": True,
            "closure_hash": "sha256:" + "0" * 64, "status": "DISAGREEMENT",
        }

    monkeypatch.setattr(workflow_reconcile, "_repository_bytes",
                        lambda _repo, _commit, relative: (CATALOG.parents[2] / relative).read_bytes())
    with pytest.raises(PPWorkflowReconcileError, match="disagreement"):
        reconcile(conform_fn=disagree, runtime_verifier=lambda **kwargs: {"verified": True, **kwargs})


def test_incomplete_evidence_is_conformant_work_not_satisfied(tmp_path, monkeypatch):
    catalog = json.loads(CATALOG.read_text())
    provider = catalog["provider_evidence"]["current-local-coding-surfaces"]
    provider["sources"] = []
    provider["local_todos"] = [{
        "id": 1738, "source": "standalone-plan-taskboard@exact",
        "body": "canonical body", "pp_ref": PP_REF,
    }]
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog))
    digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(workflow_reconcile, "CATALOG_SHA256", digest)
    monkeypatch.setattr(workflow_reconcile, "_repository_bytes",
                        lambda _repo, _commit, relative: (CATALOG.parents[2] / relative).read_bytes())
    value = reconcile(catalog_path=path,
                      runtime_verifier=lambda **kwargs: {"verified": True, **kwargs}, todo_rows=[{
        "id": 1738, "source": "unrelated-adapter", "body": "canonical body",
        "pp_ref": PP_REF,
    }])
    assert not value["ok"]
    assert "coding.local-cli-and-mcp@1" in value["unmet_capabilities"]
    assert value["solution"]["conformance_verified"]


def test_canonical_todo_identity_does_not_promote_a_source_hash_or_todo_to_admitted(tmp_path, monkeypatch):
    catalog = json.loads(CATALOG.read_text())
    provider = catalog["provider_evidence"]["current-local-coding-surfaces"]
    provider["sources"] = []
    identity = {"id": 1738, "source": "standalone-plan-taskboard@exact",
                "body": "canonical body", "pp_ref": PP_REF}
    provider["local_todos"] = [identity]
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog))
    digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(workflow_reconcile, "CATALOG_SHA256", digest)
    monkeypatch.setattr(workflow_reconcile, "_repository_bytes",
                        lambda _repo, _commit, relative: (CATALOG.parents[2] / relative).read_bytes())
    value = reconcile(catalog_path=path,
                      runtime_verifier=lambda **kwargs: {"verified": True, **kwargs},
                      todo_rows=[identity])
    assert not value["ok"]
    assert "coding.local-cli-and-mcp@1" in value["unmet_capabilities"]


def test_fully_satisfied_pp_start_has_no_materialization(monkeypatch):
    monkeypatch.setattr(coding_cli, "_initialize", lambda _path: {"coding": {
        "repository_root": str(CATALOG.parents[2]), "worktree_root": str(CATALOG.parents[2]),
    }})
    monkeypatch.setattr(coding_cli, "require_coder_account", lambda: "codex")
    monkeypatch.setattr(coding_cli.todo, "todo_list", lambda **_kwargs: [])
    monkeypatch.setattr(coding_cli, "reconcile_pp_workflow", lambda **_kwargs: {
        "ok": True, "unmet_capabilities": [], "solution": {},
    })
    first = coding_cli.start(PP_REF)
    second = coding_cli.start(PP_REF)
    assert first == second
    assert first["materialized"] == []
    assert coding_cli._todo_id("1740") == 1740


def test_incomplete_pp_start_calls_explicit_bridge_only_for_unmet_work(monkeypatch, tmp_path):
    coding = {"repository_root": str(tmp_path), "worktree_root": str(tmp_path)}
    monkeypatch.setattr(coding_cli, "_initialize", lambda _path: {"coding": coding})
    monkeypatch.setattr(coding_cli, "require_coder_account", lambda: "codex")
    monkeypatch.setattr(coding_cli.todo, "todo_list", lambda **_kwargs: [])
    solution = {
        "plan_commit": "0" * 40, "closure_hash": "sha256:" + "1" * 64,
        "work_units": [{"id": "establish:missing@1", "capability": "missing@1"}],
    }
    monkeypatch.setattr(coding_cli, "reconcile_pp_workflow", lambda **_kwargs: {
        "ok": False, "unmet_capabilities": ["missing@1"], "solution": solution,
    })
    monkeypatch.setattr(coding_cli, "compile_solution_runtime", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(coding_cli, "_pp_runtime_binding", lambda _config, _source=None: {
        "repository": tmp_path, "source_root": tmp_path, "selected_commit": "a" * 40,
        "selected_tree": "b" * 40, "runtime_mode": "source-worktree",
    })
    monkeypatch.setattr(
        __import__("tgw.development.local_workflow", fromlist=["_git"]), "_git",
        lambda _repository, *_args: "b" * 40,
    )
    calls = []

    def bridge(*_args, **kwargs):
        calls.append(kwargs)
        return {"todo_id": 42, "created": len(calls) == 1}

    monkeypatch.setattr(coding_cli, "bind_leaf", bridge)
    ticks = []
    def tick(*_args, **kwargs):
        ticks.append(kwargs)
        return TickResult(dispatched=1)
    monkeypatch.setattr(coding_cli, "tick", tick)
    value = coding_cli.start(PP_REF, source_commit="a" * 40)
    assert len(calls) == 1
    assert calls[0]["execution_root"]["kind"] == "pp"
    assert calls[0]["execution_root"]["pp_ref"] == PP_REF
    assert calls[0]["treatment_id"] == "establish:missing@1"
    assert calls[0]["worktree_identity"] == "codex"
    assert ticks == [{"todo_ids": {42}}]
    assert value["ok"] and not value["reconciliation_complete"]
    assert value["reconciliation"]["dimensions"] == {
        "reconciliation_complete": False,
        "operation_success": True,
        "materialization_attempted": True,
    }
    assert value["materialized"] == [{"todo_id": 42, "created": True}]

    monkeypatch.setattr(coding_cli, "tick", lambda *_args, **_kwargs: TickResult(errors=1))
    failed = coding_cli.start(PP_REF, source_commit="a" * 40)
    assert failed["materialized"] == [{"todo_id": 42, "created": False}]
    assert not failed["ok"]
    assert not failed["reconciliation"]["dimensions"]["operation_success"]
    assert failed["reconciliation"]["foreman"]["errors"] == 1
