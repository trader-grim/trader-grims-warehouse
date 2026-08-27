"""Atomic, read-only context snapshot for every TGW harness.

The active task and its Plan-cycle cursor are one context fact.  Keeping them
in separate files made it possible to publish a task that pointed at one leaf
while the MCP searched a different derived graph.  This module defines the
single-file representation used by the MCP and its publisher; it grants no
effect or approval authority.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import zlib
from typing import Any, Mapping

_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
SCHEMA = "tgw-current-context-snapshot/v1"
MAX_SNAPSHOT_BYTES = 256 * 1024
MAX_TASK_BYTES = 4 * 1024 * 1024
TASK_PROJECTION_ENCODING = "zlib+base64+canonical-json/v1"


class CurrentContextError(ValueError):
    """The published context is absent, malformed, or internally divergent."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _legacy_canonical(value: Any) -> bytes:
    """Exact predecessor inline-task hash representation (ASCII escaped JSON)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _bounded_legacy_canonical(value: Any, maximum: int, label: str) -> bytes:
    """Serialize the predecessor ASCII wire without permitting an oversized copy."""
    chunks: list[bytes] = []
    size = 0
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    for text in encoder.iterencode(value):
        chunk = text.encode("ascii")
        size += len(chunk)
        if size > maximum:
            raise CurrentContextError(f"{label} exceeds {maximum} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def _sha(value: Any) -> str:
    try:
        raw = _bounded_canonical(value, MAX_SNAPSHOT_BYTES - 1, "current context snapshot")
    except CurrentContextError as exc:
        raise CurrentContextError("current context snapshot exceeds 256 KiB") from exc
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _bounded_canonical(value: Any, maximum: int, label: str) -> bytes:
    """Serialize canonically while enforcing the bound before one output allocation."""
    chunks: list[bytes] = []
    size = 0
    encoder = json.JSONEncoder(
        sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    for text in encoder.iterencode(value):
        chunk = text.encode("utf-8")
        size += len(chunk)
        if size > maximum:
            raise CurrentContextError(f"{label} exceeds {maximum} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def serialized_bytes(value: Mapping[str, Any]) -> bytes:
    """The one publisher/launcher wire contract: canonical UTF-8 JSON plus LF."""
    try:
        return _bounded_canonical(
            value, MAX_SNAPSHOT_BYTES - 1, "current context snapshot"
        ) + b"\n"
    except CurrentContextError as exc:
        raise CurrentContextError("current context snapshot exceeds 256 KiB") from exc


def _project_task(task: Mapping[str, Any]) -> dict[str, str]:
    raw = _bounded_canonical(task, MAX_TASK_BYTES, "current task")
    compressed = zlib.compress(raw, 9)
    return {
        "encoding": TASK_PROJECTION_ENCODING,
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "data": base64.b64encode(compressed).decode("ascii"),
    }


def _expand_task(projection: Any) -> dict[str, Any]:
    if not isinstance(projection, Mapping) or projection.get("encoding") != TASK_PROJECTION_ENCODING:
        raise CurrentContextError("current context task projection is invalid")
    data = projection.get("data")
    digest = projection.get("sha256")
    if not isinstance(data, str) or not isinstance(digest, str):
        raise CurrentContextError("current context task projection is invalid")
    try:
        compressed = base64.b64decode(data, validate=True)
        inflater = zlib.decompressobj()
        raw = inflater.decompress(compressed, MAX_TASK_BYTES + 1)
    except (binascii.Error, ValueError, zlib.error) as exc:
        raise CurrentContextError("current context task projection is invalid") from exc
    if (
        len(raw) > MAX_TASK_BYTES
        or inflater.unconsumed_tail
        or inflater.unused_data
        or not inflater.eof
    ):
        raise CurrentContextError("current context task projection is unbounded or trailing")
    try:
        task = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CurrentContextError("current context task projection encoding is invalid") from exc
    if not isinstance(task, dict) or _canonical(task) != raw:
        raise CurrentContextError("current context task projection is not canonical")
    if compressed != zlib.compress(raw, 9):
        raise CurrentContextError("current context task projection is not canonical zlib level 9")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest) or digest != "sha256:" + hashlib.sha256(raw).hexdigest():
        raise CurrentContextError("current context task projection hash differs")
    return task


def build(task: Mapping[str, Any], cursor: Mapping[str, Any]) -> dict[str, Any]:
    """Build one bounded snapshot from a task record and its cycle cursor."""
    if not isinstance(task, Mapping) or task.get("schema") != "tgw-current-task/v1":
        raise CurrentContextError("task record is invalid")
    if not isinstance(cursor, Mapping) or cursor.get("schema") != "tgw-plan-execution-cycle-cursor/v1":
        raise CurrentContextError("cycle cursor is invalid")
    plan = task.get("plan")
    implementation = task.get("implementation")
    development = implementation.get("development_source") if isinstance(implementation, Mapping) else None
    resolved = cursor.get("resolved")
    if not isinstance(plan, Mapping) or not isinstance(development, Mapping) or not isinstance(resolved, Mapping):
        raise CurrentContextError("task/cursor bindings are invalid")
    plan_commit = plan.get("approved_commit")
    source_commit = development.get("commit")
    source_tree = cursor.get("source_tree")
    capability = development.get("next_leaf")
    treatment = resolved.get("next_treatment")
    if (
        not isinstance(plan_commit, str) or _COMMIT.fullmatch(plan_commit) is None
        or not isinstance(source_commit, str) or _COMMIT.fullmatch(source_commit) is None
        or not isinstance(source_tree, str) or _COMMIT.fullmatch(source_tree) is None
        or not isinstance(capability, str) or not capability
        or cursor.get("plan_commit") != plan_commit
        or cursor.get("source_commit") != source_commit
        or not isinstance(treatment, str) or treatment.rsplit(":", 1)[-1] != capability
    ):
        raise CurrentContextError("task and cursor select different Plan context")
    snapshot = {
        "schema": SCHEMA,
        "plan_commit": plan_commit,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "active_capability": capability,
        "active_treatment": treatment,
        "task_projection": _project_task(task),
        "cursor": dict(cursor),
    }
    # Bound the complete hash input before hashlib receives it.  This prevents
    # an oversized body from being materialized by the legacy helper first.
    snapshot["snapshot_sha256"] = _sha(snapshot)
    serialized_bytes(snapshot)
    return snapshot


def publish_bytes(task: Mapping[str, Any], cursor: Mapping[str, Any]) -> bytes:
    """Build the snapshot and return the exact bytes a publisher must install."""
    return serialized_bytes(build(task, cursor))


def parse(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a previously published snapshot."""
    if not isinstance(value, Mapping) or value.get("schema") != SCHEMA:
        raise CurrentContextError("current context snapshot is invalid")
    serialized_bytes(value)
    claimed = value.get("snapshot_sha256")
    body = dict(value)
    body.pop("snapshot_sha256", None)
    inline = body.get("task")
    expected = (
        "sha256:" + hashlib.sha256(_legacy_canonical(body)).hexdigest()
        if isinstance(inline, Mapping) and "task_projection" not in body
        else _sha(body)
    )
    if not isinstance(claimed, str) or claimed != expected:
        raise CurrentContextError("current context snapshot hash differs")
    if "task" in body and "task_projection" in body:
        raise CurrentContextError("current context snapshot has two task representations")
    task = dict(inline) if isinstance(inline, Mapping) else _expand_task(body.get("task_projection"))
    rebuilt = build(task, body.get("cursor", {}))
    if any(value.get(key) != rebuilt.get(key) for key in ("plan_commit", "source_commit", "source_tree", "active_capability", "active_treatment")):
        raise CurrentContextError("current context snapshot bindings differ")
    result = dict(value)
    result["task"] = task
    return result


def parse_bytes(raw: bytes) -> dict[str, Any]:
    """Parse exactly one snapshot stream under the serialized-byte contract."""
    if not isinstance(raw, bytes) or len(raw) > MAX_SNAPSHOT_BYTES or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise CurrentContextError("current context snapshot serialization is invalid")
    try:
        value = json.loads(raw[:-1].decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CurrentContextError("current context snapshot encoding is invalid") from exc
    if not isinstance(value, dict):
        raise CurrentContextError("current context snapshot serialization is not canonical")
    inline_only = isinstance(value.get("task"), Mapping) and "task_projection" not in value
    try:
        expected_wire = (
            _bounded_legacy_canonical(value, MAX_SNAPSHOT_BYTES - 1, "current context snapshot") + b"\n"
            if inline_only
            else serialized_bytes(value)
        )
    except CurrentContextError as exc:
        raise CurrentContextError("current context snapshot serialization is invalid") from exc
    if expected_wire != raw:
        raise CurrentContextError("current context snapshot serialization is not canonical")
    return parse(value)
