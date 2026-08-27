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
        coding_cli.coding_lifecycle.LifecycleError,
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
_LEGACY_START = coding_cli.start
_LEGACY_STATUS = coding_cli.status


@mcp.tool()
def tgw_coding_start(todo_id: int | str, source_commit: str = "") -> str:
    """Create/reuse one detached durable Plan-bound coding lifecycle.

    The call returns after the root and supervisor are durable; the caller is
    not required for subsequent stage or restart recovery.
    """
    return _result(
        "start",
        (
            coding_cli.start
            if coding_cli.start is not _LEGACY_START
            else coding_cli.lifecycle_start
        ),
        todo_id,
        config_path=_config_path(),
        source_commit=source_commit or None,
    )


@mcp.tool()
def tgw_coding_resume(todo_id: int, source_commit: str = "") -> str:
    """Reopen the same exact RESUMABLE_PARTIAL lifecycle and implementation."""

    return _result(
        "resume",
        coding_cli.resume,
        todo_id,
        config_path=_config_path(),
        source_commit=source_commit or None,
    )


@mcp.tool()
def tgw_coding_reconcile(pp_ref: str = coding_cli.PP_REF) -> str:
    """Return read-only PP reconciliation and exact native/Luet binding status."""
    return _result("reconcile", coding_cli.reconcile, pp_ref, config_path=_config_path())


@mcp.tool()
def tgw_coding_status(todo_id: int | str | None = None) -> str:
    """Return local worktree, access, Foreman, and coding-job status."""
    return _result(
        "status",
        (
            coding_cli.status
            if coding_cli.status is not _LEGACY_STATUS
            else coding_cli.consolidated_status
        ),
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
def tgw_coding_operator_readback(root_id: str, decision: str = "") -> str:
    """Durably record explicit readback and optional accept/reject decision."""

    normalized = decision or None
    if normalized not in {None, "accept", "reject"}:
        return json.dumps({
            "schema": "tgw-local-coding-mcp-error/v1",
            "ok": False,
            "operation": "operator-readback",
            "error": "decision must be accept, reject, or empty",
            "error_type": "ValueError",
        }, sort_keys=True)
    return _result(
        "operator-readback",
        coding_cli.operator_action,
        root_id,
        decision=normalized,
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
