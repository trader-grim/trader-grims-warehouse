"""MCP adapter for TGW's ordinary local Unix-user coding workflow.

This server is deliberately separate from the read-only Context MCP.  It does
not implement a second dispatcher or an authority service: every tool calls
the same ``tgw.coding_cli`` functions used by ``tgw coding``.  The MCP process
runs as the harness Linux account, and the shared workflow verifies that the
account belongs to ``tgw-coders`` before reading or changing coding state.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

from mcp.server import FastMCP

from tgw import coding_cli


def _config_path() -> Path:
    return Path(os.environ.get("TGW_CODING_CONFIG", coding_cli.DEFAULT_CONFIG))


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _result(operation: str, function: Any, *args: Any, **kwargs: Any) -> str:
    try:
        value = function(*args, **kwargs)
    except (
        coding_cli.CodingCLIError,
        coding_cli.LocalCodingWorkflowError,
        coding_cli.PlanTodoSourceError,
        OSError,
        ValueError,
    ) as exc:
        value = {
            "schema": "tgw-local-coding-mcp-error/v1",
            "ok": False,
            "operation": operation,
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
    return json.dumps(value, sort_keys=True, default=_json_default)


mcp = FastMCP(
    name="tgw-coding",
    instructions=(
        "Local tgw-lib coding controls. These tools call the same ordinary "
        "Unix-user/tgw-coders implementation as `tgw coding`; they never "
        "dispatch through tgw-prod, coding-provision, or an approval service."
    ),
)


@mcp.tool()
def tgw_coding_start(todo_id: int | str, source_commit: str = "") -> str:
    """Bind and dispatch one existing Plan Todo through the local workflow.

    The result includes the exact group-owned worktree and correctly rooted
    optional interactive-session command.  The eligible automated treatment
    is already dispatched by this call.
    """
    return _result(
        "start",
        coding_cli.start,
        todo_id,
        config_path=_config_path(),
        source_commit=source_commit or None,
    )


@mcp.tool()
def tgw_coding_reconcile(pp_ref: str = coding_cli.PP_REF) -> str:
    """Return read-only PP reconciliation and exact native/Luet binding status."""
    return _result("reconcile", coding_cli.reconcile, pp_ref, config_path=_config_path())


@mcp.tool()
def tgw_coding_status(todo_id: int | None = None) -> str:
    """Return local worktree, access, Foreman, and coding-job status."""
    return _result(
        "status",
        coding_cli.status,
        todo_id,
        config_path=_config_path(),
    )


@mcp.tool()
def tgw_coding_log(job_id: str) -> str:
    """Return one durable local coding job and its receipt-bearing payload."""
    return _result(
        "log",
        coding_cli.job_log,
        job_id,
        config_path=_config_path(),
    )


@mcp.tool()
def tgw_coding_stop(job_id: str) -> str:
    """Cancel one active local coding job without deleting its evidence."""
    return _result(
        "stop",
        coding_cli.stop,
        job_id,
        config_path=_config_path(),
    )


@mcp.tool()
def tgw_coding_access_status(todo_id: int | None = None) -> str:
    """Prove the calling Linux actor's local Unix/group and workflow binding."""
    return _result(
        "access-status",
        coding_cli.status,
        todo_id,
        config_path=_config_path(),
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
