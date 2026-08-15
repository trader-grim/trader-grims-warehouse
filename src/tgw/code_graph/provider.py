"""Commit-bound Python symbol, import, reference, and dependency graph.

This minimum provider deliberately does not claim invariant extraction or
runtime tracing.  It reads Git blobs from an exact commit, never the mutable
working tree.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

SCHEMA = "tgw-code-graph-snapshot/v1"
SERVICE_SCHEMA = "tgw-code-graph-service/v1"
MAX_LIMIT = 100
_INVARIANT = re.compile(r"\b[CE]\d+[A-Z]?\b", re.IGNORECASE)


class CodeGraphError(RuntimeError):
    pass


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={repo}", "-C", str(repo), *args],
        check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise CodeGraphError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _module(path: str) -> str:
    parts = path.removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: str, module: str):
        self.path, self.module = path, module
        self.scope: list[str] = []
        self.symbols: list[dict[str, Any]] = []
        self.imports: list[dict[str, Any]] = []
        self.names: list[dict[str, Any]] = []

    def _symbol(self, node: ast.AST, name: str, kind: str) -> None:
        qualname = ".".join((*self.scope, name))
        self.symbols.append({
            "id": f"python:{self.path}:{qualname}", "name": name,
            "qualname": qualname, "kind": kind, "path": self.path,
            "module": self.module, "line": node.lineno,
        })

    def _definition(self, node: ast.AST, name: str, kind: str) -> None:
        self._symbol(node, name, kind)
        self.scope.append(name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._definition(node, node.name, "class")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._definition(node, node.name, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._definition(node, node.name, "async-function")

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            self.imports.append({"source": self.module, "target": item.name, "line": node.lineno, "kind": "import"})

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        target = "." * node.level + (node.module or "")
        self.imports.append({
            "source": self.module, "target": target, "line": node.lineno,
            "kind": "from", "names": sorted(item.name for item in node.names),
        })

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.names.append({
                "source_scope": ".".join(self.scope) or "<module>",
                "name": node.id, "line": node.lineno,
            })


def build_snapshot(repository: Path, revision: str = "HEAD") -> dict[str, Any]:
    """Build from committed blobs at revision; dirty files cannot affect output."""
    repo = repository.resolve()
    commit = _git(repo, "rev-parse", f"{revision}^{{commit}}").strip()
    tree = _git(repo, "rev-parse", f"{commit}^{{tree}}").strip()
    paths = sorted(path for path in _git(repo, "ls-tree", "-r", "--name-only", commit).splitlines() if path.endswith(".py"))
    files, symbols, imports, pending_names, parse_errors = [], [], [], [], []
    invariant_locations: dict[str, list[dict[str, Any]]] = {}
    receipts: list[dict[str, Any]] = []
    for path in paths:
        source = _git(repo, "show", f"{commit}:{path}")
        blob_sha = hashlib.sha256(source.encode()).hexdigest()
        files.append({"path": path, "sha256": blob_sha, "module": _module(path)})
        declared_detectors = {
            match.upper() for match in re.findall(
                r"Invariant\s+([CE]\d+[A-Z]?)[^\n]{0,100}\bdetector\b",
                source, re.IGNORECASE,
            )
        }
        for line_number, line in enumerate(source.splitlines(), 1):
            for invariant in sorted(set(token.upper() for token in _INVARIANT.findall(line))):
                invariant_locations.setdefault(invariant, []).append({
                    "path": path, "line": line_number, "blob_sha256": blob_sha,
                    "kind": "detector-test" if path.startswith("tests/") and (
                        "invariant" in Path(path).name or invariant in declared_detectors
                    ) else
                    "test-reference" if path.startswith("tests/") else "source-reference",
                })
        try:
            parsed = ast.parse(source, filename=path)
        except SyntaxError as exc:
            parse_errors.append({"path": path, "line": exc.lineno, "error": exc.msg})
            continue
        visitor = _Visitor(path, _module(path))
        visitor.visit(parsed)
        symbols.extend(visitor.symbols)
        imports.extend(visitor.imports)
        pending_names.extend({**name, "module": visitor.module, "path": path} for name in visitor.names)

    local = {(item["module"], item["name"]): item["id"] for item in symbols}
    references = []
    for item in pending_names:
        target = local.get((item["module"], item["name"]))
        if target:
            references.append({
                "source": f"python:{item['path']}:{item['source_scope']}",
                "target": target, "name": item["name"], "line": item["line"],
                "resolution": "module-local-exact",
            })
    symbols.sort(key=lambda item: item["id"])
    imports.sort(key=lambda item: (item["source"], item["line"], item["target"]))
    references.sort(key=lambda item: (item["source"], item["line"], item["target"]))
    invariants = []
    for invariant, locations in sorted(invariant_locations.items()):
        detector = any(item["kind"] == "detector-test" for item in locations)
        invariants.append({
            "id": invariant,
            "status": "detector-test-present" if detector else "referenced-only",
            "evidence": locations[:50],
            "evidence_count": len(locations),
            "evidence_truncated": len(locations) > 50,
            "binding": {"commit": commit, "tree": tree},
        })
    for path in sorted(
        item for item in _git(repo, "ls-tree", "-r", "--name-only", commit).splitlines()
        if item.startswith("agent-services/receipts/") and item.endswith(".json")
    ):
        raw = _git(repo, "show", f"{commit}:{path}")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
        receipts.append({
            "path": path, "blob_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "schema": payload.get("schema") if isinstance(payload, Mapping) else None,
            "source_commit": payload.get("source_commit") if isinstance(payload, Mapping) else None,
            "status": payload.get("status") if isinstance(payload, Mapping) else None,
            "parse_status": "valid-json" if payload is not None else "invalid-json",
            "binding": {"snapshot_commit": commit, "snapshot_tree": tree},
        })
    core = {
        "schema": SCHEMA, "repository": str(repo), "commit": commit, "tree": tree,
        "languages": ["python"], "files": files, "symbols": symbols,
        "imports": imports, "dependencies": imports, "references": references,
        "invariants": invariants, "execution_receipts": receipts,
        "parse_errors": parse_errors,
        "capabilities": {
            "symbols": "available", "imports": "available",
            "references": "partial:module-local-exact-only",
            "dependencies": "available:syntactic-imports",
            "invariants": "partial:committed-references-and-detector-tests",
            "execution_receipts": "available:committed-receipt-metadata" if receipts else "unavailable:none-at-commit",
            "runtime_traces": "unavailable:not-collected",
        },
    }
    # The freshness identity is clone/host neutral; repository is diagnostic only.
    freshness_payload = {key: value for key, value in core.items() if key != "repository"}
    core["freshness_hash"] = "sha256:" + hashlib.sha256(_canonical(freshness_payload)).hexdigest()
    return core


class TraceReader(Protocol):
    def list(self, limit: int) -> Sequence[Mapping[str, Any]]: ...


@dataclass(frozen=True)
class AgentRunTraceReader:
    """Read-only adapter over the existing ``list_agent_runs(limit)`` seam."""

    loader: Callable[[int], Sequence[Mapping[str, Any]]]

    def list(self, limit: int) -> Sequence[Mapping[str, Any]]:
        return self.loader(limit)


@dataclass(frozen=True)
class CodeGraphService:
    snapshot: Mapping[str, Any]
    trace_reader: TraceReader | None = None

    def query(self, operation: str, query: str = "", limit: int = 20) -> dict[str, Any]:
        if not 1 <= limit <= MAX_LIMIT:
            raise CodeGraphError(f"limit must be between 1 and {MAX_LIMIT}")
        needle = query.casefold()
        collections = {
            "symbols": "symbols", "imports": "imports", "references": "references",
            "dependencies": "dependencies", "files": "files",
            "invariants": "invariants", "receipts": "execution_receipts",
        }
        if operation == "status":
            capabilities = dict(self.snapshot["capabilities"])
            if self.trace_reader is not None:
                capabilities["runtime_traces"] = "available:injected-agent-runs-reader"
            result: Any = {
                "commit": self.snapshot["commit"], "tree": self.snapshot["tree"],
                "freshness_hash": self.snapshot["freshness_hash"],
                "capabilities": capabilities,
            }
        elif operation == "traces":
            if self.trace_reader is None:
                result = {"status": "unavailable", "reason": "no-trace-reader-injected", "objects": []}
            else:
                result = {
                    "status": "available",
                    "objects": [_trace_object(row, self.snapshot["freshness_hash"]) for row in self.trace_reader.list(limit)[:limit]],
                }
        elif operation in collections:
            values = self.snapshot[collections[operation]]
            result = [item for item in values if not needle or needle in _canonical(item).decode().casefold()][:limit]
        else:
            raise CodeGraphError(f"unsupported operation: {operation}")
        return {"schema": SERVICE_SCHEMA, "operation": operation, "result": result}


def service_call(snapshot: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any]:
    """Pure MCP-style request/response seam; no transport or mutable state."""
    return CodeGraphService(snapshot).query(
        str(request.get("operation", "status")),
        str(request.get("query", "")), int(request.get("limit", 20)),
    )


def _trace_object(row: Mapping[str, Any], snapshot_hash: str) -> dict[str, Any]:
    """Bounded typed metadata only; transcript contents remain in their backend."""
    return {
        "schema": "tgw-code-graph-execution-trace/v1",
        "run_id": row.get("run_id"), "parent_run_id": row.get("parent_run_id"),
        "agent_type": row.get("agent_type"), "todo_id": row.get("todo_id"),
        "pp_ref": row.get("pp_ref"), "host": row.get("host"),
        "git_branch": row.get("git_branch"), "started_at": row.get("started_at"),
        "ended_at": row.get("ended_at"), "status": row.get("status"),
        "summary": row.get("summary"), "transcript_ref": row.get("transcript_path"),
        "code_snapshot_hash": snapshot_hash,
    }
