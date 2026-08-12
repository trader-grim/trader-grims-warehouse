"""Deterministic exact capability solver for ``tgw-plan/v2`` graphs.

The module deliberately has no Plan-document parser.  Callers must supply a
machine graph and the exact commit of the standalone Plan repository.  This
keeps canonical intent, resolution, and dispatch as separate objects.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Iterable, Mapping, Sequence

SOLUTION_SCHEMA = "tgw-plan-solution/v1"
GRAPH_SCHEMA = "tgw-plan/v2"
EXECUTION_GRAPH_SCHEMA = "tgw-plan-execution/v2"

STATE_RANK = {
    "unknown": 0,
    "designed": 1,
    "implemented_unverified": 2,
    "partial": 2,
    "tested": 3,
    "reviewed": 4,
    "admitted": 5,
    "deployed_unverified": 6,
    "operationally_verified": 7,
}


class PlanResolutionError(ValueError):
    """The machine graph cannot be interpreted without inventing intent."""


class StalePlanCommit(PlanResolutionError):
    """A solution is not bound to the currently registered Plan commit."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True)
class Requirement:
    kind: str
    value: str | tuple["Requirement", ...]

    @classmethod
    def parse(cls, value: Any) -> "Requirement":
        if isinstance(value, str):
            return cls("capability", value)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return cls("all", tuple(cls.parse(item) for item in value))
        if isinstance(value, Mapping):
            keys = set(value)
            if keys == {"capability"} and isinstance(value["capability"], str):
                return cls("capability", value["capability"])
            if keys == {"all"} and isinstance(value["all"], Sequence):
                return cls("all", tuple(cls.parse(item) for item in value["all"]))
            if keys == {"any"} and isinstance(value["any"], Sequence) and value["any"]:
                return cls("any", tuple(cls.parse(item) for item in value["any"]))
        raise PlanResolutionError(f"invalid requirement expression: {value!r}")

    def as_data(self) -> Any:
        if self.kind == "capability":
            return self.value
        return {self.kind: [item.as_data() for item in self.value]}


@dataclass(frozen=True)
class Provider:
    id: str
    provides: frozenset[str]
    requires: Requirement
    conflicts: frozenset[str] = frozenset()
    preference: int = 0
    available: bool = True
    blocked: bool = False
    blocked_reason: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "Provider":
        status = data.get("status")
        available = bool(data.get("available", True)) and status not in {"blocked", "rejected", "superseded"}
        return cls(
            id=str(data["id"]),
            provides=frozenset(str(item) for item in data.get("provides", ())),
            requires=Requirement.parse(data.get("requires", [])),
            conflicts=frozenset(str(item) for item in data.get("conflicts", ())),
            preference=int(data.get("preference", 0)),
            available=available,
            blocked=status == "blocked" or data.get("available") is False,
            blocked_reason=(str(data.get("blocked_reason") or status) if not available else None),
        )


@dataclass(frozen=True)
class Observation:
    capability: str
    provider: str
    state: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class Target:
    id: str
    profile: str
    minimum_state: str
    requires: Requirement


@dataclass(frozen=True)
class CapabilityGraph:
    plan_commit: str
    capabilities: frozenset[str]
    providers: tuple[Provider, ...]
    observations: tuple[Observation, ...]
    target: Target
    catalog_gaps: tuple[Mapping[str, Any], ...]

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, expected_plan_commit: str | None = None) -> "CapabilityGraph":
        if data.get("schema") != GRAPH_SCHEMA:
            raise PlanResolutionError(f"expected schema {GRAPH_SCHEMA!r}")
        commit = str(data.get("plan_commit", ""))
        if not commit:
            raise PlanResolutionError("plan_commit is required")
        if expected_plan_commit is not None and commit != expected_plan_commit:
            raise StalePlanCommit(f"graph commit {commit} != registered Plan commit {expected_plan_commit}")

        capabilities = frozenset(
            str(item["id"] if isinstance(item, Mapping) else item) for item in data.get("capabilities", ())
        )
        providers = tuple(sorted((Provider.from_mapping(item) for item in data.get("providers", ())), key=lambda p: p.id))
        if len({p.id for p in providers}) != len(providers):
            raise PlanResolutionError("provider IDs must be unique")
        observations = tuple(
            Observation(
                capability=str(item["capability"]),
                provider=str(item["provider"]),
                state=str(item["state"]),
                evidence=tuple(sorted(str(e) for e in item.get("evidence", ()))),
            )
            for item in data.get("observations", ())
        )
        raw_target = data.get("target")
        if not isinstance(raw_target, Mapping):
            raise PlanResolutionError("target is required")
        profile = str(raw_target.get("profile", "implementation"))
        minimum = str(raw_target.get("minimum_state", data.get("profiles", {}).get(profile, {}).get("minimum_state", "admitted")))
        if minimum not in STATE_RANK:
            raise PlanResolutionError(f"unknown minimum state: {minimum}")
        required = raw_target.get("requires", raw_target.get("required_capabilities", ()))
        return cls(
            plan_commit=commit,
            capabilities=capabilities,
            providers=providers,
            observations=observations,
            target=Target(str(raw_target.get("id", "plan")), profile, minimum, Requirement.parse(required)),
            catalog_gaps=tuple(dict(item) for item in data.get("catalog_gaps", ())),
        )


