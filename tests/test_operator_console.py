from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from tgw.operator_console import create_operator_console_router, project_request
from tgw.operator_console_plugin import (
    OperatorConsoleMount,
    mount_operator_console,
)
from tgw.plan_authority import AuthorityPrincipal, PrincipalRole

OPERATOR = AuthorityPrincipal("operator:fixture-alice", PrincipalRole.OPERATOR, "test-session")
EXECUTOR = AuthorityPrincipal("executor:fixture-runner", PrincipalRole.EXECUTOR, "test-credential")


class Store:
    def __init__(self, row):
        self.row = row

    def list(self, limit=100):
        return [self.row]

    def get(self, request_id):
        return self.row if request_id == self.row["request_id"] else None

    def events(self, request_id):
        return [{"event_type": "requested"}]

    def create_request(self, request):  # pragma: no cover - authority router contract
        raise AssertionError

    def decide(self, decision):  # pragma: no cover - authority router contract
        raise AssertionError

    def consume(self, request_id, *, effect_hash, generation):  # pragma: no cover
        raise AssertionError


def _row(**updates):
    row = {
        "request_id": "request:sha256:abc",
        "summary": "Release reviewed generation",
        "plan_commit": "f" * 40,
        "solution_hash": "solution:sha256:abc",
        "closure_hash": "closure:sha256:def",
        "graph_id": "graph:1",
        "object_generation": "object:1",
        "effect_kind": "coding-release",
        "effect_generation": "generation-1",
        "effect_hash": "effect:sha256:ghi",
        "effect_parameters": {},
        "evidence": ["receipt:1"],
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        "decision_kind": None,
        "receipt_id": None,
    }
    row.update(updates)
    return row


def _client(row):
    app = FastAPI()
    app.include_router(create_operator_console_router(
        Store(row), current_plan_commit=lambda: "f" * 40,
        load_solution=lambda _: {}, require_operator=lambda: OPERATOR,
        require_executor=lambda: EXECUTOR,
    ))
    return TestClient(app)


def test_projection_reports_status_evidence_and_only_legal_actions():
    pending = project_request(_row())
    assert pending["status"] == "pending"
    assert pending["evidence"] == ["receipt:1"]
    assert pending["legal_actions"] == ["view-evidence", "approve", "hold", "reconcile"]
    assert project_request(_row(decision_kind="approve"))["legal_actions"] == [
        "view-evidence", "consume-by-executor",
    ]
    assert project_request(_row(
        receipt_id="receipt:done", completed_at=datetime.now(timezone.utc), outcome="succeeded",
    ))["status"] == "succeeded"
    active = project_request(_row(receipt_id="receipt:active"))
    assert active["status"] == "reconciliation_required"
    assert active["reconciliation_required"] is True
    assert active["legal_actions"] == ["view-evidence", "reconcile"]
    ambiguous = project_request(_row(
        receipt_id="receipt:ambiguous", completed_at=datetime.now(timezone.utc), outcome="ambiguous",
    ))
    assert ambiguous["status"] == "ambiguous"
    assert ambiguous["reconciliation_required"] is True


def test_shared_projection_contains_exact_scope_solution_decision_and_receipt_provenance():
    projection = project_request(_row(
        requested_by="operator:alice",
        decision_kind="approve",
        decided_by="operator:bob",
        decision_reason="reviewed exact scope",
        reconciliation_evidence=["reconcile:1"],
        decided_at=datetime.now(timezone.utc),
        receipt_id="receipt:exact",
        handler_id="authority-canary-receipt-only@1",
        executor_principal="executor:runner",
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        outcome="succeeded",
        execution_evidence=["provider:readback"],
        rollback_receipt="rollback:unused",
    ))
    assert projection["effect"] == {
        "kind": "coding-release", "generation": "generation-1",
        "hash": "effect:sha256:ghi", "parameters": {},
    }
    assert projection["solution_hash"] == "solution:sha256:abc"
    assert projection["graph_id"] == "graph:1"
    assert projection["object_generation"] == "object:1"
    assert projection["decision"]["by"] == "operator:bob"
    assert projection["execution"]["executor_principal"] == "executor:runner"
    assert projection["execution"]["receipt_id"] == "receipt:exact"


def test_web_and_flutter_normal_navigation_name_every_authority_detail_and_control():
    web = Path(__file__).parents[1].joinpath("src/tgw/static/plan_console.html").read_text(encoding="utf-8")
    flutter = Path(__file__).parents[1].joinpath(
        "apps/tgw_app/lib/features/review/plan_authority_screen.dart",
    ).read_text(encoding="utf-8")
    for label in (
        "Exact effect scope", "Parameters", "Effect hash", "Bound Plan solution",
        "Solution hash", "Closure hash", "Graph", "Object generation", "Evidence",
        "Decision", "Execution / receipt provenance", "Executor principal",
        "Authenticated operator decision", "reconciliation_evidence",
    ):
        assert label in web
        assert label in flutter
    assert "/api/plan-authority/requests/${encodeURIComponent(requestId)}/decisions" in web
    assert "decidePlanAuthorityRequest" in flutter


