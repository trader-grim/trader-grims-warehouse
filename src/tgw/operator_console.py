"""Mountable operator console over the single PlanAuthority backend.

The HTML site and machine clients consume the same projection.  This module
does not create a second approval store or infer authority from workflow UI.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from tgw.plan_authority import (
    AUTHORITY_SCHEMA,
    AuthorityRequest,
    AuthorityStore,
    PrincipalRole,
    create_authority_router,
    require_authenticated_principal,
)

CONSOLE_SCHEMA = "tgw-operator-console/v1"
DISCOVERY_SCHEMA = "tgw-operator-console-discovery/v1"
_CLIENT = Path(__file__).with_name("static").joinpath("plan_console.html")

NON_AUTHORITY_SURFACES = (
    {"path": "/form/approvals", "meaning": "legacy action evidence; no Plan effect authority"},
    {"path": "/api/action-approvals", "meaning": "legacy action authority; no Plan effect authority"},
    {"path": "/form/runs", "meaning": "execution evidence only"},
    {"path": "/form/todos", "meaning": "work tracking only"},
    {"path": "/form/pp-clip", "meaning": "intent drafting only"},
    {"path": "/api/items/*", "meaning": "listing workflow state only"},
)

NAVIGATION = {
    "id": "plan-authority",
    "label": "Plan Authority",
    "href": "/form/plan-authority",
    "group": "Admin",
    "order": 30,
}


def _status(row: Mapping[str, Any], now: datetime) -> str:
    # The latest durable execution attempt is the source of truth.  A retry
    # preserves the exact approval but remains visible instead of being
    # collapsed into an indistinguishable "consumed" state.
    outcome = row.get("outcome")
    if outcome and outcome != "retry":
        return str(outcome)
    if row.get("receipt_id") and not row.get("completed_at"):
        # An executor can no longer report a result.  Do not infer that its
        # provider was not called: require an evidence-bearing reconciliation.
        return "reconciliation_required"
    expires = row.get("expires_at")
    if isinstance(expires, str):
        expires = datetime.fromisoformat(expires.replace("Z", "+00:00"))
    if isinstance(expires, datetime):
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= now:
            return "expired"
    if outcome == "retry":
        return "retry"
    decision = row.get("decision_kind")
    if decision:
        return str(decision)
    return "pending"


def project_request(row: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Produce the shared web/Flutter representation and its legal actions."""
    status = _status(row, now or datetime.now(timezone.utc))
    effect_parameters = row.get("effect_parameters")
    held_development_resolution = (
        row.get("effect_kind") == "development-launch"
        and isinstance(effect_parameters, Mapping)
        and isinstance(effect_parameters.get("lifecycle"), Mapping)
        and effect_parameters["lifecycle"].get("resolution", {}).get("status") != "RESOLVED"
    )
    if held_development_resolution:
        status = "clarification_required"
    actions = ["view-evidence"]
    if status == "pending":
        actions.extend(("approve", "hold", "reconcile"))
    elif status == "reconciliation_required":
        actions.append("reconcile")
    elif status in {"approve", "retry"}:
        actions.append("consume-by-executor")
    return {
        "request_id": row.get("request_id"),
        "status": status,
        "summary": row.get("summary"),
        "requested_by": row.get("requested_by"),
        "plan_commit": row.get("plan_commit"),
        "solution_hash": row.get("solution_hash"),
        "closure_hash": row.get("closure_hash"),
        "graph_id": row.get("graph_id"),
        "object_generation": row.get("object_generation"),
        "effect": {
            "kind": row.get("effect_kind"),
            "generation": row.get("effect_generation"),
            "hash": row.get("effect_hash"),
            "parameters": row.get("effect_parameters", {}),
        },
        "evidence": list(row.get("evidence") or ()),
        "expires_at": row.get("expires_at"),
        "decision": {
            "kind": row.get("decision_kind"),
            "by": row.get("decided_by"),
            "reason": row.get("decision_reason"),
            "reconciliation_evidence": list(row.get("reconciliation_evidence") or ()),
            "at": row.get("decided_at"),
        } if row.get("decision_kind") else None,
        "receipt_id": row.get("receipt_id"),
        "execution": {
            "receipt_id": row.get("receipt_id"),
            "handler_id": row.get("handler_id"),
            "executor_principal": row.get("executor_principal"),
            "started_at": row.get("started_at"),
            "completed_at": row.get("completed_at"),
            "outcome": row.get("outcome"),
            "evidence": list(row.get("execution_evidence") or ()),
            "rollback_receipt": row.get("rollback_receipt"),
            "detail": row.get("detail") or "",
        } if row.get("receipt_id") else None,
        "reconciliation_required": status in {"reconciliation_required", "ambiguous"},
        "legal_actions": actions,
        "authority": AUTHORITY_SCHEMA,
        "development": dict(effect_parameters["lifecycle"]) if (
            row.get("effect_kind") == "development-launch"
            and isinstance(effect_parameters, Mapping)
            and isinstance(effect_parameters.get("lifecycle"), Mapping)
        ) else None,
    }


