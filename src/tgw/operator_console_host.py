"""TGW host bindings for the otherwise host-neutral operator console plugin."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping

from tgw.effect_handlers import AuthorityEffectController, HeldEffect, TypedEffectHandlerRegistry
from tgw.operator_console_plugin import OperatorConsoleMount
from tgw.plan_authority import EffectKind, PostgresAuthorityStore, TypedEffect
from tgw.plan_runtime_projection import load_projection

DEFAULT_PLAN_ROOT = Path("/opt/TGW/library/plans")
_IDENTITY = re.compile(r"^[A-Za-z0-9:._-]+$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def plan_root(config: Mapping[str, Any]) -> Path:
    return Path(config.get("standalone_plan_root") or DEFAULT_PLAN_ROOT).resolve()


def current_plan_commit(config_provider: Callable[[], Mapping[str, Any]]) -> str:
    config = config_provider()
    approved = config.get("plan_approved_commit")
    if approved is not None and (not isinstance(approved, str) or not _COMMIT.fullmatch(approved)):
        raise RuntimeError("approved standalone Plan commit is invalid")
    projection_path = config.get("plan_projection_path")
    if projection_path is not None:
        if approved is None:
            raise RuntimeError("Plan runtime projection requires an approved commit")
        projection = load_projection(
            projection_path,
            expected_plan_commit=approved,
            trusted_uid=int(config.get("plan_projection_trusted_uid", 0)),
            trusted_root=config.get("plan_projection_root", "/opt/TGW/releases"),
        )
        return str(projection["plan_commit"])
    root = plan_root(config)
    ref = approved or "HEAD"
    git_path = str(config.get("plan_git_path") or "git")
    result = subprocess.run(
        [git_path, "-c", f"safe.directory={root}", "-C", str(root), "rev-parse", "--verify", f"{ref}^{{commit}}"],
        check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise RuntimeError(f"standalone Plan commit unavailable: {result.stderr.strip()}")
    return result.stdout.strip()


def load_solution(config_provider: Callable[[], Mapping[str, Any]], solution_hash: str) -> Mapping[str, Any]:
    """Load one exact persisted solution; absence remains an explicit hold."""
    if not _IDENTITY.fullmatch(solution_hash):
        raise ValueError("invalid solution identity")
    config = config_provider()
    projection_path = config.get("plan_projection_path")
    if projection_path is not None:
        approved = config.get("plan_approved_commit")
        projection = load_projection(
            projection_path,
            expected_plan_commit=approved,
            trusted_uid=int(config.get("plan_projection_trusted_uid", 0)),
            trusted_root=config.get("plan_projection_root", "/opt/TGW/releases"),
        )
        solution = projection["solution"]
        if solution.get("solution_hash") != solution_hash:
            raise ValueError(f"persisted Plan solution unavailable or ambiguous: {solution_hash}")
        return solution
    directory = plan_root(config) / "plan" / "execution" / "solutions"
    matches: list[Mapping[str, Any]] = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else ():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid persisted Plan solution: {path.name}") from exc
        if isinstance(payload, Mapping) and payload.get("solution_hash") == solution_hash:
            matches.append(payload)
    if len(matches) != 1:
        raise ValueError(f"persisted Plan solution unavailable or ambiguous: {solution_hash}")
    return matches[0]


class ConfiguredAuthorityStore:
    """Late-bound DSN proxy so FastAPI import does not open or configure DB state."""

    def __init__(self, config_provider: Callable[[], Mapping[str, Any]]):
        self.config_provider = config_provider

    def _store(self) -> PostgresAuthorityStore:
        dsn = self.config_provider().get("postgres_dsn")
        if not isinstance(dsn, str) or not dsn:
            raise RuntimeError("PlanAuthority database is not configured")
        return PostgresAuthorityStore(dsn)

    def create_request(self, request):
        return self._store().create_request(request)

    def decide(self, decision):
        return self._store().decide(decision)

    def consume(self, request_id, *, effect_hash, generation):
        return self._store().consume(request_id, effect_hash=effect_hash, generation=generation)

    def record_execution(self, request_id, receipt):
        return self._store().record_execution(request_id, receipt)

    def get(self, request_id):
        return self._store().get(request_id)

    def list(self, limit=100):
        return self._store().list(limit)

    def events(self, request_id):
        return self._store().events(request_id)


def configured_console_mount(
    config_provider: Callable[[], Mapping[str, Any]],
    *,
    require_operator: Callable[[], Any],
    require_executor: Callable[[], Any],
) -> OperatorConsoleMount:
    store = ConfiguredAuthorityStore(config_provider)

    def unavailable(_: Mapping[str, Any]) -> Mapping[str, Any]:
        raise HeldEffect("effect must be dispatched by its registered external executor")

    registry = TypedEffectHandlerRegistry(
        release_install=unavailable,
        release_rollback=unavailable,
        flake_push=unavailable,
        flake_switch_record=unavailable,
        dependency_resubmit=unavailable,
    )
    controller = AuthorityEffectController(registry, store.consume)

    def execute_request(request_id: str) -> Mapping[str, Any]:
        row = store.get(request_id)
        if row is None:
            raise ValueError("authority request is absent")
        if row.get("effect_kind") != EffectKind.AUTHORITY_CANARY.value:
            raise ValueError("effect must be dispatched by its registered external executor")
        effect = TypedEffect.parse({
            "kind": row["effect_kind"],
            "generation": row["effect_generation"],
            "parameters": row["effect_parameters"],
        })
        if effect.effect_hash != row.get("effect_hash"):
            raise ValueError("stored effect identity does not match its parameters")
        receipt = controller.execute(request_id=request_id, effect=effect).sealed_mapping()
        store.record_execution(request_id, receipt)
        return receipt

    return OperatorConsoleMount(
        store=store,
        current_plan_commit=lambda: current_plan_commit(config_provider),
        load_solution=lambda identity: load_solution(config_provider, identity),
        require_operator=require_operator,
        require_executor=require_executor,
        execute_request=execute_request,
    )
