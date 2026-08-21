"""TGW host bindings for the otherwise host-neutral operator console plugin."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from tgw.bootstrap_host_integration import (
    BootstrapHostIntegrationError,
    TypedBootstrapDeploymentProvider,
    mount_pinned_bootstrap_host_integration,
)
from tgw.development_console import resolve_request as resolve_development_request
from tgw.development_launch import enqueue_development_launch
from tgw.dynamic_surface import compile_dynamic_surface, submit_dynamic_surface
from tgw.effect_handlers import AuthorityEffectController, EffectHandlerError, TypedEffectHandlerRegistry
from tgw.operator_console_plugin import OperatorConsoleMount
from tgw.plan_authority import (
    AuthorityDecision,
    AuthorityPrincipal,
    PostgresAuthorityStore,
    PrincipalRole,
)

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


def _development_artifacts(config: Mapping[str, Any]) -> tuple[str, Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    raw = config.get("development")
    if not isinstance(raw, Mapping):
        raise ValueError("development console configuration is required")
    source_commit = raw.get("source_commit")
    if not isinstance(source_commit, str) or _COMMIT.fullmatch(source_commit) is None:
        raise ValueError("development console source commit is not exact")
    artifacts: list[Mapping[str, Any]] = []
    for name in ("provider_registry", "freshness_receipt", "card_contract"):
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
    registry, freshness, card_contract = artifacts
    if registry.get("schema") != "tgw-harness-provider-registry/v1":
        raise ValueError("development console provider registry schema is invalid")
    unsigned = dict(freshness)
    claimed = unsigned.pop("receipt_hash", None)
    if freshness.get("schema") != "tgw-w18-projection-refresh-receipt/v1" or claimed != _canonical_hash(unsigned):
        raise ValueError("development console freshness receipt is invalid")
    live_revisions = raw.get("live_revisions")
    revision_names = {
        "plan", "capability_graph", "code_graph", "workflow", "actor_contract",
    }
    if (
        not isinstance(live_revisions, Mapping)
        or set(live_revisions) != revision_names
        or not all(isinstance(value, str) and value for value in live_revisions.values())
    ):
        raise ValueError("development console live revision binding is invalid")
    gate_path = raw.get("transition_gate_path")
    if not isinstance(gate_path, str):
        raise ValueError("development console transition gate binding is invalid")
    gate = Path(gate_path)
    if (
        not gate.is_absolute() or gate == Path("/tmp") or Path("/tmp") in gate.parents
        or gate.is_symlink() or not gate.is_file()
    ):
        raise ValueError("development console transition gate is unavailable")
    try:
        recovery_status = json.loads(gate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("development console transition gate is invalid") from exc
    if not isinstance(recovery_status, Mapping):
        raise ValueError("development console transition gate is invalid")
    return (
        source_commit, registry, freshness, card_contract,
        dict(live_revisions), recovery_status,
    )


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
            receipt_id,
            outcome=outcome,
            evidence=evidence,
            rollback_receipt=rollback_receipt,
            detail=detail,
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
                raw_bootstrap,
                provider=bootstrap_provider,
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


def _dynamic_surface_bindings(
    store: ConfiguredAuthorityStore,
    config_provider: Callable[[], Mapping[str, Any]],
) -> tuple[
    Callable[[str], Mapping[str, Any]],
    Callable[[str, Mapping[str, Any], str], Mapping[str, Any]],
]:
    """Build late-bound W14 surface callbacks over PlanAuthority itself."""

    def configuration() -> tuple[str, Path]:
        raw = config_provider().get("dynamic_surfaces")
        if not isinstance(raw, Mapping) or set(raw) != {"enforcement_boundary", "receipt_root", "transition_gate_path"}:
            raise ValueError("dynamic surface configuration is unavailable")
        boundary = raw["enforcement_boundary"]
        if not isinstance(boundary, Mapping) or set(boundary) != {"root", "version", "components"}:
            raise ValueError("dynamic surface enforcement boundary is unavailable")
        boundary_root = Path(str(boundary["root"]))
        components = boundary["components"]
        if (
            not boundary_root.is_absolute()
            or not boundary_root.is_dir()
            or boundary_root.is_symlink()
            or not isinstance(boundary.get("version"), str)
            or not boundary["version"]
            or not isinstance(components, list)
            or len(components) != 4
        ):
            raise ValueError("dynamic surface enforcement boundary is unavailable")
        observed = []
        for component in components:
            if not isinstance(component, Mapping) or set(component) != {"relative_path", "content_sha256"}:
                raise ValueError("dynamic surface enforcement component is invalid")
            relative = Path(str(component["relative_path"]))
            target = boundary_root / relative
            if relative.is_absolute() or ".." in relative.parts or target.is_symlink() or not target.is_file():
                raise ValueError("dynamic surface enforcement component is unavailable")
            actual = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
            if component["content_sha256"] != actual:
                raise ValueError("dynamic surface enforcement component hash mismatch")
            observed.append(actual)
        actual = "sha256:" + hashlib.sha256(json.dumps(observed, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        root = Path(raw["receipt_root"])
        if not root.is_absolute() or root == Path("/tmp") or Path("/tmp") in root.parents or not root.is_dir() or root.is_symlink():
            raise ValueError("dynamic surface receipt root is not a durable directory")
        gate = Path(raw["transition_gate_path"])
        if not gate.is_absolute() or gate == Path("/tmp") or Path("/tmp") in gate.parents or not gate.is_file() or gate.is_symlink():
            raise ValueError("dynamic surface transition gate is unavailable")
        try:
            gate_value = json.loads(gate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("dynamic surface transition gate is invalid") from exc
        if not isinstance(gate_value, Mapping) or gate_value.get("schema") != "tgw-w18-fleet-transition-gate/v1" or gate_value.get("status") != "ACTIVE":
            raise ValueError("dynamic surfaces are suspended for a fleet transition")
        return actual, root

    def proposal(request_id: str) -> tuple[dict[str, Any], str, str]:
        row = store.get(request_id)
        if row is None:
            raise ValueError("authority request not found")
        if row.get("decision_kind") is not None or row.get("receipt_id") is not None:
            raise ValueError("authority request is no longer pending")
        plan_commit, solution_hash = row.get("plan_commit"), row.get("solution_hash")
        if not isinstance(plan_commit, str) or _COMMIT.fullmatch(plan_commit) is None:
            raise ValueError("dynamic surface Plan commit is invalid")
        if not isinstance(solution_hash, str) or _SOLUTION_HASH.fullmatch(solution_hash) is None:
            raise ValueError("dynamic surface Plan solution is invalid")
        current = _approved_plan_identity(config_provider())
        if plan_commit != current["plan_commit"] or solution_hash != current["solution_hash"]:
            raise ValueError("dynamic surface request is bound to a stale Plan solution")
        expiry = row.get("expires_at")
        if isinstance(expiry, datetime):
            expiry = expiry.astimezone(timezone.utc).isoformat()
        if not isinstance(expiry, str):
            raise ValueError("dynamic surface expiry is unavailable")
        card_hash = _canonical_hash(
            {
                "request_id": request_id,
                "effect_hash": row.get("effect_hash"),
                "effect_generation": row.get("effect_generation"),
                "object_generation": row.get("object_generation"),
            }
        )
        authority_hash = _canonical_hash(
            {
                "request_id": request_id,
                "plan_commit": plan_commit,
                "solution_hash": solution_hash,
                "closure_hash": row.get("closure_hash"),
                "expires_at": expiry,
            }
        )
        value = {
            "schema": "tgw-dynamic-surface-proposal/v1",
            "surface_id": "authority-" + card_hash.removeprefix("sha256:")[:24],
            "request_id": request_id,
            "plan_commit": plan_commit,
            "solution_hash": solution_hash,
            "card_hash": card_hash,
            "authority_hash": authority_hash,
            "expiry": expiry,
            "audience": "operator",
            "title": str(row.get("summary") or "Plan authority decision"),
            "state": "LIVE",
            "components": [
                {"type": "heading", "id": "scope", "text": "Exact bound effect"},
                {"type": "text", "id": "effect", "text": str(row.get("effect_kind") or "governed effect")},
                {"type": "input", "id": "reason", "label": "Decision reason", "input": {"kind": "string", "required": True}},
                {"type": "input", "id": "reconciliation-evidence", "label": "Reconciliation evidence identities, one per line", "input": {"kind": "string", "required": False}},
            ],
            "actions": [
                {"id": "approve", "label": "Approve", "decision": "approve", "handler_id": "plan-authority-decision", "field_ids": ["reason"]},
                {"id": "hold", "label": "Hold", "decision": "hold", "handler_id": "plan-authority-decision", "field_ids": ["reason"]},
                {"id": "reconcile", "label": "Reconcile", "decision": "reconcile", "handler_id": "plan-authority-decision", "field_ids": ["reason", "reconciliation-evidence"]},
            ],
        }
        return value, card_hash, authority_hash

    contracts = {"plan-authority-decision": {"decisions": ["approve", "hold", "reconcile"]}}

    def retain_surface(root: Path, surface: Mapping[str, Any]) -> dict[str, str]:
        surface_hash = surface.get("surface_hash")
        if not isinstance(surface_hash, str) or _SOLUTION_HASH.fullmatch(surface_hash) is None:
            raise ValueError("dynamic surface identity is invalid")
        path = root / (surface_hash.removeprefix("sha256:") + ".surface.json")
        encoded = json.dumps(surface, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != encoded:
                raise ValueError("dynamic surface retention identity collision")
        else:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        return {"path": str(path), "surface_hash": surface_hash}

    def load(request_id: str) -> Mapping[str, Any]:
        renderer, root = configuration()
        value, _card_hash, _authority_hash = proposal(request_id)
        surface = compile_dynamic_surface(
            proposal=value,
            handler_registry=contracts,
            renderer_version=renderer,
            observed_at=datetime.now(timezone.utc).isoformat(),
        )
        return {**surface, "retention": retain_surface(root, surface)}

    def submit(request_id: str, body: Mapping[str, Any], operator: str) -> Mapping[str, Any]:
        renderer, root = configuration()
        value, card_hash, authority_hash = proposal(request_id)
        surface = compile_dynamic_surface(
            proposal=value,
            handler_registry=contracts,
            renderer_version=renderer,
            observed_at=datetime.now(timezone.utc).isoformat(),
        )
        retain_surface(root, surface)
        submission = dict(body)
        submission["operator"] = operator

        def decide(invocation: Mapping[str, Any]) -> Mapping[str, Any]:
            values = invocation["values"]
            evidence = tuple(line.strip() for line in str(values.get("reconciliation-evidence") or "").splitlines() if line.strip())
            decision = AuthorityDecision.create(
                request_id,
                {
                    "kind": invocation["decision"],
                    "decided_by": operator,
                    "reason": values["reason"],
                    "reconciliation_evidence": evidence,
                },
            )
            recorded = store.decide(decision)
            return {"status": "RECORDED", "decision_id": str(recorded.get("decision_id") or decision.decision_id)}

        def persist(receipt: Mapping[str, Any]) -> Mapping[str, Any]:
            receipt_hash = receipt["receipt_hash"]
            path = root / (receipt_hash.removeprefix("sha256:") + ".json")
            encoded = json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
            except FileExistsError:
                if path.is_symlink() or path.read_bytes() != encoded:
                    raise ValueError("dynamic surface receipt identity collision")
            else:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
            return {"path": str(path), "receipt_hash": receipt_hash}

        def claim(invocation: Mapping[str, Any]) -> Mapping[str, Any]:
            claim_hash = _canonical_hash(invocation)
            path = root / (claim_hash.removeprefix("sha256:") + ".claim.json")
            value = {
                "schema": "tgw-dynamic-surface-submission-claim/v1",
                "status": "CLAIMED",
                "claim_hash": claim_hash,
                "request_id": request_id,
                "surface_hash": invocation["surface_hash"],
            }
            encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
            except FileExistsError as exc:
                raise ValueError("dynamic surface submission was replayed or is already in progress") from exc
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            return value

        return submit_dynamic_surface(
            surface=surface,
            submission=submission,
            current_card_hash=card_hash,
            current_authority_hash=authority_hash,
            handlers={"plan-authority-decision": decide},
            persist_receipt=persist,
            claim_submission=claim,
        )

    return load, submit


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
                    store,
                    config_provider,
                    bootstrap_provider=provider,
                )
            except RuntimeError as exc:
                raise ValueError("bootstrap deployment provider cannot be mounted") from exc
            return controller.execute(*args, **kwargs)

    def resolve_development(body: Mapping[str, Any], requested_by: str):
        config = config_provider()
        binding = _approved_plan_identity(config)
        solution = load_solution(config_provider, binding["solution_hash"])
        (
            source_commit, provider_registry, freshness, card_contract,
            live_revisions, recovery_status,
        ) = _development_artifacts(config)
        return resolve_development_request(
            body=body,
            solution=solution,
            plan_commit=binding["plan_commit"],
            requested_by=requested_by,
            source_commit=source_commit,
            freshness=freshness,
            provider_registry=provider_registry,
            card_contract=card_contract,
            live_revisions=live_revisions,
            recovery_status=recovery_status,
        )

    def load_recovery_status() -> Mapping[str, Any]:
        return dict(_development_artifacts(config_provider())[-1])

    load_dynamic_surface, submit_dynamic_surface_decision = _dynamic_surface_bindings(
        store,
        config_provider,
    )

    return OperatorConsoleMount(
        store=store,
        current_plan_commit=lambda: current_plan_commit(config_provider),
        load_solution=lambda identity: load_solution(config_provider, identity),
        require_operator=require_operator,
        require_executor=require_executor,
        execute_effect=execute_effect,
        resolve_development=resolve_development,
        load_recovery_status=load_recovery_status,
        load_dynamic_surface=load_dynamic_surface,
        submit_dynamic_surface_decision=submit_dynamic_surface_decision,
    )
