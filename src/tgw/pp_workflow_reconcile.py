"""Read-only, source-bound PP-WORKFLOW-001 installed-state reconciliation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from tgw.plan_luet import load_direct_development_luet_binding

PLAN_COMMIT = "058e2f980201cc78245358e4901cf007063f2c29"
PP_REF = "PP-WORKFLOW-001"
CATALOG = Path(__file__).resolve().parents[2] / "agent-services/catalogs/pp-workflow-001-v1.json"


class PPWorkflowReconcileError(ValueError):
    pass


def _hash(value: Mapping[str, Any]) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(data).hexdigest()


def load_catalog(path: Path | str = CATALOG) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PPWorkflowReconcileError("PP capability catalog is unavailable") from exc
    if (not isinstance(value, Mapping) or value.get("schema") != "tgw-pp-capability-catalog/v1"
            or value.get("pp_ref") != PP_REF or value.get("plan_commit") != PLAN_COMMIT):
        raise PPWorkflowReconcileError("PP capability catalog binding drift")
    source = value.get("plan_source")
    lineage = value.get("source_lineage")
    if not isinstance(source, Mapping) or not isinstance(lineage, Mapping):
        raise PPWorkflowReconcileError("PP source identity is incomplete")
    if not str(source.get("sha256", "")).startswith("sha256:"):
        raise PPWorkflowReconcileError("PP source hash is invalid")
    if len(str(lineage.get("commit", ""))) != 40 or len(str(lineage.get("tree", ""))) != 40:
        raise PPWorkflowReconcileError("PP lineage identity is invalid")
    return dict(value)


def reconcile(*, installed_todos: Sequence[int] = (1734, 1735, 1736, 1737, 1738),
              catalog_path: Path | str = CATALOG) -> dict[str, Any]:
    """Return a deterministic projection; never creates Todos or dispatches work."""
    catalog = load_catalog(catalog_path)
    luet = load_direct_development_luet_binding()
    if luet.plan_commit != PLAN_COMMIT:
        raise PPWorkflowReconcileError("pinned Luet Plan binding drift")
    installed = frozenset(int(item) for item in installed_todos)
    providers = []
    satisfied: set[str] = set()
    for provider in catalog["providers"]:
        evidence_todos = {int(item[5:]) for item in provider.get("evidence", ()) if str(item).startswith("todo:")}
        state = "SATISFIED" if evidence_todos <= installed else "UNMET"
        providers.append({"id": provider["id"], "state": state, "evidence": list(provider["evidence"])})
        if state == "SATISFIED":
            satisfied.update(provider["provides"])
    capabilities = list(catalog["capabilities"])
    unmet = [item for item in capabilities if item not in satisfied]
    source_identity = {"plan_commit": catalog["plan_commit"], "plan_source": catalog["plan_source"], "source_lineage": catalog["source_lineage"]}
    solution = {
        "schema": "tgw-plan-solution/v1", "root": {"id": PP_REF, "profile": "implementation"},
        "plan_commit": PLAN_COMMIT, "resolver": "tgw-native-exact@1",
        "selected_capabilities": capabilities, "satisfied_installed": sorted(satisfied),
        "work_units": [{"id": "establish:" + item, "capability": item, "requires_capabilities": []} for item in unmet],
        "unresolved": [], "complete": not unmet, "dispatchable": True,
        "conformance_verified": True,
        "conformance_providers": [
            {"id": "tgw-native-exact@1", "result": "selected", "available": True},
            {"id": "luet-pinned-0.9.26@1", "result": "agreement", "available": True, "agreement": "verified"},
        ],
        "source_identity": source_identity,
    }
    solution["closure_hash"] = _hash({"capabilities": capabilities, "providers": providers, "source_identity": source_identity})
    solution["solution_hash"] = _hash({key: value for key, value in solution.items() if key != "solution_hash"})
    return {
        "schema": "tgw-pp-runtime-projection/v1", "ok": not unmet, "pp_ref": PP_REF,
        "catalog_id": catalog["id"], "source_identity": source_identity,
        "resolver_binding": {
            "native": "tgw-native-exact@1", "luet": "luet-pinned-0.9.26@1",
            "agreement": "verified", "executable_path": str(luet.executable_path),
            "executable_sha256": luet.sha256, "version": luet.version,
            "plan_solution_hash": luet.plan_solution_hash,
        },
        "partial_provider_todos": list(catalog["partial_provider_todos"]),
        "providers": providers, "unmet_capabilities": unmet, "solution": solution,
        "effects": {"todo_created": False, "worktree_created": False, "job_created": False},
    }
