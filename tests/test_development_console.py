import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tgw.development_console import resolve_request
from tgw.development_launch import DevelopmentLaunchError, validate_development_launch
from tgw.operator_console import create_operator_console_router, project_request
from tgw.plan_authority import AuthorityPrincipal, PrincipalRole

PLAN = "a" * 40
SOURCE = "b" * 40
CLOSURE_HASH = "sha256:" + "d" * 64


def digest(value):
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def solution():
    value = {
        "schema": "tgw-plan-solution/v1",
        "plan_commit": PLAN,
        "closure_hash": CLOSURE_HASH,
        "complete": True,
        "conformance_verified": True,
        "dispatchable": True,
        "unresolved": [],
        "root": {"id": "PLAN-SITE", "profile": "reviewed", "minimum_state": "reviewed"},
        "phase_order": [["foundation"], ["site", "mobile"]],
        "selected_providers": ["foundation", "site", "mobile"],
    }
    return {**value, "solution_hash": digest(value)}


def freshness():
    body = {
        "schema": "tgw-w18-projection-refresh-receipt/v1",
        "status": "FRESH",
        "desired": {},
        "observed": {},
        "actors": [],
        "refresh": {},
        "lease": {},
        "reasons": [],
        "activation": "declarative-only",
    }
    return {**body, "receipt_hash": digest(body)}


def registry():
    return {
        "schema": "tgw-harness-provider-registry/v1",
        "id": "providers@1",
        "providers": [{
            "id": "replaceable-runner",
            "qualified_roles": ["implementation", "independent-review", "controller-verification"],
        }],
    }


def body(**extra):
    return {
        "schema": "tgw-development-console-request/v1",
        "original_request": "Build the site",
        "scope": "site and mobile client",
        "constraints": ["preserve data"],
        "effect_limits": ["source and candidate installation"],
        **extra,
    }


def resolved(**extra):
    return resolve_request(
        body=body(**extra), solution=solution(), plan_commit=PLAN,
        requested_by="operator:dave", source_commit=SOURCE,
        freshness=freshness(), provider_registry=registry(),
    )


def test_resolved_cards_are_dependency_ordered_and_harness_neutral():
    lifecycle, authority = resolved()
    assert authority.effect.kind.value == "development-launch"
    assert lifecycle["resolution"]["root"] == {"kind": "Plan", "id": "PLAN-SITE"}
    assert [card["unit"] for card in lifecycle["launch_cards"][:4]] == [
        "foundation", "foundation", "foundation", "site",
    ]
    roles = {card["role"] for card in lifecycle["launch_cards"]}
    assert roles == {"implementation", "independent-review", "controller-verification"}
    assert all(card["provider_selection"]["selected_provider"] is None for card in lifecycle["launch_cards"])
    serialized = json.dumps(lifecycle).lower()
    assert all(product not in serialized for product in ("codex", "claude", "aider", "hermes"))
    validate_development_launch(authority.effect.parameters)


def test_narrow_root_without_an_exact_solution_is_retained_but_not_approvable():
    lifecycle, authority = resolved(root={"kind": "PP", "id": "PP-OTHER"})
    assert lifecycle["resolution"]["status"] == "CLARIFICATION_REQUIRED"
    assert lifecycle["launch_cards"] == []
    projected = project_request({
        "request_id": authority.request_id,
        "effect_kind": "development-launch",
        "effect_parameters": authority.effect.parameters,
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
    })
    assert projected["status"] == "clarification_required"
    assert projected["legal_actions"] == ["view-evidence"]
    with pytest.raises(DevelopmentLaunchError, match="no resolved launch closure"):
        validate_development_launch(authority.effect.parameters)


def test_provider_selection_or_lifecycle_tamper_fails_closed():
    _, authority = resolved()
    parameters = json.loads(json.dumps(authority.effect.parameters))
    parameters["lifecycle"]["launch_cards"][0]["provider_selection"]["selected_provider"] = "chat-picked-runner"
    with pytest.raises(DevelopmentLaunchError, match="lifecycle hash"):
        validate_development_launch(parameters)


class Store:
    def __init__(self):
        self.rows = []

    def create_request(self, request):
        row = {
            **request.__dict__,
            "effect_kind": request.effect.kind.value,
            "effect_generation": request.effect.generation,
            "effect_hash": request.effect.effect_hash,
            "effect_parameters": request.effect.parameters,
        }
        self.rows.append(row)
        return row

    def list(self, limit=100):
        return self.rows[:limit]

    def get(self, request_id):
        return next((row for row in self.rows if row["request_id"] == request_id), None)

    def events(self, request_id):
        return []


def test_http_creation_uses_the_same_authority_store_and_projection():
    store = Store()
    def operator():
        return AuthorityPrincipal("operator:dave", PrincipalRole.OPERATOR, "test")

    def executor():
        return AuthorityPrincipal("executor:worker", PrincipalRole.EXECUTOR, "test")

    app = FastAPI()
    app.include_router(create_operator_console_router(
        store,
        current_plan_commit=lambda: PLAN,
        load_solution=lambda _: solution(),
        require_operator=operator,
        require_executor=executor,
        resolve_development=lambda request, principal: resolve_request(
            body=request, solution=solution(), plan_commit=PLAN,
            requested_by=principal, source_commit=SOURCE,
            freshness=freshness(), provider_registry=registry(),
        ),
    ))
    response = TestClient(app).post("/api/operator-console/development-requests", json=body())
    assert response.status_code == 201
    value = response.json()
    assert value["request"]["effect"]["kind"] == "development-launch"
    assert value["request"]["development"]["request"]["original_request"] == "Build the site"
    assert len(store.rows) == 1
