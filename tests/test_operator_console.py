from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from tgw.operator_console import create_operator_console_router, project_request
from tgw.operator_console_plugin import (
    OperatorConsoleMount,
    mount_operator_console,
)


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
        load_solution=lambda _: {}, require_operator=lambda: "operator",
        require_executor=lambda: "executor",
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
    assert project_request(_row(receipt_id="receipt:done"))["status"] == "consumed"


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

    config = OperatorConsoleMount(
        store=Store(_row()), current_plan_commit=lambda: "f" * 40,
        load_solution=lambda _: {}, require_operator=host_auth,
        require_executor=host_auth,
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
        load_solution=lambda _: {}, require_operator=lambda: None,
        require_executor=lambda: None,
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
