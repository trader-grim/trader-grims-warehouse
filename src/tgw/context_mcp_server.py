"""Read-only MCP context bound to an approved standalone Plan and source tree.

The server deliberately reads committed blobs, never mutable checkout bytes.
It exposes navigation and evidence only: it has no approval, queue, deployment,
or provider-effect operation.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from mcp.server import FastMCP

from tgw.code_graph import CodeGraphService, build_snapshot
from tgw.execution_resources import (
    CARD_RESOURCE_NAMES,
    ResourceVerificationError,
    card_resource_receipt,
    content_hash,
    validate_harness_retrieval_attestation,
)
from tgw.plan_graph import live_plan_graph

SCHEMA = "tgw-context-service/v1"
FULL_COMMIT = re.compile(r"[0-9a-f]{40}")
MAX_TEXT_BYTES = 2_000_000
MAX_QUERY = 1_000
MAX_LINES = 250
MAX_RESULTS = 100
MAX_REVIEW_BUNDLE_BYTES = 64 * 1024 * 1024
PLAN_PREFIXES = ("plan/", "pp/", "reference/")
RUNBOOK_PREFIX = "docs/runbooks/"
SCOPE_SEMANTICS = {
    "default_execution_root": "TGW Master Plan",
    "governed_execution_platform_ref": "plan/execution/GOVERNED-EXECUTION-PLATFORM-v1.yaml",
    "platform_w11_completion_implies_master_plan_completion": False,
    "narrow_plan_pp_or_todo_completion_implies_parent_completion": False,
}


class ContextError(RuntimeError):
    """A binding, source, or bounded-query precondition failed."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _path_env(name: str, default: str) -> Path:
    value = Path(os.environ.get(name, default))
    if not value.is_absolute():
        raise ContextError(f"{name} must be an absolute path")
    return value.resolve(strict=True)


def _git_env() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
    }


def _git(root: Path, *args: str, bytes_output: bool = False) -> str | bytes:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={root}", "-C", str(root), *args],
        check=False,
        capture_output=True,
        timeout=30,
        env=_git_env(),
    )
    if result.returncode:
        message = result.stderr.decode(errors="replace").strip()
        raise ContextError(message or f"git {' '.join(args)} failed")
    return result.stdout if bytes_output else result.stdout.decode().strip()


def _approved_commit() -> str:
    commit = os.environ.get("TGW_CONTEXT_PLAN_COMMIT", "")
    if not FULL_COMMIT.fullmatch(commit):
        raise ContextError("TGW_CONTEXT_PLAN_COMMIT must be a full approved commit")
    return commit


