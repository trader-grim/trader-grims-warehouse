from unittest.mock import MagicMock

import pytest

from tgw.plan_authority_schema import REQUIRED_TABLES, apply_plan_authority_schema, require_plan_authority_schema


def _connection(
    rows=(),
    constraint="CHECK effect_kind IN ('nixos-a3-successor-evaluation','tgw-prod-a3-preintegration-observation')",
):
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchall.return_value = list(rows)
    cursor.fetchone.return_value = (constraint,) if constraint is not None else None
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    connect = MagicMock(return_value=connection)
    return connect, cursor


def test_explicit_migration_is_locked_idempotent_and_hash_receipted():
    connect, cursor = _connection()
    receipt = apply_plan_authority_schema("postgresql://db", connect=connect)
    assert cursor.execute.call_args_list[0].args[0].startswith("SELECT pg_advisory_xact_lock")
    assert "CREATE TABLE IF NOT EXISTS plan_authority_requests" in cursor.execute.call_args_list[1].args[0]
    assert receipt["migration"] == "plan-authority-v1"
    assert receipt["sql_sha256"].startswith("sha256:")


def test_schema_readiness_requires_every_table():
    connect, _ = _connection((name,) for name in REQUIRED_TABLES)
    require_plan_authority_schema("postgresql://db", connect=connect)
    connect, _ = _connection([("plan_authority_requests",)])
    with pytest.raises(RuntimeError, match="schema is incomplete"):
        require_plan_authority_schema("postgresql://db", connect=connect)
    connect, _ = _connection(((name,) for name in REQUIRED_TABLES), constraint="CHECK effect_kind IN ('coding-release')")
    with pytest.raises(RuntimeError, match="constraint is stale"):
        require_plan_authority_schema("postgresql://db", connect=connect)


def test_missing_database_configuration_fails_closed():
    with pytest.raises(ValueError, match="not configured"):
        apply_plan_authority_schema("")
    with pytest.raises(ValueError, match="not configured"):
        require_plan_authority_schema("")