class ExecutionGraphAdapter:
    """Project explicit declarations from an execution graph into a catalog.

    Execution work units are transitions, not capability providers.  In
    particular, an ``establishes`` declaration proves neither an implementation
    nor observed state.  The adapter therefore emits no providers or
    observations unless a future execution schema contains those objects.
    Missing catalog objects remain visible to the exact solver as bounded gaps.
    """

    adapter_id = "tgw-execution-graph-catalog-adapter@1"

    def adapt(self, execution: Mapping[str, Any], *, plan_commit: str) -> dict[str, Any]:
        if execution.get("schema") != EXECUTION_GRAPH_SCHEMA:
            raise PlanResolutionError(f"expected schema {EXECUTION_GRAPH_SCHEMA!r}")
        if not plan_commit:
            raise PlanResolutionError("exact Plan commit is required")
        target = execution.get("target")
        if not isinstance(target, Mapping):
            raise PlanResolutionError("execution graph target is required")
        required = target.get("required_capabilities")
        if not isinstance(required, Sequence) or isinstance(required, (str, bytes)) or not required:
            raise PlanResolutionError("target.required_capabilities must be a non-empty sequence")
        if not all(isinstance(item, str) for item in required):
            raise PlanResolutionError("target capability identities must be strings")

        # These are definitions/references explicitly present in canonical
        # intent.  Work-unit IDs and prose acceptance criteria are deliberately
        # not interpreted as dependencies or providers.
        declared = set(required)
        for unit in execution.get("work_units", ()):
            if not isinstance(unit, Mapping):
                raise PlanResolutionError("work_units must contain mappings")
            for identity in unit.get("establishes", ()):
                if not isinstance(identity, str):
                    raise PlanResolutionError("work-unit capability identities must be strings")
                declared.add(identity)
            for identity in unit.get("requires_capabilities", ()):
                if not isinstance(identity, str):
                    raise PlanResolutionError("requires_capabilities identities must be strings")
                declared.add(identity)

        return {
            "schema": GRAPH_SCHEMA,
            "plan_commit": plan_commit,
            "source": {
                "adapter": self.adapter_id,
                "schema": EXECUTION_GRAPH_SCHEMA,
                "plan_id": execution.get("plan_id"),
                "version": execution.get("version"),
            },
            "capabilities": [{"id": identity} for identity in sorted(declared)],
            "providers": [],
            "observations": [],
            "target": {
                "id": str(execution.get("plan_id", "plan")),
                "profile": str(target.get("profile", "implementation")),
                "minimum_state": str(target.get("minimum_state", "admitted")),
                "required_capabilities": list(required),
            },
            "catalog_gaps": [
                {
                    "code": "MISSING_PROVIDER_DECLARATION",
                    "capability": identity,
                    "required_by": str(execution.get("plan_id", "plan")),
                }
                for identity in sorted(set(required))
            ],
        }


@dataclass
class _Candidate:
    selected: dict[str, Provider] = field(default_factory=dict)
    choices: list[dict[str, Any]] = field(default_factory=list)

    @property
    def provided(self) -> frozenset[str]:
        return frozenset(capability for provider in self.selected.values() for capability in provider.provides)

    def copy(self) -> "_Candidate":
        return _Candidate(dict(self.selected), list(self.choices))


