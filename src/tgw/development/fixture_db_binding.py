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


_FIXTURE_TODO_COLUMNS = frozenset({"reasoning", "status_note"})


def _require_fixture_todo_schema(cursor: Any) -> None:
    """Refuse fixture writes unless the already-installed Todo schema is exact."""
    cursor.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'todo_items' "
        "AND column_name = ANY(%s)",
        (list(_FIXTURE_TODO_COLUMNS),),
    )
    found = {row[0] for row in cursor.fetchall()}
    missing = sorted(_FIXTURE_TODO_COLUMNS - found)
    if missing:
        raise FixtureDatabaseBindingError(
            "fixture Todo schema is missing required columns: " + ", ".join(missing)
        )


def _explicit_dsn(config_path: Path) -> str:
    if not config_path.is_file():
        raise FixtureDatabaseBindingError("fixture database config is missing")
    raw = load_json_strict(config_path)
    if not isinstance(raw, dict) or not isinstance(raw.get("postgres_dsn"), str) or not raw["postgres_dsn"].strip():
        raise FixtureDatabaseBindingError("fixture database requires an explicit postgres_dsn")
    # Go through the established application loader as well as requiring the
    # raw key, so its legacy fallback can never satisfy this fixture contract.
    return str(load_config(config_path)["postgres_dsn"])


def fixture_worker_config(
    coding: dict[str, Any],
) -> dict[str, Any]:
    """Bind a fixture CodingWorker to the exact preflighted local DSN.

    QueueWorker initializes its state-machine adapter from top-level worker
    configuration.  Re-reading mutable configuration here could redirect the
    worker after preflight, so use only the DSN bound by
    ``initialize_fixture_database``.
    """
    if not isinstance(coding, dict):
        raise FixtureDatabaseBindingError("fixture coding configuration must be an object")
    dsn = os.environ.get("TGW_TODO_DSN")
    if not isinstance(dsn, str) or not dsn.strip():
        raise FixtureDatabaseBindingError("fixture worker has no preflighted database binding")
    return {"postgres_dsn": dsn, "coding": dict(coding)}


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
            _require_fixture_todo_schema(cur)
    except FixtureDatabaseBindingError:
        raise
    except Exception as exc:
        raise FixtureDatabaseBindingError("fixture development database preflight failed") from exc
    if database != DEVELOPMENT_DATABASE or role != DEVELOPMENT_WRITE_IDENTITY or endpoint is not None:
        raise FixtureDatabaseBindingError("fixture database connection is not the configured local development target")
    # Set the explicit DSN before importing the side-effect-free Todo adapter;
    # neither import nor init is allowed to perform schema work or use a
    # legacy fallback in this fixture path.
    os.environ["TGW_TODO_DSN"] = dsn
    todo = importlib.import_module("tgw.todo")
    queue = importlib.import_module("tgw.queue.state_machine")
    todo.init(dsn)
    queue.init(dsn)
    return {"database": database, "role": role, "config_path": str(config_path)}
