from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tgw.effect_handlers import AuthorityEffectController, EffectOutcome, TypedEffectHandlerRegistry
from tgw.plan_authority import create_authority_router
from tgw.plan_solver import solve

COMMIT = "f" * 40


def _solution():
    graph = {
        "schema": "tgw-plan/v2", "plan_commit": COMMIT,
        "capabilities": ["authority.operator-decisions@1"],
        "providers": [{"id": "authority", "provides": ["authority.operator-decisions@1"]}],
        "observations": [],
        "target": {"id": "w10", "profile": "implementation", "minimum_state": "admitted", "required_capabilities": ["authority.operator-decisions@1"]},
    }
    native = solve(graph)
    return solve(graph, conformance_result={"available": True, "closure_hash": native["closure_hash"]})


class MemoryAuthority:
    """Faithful one-shot test double; production remains PostgreSQL-backed."""

    def __init__(self):
        self.requests = {}
        self.decisions = {}
        self.attempts = {}

    def create_request(self, request):
        self.requests[request.request_id] = request
        return {"request_id": request.request_id}

    def decide(self, decision):
        prior = self.decisions.get(decision.request_id, [])
        allowed = {
            None: {"approve", "hold", "reconcile"},
            "hold": {"approve", "reconcile"},
            "reconcile": {"approve", "hold"},
            "approve": {"hold", "reconcile"},
        }
        current = prior[-1] if prior else None
        if decision.kind.value not in allowed[current]:
            raise ValueError("invalid transition")
        self.decisions.setdefault(decision.request_id, []).append(decision.kind.value)
        return {"request_id": decision.request_id, "decision_kind": decision.kind.value}

    def begin_execution(self, request_id, *, effect_hash, generation, handler_id):
        request = self.requests[request_id]
        attempts = self.attempts.setdefault(request_id, [])
        if self.decisions.get(request_id, [])[-1:] != ["approve"] or any(
            attempt.get("outcome") != "retry" for attempt in attempts
        ):
            raise ValueError("effect is not approved, terminal, or already executing")
        if request.effect.effect_hash != effect_hash or request.effect.generation != generation:
            raise ValueError("effect mismatch")
        receipt_id = "canary-authority:" + request_id + ":" + str(len(attempts) + 1)
        attempts.append({"receipt_id": receipt_id, "handler_id": handler_id})
        return {"receipt_id": receipt_id}

    def complete_execution(self, receipt_id, *, outcome, evidence=(), rollback_receipt=None, detail=""):
        for attempts in self.attempts.values():
            for attempt in attempts:
                if attempt["receipt_id"] == receipt_id:
                    attempt.update({"outcome": outcome, "evidence": tuple(evidence), "rollback_receipt": rollback_receipt, "detail": detail})
                    return attempt
        raise ValueError("unknown execution receipt")

    def get(self, request_id):
        request = self.requests.get(request_id)
        if request is None:
            return None
        return {
            "request_id": request.request_id,
            "effect_kind": request.effect.kind.value,
            "effect_generation": request.effect.generation,
            "effect_hash": request.effect.effect_hash,
            "effect_parameters": request.effect.parameters,
        }

    def list(self, limit=100):
        return []

    def events(self, request_id):
        return []


def _body(solution, canary_id):
    return {
        "solution_hash": solution["solution_hash"], "graph_id": "plan-graph:w10",
        "object_generation": canary_id, "summary": "Harmless authority roundtrip canary",
        "requested_by": "controller:w10", "evidence": ["w09:installed"],
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        "effect": {"kind": "authority-canary", "generation": canary_id, "parameters": {
            "canary_id": canary_id, "purpose": "verify-plan-authority-roundtrip",
        }},
    }


def test_request_decision_consume_execution_receipt_and_hold_reconcile_paths():
    store, solution = MemoryAuthority(), _solution()
    registry = TypedEffectHandlerRegistry(release_install=Mock(), release_rollback=Mock(), flake_push=Mock(), flake_switch_record=Mock(), dependency_resubmit=Mock())
    controller = AuthorityEffectController(registry, store)
    app = FastAPI()
    app.include_router(create_authority_router(
        store, current_plan_commit=lambda: COMMIT, load_solution=lambda _: solution,
        require_operator=lambda: None, require_executor=lambda: None,
        execute_effect=controller.execute,
    ))
    client = TestClient(app)

    created = client.post("/api/plan-authority/requests", json=_body(solution, "w10-canary-approved"))
    request_id = created.json()["request"]["request_id"]
    assert client.post(f"/api/plan-authority/requests/{request_id}/decisions", json={"kind": "approve", "decided_by": "Dave", "reason": "run harmless canary"}).status_code == 200
    consumed = client.post(f"/api/plan-authority/requests/{request_id}/consume", json={})
    assert consumed.status_code == 200
    receipt = consumed.json()["receipt"]
    assert receipt["outcome"] == EffectOutcome.SUCCEEDED
    assert receipt["evidence"][0].startswith("authority-canary:sha256:")
    assert client.post(f"/api/plan-authority/requests/{request_id}/consume", json={}).status_code == 409

    for decision in ("hold", "reconcile"):
        body = _body(solution, f"w10-canary-{decision}")
        held_id = client.post("/api/plan-authority/requests", json=body).json()["request"]["request_id"]
        response = client.post(
            f"/api/plan-authority/requests/{held_id}/decisions",
            json={"kind": decision, "decided_by": "Dave", "reason": f"{decision} path"},
        )
        assert response.json()["request"]["decision_kind"] == decision
        assert client.post(f"/api/plan-authority/requests/{held_id}/consume", json={}).status_code == 409
