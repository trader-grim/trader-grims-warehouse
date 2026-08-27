import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tgw import coding_cli, coding_mcp_server
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


def test_mandatory_fixture_is_exactly_retained_without_listing_or_item_scope():
    catalog = load_catalog()
    graph = catalog["graph"]
    assert "workflow.mandatory-fixture@1" in graph["capabilities"]
    assert "workflow.mandatory-fixture@1" in graph["target"]["required_capabilities"]
    provider = next(item for item in graph["providers"]
                    if item["id"] == "current-mandatory-fixture")
    assert provider["provides"] == ["workflow.mandatory-fixture@1"]
    assert catalog["provider_evidence"][provider["id"]] == {"sources": [{
        "path": "tests/test_pp_workflow_001_fixture.py",
        "sha256": "sha256:cc0e1f32d40cfe3aad5c7d7101e6ddc603cfc0c94dc8cde06b7435f197f52662",
    }]}
    assert not any(capability.startswith(("listing.", "item."))
                   for capability in graph["capabilities"])


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


def test_exact_current_provider_catalog_reconciles_and_source_tamper_fails_closed(
        tmp_path):
    repository = CATALOG.parents[2].resolve()
    catalog = load_catalog()
    assert workflow_reconcile.CATALOG_SHA256 == (
        "sha256:4e9be9d004ed5446d9b6cbea3eca7398e6899e111fedc74b4aa4b1907cd3f337"
    )
    assert catalog["provider_evidence"]["current-coding-foreman"] == {"sources": [{
        "path": "src/tgw/development/foreman.py",
        "sha256": "sha256:e26996600d7355b4eb918c467356feecae57f7f1d74b8900d25ea21dcf308c95",
    }]}
    declared_sources = [
        source
        for provider in catalog["provider_evidence"].values()
        for source in provider.get("sources", ())
    ]
    assert len(declared_sources) == 10
    for source in declared_sources:
        raw = (repository / source["path"]).read_bytes()
        assert source["sha256"] == "sha256:" + hashlib.sha256(raw).hexdigest()

    def verified(**kwargs):
        return {"verified": True, **kwargs}

    current = reconcile(runtime_verifier=verified)
    assert current["dimensions"] == {
        "reconciliation_complete": False,
        "operation_success": True,
        "materialization_attempted": False,
    }
    assert current["effects"] == {
        "todo_created": False,
        "worktree_created": False,
        "job_created": False,
        "plan_publication": False,
    }
    assert all(provider["state"] == "IMPLEMENTED_UNVERIFIED"
               for provider in current["providers"])

    stale_catalog = tmp_path / "stale-catalog.json"
    stale_catalog.write_bytes(CATALOG.read_bytes() + b"\n")
    with pytest.raises(PPWorkflowReconcileError,
                       match="whole PP capability catalog hash drift"):
        reconcile(catalog_path=stale_catalog, runtime_verifier=verified)

    source_root = tmp_path / "source"
    for source in declared_sources:
        destination = source_root / source["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repository / source["path"], destination)
    foreman = source_root / "src/tgw/development/foreman.py"
    foreman.write_bytes(foreman.read_bytes() + b"\n# tampered\n")
    commit = subprocess.check_output(
        ["git", "-c", f"safe.directory={repository}", "rev-parse", "HEAD"],
        cwd=repository, text=True,
    ).strip()
    tree = subprocess.check_output(
        ["git", "-c", f"safe.directory={repository}", "rev-parse", "HEAD^{tree}"],
        cwd=repository, text=True,
    ).strip()
    with pytest.raises(PPWorkflowReconcileError,
                       match="source evidence content drift: src/tgw/development/foreman.py"):
        reconcile(source_root=source_root, selected_commit=commit, selected_tree=tree,
                  runtime_verifier=verified)


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
    (source / "implementation-receipt.json").write_text("{}")
    (source / "controller-harness-receipt.json").write_text("{}")
    assert verify_selected_runtime(repository=repository, source_root=source,
                                   commit=commit, tree=tree, mode="source-worktree")["verified"]
    arbitrary = source / "importable.py"
    arbitrary.write_text("raise RuntimeError('must not be trusted')\n")
    with pytest.raises(PPWorkflowReconcileError, match="exact clean commit"):
        verify_selected_runtime(repository=repository, source_root=source,
                                commit=commit, tree=tree, mode="source-worktree")
    arbitrary.unlink()
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
    # The production owner allowlist is deliberately not widened to the
    # invoking test user for a disposable release.
    with pytest.raises(PPWorkflowReconcileError, match="immutable release"):
        verify_selected_runtime(repository=repository, source_root=release,
                                commit=commit, tree=tree, mode="immutable-release")


def test_live_installed_release_verifies_with_production_owner_defaults():
    repository = Path("/opt/TGW/tgw-lib/src/trader-grims-warehouse")
    release = Path("/opt/TGW/tgw-lib/coding-runtime/current").resolve()
    commit = release.name
    tree = subprocess.check_output(
        ["git", "-c", f"safe.directory={repository}", "rev-parse", f"{commit}^{{tree}}"],
        cwd=repository, text=True,
    ).strip()
    result = verify_selected_runtime(repository=repository, source_root=release,
                                     commit=commit, tree=tree, mode="immutable-release")
    assert result["verified"] and result["manifest_source"] == "git-ls-tree"


def test_immutable_runtime_uses_production_doctor_owner_defaults(monkeypatch, tmp_path):
    observed = {}

    def verify(paths, _commit, _release):
        observed["owners"] = paths.trusted_release_owners
        return {"verified": True}

    monkeypatch.setattr("tgw.doctor_cli._verify_release_tree", verify)
    monkeypatch.setattr(workflow_reconcile, "_repository_git", lambda *_args: {
        ("cat-file", "-t", "a" * 40): "commit",
        ("rev-parse", "a" * 40 + "^{tree}"): "b" * 40,
        ("rev-parse", "--git-common-dir"): ".git",
    }[_args[1:]])
    release = tmp_path / "runtime/releases" / ("a" * 40)
    verify_selected_runtime(repository=tmp_path, source_root=release,
                            commit="a" * 40, tree="b" * 40, mode="immutable-release")
    assert observed["owners"] == (0, 65534)


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


def test_catalog_receipt_can_never_source_authenticate_admission(tmp_path, monkeypatch):
    catalog = json.loads(CATALOG.read_text())
    catalog["provider_evidence"]["current-mandatory-fixture"]["receipts"] = [{
        "path": "admission.json", "sha256": "sha256:" + "0" * 64,
    }]
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog))
    monkeypatch.setattr(workflow_reconcile, "CATALOG_SHA256",
                        "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(workflow_reconcile, "_repository_bytes",
                        lambda _repo, _commit, relative: (CATALOG.parents[2] / relative).read_bytes())
    with pytest.raises(PPWorkflowReconcileError, match="cannot independently admit"):
        reconcile(catalog_path=path,
                  runtime_verifier=lambda **kwargs: {"verified": True, **kwargs})


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
    assert calls[0]["worktree_identity"] == "unix:codex"
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


