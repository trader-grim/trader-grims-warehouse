"""Continual-harness durable ledger — Todo 1916 leaf 11.1 (L11.1 core).

The continual harness is the one durable process identity
(NEXT-EVOLUTION-1916-RECONCILIATION-v1.md section 1, Disposability invariant).
Model sessions, worker containers, and task-local microservices are disposable
and may be terminated at any job boundary; terminating them must never lose
durable harness state or the operator session.

This module is that durable state. Per task it owns:

  * the task cursor        — where the work is (stage / step / resume point)
  * the running status     — open / blocked / done / abandoned
  * context variables      — durable key/value the harness carries between sessions
  * an append-only history — every attempt, remediation round, rejected
    candidate, review finding, failed approach, operator correction, and next
    discriminating action (LEAF-11-1 L11.1.LEDGER-AND-GIT-DISCIPLINE /
    AMENDMENT-20260905 section B). These live here and never as a git commit
    or ref.

Storage substrate: the same PostgreSQL database as queue.durable-claims@1 and
evidence.immutable-receipts@1 (W02). The ledger adds two tables and never
shares a write path with the queue.

Cursor ownership is a single-owner lease. Exactly one live owner may hold a
task's cursor; a crashed owner's lease is stolen once it expires. That is the
whole point of L11.1: a disposable session dies, the ledger keeps the cursor,
and the next session resumes from it identically.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Generator, Iterable

import psycopg2
import psycopg2.extras

# Shared with tgw.queue.state_machine — one host, one database. init() binds
# the real DSN; the default matches state_machine's own placeholder.
_DSN: str = "dbname=state_machine user=tgw"
_schema_ready = False

DEFAULT_LEASE_SECONDS = 900

# The task lifecycle. Deliberately four values — a coding task is open until it
# lands (done), is parked on an external blocker (blocked), or is deliberately
# dropped (abandoned). Enforced by a DB CHECK because it is stable.
TASK_STATUSES = frozenset({"open", "blocked", "done", "abandoned"})

# The history vocabulary from L11.1.LEDGER-AND-GIT-DISCIPLINE. Validated in
# Python, not by a DB CHECK: this list is expected to grow as the harness
# learns to record more, and a growing CHECK is a migration each time.
ENTRY_KINDS = frozenset({
    "attempt",
    "remediation",
    "rejected_candidate",
    "review_finding",
    "failed_approach",
    "operator_correction",
    "next_action",
    "note",
})


class LedgerError(RuntimeError):
    """Base class for ledger refusals."""


class LedgerLeaseError(LedgerError):
    """The caller does not hold the task cursor lease it claimed."""


class UnknownTaskError(LedgerError):
    """No ledger row exists for the named task."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS harness_ledger_task (
    task_id           TEXT PRIMARY KEY,
    cursor            JSONB NOT NULL DEFAULT '{}'::jsonb,
    status            TEXT  NOT NULL DEFAULT 'open'
                      CHECK (status IN ('open', 'blocked', 'done', 'abandoned')),
    context           JSONB NOT NULL DEFAULT '{}'::jsonb,
    owner             TEXT,
    lease_id          UUID,
    lease_expires_at  TIMESTAMPTZ,
    generation        BIGINT NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS harness_ledger_entry (
    entry_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id     TEXT NOT NULL REFERENCES harness_ledger_task(task_id) ON DELETE CASCADE,
    seq         BIGINT NOT NULL,
    kind        TEXT NOT NULL,
    body        JSONB NOT NULL DEFAULT '{}'::jsonb,
    actor       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (task_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_harness_ledger_entry_task
    ON harness_ledger_entry (task_id, seq);
"""

_TASK_COLUMNS = (
    "task_id", "cursor", "status", "context",
    "owner", "lease_id", "lease_expires_at", "generation",
    "created_at", "updated_at",
)
_TASK_SELECT = ", ".join(_TASK_COLUMNS)


def init(dsn: str) -> None:
    """Bind the PostgreSQL DSN (the same database as tgw.queue.state_machine)."""
    global _DSN, _schema_ready
    _DSN = dsn
    _schema_ready = False


@contextmanager
def _conn() -> Generator[Any, None, None]:
    con = psycopg2.connect(_DSN)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _ensure_schema() -> None:
    """Make sure the two ledger tables exist.

    The canonical creator is ``config/tgw-coding-local-roles.sql`` applied by
    ``tgw-coding-bootstrap --repair database`` (piped through ``sudo -u
    postgres``), which also GRANTs the locked-down ``tgw_coding`` role its DML
    on these tables. This function only *checks*; it attempts the DDL itself
    solely as a dev/first-run fallback for a connection that happens to hold
    CREATE. An ordinary ``tgw_coding`` caller that finds the tables missing
    gets a clear instruction, not a raw privilege error.
    """
    global _schema_ready
    if _schema_ready:
        return
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                "SELECT to_regclass('public.harness_ledger_task'),"
                "       to_regclass('public.harness_ledger_entry')"
            )
            task_tbl, entry_tbl = cur.fetchone()
            if task_tbl is not None and entry_tbl is not None:
                _schema_ready = True
                return
            try:
                cur.execute(_SCHEMA)
            except psycopg2.errors.InsufficientPrivilege as exc:
                raise LedgerError(
                    "harness ledger tables are absent — run "
                    "`tgw-coding-bootstrap --repair database` to create them "
                    "and grant tgw_coding its DML"
                ) from exc
    _schema_ready = True


def _dump(value: Any) -> str:
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _task_row(record: Any) -> dict[str, Any] | None:
    if record is None:
        return None
    out = dict(record)
    if out.get("lease_id") is not None:
        out["lease_id"] = str(out["lease_id"])
    return out


def _require_str(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


# --------------------------------------------------------------------------- #
# task row: create, read
# --------------------------------------------------------------------------- #

def ensure_task(
    task_id: str,
    *,
    cursor: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the task's ledger row if absent; return the current row.

    Idempotent and non-destructive: an existing row's cursor, status, context,
    and ownership are left exactly as they are. A resuming session calls this
    freely at startup. `cursor` / `context` seed the row only on first creation.
    """
    _require_str("task_id", task_id)
    _ensure_schema()
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO harness_ledger_task (task_id, cursor, context)
                VALUES (%s, %s::jsonb, %s::jsonb)
                ON CONFLICT (task_id) DO NOTHING
                """,
                (task_id, _dump(cursor or {}), _dump(context or {})),
            )
            cur.execute(
                f"SELECT {_TASK_SELECT} FROM harness_ledger_task WHERE task_id = %s",
                (task_id,),
            )
            return _task_row(cur.fetchone())


def read_task(task_id: str) -> dict[str, Any] | None:
    """Return the task's durable state without taking the cursor lease.

    Any fresh session gets identical cursor + status + context this way — the
    resume-from-ledger read (L11.1 acceptance 1). Returns None if unknown.
    """
    _ensure_schema()
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"SELECT {_TASK_SELECT} FROM harness_ledger_task WHERE task_id = %s",
                (task_id,),
            )
            return _task_row(cur.fetchone())


def list_tasks(
    *,
    statuses: Iterable[str] | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return known tasks, most recently updated first — a read-only projection
    for the operator ``tgw coding status`` surface. Takes no lease."""
    _ensure_schema()
    where = ""
    params: list[Any] = []
    if statuses is not None:
        status_list = list(statuses)
        if status_list:
            where = "WHERE status = ANY(%s)"
            params.append(status_list)
    params.append(max(1, limit))
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"SELECT {_TASK_SELECT} FROM harness_ledger_task "
                f"{where} ORDER BY updated_at DESC LIMIT %s",
                params,
            )
            return [_task_row(row) for row in cur.fetchall()]


# --------------------------------------------------------------------------- #
# cursor ownership: acquire, renew, release, write
# --------------------------------------------------------------------------- #

def acquire_cursor(
    task_id: str,
    owner: str,
    *,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> dict[str, Any] | None:
    """Atomically take the task cursor for `owner`.

    Returns the task row (including a fresh string `lease_id`) on success.
    Returns None when a *different* owner holds a live lease — the caller must
    not proceed. An expired lease is stolen (crash recovery). Re-acquiring as
    the same owner refreshes the lease and returns the row.

    Exactly one live owner can hold one cursor (L11.1 acceptance 2): the single
    UPDATE below is the entire guarantee — PostgreSQL serialises the row.
    """
    _require_str("task_id", task_id)
    _require_str("owner", owner)
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    _ensure_schema()
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                UPDATE harness_ledger_task
                   SET owner = %s,
                       lease_id = gen_random_uuid(),
                       lease_expires_at = now() + make_interval(secs => %s),
                       updated_at = now()
                 WHERE task_id = %s
                   AND (owner IS NULL
                        OR owner = %s
                        OR lease_expires_at IS NULL
                        OR lease_expires_at < now())
                RETURNING {_TASK_SELECT}
                """,
                (owner, lease_seconds, task_id, owner),
            )
            row = cur.fetchone()
            if row is not None:
                return _task_row(row)
            cur.execute(
                "SELECT 1 FROM harness_ledger_task WHERE task_id = %s", (task_id,)
            )
            if cur.fetchone() is None:
                raise UnknownTaskError(task_id)
            return None  # a live owner holds it


def renew_cursor(
    task_id: str,
    owner: str,
    lease_id: str,
    *,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> dict[str, Any]:
    """Extend the caller's cursor lease.

    The lease exists for mutual exclusion *between owners*, not as a
    self-imposed deadline: as long as this owner+lease_id still matches the row
    (i.e. no other session has ``acquire_cursor``-stolen a presumed-crashed
    lease — a steal rewrites owner and lease_id), the renewal succeeds even if
    the previous window lapsed. A single implement->test->review round can
    legitimately outlast one window, and it must not lose its own cursor to the
    clock. Raises LedgerLeaseError only once another owner holds it.
    """
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    _ensure_schema()
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                UPDATE harness_ledger_task
                   SET lease_expires_at = now() + make_interval(secs => %s),
                       updated_at = now()
                 WHERE task_id = %s AND owner = %s AND lease_id = %s::uuid
                RETURNING {_TASK_SELECT}
                """,
                (lease_seconds, task_id, owner, lease_id),
            )
            row = cur.fetchone()
            if row is None:
                raise LedgerLeaseError(
                    f"{owner} does not hold the cursor on {task_id} "
                    f"(another owner acquired it)"
                )
            return _task_row(row)


def release_cursor(task_id: str, owner: str, lease_id: str) -> None:
    """Give up the cursor. Best-effort: a lease already expired or stolen is
    silently fine — the point is only that this owner stops holding it."""
    _ensure_schema()
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                """
                UPDATE harness_ledger_task
                   SET owner = NULL, lease_id = NULL, lease_expires_at = NULL,
                       updated_at = now()
                 WHERE task_id = %s AND owner = %s AND lease_id = %s::uuid
                """,
                (task_id, owner, lease_id),
            )


