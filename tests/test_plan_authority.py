import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from tgw.plan_authority import AuthorityDecision, AuthorityRequest, DecisionKind, EffectKind, TypedEffect, create_authority_router
from tgw.plan_solver import solve

COMMIT = "fb9fee3e9db756ad0f5071525e943794bf1dab9b"


def test_sql_effect_constraint_covers_every_registered_effect():
    sql = Path(__file__).parents[1].joinpath("src/tgw/plan_authority.sql").read_text(encoding="utf-8")
    for kind in EffectKind:
        assert f"'{kind.value}'" in sql
    assert "DROP CONSTRAINT IF EXISTS plan_authority_requests_effect_kind_check" in sql
    assert "ADD CONSTRAINT plan_authority_requests_effect_kind_check" in sql


def _solution():
    graph = {
        "schema": "tgw-plan/v2", "plan_commit": COMMIT,
        "capabilities": ["release@1"], "providers": [{"id": "release", "provides": ["release@1"]}],
        "observations": [],
        "target": {"id": "authority-fixture", "profile": "implementation", "minimum_state": "admitted", "required_capabilities": ["release@1"]},
    }
    native = solve(graph)
    return solve(graph, conformance_result={"available": True, "closure_hash": native["closure_hash"]})


def _data(**changes):
    value = {
        "graph_id": "runtime-graph-1", "object_generation": "generation-7",
        "summary": "Install reviewed candidate", "requested_by": "controller:1",
        "evidence": ["review:passed", "test:passed"],
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        "effect": {"kind": "coding-release", "generation": "candidate-tree-7", "parameters": {"candidate_commit": "abc123"}},
    }
    value.update(changes)
    return value


def test_request_is_immutable_and_bound_to_exact_plan_solution_generation_and_effect():
    request = AuthorityRequest.create(_data(), solution=_solution(), current_plan_commit=COMMIT)

    assert request.plan_commit == COMMIT
    assert request.solution_hash == _solution()["solution_hash"]
    assert request.object_generation == "generation-7"
    assert request.effect.effect_hash.startswith("effect:sha256:")
    assert request.request_id.startswith("request:sha256:")


def test_stale_or_nonconformant_solution_cannot_create_request():
    solution = _solution()
    with pytest.raises(Exception, match="registered Plan commit"):
        AuthorityRequest.create(_data(), solution=solution, current_plan_commit="new")

    held = dict(solution, conformance_verified=False, dispatchable=False)
    with pytest.raises(Exception):
        AuthorityRequest.create(_data(), solution=held, current_plan_commit=COMMIT)


@pytest.mark.parametrize("kind", ["shell", "generic-shell", "run-command"])
def test_generic_shell_is_not_a_registered_effect(kind):
    with pytest.raises(ValueError, match="generic shell"):
        TypedEffect.parse({"kind": kind, "generation": "g", "parameters": {}})


def test_shell_shaped_parameters_are_rejected_even_for_registered_effect():
    with pytest.raises(ValueError, match="generic shell"):
        TypedEffect.parse({"kind": "coding-release", "generation": "g", "parameters": {"command": "rm -rf anything"}})


def test_decision_objects_cover_approve_hold_and_reconcile_and_are_hash_bound():
    now = datetime(2026, 8, 11, tzinfo=timezone.utc)
    decisions = [AuthorityDecision.create("request:1", {"kind": kind.value, "decided_by": "Dave", "reason": "exact choice"}, now=now) for kind in DecisionKind]

    assert [item.kind for item in decisions] == list(DecisionKind)
    assert len({item.decision_id for item in decisions}) == 3


