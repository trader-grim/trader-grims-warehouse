"""Compile one resolved Plan capability into an immutable implementation card.

The Plan remains the authority.  This module only joins the exact solved
capability with its execution-graph work unit and registered resources, then
renders the compact transport fields needed by the Todo adapter.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from tgw.plan_solver import PlanResolutionError, validate_solution_integrity
from tgw.workflow.plan_bridge import CompiledPlanRuntime


CARD_SCHEMA = "tgw-plan-execution-card/v1"
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_RESOURCE_NAMES = frozenset({
    "plan_input", "plan_commit", "plan_graph", "codegraph_snapshot", "source_tree",
    "execution_environment", "authority_conditions", "candidate_evidence", "receipt_sink",
})
_CARD_FIELDS = frozenset({
    "schema", "card_hash", "plan", "solution", "work_unit", "role", "resources",
    "source", "environment", "receipt_sink", "stop_conditions", "task", "scheduling", "receiver",
})
_PROMPTCRAFT_CAPABILITY = "promptcraft.receiver-profiles@1"
_PROMPTCRAFT_PROVIDER = "recovered-promptcraft"


class PlanExecutionCardError(ValueError):
    """A resolved leaf cannot be made into an exact bounded execution card."""


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def card_hash(value: Mapping[str, Any]) -> str:
    unsigned = dict(value)
    unsigned.pop("card_hash", None)
    return "sha256:" + hashlib.sha256(canonical(unsigned)).hexdigest()


def _binding(value: object, name: str) -> dict[str, str]:
    if (
        not isinstance(value, Mapping) or set(value) != {"ref", "hash"}
        or not isinstance(value.get("ref"), str) or not value["ref"]
        or not isinstance(value.get("hash"), str) or _HASH.fullmatch(value["hash"]) is None
    ):
        raise PlanExecutionCardError(f"execution-card resource binding is invalid: {name}")
    return {"ref": value["ref"], "hash": value["hash"]}


def _resources(value: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    if set(value) != _RESOURCE_NAMES:
        raise PlanExecutionCardError("execution-card resources are incomplete")
    return {name: _binding(value[name], name) for name in sorted(_RESOURCE_NAMES)}


def _work_unit(execution_graph: Mapping[str, Any], capability: str) -> dict[str, Any]:
    units = execution_graph.get("work_units")
    if not isinstance(units, Sequence) or isinstance(units, (str, bytes)):
        raise PlanExecutionCardError("execution graph work units are unavailable")
    matches = [dict(unit) for unit in units if isinstance(unit, Mapping) and capability in unit.get("establishes", ())]
    if len(matches) != 1:
        raise PlanExecutionCardError("resolved capability does not identify exactly one execution work unit")
    unit = matches[0]
    if not isinstance(unit.get("id"), str) or not isinstance(unit.get("title"), str):
        raise PlanExecutionCardError("execution work unit identity or intent is invalid")
    acceptance = unit.get("acceptance")
    if not isinstance(acceptance, Sequence) or isinstance(acceptance, (str, bytes)) or not acceptance or not all(isinstance(item, str) and item for item in acceptance):
        raise PlanExecutionCardError("execution work unit acceptance is invalid")
    return unit


def _provider_derived_unit(leaf: Mapping[str, Any]) -> dict[str, Any]:
    """Represent a solved provider leaf that the graph leaves implicit.

    Some approved graphs express foundational providers only in the solved
    capability closure.  Their exact `establishes` evidence is still Plan
    authority; this deliberately derives no free-form operator/Todo prose.
    """
    capability, provider, identity = leaf.get("capability"), leaf.get("selected_provider"), leaf.get("id")
    if not all(isinstance(item, str) and item for item in (capability, provider, identity)):
        raise PlanExecutionCardError("provider-derived work unit is invalid")
    establishes = leaf.get("establishes")
    if not isinstance(establishes, list) or not all(isinstance(item, str) and item for item in establishes):
        raise PlanExecutionCardError("provider-derived work unit has no exact establishment evidence")
    return {
        "id": identity, "title": f"Establish {capability} through {provider}",
        "acceptance": [f"Establish {item}" for item in establishes], "derivation": "solution-provider",
    }


def _rank(solution: Mapping[str, Any], provider: str) -> tuple[int, int]:
    phases = solution.get("phase_order")
    if not isinstance(phases, list):
        raise PlanExecutionCardError("solution phase order is unavailable")
    for phase_index, phase in enumerate(phases):
        if isinstance(phase, list) and provider in phase:
            return phase_index, phase.index(provider)
    raise PlanExecutionCardError("selected provider is absent from the solved phase order")


def _solution_leaf(solution: Mapping[str, Any], treatment_id: str) -> dict[str, Any]:
    leaves = solution.get("work_units")
    if not isinstance(leaves, list):
        raise PlanExecutionCardError("solution has no work-unit closure")
    matches = [dict(item) for item in leaves if isinstance(item, Mapping) and item.get("id") == treatment_id]
    if len(matches) != 1:
        raise PlanExecutionCardError("treatment does not identify one solved work unit")
    leaf = matches[0]
    if not isinstance(leaf.get("capability"), str) or not isinstance(leaf.get("selected_provider"), str):
        raise PlanExecutionCardError("solved work unit lacks capability/provider identity")
    return leaf


def _source_references(leaf: Mapping[str, Any]) -> list[str]:
    value = leaf.get("resume_from", [])
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise PlanExecutionCardError("solved work-unit source references are invalid")
    return sorted(value)


def _promptcraft_receiver(solution: Mapping[str, Any]) -> dict[str, Any]:
    """Bind the recovered provider by catalog identity, never by source path."""
    leaves = solution.get("work_units")
    if not isinstance(leaves, list) or not any(
        isinstance(item, Mapping)
        and item.get("capability") == _PROMPTCRAFT_CAPABILITY
        and item.get("selected_provider") == _PROMPTCRAFT_PROVIDER
        for item in leaves
    ):
        raise PlanExecutionCardError("resolved Plan lacks the canonical Promptcraft capability binding")
    return {
        "required_capability": _PROMPTCRAFT_CAPABILITY,
        "capability_provider": _PROMPTCRAFT_PROVIDER,
        "selected_provider": "launch-time-qualified-provider",
        "handoff": {"adapter": "promptcraft-card-handoff", "schema": "tgw-launcher-handoff/v1"},
    }


def build_execution_card(
    *, compiled: CompiledPlanRuntime, solution: Mapping[str, Any], execution_graph: Mapping[str, Any],
    treatment_id: str, source_commit: str, source_tree: str, resources: Mapping[str, Any],
    environment: Mapping[str, Any], execution_root: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a deterministic card for one currently eligible implementation leaf."""
    try:
        validate_solution_integrity(solution, current_plan_commit=compiled.plan_commit)
    except PlanResolutionError as exc:
        raise PlanExecutionCardError("execution card solution integrity check failed") from exc
    if (
        not compiled.dispatchable or solution.get("solution_hash") != compiled.solution_hash
        or solution.get("plan_commit") != compiled.plan_commit
        or not isinstance(source_commit, str) or _COMMIT.fullmatch(source_commit) is None
        or not isinstance(source_tree, str) or _COMMIT.fullmatch(source_tree) is None
    ):
        raise PlanExecutionCardError("execution card binding is invalid")
    eligible = {item.treatment_id for item in compiled.runtime_graph.eligible_treatments}
    if treatment_id not in eligible:
        raise PlanExecutionCardError("execution card treatment is not currently eligible")
    leaf = _solution_leaf(solution, treatment_id)
    try:
        unit = _work_unit(execution_graph, leaf["capability"])
    except PlanExecutionCardError as exc:
        if "does not identify exactly one" not in str(exc):
            raise
        unit = _provider_derived_unit(leaf)
    phase, ordinal = _rank(solution, leaf["selected_provider"])
    normalized_resources = _resources(resources)
    if not isinstance(environment, Mapping) or not isinstance(execution_root, Mapping):
        raise PlanExecutionCardError("execution card environment/root is invalid")
    source_refs = _source_references(leaf)
    task = {
        "intent": unit["title"], "acceptance": list(unit["acceptance"]),
        "source_references": source_refs,
        "body": "\n".join([
            f"Plan work unit {unit['id']}: {unit['title']}",
            f"Capability: {leaf['capability']}",
            "Acceptance:", *[f"- {item}" for item in unit["acceptance"]],
            "Bound source/test evidence:", *[f"- {item}" for item in source_refs],
        ]),
    }
    unsigned: dict[str, Any] = {
        "schema": CARD_SCHEMA,
        "plan": {"id": execution_graph.get("plan_id"), "commit": compiled.plan_commit, "root": dict(execution_root)},
        "solution": {"hash": compiled.solution_hash, "closure_hash": compiled.closure_hash},
        "work_unit": {"id": unit["id"], "capability": leaf["capability"], "treatment_id": treatment_id, "provider": leaf["selected_provider"]},
        "role": {"canonical": "implementation", "provider_selection": "launch-time-qualified-provider"},
        "receiver": _promptcraft_receiver(solution),
        "resources": normalized_resources,
        "source": {"commit": source_commit, "tree": source_tree},
        "environment": dict(environment),
        "receipt_sink": dict(normalized_resources["receipt_sink"]),
        "stop_conditions": [
            "unresolved-plan-ambiguity", "failed-candidate", "unavailable-qualified-provider",
            "external-provider-or-credential-action", "production-or-tgw-prod-scope", "flake-or-nix-scope",
            "business-listing-or-ebay-scope", "destructive-action",
        ],
        "task": task,
        "scheduling": {"phase": phase, "ordinal": ordinal, "transport_priority": 1_000_000 - phase * 1_000 - ordinal},
    }
    return {**unsigned, "card_hash": card_hash(unsigned)}


