"""Single immutable authority for Plan-bound requests, decisions, and effects."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from typing import Any, Callable, Mapping, Protocol, Sequence

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException

from tgw.plan_solver import validate_for_dispatch

AUTHORITY_SCHEMA = "tgw-plan-authority/v1"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()


def _identity(kind: str, value: Any) -> str:
    return f"{kind}:sha256:" + sha256(_canonical(value)).hexdigest()


class DecisionKind(str, Enum):
    APPROVE = "approve"
    HOLD = "hold"
    RECONCILE = "reconcile"


class EffectKind(str, Enum):
    CODING_RELEASE = "coding-release"
    BOUNDED_FLAKE_PUSH = "bounded-flake-push"
    FLAKE_SWITCH_RECORD_ONLY = "flake-switch-record-only"
    DEPENDENCY_RESUBMIT = "dependency-resubmit"
    AUTHORITY_CANARY = "authority-canary"
    APPROVAL_PLATFORM_BOOTSTRAP_DEPLOYMENT = "approval-platform-bootstrap-deployment"


@dataclass(frozen=True)
class TypedEffect:
    kind: EffectKind
    generation: str
    parameters: Mapping[str, Any]

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> "TypedEffect":
        try:
            kind = EffectKind(value["kind"])
        except (KeyError, ValueError) as exc:
            raise ValueError("effect kind is not registered; generic shell effects are forbidden") from exc
        generation = value.get("generation")
        parameters = value.get("parameters", {})
        if not isinstance(generation, str) or not generation:
            raise ValueError("effect generation is required")
        if not isinstance(parameters, Mapping):
            raise ValueError("effect parameters must be an object")
        forbidden = {"shell", "command", "argv"}.intersection(parameters)
        if forbidden:
            raise ValueError("generic shell parameters are forbidden")
        return cls(kind, generation, dict(parameters))

    @property
    def effect_hash(self) -> str:
        return _identity("effect", {"kind": self.kind.value, "generation": self.generation, "parameters": self.parameters})


@dataclass(frozen=True)
class AuthorityRequest:
    request_id: str
    plan_commit: str
    solution_hash: str
    closure_hash: str
    graph_id: str
    object_generation: str
    effect: TypedEffect
    summary: str
    evidence: tuple[str, ...]
    requested_by: str
    expires_at: datetime

    @classmethod
    def create(
        cls,
        data: Mapping[str, Any],
        *,
        solution: Mapping[str, Any],
        current_plan_commit: str,
        now: datetime | None = None,
    ) -> "AuthorityRequest":
        validate_for_dispatch(solution, current_plan_commit=current_plan_commit)
        now = now or datetime.now(timezone.utc)
        expires_at = datetime.fromisoformat(str(data["expires_at"]).replace("Z", "+00:00"))
        if expires_at.tzinfo is None or expires_at <= now:
            raise ValueError("expires_at must be a future timezone-aware timestamp")
        required = ("graph_id", "object_generation", "summary", "requested_by")
        if any(not isinstance(data.get(name), str) or not data[name].strip() for name in required):
            raise ValueError("graph_id, object_generation, summary, and requested_by are required")
        evidence = data.get("evidence", ())
        if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)) or not all(isinstance(item, str) and item for item in evidence):
            raise ValueError("evidence must be a sequence of identities")
        effect = TypedEffect.parse(data.get("effect", {}))
        payload = {
            "plan_commit": current_plan_commit,
            "solution_hash": solution["solution_hash"],
            "closure_hash": solution["closure_hash"],
            "graph_id": data["graph_id"],
            "object_generation": data["object_generation"],
            "effect_hash": effect.effect_hash,
            "summary": data["summary"].strip(),
            "evidence": sorted(set(evidence)),
            "requested_by": data["requested_by"].strip(),
            "expires_at": expires_at.isoformat(),
        }
        return cls(
            request_id=_identity("request", payload),
            plan_commit=current_plan_commit,
            solution_hash=str(solution["solution_hash"]),
            closure_hash=str(solution["closure_hash"]),
            graph_id=data["graph_id"],
            object_generation=data["object_generation"],
            effect=effect,
            summary=payload["summary"],
            evidence=tuple(payload["evidence"]),
            requested_by=payload["requested_by"],
            expires_at=expires_at,
        )


@dataclass(frozen=True)
class AuthorityDecision:
    decision_id: str
    request_id: str
    kind: DecisionKind
    decided_by: str
    reason: str
    reconciliation_evidence: tuple[str, ...]
    decided_at: datetime

    @classmethod
    def create(cls, request_id: str, data: Mapping[str, Any], *, now: datetime | None = None) -> "AuthorityDecision":
        kind = DecisionKind(data.get("kind"))
        decided_by, reason = data.get("decided_by"), data.get("reason")
        if not isinstance(decided_by, str) or not decided_by or not isinstance(reason, str) or not reason:
            raise ValueError("decided_by and reason are required")
        evidence = data.get("reconciliation_evidence", ())
        if (
            not isinstance(evidence, Sequence)
            or isinstance(evidence, (str, bytes))
            or not all(isinstance(item, str) and item for item in evidence)
        ):
            raise ValueError("reconciliation_evidence must be a sequence of identities")
        if kind is not DecisionKind.RECONCILE and evidence:
            raise ValueError("reconciliation_evidence is only valid for reconcile decisions")
        decided_at = now or datetime.now(timezone.utc)
        normalized_evidence = tuple(sorted(set(evidence)))
        payload = {
            "request_id": request_id, "kind": kind.value, "decided_by": decided_by,
            "reason": reason, "reconciliation_evidence": normalized_evidence,
            "decided_at": decided_at.isoformat(),
        }
        return cls(_identity("decision", payload), request_id, kind, decided_by, reason, normalized_evidence, decided_at)


class AuthorityStore(Protocol):
    def create_request(self, request: AuthorityRequest) -> Mapping[str, Any]: ...
    def decide(self, decision: AuthorityDecision) -> Mapping[str, Any]: ...
    def begin_execution(self, request_id: str, *, effect_hash: str, generation: str, handler_id: str) -> Mapping[str, Any]: ...
    def complete_execution(
        self,
        receipt_id: str,
        *,
        outcome: str,
        evidence: Sequence[str] = (),
        rollback_receipt: str | None = None,
        detail: str = "",
    ) -> Mapping[str, Any]: ...
    def get(self, request_id: str) -> Mapping[str, Any] | None: ...
    def list(self, limit: int = 100) -> Sequence[Mapping[str, Any]]: ...
    def events(self, request_id: str) -> Sequence[Mapping[str, Any]]: ...


class PostgresAuthorityStore:
    """Canonical durable store for the authority and execution state machine.

    An authority is *not* consumed before a provider runs.  ``begin_execution``
    records an exclusive durable attempt, and ``complete_execution`` records the
    provider outcome in the same transaction as the corresponding authority
    transition.  A process that dies between the two leaves an explicit active
    attempt, which is intentionally non-retryable until reconciliation instead
    of silently spending an approval or risking a duplicate provider call.
    """

    def __init__(self, dsn: str):
        self.dsn = dsn

    def _connection(self):
        return psycopg2.connect(self.dsn)

    def create_request(self, request: AuthorityRequest) -> Mapping[str, Any]:
        with self._connection() as con, con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """INSERT INTO plan_authority_requests
                (request_id, plan_commit, solution_hash, closure_hash, graph_id, object_generation,
                 effect_kind, effect_generation, effect_hash, effect_parameters, summary, evidence,
                 requested_by, expires_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s,%s)
                ON CONFLICT (request_id) DO NOTHING RETURNING *""",
                (request.request_id, request.plan_commit, request.solution_hash, request.closure_hash,
                 request.graph_id, request.object_generation, request.effect.kind.value,
                 request.effect.generation, request.effect.effect_hash, json.dumps(request.effect.parameters),
                 request.summary, json.dumps(list(request.evidence)), request.requested_by, request.expires_at),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError("request already exists")
            self._event(cur, request.request_id, "requested", {"request_id": request.request_id})
            return dict(row)

    def decide(self, decision: AuthorityDecision) -> Mapping[str, Any]:
        with self._connection() as con, con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT expires_at FROM plan_authority_requests WHERE request_id=%s FOR UPDATE",
                (decision.request_id,),
            )
            request = cur.fetchone()
            if request is None:
                raise ValueError("request is absent")
            cur.execute(
                """SELECT * FROM plan_authority_effect_receipts
                    WHERE request_id=%s AND completed_at IS NULL FOR UPDATE""",
                (decision.request_id,),
            )
            active_attempt = cur.fetchone()
            if request["expires_at"] <= decision.decided_at and active_attempt is None:
                raise ValueError("request is expired")
            if active_attempt is not None:
                if decision.kind is not DecisionKind.RECONCILE:
                    raise ValueError("request has an unresolved execution attempt; reconcile it explicitly")
                if not decision.reconciliation_evidence:
                    raise ValueError("active execution reconciliation requires evidence")
            else:
                cur.execute(
                    """SELECT outcome FROM plan_authority_effect_receipts
                        WHERE request_id=%s
                        ORDER BY started_at DESC, receipt_id DESC LIMIT 1""",
                    (decision.request_id,),
                )
                latest_effect = cur.fetchone()
                if latest_effect is not None and latest_effect["outcome"] != "retry":
                    raise ValueError("request has a terminal execution outcome")
            cur.execute(
                """SELECT decision_kind FROM plan_authority_decisions
                    WHERE request_id=%s ORDER BY decided_at DESC, decision_id DESC LIMIT 1""",
                (decision.request_id,),
            )
            prior = cur.fetchone()
            prior_kind = prior["decision_kind"] if prior else None
            allowed = {
                None: {DecisionKind.APPROVE.value, DecisionKind.HOLD.value, DecisionKind.RECONCILE.value},
                DecisionKind.HOLD.value: {DecisionKind.APPROVE.value, DecisionKind.RECONCILE.value},
                DecisionKind.RECONCILE.value: {DecisionKind.APPROVE.value, DecisionKind.HOLD.value},
                # A retry returns the request to its existing exact approval;
                # a new decision may instead deliberately hold/reconcile it.
                DecisionKind.APPROVE.value: {DecisionKind.HOLD.value, DecisionKind.RECONCILE.value},
            }
            if decision.kind.value not in allowed.get(prior_kind, set()):
                raise ValueError(f"invalid authority transition {prior_kind or 'pending'} -> {decision.kind.value}")
            settled_receipt_id = None
            if active_attempt is not None:
                settled_receipt_id = str(active_attempt["receipt_id"])
                cur.execute(
                    """UPDATE plan_authority_effect_receipts
                          SET outcome='ambiguous', evidence=%s::jsonb,
                              detail=%s, completed_at=%s
                        WHERE receipt_id=%s AND completed_at IS NULL""",
                    (
                        json.dumps(list(decision.reconciliation_evidence)),
                        f"reconciled active execution as ambiguous: {decision.reason}",
                        decision.decided_at,
                        settled_receipt_id,
                    ),
                )
                if cur.rowcount != 1:  # pragma: no cover - lock above is the invariant
                    raise ValueError("active execution changed before reconciliation")
            cur.execute(
                """INSERT INTO plan_authority_decisions
                   (decision_id,request_id,decision_kind,decided_by,reason,reconciliation_evidence,decided_at)
                   VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s) RETURNING *""",
                (decision.decision_id, decision.request_id, decision.kind.value,
                 decision.decided_by, decision.reason,
                 json.dumps(list(decision.reconciliation_evidence)), decision.decided_at),
            )
            row = cur.fetchone()
            if settled_receipt_id is not None:
                self._event(cur, decision.request_id, "execution-reconciled", {
                    "receipt_id": settled_receipt_id,
                    "outcome": "ambiguous",
                    "evidence": list(decision.reconciliation_evidence),
                })
            self._event(cur, decision.request_id, "decided", asdict(decision))
            result = dict(row)
            if settled_receipt_id is not None:
                result["settled_receipt_id"] = settled_receipt_id
                result["execution_outcome"] = "ambiguous"
            return result

    def begin_execution(
        self,
        request_id: str,
        *,
        effect_hash: str,
        generation: str,
        handler_id: str,
    ) -> Mapping[str, Any]:
        """Durably claim one approved effect without consuming its approval.

        The row is an intentional ambiguity fence.  It is never deleted: a
        stopped executor remains visible and cannot be replayed as if no call
        had reached the registered provider.
        """
        receipt_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        with self._connection() as con, con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Serialize attempts for a request before evaluating the active
            # attempt fence.  The partial unique index in the schema remains
            # the final cross-process invariant.
            cur.execute(
                "SELECT request_id FROM plan_authority_requests WHERE request_id=%s FOR UPDATE",
                (request_id,),
            )
            if cur.fetchone() is None:
                raise ValueError("request is absent")
            cur.execute(
                """INSERT INTO plan_authority_effect_receipts
                   (receipt_id,request_id,effect_hash,effect_generation,handler_id,started_at)
                   SELECT %s,r.request_id,r.effect_hash,r.effect_generation,%s,%s
                     FROM plan_authority_requests r
                    WHERE r.request_id=%s AND r.expires_at>%s
                      AND r.effect_hash=%s AND r.effect_generation=%s
                      AND (SELECT d.decision_kind
                             FROM plan_authority_decisions d
                            WHERE d.request_id=r.request_id
                            ORDER BY d.decided_at DESC, d.decision_id DESC LIMIT 1)='approve'
                      AND NOT EXISTS (
                          SELECT 1 FROM plan_authority_effect_receipts terminal
                           WHERE terminal.request_id=r.request_id
                             AND terminal.completed_at IS NOT NULL
                             AND terminal.outcome <> 'retry'
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM plan_authority_effect_receipts active
                           WHERE active.request_id=r.request_id
                             AND active.completed_at IS NULL
                      )
                   RETURNING *""",
                (receipt_id, handler_id, now, request_id, now, effect_hash, generation),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError("effect is not approved, expired, mismatched, terminal, or already executing")
            receipt = {
                "schema": "tgw-plan-effect-receipt/v2",
                "receipt_id": receipt_id,
                "request_id": request_id,
                "effect_hash": effect_hash,
                "generation": generation,
                "handler_id": handler_id,
                "started_at": now.isoformat(),
            }
            self._event(cur, request_id, "execution-started", receipt)
            return receipt

    def complete_execution(
        self,
        receipt_id: str,
        *,
        outcome: str,
        evidence: Sequence[str] = (),
        rollback_receipt: str | None = None,
        detail: str = "",
    ) -> Mapping[str, Any]:
        """Persist a provider outcome and resulting authority state atomically."""
        allowed_outcomes = {"succeeded", "retry", "ambiguous", "rolled_back", "failed"}
        if outcome not in allowed_outcomes:
            raise ValueError("execution outcome is not registered")
        if not isinstance(detail, str) or not all(isinstance(item, str) and item for item in evidence):
            raise ValueError("execution receipt content is invalid")
        now = datetime.now(timezone.utc)
        with self._connection() as con, con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """UPDATE plan_authority_effect_receipts
                      SET outcome=%s, evidence=%s::jsonb, rollback_receipt=%s,
                          detail=%s, completed_at=%s
                    WHERE receipt_id=%s AND completed_at IS NULL
                RETURNING *""",
                (outcome, json.dumps(sorted(set(evidence))), rollback_receipt, detail, now, receipt_id),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError("execution receipt is absent or already completed")
            receipt = dict(row)
            receipt["schema"] = "tgw-effect-execution-receipt/v1"
            self._event(cur, str(row["request_id"]), "execution-completed", {
                "receipt_id": str(row["receipt_id"]),
                "outcome": outcome,
                "rollback_receipt": rollback_receipt,
            })
            return receipt

    def get(self, request_id: str) -> Mapping[str, Any] | None:
        return self._query_one(
            self._request_projection_sql("WHERE r.request_id=%s"),
            (request_id,),
        )

    def list(self, limit: int = 100) -> Sequence[Mapping[str, Any]]:
        with self._connection() as con, con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                self._request_projection_sql("ORDER BY r.requested_at DESC LIMIT %s"),
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]

    def events(self, request_id: str) -> Sequence[Mapping[str, Any]]:
        with self._connection() as con, con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM plan_authority_events WHERE request_id=%s ORDER BY sequence", (request_id,))
            return [dict(row) for row in cur.fetchall()]

    def _query_one(self, query: str, params: tuple[Any, ...]) -> Mapping[str, Any] | None:
        with self._connection() as con, con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            row = cur.fetchone()
            return dict(row) if row else None

    @staticmethod
    def _request_projection_sql(tail: str) -> str:
        """Use latest decision/attempt, never a lossy multi-row join."""
        return f"""SELECT r.*,d.decision_id,d.decision_kind,d.decided_by,
                          d.reason AS decision_reason,d.reconciliation_evidence,d.decided_at,
                          e.receipt_id,e.started_at,e.completed_at,e.outcome,
                          e.handler_id,e.evidence AS execution_evidence,
                          e.rollback_receipt,e.detail,
                          e.completed_at AS consumed_at
                     FROM plan_authority_requests r
                LEFT JOIN LATERAL (
                    SELECT * FROM plan_authority_decisions d
                     WHERE d.request_id=r.request_id
                     ORDER BY d.decided_at DESC, d.decision_id DESC LIMIT 1
                ) d ON TRUE
                LEFT JOIN LATERAL (
                    SELECT * FROM plan_authority_effect_receipts e
                     WHERE e.request_id=r.request_id
                     ORDER BY e.started_at DESC, e.receipt_id DESC LIMIT 1
                ) e ON TRUE
                {tail}"""

    @staticmethod
    def _event(cur: Any, request_id: str, event_type: str, details: Mapping[str, Any]) -> None:
        cur.execute("INSERT INTO plan_authority_events (request_id,event_type,details) VALUES (%s,%s,%s::jsonb)", (request_id, event_type, json.dumps(details, default=str)))


def create_authority_router(
    store: AuthorityStore,
    *,
    current_plan_commit: Callable[[], str],
    load_solution: Callable[[str], Mapping[str, Any]],
    require_operator: Callable[[], Any],
    require_executor: Callable[[], Any],
    execute_effect: Callable[..., Any] | None = None,
) -> APIRouter:
    """One HTTP projection over the canonical authority store."""

    router = APIRouter(prefix="/api/plan-authority", tags=["plan-authority"])

    def authenticated_operator_identity(value: Any) -> str:
        """Bind mutation receipts to the identity returned by host auth.

        Request JSON is untrusted.  In particular, it must never be able to
        choose the principal recorded in a durable authority request or
        decision.  The host's operator dependency is the only identity
        authority for this router.
        """
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(401, "authenticated operator identity is required")
        return value.strip()

    @router.get("/requests", dependencies=[Depends(require_operator)])
    def list_requests(limit: int = 100):
        return {"schema": AUTHORITY_SCHEMA, "requests": store.list(limit)}

    @router.get("/requests/{request_id}", dependencies=[Depends(require_operator)])
    def get_request(request_id: str):
        row = store.get(request_id)
        if row is None:
            raise HTTPException(404, "request not found")
        return {"schema": AUTHORITY_SCHEMA, "request": row, "events": store.events(request_id)}

    @router.post("/requests", status_code=201)
    def request_effect(body: dict[str, Any], operator_identity: Any = Depends(require_operator)):
        try:
            payload = dict(body)
            payload["requested_by"] = authenticated_operator_identity(operator_identity)
            solution = load_solution(str(payload["solution_hash"]))
            request = AuthorityRequest.create(payload, solution=solution, current_plan_commit=current_plan_commit())
            return {"schema": AUTHORITY_SCHEMA, "request": store.create_request(request)}
        except (KeyError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/requests/{request_id}/decisions")
    def decide(request_id: str, body: dict[str, Any], operator_identity: Any = Depends(require_operator)):
        try:
            payload = dict(body)
            payload["decided_by"] = authenticated_operator_identity(operator_identity)
            return {"schema": AUTHORITY_SCHEMA, "request": store.decide(AuthorityDecision.create(request_id, payload))}
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/requests/{request_id}/consume", dependencies=[Depends(require_executor)])
    def consume(request_id: str, body: dict[str, Any] | None = None):
        """Execute the already-recorded typed effect through a registered handler.

        This endpoint deliberately accepts no client-supplied effect hash or
        generation.  An executor can only redeem the immutable effect stored in
        the request, and only through the supplied controller.
        """
        try:
            if body:
                raise ValueError("consume takes no effect payload; the approved request is authoritative")
            if execute_effect is None:
                raise ValueError("no registered effect executor is mounted")
            row = store.get(request_id)
            if row is None:
                raise ValueError("request not found")
            effect = TypedEffect.parse({
                "kind": row["effect_kind"],
                "generation": row["effect_generation"],
                "parameters": row["effect_parameters"],
            })
            if effect.effect_hash != row["effect_hash"]:
                raise ValueError("stored request effect binding is invalid")
            receipt = execute_effect(request_id=request_id, effect=effect)
            if is_dataclass(receipt):
                receipt = asdict(receipt)
            if not isinstance(receipt, Mapping):
                raise ValueError("registered effect executor returned an invalid receipt")
            return {"schema": AUTHORITY_SCHEMA, "receipt": dict(receipt)}
        except (KeyError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    return router
