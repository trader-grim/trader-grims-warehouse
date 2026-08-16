"""Live PostgreSQL proof for the PlanAuthority lifecycle.

Set ``TGW_TEST_PLAN_AUTHORITY_DSN`` to a disposable database.  This test
creates only the plan_authority tables and truncates them between tests; it
never falls back to a production-like default DSN.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2
import pytest

from tgw.operator_console import project_request
from tgw.operator_console_host import ConfiguredAuthorityStore
from tgw.plan_authority import AuthorityDecision, AuthorityRequest, PostgresAuthorityStore
from tgw.plan_solver import solve

_DSN = os.environ.get("TGW_TEST_PLAN_AUTHORITY_DSN")
_COMMIT = "e" * 40
_EXECUTOR = "executor:postgres-canary"


def _database_is_available() -> bool:
    if not _DSN:
        return False
    try:
        connection = psycopg2.connect(_DSN)
        connection.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _database_is_available(),
    reason="set TGW_TEST_PLAN_AUTHORITY_DSN to a disposable PostgreSQL database",
)


def _solution():
    graph = {
        "schema": "tgw-plan/v2", "plan_commit": _COMMIT,
        "capabilities": ["authority.operator-decisions@1"],
        "providers": [{"id": "authority", "provides": ["authority.operator-decisions@1"]}],
        "observations": [],
        "target": {
            "id": "authority-postgres", "profile": "implementation",
            "minimum_state": "admitted", "required_capabilities": ["authority.operator-decisions@1"],
        },
    }
    native = solve(graph)
    return solve(graph, conformance_result={"available": True, "closure_hash": native["closure_hash"]})


def _request() -> AuthorityRequest:
    solution = _solution()
    return AuthorityRequest.create({
        "graph_id": "graph:postgres", "object_generation": "generation:postgres",
        "summary": "durable authority regression", "requested_by": "controller:postgres",
        "evidence": ["review:postgres"],
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        "effect": {
            "kind": "authority-canary", "generation": "canary:postgres",
            "parameters": {"canary_id": "canary:postgres", "purpose": "verify-plan-authority-roundtrip"},
        },
    }, solution=solution, current_plan_commit=_COMMIT)


@pytest.fixture(autouse=True)
def _initialize_schema():
    sql = Path(__file__).parents[1].joinpath("src/tgw/plan_authority.sql").read_text(encoding="utf-8")
    with psycopg2.connect(_DSN) as connection, connection.cursor() as cursor:
        cursor.execute(sql)
        cursor.execute(
            "TRUNCATE plan_authority_effect_receipts, plan_authority_decisions, "
            "plan_authority_events, plan_authority_requests CASCADE"
        )
    yield


def test_postgres_preserves_full_decision_history_and_retry_then_terminal_receipts():
    store = PostgresAuthorityStore(_DSN)
    request = _request()
    store.create_request(request)

    for kind in ("hold", "reconcile", "approve"):
        store.decide(AuthorityDecision.create(request.request_id, {
            "kind": kind, "decided_by": "operator:postgres", "reason": f"{kind} lifecycle proof",
        }))
    first = store.begin_execution(
        request.request_id, effect_hash=request.effect.effect_hash,
        generation=request.effect.generation, handler_id="authority-canary-receipt-only@1",
        executor_principal=_EXECUTOR,
    )
    retry = store.complete_execution(first["receipt_id"], outcome="retry", detail="temporary provider outage")
    assert retry["outcome"] == "retry"

    # Retry did not irrecoverably spend the approval; the same exact request may
    # make one new attempt, and the terminal ambiguity is then durable.
    second = store.begin_execution(
        request.request_id, effect_hash=request.effect.effect_hash,
        generation=request.effect.generation, handler_id="authority-canary-receipt-only@1",
        executor_principal=_EXECUTOR,
    )
    terminal = store.complete_execution(second["receipt_id"], outcome="ambiguous", detail="provider outcome unknown")
    assert terminal["outcome"] == "ambiguous"
    with pytest.raises(ValueError, match="terminal"):
        store.begin_execution(
            request.request_id, effect_hash=request.effect.effect_hash,
            generation=request.effect.generation, handler_id="authority-canary-receipt-only@1",
        executor_principal=_EXECUTOR,
        )

    row = store.get(request.request_id)
    assert row["decision_kind"] == "approve"
    assert row["outcome"] == "ambiguous"
    assert row["executor_principal"] == _EXECUTOR
    events = store.events(request.request_id)
    assert [event["event_type"] for event in events] == [
        "requested", "decided", "decided", "decided", "execution-started",
        "execution-completed", "execution-started", "execution-completed",
    ]


def test_postgres_reconcile_settles_an_active_attempt_as_auditable_ambiguity():
    store = PostgresAuthorityStore(_DSN)
    request = _request()
    store.create_request(request)
    store.decide(AuthorityDecision.create(request.request_id, {
        "kind": "approve", "decided_by": "operator:postgres", "reason": "approve exact canary",
    }))
    attempt = store.begin_execution(
        request.request_id, effect_hash=request.effect.effect_hash,
        generation=request.effect.generation, handler_id="authority-canary-receipt-only@1",
        executor_principal=_EXECUTOR,
    )
    assert store.get(request.request_id)["receipt_id"] == attempt["receipt_id"]
    assert project_request(store.get(request.request_id))["status"] == "reconciliation_required"
    with pytest.raises(ValueError, match="already executing"):
        store.begin_execution(
            request.request_id, effect_hash=request.effect.effect_hash,
            generation=request.effect.generation, handler_id="authority-canary-receipt-only@1",
        executor_principal=_EXECUTOR,
        )
    with pytest.raises(ValueError, match="requires evidence"):
        store.decide(AuthorityDecision.create(request.request_id, {
            "kind": "reconcile", "decided_by": "operator:postgres", "reason": "executor exited",
        }))
    settled = store.decide(AuthorityDecision.create(request.request_id, {
        "kind": "reconcile", "decided_by": "operator:postgres", "reason": "executor exited",
        "reconciliation_evidence": ["worker:exit-137", "provider:result-unknown"],
    }))
    assert settled["settled_receipt_id"] == attempt["receipt_id"]
    assert settled["execution_outcome"] == "ambiguous"
    row = store.get(request.request_id)
    assert row["decision_kind"] == "reconcile"
    assert row["outcome"] == "ambiguous"
    assert row["execution_evidence"] == ["provider:result-unknown", "worker:exit-137"]
    assert project_request(row)["status"] == "ambiguous"
    assert project_request(row)["reconciliation_required"] is True
    with pytest.raises(ValueError, match="terminal"):
        store.begin_execution(
            request.request_id, effect_hash=request.effect.effect_hash,
            generation=request.effect.generation, handler_id="authority-canary-receipt-only@1",
        executor_principal=_EXECUTOR,
        )
    assert [event["event_type"] for event in store.events(request.request_id)] == [
        "requested", "decided", "execution-started", "execution-reconciled", "decided",
    ]


def test_disposable_postgres_configured_host_canary_seam_retains_executor_provenance():
    """Exercise the late-bound host store only against an explicitly disposable DSN.

    This persists an authority-canary lifecycle fence but never invokes a
    provider, deploys, or creates an external credential.
    """
    store = ConfiguredAuthorityStore(lambda: {"postgres_dsn": _DSN})
    request = _request()
    store.create_request(request)
    store.decide(AuthorityDecision.create(request.request_id, {
        "kind": "approve", "decided_by": "operator:postgres", "reason": "configured host seam",
    }))
    attempt = store.begin_execution(
        request.request_id,
        effect_hash=request.effect.effect_hash,
        generation=request.effect.generation,
        handler_id="authority-canary-receipt-only@1",
        executor_principal=_EXECUTOR,
    )
    completed = store.complete_execution(attempt["receipt_id"], outcome="succeeded")
    assert completed["executor_principal"] == _EXECUTOR
    assert project_request(store.get(request.request_id))["execution"]["executor_principal"] == _EXECUTOR


def test_postgres_migrates_v1_single_decision_and_eager_consumption_schema():
    """An installed v1 schema upgrades without discarding old authority rows."""
    legacy = """
        DROP TABLE IF EXISTS plan_authority_events CASCADE;
        DROP TABLE IF EXISTS plan_authority_effect_receipts CASCADE;
        DROP TABLE IF EXISTS plan_authority_decisions CASCADE;
        DROP TABLE IF EXISTS plan_authority_requests CASCADE;
        CREATE TABLE plan_authority_requests (
            request_id text PRIMARY KEY, plan_commit text NOT NULL, solution_hash text NOT NULL,
            closure_hash text NOT NULL, graph_id text NOT NULL, object_generation text NOT NULL,
            effect_kind text NOT NULL, effect_generation text NOT NULL, effect_hash text NOT NULL,
            effect_parameters jsonb NOT NULL, summary text NOT NULL, evidence jsonb NOT NULL,
            requested_by text NOT NULL, expires_at timestamptz NOT NULL,
            requested_at timestamptz NOT NULL DEFAULT NOW()
        );
        CREATE TABLE plan_authority_decisions (
            decision_id text PRIMARY KEY,
            request_id text NOT NULL UNIQUE REFERENCES plan_authority_requests(request_id),
            decision_kind text CHECK (decision_kind IN ('approve','hold','reconcile')),
            decided_by text NOT NULL, reason text NOT NULL, decided_at timestamptz NOT NULL
        );
        CREATE TABLE plan_authority_effect_receipts (
            receipt_id uuid PRIMARY KEY,
            request_id text NOT NULL UNIQUE REFERENCES plan_authority_requests(request_id),
            effect_hash text NOT NULL, effect_generation text NOT NULL,
            consumed_at timestamptz NOT NULL
        );
        CREATE TABLE plan_authority_events (
            sequence bigserial PRIMARY KEY,
            request_id text NOT NULL REFERENCES plan_authority_requests(request_id),
            event_type text NOT NULL, details jsonb NOT NULL,
            occurred_at timestamptz NOT NULL DEFAULT NOW()
        );
    """
    schema = Path(__file__).parents[1].joinpath("src/tgw/plan_authority.sql").read_text(encoding="utf-8")
    with psycopg2.connect(_DSN) as connection, connection.cursor() as cursor:
        cursor.execute(legacy)
        cursor.execute(schema)
        cursor.execute("""
            SELECT conname FROM pg_constraint
             WHERE conrelid='plan_authority_decisions'::regclass AND contype='u'
        """)
        assert cursor.fetchall() == []
        cursor.execute("""
            SELECT column_name FROM information_schema.columns
             WHERE table_name='plan_authority_decisions'
               AND column_name='reconciliation_evidence'
        """)
        assert cursor.fetchall() == [("reconciliation_evidence",)]
        cursor.execute("""
            SELECT conname FROM pg_constraint
             WHERE conrelid='plan_authority_effect_receipts'::regclass AND contype='u'
        """)
        assert cursor.fetchall() == []
        cursor.execute("""
            SELECT column_name FROM information_schema.columns
             WHERE table_name='plan_authority_effect_receipts'
               AND column_name IN ('handler_id','executor_principal','started_at','completed_at','outcome','evidence','rollback_receipt','detail')
        """)
        assert {row[0] for row in cursor.fetchall()} == {
            "handler_id", "executor_principal", "started_at", "completed_at", "outcome", "evidence", "rollback_receipt", "detail",
        }
