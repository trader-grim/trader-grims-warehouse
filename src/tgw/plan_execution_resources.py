"""Canonical, registered-resource binding for Plan execution cards.

The Plan card compiler owns task derivation.  This module owns the separate
step that turns an eligible card into a launchable envelope.  It accepts only
content-addressed references already returned by the registered Context,
Plan, CodeGraph, and environment services; it never reads a Plan copy,
worktree, release, or ambient configuration as a fallback.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from tgw.plan_execution_card import (
    PlanExecutionCardError, build_execution_card, card_hash, validate_execution_card,
)
from tgw.workflow.plan_bridge import CompiledPlanRuntime


ENVELOPE_SCHEMA = "tgw-plan-execution-envelope/v1"
CONTEXT_SCHEMA = "tgw-plan-execution-resource-context/v1"
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_RESOURCE_NAMES = frozenset({
    "plan_input", "plan_commit", "plan_graph", "codegraph_snapshot", "source_tree",
    "execution_environment", "authority_conditions", "candidate_evidence", "receipt_sink",
})
_CONTEXT_FIELDS = frozenset({
    "schema", "context", "plan", "codegraph", "source", "environment", "authority",
    "candidate_evidence", "receipt_sink", "receiver",
})


class PlanExecutionResourceError(ValueError):
    """An execution card cannot be bound to current registered resources."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def envelope_hash(value: Mapping[str, Any]) -> str:
    unsigned = dict(value)
    unsigned.pop("envelope_hash", None)
    return _hash(unsigned)


def _binding(value: object, label: str) -> dict[str, str]:
    if (
        not isinstance(value, Mapping) or set(value) != {"ref", "hash"}
        or not isinstance(value.get("ref"), str) or not value["ref"].startswith("mcp:tgw-context/")
        or not isinstance(value.get("hash"), str) or _HASH.fullmatch(value["hash"]) is None
    ):
        raise PlanExecutionResourceError(f"registered resource is invalid: {label}")
    # Registered MCP references are deliberately logical references, never a
    # path that can silently point at a worktree, release, cache, or home.
    if any(fragment in value["ref"] for fragment in ("/opt/TGW/w/", "actor-runtime", "releases/", "/home/")):
        raise PlanExecutionResourceError(f"registered resource is historical or ephemeral: {label}")
    return {"ref": value["ref"], "hash": value["hash"]}