def create_operator_console_router(
    store: AuthorityStore,
    *,
    current_plan_commit: Callable[[], str],
    load_solution: Callable[[str], Mapping[str, Any]],
    require_operator: Callable[[], Any],
    require_executor: Callable[[], Any],
    execute_effect: Callable[..., Any] | None = None,
    resolve_development: Callable[[Mapping[str, Any], str], tuple[Mapping[str, Any], AuthorityRequest]] | None = None,
) -> APIRouter:
    """Return one mountable router for UI, shared API, and authority writes."""
    router = APIRouter()
    router.include_router(create_authority_router(
        store,
        current_plan_commit=current_plan_commit,
        load_solution=load_solution,
        require_operator=require_operator,
        require_executor=require_executor,
        execute_effect=execute_effect,
    ))

    @router.get("/api/operator-console/discovery")
    def discovery(operator_identity: Any = Depends(require_operator)):
        require_authenticated_principal(operator_identity, PrincipalRole.OPERATOR)
        return {
            "schema": DISCOVERY_SCHEMA,
            "site": "/form/plan-authority",
            "projection_api": "/api/operator-console/requests",
            "authority_api": "/api/plan-authority",
            "authority_backend": AUTHORITY_SCHEMA,
            "clients": ["web", "flutter"],
            "navigation": NAVIGATION,
            "non_authority_surfaces": NON_AUTHORITY_SURFACES,
        }

    @router.get("/api/operator-console/requests")
    def requests(limit: int = 100, operator_identity: Any = Depends(require_operator)):
        require_authenticated_principal(operator_identity, PrincipalRole.OPERATOR)
        return {"schema": CONSOLE_SCHEMA, "requests": [project_request(row) for row in store.list(limit)]}

    @router.get("/api/operator-console/requests/{request_id}")
    def request(request_id: str, operator_identity: Any = Depends(require_operator)):
        require_authenticated_principal(operator_identity, PrincipalRole.OPERATOR)
        row = store.get(request_id)
        if row is None:
            raise HTTPException(404, "request not found")
        return {
            "schema": CONSOLE_SCHEMA,
            "request": project_request(row),
            "events": store.events(request_id),
        }

    @router.post("/api/operator-console/development-requests", status_code=201)
    def create_development_request(body: dict[str, Any], operator_identity: Any = Depends(require_operator)):
        principal = require_authenticated_principal(operator_identity, PrincipalRole.OPERATOR)
        if resolve_development is None:
            raise HTTPException(503, "development request resolver is not mounted")
        try:
            lifecycle, authority = resolve_development(body, principal.identity)
            row = store.create_request(authority)
            return {
                "schema": CONSOLE_SCHEMA,
                "request": project_request(row),
                "development": dict(lifecycle),
            }
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/form/plan-authority", response_class=HTMLResponse)
    def site(operator_identity: Any = Depends(require_operator)):
        require_authenticated_principal(operator_identity, PrincipalRole.OPERATOR)
        return HTMLResponse(_CLIENT.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})

    return router
