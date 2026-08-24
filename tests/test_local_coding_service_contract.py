from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _unit(name: str) -> str:
    return (ROOT / "systemd" / name).read_text(encoding="utf-8")


def test_local_coding_units_use_unix_accounts_and_shared_group() -> None:
    expectations = {
        "tgw-coding-local-foreman.service": "db",
        "tgw-codex-implement-worker.service": "codex",
        "tgw-controller-verify-worker.service": "db",
    }
    for name, actor in expectations.items():
        unit = _unit(name)
        assert f"User={actor}\n" in unit
        assert f"Group={actor}\n" in unit
        assert "SupplementaryGroups=tgw-coders" in unit
        assert "UMask=0002" in unit
        assert "tgw.development.local_workflow" in unit
        assert "/opt/TGW/tgw-lib/coding-runtime/current/src" in unit
        lowered = unit.lower()
        for forbidden in ("tgw-prod", "ssh ", "api_endpoint", "actor-fleet", "execution-card"):
            assert forbidden not in lowered


def test_foreman_timer_is_automatic_and_local() -> None:
    timer = _unit("tgw-coding-local-foreman.timer")
    assert "OnBootSec=15s" in timer
    assert "OnUnitActiveSec=10s" in timer
    assert "Unit=tgw-coding-local-foreman.service" in timer


def test_database_roles_are_peer_named_and_narrowly_scoped() -> None:
    sql = (ROOT / "config" / "tgw-coding-local-roles.sql").read_text(encoding="utf-8")
    assert "CREATE ROLE db LOGIN INHERIT" in sql
    assert "CREATE ROLE codex LOGIN INHERIT" in sql
    assert "ALTER ROLE db LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE" in sql
    assert "ALTER ROLE codex LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE" in sql
    assert "GRANT tgw_coding TO db, codex" in sql
    assert "public.todo_items, public.queue_jobs" in sql
    assert "ALL TABLES" not in sql
    assert "ALTER DEFAULT PRIVILEGES" not in sql
    assert "tgw-prod" not in sql