def _object(value: object, label: str, fields: set[str] | frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise PlanExecutionResourceError(f"registered resource context is invalid: {label}")
    return dict(value)


@dataclass(frozen=True)
class ExecutionResourceContext:
    """Exact service outputs required to bind one current Plan card."""

    value: Mapping[str, Any]

    @classmethod
    def parse(cls, value: object) -> "ExecutionResourceContext":
        root = _object(value, "root", _CONTEXT_FIELDS)
        if root.get("schema") != CONTEXT_SCHEMA:
            raise PlanExecutionResourceError("registered resource context schema is invalid")
        context = _object(root["context"], "context", {"service", "status_hash"})
        if context.get("service") != "tgw-context" or not isinstance(context.get("status_hash"), str) or _HASH.fullmatch(context["status_hash"]) is None:
            raise PlanExecutionResourceError("registered Context service binding is invalid")
        plan = _object(root["plan"], "plan", {"commit", "tree", "input", "graph"})
        codegraph = _object(root["codegraph"], "CodeGraph", {"commit", "tree", "snapshot"})
        source = _object(root["source"], "source", {"commit", "tree", "tree_ref"})
        environment = _object(root["environment"], "environment", {"profile", "catalog"})
        authority = _object(root["authority"], "authority", {"solution_hash", "closure_hash", "conditions"})
        candidate = _object(root["candidate_evidence"], "candidate evidence", {"clean", "binding"})
        receiver = _object(root["receiver"], "receiver", {"capability", "provider", "handoff"})
        for record, label in ((plan, "plan"), (codegraph, "CodeGraph"), (source, "source")):
            if not all(isinstance(record.get(key), str) and _COMMIT.fullmatch(record[key]) for key in ("commit", "tree")):
                raise PlanExecutionResourceError(f"registered {label} commit/tree is invalid")
        if source["commit"] != codegraph["commit"] or source["tree"] != codegraph["tree"]:
            raise PlanExecutionResourceError("registered source and CodeGraph bindings disagree")
        if environment.get("profile") != "development":
            raise PlanExecutionResourceError("registered execution environment is not development")
        if candidate.get("clean") is not True:
            raise PlanExecutionResourceError("candidate evidence does not bind a clean source")
        if receiver != {
            "capability": "promptcraft.receiver-profiles@1",
            "provider": "recovered-promptcraft",
            "handoff": {"adapter": "promptcraft-card-handoff", "schema": "tgw-launcher-handoff/v1"},
        }:
            raise PlanExecutionResourceError("registered Promptcraft receiver binding is invalid")
        for binding, label in (
            (plan["input"], "plan input"), (plan["graph"], "plan graph"),
            (codegraph["snapshot"], "CodeGraph snapshot"), (source["tree_ref"], "source tree"),
            (environment["catalog"], "execution environment"), (authority["conditions"], "authority conditions"),
            (candidate["binding"], "candidate evidence"), (root["receipt_sink"], "receipt sink"),
        ):
            _binding(binding, label)
        for key in ("solution_hash", "closure_hash"):
            if not isinstance(authority.get(key), str) or _HASH.fullmatch(authority[key]) is None:
                raise PlanExecutionResourceError("registered authority conditions are invalid")
        return cls(root)

    def bindings(self, *, plan_commit: str, solution_hash: str, closure_hash: str, source_commit: str, source_tree: str) -> dict[str, dict[str, str]]:
        value = self.value
        plan, codegraph, source = value["plan"], value["codegraph"], value["source"]
        authority = value["authority"]
        if (plan["commit"], solution_hash, closure_hash, source_commit, source_tree) != (
            plan_commit, authority["solution_hash"], authority["closure_hash"], source["commit"], source["tree"],
        ) or codegraph["commit"] != source_commit or codegraph["tree"] != source_tree:
            raise PlanExecutionResourceError("registered resources do not match the resolved card binding")
        return {
            "plan_input": _binding(plan["input"], "plan input"),
            "plan_commit": {"ref": f"mcp:tgw-context/approved-plan/{plan_commit}", "hash": _hash({"commit": plan_commit, "tree": plan["tree"]})},
            "plan_graph": _binding(plan["graph"], "plan graph"),
            "codegraph_snapshot": _binding(codegraph["snapshot"], "CodeGraph snapshot"),
            "source_tree": _binding(source["tree_ref"], "source tree"),
            "execution_environment": _binding(value["environment"]["catalog"], "execution environment"),
            "authority_conditions": _binding(authority["conditions"], "authority conditions"),
            "candidate_evidence": _binding(value["candidate_evidence"]["binding"], "candidate evidence"),
            "receipt_sink": _binding(value["receipt_sink"], "receipt sink"),
        }


def context_from_registered_status(
    status: Mapping[str, Any], *, solution: Mapping[str, Any], plan_graph_hash: str,
    receipt_sink: Mapping[str, Any],
) -> ExecutionResourceContext:
    """Derive all card resources from one registered ``tgw-context`` status.

    Callers provide the exact resolver product and a registered receipt-sink
    descriptor; they cannot supply individual Plan/source/environment bindings.
    This is the sole source-side construction API for a real Plan card.
    """
    if not isinstance(status, Mapping) or status.get("schema") != "tgw-context-service/v1" or status.get("ok") is not True:
        raise PlanExecutionResourceError("registered Context status is invalid")
    plan = status.get("plan")
    source = status.get("source")
    codegraph = status.get("code_graph")
    environment = status.get("environment")
    if not all(isinstance(item, Mapping) for item in (plan, source, codegraph, environment)):
        raise PlanExecutionResourceError("registered Context status is incomplete")
    plan_commit, plan_tree = plan.get("approved_commit"), plan.get("approved_tree")
    source_commit, source_tree = source.get("commit"), source.get("tree")
    if not all(isinstance(item, str) and _COMMIT.fullmatch(item) for item in (plan_commit, plan_tree, source_commit, source_tree)):
        raise PlanExecutionResourceError("registered Context commit/tree identity is invalid")
    if source.get("working_tree_clean") is not True or codegraph.get("commit") != source_commit or codegraph.get("tree") != source_tree:
        raise PlanExecutionResourceError("registered source or CodeGraph state is stale")
    sources = plan.get("sources")
    execution = sources.get("plan/execution/GOVERNED-EXECUTION-PLATFORM-v1.yaml") if isinstance(sources, Mapping) else None
    catalog_hash = environment.get("catalog_hash")
    if (
        not isinstance(execution, Mapping) or not isinstance(execution.get("sha256"), str) or _HASH.fullmatch(execution["sha256"]) is None
        or not isinstance(catalog_hash, str) or _HASH.fullmatch(catalog_hash) is None
        or not isinstance(codegraph.get("freshness_hash"), str) or _HASH.fullmatch(codegraph["freshness_hash"]) is None
        or not isinstance(status.get("context_sha256"), str) or _HASH.fullmatch(status["context_sha256"]) is None
        or not isinstance(plan_graph_hash, str) or _HASH.fullmatch(plan_graph_hash) is None
    ):
        raise PlanExecutionResourceError("registered Context hashes are invalid")
    if solution.get("plan_commit") != plan_commit or not all(
        isinstance(solution.get(key), str) and _HASH.fullmatch(solution[key]) is not None
        for key in ("solution_hash", "closure_hash")
    ):
        raise PlanExecutionResourceError("resolved solution does not match registered Plan status")
    sink = _binding(receipt_sink, "receipt sink")
    status_hash = status["context_sha256"]
    raw = {
        "schema": CONTEXT_SCHEMA,
        "context": {"service": "tgw-context", "status_hash": status_hash},
        "plan": {
            "commit": plan_commit, "tree": plan_tree,
            "input": {"ref": f"mcp:tgw-context/approved-plan-source/{plan_commit}/governed-execution-platform", "hash": execution["sha256"]},
            "graph": {"ref": f"mcp:tgw-context/approved-plan-graph/{plan_commit}", "hash": plan_graph_hash},
        },
        "codegraph": {
            "commit": source_commit, "tree": source_tree,
            "snapshot": {"ref": f"mcp:tgw-context/codegraph/{source_commit}", "hash": codegraph["freshness_hash"]},
        },
        "source": {
            "commit": source_commit, "tree": source_tree,
            "tree_ref": {"ref": f"mcp:tgw-context/source-tree/{source_commit}", "hash": _hash({"commit": source_commit, "tree": source_tree})},
        },
        "environment": {"profile": "development", "catalog": {"ref": "mcp:tgw-context/environment/development", "hash": catalog_hash}},
        "authority": {
            "solution_hash": solution["solution_hash"], "closure_hash": solution["closure_hash"],
            "conditions": {"ref": f"mcp:tgw-context/solution/{solution['solution_hash']}", "hash": _hash(solution)},
        },
        "candidate_evidence": {
            "clean": True,
            "binding": {"ref": f"mcp:tgw-context/source-status/{source_commit}", "hash": source["status_sha256"]},
        },
        "receipt_sink": sink,
        "receiver": {"capability": "promptcraft.receiver-profiles@1", "provider": "recovered-promptcraft", "handoff": {"adapter": "promptcraft-card-handoff", "schema": "tgw-launcher-handoff/v1"}},
    }
    return ExecutionResourceContext.parse(raw)


def bind_execution_envelope(
    *, compiled: CompiledPlanRuntime, solution: Mapping[str, Any], execution_graph: Mapping[str, Any],
    treatment_id: str, source_commit: str, source_tree: str, context: ExecutionResourceContext,
    execution_root: Mapping[str, Any],
) -> dict[str, Any]:
    """Mechanically build and bind one launchable Plan-card envelope."""
    bindings = context.bindings(
        plan_commit=compiled.plan_commit, solution_hash=compiled.solution_hash,
        closure_hash=compiled.closure_hash, source_commit=source_commit, source_tree=source_tree,
    )
    card = build_execution_card(
        compiled=compiled, solution=solution, execution_graph=execution_graph,
        treatment_id=treatment_id, source_commit=source_commit, source_tree=source_tree,
        resources=bindings,
        environment={"profile": context.value["environment"]["profile"], "catalog": bindings["execution_environment"]},
        execution_root=execution_root,
    )
    unsigned = {
        "schema": ENVELOPE_SCHEMA, "card": card, "resources": bindings,
        "context": {"service": context.value["context"]["service"], "status_hash": context.value["context"]["status_hash"]},
        "receiver": dict(context.value["receiver"]),
    }
    return {**unsigned, "envelope_hash": envelope_hash(unsigned)}


def validate_execution_envelope(value: object, *, compiled: CompiledPlanRuntime | None = None) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"schema", "envelope_hash", "card", "resources", "context", "receiver"} or value.get("schema") != ENVELOPE_SCHEMA:
        raise PlanExecutionResourceError("Plan execution envelope schema is invalid")
    envelope = dict(value)
    if envelope.get("envelope_hash") != envelope_hash(envelope):
        raise PlanExecutionResourceError("Plan execution envelope hash is invalid")
    try:
        card = validate_execution_card(envelope["card"], compiled=compiled)
    except PlanExecutionCardError as exc:
        raise PlanExecutionResourceError("Plan execution envelope card is invalid") from exc
    resources = envelope.get("resources")
    if not isinstance(resources, Mapping) or set(resources) != _RESOURCE_NAMES:
        raise PlanExecutionResourceError("Plan execution envelope resources are incomplete")
    normalized = {name: _binding(resources[name], name) for name in sorted(_RESOURCE_NAMES)}
    if normalized != card["resources"]:
        raise PlanExecutionResourceError("Plan execution envelope resources do not match the card")
    context = envelope.get("context")
    receiver = envelope.get("receiver")
    if (
        not isinstance(context, Mapping) or set(context) != {"service", "status_hash"}
        or context.get("service") != "tgw-context" or not isinstance(context.get("status_hash"), str) or _HASH.fullmatch(context["status_hash"]) is None
        or receiver != {
            "capability": "promptcraft.receiver-profiles@1", "provider": "recovered-promptcraft",
            "handoff": {"adapter": "promptcraft-card-handoff", "schema": "tgw-launcher-handoff/v1"},
        }
    ):
        raise PlanExecutionResourceError("Plan execution envelope registered bindings are invalid")
    envelope["card"] = card
    envelope["resources"] = normalized
    return envelope