def select_next_execution_card(
    *, compiled: CompiledPlanRuntime, solution: Mapping[str, Any], execution_graph: Mapping[str, Any],
    source_commit: str, source_tree: str, resources: Mapping[str, Any], environment: Mapping[str, Any],
    execution_root: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the deterministic earliest currently eligible execution card."""
    candidates = [
        build_execution_card(
            compiled=compiled, solution=solution, execution_graph=execution_graph,
            treatment_id=item.treatment_id, source_commit=source_commit, source_tree=source_tree,
            resources=resources, environment=environment, execution_root=execution_root,
        )
        for item in compiled.runtime_graph.eligible_treatments
    ]
    if not candidates:
        raise PlanExecutionCardError("resolved Plan has no eligible execution card")
    return min(candidates, key=lambda card: (
        card["scheduling"]["phase"], card["scheduling"]["ordinal"], card["work_unit"]["id"],
    ))


def validate_execution_card(value: object, *, compiled: CompiledPlanRuntime | None = None) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _CARD_FIELDS or value.get("schema") != CARD_SCHEMA:
        raise PlanExecutionCardError("execution card schema is invalid")
    card = dict(value)
    if card.get("card_hash") != card_hash(card):
        raise PlanExecutionCardError("execution card hash is invalid")
    plan, solution, unit, role, receiver, source, scheduling, task = (
        card.get(name) for name in ("plan", "solution", "work_unit", "role", "receiver", "source", "scheduling", "task")
    )
    if (
        not all(isinstance(item, Mapping) for item in (plan, solution, unit, role, receiver, source, scheduling, task))
        or not isinstance(plan.get("commit"), str) or _COMMIT.fullmatch(plan["commit"]) is None
        or not isinstance(solution.get("hash"), str) or _HASH.fullmatch(solution["hash"]) is None
        or not isinstance(solution.get("closure_hash"), str) or _HASH.fullmatch(solution["closure_hash"]) is None
        or not isinstance(unit.get("treatment_id"), str) or not isinstance(unit.get("capability"), str)
        or role != {"canonical": "implementation", "provider_selection": "launch-time-qualified-provider"}
        or receiver != {
            "required_capability": _PROMPTCRAFT_CAPABILITY,
            "capability_provider": _PROMPTCRAFT_PROVIDER,
            "selected_provider": "launch-time-qualified-provider",
            "handoff": {"adapter": "promptcraft-card-handoff", "schema": "tgw-launcher-handoff/v1"},
        }
        or not isinstance(source.get("commit"), str) or _COMMIT.fullmatch(source["commit"]) is None
        or not isinstance(source.get("tree"), str) or _COMMIT.fullmatch(source["tree"]) is None
        or not isinstance(scheduling.get("transport_priority"), int)
        or not isinstance(task.get("body"), str) or not task["body"]
    ):
        raise PlanExecutionCardError("execution card fields are invalid")
    _resources(card.get("resources") if isinstance(card.get("resources"), Mapping) else {})
    _binding(card.get("receipt_sink"), "receipt_sink")
    if compiled is not None and (
        plan["commit"] != compiled.plan_commit or solution["hash"] != compiled.solution_hash
        or solution["closure_hash"] != compiled.closure_hash
        or unit["treatment_id"] not in {item.treatment_id for item in compiled.runtime_graph.eligible_treatments}
    ):
        raise PlanExecutionCardError("execution card does not match the current resolved runtime")
    return card