def test_mount_exposes_shared_api_site_and_canonical_authority_router():
    client = _client(_row())
    assert client.get("/api/operator-console/requests").json()["requests"][0]["status"] == "pending"
    assert client.get("/api/operator-console/requests/request:sha256:abc").json()["events"]
    assert client.get("/api/plan-authority/requests").status_code == 200
    site = client.get("/form/plan-authority")
    assert site.status_code == 200
    assert "Only records from PlanAuthority grant authority" in site.text


def test_discovery_names_one_backend_and_non_authority_surfaces():
    discovery = _client(_row()).get("/api/operator-console/discovery").json()
    assert discovery["authority_api"] == "/api/plan-authority"
    assert discovery["site"] == "/form/plan-authority"
    assert discovery["clients"] == ["web", "flutter"]
    assert discovery["navigation"] == {
        "id": "plan-authority", "label": "Plan Authority",
        "href": "/form/plan-authority", "group": "Admin", "order": 30,
    }
    assert discovery["dynamic_surfaces"] == {
        "available": False, "schema": "tgw-dynamic-surface/v1",
    }


def test_dynamic_surface_api_uses_host_auth_and_mounted_controller():
    app = FastAPI()
    seen = {}
    app.include_router(create_operator_console_router(
        Store(_row()), current_plan_commit=lambda: "f" * 40,
        load_solution=lambda _: {}, require_operator=lambda: OPERATOR,
        require_executor=lambda: EXECUTOR,
        load_dynamic_surface=lambda request_id: {
            "schema": "tgw-dynamic-surface/v1", "request_id": request_id,
        },
        submit_dynamic_surface_decision=lambda request_id, body, operator: (
            seen.update(request_id=request_id, body=body, operator=operator)
            or {"status": "RECORDED"}
        ),
    ))
    client = TestClient(app)
    assert client.get("/api/operator-console/requests/request:sha256:abc/surface").json()["request_id"] == "request:sha256:abc"
    response = client.post(
        "/api/operator-console/requests/request:sha256:abc/surface/decisions",
        json={"surface_hash": "sha256:value"},
    )
    assert response.json()["status"] == "RECORDED"
    assert seen["operator"] == OPERATOR.identity


def test_shared_authenticated_site_navigation_links_canonical_authority():
    nav = Path(__file__).parents[1].joinpath("src/tgw/static/nav.js").read_text(encoding="utf-8")
    assert nav.count('href="/form/plan-authority"') == 1
    discovery = _client(_row()).get("/api/operator-console/discovery").json()
    assert discovery["navigation"]["href"] in nav
    assert {item["path"] for item in discovery["non_authority_surfaces"]} >= {
        "/form/approvals", "/api/action-approvals", "/form/runs", "/form/todos",
        "/form/pp-clip", "/api/items/*",
    }


def test_plugin_mount_uses_host_auth_and_returns_flutter_json():
    app = FastAPI()

    def host_auth(authorization: str | None = Header(default=None)):
        if authorization != "Bearer shared-host-token":
            raise HTTPException(401, "host auth rejected")
        return OPERATOR

    def executor_auth(authorization: str | None = Header(default=None)):
        if authorization != "Bearer executor-token":
            raise HTTPException(401, "executor auth rejected")
        return EXECUTOR

    config = OperatorConsoleMount(
        store=Store(_row()), current_plan_commit=lambda: "f" * 40,
        load_solution=lambda _: {}, require_operator=host_auth,
        require_executor=executor_auth,
    )
    mount_operator_console(app, config)
    client = TestClient(app)
    assert client.get("/api/operator-console/requests").status_code == 401
    response = client.get(
        "/api/operator-console/requests",
        headers={"Authorization": "Bearer shared-host-token", "Accept": "application/json"},
    )
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert payload["schema"] == "tgw-operator-console/v1"
    assert isinstance(payload["requests"][0]["expires_at"], str)


def test_plugin_rejects_duplicate_mount_or_route_shadowing():
    config = OperatorConsoleMount(
        store=Store(_row()), current_plan_commit=lambda: "f" * 40,
        load_solution=lambda _: {}, require_operator=lambda: OPERATOR,
        require_executor=lambda: EXECUTOR,
    )
    app = FastAPI()
    mount_operator_console(app, config)
    try:
        mount_operator_console(app, config)
    except RuntimeError as exc:
        assert "already mounted" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("duplicate mount was accepted")

    colliding = FastAPI()

    @colliding.get("/form/plan-authority")
    def legacy():
        return "legacy"

    try:
        mount_operator_console(colliding, config)
    except RuntimeError as exc:
        assert "route collision" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("route shadowing was accepted")