def test_cli_and_mcp_reconcile_share_configured_path(monkeypatch, tmp_path):
    expected = {"schema": "projection", "ok": False}
    calls = []
    monkeypatch.setattr(coding_cli, "_initialize", lambda path: calls.append(path) or {
        "coding": {"repository_root": str(tmp_path)}})
    monkeypatch.setattr(coding_cli.todo, "todo_list", lambda **_kwargs: [])
    monkeypatch.setattr(coding_cli, "_pp_runtime_binding", lambda _config: {"binding": "exact"})
    monkeypatch.setattr(coding_cli, "reconcile_pp_workflow", lambda **kwargs: expected)
    assert coding_cli.reconcile(PP_REF, config_path=tmp_path / "cli.json") == expected
    monkeypatch.setattr(coding_mcp_server, "_config_path", lambda: tmp_path / "mcp.json")
    payload = json.loads(coding_mcp_server.tgw_coding_reconcile())
    assert payload == expected
    assert calls == [tmp_path / "cli.json", tmp_path / "mcp.json"]


def test_coding_reconcile_returns_read_only_native_luet_projection(monkeypatch, tmp_path):
    repository = CATALOG.parents[2].resolve()
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True,
    ).strip()
    tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=repository, text=True,
    ).strip()
    binding = {
        "repository": repository,
        "source_root": repository,
        "selected_commit": commit,
        "selected_tree": tree,
        "runtime_mode": "source-worktree",
    }
    monkeypatch.setattr(coding_cli, "_initialize", lambda _path: {
        "coding": {"repository_root": str(repository)},
    })
    monkeypatch.setattr(coding_cli.todo, "todo_list", lambda **_kwargs: [])
    monkeypatch.setattr(coding_cli, "_pp_runtime_binding", lambda _config: binding)
    monkeypatch.setattr(coding_cli, "reconcile_pp_workflow", lambda **kwargs: reconcile(
        runtime_verifier=lambda **runtime: {"verified": True, **runtime}, **kwargs,
    ))

    result = coding_cli.reconcile(PP_REF, config_path=tmp_path / "cli.json")

    assert not result["ok"]
    assert result["resolver_binding"]["agreement"] == "verified"
    assert result["solution"]["conformance_verified"]
    assert result["dimensions"] == {
        "reconciliation_complete": False,
        "operation_success": True,
        "materialization_attempted": False,
    }
    assert not any(result["effects"].values())