def _approved_solution() -> str:
    solution = os.environ.get("TGW_CONTEXT_PLAN_SOLUTION", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", solution):
        raise ContextError("TGW_CONTEXT_PLAN_SOLUTION must be an exact approved solution hash")
    return solution


def _bindings() -> dict[str, Any]:
    plan_root = _path_env("TGW_CONTEXT_PLAN_ROOT", "/opt/TGW/tgw-lib/runtime/approved-plan")
    plan_repository = _path_env("TGW_CONTEXT_PLAN_REPOSITORY", "/opt/TGW/library/plans")
    source_root = _path_env(
        "TGW_CONTEXT_SOURCE_ROOT", "/opt/TGW/tgw-lib/src/trader-grims-warehouse"
    )
    runtime_root = Path(os.environ.get("TGW_CONTEXT_RUNTIME_ROOT", "/opt/TGW/tgw-lib/var/context"))
    if not runtime_root.is_absolute():
        raise ContextError("TGW_CONTEXT_RUNTIME_ROOT must be an absolute path")
    approved = _approved_commit()
    solution = _approved_solution()
    if _git(plan_root, "rev-parse", "HEAD^{commit}") != approved:
        raise ContextError("approved Plan materialization does not match configured commit")
    if _git(plan_root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ContextError("approved Plan materialization is not clean")
    if _git(plan_repository, "cat-file", "-t", approved) != "commit":
        raise ContextError("approved Plan commit is absent from the canonical repository")
    source_commit = _git(source_root, "rev-parse", "HEAD^{commit}")
    assert isinstance(source_commit, str)
    source_status = _git(source_root, "status", "--porcelain=v1", "--untracked-files=all")
    return {
        "plan_root": plan_root,
        "plan_repository": plan_repository,
        "plan_commit": approved,
        "plan_solution_hash": solution,
        "plan_tree": _git(plan_root, "rev-parse", "HEAD^{tree}"),
        "plan_repository_head": _git(plan_repository, "rev-parse", "HEAD^{commit}"),
        "source_root": source_root,
        "source_commit": source_commit,
        "source_tree": _git(source_root, "rev-parse", f"{source_commit}^{{tree}}"),
        "source_worktree_clean": not bool(source_status),
        "source_status_sha256": _sha(str(source_status).encode()),
        "runtime_root": runtime_root,
    }


def _bytes_at(root: Path, commit: str, path: str) -> bytes:
    raw = _git(root, "show", f"{commit}:{path}", bytes_output=True)
    assert isinstance(raw, bytes)
    if len(raw) > MAX_TEXT_BYTES:
        raise ContextError(f"source exceeds {MAX_TEXT_BYTES} byte retrieval bound")
    return raw


def _safe_path(path: str, prefixes: tuple[str, ...]) -> str:
    if not isinstance(path, str) or not path or len(path) > 500:
        raise ContextError("path must be a non-empty bounded string")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or ".." in parsed.parts or parsed.as_posix() != path:
        raise ContextError("path must be canonical and repository-relative")
    if not path.startswith(prefixes):
        raise ContextError("path is outside the admitted context roots")
    return path


def _chunk(root: Path, commit: str, path: str, start_line: int, max_lines: int) -> dict[str, Any]:
    if type(start_line) is not int or start_line < 1:
        raise ContextError("start_line must be a positive integer")
    if type(max_lines) is not int or not 1 <= max_lines <= MAX_LINES:
        raise ContextError(f"max_lines must be between 1 and {MAX_LINES}")
    raw = _bytes_at(root, commit, path)
    lines = raw.decode("utf-8").splitlines()
    selected = lines[start_line - 1:start_line - 1 + max_lines]
    return {
        "schema": "tgw-context-source-chunk/v1", "commit": commit, "path": path,
        "sha256": _sha(raw), "bytes": len(raw), "total_lines": len(lines),
        "start_line": start_line,
        "end_line": start_line + len(selected) - 1 if selected else start_line - 1,
        "content": "\n".join(selected),
    }


@lru_cache(maxsize=4)
def _code_snapshot(root_text: str, commit: str) -> dict[str, Any]:
    return build_snapshot(Path(root_text), commit)


def context_status() -> dict[str, Any]:
    binding = _bindings()
    plan_root = binding["plan_repository"]
    plan_commit = binding["plan_commit"]
    assert isinstance(plan_root, Path) and isinstance(plan_commit, str)
    source_root = binding["source_root"]
    source_commit = binding["source_commit"]
    assert isinstance(source_root, Path) and isinstance(source_commit, str)
    graph = _code_snapshot(str(source_root), source_commit)
    identities = {}
    for path in (
        "plan/SPEC-plan-capability-graph-v2.md",
        "plan/TGW-Master-Plan.md",
        "plan/execution/GOVERNED-EXECUTION-PLATFORM-v1.yaml",
    ):
        raw = _bytes_at(plan_root, plan_commit, path)
        identities[path] = {"sha256": _sha(raw), "bytes": len(raw)}
    result = {
        "schema": SCHEMA, "ok": True, "host_role": "tgw-lib-authoritative-context",
        "plan": {
            "repository": str(plan_root), "approved_materialization": str(binding["plan_root"]),
            "approved_commit": plan_commit, "approved_tree": binding["plan_tree"],
            "approved_solution_hash": binding["plan_solution_hash"],
            "evidence_head": binding["plan_repository_head"], "sources": identities,
        },
        "source": {
            "repository": str(source_root), "commit": source_commit, "tree": binding["source_tree"],
            "working_tree_clean": binding["source_worktree_clean"],
            "status_sha256": binding["source_status_sha256"],
        },
        "code_graph": {key: graph[key] for key in ("commit", "tree", "freshness_hash", "capabilities")},
        "scope_semantics": dict(SCOPE_SEMANTICS),
    }
    result["context_sha256"] = _sha(_canonical(result))
    return result


def plan_graph(task: str, receiver: str = "codex", operation: str = "brief", limit: int = 12) -> dict[str, Any]:
    if not isinstance(task, str) or not task.strip() or len(task) > MAX_QUERY:
        raise ContextError("task must be a non-empty bounded string")
    binding = _bindings()
    result = live_plan_graph(
        binding["plan_root"], task, receiver=receiver, operation=operation, limit=limit,
        runtime_root=binding["runtime_root"],
        approved_plan_commit=binding["plan_commit"],
        approved_solution_hash=binding["plan_solution_hash"],
    )
    if result["plan_commit"] != binding["plan_commit"]:
        raise ContextError("Plan Graph did not bind the approved Plan commit")
    result["scope_semantics"] = dict(SCOPE_SEMANTICS)
    return result


def source_chunk(path: str, start_line: int = 1, max_lines: int = 200) -> dict[str, Any]:
    binding = _bindings()
    path = _safe_path(path, PLAN_PREFIXES)
    result = _chunk(binding["plan_repository"], binding["plan_commit"], path, start_line, max_lines)
    result["authority"] = "standalone-plan"
    return result


def runbooks(query: str = "", path: str = "", start_line: int = 1, max_lines: int = 200, limit: int = 20) -> dict[str, Any]:
    binding = _bindings()
    root, commit = binding["source_root"], binding["source_commit"]
    assert isinstance(root, Path) and isinstance(commit, str)
    if path:
        result = _chunk(root, commit, _safe_path(path, (RUNBOOK_PREFIX,)), start_line, max_lines)
        result["authority"] = "committed-application-runbook"
        return result
    if not isinstance(query, str) or len(query) > MAX_QUERY:
        raise ContextError("query must be a bounded string")
    if type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
        raise ContextError(f"limit must be between 1 and {MAX_RESULTS}")
    tokens = sorted(set(re.findall(r"[a-z0-9_-]{3,}", query.casefold())))
    paths = _git(root, "ls-tree", "-r", "--name-only", commit, "--", RUNBOOK_PREFIX)
    assert isinstance(paths, str)
    matches = []
    for candidate in paths.splitlines():
        if not candidate.endswith(".md"):
            continue
        raw = _bytes_at(root, commit, candidate)
        haystack = f"{candidate}\n{raw.decode('utf-8')}".casefold()
        score = sum(haystack.count(token) for token in tokens)
        if query.casefold().strip() and score == 0:
            continue
        matches.append({"path": candidate, "sha256": _sha(raw), "bytes": len(raw), "score": score})
    matches.sort(key=lambda item: (-item["score"], item["path"]))
    return {"schema": "tgw-context-runbook-index/v1", "commit": commit, "tree": binding["source_tree"], "query": query, "matches": matches[:limit]}


def code_graph(operation: str = "status", query: str = "", limit: int = 20) -> dict[str, Any]:
    if not isinstance(query, str) or len(query) > MAX_QUERY:
        raise ContextError("query must be a bounded string")
    binding = _bindings()
    snapshot = _code_snapshot(str(binding["source_root"]), str(binding["source_commit"]))
    result = CodeGraphService(snapshot).query(operation, query, limit)
    result["binding"] = {key: snapshot[key] for key in ("commit", "tree", "freshness_hash")}
    return result


def context_bundle(task: str, receiver: str = "codex", limit: int = 12) -> dict[str, Any]:
    if not isinstance(task, str) or not task.strip() or len(task) > MAX_QUERY:
        raise ContextError("task must be a non-empty bounded string")
    result = {
        "schema": "tgw-context-task-bundle/v1", "task": task, "receiver": receiver,
        "status": context_status(), "plan_graph": plan_graph(task, receiver, limit=limit),
        "runbooks": runbooks(query=task, limit=min(limit, 20)), "code_graph": code_graph(),
        "instructions": [
            "Retrieve cited Plan and runbook chunks before changing code.",
            "Use CodeGraph queries against the bound source commit; do not infer from a worktree.",
            "Report Plan, PP, Todo, implementation, deployment, and live status separately.",
            "Never describe platform W11 completion as completion of the TGW Master Plan.",
        ],
    }
    result["bundle_sha256"] = _sha(_canonical(result))
    return result


class HTTPReviewContextBrokerClient:
    """Call the privileged broker from the separately operated context service."""

    def __init__(self, endpoint: str) -> None:
        parsed = urlsplit(endpoint) if isinstance(endpoint, str) else None
        if (
            parsed is None
            or parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "::1"}
            or parsed.port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ContextError("governed review context broker endpoint is invalid")
        credential = os.environ.get("TGW_CONTEXT_BROKER_REQUEST_CREDENTIAL", "")
        if not credential or any(character.isspace() for character in credential):
            raise ContextError("governed review context broker credential is unavailable")
        self.endpoint = endpoint.rstrip("/")
        self.authorization = "Bearer " + credential

    def execute(self, request_value: Mapping[str, Any]) -> dict[str, Any]:
        raw = _canonical(request_value)
        request = Request(
            self.endpoint + "/v1/review-context", data=raw, method="POST",
            headers={
                "Accept": "application/json", "Content-Type": "application/json",
                "Authorization": self.authorization,
            },
        )
        try:
            with urlopen(request, timeout=30) as response:  # nosec: protected configured endpoint
                body = response.read(MAX_REVIEW_BUNDLE_BYTES + 1)
        except (HTTPError, URLError, OSError) as exc:
            raise ContextError("governed review context broker failed") from exc
        if len(body) > MAX_REVIEW_BUNDLE_BYTES:
            raise ContextError("governed review context broker response exceeds its bound")
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContextError("governed review context broker response is invalid") from exc
        if not isinstance(value, dict):
            raise ContextError("governed review context broker response is invalid")
        return value


def _review_context_run(
    *, challenge: str, card_json: str, handoff_hash: str,
    resource_receipt_hash: str, skill_contract_hash: str,
    grant_json: str,
    broker_factory: Any = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch every review-card resource inside one authenticated service run."""

    if re.fullmatch(r"[0-9a-f]{64}", challenge) is None:
        raise ContextError("governed review challenge is invalid")
    if not isinstance(card_json, str) or not card_json or len(card_json.encode()) > MAX_TEXT_BYTES:
        raise ContextError("governed review card framing is invalid")
    if (
        not isinstance(grant_json, str)
        or not grant_json
        or len(grant_json.encode()) > MAX_TEXT_BYTES
    ):
        raise ContextError("governed review grant framing is invalid")
    try:
        card = json.loads(card_json)
        grant = json.loads(grant_json)
        receipt = card_resource_receipt(card)
    except (json.JSONDecodeError, ResourceVerificationError) as exc:
        raise ContextError("governed review card is invalid") from exc
    if (
        card.get("role") != "independent-review"
        or not isinstance(grant, Mapping)
        or set(grant) != {"schema", "request", "request_hash"}
        or grant.get("schema") != "tgw-governed-review-context-grant/v1"
        or not isinstance(grant.get("request"), Mapping)
        or grant.get("request_hash") != _sha(_canonical(grant.get("request")))
        or receipt["receipt_hash"] != resource_receipt_hash
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", handoff_hash)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", skill_contract_hash)
        or skill_contract_hash != os.environ.get("TGW_CONTEXT_REVIEW_SKILL_CONTRACT_HASH")
    ):
        raise ContextError("governed review context binding is invalid")
    try:
        expected_uid = int(os.environ.get("TGW_CONTEXT_REVIEW_UID", ""))
        expected_gid = int(os.environ.get("TGW_CONTEXT_REVIEW_GID", ""))
    except ValueError as exc:
        raise ContextError("governed review runtime identity is invalid") from exc
    service_id = os.environ.get("TGW_CONTEXT_RESOURCE_SERVICE_ID", "")
    client_id = os.environ.get("TGW_CONTEXT_RESOURCE_SERVICE_CLIENT_ID", "")
    try:
        broker_factory = broker_factory or HTTPReviewContextBrokerClient
        broker = broker_factory(os.environ.get("TGW_CONTEXT_REVIEW_BROKER_ENDPOINT", ""))
        expected_request = {
            "schema": "tgw-context-review-broker-request/v1",
            "client_id": client_id,
            "challenge": challenge,
            "skill_contract_hash": skill_contract_hash,
            "card_hash": card["card_hash"], "role": "independent-review",
            "execution_identity": (
                f"governed-review:{challenge}:uid={expected_uid}:gid={expected_gid}"
            ),
            "handoff_hash": handoff_hash,
            "resource_receipt_hash": resource_receipt_hash,
            "resources": {
                name: card["bindings"][name] for name in sorted(CARD_RESOURCE_NAMES)
            },
            "issued_at": grant["request"].get("issued_at"),
            "not_before": grant["request"].get("not_before"),
            "expires_at": grant["request"].get("expires_at"),
        }
        if grant["request"] != expected_request:
            raise ResourceVerificationError("review context grant binding mismatch")
        service_bundle = broker.execute(expected_request)
        if (
            not isinstance(service_bundle, Mapping)
            or service_bundle.get("schema") != "tgw-context-review-resource-bundle/v1"
            or service_bundle.get("client_id") != client_id
            or service_bundle.get("challenge") != challenge
            or service_bundle.get("skill_contract_hash") != skill_contract_hash
        ):
            raise ResourceVerificationError("review context service bundle binding mismatch")
        unsigned_bundle = dict(service_bundle)
        claimed_bundle_hash = unsigned_bundle.pop("bundle_hash", None)
        if claimed_bundle_hash != _sha(_canonical(unsigned_bundle)):
            raise ResourceVerificationError("review context service bundle hash mismatch")
        attestation = service_bundle.get("retrieval_attestation")
        attestation = validate_harness_retrieval_attestation(
            attestation,
            expected={
                "service_id": service_id, "client_id": client_id,
                "card_hash": card["card_hash"], "role": "independent-review",
                "execution_identity": (
                    f"governed-review:{challenge}:uid={expected_uid}:gid={expected_gid}"
                ),
                "handoff_hash": handoff_hash,
                "resource_receipt_hash": resource_receipt_hash,
                "resources": {
                    name: card["bindings"][name] for name in sorted(CARD_RESOURCE_NAMES)
                },
            },
            attestation_key_id=os.environ.get("TGW_CONTEXT_ATTESTATION_KEY_ID"),
            attestation_public_key=os.environ.get("TGW_CONTEXT_ATTESTATION_PUBLIC_KEY"),
        )
        encoded_resources = service_bundle.get("resources")
        if not isinstance(encoded_resources, Mapping) or set(encoded_resources) != CARD_RESOURCE_NAMES:
            raise ResourceVerificationError("review context service resources are invalid")
        visible_resources = {}
        for name in sorted(CARD_RESOURCE_NAMES):
            encoded = encoded_resources[name]
            binding = card["bindings"][name]
            if (
                not isinstance(encoded, Mapping)
                or set(encoded) != {
                    "ref", "hash", "content_sha256", "content_base64",
                }
                or encoded.get("ref") != binding["ref"]
                or encoded.get("hash") != binding["hash"]
                or not isinstance(encoded.get("content_base64"), str)
            ):
                raise ResourceVerificationError("review context service resource binding mismatch")
            try:
                content = base64.b64decode(encoded["content_base64"], validate=True)
            except (TypeError, ValueError) as exc:
                raise ResourceVerificationError("review context service resource encoding is invalid") from exc
            if (
                content_hash(content) != encoded["content_sha256"]
                or content_hash(content) != binding["hash"]
            ):
                raise ResourceVerificationError("review context service resource content differs")
            visible_resources[name] = {
                "ref": binding["ref"], "hash": binding["hash"],
                "content_sha256": encoded["content_sha256"],
                "content": content.decode("utf-8", errors="replace"),
            }
    except (KeyError, ResourceVerificationError, ValueError) as exc:
        raise ContextError("governed review registered context retrieval failed") from exc
    receipt = {
        "schema": "tgw-context-review-run/v1", "status": "PASS",
        "context_run_id": attestation["run_id"], "challenge": challenge,
        "card_hash": card["card_hash"], "resource_receipt_hash": resource_receipt_hash,
        "skill_contract_hash": skill_contract_hash,
        "runtime_uid": expected_uid, "runtime_gid": expected_gid,
        "retrieval_attestation": attestation,
        "resource_bundle_hash": service_bundle["bundle_hash"],
    }
    return receipt, visible_resources

mcp = FastMCP(name="tgw-context", instructions="Authoritative read-only TGW planning and coding context hosted on tgw-lib.")
governed_review_mcp = FastMCP(
    name="tgw-context-governed-review",
    instructions="Governed-review-only registered TGW context retrieval.",
)


def _json_call(function: Any, *args: Any, **kwargs: Any) -> str:
    try:
        return json.dumps(function(*args, **kwargs), ensure_ascii=False)
    except Exception as exc:  # MCP errors are a bounded, serializable response.
        return json.dumps({"ok": False, "error": str(exc), "error_type": type(exc).__name__})


@mcp.tool()
def tgw_context_status() -> str:
    """Return exact approved Plan, source, CodeGraph, and scope bindings."""
    return _json_call(context_status)


def _tgw_context_bundle(
    task: str, receiver: str = "codex", limit: int = 12, challenge: str = "",
    card_json: str = "", handoff_hash: str = "", resource_receipt_hash: str = "",
    skill_contract_hash: str = "", grant_json: str = "", *, governed_only: bool = False,
) -> str:
    """Return context; a governed review must also open and complete its bound retrieval run."""

    def result() -> dict[str, Any]:
        supplied = (
            challenge, card_json, handoff_hash, resource_receipt_hash,
            skill_contract_hash, grant_json,
        )
        if not any(supplied) and not governed_only:
            return context_bundle(task, receiver, limit)
        if not all(supplied):
            raise ContextError("governed review context arguments must be complete")
        review_receipt, visible_resources = _review_context_run(
            challenge=challenge, card_json=card_json, handoff_hash=handoff_hash,
            resource_receipt_hash=resource_receipt_hash,
            skill_contract_hash=skill_contract_hash, grant_json=grant_json,
        )
        bundle = {
            "task": task, "receiver": receiver,
            "registered_resources": visible_resources,
            "registered_resource_retrieval": review_receipt,
        }
        bundle["bundle_sha256"] = _sha(_canonical(bundle))
        return bundle

    return _json_call(result)


@mcp.tool()
def tgw_context_bundle(
    task: str, receiver: str = "codex", limit: int = 12, challenge: str = "",
    card_json: str = "", handoff_hash: str = "", resource_receipt_hash: str = "",
    skill_contract_hash: str = "", grant_json: str = "",
) -> str:
    """Return ordinary context or complete an explicitly bound governed retrieval."""

    return _tgw_context_bundle(
        task, receiver, limit, challenge, card_json, handoff_hash,
        resource_receipt_hash, skill_contract_hash, grant_json,
    )


@governed_review_mcp.tool(name="tgw_context_bundle")
def governed_review_context_bundle(
    task: str, receiver: str = "codex", limit: int = 12, challenge: str = "",
    card_json: str = "", handoff_hash: str = "", resource_receipt_hash: str = "",
    skill_contract_hash: str = "", grant_json: str = "",
) -> str:
    """Complete one fully bound governed-review registered-resource retrieval."""

    return _tgw_context_bundle(
        task, receiver, limit, challenge, card_json, handoff_hash,
        resource_receipt_hash, skill_contract_hash, grant_json,
        governed_only=True,
    )


@mcp.tool()
def tgw_context_plan_graph(task: str, receiver: str = "codex", operation: str = "brief", limit: int = 12) -> str:
    """Query the Plan Graph derived from the exact approved Plan materialization."""
    return _json_call(plan_graph, task, receiver, operation, limit)


@mcp.tool()
def tgw_context_plan_source(path: str, start_line: int = 1, max_lines: int = 200) -> str:
    """Read a bounded chunk from exact approved standalone-Plan source."""
    return _json_call(source_chunk, path, start_line, max_lines)


@mcp.tool()
def tgw_context_runbooks(query: str = "", path: str = "", start_line: int = 1, max_lines: int = 200, limit: int = 20) -> str:
    """Search or read committed application runbooks from the bound source tree."""
    return _json_call(runbooks, query, path, start_line, max_lines, limit)


@mcp.tool()
def tgw_context_code_graph(operation: str = "status", query: str = "", limit: int = 20) -> str:
    """Query the CodeGraph snapshot bound to committed application source."""
    return _json_call(code_graph, operation, query, limit)


def _sse_binding(*, governed: bool = False) -> tuple[str, int]:
    host = os.environ.get("TGW_CONTEXT_MCP_HOST", "127.0.0.1").strip()
    if not host:
        raise ValueError("TGW_CONTEXT_MCP_HOST must not be empty")
    if governed and host not in {"127.0.0.1", "::1"}:
        raise ValueError("governed review MCP host must be loopback")
    try:
        port = int(os.environ.get("TGW_CONTEXT_MCP_PORT", "8766"))
    except ValueError as exc:
        raise ValueError("TGW_CONTEXT_MCP_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("TGW_CONTEXT_MCP_PORT must be between 1 and 65535")
    return host, port


def main() -> None:
    import sys

    governed = "--governed-review-sse" in sys.argv
    if "--sse" not in sys.argv and not governed:
        mcp.run(transport="stdio")
        return
    from mcp.server.transport_security import TransportSecuritySettings

    host, port = _sse_binding(governed=governed)
    server = governed_review_mcp if governed else mcp
    server.settings.host = host
    server.settings.port = port
    authority = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    server.settings.transport_security = TransportSecuritySettings(
        allowed_hosts=[authority],
        allowed_origins=[f"http://{authority}"],
    )
    server.run(transport="sse")


if __name__ == "__main__":
    main()
