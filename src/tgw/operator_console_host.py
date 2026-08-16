"""TGW host bindings for the otherwise host-neutral operator console plugin."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping

from tgw.effect_handlers import AuthorityEffectController, EffectHandlerError, TypedEffectHandlerRegistry
from tgw.operator_console_plugin import OperatorConsoleMount
from tgw.plan_authority import AuthorityPrincipal, PostgresAuthorityStore, PrincipalRole

DEFAULT_PLAN_ROOT = Path("/opt/TGW/library/plans")
_IDENTITY = re.compile(r"^[A-Za-z0-9:._-]+$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SOLUTION_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")


def configured_authority_principal(
    config: Mapping[str, Any],
    *,
    field: str,
    role: PrincipalRole,
    authentication_binding: str,
) -> AuthorityPrincipal:
    """Resolve one named PlanAuthority principal from host configuration.

    This deliberately has no fallback identity: a configured credential or
    session mechanism that lacks a matching principal cannot use the console.
    """
    try:
        return AuthorityPrincipal(
            identity=config.get(field),
            role=role,
            authentication_binding=authentication_binding,
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"named Plan authority {role.value} principal is not configured") from exc


def plan_root(config: Mapping[str, Any]) -> Path:
    return Path(config.get("standalone_plan_root") or DEFAULT_PLAN_ROOT).resolve()


def current_plan_commit(config_provider: Callable[[], Mapping[str, Any]]) -> str:
    config = config_provider()
    root = plan_root(config)
    approved = config.get("plan_approved_commit")
    if not isinstance(approved, str) or not _COMMIT.fullmatch(approved):
        # Plan HEAD is an update source, not effect authority.  A host must
        # explicitly bind the console to a frozen approved Plan materialization.
        raise RuntimeError("exact approved standalone Plan commit is required")
    ref = approved
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
    approved = config.get("plan_approved_solution_hash")
    if not isinstance(approved, str) or not _SOLUTION_HASH.fullmatch(approved):
        raise ValueError("exact approved Plan solution is required")
    if solution_hash != approved:
        raise ValueError("requested solution is not the approved Plan solution")
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
    if matches[0].get("plan_commit") != current_plan_commit(config_provider):
        raise ValueError("persisted Plan solution is not bound to the approved Plan commit")
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

    def begin_execution(self, request_id, *, effect_hash, generation, handler_id, executor_principal):
        return self._store().begin_execution(
            request_id,
            effect_hash=effect_hash,
            generation=generation,
            handler_id=handler_id,
            executor_principal=executor_principal,
        )

    def complete_execution(self, receipt_id, *, outcome, evidence=(), rollback_receipt=None, detail=""):
        return self._store().complete_execution(
            receipt_id, outcome=outcome, evidence=evidence,
            rollback_receipt=rollback_receipt, detail=detail,
        )

    def get(self, request_id):
        return self._store().get(request_id)

    def list(self, limit=100):
        return self._store().list(limit)

    def events(self, request_id):
        return self._store().events(request_id)


def _unmounted_provider(name: str) -> Callable[[Mapping[str, str]], Mapping[str, Any]]:
    """Keep the closed registry mounted while refusing unavailable host effects.

    The controller still writes a durable failed/rollback receipt.  No caller
    can escape this path to invoke a command or ambient provider directly.
    The receipt-only authority canary is registered inside
    :class:`TypedEffectHandlerRegistry` and remains executable for end-to-end
    authority health checks.
    """
    def unavailable(_: Mapping[str, str]) -> Mapping[str, Any]:
        raise EffectHandlerError(f"registered {name} provider is unavailable on this host")
    return unavailable


def configured_execution_controller(store: ConfiguredAuthorityStore) -> AuthorityEffectController:
    """Build the one host execution boundary over the canonical store.

    Concrete provider mounting is deliberately explicit.  Until a host mounts
    an allowlisted provider, its typed effect is denied *inside* the controller
    and receives a durable outcome; it can never fall back to /consume's old
    direct-redemption path.
    """
    registry = TypedEffectHandlerRegistry(
        release_install=_unmounted_provider("coding-release"),
        release_rollback=_unmounted_provider("coding-release rollback"),
        flake_push=_unmounted_provider("bounded-flake-push"),
        flake_switch_record=_unmounted_provider("flake-switch-record-only"),
        dependency_resubmit=_unmounted_provider("dependency-resubmit"),
        bootstrap_install=_unmounted_provider("approval-platform-bootstrap-deployment"),
        bootstrap_rollback=_unmounted_provider("approval-platform-bootstrap-deployment rollback"),
    )
    return AuthorityEffectController(registry, store)


def configured_console_mount(
    config_provider: Callable[[], Mapping[str, Any]],
    *,
    require_operator: Callable[[], Any],
    require_executor: Callable[[], Any],
    execute_effect: Callable[..., Any] | None = None,
) -> OperatorConsoleMount:
    store = ConfiguredAuthorityStore(config_provider)
    return OperatorConsoleMount(
        store=store,
        current_plan_commit=lambda: current_plan_commit(config_provider),
        load_solution=lambda identity: load_solution(config_provider, identity),
        require_operator=require_operator,
        require_executor=require_executor,
        execute_effect=execute_effect or configured_execution_controller(store).execute,
    )
