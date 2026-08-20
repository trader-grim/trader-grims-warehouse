"""Local, non-activating controller seam for an approved Plan transition.

This module prepares evidence for W12.  It deliberately has no configuration,
MCP, service, authority-store, or provider-effect dependency: activation and
rollback are described as one atomic boundary for a separately authorized
operator, never performed here.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Mapping

from tgw.plan_solver import validate_for_dispatch, validate_solution_integrity

HANDOFF_SCHEMA = "tgw-plan-transition-handoff/v1"
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


class PlanTransitionError(ValueError):
    """A local transition precondition is missing or contradictory."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + sha256(_canonical(value)).hexdigest()


def _git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False, timeout=30
    )
    if check and result.returncode:
        raise PlanTransitionError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


@dataclass(frozen=True)
class PlanBinding:
    """One immutable Plan commit and its exact solution artifact."""

    plan_commit: str
    solution: Mapping[str, Any]

    def validate(self, *, dispatchable: bool = False) -> dict[str, str]:
        if not _COMMIT.fullmatch(self.plan_commit):
            raise PlanTransitionError("Plan commit must be exactly 40 lowercase hex characters")
        try:
            validate_solution_integrity(self.solution, current_plan_commit=self.plan_commit)
            if dispatchable:
                validate_for_dispatch(self.solution, current_plan_commit=self.plan_commit)
        except ValueError as exc:
            raise PlanTransitionError(str(exc)) from exc
        return {"plan_commit": self.plan_commit, "solution_hash": str(self.solution["solution_hash"])}


@dataclass(frozen=True)
class DetachedMaterialization:
    """A clean detached worktree that may later become the approved snapshot."""

    path: Path
    plan_commit: str
    tree: str

    def as_data(self) -> dict[str, str]:
        return {"path": str(self.path), "plan_commit": self.plan_commit, "tree": self.tree, "mode": "detached"}


def inspect_detached_materialization(path: str | Path, *, expected_commit: str) -> DetachedMaterialization:
    """Validate a prepared snapshot without reading mutable checkout bytes."""
    if not _COMMIT.fullmatch(expected_commit):
        raise PlanTransitionError("expected Plan commit must be exact")
    try:
        root = Path(path).resolve(strict=True)
    except OSError as exc:
        raise PlanTransitionError("materialization path is unavailable") from exc
    if _git(root, "rev-parse", "--show-toplevel") != str(root):
        raise PlanTransitionError("materialization must be a repository root")
    if _git(root, "rev-parse", "HEAD^{commit}") != expected_commit:
        raise PlanTransitionError("materialization commit does not match its binding")
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise PlanTransitionError("materialization is not clean")
    symbolic = subprocess.run(
        ["git", "-C", str(root), "symbolic-ref", "-q", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if symbolic.returncode == 0:
        raise PlanTransitionError("materialization must be detached")
    return DetachedMaterialization(root, expected_commit, _git(root, "rev-parse", "HEAD^{tree}"))


def prepare_detached_materialization(
    plan_repository: str | Path, *, successor_commit: str, destination: str | Path
) -> DetachedMaterialization:
    """Create and verify a detached successor worktree; never switch approval."""
    if not _COMMIT.fullmatch(successor_commit):
        raise PlanTransitionError("successor Plan commit must be exact")
    repository = Path(plan_repository).resolve(strict=True)
    target = Path(destination)
    if target.exists() or target.is_symlink():
        raise PlanTransitionError("detached materialization destination must not already exist")
    if _git(repository, "cat-file", "-t", successor_commit) != "commit":
        raise PlanTransitionError("successor Plan commit is absent from canonical repository")
    result = subprocess.run(
        ["git", "-C", str(repository), "worktree", "add", "--detach", str(target), successor_commit],
        capture_output=True, text=True, check=False, timeout=30,
    )
    if result.returncode:
        raise PlanTransitionError(result.stderr.strip() or "could not prepare detached materialization")
    try:
        return inspect_detached_materialization(target, expected_commit=successor_commit)
    except Exception:
        subprocess.run(
            ["git", "-C", str(repository), "worktree", "remove", "--force", str(target)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        raise


class PlanTransitionController:
    """Build a receipt-backed W12 handoff while leaving live state untouched."""

    def build_successor(
        self, *, predecessor: PlanBinding, successor_commit: str,
        successor_graph: Mapping[str, Any], solver: Callable[..., Mapping[str, Any]],
    ) -> PlanBinding:
        predecessor.validate()
        if not _COMMIT.fullmatch(successor_commit) or successor_commit == predecessor.plan_commit:
            raise PlanTransitionError("successor Plan commit must be exact and differ from predecessor")
        if successor_graph.get("plan_commit") != successor_commit:
            raise PlanTransitionError("successor graph is not bound to successor Plan commit")
        solution = dict(solver(successor_graph, expected_plan_commit=successor_commit))
        successor = PlanBinding(successor_commit, solution)
        successor.validate(dispatchable=True)
        return successor

    def handoff(
        self, *, predecessor: PlanBinding, successor: PlanBinding,
        predecessor_materialization: DetachedMaterialization,
        successor_materialization: DetachedMaterialization,
        amendment_id: str,
    ) -> dict[str, Any]:
        previous = predecessor.validate()
        next_binding = successor.validate(dispatchable=True)
        if not amendment_id or not isinstance(amendment_id, str):
            raise PlanTransitionError("stable amendment ID is required")
        previous_materialization = self._reinspect_materialization(
            predecessor_materialization, expected_commit=predecessor.plan_commit,
            label="rollback materialization",
        )
        next_materialization = self._reinspect_materialization(
            successor_materialization, expected_commit=successor.plan_commit,
            label="prepared materialization",
        )
        receipt: dict[str, Any] = {
            "schema": HANDOFF_SCHEMA,
            "status": "prepared-not-activated",
            "amendment_id": amendment_id,
            "predecessor": {**previous, "materialization": previous_materialization.as_data()},
            "successor": {**next_binding, "materialization": next_materialization.as_data()},
            "activation_boundary": {
                "requires_operator_direction": True,
                "atomic_updates": ["approved_ref", "approved_materialization", "context_plan_commit", "context_solution_hash"],
                "plan_commit": next_binding["plan_commit"],
                "solution_hash": next_binding["solution_hash"],
                "materialization_path": str(next_materialization.path),
                "materialization_tree": next_materialization.tree,
                "forbidden_actions": ["mcp_switch", "service_restart", "provider_effect", "business_data_mutation"],
            },
            "rollback_boundary": {
                "requires_operator_direction": True,
                "atomic_restore": ["approved_ref", "approved_materialization", "context_plan_commit", "context_solution_hash"],
                "plan_commit": previous["plan_commit"],
                "solution_hash": previous["solution_hash"],
                "materialization_path": str(previous_materialization.path),
                "materialization_tree": previous_materialization.tree,
                "target": previous,
            },
        }
        receipt["receipt_hash"] = _hash(receipt)
        return receipt

    @staticmethod
    def _reinspect_materialization(
        declared: DetachedMaterialization, *, expected_commit: str, label: str,
    ) -> DetachedMaterialization:
        """Reject a forged record or a snapshot changed after preparation."""
        if declared.plan_commit != expected_commit:
            raise PlanTransitionError(f"{label} is not bound to its declared Plan commit")
        observed = inspect_detached_materialization(declared.path, expected_commit=expected_commit)
        if observed.tree != declared.tree:
            raise PlanTransitionError(f"{label} tree does not match its prepared record")
        return observed
