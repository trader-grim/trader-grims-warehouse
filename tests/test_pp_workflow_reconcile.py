import json

import pytest

from tgw import coding_cli
from tgw.pp_workflow_reconcile import (
    CATALOG,
    PP_REF,
    PPWorkflowReconcileError,
    load_catalog,
    reconcile,
)


def test_real_pinned_luet_executes_and_agrees_on_pp_specific_closure():
    first = reconcile()
    second = reconcile()
    assert first == second
    assert first["ok"] and first["unmet_capabilities"] == []
    assert first["solution"]["conformance_verified"]
    assert first["solution"]["dispatchable"]
    assert first["resolver_binding"]["agreement"] == "verified"
    assert first["resolver_binding"]["version"] == "0.9.26"
    assert all(item["evidence"] for item in first["providers"])


def test_catalog_and_plan_content_tampering_fail_closed(tmp_path):
    catalog = json.loads(CATALOG.read_text())
    catalog["identity"]["review_sha256"] = "sha256:" + "0" * 64
    bad_catalog = tmp_path / "catalog.json"
    bad_catalog.write_text(json.dumps(catalog))
    with pytest.raises(PPWorkflowReconcileError, match="identity drift"):
        load_catalog(bad_catalog)

    plan = tmp_path / "plan/pp"
    plan.mkdir(parents=True)
    (plan / "PP-WORKFLOW-001.md").write_text("tampered")
    with pytest.raises(PPWorkflowReconcileError, match="source content hash drift"):
        reconcile(plan_root=tmp_path)


def test_source_tree_tampering_fails_closed(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(PPWorkflowReconcileError, match="evidence source"):
        reconcile(source_root=source)


def test_solver_disagreement_never_claims_metadata_agreement():
    def disagree(*_args, **_kwargs):
        return {
            "provider_id": "luet-pinned-0.9.26@1", "available": True,
            "closure_hash": "sha256:" + "0" * 64, "status": "DISAGREEMENT",
        }

    with pytest.raises(PPWorkflowReconcileError, match="disagreement"):
        reconcile(conform_fn=disagree)


def test_incomplete_evidence_is_conformant_work_not_satisfied(tmp_path):
    catalog = json.loads(CATALOG.read_text())
    provider = catalog["provider_evidence"]["current-local-coding-surfaces"]
    provider["sources"] = []
    provider["local_todos"] = [{
        "id": 1738, "source": "standalone-plan-taskboard@exact",
        "body": "canonical body", "pp_ref": PP_REF,
    }]
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog))
    value = reconcile(catalog_path=path, todo_rows=[{
        "id": 1738, "source": "unrelated-adapter", "body": "canonical body",
        "pp_ref": PP_REF,
    }])
    assert not value["ok"]
    assert value["unmet_capabilities"] == ["coding.local-cli-and-mcp@1"]
    assert value["solution"]["conformance_verified"]


def test_canonical_todo_identity_accepts_exact_row_not_coincident_number(tmp_path):
    catalog = json.loads(CATALOG.read_text())
    provider = catalog["provider_evidence"]["current-local-coding-surfaces"]
    provider["sources"] = []
    identity = {"id": 1738, "source": "standalone-plan-taskboard@exact",
                "body": "canonical body", "pp_ref": PP_REF}
    provider["local_todos"] = [identity]
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog))
    assert reconcile(catalog_path=path, todo_rows=[identity])["ok"]


def test_fully_satisfied_pp_start_has_no_materialization(monkeypatch):
    monkeypatch.setattr(coding_cli, "_initialize", lambda _path: {"coding": {}})
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
    calls = []

    def bridge(*_args, **kwargs):
        calls.append(kwargs)
        return {"todo_id": 42, "created": True}

    monkeypatch.setattr(coding_cli, "bind_leaf", bridge)
    ticks = []
    monkeypatch.setattr(coding_cli, "tick", lambda *_args, **kwargs: ticks.append(kwargs))
    value = coding_cli.start(PP_REF, source_commit="a" * 40)
    assert len(calls) == 1
    assert calls[0]["execution_root"]["kind"] == "pp"
    assert calls[0]["execution_root"]["pp_ref"] == PP_REF
    assert calls[0]["treatment_id"] == "establish:missing@1"
    assert ticks == [{"todo_ids": {42}}]
    assert value["materialized"] == [{"todo_id": 42, "created": True}]