def test_one_shot_generation_match_is_atomic_under_concurrency():
    lock = threading.Lock()
    consumed = False
    outcomes = []

    def consume(generation):
        nonlocal consumed
        with lock:
            if consumed or generation != "candidate-tree-7":
                outcomes.append(False)
            else:
                consumed = True
                outcomes.append(True)

    threads = [threading.Thread(target=consume, args=("candidate-tree-7",)) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 1


def test_http_api_is_one_projection_over_injected_canonical_store():
    class Store:
        def __init__(self):
            self.request = None
            self.event_rows = []

        def create_request(self, request):
            self.request = request
            self.event_rows.append({"event_type": "requested"})
            return {"request_id": request.request_id}

        def list(self, limit=100):
            return [] if self.request is None else [{"request_id": self.request.request_id}]

        def get(self, request_id):
            return None if self.request is None or request_id != self.request.request_id else {"request_id": request_id}

        def events(self, request_id):
            return self.event_rows

        def decide(self, decision):
            self.decision = decision
            self.event_rows.append({"event_type": "decided", "kind": decision.kind.value})
            return {"request_id": decision.request_id, "decision_kind": decision.kind.value}

        def consume(self, request_id, *, effect_hash, generation):
            return {"request_id": request_id, "effect_hash": effect_hash, "generation": generation}

    store = Store()
    solution = _solution()
    app = FastAPI()
    app.include_router(
        create_authority_router(
            store,
            current_plan_commit=lambda: COMMIT,
            load_solution=lambda identity: solution if identity == solution["solution_hash"] else {},
            require_operator=lambda: "operator:authenticated",
            require_executor=lambda: "executor",
        )
    )
    client = TestClient(app)
    body = {**_data(requested_by="caller:spoofed"), "solution_hash": solution["solution_hash"]}

    created = client.post("/api/plan-authority/requests", json=body)
    assert created.status_code == 201
    request_id = created.json()["request"]["request_id"]
    assert store.request.requested_by == "operator:authenticated"
    assert client.get("/api/plan-authority/requests").json()["requests"] == [{"request_id": request_id}]
    decision = client.post(
        f"/api/plan-authority/requests/{request_id}/decisions",
        json={"kind": "hold", "decided_by": "caller:spoofed", "reason": "needs reconciliation"},
    )
    assert decision.json()["request"]["decision_kind"] == "hold"
    assert store.decision.decided_by == "operator:authenticated"
    detail = client.get(f"/api/plan-authority/requests/{request_id}").json()
    assert [event["event_type"] for event in detail["events"]] == ["requested", "decided"]


def test_consume_requires_a_distinct_executor_and_uses_only_the_stored_typed_effect():
    effect = TypedEffect.parse({
        "kind": "authority-canary", "generation": "canary-1",
        "parameters": {"canary_id": "canary:1", "purpose": "verify-plan-authority-roundtrip"},
    })

    class Store:
        row = {
            "request_id": "request:1", "effect_kind": effect.kind.value,
            "effect_generation": effect.generation, "effect_parameters": effect.parameters,
            "effect_hash": effect.effect_hash,
        }

        def get(self, request_id):
            return self.row if request_id == "request:1" else None

        def list(self, limit=100):
            return []

        def events(self, request_id):
            return []

        def create_request(self, request):  # pragma: no cover - route contract
            raise AssertionError

        def decide(self, decision):  # pragma: no cover - route contract
            raise AssertionError

    invoked = []

    def require_operator(role: str | None = Header(default=None)):
        if role != "operator":
            raise HTTPException(401, "operator required")

    def require_executor(role: str | None = Header(default=None)):
        if role != "executor":
            raise HTTPException(401, "executor required")

    def registered_controller(*, request_id, effect):
        invoked.append((request_id, effect))
        return {"receipt_id": "attempt:1", "outcome": "succeeded"}

    app = FastAPI()
    app.include_router(create_authority_router(
        Store(), current_plan_commit=lambda: COMMIT, load_solution=lambda _: _solution(),
        require_operator=require_operator, require_executor=require_executor,
        execute_effect=registered_controller,
    ))
    client = TestClient(app)

    # A normal operator is not an executor, even if the request is already approved.
    assert client.post("/api/plan-authority/requests/request:1/consume", json={}, headers={"role": "operator"}).status_code == 401
    # An executor cannot smuggle a changed effect/generation around the controller.
    assert client.post(
        "/api/plan-authority/requests/request:1/consume",
        json={"effect_hash": "effect:attacker", "generation": "other"}, headers={"role": "executor"},
    ).status_code == 409
    executed = client.post(
        "/api/plan-authority/requests/request:1/consume", json={}, headers={"role": "executor"},
    )
    assert executed.status_code == 200
    assert executed.json()["receipt"]["outcome"] == "succeeded"
    assert invoked == [("request:1", effect)]

    unavailable = FastAPI()
    unavailable.include_router(create_authority_router(
        Store(), current_plan_commit=lambda: COMMIT, load_solution=lambda _: _solution(),
        require_operator=require_operator, require_executor=require_executor,
    ))
    assert TestClient(unavailable).post(
        "/api/plan-authority/requests/request:1/consume", json={}, headers={"role": "executor"},
    ).status_code == 409