class ExactCapabilitySolver:
    """Exhaustive, deterministic solver suitable as the native conformance provider."""

    provider_id = "tgw-native-exact@1"

    def solve(self, graph: CapabilityGraph) -> dict[str, Any]:
        solutions: list[_Candidate] = []
        failures: list[dict[str, Any]] = []
        self._expand(graph, graph.target.requires, _Candidate(), (), solutions, failures)
        if not solutions:
            return self._artifact(graph, None, failures)
        # Preference is considered only after complete closures exist.  Stable IDs
        # provide a total ordering independent of input/YAML order.
        winner = sorted(
            solutions,
            key=lambda candidate: (-sum(p.preference for p in candidate.selected.values()), tuple(sorted(candidate.selected))),
        )[0]
        return self._artifact(graph, winner, failures)

    def _expand(
        self,
        graph: CapabilityGraph,
        requirement: Requirement,
        candidate: _Candidate,
        path: tuple[str, ...],
        solutions: list[_Candidate],
        failures: list[dict[str, Any]],
    ) -> None:
        if requirement.kind == "all":
            self._expand_all(graph, tuple(requirement.value), candidate, path, solutions, failures)
            return
        if requirement.kind == "any":
            for option in requirement.value:
                before = len(solutions)
                branch: list[_Candidate] = []
                self._expand(graph, option, candidate.copy(), path, branch, failures)
                for result in branch:
                    result.choices.append({"requirement": requirement.as_data(), "selected": option.as_data()})
                solutions.extend(branch)
                if len(solutions) == before:
                    continue
            return

        capability = str(requirement.value)
        if capability in candidate.provided:
            solutions.append(candidate)
            return
        providers = [provider for provider in graph.providers if capability in provider.provides and provider.blocked_reason not in {"rejected", "superseded"}]
        if not providers:
            code = "UNSATISFIED" if capability in graph.capabilities else "UNKNOWN_CAPABILITY"
            failures.append({"code": code, "capability": capability, "path": [*path, capability]})
            return
        available = [provider for provider in providers if provider.available]
        if not available and any(provider.blocked for provider in providers):
            failures.append(
                {
                    "code": "BLOCKED",
                    "capability": capability,
                    "path": [*path, capability],
                    "providers": [{"id": p.id, "reason": p.blocked_reason} for p in providers],
                }
            )
            return
        if not available:
            failures.append({"code": "UNSATISFIED", "capability": capability, "path": [*path, capability]})
            return
        for provider in sorted(available, key=lambda p: p.id):
            conflict = self._conflict(provider, candidate)
            if conflict:
                failures.append({"code": "UNSATISFIED", "capability": capability, "path": [*path, capability], "provider": provider.id, "reason": conflict})
                continue
            branch = candidate.copy()
            branch.selected[provider.id] = provider
            nested: list[_Candidate] = []
            self._expand(graph, provider.requires, branch, (*path, capability), nested, failures)
            solutions.extend(nested)

    def _expand_all(
        self,
        graph: CapabilityGraph,
        requirements: tuple[Requirement, ...],
        candidate: _Candidate,
        path: tuple[str, ...],
        solutions: list[_Candidate],
        failures: list[dict[str, Any]],
    ) -> None:
        if not requirements:
            solutions.append(candidate)
            return
        heads: list[_Candidate] = []
        self._expand(graph, requirements[0], candidate, path, heads, failures)
        for head in heads:
            self._expand_all(graph, requirements[1:], head, path, solutions, failures)

    @staticmethod
    def _conflict(provider: Provider, candidate: _Candidate) -> str | None:
        selected_ids = set(candidate.selected)
        provided = set(candidate.provided)
        if hit := provider.conflicts & (selected_ids | provided):
            return "conflicts with " + sorted(hit)[0]
        for selected in candidate.selected.values():
            if hit := selected.conflicts & ({provider.id} | set(provider.provides)):
                return f"{selected.id} conflicts with {sorted(hit)[0]}"
        return None

    def _artifact(self, graph: CapabilityGraph, winner: _Candidate | None, failures: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        selected = sorted(winner.selected.values(), key=lambda p: p.id) if winner else []
        observations = {(o.capability, o.provider): o for o in graph.observations}
        minimum = STATE_RANK[graph.target.minimum_state]
        satisfied: list[dict[str, Any]] = []
        work_units: list[dict[str, Any]] = []
        for provider in selected:
            for capability in sorted(provider.provides):
                observation = observations.get((capability, provider.id))
                if observation and STATE_RANK.get(observation.state, -1) >= minimum:
                    satisfied.append({"capability": capability, "provider": provider.id, "state": observation.state, "evidence": list(observation.evidence)})
                else:
                    work_units.append(
                        {
                            "id": "establish:" + capability,
                            "establishes": [f"{capability}@{graph.target.minimum_state}"],
                            "selected_provider": provider.id,
                            "requires_capabilities": sorted(self._leaf_capabilities(provider.requires)),
                            "resume_from": list(observation.evidence) if observation else [],
                        }
                    )
        if winner is None and graph.catalog_gaps:
            unresolved = sorted(
                (
                    {
                        "code": "UNSATISFIED",
                        "capability": str(item["capability"]),
                        "reason": str(item["code"]),
                        "required_by": str(item["required_by"]),
                    }
                    for item in graph.catalog_gaps
                ),
                key=lambda item: _canonical(item),
            )
        elif winner is None:
            unresolved = sorted({json.dumps(item, sort_keys=True): dict(item) for item in failures}.values(), key=lambda item: _canonical(item))
        else:
            unresolved = []
        rejected = []
        if winner:
            selected_ids = set(winner.selected)
            for provider in graph.providers:
                if provider.id not in selected_ids and provider.provides & winner.provided:
                    rejected.append({"provider": provider.id, "reason": provider.blocked_reason or "lower-ranked-or-incompatible-complete-alternative"})
        artifact: dict[str, Any] = {
            "schema": SOLUTION_SCHEMA,
            "resolver": self.provider_id,
            "conformance_providers": [
                {"id": self.provider_id, "available": True, "result": "selected"},
                {"id": "luet-adapter@1", "available": False, "result": "UNAVAILABLE", "agreement": "not-claimed"},
            ],
            "plan_commit": graph.plan_commit,
            "root": {"id": graph.target.id, "profile": graph.target.profile, "minimum_state": graph.target.minimum_state},
            "complete": winner is not None,
            "dispatchable": winner is not None,
            "selected_providers": [p.id for p in selected],
            "selected_capabilities": sorted(winner.provided) if winner else [],
            "selected_alternatives": sorted(winner.choices, key=lambda item: _canonical(item)) if winner else [],
            "rejected_alternatives": rejected,
            "satisfied_installed": satisfied,
            "work_units": sorted(work_units, key=lambda item: item["id"]),
            "phase_order": self._phase_order(selected),
            "unresolved": unresolved,
            "reusable_receipts": sorted({e for item in satisfied for e in item["evidence"]}),
            "invalidated_receipts": [],
        }
        artifact["solution_hash"] = _hash(artifact)
        return artifact

    @staticmethod
    def _phase_order(providers: Sequence[Provider]) -> list[list[str]]:
        """Return deterministic provider phases derived from capability edges."""

        owners = {capability: provider.id for provider in providers for capability in provider.provides}
        dependencies = {
            provider.id: {owners[capability] for capability in ExactCapabilitySolver._leaf_capabilities(provider.requires) if capability in owners and owners[capability] != provider.id}
            for provider in providers
        }
        phases: list[list[str]] = []
        emitted: set[str] = set()
        while len(emitted) < len(providers):
            ready = sorted(provider_id for provider_id, needs in dependencies.items() if provider_id not in emitted and needs <= emitted)
            if not ready:  # A selected closure may legitimately be mutually provided.
                ready = sorted(set(dependencies) - emitted)
            phases.append(ready)
            emitted.update(ready)
        return phases

    @classmethod
    def _leaf_capabilities(cls, requirement: Requirement) -> set[str]:
        if requirement.kind == "capability":
            return {str(requirement.value)}
        return set().union(*(cls._leaf_capabilities(item) for item in requirement.value))


def validate_for_dispatch(solution: Mapping[str, Any], *, current_plan_commit: str) -> None:
    """Fail closed unless a complete solution is current and internally intact."""

    if solution.get("schema") != SOLUTION_SCHEMA:
        raise PlanResolutionError("not a tgw-plan-solution/v1 artifact")
    if solution.get("plan_commit") != current_plan_commit:
        raise StalePlanCommit(f"solution commit {solution.get('plan_commit')} != registered Plan commit {current_plan_commit}")
    unsigned = dict(solution)
    claimed = unsigned.pop("solution_hash", None)
    if claimed != _hash(unsigned):
        raise PlanResolutionError("solution hash mismatch")
    if not solution.get("complete") or not solution.get("dispatchable") or solution.get("unresolved"):
        raise PlanResolutionError("solution is incomplete and cannot dispatch")


def solve(graph: Mapping[str, Any], *, expected_plan_commit: str | None = None) -> dict[str, Any]:
    """Parse and solve one exact machine graph."""

    return ExactCapabilitySolver().solve(CapabilityGraph.from_mapping(graph, expected_plan_commit=expected_plan_commit))
