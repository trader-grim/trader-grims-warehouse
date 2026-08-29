from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _unit(name: str) -> str:
    return (ROOT / "systemd" / name).read_text(encoding="utf-8")


def test_local_coding_units_use_unix_accounts_and_shared_group() -> None:
    expectations = {
        "tgw-coding-local-foreman.service": "db",
        "tgw-codex-implement-worker.service": "codex",
        "tgw-controller-verify-worker.service": "codex",
    }
    for name, actor in expectations.items():
        unit = _unit(name)
        assert f"User={actor}\n" in unit
        expected_group = "tgw-coders" if name == "tgw-controller-verify-worker.service" else actor
        assert f"Group={expected_group}\n" in unit
        assert "SupplementaryGroups=tgw-coders" in unit
        assert "UMask=0002" in unit
        assert "tgw.development.local_workflow" in unit
        if name == "tgw-coding-local-foreman.service":
            assert "/opt/TGW/tgw-lib/coding-runtime/current/src" in unit
        else:
            assert "WorkingDirectory=/opt/TGW/tgw-lib/coding-runtime/current" in unit
            assert "Environment=PYTHONPATH=src" in unit
        assert "ProtectSystem=strict" not in unit
        assert "ReadWritePaths=" not in unit
        lowered = unit.lower()
        for forbidden in ("tgw-prod", "ssh ", "api_endpoint", "actor-fleet", "execution-card"):
            assert forbidden not in lowered


def test_foreman_timer_is_automatic_and_local() -> None:
    timer = _unit("tgw-coding-local-foreman.timer")
    assert "OnBootSec=15s" in timer
    assert "OnUnitActiveSec=10s" in timer
    assert "Unit=tgw-coding-local-foreman.service" in timer


def test_database_roles_are_universal_and_peer_mapped() -> None:
    sql = (ROOT / "config" / "tgw-coding-local-roles.sql").read_text(encoding="utf-8")
    assert "CREATE ROLE tgw_coding LOGIN INHERIT" in sql
    assert "ALTER ROLE tgw_coding LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE" in sql
    assert "CREATE ROLE db LOGIN" not in sql
    assert "CREATE ROLE codex LOGIN" not in sql
    assert "DROP ROLE" in sql
    assert "public.todo_items, public.queue_jobs" in sql
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" in sql
    assert "ALL TABLES" not in sql
    assert "ALTER DEFAULT PRIVILEGES" not in sql
    assert "tgw-prod" not in sql
    ident = (ROOT / "config/environment/postgresql/pg_ident.conf").read_text(encoding="utf-8")
    hba = (ROOT / "config/environment/postgresql/pg_hba.conf").read_text(encoding="utf-8")
    entries = [
        tuple(line.split())
        for line in ident.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert entries
    assert all(row[0] == "tgw-coders" and row[2] == "tgw_coding" for row in entries)
    for actor in ("db", "codex", "claude", "deepseek"):
        assert any(row[1] == actor for row in entries)
    assert "local   all             tgw_coding      peer map=tgw-coders" in hba
