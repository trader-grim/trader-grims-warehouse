"""Single immutable authority for Plan-bound requests, decisions, and effects."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
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
    NIXOS_REVIEWED_EVALUATION = "nixos-reviewed-evaluation"


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
    decided_at: datetime

    @classmethod
    def create(cls, request_id: str, data: Mapping[str, Any], *, now: datetime | None = None) -> "AuthorityDecision":
        kind = DecisionKind(data.get("kind"))
        decided_by, reason = data.get("decided_by"), data.get("reason")
        if not isinstance(decided_by, str) or not decided_by or not isinstance(reason, str) or not reason:
            raise ValueError("decided_by and reason are required")
        decided_at = now or datetime.now(timezone.utc)
        payload = {"request_id": request_id, "kind": kind.value, "decided_by": decided_by, "reason": reason, "decided_at": decided_at.isoformat()}
        return cls(_identity("decision", payload), request_id, kind, decided_by, reason, decided_at)


class AuthorityStore(Protocol):
    def create_request(self, request: AuthorityRequest) -> Mapping[str, Any]: ...
    def decide(self, decision: AuthorityDecision) -> Mapping[str, Any]: ...
    def consume(self, request_id: str, *, effect_hash: str, generation: str) -> Mapping[str, Any]: ...
    def get(self, request_id: str) -> Mapping[str, Any] | None: ...
    def list(self, limit: int = 100) -> Sequence[Mapping[str, Any]]: ...
    def events(self, request_id: str) -> Sequence[Mapping[str, Any]]: ...


class PostgresAuthorityStore:
    """Canonical durable store; conditional SQL owns one-shot transitions."""

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
                """INSERT INTO plan_authority_decisions
                   (decision_id,request_id,decision_kind,decided_by,reason,decided_at)
                   SELECT %s,%s,%s,%s,%s,%s FROM plan_authority_requests
                    WHERE request_id=%s AND expires_at>%s
                   ON CONFLICT (request_id) DO NOTHING RETURNING *""",
                (decision.decision_id, decision.request_id, decision.kind.value,
                 decision.decided_by, decision.reason, decision.decided_at,
                 decision.request_id, decision.decided_at),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError("request is absent, expired, or already decided")
            self._event(cur, decision.request_id, "decided", asdict(decision))
            return dict(row)

    def consume(self, request_id: str, *, effect_hash: str, generation: str) -> Mapping[str, Any]:
        receipt_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        with self._connection() as con, con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """INSERT INTO plan_authority_effect_receipts
                   (receipt_id,request_id,effect_hash,effect_generation,consumed_at)
                   SELECT %s,r.request_id,r.effect_hash,r.effect_generation,%s
                     FROM plan_authority_requests r
                     JOIN plan_authority_decisions d ON d.request_id=r.request_id
                    WHERE r.request_id=%s AND d.decision_kind='approve' AND r.expires_at>%s
                      AND r.effect_hash=%s AND r.effect_generation=%s
                   ON CONFLICT (request_id) DO NOTHING RETURNING *""",
                (receipt_id, now, request_id, now, effect_hash, generation),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError("effect is not approved, expired, mismatched, or already consumed")
            receipt = {"schema": "tgw-plan-effect-receipt/v1", "receipt_id": receipt_id, "request_id": request_id, "effect_hash": effect_hash, "generation": generation, "consumed_at": now.isoformat()}
            self._event(cur, request_id, "consumed", receipt)
            return receipt

    def get(self, request_id: str) -> Mapping[str, Any] | None:
        return self._query_one(
            """SELECT r.*,d.decision_id,d.decision_kind,d.decided_by,d.reason AS decision_reason,d.decided_at,
                      e.receipt_id,e.consumed_at
                 FROM plan_authority_requests r LEFT JOIN plan_authority_decisions d USING(request_id)
                 LEFT JOIN plan_authority_effect_receipts e USING(request_id) WHERE r.request_id=%s""",
            (request_id,),
        )

    def list(self, limit: int = 100) -> Sequence[Mapping[str, Any]]:
        with self._connection() as con, con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT r.*,d.decision_id,d.decision_kind,d.decided_by,d.reason AS decision_reason,d.decided_at,
                          e.receipt_id,e.consumed_at
                     FROM plan_authority_requests r LEFT JOIN plan_authority_decisions d USING(request_id)
                     LEFT JOIN plan_authority_effect_receipts e USING(request_id)
                    ORDER BY r.requested_at DESC LIMIT %s""",
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
    def _event(cur: Any, request_id: str, event_type: str, details: Mapping[str, Any]) -> None:
        cur.execute("INSERT INTO plan_authority_events (request_id,event_type,details) VALUES (%s,%s,%s::jsonb)", (request_id, event_type, json.dumps(details, default=str)))


def create_authority_router(
    store: AuthorityStore,
    *,
    current_plan_commit: Callable[[], str],
    load_solution: Callable[[str], Mapping[str, Any]],
    require_operator: Callable[[], Any],
    require_executor: Callable[[], Any],
) -> APIRouter:
    """One HTTP projection over the canonical authority store."""

    router = APIRouter(prefix="/api/plan-authority", tags=["plan-authority"])

    @router.get("/requests", dependencies=[Depends(require_operator)])
    def list_requests(limit: int = 100):
        return {"schema": AUTHORITY_SCHEMA, "requests": store.list(limit)}

    @router.get("/requests/{request_id}", dependencies=[Depends(require_operator)])
    def get_request(request_id: str):
        row = store.get(request_id)
        if row is None:
            raise HTTPException(404, "request not found")
        return {"schema": AUTHORITY_SCHEMA, "request": row, "events": store.events(request_id)}

    @router.post("/requests", status_code=201, dependencies=[Depends(require_operator)])
    def request_effect(body: dict[str, Any]):
        try:
            solution = load_solution(str(body["solution_hash"]))
            request = AuthorityRequest.create(body, solution=solution, current_plan_commit=current_plan_commit())
            return {"schema": AUTHORITY_SCHEMA, "request": store.create_request(request)}
        except (KeyError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/requests/{request_id}/decisions", dependencies=[Depends(require_operator)])
    def decide(request_id: str, body: dict[str, Any]):
        try:
            return {"schema": AUTHORITY_SCHEMA, "request": store.decide(AuthorityDecision.create(request_id, body))}
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/requests/{request_id}/consume", dependencies=[Depends(require_executor)])
    def consume(request_id: str, body: dict[str, Any]):
        try:
            return {"schema": AUTHORITY_SCHEMA, "receipt": store.consume(request_id, effect_hash=str(body["effect_hash"]), generation=str(body["generation"]))}
        except (KeyError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    return router