def write_cursor(
    task_id: str,
    owner: str,
    lease_id: str,
    *,
    cursor: dict[str, Any] | None = None,
    status: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update cursor / status / context. Requires the caller still owns the cursor.

    Each write bumps `generation`. A caller whose lease was stolen (because it
    was presumed crashed, and another session ran `acquire_cursor`) cannot
    clobber the new owner's state — the steal rewrote owner/lease_id, so the
    UPDATE matches no row and raises LedgerLeaseError. A merely-lapsed window
    with no steal is still this owner's to write (see `renew_cursor`).
    """
    if status is not None and status not in TASK_STATUSES:
        raise ValueError(f"status must be one of {sorted(TASK_STATUSES)}")
    if cursor is None and status is None and context is None:
        raise ValueError("write_cursor needs at least one of cursor/status/context")
    _ensure_schema()
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                UPDATE harness_ledger_task
                   SET cursor = COALESCE(%s::jsonb, cursor),
                       status = COALESCE(%s, status),
                       context = COALESCE(%s::jsonb, context),
                       generation = generation + 1,
                       updated_at = now()
                 WHERE task_id = %s AND owner = %s AND lease_id = %s::uuid
                RETURNING {_TASK_SELECT}
                """,
                (
                    _dump(cursor) if cursor is not None else None,
                    status,
                    _dump(context) if context is not None else None,
                    task_id, owner, lease_id,
                ),
            )
            row = cur.fetchone()
            if row is None:
                raise LedgerLeaseError(
                    f"{owner} does not hold a live cursor lease on {task_id}"
                )
            return _task_row(row)


