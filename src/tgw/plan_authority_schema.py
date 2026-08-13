"""Explicit, idempotent PlanAuthority schema migration and readiness check."""

from __future__ import annotations

from hashlib import sha256
from importlib.resources import files
from typing import Any, Callable

import psycopg2

REQUIRED_TABLES = frozenset(
    {
        "plan_authority_requests",
        "plan_authority_decisions",
        "plan_authority_effect_receipts",
        "plan_authority_events",
    }
)


def _sql() -> str:
    return files("tgw").joinpath("plan_authority.sql").read_text(encoding="utf-8")


def apply_plan_authority_schema(dsn: str, *, connect: Callable[..., Any] = psycopg2.connect) -> dict[str, str]:
    if not dsn:
        raise ValueError("PlanAuthority database is not configured")
    sql = _sql()
    with connect(dsn) as con, con.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("tgw-plan-authority-schema-v1",))
        cur.execute(sql)
    return {"schema": "tgw-schema-migration-receipt/v1", "migration": "plan-authority-v1", "sql_sha256": "sha256:" + sha256(sql.encode()).hexdigest()}


def require_plan_authority_schema(dsn: str, *, connect: Callable[..., Any] = psycopg2.connect) -> None:
    if not dsn:
        raise ValueError("PlanAuthority database is not configured")
    with connect(dsn) as con, con.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname=current_schema() AND tablename = ANY(%s)", (sorted(REQUIRED_TABLES),))
        present = {row[0] for row in cur.fetchall()}
        cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_catalog.pg_constraint WHERE conrelid='plan_authority_requests'::regclass AND conname='plan_authority_requests_effect_kind_check'")
        constraint = cur.fetchone()
    missing = REQUIRED_TABLES - present
    if missing:
        raise RuntimeError(f"PlanAuthority schema is incomplete: {sorted(missing)}")
    if not constraint or "nixos-a3-successor-evaluation" not in str(constraint[0]):
        raise RuntimeError("PlanAuthority effect-kind constraint is stale")
