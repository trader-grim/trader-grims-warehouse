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
from pathlib import Path
from typing import Any


SERVER_SOURCE = Path(
    "/opt/TGW/tgw-lib/actor-runtime/releases/w18-9634e8a7-20260822/src"
)
CONTEXT_SOURCE = Path("/opt/TGW/tgw-lib/src/trader-grims-warehouse")
CATALOG = Path("/opt/TGW/tgw-lib/config/tgw-context-debian-v1.json")
CURRENT_TASK = Path("/opt/TGW/tgw-lib/config/tgw-context-current-task.json")
HARNESS_ACTORS = frozenset({"codex", "claude", "deepseek"})


def _harness_actor() -> str:
    actor = pwd.getpwuid(os.geteuid()).pw_name
    if actor not in HARNESS_ACTORS:
        raise ValueError("MCP process account is not a registered TGW harness actor")
    return actor


def _current_task() -> str:
    actor = _harness_actor()
    observed = CURRENT_TASK.stat(follow_symlinks=False)
    if (
        CURRENT_TASK.is_symlink()
        or not stat.S_ISREG(observed.st_mode)
        or observed.st_uid != 0
        or observed.st_mode & 0o022
        or observed.st_size > 64 * 1024
    ):
        raise ValueError("current TGW task record is not root protected")
    raw = CURRENT_TASK.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("schema") != "tgw-current-task/v1":
        raise ValueError("current TGW task record is invalid")
    result = {
        **value,
        "actor": actor,
        "receiver": actor,
        "record_path": str(CURRENT_TASK),
        "record_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
    }
    return json.dumps(result, sort_keys=True, separators=(",", ":"))


def _bind_context_receiver(context_server: Any) -> None:
    original_plan_graph = context_server.plan_graph
    original_context_bundle = context_server._tgw_context_bundle

    def actor_plan_graph(
        task: str, receiver: str = "", operation: str = "brief", limit: int = 12
    ) -> dict[str, object]:
        return original_plan_graph(task, _harness_actor(), operation, limit)

    def actor_context_bundle(
        task: str,
        receiver: str = "",
        limit: int = 12,
        challenge: str = "",
        card_json: str = "",
        handoff_hash: str = "",
        resource_receipt_hash: str = "",
        skill_contract_hash: str = "",
        grant_json: str = "",
        *,
        governed_only: bool = False,
    ) -> str:
        return original_context_bundle(
            task,
            _harness_actor(),
            limit,
            challenge,
            card_json,
            handoff_hash,
            resource_receipt_hash,
            skill_contract_hash,
            grant_json,
            governed_only=governed_only,
        )

    context_server.plan_graph = actor_plan_graph
    context_server._tgw_context_bundle = actor_context_bundle


def main() -> None:
    if socket.gethostname().split(".", 1)[0] != "tgw-lib":
        raise SystemExit("tgw-context-mcp is available only on tgw-lib")
    for path in (SERVER_SOURCE, CONTEXT_SOURCE, CATALOG, CURRENT_TASK):
        if not path.exists():
            raise SystemExit(f"required TGW context input is unavailable: {path}")

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
            "TGW_CONTEXT_RUNTIME_ROOT": "/opt/TGW/tgw-lib/var/context",
            "TGW_CONTEXT_ENVIRONMENT_CATALOG": str(CATALOG),
            "TGW_CONTEXT_ENVIRONMENT_CATALOG_HASH": "sha256:8f81f755a25cb54b53c751f8fa6b554f5076cc8106dd91ee395fe9a8206e9894",
        }
    )
    sys.path.insert(0, str(SERVER_SOURCE))
    from tgw import context_mcp_server

    _bind_context_receiver(context_mcp_server)
    context_main = context_mcp_server.main
    mcp = context_mcp_server.mcp

    @mcp.tool()
    def tgw_context_current_task() -> str:
        """Return the current TGW task bound to this Linux harness actor."""
        return _current_task()

    context_main()


if __name__ == "__main__":
    main()
