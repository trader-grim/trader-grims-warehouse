#!/opt/TGW/.venvs/controller/bin/python3
"""Launch the shared read-only TGW context MCP on the Debian development host."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import stat
import sys
from pathlib import Path


SERVER_SOURCE = Path(
    "/opt/TGW/tgw-lib/actor-runtime/releases/w18-9634e8a7-20260822/src"
)
CONTEXT_SOURCE = Path("/opt/TGW/tgw-lib/src/trader-grims-warehouse")
CATALOG = Path("/opt/TGW/tgw-lib/config/tgw-context-debian-v1.json")
CURRENT_TASK = Path("/opt/TGW/tgw-lib/config/tgw-context-current-task.json")


def _current_task(actor: str) -> str:
    if actor and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", actor) is None:
        raise ValueError("actor must be a canonical Linux account name")
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
        "receiver": actor or None,
        "record_path": str(CURRENT_TASK),
        "record_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
    }
    return json.dumps(result, sort_keys=True, separators=(",", ":"))


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
    from tgw.context_mcp_server import main as context_main, mcp

    @mcp.tool()
    def tgw_context_current_task(actor: str = "") -> str:
        """Return the root-owned current TGW task handoff for a declared actor."""
        return _current_task(actor)

    context_main()


if __name__ == "__main__":
    main()
