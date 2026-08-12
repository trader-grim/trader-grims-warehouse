"""Fail-closed composition of authority, typed handlers, and release mounts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from tgw.effect_handlers import AuthorityEffectController, TypedEffectHandlerRegistry
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


def compose_deployment_controller(
    mounts: DeploymentMounts,
    *,
    expected_host: str,
    consume_authority: Callable[..., Mapping[str, Any]],
    backup: Callable[[str, str], Mapping[str, Any]],
    health: Callable[[str, str], Mapping[str, Any]],
    require_authority_schema: Callable[[], None],
    flake_push: Provider,
    flake_switch_record: Provider,
    dependency_resubmit: Provider,
    enable_platform_bootstrap: bool = False,
    platform_bootstrap: A3PlatformBootstrapProvider | None = None,
) -> AuthorityEffectController:
    """Create the production controller only after every binding is concrete."""
    if mounts.target_host != expected_host:
        raise ValueError("deployment host does not match the registered production host")
    if not mounts.root_id or not mounts.artifact_ref:
        raise ValueError("symbolic release root and artifact identities are required")
    root = Path(mounts.root_path)
    artifact = Path(mounts.artifact_path)
    if not root.is_dir():
        raise ValueError("mounted release root is unavailable")
    if not artifact.is_file():
        raise ValueError("mounted candidate artifact is unavailable")
    for provider in (consume_authority, backup, health, require_authority_schema, flake_push, flake_switch_record, dependency_resubmit):
        if not callable(provider):
            raise ValueError("deployment provider binding is unavailable")
    if enable_platform_bootstrap:
        if not isinstance(platform_bootstrap, A3PlatformBootstrapProvider):
            raise ValueError("enabled platform bootstrap lacks its closed provider, keys, closures, or receipt store")
        if platform_bootstrap.manifest["target_host"] != expected_host:
            raise ValueError("platform-bootstrap manifest target differs from the deployment host")
    elif platform_bootstrap is not None:
        raise ValueError("platform-bootstrap provider is mounted while installation is disabled")
    require_authority_schema()
    release = MountedReleaseController(
        roots={mounts.root_id: root},
        artifacts={mounts.artifact_ref: artifact},
        backup=backup,
        health=health,
    )
    registry = TypedEffectHandlerRegistry(
        release_install=release.install,
        release_rollback=release.rollback,
        flake_push=flake_push,
        flake_switch_record=flake_switch_record,
        dependency_resubmit=dependency_resubmit,
        bootstrap_install=platform_bootstrap.install if platform_bootstrap is not None else None,
        bootstrap_rollback=platform_bootstrap.rollback if platform_bootstrap is not None else None,
    )
    return AuthorityEffectController(registry, consume_authority)