# --------------------------------------------------------------------------- #
# append-only history
# --------------------------------------------------------------------------- #

def append(
    task_id: str,
    kind: str,
    body: dict[str, Any],
    *,
    actor: str | None = None,
) -> int:
    """Append one history entry; return its per-task sequence number.

    Not lease-guarded on purpose: a review finding or an operator correction is
    recorded by whoever produced it, not only by the cursor holder. Appends for
    one task are serialised (the task row is locked) so `seq` is gap-free and
    monotonic.
    """
    if kind not in ENTRY_KINDS:
        raise ValueError(f"kind must be one of {sorted(ENTRY_KINDS)}")
    if not isinstance(body, dict):
        raise TypeError("history entry body must be a JSON object")
    _ensure_schema()
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT 1 FROM harness_ledger_task WHERE task_id = %s FOR UPDATE",
                (task_id,),
            )
            if cur.fetchone() is None:
                raise UnknownTaskError(task_id)
            cur.execute(
                """
                INSERT INTO harness_ledger_entry (task_id, seq, kind, body, actor)
                SELECT %s, COALESCE(MAX(seq), 0) + 1, %s, %s::jsonb, %s
                  FROM harness_ledger_entry WHERE task_id = %s
                RETURNING seq
                """,
                (task_id, kind, _dump(body), actor, task_id),
            )
            return int(cur.fetchone()["seq"])


def history(
    task_id: str,
    *,
    kinds: Iterable[str] | None = None,
    after_seq: int = 0,
) -> list[dict[str, Any]]:
    """Return the task's append-only history in order, oldest first.

    The full attempt / remediation / rejected-candidate / finding / correction
    record for a task is reconstructable from this alone — no git archaeology
    (L11.1.LEDGER-AND-GIT-DISCIPLINE acceptance).
    """
    _ensure_schema()
    where = "WHERE task_id = %s AND seq > %s"
    params: list[Any] = [task_id, after_seq]
    if kinds is not None:
        kind_list = list(kinds)
        if kind_list:
            where += " AND kind = ANY(%s)"
            params.append(kind_list)
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT seq, kind, body, actor, created_at
                  FROM harness_ledger_entry
                  {where}
                 ORDER BY seq
                """,
                params,
            )
            return [dict(row) for row in cur.fetchall()]
