"""Fail-closed composition of authority, typed handlers, and release mounts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tgw.application_deployment_contract import (
    ApplicationDeploymentContractResolver,
    PinnedApplicationDeploymentContractResolver,
)
from tgw.application_release_provider import SshApplicationReleaseProvider
from tgw.bootstrap_authority import ApplicationBootstrapGrant, BootstrapSessionAuthority
from tgw.effect_handlers import AuthorityEffectController, TypedEffectHandlerRegistry
from tgw.effect_completion_store import ImmutableEffectCompletionStore
from tgw.nixos_a3_successor_evaluation import A3SuccessorEvaluationProvider
from tgw.platform_bootstrap import A3PlatformBootstrapProvider
from tgw.release_controller import MountedReleaseController

Provider = Callable[[Mapping[str, str]], Mapping[str, Any]]


@dataclass(frozen=True)
class DeploymentMounts:
    target_host: str
    root_id: str
    root_path: Path
    artifact_ref: str
    artifact_path: Path


@dataclass(frozen=True)
class ReleaseProviders:
    observe_predecessor: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    quiesce_services: Callable[[str, str, Sequence[str], str], Mapping[str, Any]]
    backup: Callable[[str, str, str], Mapping[str, Any]]
    migrate: Callable[[str, str, Path, Sequence[Mapping[str, Any]], str, str], Mapping[str, Any]]
    stage_runtime: Callable[[str, str, Path, Mapping[str, Any], Mapping[str, Any], str], Mapping[str, Any]]
    activate_generation: Callable[[str, str, str, str, str], Mapping[str, Any]]
    restart_services: Callable[[str, str, Sequence[str], str], Mapping[str, Any]]
    health: Callable[[str, str, Sequence[str], str], Mapping[str, Any]]
    verify_unrelated_state: Callable[[str, str, str], Mapping[str, Any]]
    record_stage: Callable[[str, str, Sequence[str]], Mapping[str, Any]]
    reconcile_predecessor: Callable[[str, str, str, Sequence[str], Sequence[str]], Mapping[str, Any]]

    def validate(self) -> None:
        if any(not callable(getattr(self, name)) for name in self.__dataclass_fields__):
            raise ValueError("release provider binding is incomplete")


def _mounted_release(mounts: DeploymentMounts, providers: ReleaseProviders, *, expected_host: str) -> MountedReleaseController:
    if mounts.target_host != expected_host:
        raise ValueError("deployment host does not match the registered production host")
    if not mounts.root_id or not mounts.artifact_ref:
        raise ValueError("symbolic release root and artifact identities are required")
    root, artifact = Path(mounts.root_path), Path(mounts.artifact_path)
    if not root.is_dir():
        raise ValueError("mounted release root is unavailable")
    if not artifact.is_file():
        raise ValueError("mounted candidate artifact is unavailable")
    providers.validate()
    return MountedReleaseController(
        roots={mounts.root_id: root}, artifacts={mounts.artifact_ref: artifact},
        **{name: getattr(providers, name) for name in providers.__dataclass_fields__},
    )


def _registry(
    release: MountedReleaseController,
    *,
    flake_push: Provider,
    flake_switch_record: Provider,
    dependency_resubmit: Provider,
    application_resolver: ApplicationDeploymentContractResolver | None = None,
    application_validate: Provider | None = None,
    platform_bootstrap: A3PlatformBootstrapProvider | None = None,
    bootstrap_contract_resolver=None,
    a3_successor_evaluation: A3SuccessorEvaluationProvider | None = None,
) -> TypedEffectHandlerRegistry:
    return TypedEffectHandlerRegistry(
        release_install=release.install, release_rollback=release.rollback,
        flake_push=flake_push, flake_switch_record=flake_switch_record,
        dependency_resubmit=dependency_resubmit,
        application_bootstrap_contract_resolver=application_resolver,
        application_bootstrap_install=release.install if application_resolver is not None else None,
        application_bootstrap_rollback=release.rollback if application_resolver is not None else None,
        application_bootstrap_validate=application_validate,
        bootstrap_install=platform_bootstrap.install if platform_bootstrap is not None else None,
        bootstrap_rollback=platform_bootstrap.rollback if platform_bootstrap is not None else None,
        bootstrap_validate=platform_bootstrap.preflight if platform_bootstrap is not None else None,
        bootstrap_contract_resolver=bootstrap_contract_resolver,
        nixos_a3_successor_evaluation=a3_successor_evaluation,
    )


def compose_application_bootstrap_controller(
    *,
    expected_host: str,
    authority: BootstrapSessionAuthority,
    application_resolver: PinnedApplicationDeploymentContractResolver,
    terminal_store: ImmutableEffectCompletionStore,
    provider: SshApplicationReleaseProvider,
    controller_evidence: str,
    terminal_precheck: Callable[[], None],
    flake_push: Provider,
    flake_switch_record: Provider,
    dependency_resubmit: Provider,
) -> AuthorityEffectController:
    """Mount W09 only from the sealed SSH/helper production composition."""
    if type(authority) is not BootstrapSessionAuthority:
        raise ValueError("W09 bootstrap session authority is not mounted")
    if authority.production_authority is not True:
        raise ValueError("W09 bootstrap grant is not a protected production authority")
    if type(authority.grant) is not ApplicationBootstrapGrant:
        raise ValueError("W09 requires the disjoint application bootstrap grant")
    if type(application_resolver) is not PinnedApplicationDeploymentContractResolver:
        raise ValueError("pinned W09 application contract resolver is unavailable")
    if application_resolver.production_authority is not True:
        raise ValueError("W09 contract resolver is not a sealed production authority")
    if type(terminal_store) is not ImmutableEffectCompletionStore:
        raise ValueError("immutable W09 terminal receipt sink is unavailable")
    if type(provider) is not SshApplicationReleaseProvider or provider.production_authority is not True:
        raise ValueError("sealed tgw-prod application release provider is unavailable")
    if not isinstance(controller_evidence, str) or not controller_evidence.startswith(
        "w09-controller-config:sha256:"
    ) or not callable(terminal_precheck):
        raise ValueError("W09 controller config provenance is unavailable")
    if expected_host != "tgw-prod" or provider.descriptor["target"]["host"] != expected_host:
        raise ValueError("W09 provider target differs from exact production host")
    production = application_resolver._production
    mounted_grant = authority.grant
    mounted_sink = (
        terminal_store.root,
        terminal_store.sink_id,
        terminal_store.descriptor_hash,
    )
    if (
        terminal_store.sink_id != production.operation_sink_id
        or terminal_store.descriptor_hash != production.operation_sink_descriptor_hash
    ):
        raise ValueError("W09 terminal store differs from the pinned production operation sink")
    def unavailable_steady_state(_parameters: Mapping[str, Any]) -> Mapping[str, Any]:
        raise ValueError("steady-state coding release is not mounted in the one-use W09 controller")

    registry = TypedEffectHandlerRegistry(
        release_install=unavailable_steady_state,
        release_rollback=unavailable_steady_state,
        flake_push=flake_push,
        flake_switch_record=flake_switch_record,
        dependency_resubmit=dependency_resubmit,
        application_bootstrap_contract_resolver=application_resolver,
        application_bootstrap_install=provider.install,
        application_bootstrap_rollback=provider.rollback,
        application_bootstrap_validate=provider.preflight,
    )
    def consume_exact(request_id: str, **binding: Any) -> Mapping[str, Any]:
        if authority.grant is not mounted_grant:
            raise ValueError("mounted W09 grant identity changed")
        return BootstrapSessionAuthority.consume(authority, request_id, **binding)

    def persist_exact(receipt: Mapping[str, Any]) -> Mapping[str, str]:
        terminal_precheck()
        if (
            terminal_store.root,
            terminal_store.sink_id,
            terminal_store.descriptor_hash,
        ) != mounted_sink:
            raise ValueError("mounted W09 terminal sink identity changed")
        return ImmutableEffectCompletionStore.persist(terminal_store, receipt)

    return AuthorityEffectController(
        registry, consume_exact, terminal_recorder=persist_exact,
        bound_evidence=(controller_evidence,),
    )


def compose_deployment_controller(
    mounts: DeploymentMounts,
    providers: ReleaseProviders,
    *,
    expected_host: str,
    consume_authority: Callable[..., Mapping[str, Any]],
    require_authority_schema: Callable[[], None],
    flake_push: Provider,
    flake_switch_record: Provider,
    dependency_resubmit: Provider,
    enable_platform_bootstrap: bool = False,
    platform_bootstrap: A3PlatformBootstrapProvider | None = None,
    bootstrap_contract_resolver=None,
    enable_a3_successor_evaluation: bool = False,
    a3_successor_evaluation: A3SuccessorEvaluationProvider | None = None,
) -> AuthorityEffectController:
    """Create the post-W10 steady-state controller with concrete bindings."""
    if not callable(consume_authority) or not callable(require_authority_schema):
        raise ValueError("steady-state authority binding is unavailable")
    if enable_platform_bootstrap:
        if not isinstance(platform_bootstrap, A3PlatformBootstrapProvider):
            raise ValueError("enabled platform bootstrap lacks its closed provider, keys, closures, or receipt store")
        if platform_bootstrap.manifest["target_host"] != expected_host:
            raise ValueError("platform-bootstrap manifest target differs from the deployment host")
    elif platform_bootstrap is not None or bootstrap_contract_resolver is not None:
        raise ValueError("platform-bootstrap provider is mounted while installation is disabled")
    if enable_a3_successor_evaluation:
        if not isinstance(a3_successor_evaluation, A3SuccessorEvaluationProvider):
            raise ValueError("enabled A3 successor evaluation lacks its closed provider and composition")
        if a3_successor_evaluation.composition.status != "REVIEWED_EXECUTABLE" or a3_successor_evaluation.composition.allow_fixture:
            raise ValueError("A3 successor production integration is not executable")
    elif a3_successor_evaluation is not None:
        raise ValueError("A3 successor evaluation provider is mounted while evaluation is disabled")
    release = _mounted_release(mounts, providers, expected_host=expected_host)
    require_authority_schema()
    registry = _registry(
        release, flake_push=flake_push, flake_switch_record=flake_switch_record,
        dependency_resubmit=dependency_resubmit,
        platform_bootstrap=platform_bootstrap if enable_platform_bootstrap else None,
        bootstrap_contract_resolver=bootstrap_contract_resolver if enable_platform_bootstrap else None,
        a3_successor_evaluation=a3_successor_evaluation if enable_a3_successor_evaluation else None,
    )
    return AuthorityEffectController(registry, consume_authority)
