"""Direct tgw-lib Context bindings with no fleet or admission dependency."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def install(
    context_server: Any,
    *,
    current_context: Callable[[], dict[str, Any]],
    actor: Callable[[], str],
) -> None:
    """Replace fleet-bound server facts with direct local read-only facts."""

    def bindings() -> dict[str, Any]:
        plan_root = context_server._path_env(
            "TGW_CONTEXT_PLAN_ROOT",
            "/opt/TGW/library/approved/058e2f980201cc78245358e4901cf007063f2c29",
        )
        plan_repository = context_server._path_env(
            "TGW_CONTEXT_PLAN_REPOSITORY", "/opt/TGW/library/plans"
        )
        source_root = context_server._path_env(
            "TGW_CONTEXT_SOURCE_ROOT", "/opt/TGW/tgw-lib/src/trader-grims-warehouse"
        )
        runtime_root = Path(
            os.environ.get("TGW_CONTEXT_RUNTIME_ROOT", "/opt/TGW/tgw-lib/var/context")
        )
        catalog_path = Path(
            os.environ.get(
                "TGW_CONTEXT_ENVIRONMENT_CATALOG",
                "/opt/TGW/tgw-lib/config/tgw-context-debian-v1.json",
            )
        )
        approved = context_server._approved_commit()
        solution = context_server._approved_solution()
        catalog_raw = catalog_path.read_bytes()
        catalog = json.loads(catalog_raw)
        if catalog.get("schema") != "tgw-execution-environment-catalog/v3":
            raise context_server.ContextError("execution environment catalog is invalid")
        expected_catalog = os.environ.get("TGW_CONTEXT_ENVIRONMENT_CATALOG_HASH", "")
        catalog_hash = _sha(_canonical(catalog))
        if expected_catalog and catalog_hash != expected_catalog:
            raise context_server.ContextError("execution environment catalog hash differs")
        if context_server._git(plan_root, "rev-parse", "HEAD^{commit}") != approved:
            raise context_server.ContextError("approved Plan materialization differs")
        evidence_head = context_server._git(plan_repository, "rev-parse", "HEAD^{commit}")
        if not context_server._is_ancestor(plan_repository, approved, evidence_head):
            raise context_server.ContextError("Plan evidence does not descend from approval")
        source_commit = context_server._git(source_root, "rev-parse", "HEAD^{commit}")
        source_tree = context_server._git(source_root, "rev-parse", f"{source_commit}^{{tree}}")
        source_status = context_server._git(
            source_root, "status", "--porcelain=v1", "--untracked-files=all"
        )
        return {
            "plan_root": plan_root,
            "plan_repository": plan_repository,
            "plan_commit": approved,
            "plan_solution_hash": solution,
            "plan_tree": context_server._git(plan_root, "rev-parse", "HEAD^{tree}"),
            "plan_repository_head": evidence_head,
            "plan_repository_tree": context_server._git(
                plan_repository, "rev-parse", f"{evidence_head}^{{tree}}"
            ),
            "source_root": source_root,
            "source_commit": source_commit,
            "source_tree": source_tree,
            "source_worktree_clean": not bool(source_status),
            "source_status_sha256": _sha(str(source_status).encode()),
            "runtime_root": runtime_root,
            "environment_catalog": catalog,
            "environment_catalog_path": catalog_path,
            "environment_catalog_resolved_path": catalog_path.resolve(strict=True),
            "environment_catalog_hash": catalog_hash,
        }

    def status() -> dict[str, Any]:
        binding = bindings()
        graph = context_server._code_snapshot(
            str(binding["source_root"]), binding["source_commit"]
        )
        snapshot = current_context()
        result = {
            "schema": "tgw-context-service/v2-local",
            "ok": True,
            "host_role": "tgw-lib-independent-development-context",
            "actor": actor(),
            "plan": {
                "repository": str(binding["plan_repository"]),
                "approved_materialization": str(binding["plan_root"]),
                "approved_commit": binding["plan_commit"],
                "approved_tree": binding["plan_tree"],
                "approved_solution_hash": binding["plan_solution_hash"],
                "evidence_head": binding["plan_repository_head"],
                "evidence_tree": binding["plan_repository_tree"],
                "evidence_descends_from_approved": True,
            },
            "source": {
                "repository": str(binding["source_root"]),
                "commit": binding["source_commit"],
                "tree": binding["source_tree"],
                "working_tree_clean": binding["source_worktree_clean"],
                "status_sha256": binding["source_status_sha256"],
            },
            "code_graph": {
                key: graph[key]
                for key in ("commit", "tree", "freshness_hash", "capabilities")
            },
            "environment": {
                "catalog": binding["environment_catalog"],
                "catalog_path": str(binding["environment_catalog_path"]),
                "catalog_hash": binding["environment_catalog_hash"],
            },
            "current_context": {
                key: snapshot[key]
                for key in (
                    "active_capability",
                    "active_treatment",
                    "plan_commit",
                    "source_commit",
                    "snapshot_sha256",
                )
            },
            "dependencies": {
                "actor_runtime": False,
                "fleet": False,
                "admission": False,
                "memory": False,
                "tgw_prod": False,
            },
            "generation_status": {
                "state": "CURRENT",
                "line": (
                    "TGW Context: CURRENT local=tgw-lib "
                    f"plan={binding['plan_commit'][:12]} "
                    f"source={binding['source_commit'][:12]}"
                ),
            },
        }
        result["context_sha256"] = _sha(_canonical(result))
        return result

    def orientation_bundle(requested_actor: str) -> dict[str, Any]:
        if requested_actor != actor():
            raise context_server.ContextError("orientation actor differs from Linux account")
        binding = bindings()
        snapshot = current_context()
        orientation_path = Path(f"/home/{requested_actor}/TGW-ORIENTATION.md")
        orientation = orientation_path.read_text(encoding="utf-8") if orientation_path.is_file() else ""
        result = {
            "schema": "tgw-orientation-bundle/v1",
            "status": "READY",
            "actor": requested_actor,
            "host": "tgw-lib",
            "host_boundaries": {
                "development": "tgw-lib",
                "production": "tgw-prod",
                "production_required_for_development": False,
            },
            "coding": {
                "group": "tgw-coders",
                "source": str(binding["source_root"]),
                "worktrees": "/opt/TGW/var/worktrees",
                "remote_provision_api": False,
            },
            "context": {
                "snapshot_sha256": snapshot["snapshot_sha256"],
                "active_capability": snapshot["active_capability"],
                "active_treatment": snapshot["active_treatment"],
            },
            "orientation_document": {
                "path": str(orientation_path) if orientation else None,
                "sha256": _sha(orientation.encode()) if orientation else None,
                "content": orientation,
            },
            "requires": {
                "seed": False,
                "card": False,
                "fleet_lease": False,
                "memory": False,
                "tgw_prod": False,
            },
        }
        result["bundle_sha256"] = _sha(_canonical(result))
        return result

    def confirm_rebind(
        transaction_id: str, direction: str, obligation_id: str
    ) -> dict[str, Any]:
        """Retain protocol compatibility without mutating retired fleet state."""
        return {
            "schema": "tgw-context-local-operation/v1",
            "ok": False,
            "operation": "confirm-rebind",
            "state": "RETIRED",
            "message": "local Context has no fleet rebind transaction",
            "context": status()["current_context"],
        }

    context_server._bindings = bindings
    context_server.context_status = status
    context_server.onboarding_bundle = orientation_bundle
    context_server.confirm_rebind = confirm_rebind
