"""TGW host bindings for the otherwise host-neutral operator console plugin."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Mapping

from tgw.bootstrap_host_integration import (
    BootstrapHostIntegrationError,
    TypedBootstrapDeploymentProvider,
    mount_pinned_bootstrap_host_integration,
)
from tgw.development_console import resolve_request as resolve_development_request
from tgw.development_launch import enqueue_development_launch
from tgw.effect_handlers import AuthorityEffectController, EffectHandlerError, TypedEffectHandlerRegistry
from tgw.operator_console_plugin import OperatorConsoleMount
from tgw.plan_authority import AuthorityPrincipal, PostgresAuthorityStore, PrincipalRole

DEFAULT_PLAN_ROOT = Path("/opt/TGW/library/plans")
_IDENTITY = re.compile(r"^[A-Za-z0-9:._-]+$")
_SOLUTION_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


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


def _approved_plan_identity(config: Mapping[str, Any]) -> Mapping[str, str]:
    """Resolve the sole clean Plan snapshot permitted to drive the console."""
    projection_path = config.get("plan_projection_path")
    if projection_path is not None:
        from tgw.plan_runtime_projection import load_projection

        approved_commit = config.get("plan_approved_commit")
        approved_solution = config.get("plan_approved_solution_hash")
        projection = load_projection(
            projection_path,
            expected_plan_commit=approved_commit,
            trusted_uid=int(config.get("plan_projection_trusted_uid", 0)),
            trusted_root=config.get("plan_projection_root", "/opt/TGW/releases"),
        )
        solution_hash = projection["solution"]["solution_hash"]
        if approved_solution != solution_hash:
            raise RuntimeError("approved Plan projection solution mismatch")
        return {
            "plan_root": "",
            "plan_commit": projection["plan_commit"],
            "solution_hash": solution_hash,
        }
    from tgw.plan_graph import SourcePreconditionError, approved_plan_binding

    try:
        return approved_plan_binding(
            plan_root(config),
            approved_plan_commit=config.get("plan_approved_commit"),
            approved_solution_hash=config.get("plan_approved_solution_hash"),
            git_path=str(config.get("plan_git_path") or "git"),
        )
    except SourcePreconditionError as exc:
        raise RuntimeError(f"approved standalone Plan binding unavailable: {exc.code}") from exc


def current_plan_commit(config_provider: Callable[[], Mapping[str, Any]]) -> str:
    return _approved_plan_identity(config_provider())["plan_commit"]


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
    binding = _approved_plan_identity(config)
    projection_path = config.get("plan_projection_path")
    if projection_path is not None:
        from tgw.plan_runtime_projection import load_projection

        projection = load_projection(
            projection_path,
            expected_plan_commit=binding["plan_commit"],
            trusted_uid=int(config.get("plan_projection_trusted_uid", 0)),
            trusted_root=config.get("plan_projection_root", "/opt/TGW/releases"),
        )
        solution = projection["solution"]
        if solution["solution_hash"] != solution_hash:
            raise ValueError(f"persisted Plan solution unavailable or ambiguous: {solution_hash}")
        return solution
    raw_directory = config.get("plan_solution_root")
    if not isinstance(raw_directory, (str, Path)) or not str(raw_directory):
        raise ValueError("approved Plan solution store is required")
    directory = Path(raw_directory).resolve()
    source_root = Path(binding["plan_root"])
    if directory == source_root or source_root in directory.parents:
        raise ValueError("approved Plan solution store must be outside the Plan materialization")
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
    if matches[0].get("plan_commit") != binding["plan_commit"]:
        raise ValueError("persisted Plan solution is not bound to the approved Plan commit")
    return matches[0]


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _development_artifacts(config: Mapping[str, Any]) -> tuple[str, Mapping[str, Any], Mapping[str, Any]]:
    raw = config.get("development")
    if not isinstance(raw, Mapping):
        raise ValueError("development console configuration is required")
    source_commit = raw.get("source_commit")
    if not isinstance(source_commit, str) or _COMMIT.fullmatch(source_commit) is None:
        raise ValueError("development console source commit is not exact")
    artifacts: list[Mapping[str, Any]] = []
    for name in ("provider_registry", "freshness_receipt"):
        path_value, expected = raw.get(name + "_path"), raw.get(name + "_hash")
        if not isinstance(path_value, str) or not path_value.startswith("/") or not isinstance(expected, str) or _SOLUTION_HASH.fullmatch(expected) is None:
            raise ValueError(f"development console {name} binding is invalid")
        path = Path(path_value)
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"development console {name} is unavailable")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"development console {name} is invalid") from exc
        if not isinstance(value, Mapping) or _canonical_hash(value) != expected:
            raise ValueError(f"development console {name} hash mismatch")
        artifacts.append(value)
    registry, freshness = artifacts
    if registry.get("schema") != "tgw-harness-provider-registry/v1":
        raise ValueError("development console provider registry schema is invalid")
    unsigned = dict(freshness)
    claimed = unsigned.pop("receipt_hash", None)
    if freshness.get("schema") != "tgw-w18-projection-refresh-receipt/v1" or claimed != _canonical_hash(unsigned):
        raise ValueError("development console freshness receipt is invalid")
    return source_commit, registry, freshness


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


def configured_execution_controller(
    store: ConfiguredAuthorityStore,
    config_provider: Callable[[], Mapping[str, Any]],
    *,
    bootstrap_provider: TypedBootstrapDeploymentProvider | None = None,
) -> AuthorityEffectController:
    """Build the one host execution boundary over the canonical store.

    Concrete provider mounting is deliberately explicit.  Until a host mounts
    an allowlisted provider, its typed effect is denied *inside* the controller
    and receives a durable outcome; it can never fall back to /consume's old
    direct-redemption path.
    """
    raw_bootstrap = config_provider().get("pinned_bootstrap_host_integration")
    integration = None
    if raw_bootstrap is not None or bootstrap_provider is not None:
        if not isinstance(raw_bootstrap, Mapping):
            raise RuntimeError("pinned bootstrap host integration configuration is required")
        try:
            integration = mount_pinned_bootstrap_host_integration(
                raw_bootstrap, provider=bootstrap_provider,
            )
        except BootstrapHostIntegrationError as exc:
            raise RuntimeError("pinned bootstrap host integration cannot be mounted") from exc
    registry = TypedEffectHandlerRegistry(
        release_install=_unmounted_provider("coding-release"),
        release_rollback=_unmounted_provider("coding-release rollback"),
        flake_push=_unmounted_provider("bounded-flake-push"),
        flake_switch_record=_unmounted_provider("flake-switch-record-only"),
        dependency_resubmit=_unmounted_provider("dependency-resubmit"),
        development_launch=lambda parameters: enqueue_development_launch(config_provider(), parameters),
        bootstrap_install=(integration.install if integration else _unmounted_provider("approval-platform-bootstrap-deployment")),
        bootstrap_rollback=(integration.rollback if integration else _unmounted_provider("approval-platform-bootstrap-deployment rollback")),
        bootstrap_contract_resolver=(integration.resolver if integration else None),
    )
    return AuthorityEffectController(registry, store)


def configured_console_mount(
    config_provider: Callable[[], Mapping[str, Any]],
    *,
    require_operator: Callable[[], Any],
    require_executor: Callable[[], Any],
    execute_effect: Callable[..., Any] | None = None,
    bootstrap_provider: TypedBootstrapDeploymentProvider | None = None,
    bootstrap_provider_factory: Callable[[Mapping[str, Any]], TypedBootstrapDeploymentProvider | None] | None = None,
) -> OperatorConsoleMount:
    if bootstrap_provider is not None and bootstrap_provider_factory is not None:
        raise RuntimeError("bootstrap provider and provider factory cannot both be configured")
    store = ConfiguredAuthorityStore(config_provider)
    if execute_effect is None:
        # The HTTP module mounts routes before its lifespan loads configuration.
        # Build the closed controller only at consume time, so a configured
        # provider is visible then and malformed provider configuration fails
        # before AuthorityEffectController can call begin_execution.
        def execute_effect(*args: Any, **kwargs: Any) -> Any:
            provider = bootstrap_provider
            if bootstrap_provider_factory is not None:
                try:
                    provider = bootstrap_provider_factory(config_provider())
                except BootstrapHostIntegrationError as exc:
                    raise ValueError("bootstrap deployment provider cannot be mounted") from exc
            try:
                controller = configured_execution_controller(
                    store, config_provider, bootstrap_provider=provider,
                )
            except RuntimeError as exc:
                raise ValueError("bootstrap deployment provider cannot be mounted") from exc
            return controller.execute(*args, **kwargs)
    def resolve_development(body: Mapping[str, Any], requested_by: str):
        config = config_provider()
        binding = _approved_plan_identity(config)
        solution = load_solution(config_provider, binding["solution_hash"])
        source_commit, provider_registry, freshness = _development_artifacts(config)
        return resolve_development_request(
            body=body,
            solution=solution,
            plan_commit=binding["plan_commit"],
            requested_by=requested_by,
            source_commit=source_commit,
            freshness=freshness,
            provider_registry=provider_registry,
        )

    return OperatorConsoleMount(
        store=store,
        current_plan_commit=lambda: current_plan_commit(config_provider),
        load_solution=lambda identity: load_solution(config_provider, identity),
        require_operator=require_operator,
        require_executor=require_executor,
        execute_effect=execute_effect,
        resolve_development=resolve_development,
    )
