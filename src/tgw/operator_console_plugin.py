"""FastAPI mount contract for the consolidated operator console.

The later host integration is exactly one line after constructing the config::

    mount_operator_console(app, console_config)

Keeping this adapter separate lets a host pass its existing authentication
dependencies without importing the host application or duplicating authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from fastapi import FastAPI

from tgw.operator_console import create_operator_console_router
from tgw.plan_authority import AuthorityStore

_MOUNTED_ATTRIBUTE = "_tgw_operator_console_mounted"
_ROUTES = {
    ("/api/plan-authority/requests", ("GET",)),
    ("/api/plan-authority/requests", ("POST",)),
    ("/api/plan-authority/requests/{request_id}", ("GET",)),
    ("/api/plan-authority/requests/{request_id}/decisions", ("POST",)),
    ("/api/plan-authority/requests/{request_id}/consume", ("POST",)),
    ("/api/operator-console/discovery", ("GET",)),
    ("/api/operator-console/requests", ("GET",)),
    ("/api/operator-console/requests/{request_id}", ("GET",)),
    ("/api/operator-console/development-requests", ("POST",)),
    ("/api/operator-console/requests/{request_id}/surface", ("GET",)),
    ("/api/operator-console/requests/{request_id}/surface/decisions", ("POST",)),
    ("/form/plan-authority", ("GET",)),
}


@dataclass(frozen=True)
class OperatorConsoleMount:
    store: AuthorityStore
    current_plan_commit: Callable[[], str]
    load_solution: Callable[[str], Mapping[str, Any]]
    require_operator: Callable[[], Any]
    require_executor: Callable[[], Any]
    # Only a registered AuthorityEffectController (or equivalent closed
    # dispatcher) may be mounted here.  ``None`` leaves /consume fail-closed.
    execute_effect: Callable[..., Any] | None = None
    resolve_development: Callable[..., Any] | None = None
    load_dynamic_surface: Callable[..., Any] | None = None
    submit_dynamic_surface_decision: Callable[..., Any] | None = None


def mount_operator_console(app: FastAPI, config: OperatorConsoleMount) -> None:
    """Mount once, preserving the host's auth functions as dependencies."""
    if getattr(app.state, _MOUNTED_ATTRIBUTE, False):
        raise RuntimeError("operator console is already mounted")
    if config.require_operator is config.require_executor:
        raise RuntimeError("executor authorization must be separate from operator authorization")
    router = create_operator_console_router(
        config.store,
        current_plan_commit=config.current_plan_commit,
        load_solution=config.load_solution,
        require_operator=config.require_operator,
        require_executor=config.require_executor,
        execute_effect=config.execute_effect,
        resolve_development=config.resolve_development,
        load_dynamic_surface=config.load_dynamic_surface,
        submit_dynamic_surface_decision=config.submit_dynamic_surface_decision,
    )
    existing = {
        (route.path, tuple(sorted(getattr(route, "methods", None) or ())))
        for route in app.routes
        if hasattr(route, "path")
    }
    collisions = sorted(_ROUTES & existing)
    if collisions:
        rendered = ", ".join(f"{methods} {path}" for path, methods in collisions)
        raise RuntimeError(f"operator console route collision: {rendered}")
    app.include_router(router)
    setattr(app.state, _MOUNTED_ATTRIBUTE, True)
