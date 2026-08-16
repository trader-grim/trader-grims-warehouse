"""Authoritative read-only Plan, runbook, and CodeGraph MCP for tgw-lib.

This server deliberately does not expose inventory or production effects.  It
binds every answer to the approved standalone Plan commit and a committed
application source tree.  The advancing Plan repository and mutable source
working tree are diagnostics, never retrieval authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

from mcp.server import FastMCP

from tgw.code_graph import CodeGraphService, build_snapshot
from tgw.plan_graph import live_plan_graph

SCHEMA = "tgw-context-service/v1"
FULL_COMMIT = re.compile(r"[0-9a-f]{40}")
MAX_TEXT_BYTES = 2_000_000
MAX_QUERY = 1_000
MAX_LINES = 250
MAX_RESULTS = 100
PLAN_PREFIXES = ("plan/", "pp/", "reference/")
RUNBOOK_PREFIX = "docs/runbooks/"
SCOPE_SEMANTICS = {
    "default_execution_root": "TGW Master Plan",
    "governed_execution_platform_ref": "plan/execution/GOVERNED-EXECUTION-PLATFORM-v1.yaml",
    "platform_w11_completion_implies_master_plan_completion": False,
    "narrow_plan_pp_or_todo_completion_implies_parent_completion": False,
}


class ContextError(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _path_env(name: str, default: str) -> Path:
    raw = os.environ.get(name, default)
    path = Path(raw)
    if not path.is_absolute():
        raise ContextError(f"{name} must be an absolute path")
    return path.resolve(strict=True)


def _git(root: Path, *args: str, timeout: int = 30) -> str:
    process = subprocess.run(
        ["git", "-c", f"safe.directory={root}", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_git_env(),
    )
    if process.returncode:
        raise ContextError(process.stderr.strip() or f"git {' '.join(args)} failed")
    return process.stdout


def _git_env() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
    }


def _approved_commit() -> str:
    value = os.environ.get("TGW_CONTEXT_PLAN_COMMIT", "")
    if not FULL_COMMIT.fullmatch(value):
        raise ContextError("TGW_CONTEXT_PLAN_COMMIT must be a full approved commit")
    return value


def _bindings() -> dict[str, Any]:
    plan_root = _path_env("TGW_CONTEXT_PLAN_ROOT", "/opt/TGW/tgw-lib/runtime/approved-plan")
    plan_repo = _path_env("TGW_CONTEXT_PLAN_REPOSITORY", "/opt/TGW/library/plans")
    source_root = _path_env(
        "TGW_CONTEXT_SOURCE_ROOT", "/opt/TGW/tgw-lib/src/trader-grims-warehouse"
    )
    runtime_root = Path(
        os.environ.get("TGW_CONTEXT_RUNTIME_ROOT", "/opt/TGW/tgw-lib/var/context")
    )
    if not runtime_root.is_absolute():
        raise ContextError("TGW_CONTEXT_RUNTIME_ROOT must be absolute")

    approved = _approved_commit()
    plan_head = _git(plan_root, "rev-parse", "HEAD^{commit}").strip()
    if plan_head != approved:
        raise ContextError("approved Plan materialization does not match configured commit")
    if _git(plan_root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ContextError("approved Plan materialization is not clean")
    if _git(plan_repo, "cat-file", "-t", approved).strip() != "commit":
        raise ContextError("approved Plan commit is absent from the canonical repository")

    source_commit = _git(source_root, "rev-parse", "HEAD^{commit}").strip()
    source_tree = _git(source_root, "rev-parse", f"{source_commit}^{{tree}}").strip()
    source_status = _git(
        source_root, "status", "--porcelain=v1", "--untracked-files=all"
    )
    return {
        "plan_root": plan_root,
        "plan_repository": plan_repo,
        "plan_commit": approved,
        "plan_tree": _git(plan_root, "rev-parse", "HEAD^{tree}").strip(),
        "plan_repository_head": _git(plan_repo, "rev-parse", "HEAD^{commit}").strip(),
        "source_root": source_root,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "source_worktree_clean": not bool(source_status),
        "source_status_sha256": _sha(source_status.encode()),
        "runtime_root": runtime_root,
    }


def _git_bytes(root: Path, commit: str, path: str) -> bytes:
    process = subprocess.run(
        ["git", "-c", f"safe.directory={root}", "-C", str(root), "show", f"{commit}:{path}"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        env=_git_env(),
    )
    if process.returncode:
        raise ContextError(process.stderr.decode(errors="replace").strip() or f"missing source: {path}")
    if len(process.stdout) > MAX_TEXT_BYTES:
        raise ContextError(f"source exceeds {MAX_TEXT_BYTES} byte retrieval bound")
    return process.stdout


def _safe_source_path(path: str, prefixes: tuple[str, ...]) -> str:
    if not isinstance(path, str) or not path or len(path) > 500:
        raise ContextError("path must be a non-empty bounded string")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or ".." in parsed.parts or str(parsed) != path:
        raise ContextError("path must be canonical and repository-relative")
    if not path.startswith(prefixes):
        raise ContextError("path is outside the admitted context roots")
    return path


def _tree_paths(root: Path, commit: str, prefix: str) -> list[str]:
    return sorted(
        line
        for line in _git(root, "ls-tree", "-r", "--name-only", commit, "--", prefix).splitlines()
        if line
    )


@lru_cache(maxsize=4)
def _code_snapshot(root_text: str, commit: str) -> dict[str, Any]:
    return build_snapshot(Path(root_text), commit)


def context_status() -> dict[str, Any]:
    binding = _bindings()
    plan_repo = binding["plan_repository"]
    plan_commit = binding["plan_commit"]
    source_root = binding["source_root"]
    source_commit = binding["source_commit"]
    graph = _code_snapshot(str(source_root), source_commit)
    identities = {}
    for path in (
        "plan/SPEC-plan-capability-graph-v2.md",
        "plan/TGW-Master-Plan.md",
        "plan/execution/GOVERNED-EXECUTION-PLATFORM-v1.yaml",
    ):
        raw = _git_bytes(plan_repo, plan_commit, path)
        identities[path] = {"sha256": _sha(raw), "bytes": len(raw)}
    result = {
        "schema": SCHEMA,
        "ok": True,
        "host_role": "tgw-lib-authoritative-context",
        "plan": {
            "repository": str(plan_repo),
            "approved_materialization": str(binding["plan_root"]),
            "approved_commit": plan_commit,
            "approved_tree": binding["plan_tree"],
            "evidence_head": binding["plan_repository_head"],
            "sources": identities,
        },
        "source": {
            "repository": str(source_root),
            "commit": source_commit,
            "tree": binding["source_tree"],
            "working_tree_clean": binding["source_worktree_clean"],
            "status_sha256": binding["source_status_sha256"],
        },
        "code_graph": {
            "commit": graph["commit"],
            "tree": graph["tree"],
            "freshness_hash": graph["freshness_hash"],
            "capabilities": graph["capabilities"],
        },
        "scope_semantics": dict(SCOPE_SEMANTICS),
    }
    result["context_sha256"] = _sha(_canonical(result))
    return result


def plan_graph(task: str, receiver: str = "codex", operation: str = "brief", limit: int = 12) -> dict[str, Any]:
    if not isinstance(task, str) or not task.strip() or len(task) > MAX_QUERY:
        raise ContextError("task must be a non-empty bounded string")
    binding = _bindings()
    result = live_plan_graph(
        binding["plan_root"],
        task,
        receiver=receiver,
        operation=operation,
        limit=limit,
        runtime_root=binding["runtime_root"],
    )
    if result["plan_commit"] != binding["plan_commit"]:
        raise ContextError("Plan Graph did not bind the approved Plan commit")
    result["scope_semantics"] = dict(SCOPE_SEMANTICS)
    return result


def source_chunk(path: str, start_line: int = 1, max_lines: int = 200) -> dict[str, Any]:
    path = _safe_source_path(path, PLAN_PREFIXES)
    if type(start_line) is not int or start_line < 1:
        raise ContextError("start_line must be a positive integer")
    if type(max_lines) is not int or not 1 <= max_lines <= MAX_LINES:
        raise ContextError(f"max_lines must be between 1 and {MAX_LINES}")
    binding = _bindings()
    raw = _git_bytes(binding["plan_repository"], binding["plan_commit"], path)
    lines = raw.decode("utf-8").splitlines()
    selected = lines[start_line - 1 : start_line - 1 + max_lines]
    return {
        "schema": "tgw-context-source-chunk/v1",
        "authority": "standalone-plan",
        "commit": binding["plan_commit"],
        "path": path,
        "sha256": _sha(raw),
        "bytes": len(raw),
        "total_lines": len(lines),
        "start_line": start_line,
        "end_line": start_line + len(selected) - 1 if selected else start_line - 1,
        "content": "\n".join(selected),
    }


def runbooks(query: str = "", path: str = "", start_line: int = 1, max_lines: int = 200, limit: int = 20) -> dict[str, Any]:
    binding = _bindings()
    root, commit = binding["source_root"], binding["source_commit"]
    if path:
        path = _safe_source_path(path, (RUNBOOK_PREFIX,))
        result = source_chunk_from_repository(root, commit, path, start_line, max_lines)
        result["authority"] = "committed-application-runbook"
        return result
    if not isinstance(query, str) or len(query) > MAX_QUERY:
        raise ContextError("query must be a bounded string")
    if type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
        raise ContextError(f"limit must be between 1 and {MAX_RESULTS}")
    needle = query.casefold().strip()
    tokens = sorted(set(re.findall(r"[a-z0-9_-]{3,}", needle)))
    matches = []
    for candidate in _tree_paths(root, commit, RUNBOOK_PREFIX):
        if not candidate.endswith(".md"):
            continue
        raw = _git_bytes(root, commit, candidate)
        text = raw.decode("utf-8")
        haystack = f"{candidate}\n{text}".casefold()
        score = sum(haystack.count(token) for token in tokens)
        if needle and needle not in haystack and score == 0:
            continue
        matches.append({
            "path": candidate, "sha256": _sha(raw), "bytes": len(raw),
            "score": score,
        })
    matches.sort(key=lambda item: (-item["score"], item["path"]))
    return {
        "schema": "tgw-context-runbook-index/v1",
        "commit": commit,
        "tree": binding["source_tree"],
        "query": query,
        "matches": matches[:limit],
    }


def source_chunk_from_repository(root: Path, commit: str, path: str, start_line: int, max_lines: int) -> dict[str, Any]:
    if type(start_line) is not int or start_line < 1:
        raise ContextError("start_line must be a positive integer")
    if type(max_lines) is not int or not 1 <= max_lines <= MAX_LINES:
        raise ContextError(f"max_lines must be between 1 and {MAX_LINES}")
    raw = _git_bytes(root, commit, path)
    lines = raw.decode("utf-8").splitlines()
    selected = lines[start_line - 1 : start_line - 1 + max_lines]
    return {
        "schema": "tgw-context-source-chunk/v1",
        "commit": commit,
        "path": path,
        "sha256": _sha(raw),
        "bytes": len(raw),
        "total_lines": len(lines),
        "start_line": start_line,
        "end_line": start_line + len(selected) - 1 if selected else start_line - 1,
        "content": "\n".join(selected),
    }


def code_graph(operation: str = "status", query: str = "", limit: int = 20) -> dict[str, Any]:
    if not isinstance(query, str) or len(query) > MAX_QUERY:
        raise ContextError("query must be a bounded string")
    binding = _bindings()
    snapshot = _code_snapshot(str(binding["source_root"]), binding["source_commit"])
    result = CodeGraphService(snapshot).query(operation, query, limit)
    result["binding"] = {
        "commit": binding["source_commit"],
        "tree": binding["source_tree"],
        "freshness_hash": snapshot["freshness_hash"],
    }
    return result


def context_bundle(task: str, receiver: str = "codex", limit: int = 12) -> dict[str, Any]:
    if not isinstance(task, str) or not task.strip() or len(task) > MAX_QUERY:
        raise ContextError("task must be a non-empty bounded string")
    status = context_status()
    bundle = {
        "schema": "tgw-context-task-bundle/v1",
        "task": task,
        "receiver": receiver,
        "status": status,
        "plan_graph": plan_graph(task, receiver=receiver, limit=limit),
        "runbooks": runbooks(query=task, limit=min(limit, 20)),
        "code_graph": code_graph("status"),
        "instructions": [
            "Retrieve cited Plan and runbook chunks before changing code.",
            "Use CodeGraph queries against the bound source commit; do not infer from a worktree.",
            "Report Plan, PP, Todo, implementation, deployment, and live status separately.",
            "Never describe platform W11 completion as completion of the TGW Master Plan.",
        ],
    }
    bundle["bundle_sha256"] = _sha(_canonical(bundle))
    return bundle


mcp = FastMCP(
    name="tgw-context",
    instructions=(
        "Authoritative read-only TGW planning and coding context hosted on tgw-lib. "
        "Call tgw_context_bundle before coding or reporting Plan completion."
    ),
)


def _json_call(function: Any, *args: Any, **kwargs: Any) -> str:
    try:
        return json.dumps(function(*args, **kwargs), ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc), "error_type": type(exc).__name__})


@mcp.tool()
def tgw_context_status() -> str:
    """Return exact approved Plan, source, CodeGraph, and scope bindings."""
    return _json_call(context_status)


@mcp.tool()
def tgw_context_bundle(task: str, receiver: str = "codex", limit: int = 12) -> str:
    """Return the bounded authoritative starting context for one task."""
    return _json_call(context_bundle, task, receiver, limit)


@mcp.tool()
def tgw_context_plan_graph(task: str, receiver: str = "codex", operation: str = "brief", limit: int = 12) -> str:
    """Query the Plan Graph derived from the exact approved Plan materialization."""
    return _json_call(plan_graph, task, receiver, operation, limit)


@mcp.tool()
def tgw_context_plan_source(path: str, start_line: int = 1, max_lines: int = 200) -> str:
    """Read a bounded line chunk from an exact approved Plan source."""
    return _json_call(source_chunk, path, start_line, max_lines)


@mcp.tool()
def tgw_context_runbooks(query: str = "", path: str = "", start_line: int = 1, max_lines: int = 200, limit: int = 20) -> str:
    """Search or read committed runbooks from the bound application source."""
    return _json_call(runbooks, query, path, start_line, max_lines, limit)


@mcp.tool()
def tgw_context_code_graph(operation: str = "status", query: str = "", limit: int = 20) -> str:
    """Query the CodeGraph snapshot built from the bound committed source tree."""
    return _json_call(code_graph, operation, query, limit)


def _sse_binding() -> tuple[str, int]:
    host = os.environ.get("TGW_CONTEXT_MCP_HOST", "127.0.0.1").strip()
    if not host:
        raise ValueError("TGW_CONTEXT_MCP_HOST must not be empty")
    try:
        port = int(os.environ.get("TGW_CONTEXT_MCP_PORT", "8766"))
    except ValueError as exc:
        raise ValueError("TGW_CONTEXT_MCP_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("TGW_CONTEXT_MCP_PORT must be between 1 and 65535")
    return host, port


def main() -> None:
    import sys

    if "--sse" not in sys.argv:
        mcp.run(transport="stdio")
        return
    from mcp.server.transport_security import TransportSecuritySettings

    host, port = _sse_binding()
    mcp.settings.host = host
    mcp.settings.port = port
    mcp.settings.transport_security = TransportSecuritySettings(
        allowed_hosts=[f"{host}:{port}"],
        allowed_origins=[f"http://{host}:{port}"],
    )
    mcp.run(transport="sse")


if __name__ == "__main__":
    main()
