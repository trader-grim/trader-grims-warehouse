"""Fail-closed database binding for the one-shot local fixture proof."""
from __future__ import annotations

import importlib
import os
import pwd
from pathlib import Path
from typing import Any, Callable

import psycopg2
from psycopg2.extensions import parse_dsn

from tgw.config import DEFAULT_CONFIG, load_config, load_json_strict

DEVELOPMENT_DATABASE = "tgw_lib_dev_state_machine"
DEVELOPMENT_WRITE_IDENTITY = "tigwadev"


class FixtureDatabaseBindingError(RuntimeError):
    """The fixture runner lacks its explicitly local database binding."""


def _explicit_dsn(config_path: Path) -> str:
    if not config_path.is_file():
        raise FixtureDatabaseBindingError("fixture database config is missing")
    raw = load_json_strict(config_path)
    if not isinstance(raw, dict) or not isinstance(raw.get("postgres_dsn"), str) or not raw["postgres_dsn"].strip():
        raise FixtureDatabaseBindingError("fixture database requires an explicit postgres_dsn")
    # Go through the established application loader as well as requiring the
    # raw key, so its legacy fallback can never satisfy this fixture contract.
    return str(load_config(config_path)["postgres_dsn"])


def initialize_fixture_database(
    *, config_path: Path = DEFAULT_CONFIG,
    connect: Callable[[str], Any] = psycopg2.connect,
) -> dict[str, str]:
    """Validate and install the one local DSN into both fixture adapters.

    This never selects a default DSN, contacts a remote endpoint, or elevates
    privileges.  The invoking one-shot process must already be ``tigwadev``.
    """
    dsn = _explicit_dsn(config_path)
    try:
        parsed = parse_dsn(dsn)
    except Exception as exc:
        raise FixtureDatabaseBindingError("fixture postgres_dsn is malformed") from exc
    if parsed.get("dbname") != DEVELOPMENT_DATABASE or parsed.get("user") != DEVELOPMENT_WRITE_IDENTITY:
        raise FixtureDatabaseBindingError("fixture postgres_dsn does not name the configured development target")
    host = parsed.get("host")
    if host not in (None, "", "/var/run/postgresql"):
        raise FixtureDatabaseBindingError("fixture postgres_dsn must use a local PostgreSQL endpoint")
    if pwd.getpwuid(os.geteuid()).pw_name != DEVELOPMENT_WRITE_IDENTITY:
        raise FixtureDatabaseBindingError("fixture process is not the configured local write identity")
    try:
        with connect(dsn) as con, con.cursor() as cur:
            cur.execute("SELECT current_database(), current_user, inet_server_addr()")
            database, role, endpoint = cur.fetchone()
    except Exception as exc:
        raise FixtureDatabaseBindingError("fixture development database preflight failed") from exc
    if database != DEVELOPMENT_DATABASE or role != DEVELOPMENT_WRITE_IDENTITY or endpoint is not None:
        raise FixtureDatabaseBindingError("fixture database connection is not the configured local development target")
    # ``tgw.todo`` performs legacy import-time compatibility checks.  Give it
    # this exact DSN before import, then initialize its explicit adapter state.
    os.environ["TGW_TODO_DSN"] = dsn
    todo = importlib.import_module("tgw.todo")
    queue = importlib.import_module("tgw.queue.state_machine")
    todo.init(dsn)
    queue.init(dsn)
    return {"database": database, "role": role, "config_path": str(config_path)}
