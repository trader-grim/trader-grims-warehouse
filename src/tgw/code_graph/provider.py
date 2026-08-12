"""Commit-bound Python symbol, import, reference, and dependency graph.

This minimum provider deliberately does not claim invariant extraction or
runtime tracing.  It reads Git blobs from an exact commit, never the mutable
working tree.
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "tgw-code-graph-snapshot/v1"
SERVICE_SCHEMA = "tgw-code-graph-service/v1"
MAX_LIMIT = 100


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
    for path in paths:
        source = _git(repo, "show", f"{commit}:{path}")
        files.append({"path": path, "sha256": hashlib.sha256(source.encode()).hexdigest(), "module": _module(path)})
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
    core = {
        "schema": SCHEMA, "repository": str(repo), "commit": commit, "tree": tree,
        "languages": ["python"], "files": files, "symbols": symbols,
        "imports": imports, "dependencies": imports, "references": references,
        "parse_errors": parse_errors,
        "capabilities": {
            "symbols": "available", "imports": "available",
            "references": "partial:module-local-exact-only",
            "dependencies": "available:syntactic-imports",
            "invariants": "unavailable:not-implemented",
            "runtime_traces": "unavailable:not-collected",
        },
    }
    # The freshness identity is clone/host neutral; repository is diagnostic only.
    freshness_payload = {key: value for key, value in core.items() if key != "repository"}
    core["freshness_hash"] = "sha256:" + hashlib.sha256(_canonical(freshness_payload)).hexdigest()
    return core


@dataclass(frozen=True)
class CodeGraphService:
    snapshot: Mapping[str, Any]

    def query(self, operation: str, query: str = "", limit: int = 20) -> dict[str, Any]:
        if not 1 <= limit <= MAX_LIMIT:
            raise CodeGraphError(f"limit must be between 1 and {MAX_LIMIT}")
        needle = query.casefold()
        collections = {
            "symbols": "symbols", "imports": "imports", "references": "references",
            "dependencies": "dependencies", "files": "files",
        }
        if operation == "status":
            result: Any = {
                "commit": self.snapshot["commit"], "tree": self.snapshot["tree"],
                "freshness_hash": self.snapshot["freshness_hash"],
                "capabilities": self.snapshot["capabilities"],
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
