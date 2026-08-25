import json

from tgw import coding_cli
from tgw.pp_workflow_reconcile import PP_REF, reconcile


def test_current_partial_providers_reconcile_to_exact_satisfied_pp_projection():
    first = reconcile()
    second = reconcile()
    assert first == second
    assert first["ok"] and first["unmet_capabilities"] == []
    assert first["solution"]["schema"] == "tgw-plan-solution/v1"
    assert first["solution"]["root"]["id"] == PP_REF
    assert first["solution"]["conformance_providers"][1]["agreement"] == "verified"
    assert first["resolver_binding"]["agreement"] == "verified"
    assert first["resolver_binding"]["version"] == "0.9.26"
    assert first["effects"] == {"todo_created": False, "worktree_created": False, "job_created": False}


def test_missing_partial_provider_is_genuinely_unmet_and_has_one_work_unit():
    value = reconcile(installed_todos=(1734, 1735, 1736, 1737))
    assert not value["ok"]
    assert value["unmet_capabilities"] == ["coding.local-cli-and-mcp@1"]
    assert len(value["solution"]["work_units"]) == 1


def test_coding_start_pp_is_idempotent_and_numeric_start_remains_supported(monkeypatch):
    monkeypatch.setattr(coding_cli, "require_coder_account", lambda: "codex")
    first = coding_cli.start(PP_REF)
    second = coding_cli.start(PP_REF)
    assert first == second
    assert first["materialized"] is False
    assert first["actor"] == "codex" and first["group"] == "tgw-coders"
    assert coding_cli._todo_id("1740") == 1740


def test_catalog_is_source_owned_and_exactly_bound():
    value = reconcile()
    identity = value["source_identity"]
    assert identity["plan_commit"] == "058e2f980201cc78245358e4901cf007063f2c29"
    assert identity["plan_source"]["sha256"] == "sha256:ee5eac22eb072649ea601d77f398ee87e8397f9a84eb3675b4d61f1c32f81af9"
    json.dumps(value, sort_keys=True)
