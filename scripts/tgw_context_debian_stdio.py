#!/opt/TGW/.venvs/controller/bin/python3
"""Launch the shared read-only TGW context MCP on the Debian development host."""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import socket
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

SERVER_SOURCE = Path("/opt/TGW/tgw-lib/context-runtime/src")
CONTEXT_SOURCE = Path("/opt/TGW/tgw-lib/src/trader-grims-warehouse")
CATALOG = Path("/opt/TGW/tgw-lib/config/tgw-context-debian-v1.json")
CURRENT_CONTEXT = Path("/opt/TGW/tgw-lib/config/tgw-context-current.json")
HARNESS_ACTORS = frozenset({"codex", "claude", "deepseek"})
RETIRED_TOOLS = ("tgw_context_bundle", "tgw_context_confirm_rebind")

sys.path.insert(0, str(SERVER_SOURCE))

from tgw.current_context_snapshot import CurrentContextError  # noqa: E402
from tgw.current_context_snapshot import parse as parse_snapshot  # noqa: E402


def _harness_actor() -> str:
    actor = pwd.getpwuid(os.geteuid()).pw_name
    if actor not in HARNESS_ACTORS:
        raise ValueError("MCP process account is not a registered TGW harness actor")
    return actor


def _current_context() -> dict[str, Any]:
    """Load the one atomic task/cursor snapshot exposed by this MCP."""
    observed = CURRENT_CONTEXT.stat(follow_symlinks=False)
    if (
        CURRENT_CONTEXT.is_symlink()
        or not stat.S_ISREG(observed.st_mode)
        or observed.st_mode & 0o022
        or observed.st_size > 256 * 1024
    ):
        raise ValueError("current TGW context snapshot is not protected read-only data")
    raw = CURRENT_CONTEXT.read_bytes()
    try:
        value = parse_snapshot(json.loads(raw))
    except (json.JSONDecodeError, CurrentContextError) as exc:
        raise ValueError("current TGW context snapshot is invalid") from exc
    value["record_path"] = str(CURRENT_CONTEXT)
    value["record_sha256"] = "sha256:" + hashlib.sha256(raw).hexdigest()
    return value


def _current_task() -> str:
    actor = _harness_actor()
    context = _current_context()
    value = context["task"]
    result = {
        **value,
        "actor": actor,
        "receiver": actor,
        "context": {
            key: context[key]
            for key in (
                "active_capability", "active_treatment", "plan_commit",
                "source_commit", "snapshot_sha256",
            )
        },
        "record_path": context["record_path"],
        "record_sha256": context["record_sha256"],
    }
    return json.dumps(result, sort_keys=True, separators=(",", ":"))


def _bind_context_receiver(context_server: Any) -> None:
    original_plan_graph = context_server.plan_graph

    def actor_plan_graph(
        task: str, receiver: str = "", operation: str = "brief", limit: int = 12
    ) -> dict[str, object]:
        context = _current_context()
        selected = context["active_capability"] if task in {"", "current"} else task
        result = original_plan_graph(selected, _harness_actor(), operation, limit)
        result["current_context"] = {
            key: context[key]
            for key in (
                "active_capability", "active_treatment", "plan_commit",
                "source_commit", "snapshot_sha256",
            )
        }
        return result

    context_server.plan_graph = actor_plan_graph


def _retire_obsolete_tools(mcp: Any) -> None:
    for name in RETIRED_TOOLS:
        mcp.remove_tool(name)


def context_server_bundle(context_server: Any, task: str, limit: int) -> str:
    """Compose only existing read-only local bindings for the current actor."""

    def build() -> dict[str, Any]:
        if not isinstance(task, str) or not task.strip() or len(task) > 1_000:
            raise context_server.ContextError("task must be ordinary non-empty text")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise context_server.ContextError("limit must be between 1 and 50")
        actor = _harness_actor()
        snapshot = _current_context()
        selected = snapshot["active_capability"] if task == "current" else task
        status = context_server.context_status()
        plan_graph = context_server.plan_graph(selected, actor, "brief", limit)
        runbooks = context_server.runbooks(selected, "", 1, 200, limit, "all")
        code_graph = context_server.code_graph("status", "", limit)
        result = {
            "schema": "tgw-context-bundle/v2-local-read-only",
            "ok": True,
            "task": selected,
            "actor": actor,
            "receiver": actor,
            "status": status,
            "plan_graph": plan_graph,
            "runbooks": runbooks,
            "code_graph": code_graph,
            "current_context": {
                key: snapshot[key]
                for key in (
                    "active_capability", "active_treatment", "plan_commit",
                    "source_commit", "source_tree", "snapshot_sha256",
                )
            },
            "dependencies": {
                "authority": False, "grant": False, "approval": False,
                "admission": False, "dispatch": False, "execution": False,
                "database": False, "queue": False, "tgw_prod": False,
                "provider": False,
            },
        }
        result["bundle_sha256"] = "sha256:" + hashlib.sha256(
            json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return result

    return context_server._json_call(build)


def main() -> None:
    if socket.gethostname().split(".", 1)[0] != "tgw-lib":
        raise SystemExit("tgw-context-mcp is available only on tgw-lib")
    for path in (SERVER_SOURCE, CONTEXT_SOURCE, CATALOG, CURRENT_CONTEXT):
        if not path.exists():
            raise SystemExit(f"required TGW context input is unavailable: {path}")

    with tempfile.TemporaryDirectory(prefix=f"tgw-context-{_harness_actor()}-") as runtime:
        os.environ.update(
        {
            "HOME": str(Path.home()),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(SERVER_SOURCE),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_OPTIONAL_LOCKS": "0",
            "TGW_CONTEXT_PLAN_ROOT": "/opt/TGW/library/approved/058e2f980201cc78245358e4901cf007063f2c29",
            "TGW_CONTEXT_PLAN_REPOSITORY": "/opt/TGW/library/plans",
            "TGW_CONTEXT_PLAN_COMMIT": "058e2f980201cc78245358e4901cf007063f2c29",
            "TGW_CONTEXT_PLAN_SOLUTION": "sha256:ecce15aad2699492c0c5577bff1af7005ffbbec6ae6166b325b34c1cc7e70e9f",
            "TGW_CONTEXT_SOURCE_ROOT": str(CONTEXT_SOURCE),
            "TGW_CONTEXT_RUNTIME_ROOT": runtime,
            "TGW_CONTEXT_ENVIRONMENT_CATALOG": str(CATALOG),
            "TGW_CONTEXT_ENVIRONMENT_CATALOG_HASH": "sha256:8f81f755a25cb54b53c751f8fa6b554f5076cc8106dd91ee395fe9a8206e9894",
        }
        )
        from tgw import context_mcp_server
        from tgw.local_context_runtime import install as install_local_context

        install_local_context(
            context_mcp_server, current_context=_current_context, actor=_harness_actor
        )
        _bind_context_receiver(context_mcp_server)
        context_main = context_mcp_server.main
        mcp = context_mcp_server.mcp
        _retire_obsolete_tools(mcp)

        @mcp.tool()
        def tgw_context_bundle(task: str = "current", limit: int = 12) -> str:
            """Return the exact actor-bound local planning and source bindings."""
            return context_server_bundle(context_mcp_server, task, limit)

        @mcp.tool()
        def tgw_context_current_task() -> str:
            """Return the current TGW task bound to this Linux harness actor."""
            return _current_task()

        context_main()


if __name__ == "__main__":
    main()
